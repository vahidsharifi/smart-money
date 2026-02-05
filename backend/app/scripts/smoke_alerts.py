import asyncio
from app.utils import random_evm_address
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.db import async_session
from app.models import Alert, SignalOutcome, TokenRisk, Trade, WalletMetric
from app.worker_alerts import run_once


async def main() -> None:
    wallet_address = random_evm_address()
    token_address = random_evm_address()
    tx_hash = "0x" + "d" * 64
    seed_wallet = random_evm_address()

    async with async_session() as session:
        session.add(
            WalletMetric(
                chain="ethereum",
                wallet_address=wallet_address,
                total_value=25_000.0,
                pnl=0.0,
                updated_at=datetime.utcnow(),
            )
        )
        seed_alert = Alert(
            chain="ethereum",
            wallet_address=seed_wallet,
            token_address=token_address,
            alert_type="trade_conviction",
            tss=70.0,
            conviction=50.0,
            reasons={"seed": True},
            narrative="seed",
            created_at=datetime.utcnow() - timedelta(hours=6),
        )
        session.add(seed_alert)
        await session.flush()
        session.add(
            SignalOutcome(
                alert_id=seed_alert.id,
                horizon_minutes=360,
                was_sellable_entire_window=True,
                min_exit_slippage_1k=Decimal("0.01"),
                max_exit_slippage_1k=Decimal("0.02"),
                tradeable_peak_gain=Decimal("0.22"),
                tradeable_drawdown=Decimal("-0.04"),
                net_tradeable_return_est=Decimal("0.15"),
                trap_flag=False,
                evaluated_at=datetime.utcnow(),
            )
        )
        session.add(
            TokenRisk(
                chain="ethereum",
                address=token_address,
                score=8.0,
                components={"tss": {"score": 8.0}},
                updated_at=datetime.utcnow(),
            )
        )
        await session.merge(
            Trade(
                chain="ethereum",
                tx_hash=tx_hash,
                log_index=0,
                wallet_address=wallet_address,
                token_address=token_address,
                side="BUY",
                amount=5.0,
                price=100.0,
                usd_value=500.0,
                block_time=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

    await run_once()

    async with async_session() as session:
        result = await session.execute(
            select(Alert)
            .where(
                Alert.chain == "ethereum",
                Alert.wallet_address == wallet_address,
                Alert.token_address == token_address,
                Alert.alert_type == "trade_conviction",
            )
            .limit(1)
        )
        alert = result.scalar_one_or_none()
        if alert is None:
            raise RuntimeError("Alert not found after alerts worker run.")
        if not isinstance(alert.reasons, dict):
            raise RuntimeError("Alert reasons missing or not JSON.")
        if "tss" not in alert.reasons:
            raise RuntimeError("Alert reasons missing tss data.")

        print(
            "Alerts smoke test passed: "
            f"conviction={alert.reasons.get('conviction')} tss={alert.reasons.get('tss')}"
        )


if __name__ == "__main__":
    asyncio.run(main())
