from typing import Any

from app.schemas import ScoreReason


def deterministic_score(dex: dict[str, Any], goplus: dict[str, Any]) -> tuple[float, list[ScoreReason]]:
    reasons: list[ScoreReason] = []
    score = 100.0

    pairs = dex.get("pairs") or []
    if not pairs:
        score -= 20
        reasons.append(
            ScoreReason(
                source="dexscreener",
                message="No active pairs found on DexScreener.",
                severity="medium",
            )
        )
    else:
        liquidity = pairs[0].get("liquidity", {}).get("usd") or 0
        if liquidity < 10000:
            score -= 15
            reasons.append(
                ScoreReason(
                    source="dexscreener",
                    message="Low liquidity detected on top pair.",
                    severity="medium",
                    data={"liquidity_usd": liquidity},
                )
            )

    token_data = (goplus.get("result") or {}).values()
    token_info = next(iter(token_data), {}) if token_data else {}
    if token_info:
        if token_info.get("is_honeypot") == "1":
            score -= 50
            reasons.append(
                ScoreReason(
                    source="goplus",
                    message="Token flagged as honeypot.",
                    severity="high",
                    data={"is_honeypot": True},
                )
            )
        if token_info.get("is_blacklisted") == "1":
            score -= 20
            reasons.append(
                ScoreReason(
                    source="goplus",
                    message="Token contract is blacklisted.",
                    severity="high",
                    data={"is_blacklisted": True},
                )
            )
    else:
        score -= 10
        reasons.append(
            ScoreReason(
                source="goplus",
                message="No GoPlus security data available.",
                severity="low",
            )
        )

    return max(score, 0.0), reasons
