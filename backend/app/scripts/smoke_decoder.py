import asyncio

from sqlalchemy import select

from app.db import async_session
from app.models import Trade
from app.scripts import smoke_listener
from app.worker_decoder import run_once

FAKE_TX_HASH = "0x" + "1" * 64


async def main() -> None:
    await smoke_listener.main()
    await run_once()

    async with async_session() as session:
        result = await session.execute(select(Trade).where(Trade.tx_hash == FAKE_TX_HASH))
        trade = result.scalar_one_or_none()
        if trade is None:
            raise RuntimeError("Decoded trade not found for fake tx hash")
        print(f"Decoder stored trade for tx_hash={FAKE_TX_HASH}")


if __name__ == "__main__":
    asyncio.run(main())
