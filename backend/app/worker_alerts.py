from __future__ import annotations

import asyncio
import logging

from redis.asyncio import Redis
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.config import settings, validate_chain_config
from app.cost_model import estimate_trade_gas_cost
from app.db import async_session
from app.logging import configure_logging
from app.models import Alert, SignalOutcome, TokenRisk, Trade, WalletMetric, WatchPair
from app.narrator import narrate_alert
from app.utils.ops import start_heartbeat, stop_heartbeat
from app.utils import install_shutdown_handlers
from app.utils.wallets import is_wallet_ignored

configure_logging()
logger = logging.getLogger(__name__)

ALERT_TYPE = "trade_conviction"
COOLDOWN_MINUTES = 60
LOOKBACK_HOURS = 24
POOL_ALERT_TYPE = "pool_activity"
POOL_ALERT_USD_SCALE = 100_000.0


def _tier_for_value(total_value: float | None) -> str:
    if total_value is None:
        return "ignore"
    if total_value >= settings.tier_ocean_threshold:
        return "ocean"
    if total_value >= settings.tier_shadow_threshold:
        return "shadow"
    if total_value >= settings.tier_titan_threshold:
        return "titan"
    return "ignore"


def _calculate_conviction(*, tss_score: float, total_value: float | None) -> float:
    wallet_value = total_value or 0.0
    wallet_ratio = min(wallet_value / settings.tier_titan_threshold, 1.0)
    conviction = (tss_score / 100.0) * 60.0 + wallet_ratio * 40.0
    return round(conviction, 2)


def _calculate_pool_conviction(*, tss_score: float, usd_value: float | None) -> float:
    size_usd = max(float(usd_value or 0.0), 0.0)
    size_score = min(size_usd / POOL_ALERT_USD_SCALE, 1.0)
    conviction = (tss_score / 100.0) * 50.0 + size_score * 50.0
    return round(conviction, 2)


def _chain_expected_move(chain: str) -> float:
    if chain == "bsc":
        return settings.netev_expected_move_bsc
    return settings.netev_expected_move_eth


def _chain_min_usd_profit(chain: str) -> float:
    if chain == "bsc":
        return settings.netev_min_usd_profit_bsc
    return settings.netev_min_usd_profit_eth


def _chain_min_roi(chain: str) -> float:
    if chain == "bsc":
        return settings.netev_min_roi_bsc
    return settings.netev_min_roi_eth



async def _derived_expected_move(session, *, chain: str, token_address: str) -> float | None:
    result = await session.execute(
        select(func.avg(SignalOutcome.net_tradeable_return_est))
        .join(Alert, Alert.id == SignalOutcome.alert_id)
        .where(
            Alert.chain == chain,
            Alert.token_address == token_address,
            SignalOutcome.was_sellable_entire_window.is_(True),
            SignalOutcome.trap_flag.is_(False),
            SignalOutcome.net_tradeable_return_est.is_not(None),
        )
    )
    avg_outcome = result.scalar_one_or_none()
    if avg_outcome is None:
        return None
    return max(0.0, min(0.2, float(avg_outcome)))


async def _latest_alert(session, *, chain: str, wallet_address: str, token_address: str) -> Alert | None:
    result = await session.execute(
        select(Alert)
        .where(
            Alert.chain == chain,
            Alert.wallet_address == wallet_address,
            Alert.token_address == token_address,
            Alert.alert_type == ALERT_TYPE,
        )
        .order_by(Alert.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _watch_pair_active(session, *, chain: str, pair_address: str, now: datetime) -> bool:
    result = await session.execute(
        select(WatchPair)
        .where(
            WatchPair.chain == chain,
            WatchPair.pair_address == pair_address,
            WatchPair.expires_at > now,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _emit_pool_alert(
    session,
    *,
    trade: Trade,
    token_risk: TokenRisk,
    wallet_address: str,
) -> bool:
    latest = await _latest_alert(
        session,
        chain=trade.chain,
        wallet_address=wallet_address,
        token_address=trade.token_address or "",
    )
    if latest is not None:
        cooldown_until = latest.created_at + timedelta(minutes=COOLDOWN_MINUTES)
        if datetime.utcnow() < cooldown_until:
            logger.debug(
                "alert_skip_cooldown wallet=%s token=%s",
                wallet_address,
                trade.token_address,
            )
            return False

    tss_score = 0.0
    if isinstance(token_risk.components, dict):
        tss_score = float(token_risk.components.get("tss", {}).get("score") or 0.0)
    if token_risk.score is not None:
        tss_score = float(token_risk.score)

    conviction = _calculate_pool_conviction(
        tss_score=tss_score,
        usd_value=trade.usd_value,
    )
    reasons = {
        "conviction": conviction,
        "tss": tss_score,
        "pool_address": trade.pair_address,
        "trade": {
            "tx_hash": trade.tx_hash,
            "log_index": trade.log_index,
            "side": trade.side,
            "amount": trade.amount,
            "price": trade.price,
            "usd_value": trade.usd_value,
            "block_time": trade.block_time.isoformat() if trade.block_time else None,
        },
        "source": "pool_watchlist",
    }
    narrative = await narrate_alert(reasons)
    session.add(
        Alert(
            chain=trade.chain,
            wallet_address=wallet_address,
            token_address=trade.token_address,
            alert_type=POOL_ALERT_TYPE,
            tss=tss_score,
            conviction=conviction,
            reasons=reasons,
            narrative=narrative,
            created_at=datetime.utcnow(),
        )
    )
    return True


async def _netev_gate(
    session,
    *,
    trade: Trade,
    token_risk: TokenRisk,
) -> tuple[bool, dict]:
    size_usd = float(trade.usd_value or 0.0)
    if size_usd <= 0:
        return False, {"reason": "missing_trade_size_usd"}

    derived_move = await _derived_expected_move(
        session,
        chain=trade.chain,
        token_address=trade.token_address or "",
    )
    expected_move = derived_move if derived_move is not None else _chain_expected_move(trade.chain)

    slippage = settings.netev_default_slippage
    if isinstance(token_risk.components, dict):
        slippage = float(token_risk.components.get("estimated_slippage", slippage) or slippage)

    gross_profit_usd = size_usd * expected_move
    gas_breakdown = await estimate_trade_gas_cost(session, trade=trade)
    gas_cost_usd = float(gas_breakdown["gas_cost_usd"])
    slippage_cost_usd = size_usd * max(0.0, slippage)
    netev_usd = gross_profit_usd - gas_cost_usd - slippage_cost_usd
    netev_roi = netev_usd / size_usd if size_usd > 0 else -1.0

    min_usd = _chain_min_usd_profit(trade.chain)
    min_roi = _chain_min_roi(trade.chain)
    passed = netev_usd >= min_usd and netev_roi >= min_roi
    payload = {
        "passed": passed,
        "gate_failure_reason": None if passed else "netev_below_threshold",
        "expected_move": round(expected_move, 6),
        "derived_from_outcomes": derived_move is not None,
        "size_usd": round(size_usd, 6),
        "gross_profit_usd": round(gross_profit_usd, 6),
        "gas_cost_usd": round(gas_cost_usd, 6),
        "gas_cost_source": gas_breakdown.get("source"),
        "native_price_usd": gas_breakdown.get("native_price_usd"),
        "gas_used": gas_breakdown.get("gas_used"),
        "effective_gas_price_wei": gas_breakdown.get("effective_gas_price_wei"),
        "avg_gas_usd_1h": gas_breakdown.get("avg_gas_usd_1h"),
        "p95_gas_usd_1h": gas_breakdown.get("p95_gas_usd_1h"),
        "slippage_cost_usd": round(slippage_cost_usd, 6),
        "netev_usd": round(netev_usd, 6),
        "netev_roi": round(netev_roi, 6),
        "min_usd_profit": min_usd,
        "min_roi_after_costs": min_roi,
    }
    return passed, payload


async def run_once() -> int:
    cutoff = datetime.utcnow() - timedelta(hours=LOOKBACK_HOURS)
    async with async_session() as session:
        trades_result = await session.execute(
            select(Trade)
            .where(func.lower(Trade.side) == "buy", Trade.created_at >= cutoff)
            .order_by(Trade.created_at.desc())
        )
        trades = trades_result.scalars().all()
        created = 0

        for trade in trades:
            if not trade.wallet_address or not trade.token_address:
                logger.debug("alert_skip_missing_wallet trade=%s", trade.tx_hash)
                continue

            token_result = await session.execute(
                select(TokenRisk)
                .where(TokenRisk.chain == trade.chain, TokenRisk.address == trade.token_address)
                .limit(1)
            )
            token_risk = token_result.scalar_one_or_none()
            if token_risk is None:
                logger.debug("alert_skip_missing_token_risk token=%s", trade.token_address)
                continue

            now = datetime.utcnow()
            watch_pair_active = False
            if trade.pair_address:
                watch_pair_active = await _watch_pair_active(
                    session,
                    chain=trade.chain,
                    pair_address=trade.pair_address,
                    now=now,
                )

            if watch_pair_active and not (trade.usd_value or 0):
                alert_wallet_address = trade.wallet_address or trade.pair_address
                if alert_wallet_address:
                    created_pool = await _emit_pool_alert(
                        session,
                        trade=trade,
                        token_risk=token_risk,
                        wallet_address=alert_wallet_address,
                    )
                    if created_pool:
                        created += 1
                        continue

            wallet_ignored = await is_wallet_ignored(
                session, chain=trade.chain, wallet_address=trade.wallet_address
            )
            metric_result = await session.execute(
                select(WalletMetric)
                .where(
                    WalletMetric.chain == trade.chain,
                    WalletMetric.wallet_address == trade.wallet_address,
                )
                .limit(1)
            )
            wallet_metric = metric_result.scalar_one_or_none()

            if wallet_metric is None or wallet_ignored:
                if watch_pair_active:
                    alert_wallet_address = trade.wallet_address or trade.pair_address
                    if not alert_wallet_address:
                        logger.debug("alert_skip_missing_wallet trade=%s", trade.tx_hash)
                        continue
                    created_pool = await _emit_pool_alert(
                        session,
                        trade=trade,
                        token_risk=token_risk,
                        wallet_address=alert_wallet_address,
                    )
                    if created_pool:
                        created += 1
                        continue
                if wallet_metric is None:
                    logger.debug(
                        "alert_skip_missing_wallet_metrics wallet=%s", trade.wallet_address
                    )
                else:
                    logger.info(
                        "alert_skip_ignored_wallet chain=%s wallet=%s trade=%s",
                        trade.chain,
                        trade.wallet_address,
                        trade.tx_hash,
                    )
                continue

            latest = await _latest_alert(
                session,
                chain=trade.chain,
                wallet_address=trade.wallet_address,
                token_address=trade.token_address,
            )
            if latest is not None:
                cooldown_until = latest.created_at + timedelta(minutes=COOLDOWN_MINUTES)
                if datetime.utcnow() < cooldown_until:
                    logger.debug(
                        "alert_skip_cooldown wallet=%s token=%s",
                        trade.wallet_address,
                        trade.token_address,
                    )
                    continue

            tss_score = 0.0
            if isinstance(token_risk.components, dict):
                tss_score = float(token_risk.components.get("tss", {}).get("score") or 0.0)
            if token_risk.score is not None:
                tss_score = float(token_risk.score)

            netev_passed, netev = await _netev_gate(
                session,
                trade=trade,
                token_risk=token_risk,
            )
            if not netev_passed:
                logger.info(
                    "alert_skip_netev chain=%s wallet=%s token=%s netev=%s",
                    trade.chain,
                    trade.wallet_address,
                    trade.token_address,
                    netev,
                )
                continue

            conviction = _calculate_conviction(
                tss_score=tss_score,
                total_value=wallet_metric.total_value,
            )
            tier = _tier_for_value(wallet_metric.total_value)
            reasons = {
                "conviction": conviction,
                "tier": tier,
                "wallet_total_value": wallet_metric.total_value,
                "tss": tss_score,
                "cooldown_minutes": COOLDOWN_MINUTES,
                "netev": netev,
                "trade": {
                    "tx_hash": trade.tx_hash,
                    "log_index": trade.log_index,
                    "side": trade.side,
                    "amount": trade.amount,
                    "price": trade.price,
                    "usd_value": trade.usd_value,
                    "block_time": trade.block_time.isoformat() if trade.block_time else None,
                },
            }
            narrative = await narrate_alert(reasons)
            session.add(
                Alert(
                    chain=trade.chain,
                    wallet_address=trade.wallet_address,
                    token_address=trade.token_address,
                    alert_type=ALERT_TYPE,
                    tss=tss_score,
                    conviction=conviction,
                    reasons=reasons,
                    narrative=narrative,
                    created_at=datetime.utcnow(),
                )
            )
            created += 1

        await session.commit()
        return created


async def run_worker(interval_seconds: int = 60) -> None:
    validate_chain_config()
    logger.info("alerts_worker_started")
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    heartbeat_task = await start_heartbeat(redis, worker_name="alerts-worker")
    stop_event = asyncio.Event()
    install_shutdown_handlers(stop_event, logger)
    try:
        while not stop_event.is_set():
            created = await run_once()
            logger.info("alerts_worker_cycle alerts=%s", created)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                continue
    finally:
        await stop_heartbeat(heartbeat_task)
        await redis.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
