from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import Select, and_, case, func, or_, select
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
    avg_contribution: float


@dataclass(frozen=True)
class OutcomeContribution:
    alert_id: str
    token_address: str | None
    net_tradeable_return_est: float
    early_factor: float
    crowding_penalty: float
    copycat_penalty: float
    contribution: float


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


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _baseline_prior(wallet: Wallet) -> float:
    prior_weight = max(0.0, _as_float(wallet.prior_weight))
    return prior_weight * settings.merit_prior_constant


def _high_merit_filter() -> object:
    return or_(
        Wallet.tier.in_(["shadow", "titan"]),
        Wallet.merit_score >= Decimal(str(settings.merit_shadow_to_titan_threshold)),
    )


def _early_factor(rank: int) -> float:
    if rank <= 1:
        return 1.0
    if rank == 2:
        return 0.7
    return 0.5


def _crowding_penalty(high_merit_count: int) -> float:
    if high_merit_count <= 1:
        return 0.0
    return _clamp01((high_merit_count - 1) * 0.15)


def _copycat_penalty_from_reason(wallet: Wallet) -> float | None:
    reason = wallet.tier_reason if isinstance(wallet.tier_reason, dict) else {}
    raw = reason.get("copycat_burst_score")
    if raw is None:
        return None
    try:
        return _clamp01(float(raw))
    except (TypeError, ValueError):
        return None


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
        "avg_contribution": round(stats.avg_contribution, 6),
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
            rationale["last_promotion_reason"] = "ocean_to_shadow"
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
            rationale["last_promotion_reason"] = "shadow_to_titan"
            return "titan", rationale

    # Seed decay rule.
    if wallet.source == "seed_pack" and stats.sample_size >= settings.merit_seed_decay_min_outcomes:
        if merit_score <= settings.merit_seed_decay_threshold:
            target_tier = settings.merit_seed_decay_target_tier
            rationale["event"] = "demotion"
            rationale["rule"] = "seed_decay_low_merit"
            rationale["target_tier"] = target_tier
            rationale["last_demotion_reason"] = "seed_decay_low_merit"
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


async def _build_contributions(session: AsyncSession, wallet: Wallet) -> list[OutcomeContribution]:
    valid_filter = (
        SignalOutcome.was_sellable_entire_window.is_(True),
        SignalOutcome.trap_flag.is_(False),
        SignalOutcome.net_tradeable_return_est.is_not(None),
    )
    outcomes = (
        (
            await session.execute(
                select(
                    Alert.id,
                    Alert.token_address,
                    Alert.created_at,
                    SignalOutcome.net_tradeable_return_est,
                )
                .join(SignalOutcome, SignalOutcome.alert_id == Alert.id)
                .where(
                    Alert.chain == wallet.chain,
                    Alert.wallet_address == wallet.address,
                    *valid_filter,
                )
            )
        )
        .all()
    )

    contributions: list[OutcomeContribution] = []
    known_copycat_penalty = _copycat_penalty_from_reason(wallet)

    for row in outcomes:
        token_address = row.token_address
        if not token_address:
            continue

        first_seen_rows = (
            (
                await session.execute(
                    select(Alert.wallet_address, func.min(Alert.created_at).label("first_seen"))
                    .join(Wallet, and_(Wallet.chain == Alert.chain, Wallet.address == Alert.wallet_address))
                    .where(
                        Alert.chain == wallet.chain,
                        Alert.token_address == token_address,
                        _high_merit_filter(),
                    )
                    .group_by(Alert.wallet_address)
                    .order_by(func.min(Alert.created_at).asc())
                )
            )
            .all()
        )
        rank = len(first_seen_rows) + 1
        for idx, first_seen in enumerate(first_seen_rows, start=1):
            if first_seen.wallet_address == wallet.address:
                rank = idx
                break

        early_factor = _early_factor(rank)

        ten_minutes = timedelta(minutes=10)
        crowd_count = int(
            (
                await session.execute(
                    select(func.count(func.distinct(Alert.wallet_address)))
                    .join(Wallet, and_(Wallet.chain == Alert.chain, Wallet.address == Alert.wallet_address))
                    .where(
                        Alert.chain == wallet.chain,
                        Alert.token_address == token_address,
                        Alert.created_at >= row.created_at - ten_minutes,
                        Alert.created_at <= row.created_at + ten_minutes,
                        _high_merit_filter(),
                    )
                )
            ).scalar_one()
            or 0
        )
        crowding_penalty = _crowding_penalty(crowd_count)

        if known_copycat_penalty is not None:
            copycat_penalty = known_copycat_penalty
        else:
            burst_window = timedelta(seconds=5)
            same_block_density = int(
                (
                    await session.execute(
                        select(func.count(func.distinct(Alert.wallet_address)))
                        .where(
                            Alert.chain == wallet.chain,
                            Alert.token_address == token_address,
                            Alert.created_at >= row.created_at - burst_window,
                            Alert.created_at <= row.created_at + burst_window,
                        )
                    )
                ).scalar_one()
                or 0
            )
            copycat_penalty = _clamp01(max(same_block_density - 1, 0) * 0.12)

        weight = early_factor * (1.0 - crowding_penalty) * (1.0 - copycat_penalty)
        net_return = float(row.net_tradeable_return_est or 0.0)
        contribution = net_return * weight
        contributions.append(
            OutcomeContribution(
                alert_id=str(row.id),
                token_address=token_address,
                net_tradeable_return_est=net_return,
                early_factor=early_factor,
                crowding_penalty=crowding_penalty,
                copycat_penalty=copycat_penalty,
                contribution=contribution,
            )
        )

    return contributions


async def run_merit_update_once(session: AsyncSession) -> int:
    wallets_result = await session.execute(select(Wallet))
    wallets = list(wallets_result.scalars().all())
    updated = 0

    for wallet in wallets:
        stats_query = _wallet_stats_query(chain=wallet.chain, wallet_address=wallet.address)
        row = (await session.execute(stats_query)).one_or_none()
        contributions = await _build_contributions(session, wallet)

        if row is None:
            stats = WalletOutcomeStats(
                chain=wallet.chain,
                wallet_address=wallet.address,
                sample_size=0,
                positive_count=0,
                avg_return=0.0,
                avg_contribution=0.0,
            )
        else:
            avg_contribution = 0.0
            if contributions:
                avg_contribution = sum(item.contribution for item in contributions) / len(contributions)
            stats = WalletOutcomeStats(
                chain=row.chain,
                wallet_address=row.wallet_address,
                sample_size=int(row.sample_size or 0),
                positive_count=int(row.positive_count or 0),
                avg_return=float(row.avg_return or 0.0),
                avg_contribution=avg_contribution,
            )

        old_merit = _as_float(wallet.merit_score)
        baseline = _baseline_prior(wallet)
        merit = old_merit * settings.merit_decay + baseline * (1.0 - settings.merit_decay)
        if stats.sample_size > 0:
            observed = _clamp_return(stats.avg_contribution)
            merit = merit * settings.merit_decay + observed * (1.0 - settings.merit_decay)

        next_tier, rationale = _next_tier(wallet, merit, stats)
        wallet.merit_score = _to_decimal(merit)
        wallet.tier = next_tier
        now = datetime.utcnow()
        contribution_summary: dict[str, object] = {
            "sample_size": len(contributions),
            "avg_contribution": round(stats.avg_contribution, 6),
        }
        if contributions:
            contribution_summary["latest"] = {
                "alert_id": contributions[-1].alert_id,
                "token_address": contributions[-1].token_address,
                "net_tradeable_return_est": round(contributions[-1].net_tradeable_return_est, 6),
                "early_factor": round(contributions[-1].early_factor, 4),
                "crowding_penalty": round(contributions[-1].crowding_penalty, 4),
                "copycat_penalty": round(contributions[-1].copycat_penalty, 4),
                "contribution": round(contributions[-1].contribution, 6),
            }
        existing_reason = wallet.tier_reason if isinstance(wallet.tier_reason, dict) else {}
        if rationale.get("last_promotion_reason") is None and existing_reason.get("last_promotion_reason") is not None:
            rationale["last_promotion_reason"] = existing_reason.get("last_promotion_reason")
        if rationale.get("last_demotion_reason") is None and existing_reason.get("last_demotion_reason") is not None:
            rationale["last_demotion_reason"] = existing_reason.get("last_demotion_reason")
        rationale["last_merit_update_at"] = now.isoformat()
        rationale["last_contribution_summary"] = contribution_summary
        wallet.tier_reason = rationale
        updated += 1

    return updated
