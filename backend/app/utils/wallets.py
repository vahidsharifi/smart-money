from __future__ import annotations

from app.models import Wallet


async def is_wallet_ignored(session, *, chain: str, wallet_address: str | None) -> bool:
    if not wallet_address:
        return False
    normalized = wallet_address.lower()
    wallet = await session.get(Wallet, {"chain": chain, "address": normalized})
    return wallet is not None and wallet.tier == "ignore"
