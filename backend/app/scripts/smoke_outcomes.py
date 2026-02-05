import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db import async_session
from app.models import Alert, SignalOutcome, TokenRisk, Trade
from app.worker_outcome_evaluator import HORIZONS_MINUTES, run_outcome_evaluator_once


async def main() -> None:
    wallet_address = f"0x{uuid.uuid4().hex[:40]}"
    token_address = f"0x{uuid.uuid4().hex[:40]}"
    alert_time = datetime.utcnow() - timedelta(hours=30)

    async with async_session() as session:
        alert = Alert(
            chain="ethereum",
            wallet_address=wallet_address,
            token_address=token_address,
            alert_type="trade_conviction",
            tss=70.0,
            conviction=65.0,
            reasons={"entry_price": 1.0, "source": "smoke_outcomes"},
            created_at=alert_time,
        )
        session.add(alert)

        risk_history = [
            {
                "updated_at": (alert_time + timedelta(minutes=10)).isoformat(),
                "flags": [],
                "max_suggested_size_usd": 20_000,
                "liquidity_usd": 200_000,
            },
            {
                "updated_at": (alert_time + timedelta(hours=6)).isoformat(),
                "flags": [],
                "max_suggested_size_usd": 15_000,
                "liquidity_usd": 120_000,
            },
            {
                "updated_at": (alert_time + timedelta(hours=20)).isoformat(),
                "flags": [],
                "max_suggested_size_usd": 10_000,
                "liquidity_usd": 90_000,
            },
        ]
        session.add(
            TokenRisk(
                chain="ethereum",
                address=token_address,
                token_address=token_address,
                score=75.0,
                tss=75.0,
                flags=[],
                components={"history": risk_history, "max_suggested_size_usd": 12_000},
                updated_at=datetime.utcnow(),
            )
        )

        prices = [1.00, 1.08, 1.02, 1.12, 0.96, 1.18]
        minutes_offsets = [0, 15, 120, 600, 1200, 1500]
        for idx, price in enumerate(prices):
            session.add(
                Trade(
                    chain="ethereum",
                    tx_hash=f"0x{uuid.uuid4().hex}{idx:02x}",
                    log_index=0,
                    wallet_address=wallet_address,
                    token_address=token_address,
                    side="BUY",
                    amount=100.0,
                    price=price,
                    usd_value=price * 100,
                    block_time=alert_time + timedelta(minutes=minutes_offsets[idx]),
                    created_at=datetime.utcnow(),
                )
            )

        await session.commit()
        alert_id = alert.id

    await run_outcome_evaluator_once()

    async with async_session() as session:
        rows = (
            await session.execute(
                select(SignalOutcome)
                .where(SignalOutcome.alert_id == alert_id)
                .order_by(SignalOutcome.horizon_minutes.asc())
            )
        ).scalars().all()

    if len(rows) != len(HORIZONS_MINUTES):
        raise RuntimeError(
            f"Expected {len(HORIZONS_MINUTES)} outcomes, found {len(rows)} for alert={alert_id}."
        )

    horizons = {row.horizon_minutes for row in rows}
    if horizons != set(HORIZONS_MINUTES):
        raise RuntimeError(f"Unexpected horizons: {horizons}")

    for row in rows:
        if row.net_tradeable_return_est is None:
            raise RuntimeError(f"Missing net_tradeable_return_est for horizon {row.horizon_minutes}")

    print(f"Outcome smoke test passed: alert={alert_id} horizons={sorted(horizons)}")


if __name__ == "__main__":
    asyncio.run(main())
