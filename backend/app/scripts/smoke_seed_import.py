import asyncio
import csv
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from app.db import async_session
from app.models import Alert, Trade, Wallet, WatchPair
from app.services.seed_importer import resolve_seed_pack_paths, run_seed_import
from app.worker_alerts import run_once as run_alerts_once


def _read_first_row(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row:
                return {key: (value or "") for key, value in row.items()}
    raise RuntimeError(f"No rows found in {path}")


async def _verify_watch_pair(paths) -> None:
    row = _read_first_row(paths.watched_pools)
    chain = (row.get("chain") or "ethereum").lower()
    pair_address = (row.get("pool_address") or row.get("pair_address") or "").lower()
    async with async_session() as session:
        watch_pair = await session.get(
            WatchPair, {"chain": chain, "pair_address": pair_address}
        )
        if watch_pair is None:
            raise RuntimeError("Seed watch pair not found in watch_pairs")
        print(f"Seed watch pair found: {watch_pair.pair_address}")


async def _verify_seed_wallet(paths) -> str:
    row = _read_first_row(paths.seed_wallets)
    chain = (row.get("chain") or "ethereum").lower()
    address = (row.get("address") or "").lower()
    async with async_session() as session:
        wallet = await session.get(Wallet, {"chain": chain, "address": address})
        if wallet is None:
            raise RuntimeError("Seed wallet not found in wallets")
        if wallet.tier != "shadow":
            raise RuntimeError("Seed wallet tier is not shadow")
        if float(wallet.prior_weight or 0.0) != 0.3:
            raise RuntimeError("Seed wallet prior_weight not set to 0.3")
        print(f"Seed wallet found: {wallet.address} tier={wallet.tier}")
    return address


async def _verify_ignore_wallet(paths) -> str:
    row = _read_first_row(paths.ignore_list)
    chain = (row.get("chain") or "ethereum").lower()
    address = (row.get("address") or "").lower()
    async with async_session() as session:
        wallet = await session.get(Wallet, {"chain": chain, "address": address})
        if wallet is None:
            raise RuntimeError("Ignore wallet not found in wallets")
        if wallet.tier != "ignore":
            raise RuntimeError("Ignore wallet tier is not ignore")
        print(f"Ignore wallet found: {wallet.address} tier={wallet.tier}")
    return address


async def _verify_ignored_wallet_alert_block(ignore_wallet: str) -> None:
    tx_hash = "0x" + "2" * 64
    token_address = "0x" + "3" * 40
    async with async_session() as session:
        session.add(
            Trade(
                chain="ethereum",
                tx_hash=tx_hash,
                log_index=0,
                block_number=12_345_678,
                wallet_address=ignore_wallet,
                token_address=token_address,
                side="BUY",
                amount=1.0,
                price=1.0,
                usd_value=1.0,
                block_time=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
        )
        await session.commit()

    async with async_session() as session:
        before = await session.execute(
            select(func.count(Alert.id)).where(Alert.wallet_address == ignore_wallet)
        )
        before_count = before.scalar_one() or 0

    await run_alerts_once()

    async with async_session() as session:
        after = await session.execute(
            select(func.count(Alert.id)).where(Alert.wallet_address == ignore_wallet)
        )
        after_count = after.scalar_one() or 0
        if after_count != before_count:
            raise RuntimeError("Ignored wallet created an alert unexpectedly")
        print("Ignored wallet trade did not create alert (expected)")


async def main() -> None:
    paths = resolve_seed_pack_paths()
    await run_seed_import(base_dir=paths.watched_pools.parent)
    await _verify_watch_pair(paths)
    await _verify_seed_wallet(paths)
    ignore_wallet = await _verify_ignore_wallet(paths)
    await _verify_ignored_wallet_alert_block(ignore_wallet)


if __name__ == "__main__":
    asyncio.run(main())
