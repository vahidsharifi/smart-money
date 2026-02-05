import json
import logging
import re
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_SENTENCE_RE = re.compile(r"[.!?]+")


def _format_scalar(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _format_list(value: Any) -> str:
    if value is None:
        return "none provided"
    if isinstance(value, list):
        items = [_format_scalar(item) for item in value if item is not None]
    else:
        items = [_format_scalar(value)]
    if not items:
        return "none provided"
    return "; ".join(items)


def _deterministic_template(reasons: dict[str, Any]) -> str:
    parts: list[str] = []
    for label, key in (
        ("conviction", "conviction"),
        ("tss", "tss"),
        ("regime", "regime"),
        ("tier", "tier"),
        ("wallet_total_value", "wallet_total_value"),
        ("total_value", "total_value"),
    ):
        if key in reasons and reasons[key] is not None:
            parts.append(f"{label} {reasons[key]}")
    summary = "Alert summary based on provided signals"
    if parts:
        summary = f"{summary}: {', '.join(parts)}"
    sentence_one = f"{summary}."

    sentence_two = (
        "Reasons: "
        f"{_format_list(reasons.get('reasons'))}. "
        "Risks: "
        f"{_format_list(reasons.get('risks'))}. "
        "Invalidation: "
        f"{_format_list(reasons.get('invalidation'))}."
    )
    return f"{sentence_one} {sentence_two}"


def _trim_to_sentences(text: str, *, limit: int = 3) -> str:
    sentences = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    if len(sentences) > limit:
        sentences = sentences[:limit]
    return ". ".join(sentences).strip() + "."


def _response_has_only_known_numbers(response: str, reasons_json: str) -> bool:
    allowed = set(_NUMBER_RE.findall(reasons_json))
    for token in _NUMBER_RE.findall(response):
        if token not in allowed:
            return False
    return True


async def narrate_alert(reasons: dict[str, Any]) -> str:
    reasons_json = json.dumps(reasons, sort_keys=True)
    prompt = (
        "Write a 2-3 sentence narrative using only the exact values in the JSON. "
        "Do not invent, infer, or calculate any numbers. "
        "If you mention numbers, they must appear verbatim in the JSON. "
        f"JSON: {reasons_json}"
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{settings.ollama_url}/api/generate",
                json={"model": settings.ollama_model, "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            payload = response.json()
            narrative = str(payload.get("response", "")).strip()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        logger.warning("ollama_failed", extra={"error": str(exc)})
        return _deterministic_template(reasons)

    if not narrative:
        return _deterministic_template(reasons)

    narrative = _trim_to_sentences(narrative, limit=3)
    if len([s for s in _SENTENCE_RE.split(narrative) if s.strip()]) < 2:
        return _deterministic_template(reasons)
    if not _response_has_only_known_numbers(narrative, reasons_json):
        return _deterministic_template(reasons)
    return narrative
