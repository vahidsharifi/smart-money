import asyncio
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select

from app.db import async_session
from app.models import Alert, SignalOutcome, TokenRisk, Trade, Wallet, WalletMetric
from app.services.merit import run_merit_update_once
from app.worker_alerts import run_once as run_alerts_once


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
    await session.execute(
        delete(SignalOutcome).where(
            SignalOutcome.alert_id.in_(
                select(Alert.id).where(Alert.wallet_address.in_([wallet, seed_wallet]))
            )
        )
    )
    await session.execute(delete(Alert).where(Alert.wallet_address.in_([wallet, seed_wallet])))
    await session.execute(delete(Trade).where(Trade.wallet_address == wallet))
    await session.execute(delete(TokenRisk).where(TokenRisk.address.in_([token_fail, token_pass])))
    await session.execute(delete(WalletMetric).where(WalletMetric.wallet_address == wallet))
    await session.execute(delete(Wallet).where(Wallet.address.in_([wallet, seed_wallet])))


async def main() -> None:
    wallet = f"0x{uuid.uuid4().hex[:40]}".lower()
    token_outcome = f"0x{uuid.uuid4().hex[:40]}".lower()
    token_fail = f"0x{uuid.uuid4().hex[:40]}".lower()
    token_pass = f"0x{uuid.uuid4().hex[:40]}".lower()
    seed_wallet = f"0x{uuid.uuid4().hex[:40]}".lower()
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
        wallet_row = Wallet(
            chain="ethereum",
            address=wallet,
            source="seed_pack",
            prior_weight=Decimal("0.3"),
            merit_score=Decimal("0.0"),
            tier="shadow",
            tier_reason={"integrity_score": 0.95},
            ignore_reason=None,
            created_at=datetime.utcnow(),
        )
        session.add(wallet_row)
        session.add(
            WalletMetric(
                chain="ethereum",
                wallet_address=wallet,
                total_value=50_000.0,
                pnl=0.0,
                updated_at=datetime.utcnow(),
            )
        )

        alert = Alert(
            chain="ethereum",
            wallet_address=wallet,
            token_address=token_outcome,
            alert_type="trade_conviction",
            tss=70.0,
            conviction=50.0,
            reasons={"seed": True},
            narrative="seed",
            created_at=datetime.utcnow() - timedelta(hours=6),
        )
        session.add(alert)
        await session.flush()
        session.add(
            SignalOutcome(
                alert_id=alert.id,
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

        # Seed outcomes for token_pass so NetEV uses a higher derived expected move.
        seed_alert = Alert(
            chain="ethereum",
            wallet_address=seed_wallet,
            token_address=token_pass,
            alert_type="trade_conviction",
            tss=75.0,
            conviction=55.0,
            reasons={"seed": True},
            narrative="seed",
            created_at=datetime.utcnow() - timedelta(hours=12),
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
                tradeable_peak_gain=Decimal("0.25"),
                tradeable_drawdown=Decimal("-0.03"),
                net_tradeable_return_est=Decimal("0.18"),
                trap_flag=False,
                evaluated_at=datetime.utcnow(),
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

        await session.merge(
            Trade(
                chain="ethereum",
                tx_hash=tx_fail,
                log_index=0,
                wallet_address=wallet,
                token_address=token_fail,
                side="BUY",
                amount=10.0,
                price=1.0,
                usd_value=120.0,
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
                amount=10.0,
                price=1.0,
                usd_value=500.0,
                block_time=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

    async with async_session() as session:
        wallet_before = await session.get(Wallet, {"chain": "ethereum", "address": wallet})
        before_merit = float(wallet_before.merit_score or 0.0)
        await run_merit_update_once(session)
        await session.commit()

    async with async_session() as session:
        wallet_after = await session.get(Wallet, {"chain": "ethereum", "address": wallet})
        after_merit = float(wallet_after.merit_score or 0.0)
        if not after_merit > before_merit:
            raise RuntimeError(f"Merit score did not increase: before={before_merit} after={after_merit}")

    created_count = await run_alerts_once()

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
            raise RuntimeError("NetEV fail scenario unexpectedly produced an alert.")
        if pass_alert is None:
            raise RuntimeError("NetEV pass scenario did not produce an alert.")

    print(
        "Smoke merit+netev passed: "
        f"merit_before={before_merit:.6f} merit_after={after_merit:.6f} alerts_created={created_count}"
    )


if __name__ == "__main__":
    asyncio.run(main())
