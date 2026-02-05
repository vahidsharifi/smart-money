import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select

from app.db import async_session
from app.models import Alert
from app.narrator import narrate_alert


async def main() -> None:
    wallet_address = f"0x{uuid.uuid4().hex[:40]}"
    token_address = f"0x{uuid.uuid4().hex[:40]}"
    reasons = {
        "tss": 7.2,
        "conviction": 62.5,
        "regime": "momentum",
        "reasons": ["steady buying flow", "wallet tier increase"],
        "risks": ["liquidity concentrated", "large holder exposure"],
        "invalidation": ["tss drops below 5", "wallet tier falls to ignore"],
    }

    narrative = await narrate_alert(reasons)
    print(f"Narrative: {narrative}")

    async with async_session() as session:
        session.add(
            Alert(
                chain="ethereum",
                wallet_address=wallet_address,
                token_address=token_address,
                alert_type="narrator_smoke",
                reasons=reasons,
                narrative=narrative,
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

    async with async_session() as session:
        result = await session.execute(
            select(Alert)
            .where(
                Alert.chain == "ethereum",
                Alert.wallet_address == wallet_address,
                Alert.token_address == token_address,
                Alert.alert_type == "narrator_smoke",
            )
            .limit(1)
        )
        alert = result.scalar_one_or_none()
        if alert is None:
            raise RuntimeError("Narrator smoke alert not found.")
        if not alert.narrative:
            raise RuntimeError("Narrator smoke alert missing narrative.")

        print("Narrator smoke test passed.")


if __name__ == "__main__":
    asyncio.run(main())
