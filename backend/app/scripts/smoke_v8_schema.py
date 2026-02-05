import asyncio
from app.utils import random_evm_address
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.db import async_session
from app.models import Alert, SignalOutcome, Wallet, WatchPair


def _sample_address() -> str:
    return random_evm_address()


async def main() -> None:
    async with async_session() as session:
        watch_pair = WatchPair(
            chain="ethereum",
            pair_address=_sample_address(),
            dex="uniswap",
            token0_symbol="AAA",
            token0_address=_sample_address(),
            token1_symbol="BBB",
            token1_address=_sample_address(),
            source="seed_pack",
            priority=1,
            expires_at=datetime.utcnow() + timedelta(days=1),
            last_seen=datetime.utcnow(),
        )
        session.add(watch_pair)

        wallet = Wallet(
            chain="ethereum",
            address=_sample_address(),
            source="manual",
            prior_weight=Decimal("0.42"),
            merit_score=Decimal("0.88"),
            tier="titan",
            tier_reason={"source": "smoke"},
            ignore_reason=None,
            created_at=datetime.utcnow(),
        )
        session.add(wallet)

        result = await session.execute(select(Alert).limit(1))
        alert = result.scalar_one_or_none()
        if alert is None:
            alert = Alert(
                chain="ethereum",
                wallet_address=_sample_address(),
                token_address=_sample_address(),
                alert_type="smoke",
                reasons={"source": "smoke"},
                created_at=datetime.utcnow(),
            )
            session.add(alert)
            await session.flush()

        outcome = SignalOutcome(
            alert_id=alert.id,
            horizon_minutes=30,
            was_sellable_entire_window=True,
            min_exit_slippage_1k=Decimal("0.01"),
            max_exit_slippage_1k=Decimal("0.04"),
            tradeable_peak_gain=Decimal("0.25"),
            tradeable_drawdown=Decimal("0.12"),
            net_tradeable_return_est=Decimal("0.18"),
            trap_flag=False,
            evaluated_at=datetime.utcnow(),
        )
        session.add(outcome)

        await session.commit()

        print("Inserted watch_pairs, wallets, and signal_outcomes rows successfully.")


if __name__ == "__main__":
    asyncio.run(main())
