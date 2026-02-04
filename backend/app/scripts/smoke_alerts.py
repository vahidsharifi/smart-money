import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select

from app.db import async_session
from app.models import Alert, TokenRisk, Trade, WalletMetric
from app.worker_alerts import run_once


async def main() -> None:
    wallet_address = f"0x{uuid.uuid4().hex[:40]}"
    token_address = f"0x{uuid.uuid4().hex[:40]}"
    tx_hash = "0x" + "d" * 64

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
                price=2.0,
                usd_value=10.0,
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
