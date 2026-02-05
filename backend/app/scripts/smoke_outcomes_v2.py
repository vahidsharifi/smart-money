import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from app.db import async_session
from app.models import Alert, SignalOutcome, TokenRisk, Trade
from app.utils import HttpClient
from app.worker_outcome_evaluator import _evaluate_alert_horizon


async def _cleanup(session, *, wallet: str, token: str, tx_hashes: list[str]) -> None:
    await session.execute(delete(SignalOutcome).where(SignalOutcome.alert_id.in_(select(Alert.id).where(Alert.wallet_address == wallet))))
    await session.execute(delete(Alert).where(Alert.wallet_address == wallet))
    await session.execute(delete(TokenRisk).where(TokenRisk.address == token))
    await session.execute(delete(Trade).where(Trade.tx_hash.in_(tx_hashes)))


async def main() -> None:
    now = datetime.utcnow()
    wallet = f"0x{uuid.uuid4().hex[:40]}"
    token = f"0x{uuid.uuid4().hex[:40]}"
    pair = f"0x{uuid.uuid4().hex[:40]}"
    tx_hashes = ["0x" + uuid.uuid4().hex * 2 for _ in range(4)]

    alert_time = now - timedelta(hours=7)
    t0 = alert_time + timedelta(minutes=30)
    t1 = alert_time + timedelta(minutes=80)
    t2 = alert_time + timedelta(minutes=140)
    t3 = alert_time + timedelta(minutes=220)

    history = [
        {"updated_at": t0.isoformat(), "max_suggested_size_usd": 1500, "sellable": True},
        {"updated_at": t1.isoformat(), "max_suggested_size_usd": 400, "sellable": True},
        {"updated_at": t2.isoformat(), "max_suggested_size_usd": 6000, "sellable": False},
        {"updated_at": t3.isoformat(), "max_suggested_size_usd": 3000, "sellable": True},
    ]

    async with async_session() as session:
        await _cleanup(session, wallet=wallet, token=token, tx_hashes=tx_hashes)

        alert = Alert(
            chain="ethereum",
            wallet_address=wallet,
            token_address=token,
            alert_type="trade_conviction",
            reasons={"entry_price": 1.0, "pair_address": pair},
            narrative="smoke outcomes v2",
            created_at=alert_time,
        )
        session.add(alert)
        await session.flush()

        session.add(
            TokenRisk(
                chain="ethereum",
                address=token,
                token_address=token,
                components={"history": history},
                updated_at=t3,
            )
        )

        prices = [1.05, 1.60, 1.80, 1.20]
        times = [t0, t1, t2, t3]
        for idx, (tx_hash, price, block_time) in enumerate(zip(tx_hashes, prices, times, strict=True)):
            await session.merge(
                Trade(
                    chain="ethereum",
                    tx_hash=tx_hash,
                    log_index=idx,
                    wallet_address=wallet,
                    token_address=token,
                    pair_address=pair,
                    side="BUY",
                    amount=10.0,
                    price=price,
                    decode_confidence=0.99,
                    usd_value=price * 10.0,
                    block_time=block_time,
                    created_at=block_time,
                )
            )

        await session.commit()

    async with HttpClient() as client:
        async with async_session() as session:
            alert = (await session.execute(select(Alert).where(Alert.wallet_address == wallet))).scalars().one()
            outcome = await _evaluate_alert_horizon(
                session=session,
                client=client,
                alert=alert,
                horizon_minutes=360,
            )
            if outcome is None:
                raise RuntimeError("Evaluator returned no outcome")

            raw_peak_gain = max(prices) / 1.0 - 1.0
            if outcome.exit_feasible_peak_gain is None:
                raise RuntimeError("Expected exit_feasible_peak_gain to be computed")

            exit_feasible = float(outcome.exit_feasible_peak_gain)
            if not exit_feasible < raw_peak_gain:
                raise RuntimeError(
                    f"Expected exit-feasible peak gain ({exit_feasible}) to be lower than raw peak gain ({raw_peak_gain})"
                )
            if not raw_peak_gain != exit_feasible:
                raise RuntimeError("Expected exit_feasible_peak_gain != raw_peak_gain")

            print(
                "Smoke outcomes v2 passed: "
                f"raw_peak_gain={raw_peak_gain:.4f} "
                f"exit_feasible_peak_gain={exit_feasible:.4f} "
                f"exit_feasible_peak_time={outcome.exit_feasible_peak_time}"
            )


if __name__ == "__main__":
    asyncio.run(main())
