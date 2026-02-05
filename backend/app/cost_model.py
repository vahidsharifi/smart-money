from __future__ import annotations

import logging
from datetime import datetime, timedelta
from statistics import quantiles

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ChainGasEstimate, GasCostObservation, Trade

logger = logging.getLogger(__name__)

NATIVE_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
NATIVE_PRICE_COINS = {
    "ethereum": "ethereum",
    "bsc": "binancecoin",
}


def _rpc_http_url(chain: str) -> str | None:
    cfg = settings.chain_config.get(chain)
    return cfg.rpc_http if cfg else None


async def _fetch_tx_receipt(chain: str, tx_hash: str) -> dict | None:
    rpc_url = _rpc_http_url(chain)
    if not rpc_url:
        return None
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getTransactionReceipt",
        "params": [tx_hash],
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(rpc_url, json=payload)
        response.raise_for_status()
        body = response.json()
    except Exception:
        logger.debug("gas_cost_receipt_rpc_failed chain=%s tx=%s", chain, tx_hash, exc_info=True)
        return None
    if "error" in body:
        return None
    receipt = body.get("result")
    return receipt if isinstance(receipt, dict) else None


async def _fetch_native_price_usd(chain: str) -> float | None:
    coin = NATIVE_PRICE_COINS.get(chain)
    if not coin:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                NATIVE_PRICE_URL,
                params={"ids": coin, "vs_currencies": "usd"},
            )
        response.raise_for_status()
        body = response.json()
        value = body.get(coin, {}).get("usd")
        if value is None:
            return None
        return float(value)
    except Exception:
        logger.debug("native_price_lookup_failed chain=%s", chain, exc_info=True)
        return None


def _chain_default_gas_cost(chain: str) -> float:
    if chain == "bsc":
        return settings.netev_gas_cost_usd_bsc
    return settings.netev_gas_cost_usd_eth


async def _refresh_chain_estimate(session: AsyncSession, *, chain: str) -> ChainGasEstimate | None:
    cutoff = datetime.utcnow() - timedelta(hours=1)
    rows = (
        await session.execute(
            select(GasCostObservation.gas_cost_usd)
            .where(GasCostObservation.chain == chain, GasCostObservation.observed_at >= cutoff)
            .order_by(GasCostObservation.observed_at.desc())
        )
    ).scalars().all()
    if not rows:
        return None

    values = [float(v) for v in rows]
    avg_cost = sum(values) / len(values)
    p95_cost = values[0] if len(values) == 1 else quantiles(values, n=100, method="inclusive")[94]

    estimate = await session.get(ChainGasEstimate, chain)
    if estimate is None:
        estimate = ChainGasEstimate(chain=chain)
        session.add(estimate)

    estimate.avg_gas_usd_1h = float(avg_cost)
    estimate.p95_gas_usd_1h = float(p95_cost)
    estimate.samples_1h = len(values)
    estimate.updated_at = datetime.utcnow()
    await session.flush()
    return estimate


async def _record_observation(
    session: AsyncSession,
    *,
    chain: str,
    tx_hash: str,
    gas_cost_usd: float,
) -> ChainGasEstimate | None:
    existing = await session.get(GasCostObservation, {"chain": chain, "tx_hash": tx_hash})
    if existing is None:
        session.add(
            GasCostObservation(
                chain=chain,
                tx_hash=tx_hash,
                gas_cost_usd=gas_cost_usd,
                observed_at=datetime.utcnow(),
            )
        )
    return await _refresh_chain_estimate(session, chain=chain)


async def estimate_trade_gas_cost(session: AsyncSession, *, trade: Trade) -> dict:
    default_cost = _chain_default_gas_cost(trade.chain)
    receipt = await _fetch_tx_receipt(trade.chain, trade.tx_hash)
    estimate = await session.get(ChainGasEstimate, trade.chain)

    if receipt:
        gas_used_hex = receipt.get("gasUsed")
        gas_price_hex = receipt.get("effectiveGasPrice") or receipt.get("gasPrice")
        if isinstance(gas_used_hex, str) and isinstance(gas_price_hex, str):
            gas_used = int(gas_used_hex, 16)
            gas_price_wei = int(gas_price_hex, 16)
            native_price = await _fetch_native_price_usd(trade.chain)
            if native_price is not None:
                gas_native = (gas_used * gas_price_wei) / 1e18
                gas_cost_usd = gas_native * native_price
                estimate = await _record_observation(
                    session,
                    chain=trade.chain,
                    tx_hash=trade.tx_hash,
                    gas_cost_usd=gas_cost_usd,
                )
                return {
                    "gas_cost_usd": float(gas_cost_usd),
                    "source": "receipt_actual",
                    "native_price_usd": float(native_price),
                    "gas_used": gas_used,
                    "effective_gas_price_wei": gas_price_wei,
                    "avg_gas_usd_1h": float(estimate.avg_gas_usd_1h) if estimate else None,
                    "p95_gas_usd_1h": float(estimate.p95_gas_usd_1h) if estimate else None,
                }

    if estimate and estimate.p95_gas_usd_1h is not None:
        return {
            "gas_cost_usd": float(estimate.p95_gas_usd_1h),
            "source": "rolling_p95_1h",
            "native_price_usd": None,
            "gas_used": None,
            "effective_gas_price_wei": None,
            "avg_gas_usd_1h": float(estimate.avg_gas_usd_1h) if estimate.avg_gas_usd_1h is not None else None,
            "p95_gas_usd_1h": float(estimate.p95_gas_usd_1h),
        }

    return {
        "gas_cost_usd": float(default_cost),
        "source": "chain_default",
        "native_price_usd": None,
        "gas_used": None,
        "effective_gas_price_wei": None,
        "avg_gas_usd_1h": float(estimate.avg_gas_usd_1h) if estimate and estimate.avg_gas_usd_1h is not None else None,
        "p95_gas_usd_1h": float(estimate.p95_gas_usd_1h) if estimate and estimate.p95_gas_usd_1h is not None else None,
    }
