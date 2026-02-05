import asyncio
import uuid
from app.utils import random_evm_address
from datetime import datetime
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, select

from app.db import async_session
from app.models import Alert, SignalOutcome, TokenRisk, Trade, WalletMetric
from app.worker_alerts import _netev_gate, run_once as run_alerts_once


async def _reset_rows(
    session,
    *,
    wallet: str,
    seed_wallet: str,
    token_fail: str,
    token_pass: str,
    tx_fail: str,
    tx_pass: str,
) -> None:
    await session.execute(delete(SignalOutcome).where(SignalOutcome.alert_id.in_(select(Alert.id).where(Alert.wallet_address.in_([wallet, seed_wallet])))))
    await session.execute(delete(Alert).where(Alert.wallet_address.in_([wallet, seed_wallet])))
    await session.execute(delete(Trade).where(Trade.wallet_address == wallet))
    await session.execute(delete(TokenRisk).where(TokenRisk.address.in_([token_fail, token_pass])))
    await session.execute(delete(WalletMetric).where(WalletMetric.wallet_address == wallet))


async def main() -> None:
    wallet = random_evm_address()
    token_fail = random_evm_address()
    token_pass = random_evm_address()
    seed_wallet = random_evm_address()
    tx_fail = "0x" + uuid.uuid4().hex * 2
    tx_pass = "0x" + uuid.uuid4().hex * 2

    async with async_session() as session:
        await _reset_rows(
            session,
            wallet=wallet,
            seed_wallet=seed_wallet,
            token_fail=token_fail,
            token_pass=token_pass,
            tx_fail=tx_fail,
            tx_pass=tx_pass,
        )
        session.add(
            WalletMetric(
                chain="ethereum",
                wallet_address=wallet,
                total_value=50_000.0,
                pnl=0.0,
                updated_at=datetime.utcnow(),
            )
        )
        session.add(
            TokenRisk(
                chain="ethereum",
                address=token_fail,
                score=85.0,
                components={"tss": {"score": 85.0}},
                updated_at=datetime.utcnow(),
            )
        )
        session.add(
            TokenRisk(
                chain="ethereum",
                address=token_pass,
                score=85.0,
                components={"tss": {"score": 85.0}},
                updated_at=datetime.utcnow(),
            )
        )
        seed_alert = Alert(
            chain="ethereum",
            wallet_address=seed_wallet,
            token_address=token_pass,
            alert_type="trade_conviction",
            tss=75.0,
            conviction=55.0,
            reasons={"seed": True},
            narrative="seed",
            created_at=datetime.utcnow(),
        )
        session.add(seed_alert)
        await session.flush()
        session.add(
            SignalOutcome(
                alert_id=seed_alert.id,
                horizon_minutes=360,
                was_sellable_entire_window=True,
                tradeable_peak_gain=0.20,
                tradeable_drawdown=-0.05,
                net_tradeable_return_est=0.15,
                trap_flag=False,
                evaluated_at=datetime.utcnow(),
            )
        )
        await session.merge(
            Trade(
                chain="ethereum",
                tx_hash=tx_fail,
                log_index=0,
                wallet_address=wallet,
                token_address=token_fail,
                side="BUY",
                amount=20.0,
                price=1.0,
                usd_value=500.0,
                block_time=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        await session.merge(
            Trade(
                chain="ethereum",
                tx_hash=tx_pass,
                log_index=0,
                wallet_address=wallet,
                token_address=token_pass,
                side="BUY",
                amount=20.0,
                price=1.0,
                usd_value=500.0,
                block_time=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

    high_gas = {
        "gas_cost_usd": 80.0,
        "source": "mock",
        "native_price_usd": 3000.0,
        "gas_used": 210000,
        "effective_gas_price_wei": 1,
        "avg_gas_usd_1h": 50.0,
        "p95_gas_usd_1h": 75.0,
    }
    low_gas = {
        "gas_cost_usd": 5.0,
        "source": "mock",
        "native_price_usd": 3000.0,
        "gas_used": 120000,
        "effective_gas_price_wei": 1,
        "avg_gas_usd_1h": 4.0,
        "p95_gas_usd_1h": 6.0,
    }

    async with async_session() as session:
        fail_trade = await session.get(Trade, {"chain": "ethereum", "tx_hash": tx_fail, "log_index": 0})
        fail_risk = await session.get(TokenRisk, {"chain": "ethereum", "address": token_fail})
        with patch("app.worker_alerts.estimate_trade_gas_cost", AsyncMock(return_value=high_gas)):
            passed, debug = await _netev_gate(session, trade=fail_trade, token_risk=fail_risk)
        if passed:
            raise RuntimeError("Expected high gas scenario to fail NetEV gate.")
        required = ["netev_usd", "netev_roi", "gas_cost_usd", "expected_move", "min_usd_profit", "min_roi_after_costs"]
        missing = [k for k in required if k not in debug]
        if missing:
            raise RuntimeError(f"Missing NetEV debug fields for fail scenario: {missing}")

    async def _gas_for_trade(session, *, trade):
        return high_gas if trade.tx_hash == tx_fail else low_gas

    with patch("app.worker_alerts.estimate_trade_gas_cost", AsyncMock(side_effect=_gas_for_trade)):
        created = await run_alerts_once()

    async with async_session() as session:
        fail_alert = (
            await session.execute(
                select(Alert).where(
                    Alert.wallet_address == wallet,
                    Alert.token_address == token_fail,
                    Alert.alert_type == "trade_conviction",
                )
            )
        ).scalars().first()
        pass_alert = (
            await session.execute(
                select(Alert).where(
                    Alert.wallet_address == wallet,
                    Alert.token_address == token_pass,
                    Alert.alert_type == "trade_conviction",
                )
            )
        ).scalars().first()

        if fail_alert is not None:
            raise RuntimeError("High gas NetEV failure unexpectedly produced an alert.")
        if pass_alert is None:
            raise RuntimeError("Low gas NetEV pass did not produce an alert.")

        netev = (pass_alert.reasons or {}).get("netev", {})
        if netev.get("gas_cost_source") != "mock":
            raise RuntimeError("Expected alert reasons to include NetEV v2 gas breakdown fields.")

    print(f"Smoke NetEV v2 passed: alerts_created={created} fail_passed={False} pass_alert_created={True}")


if __name__ == "__main__":
    asyncio.run(main())
