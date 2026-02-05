import asyncio
from app.utils import random_evm_address
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select

from app.db import async_session
from app.models import Alert, SignalOutcome, Wallet
from app.services.merit import run_merit_update_once


async def _reset_rows(session, *, wallets: list[str], token: str) -> None:
    await session.execute(
        delete(SignalOutcome).where(
            SignalOutcome.alert_id.in_(select(Alert.id).where(Alert.wallet_address.in_(wallets)))
        )
    )
    await session.execute(delete(Alert).where(Alert.wallet_address.in_(wallets)))
    await session.execute(delete(Wallet).where(Wallet.address.in_(wallets)))
    await session.execute(delete(Alert).where(Alert.token_address == token))


async def main() -> None:
    token = random_evm_address()
    wallet_a = random_evm_address()
    wallet_b = random_evm_address()
    wallet_c = random_evm_address()
    wallets = [wallet_a, wallet_b, wallet_c]

    now = datetime.utcnow()
    t1 = now - timedelta(minutes=30)
    t2 = t1 + timedelta(minutes=2)
    t3 = t2 + timedelta(minutes=2)

    async with async_session() as session:
        await _reset_rows(session, wallets=wallets, token=token)

        for address in wallets:
            session.add(
                Wallet(
                    chain="ethereum",
                    address=address,
                    source="autopilot",
                    prior_weight=Decimal("0.0"),
                    merit_score=Decimal("0.10"),
                    tier="shadow",
                    tier_reason={"integrity_score": 0.95},
                    ignore_reason=None,
                    created_at=now,
                )
            )

        alerts: list[Alert] = []
        for address, created_at in ((wallet_a, t1), (wallet_b, t2), (wallet_c, t3)):
            alert = Alert(
                chain="ethereum",
                wallet_address=address,
                token_address=token,
                alert_type="trade_conviction",
                tss=80.0,
                conviction=50.0,
                reasons={"smoke": "merit_v2"},
                narrative="smoke merit v2",
                created_at=created_at,
            )
            session.add(alert)
            alerts.append(alert)

        await session.flush()

        for alert in alerts:
            session.add(
                SignalOutcome(
                    alert_id=alert.id,
                    horizon_minutes=360,
                    was_sellable_entire_window=True,
                    min_exit_slippage_1k=Decimal("0.01"),
                    max_exit_slippage_1k=Decimal("0.02"),
                    tradeable_peak_gain=Decimal("0.22"),
                    tradeable_drawdown=Decimal("-0.04"),
                    net_tradeable_return_est=Decimal("0.20"),
                    trap_flag=False,
                    evaluated_at=now,
                )
            )

        await session.commit()

    async with async_session() as session:
        await run_merit_update_once(session)
        await session.commit()

    async with async_session() as session:
        a = await session.get(Wallet, {"chain": "ethereum", "address": wallet_a})
        b = await session.get(Wallet, {"chain": "ethereum", "address": wallet_b})
        c = await session.get(Wallet, {"chain": "ethereum", "address": wallet_c})

        merit_a = float(a.merit_score or 0.0)
        merit_b = float(b.merit_score or 0.0)
        merit_c = float(c.merit_score or 0.0)

        if not (merit_a > merit_b > merit_c):
            raise RuntimeError(
                "Merit v2 ordering failed: "
                f"A={merit_a:.6f} B={merit_b:.6f} C={merit_c:.6f}"
            )

        print(
            "Smoke merit v2 passed: "
            f"A={merit_a:.6f} B={merit_b:.6f} C={merit_c:.6f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
