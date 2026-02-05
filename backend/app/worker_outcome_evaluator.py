from __future__ import annotations

import asyncio
import bisect
import logging
import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.db import async_session
from app.logging import configure_logging
from app.models import Alert, SignalOutcome, TokenRisk, Trade
from app.utils import HttpClient, install_shutdown_handlers

configure_logging()
logger = logging.getLogger(__name__)

HORIZONS_MINUTES = (30, 360, 1440)
RUN_INTERVAL_SECONDS = 300
USD_NOTIONAL = 1_000.0
CRITICAL_RISK_FLAGS = {"honeypot", "cannot_sell", "liquidity_floor_breach", "liquidity_pull"}
DEX_CACHE_TTL_SECONDS = 120

_dex_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(value, 8)))


def _normalize_flags(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(flag).strip().lower() for flag in value if flag}
    if isinstance(value, dict):
        out: set[str] = set()
        for key, raw in value.items():
            if raw:
                out.add(str(key).strip().lower())
        return out
    return set()


def _extract_risk_snapshots(token_risk: TokenRisk) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    components = token_risk.components if isinstance(token_risk.components, dict) else {}
    history = components.get("history")
    if isinstance(history, list):
        for item in history:
            if isinstance(item, dict):
                snapshots.append(item)

    if not snapshots:
        snapshots.append(
            {
                "updated_at": token_risk.updated_at.isoformat() if token_risk.updated_at else None,
                "flags": token_risk.flags if isinstance(token_risk.flags, list) else components.get("flags"),
                "max_suggested_size_usd": components.get("max_suggested_size_usd"),
                "liquidity_usd": (components.get("tss") or {}).get("dexscreener", {}).get("max_liquidity_usd"),
            }
        )
    return snapshots


def _parse_snapshot_time(snapshot: dict[str, Any]) -> datetime | None:
    raw = snapshot.get("updated_at") or snapshot.get("timestamp") or snapshot.get("ts")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        if raw > 1_000_000_000_000:
            raw = raw / 1000
        try:
            return datetime.utcfromtimestamp(float(raw))
        except (TypeError, ValueError, OSError):
            return None
    if isinstance(raw, str):
        normalized = raw.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _risk_window_assessment(snapshots: list[dict[str, Any]], start: datetime, end: datetime) -> tuple[bool | None, bool, bool, list[dict[str, Any]]]:
    in_window: list[dict[str, Any]] = []
    for snapshot in snapshots:
        snapshot_time = _parse_snapshot_time(snapshot)
        if snapshot_time is None:
            continue
        if start <= snapshot_time <= end:
            in_window.append(snapshot)

    if len(in_window) < 2:
        return None, False, True, in_window

    for snapshot in in_window:
        flags = _normalize_flags(snapshot.get("flags"))
        if flags.intersection(CRITICAL_RISK_FLAGS):
            return False, True, False, in_window

    return True, False, False, in_window


def _estimate_slippage(snapshots: list[dict[str, Any]]) -> tuple[float | None, float | None, bool]:
    candidates: list[float] = []
    for snapshot in snapshots:
        slip_payload = snapshot.get("slippage") if isinstance(snapshot, dict) else None
        direct = _safe_float((slip_payload or {}).get("exit_slippage_1k")) if isinstance(slip_payload, dict) else None
        if direct is not None:
            candidates.append(max(0.0, direct))
            continue

        max_size = _safe_float(snapshot.get("max_suggested_size_usd"))
        if max_size is None:
            max_size = _safe_float((snapshot.get("components") or {}).get("max_suggested_size_usd")) if isinstance(snapshot.get("components"), dict) else None
        if max_size is None:
            liquidity = _safe_float(snapshot.get("liquidity_usd"))
            if liquidity is not None:
                max_size = liquidity * 0.02

        if max_size is None or max_size <= 0:
            continue

        ratio = USD_NOTIONAL / max_size
        slippage = max(0.0025, min(0.40, 0.02 * ratio))
        candidates.append(slippage)

    if not candidates:
        return None, None, True
    return min(candidates), max(candidates), False


def _snapshot_sellable(snapshot: dict[str, Any]) -> bool:
    if not isinstance(snapshot, dict):
        return False
    for key in ("sellability", "sellable", "can_sell"):
        value = snapshot.get(key)
        if isinstance(value, bool):
            return value
    flags = _normalize_flags(snapshot.get("flags"))
    if flags.intersection(CRITICAL_RISK_FLAGS):
        return False
    return True


def _snapshot_max_size(snapshot: dict[str, Any]) -> float | None:
    direct = _safe_float(snapshot.get("max_suggested_size_usd"))
    if direct is not None:
        return direct
    components = snapshot.get("components")
    if isinstance(components, dict):
        nested = _safe_float(components.get("max_suggested_size_usd"))
        if nested is not None:
            return nested
    return None


def _is_exit_feasible_snapshot(snapshot: dict[str, Any]) -> bool:
    max_size = _snapshot_max_size(snapshot)
    if max_size is None or max_size < 1000:
        return False
    return _snapshot_sellable(snapshot)


async def _fetch_dex_prices(client: HttpClient, *, token_address: str, anchor_time: datetime) -> list[tuple[datetime, float]]:
    now = time.time()
    cached = _dex_cache.get(token_address)
    if cached and now - cached[0] < DEX_CACHE_TTL_SECONDS:
        payload = cached[1]
    else:
        url = f"{settings.dexscreener_base_url}/tokens/{token_address}"
        payload = await client.get_json(url)
        if isinstance(payload, dict):
            _dex_cache[token_address] = (now, payload)

    if not isinstance(payload, dict):
        return []
    prices: list[tuple[datetime, float]] = []
    pairs = payload.get("pairs")
    if not isinstance(pairs, list):
        return prices
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        price = _safe_float(pair.get("priceUsd"))
        if price is not None and price > 0:
            prices.append((anchor_time, price))
    return prices


async def _price_series(
    session: Any,
    client: HttpClient,
    *,
    chain: str,
    token_address: str,
    alert: Alert,
    start: datetime,
    end: datetime,
) -> tuple[list[tuple[datetime, float]], bool]:
    reasons = alert.reasons if isinstance(alert.reasons, dict) else {}
    pair_address = reasons.get("pair_address")

    filters = [Trade.token_address == token_address]
    if isinstance(pair_address, str) and pair_address:
        filters.append(Trade.pair_address == pair_address)

    result = await session.execute(
        select(Trade.block_time, Trade.price)
        .where(
            Trade.chain == chain,
            *filters,
            Trade.block_time >= start,
            Trade.block_time <= end,
            Trade.decode_confidence >= 0.6,
            Trade.price.isnot(None),
            Trade.price > 0,
        )
        .order_by(Trade.block_time.asc())
    )
    prices = [
        (ts, float(value))
        for ts, value in result.all()
        if ts is not None and value is not None and value > 0
    ]
    if len(prices) >= 2:
        return prices, False

    dex_prices = await _fetch_dex_prices(client, token_address=token_address, anchor_time=end)
    all_prices = prices + dex_prices
    return all_prices, len(all_prices) < 2


def _entry_price(alert: Alert, prices: list[tuple[datetime, float]]) -> float | None:
    reasons = alert.reasons if isinstance(alert.reasons, dict) else {}
    entry = _safe_float(reasons.get("entry_price"))
    if entry is not None and entry > 0:
        return entry
    if prices:
        return prices[0][1]
    return None


def _exit_feasible_peak(
    prices: list[tuple[datetime, float]],
    in_window_snapshots: list[dict[str, Any]],
    *,
    entry_price: float,
) -> tuple[float | None, datetime | None, bool]:
    if not prices:
        return None, None, False

    parsed_snapshots = sorted(
        (
            snapshot_time,
            _is_exit_feasible_snapshot(snapshot),
        )
        for snapshot in in_window_snapshots
        if (snapshot_time := _parse_snapshot_time(snapshot)) is not None
    )
    if not parsed_snapshots:
        return None, None, False

    snapshot_times = [snapshot_time for snapshot_time, _ in parsed_snapshots]
    if not any(is_feasible for _, is_feasible in parsed_snapshots):
        return None, None, False

    max_gain: float | None = None
    max_time: datetime | None = None
    for price_time, price in prices:
        nearest_index = bisect.bisect_right(snapshot_times, price_time) - 1
        if nearest_index < 0:
            continue
        _, is_feasible = parsed_snapshots[nearest_index]
        if not is_feasible:
            continue
        gain = price / entry_price - 1
        if max_gain is None or gain > max_gain:
            max_gain = gain
            max_time = price_time

    if max_gain is None:
        return None, None, False
    was_sellable_entire_window = all(
        _is_exit_feasible_snapshot(snapshot)
        for snapshot in in_window_snapshots
        if _parse_snapshot_time(snapshot) is not None
    )
    return max_gain, max_time, was_sellable_entire_window


def _net_return(
    peak_gain: float | None,
    *,
    max_slippage: float | None,
    trap_flag: bool,
    sellable: bool | None,
) -> float | None:
    if peak_gain is None:
        return None
    gas_cost = 0.006
    slippage_cost = max_slippage if max_slippage is not None else 0.02
    conservative_cost = gas_cost + slippage_cost
    net = peak_gain - conservative_cost
    if trap_flag or sellable is False:
        return min(net, -0.15)
    return net


async def _evaluate_alert_horizon(
    *,
    session: Any,
    client: HttpClient,
    alert: Alert,
    horizon_minutes: int,
) -> SignalOutcome | None:
    if not alert.token_address:
        return None

    window_start = alert.created_at
    window_end = alert.created_at + timedelta(minutes=horizon_minutes)

    token_risk = await session.get(TokenRisk, {"chain": alert.chain, "address": alert.token_address})
    snapshots = _extract_risk_snapshots(token_risk) if token_risk is not None else []

    sellable, trap_flag, risk_insufficient, in_window_snapshots = _risk_window_assessment(
        snapshots,
        window_start,
        window_end,
    )

    min_slippage: float | None = None
    max_slippage: float | None = None
    if in_window_snapshots:
        min_slippage, max_slippage, _ = _estimate_slippage(in_window_snapshots)

    prices, prices_insufficient = await _price_series(
        session,
        client,
        chain=alert.chain,
        token_address=alert.token_address,
        alert=alert,
        start=window_start,
        end=window_end,
    )
    entry_price = _entry_price(alert, prices)

    peak_gain: float | None = None
    drawdown: float | None = None
    if not prices_insufficient and entry_price and entry_price > 0:
        raw_values = [price for _, price in prices]
        peak_gain = max(raw_values) / entry_price - 1
        drawdown = min(raw_values) / entry_price - 1

    raw_peak_gain = peak_gain
    exit_feasible_peak_gain: float | None = None
    exit_feasible_peak_time: datetime | None = None
    was_sellable_entire_window = sellable
    if not prices_insufficient and entry_price and entry_price > 0:
        exit_feasible_peak_gain, exit_feasible_peak_time, was_sellable_entire_window = _exit_feasible_peak(
            prices,
            in_window_snapshots,
            entry_price=entry_price,
        )
        if exit_feasible_peak_gain is None:
            peak_gain = None
            was_sellable_entire_window = False
        else:
            peak_gain = raw_peak_gain

    net = _net_return(peak_gain, max_slippage=max_slippage, trap_flag=trap_flag, sellable=was_sellable_entire_window)
    return SignalOutcome(
        alert_id=alert.id,
        horizon_minutes=horizon_minutes,
        was_sellable_entire_window=was_sellable_entire_window,
        min_exit_slippage_1k=_to_decimal(min_slippage),
        max_exit_slippage_1k=_to_decimal(max_slippage),
        tradeable_peak_gain=_to_decimal(peak_gain),
        exit_feasible_peak_gain=_to_decimal(exit_feasible_peak_gain),
        exit_feasible_peak_time=exit_feasible_peak_time,
        tradeable_drawdown=_to_decimal(drawdown),
        net_tradeable_return_est=_to_decimal(net),
        trap_flag=trap_flag,
        evaluated_at=datetime.utcnow(),
    )


async def run_outcome_evaluator_once() -> int:
    now = datetime.utcnow()
    inserted = 0
    async with HttpClient() as client:
        async with async_session() as session:
            for horizon in HORIZONS_MINUTES:
                eligible_cutoff = now - timedelta(minutes=horizon)
                subq = (
                    select(SignalOutcome.id)
                    .where(SignalOutcome.alert_id == Alert.id, SignalOutcome.horizon_minutes == horizon)
                    .exists()
                )
                result = await session.execute(
                    select(Alert)
                    .where(Alert.created_at <= eligible_cutoff)
                    .where(~subq)
                    .order_by(Alert.created_at.asc())
                    .limit(200)
                )
                alerts = result.scalars().all()
                for alert in alerts:
                    outcome = await _evaluate_alert_horizon(
                        session=session,
                        client=client,
                        alert=alert,
                        horizon_minutes=horizon,
                    )
                    if outcome is None:
                        continue
                    session.add(outcome)
                    inserted += 1
            await session.commit()

    logger.info("outcome_evaluator_complete inserted=%s", inserted)
    return inserted


async def run_worker() -> None:
    stop_event = asyncio.Event()
    install_shutdown_handlers(stop_event, logger)
    while not stop_event.is_set():
        try:
            await run_outcome_evaluator_once()
        except Exception:
            logger.exception("outcome_evaluator_iteration_failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RUN_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue


if __name__ == "__main__":
    asyncio.run(run_worker())
