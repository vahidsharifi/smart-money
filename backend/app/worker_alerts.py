from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import settings, validate_chain_config
from app.db import async_session
from app.logging import configure_logging
from app.models import Alert, TokenRisk, Trade, WalletMetric
from app.narrator import narrate_alert

configure_logging()
logger = logging.getLogger(__name__)

ALERT_TYPE = "trade_conviction"
COOLDOWN_MINUTES = 60
LOOKBACK_HOURS = 24


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


async def run_once() -> int:
    cutoff = datetime.utcnow() - timedelta(hours=LOOKBACK_HOURS)
    async with async_session() as session:
        trades_result = await session.execute(
            select(Trade)
            .where(Trade.side == "BUY", Trade.created_at >= cutoff)
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

            metric_result = await session.execute(
                select(WalletMetric)
                .where(
                    WalletMetric.chain == trade.chain,
                    WalletMetric.wallet_address == trade.wallet_address,
                )
                .limit(1)
            )
            wallet_metric = metric_result.scalar_one_or_none()
            if wallet_metric is None:
                logger.debug("alert_skip_missing_wallet_metrics wallet=%s", trade.wallet_address)
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
    while True:
        created = await run_once()
        logger.info("alerts_worker_cycle alerts=%s", created)
        await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    asyncio.run(run_worker())
