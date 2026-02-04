import asyncio
import uuid
from datetime import datetime

from app.db import async_session
from app.models import Wallet


async def main() -> None:
    wallet_address = f"0x{uuid.uuid4().hex[:40]}"
    async with async_session() as session:
        wallet = Wallet(chain="ethereum", address=wallet_address, created_at=datetime.utcnow())
        session.add(wallet)
        await session.commit()

        fetched = await session.get(Wallet, {"chain": "ethereum", "address": wallet_address})
        if fetched is None:
            raise RuntimeError("Wallet row not found after insert.")

        print(f"Inserted wallet: {fetched.chain}:{fetched.address}")


if __name__ == "__main__":
    asyncio.run(main())
