from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Alert, SignalOutcome, Wallet


@dataclass(frozen=True)
class WalletOutcomeStats:
    chain: str
    wallet_address: str
    sample_size: int
    positive_count: int
    avg_return: float


def _to_decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 8)))


def _as_float(value: Decimal | float | None) -> float:
    if value is None:
        return 0.0
    return float(value)


def _clamp_return(value: float) -> float:
    return max(
        settings.merit_return_clamp_min,
        min(settings.merit_return_clamp_max, value),
    )


def _baseline_prior(wallet: Wallet) -> float:
    prior_weight = max(0.0, _as_float(wallet.prior_weight))
    return prior_weight * settings.merit_prior_constant


def _flags_from_reason(wallet: Wallet) -> tuple[bool, bool, float]:
    reason = wallet.tier_reason if isinstance(wallet.tier_reason, dict) else {}
    bot_suspect = bool(reason.get("bot_suspect", False))
    copycat_dominant = bool(reason.get("copycat_dominant", False))
    integrity_score = float(reason.get("integrity_score", 1.0) or 0.0)
    return bot_suspect, copycat_dominant, integrity_score


def _next_tier(wallet: Wallet, merit_score: float, stats: WalletOutcomeStats) -> tuple[str | None, dict]:
    current = wallet.tier
    bot_suspect, copycat_dominant, integrity_score = _flags_from_reason(wallet)

    rationale = {
        "updated_at": datetime.utcnow().isoformat(),
        "from_tier": current,
        "sample_size": stats.sample_size,
        "positive_outcomes": stats.positive_count,
        "avg_valid_return": round(stats.avg_return, 6),
        "merit_score": round(merit_score, 6),
        "bot_suspect": bot_suspect,
        "copycat_dominant": copycat_dominant,
        "integrity_score": round(integrity_score, 4),
        "learning_filter": {
            "was_sellable_entire_window": True,
            "trap_flag": False,
            "net_tradeable_return_est_not_null": True,
        },
    }

    # Ocean -> Shadow
    if current == "ocean":
        if (
            stats.positive_count >= settings.merit_ocean_to_shadow_positive_min
            and not bot_suspect
            and not copycat_dominant
        ):
            rationale["event"] = "promotion"
            rationale["rule"] = "ocean_to_shadow"
            return "shadow", rationale

    # Shadow -> Titan
    if current == "shadow":
        if (
            stats.sample_size >= settings.merit_shadow_to_titan_sample_min
            and merit_score >= settings.merit_shadow_to_titan_threshold
            and integrity_score >= settings.merit_integrity_min
        ):
            rationale["event"] = "promotion"
            rationale["rule"] = "shadow_to_titan"
            return "titan", rationale

    # Seed decay rule.
    if wallet.source == "seed_pack" and stats.sample_size >= settings.merit_seed_decay_min_outcomes:
        if merit_score <= settings.merit_seed_decay_threshold:
            target_tier = settings.merit_seed_decay_target_tier
            rationale["event"] = "demotion"
            rationale["rule"] = "seed_decay_low_merit"
            rationale["target_tier"] = target_tier
            return target_tier, rationale

    rationale["event"] = "score_update"
    rationale["rule"] = "none"
    return current, rationale


def _wallet_stats_query(*, chain: str, wallet_address: str) -> Select:
    valid_filter = (
        SignalOutcome.was_sellable_entire_window.is_(True),
        SignalOutcome.trap_flag.is_(False),
        SignalOutcome.net_tradeable_return_est.is_not(None),
    )
    return (
        select(
            Alert.chain.label("chain"),
            Alert.wallet_address.label("wallet_address"),
            func.count(SignalOutcome.id).label("sample_size"),
            func.sum(
                case((SignalOutcome.net_tradeable_return_est > 0, 1), else_=0)
            ).label("positive_count"),
            func.avg(SignalOutcome.net_tradeable_return_est).label("avg_return"),
        )
        .join(SignalOutcome, SignalOutcome.alert_id == Alert.id)
        .where(
            Alert.chain == chain,
            Alert.wallet_address == wallet_address,
            *valid_filter,
        )
        .group_by(Alert.chain, Alert.wallet_address)
    )


async def run_merit_update_once(session: AsyncSession) -> int:
    wallets_result = await session.execute(select(Wallet))
    wallets = list(wallets_result.scalars().all())
    updated = 0

    for wallet in wallets:
        stats_query = _wallet_stats_query(chain=wallet.chain, wallet_address=wallet.address)
        row = (await session.execute(stats_query)).one_or_none()

        if row is None:
            stats = WalletOutcomeStats(
                chain=wallet.chain,
                wallet_address=wallet.address,
                sample_size=0,
                positive_count=0,
                avg_return=0.0,
            )
        else:
            stats = WalletOutcomeStats(
                chain=row.chain,
                wallet_address=row.wallet_address,
                sample_size=int(row.sample_size or 0),
                positive_count=int(row.positive_count or 0),
                avg_return=float(row.avg_return or 0.0),
            )

        old_merit = _as_float(wallet.merit_score)
        baseline = _baseline_prior(wallet)
        merit = old_merit * settings.merit_decay + baseline * (1.0 - settings.merit_decay)
        if stats.sample_size > 0:
            observed = _clamp_return(stats.avg_return)
            merit = merit * settings.merit_decay + observed * (1.0 - settings.merit_decay)

        next_tier, rationale = _next_tier(wallet, merit, stats)
        wallet.merit_score = _to_decimal(merit)
        wallet.tier = next_tier
        wallet.tier_reason = rationale
        updated += 1

    return updated
