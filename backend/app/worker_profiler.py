from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from sqlalchemy import select

from app.config import settings, validate_chain_config
from app.db import async_session
from app.logging import configure_logging
from app.models import Alert, Position, Trade, WalletMetric
from app.services.merit import run_merit_update_once
from app.narrator import narrate_alert
from app.utils import install_shutdown_handlers
from app.utils.wallets import is_wallet_ignored

configure_logging()
logger = logging.getLogger(__name__)


@dataclass
class PositionState:
    quantity: float = 0.0
    average_price: float | None = None


def _normalize_side(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    if value in {"buy", "sell"}:
        return value
    return None


def _safe_float(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _effective_price(trade: Trade) -> float | None:
    price = _safe_float(trade.price)
    if price is not None:
        return price
    amount = _safe_float(trade.amount)
    usd_value = _safe_float(trade.usd_value)
    if amount and usd_value:
        return usd_value / amount
    return None


def _trade_sort_key(trade: Trade) -> tuple[datetime, datetime, str, int]:
    block_time = trade.block_time or trade.created_at or datetime.min
    created_at = trade.created_at or datetime.min
    return (block_time, created_at, trade.tx_hash or "", trade.log_index or 0)


def _apply_trade(position: PositionState, trade: Trade) -> None:
    side = _normalize_side(trade.side)
    amount = _safe_float(trade.amount)
    if side is None or amount is None:
        return
    if side == "buy":
        price = _effective_price(trade)
        if price is None:
            return
        total_cost = (position.average_price or 0.0) * position.quantity + amount * price
        position.quantity += amount
        position.average_price = total_cost / position.quantity if position.quantity else None
    else:
        if position.quantity <= 0:
            return
        sell_qty = min(position.quantity, amount)
        position.quantity -= sell_qty
        if position.quantity <= 0:
            position.quantity = 0.0
            position.average_price = None


def _tier_for_value(total_value: float) -> str:
    if total_value >= settings.tier_ocean_threshold:
        return "ocean"
    if total_value >= settings.tier_shadow_threshold:
        return "shadow"
    if total_value >= settings.tier_titan_threshold:
        return "titan"
    return "ignore"


async def _fetch_trades(session) -> list[Trade]:
    result = await session.execute(select(Trade).where(Trade.wallet_address.is_not(None)))
    trades = list(result.scalars().all())
    trades.sort(key=_trade_sort_key)
    return trades


async def _upsert_positions(
    session, *, chain: str, wallet_address: str, positions: dict[str, PositionState]
) -> None:
    for token_address, position in positions.items():
        result = await session.execute(
            select(Position)
            .where(
                Position.chain == chain,
                Position.wallet_address == wallet_address,
                Position.token_address == token_address,
            )
            .limit(1)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.quantity = position.quantity
            existing.average_price = position.average_price
            existing.updated_at = datetime.utcnow()
        else:
            session.add(
                Position(
                    chain=chain,
                    wallet_address=wallet_address,
                    token_address=token_address,
                    quantity=position.quantity,
                    average_price=position.average_price,
                    updated_at=datetime.utcnow(),
                )
            )


async def _upsert_wallet_metric(
    session, *, chain: str, wallet_address: str, total_value: float
) -> WalletMetric:
    result = await session.execute(
        select(WalletMetric)
        .where(WalletMetric.chain == chain, WalletMetric.wallet_address == wallet_address)
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.total_value = total_value
        existing.pnl = 0.0
        existing.updated_at = datetime.utcnow()
        return existing
    metric = WalletMetric(
        chain=chain,
        wallet_address=wallet_address,
        total_value=total_value,
        pnl=0.0,
        updated_at=datetime.utcnow(),
    )
    session.add(metric)
    return metric


async def _maybe_create_tier_alert(
    session, *, chain: str, wallet_address: str, tier: str, total_value: float
) -> None:
    if tier == "ignore":
        return
    cutoff = datetime.utcnow() - timedelta(hours=1)
    result = await session.execute(
        select(Alert)
        .where(
            Alert.chain == chain,
            Alert.wallet_address == wallet_address,
            Alert.alert_type == "wallet_tier",
            Alert.created_at >= cutoff,
        )
        .order_by(Alert.created_at.desc())
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing and existing.reasons.get("tier") == tier:
        return
    reasons = {"tier": tier, "total_value": total_value}
    narrative = await narrate_alert(reasons)
    session.add(
        Alert(
            chain=chain,
            wallet_address=wallet_address,
            token_address=None,
            alert_type="wallet_tier",
            reasons=reasons,
            narrative=narrative,
            created_at=datetime.utcnow(),
        )
    )


async def run_once() -> int:
    async with async_session() as session:
        trades = await _fetch_trades(session)
        positions_by_wallet: dict[tuple[str, str], dict[str, PositionState]] = defaultdict(
            dict
        )
        for trade in trades:
            if not trade.wallet_address or not trade.token_address:
                continue
            key = (trade.chain, trade.wallet_address)
            token_positions = positions_by_wallet[key]
            token_state = token_positions.get(trade.token_address)
            if token_state is None:
                token_state = PositionState()
                token_positions[trade.token_address] = token_state
            _apply_trade(token_state, trade)

        updates = 0
        for (chain, wallet_address), positions in positions_by_wallet.items():
            if await is_wallet_ignored(
                session, chain=chain, wallet_address=wallet_address
            ):
                logger.info(
                    "profiler_skip_ignored_wallet chain=%s wallet=%s",
                    chain,
                    wallet_address,
                )
                continue
            await _upsert_positions(
                session, chain=chain, wallet_address=wallet_address, positions=positions
            )
            total_value = sum(
                position.quantity * (position.average_price or 0.0)
                for position in positions.values()
            )
            await _upsert_wallet_metric(
                session,
                chain=chain,
                wallet_address=wallet_address,
                total_value=total_value,
            )
            tier = _tier_for_value(total_value)
            await _maybe_create_tier_alert(
                session,
                chain=chain,
                wallet_address=wallet_address,
                tier=tier,
                total_value=total_value,
            )
            updates += 1

        merit_updates = await run_merit_update_once(session)
        await session.commit()
        return max(updates, merit_updates)


async def run_worker(interval_seconds: int = 3600) -> None:
    validate_chain_config()
    logger.info("profiler_started")
    stop_event = asyncio.Event()
    install_shutdown_handlers(stop_event, logger)
    while not stop_event.is_set():
        updated = await run_once()
        logger.info("profiler_snapshot_complete wallets=%s", updated)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue


if __name__ == "__main__":
    asyncio.run(run_worker())
