from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from sqlalchemy import select

from app.db import async_session
from app.models import Wallet, WatchPair

logger = logging.getLogger(__name__)

SEED_PACK_SOURCE = "seed_pack"
SEED_PACK_FILENAMES = {
    "watched_pools": "watched_pools.csv",
    "seed_wallets": "seed_wallets.csv",
    "ignore_list": "ignore_list.csv",
}


@dataclass(frozen=True)
class SeedPackPaths:
    watched_pools: Path
    seed_wallets: Path
    ignore_list: Path


def _clean_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _normalize_address(value: str | None) -> str | None:
    cleaned = _clean_value(value)
    if cleaned is None:
        return None
    from app.utils import normalize_evm_address

    return normalize_evm_address(cleaned)


def resolve_seed_pack_paths(base_dir: Path | None = None) -> SeedPackPaths:
    search_root = base_dir or Path.cwd()
    candidates = [search_root, search_root / "seed_pack"]
    resolved: dict[str, Path] = {}
    for key, filename in SEED_PACK_FILENAMES.items():
        for candidate in candidates:
            path = candidate / filename
            if path.exists():
                resolved[key] = path
                break
    missing = [key for key in SEED_PACK_FILENAMES if key not in resolved]
    if missing:
        missing_names = ", ".join(SEED_PACK_FILENAMES[key] for key in missing)
        raise FileNotFoundError(
            f"Missing seed pack CSVs ({missing_names}). Expected in {candidates[0]} or {candidates[1]}."
        )
    return SeedPackPaths(
        watched_pools=resolved["watched_pools"],
        seed_wallets=resolved["seed_wallets"],
        ignore_list=resolved["ignore_list"],
    )


def _read_csv(path: Path) -> Iterable[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row:
                continue
            yield {key: (value or "") for key, value in row.items()}


def _build_warm_start_reason(row: dict[str, str]) -> dict:
    return {
        "source": SEED_PACK_SOURCE,
        "type": "warm_start",
        "label": _clean_value(row.get("label_or_category_guess")),
        "why_included": _clean_value(row.get("why_included")),
        "evidence_sources": _clean_value(row.get("evidence_sources")),
        "date_observed": _clean_value(row.get("date_observed")),
    }


def _build_ignore_reason(row: dict[str, str]) -> dict:
    return {
        "source": SEED_PACK_SOURCE,
        "type": "ignore",
        "ignore_type": _clean_value(row.get("type")),
        "sources": _clean_value(row.get("sources")),
        "date_observed": _clean_value(row.get("date_observed")),
        "notes": _clean_value(row.get("notes")),
    }


async def import_watch_pairs(path: Path) -> int:
    expires_at = datetime.utcnow() + timedelta(days=30)
    imported = 0
    async with async_session() as session:
        for row in _read_csv(path):
            chain = (_clean_value(row.get("chain")) or "ethereum").lower()
            pair_address = _normalize_address(row.get("pool_address") or row.get("pair_address"))
            if not pair_address:
                continue
            dex = _clean_value(row.get("dex"))
            token0_symbol = _clean_value(row.get("token0_symbol"))
            token1_symbol = _clean_value(row.get("token1_symbol"))
            token0_address = _normalize_address(row.get("token0_address"))
            token1_address = _normalize_address(row.get("token1_address"))

            existing = await session.get(
                WatchPair, {"chain": chain, "pair_address": pair_address}
            )
            if existing:
                if dex:
                    existing.dex = dex
                if token0_symbol:
                    existing.token0_symbol = token0_symbol
                if token1_symbol:
                    existing.token1_symbol = token1_symbol
                if token0_address:
                    existing.token0_address = token0_address
                if token1_address:
                    existing.token1_address = token1_address
                existing.source = SEED_PACK_SOURCE
                existing.priority = 100
                existing.expires_at = expires_at
            else:
                session.add(
                    WatchPair(
                        chain=chain,
                        pair_address=pair_address,
                        dex=dex,
                        token0_symbol=token0_symbol,
                        token0_address=token0_address,
                        token1_symbol=token1_symbol,
                        token1_address=token1_address,
                        source=SEED_PACK_SOURCE,
                        priority=100,
                        expires_at=expires_at,
                        last_seen=None,
                    )
                )
            imported += 1
        await session.commit()
    return imported


async def import_seed_wallets(path: Path) -> int:
    imported = 0
    async with async_session() as session:
        for row in _read_csv(path):
            chain = (_clean_value(row.get("chain")) or "ethereum").lower()
            address = _normalize_address(row.get("address"))
            if not address:
                continue
            existing = await session.get(Wallet, {"chain": chain, "address": address})
            if existing and existing.tier == "ignore":
                logger.info("seed_wallet_skip_ignore chain=%s address=%s", chain, address)
                continue
            tier_reason = _build_warm_start_reason(row)
            if existing:
                existing.source = SEED_PACK_SOURCE
                existing.prior_weight = Decimal("0.3")
                existing.merit_score = Decimal("0.0")
                existing.tier = "shadow"
                existing.tier_reason = tier_reason
            else:
                session.add(
                    Wallet(
                        chain=chain,
                        address=address,
                        source=SEED_PACK_SOURCE,
                        prior_weight=Decimal("0.3"),
                        merit_score=Decimal("0.0"),
                        tier="shadow",
                        tier_reason=tier_reason,
                        ignore_reason=None,
                    )
                )
            imported += 1
        await session.commit()
    return imported


async def import_ignore_list(path: Path) -> int:
    imported = 0
    async with async_session() as session:
        for row in _read_csv(path):
            chain = (_clean_value(row.get("chain")) or "ethereum").lower()
            address = _normalize_address(row.get("address"))
            if not address:
                continue
            ignore_type = _clean_value(row.get("type"))
            notes = _clean_value(row.get("notes"))
            ignore_reason = None
            if ignore_type and notes:
                ignore_reason = f"{ignore_type}: {notes}"
            else:
                ignore_reason = ignore_type or notes
            tier_reason = _build_ignore_reason(row)

            existing = await session.get(Wallet, {"chain": chain, "address": address})
            if existing:
                existing.source = SEED_PACK_SOURCE
                existing.tier = "ignore"
                existing.ignore_reason = ignore_reason
                existing.tier_reason = tier_reason
            else:
                session.add(
                    Wallet(
                        chain=chain,
                        address=address,
                        source=SEED_PACK_SOURCE,
                        prior_weight=Decimal("0.0"),
                        merit_score=Decimal("0.0"),
                        tier="ignore",
                        tier_reason=tier_reason,
                        ignore_reason=ignore_reason,
                    )
                )
            imported += 1
        await session.commit()
    return imported


async def run_seed_import(base_dir: Path | None = None) -> dict[str, int]:
    paths = resolve_seed_pack_paths(base_dir=base_dir)
    logger.info("seed_import_start watched_pools=%s", paths.watched_pools)
    watched = await import_watch_pairs(paths.watched_pools)
    wallets = await import_seed_wallets(paths.seed_wallets)
    ignored = await import_ignore_list(paths.ignore_list)
    logger.info(
        "seed_import_complete watched_pools=%s seed_wallets=%s ignore_list=%s",
        watched,
        wallets,
        ignored,
    )
    return {
        "watched_pools": watched,
        "seed_wallets": wallets,
        "ignore_list": ignored,
    }


async def _validate_seed_pack(paths: SeedPackPaths) -> None:
    for label, path in (
        ("watched_pools", paths.watched_pools),
        ("seed_wallets", paths.seed_wallets),
        ("ignore_list", paths.ignore_list),
    ):
        logger.info("seed_pack_path label=%s path=%s", label, path)


async def main() -> None:
    paths = resolve_seed_pack_paths()
    await _validate_seed_pack(paths)
    await run_seed_import(base_dir=paths.watched_pools.parent)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
