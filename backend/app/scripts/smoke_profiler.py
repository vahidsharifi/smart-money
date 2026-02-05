import asyncio
from app.utils import random_evm_address
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db import async_session
from app.models import Position, Trade, WalletMetric
from app.worker_profiler import run_once


async def main() -> None:
    wallet_address = random_evm_address()
    token_address = random_evm_address()
    base_time = datetime.utcnow() - timedelta(hours=1)

    trades = [
        Trade(
            chain="ethereum",
            tx_hash="0x" + "a" * 64,
            log_index=0,
            wallet_address=wallet_address,
            token_address=token_address,
            side="BUY",
            amount=10.0,
            price=2.0,
            usd_value=20.0,
            block_time=base_time,
            created_at=base_time,
        ),
        Trade(
            chain="ethereum",
            tx_hash="0x" + "b" * 64,
            log_index=0,
            wallet_address=wallet_address,
            token_address=token_address,
            side="BUY",
            amount=5.0,
            price=4.0,
            usd_value=20.0,
            block_time=base_time + timedelta(minutes=10),
            created_at=base_time + timedelta(minutes=10),
        ),
        Trade(
            chain="ethereum",
            tx_hash="0x" + "c" * 64,
            log_index=0,
            wallet_address=wallet_address,
            token_address=token_address,
            side="SELL",
            amount=3.0,
            price=3.0,
            usd_value=9.0,
            block_time=base_time + timedelta(minutes=20),
            created_at=base_time + timedelta(minutes=20),
        ),
    ]

    async with async_session() as session:
        session.add_all(trades)
        await session.commit()

    await run_once()

    async with async_session() as session:
        position_result = await session.execute(
            select(Position)
            .where(
                Position.chain == "ethereum",
                Position.wallet_address == wallet_address,
                Position.token_address == token_address,
            )
            .limit(1)
        )
        position = position_result.scalar_one_or_none()
        if position is None:
            raise RuntimeError("Position not found after profiler run.")

        metric_result = await session.execute(
            select(WalletMetric)
            .where(
                WalletMetric.chain == "ethereum",
                WalletMetric.wallet_address == wallet_address,
            )
            .limit(1)
        )
        metric = metric_result.scalar_one_or_none()
        if metric is None:
            raise RuntimeError("Wallet metrics not found after profiler run.")

        expected_quantity = 12.0
        expected_avg_price = (10.0 * 2.0 + 5.0 * 4.0) / 15.0
        expected_total_value = expected_quantity * expected_avg_price

        if abs(position.quantity - expected_quantity) > 1e-6:
            raise RuntimeError(
                f"Unexpected position quantity {position.quantity} (expected {expected_quantity})."
            )
        if position.average_price is None or abs(position.average_price - expected_avg_price) > 1e-6:
            raise RuntimeError(
                f"Unexpected average price {position.average_price} (expected {expected_avg_price})."
            )
        if metric.total_value is None or abs(metric.total_value - expected_total_value) > 1e-6:
            raise RuntimeError(
                f"Unexpected total value {metric.total_value} (expected {expected_total_value})."
            )

        print(
            "Profiler smoke test passed: "
            f"qty={position.quantity}, avg={position.average_price}, total={metric.total_value}"
        )


if __name__ == "__main__":
    asyncio.run(main())
