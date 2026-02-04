import logging
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings, validate_chain_config
from app.db import async_session, get_session
from app.logging import configure_logging
from app.models import ScoreRecord
from app.schemas import NarrativeRequest, NarrativeResponse, ScoreRequest, ScoreResponse
from app.scoring import deterministic_score
from app.services import fetch_dexscreener, fetch_goplus, narrate_with_ollama

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Project Titan API")


async def get_redis() -> Redis:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield redis
    finally:
        await redis.close()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.post("/score", response_model=ScoreResponse)
async def score_token(
    request: ScoreRequest,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> ScoreResponse:
    try:
        dex = await fetch_dexscreener(redis, request.token_address)
        goplus = await fetch_goplus(redis, request.token_address)
    except Exception as exc:
        logger.exception("external_fetch_failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    score_value, reasons = deterministic_score(dex, goplus)
    record = ScoreRecord(
        token_address=request.token_address,
        chain=request.chain,
        score=score_value,
        reasons=[reason.model_dump() for reason in reasons],
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)

    return ScoreResponse(
        id=record.id,
        token_address=record.token_address,
        chain=record.chain,
        score=record.score,
        reasons=reasons,
        created_at=record.created_at,
    )


@app.post("/narrate", response_model=NarrativeResponse)
async def narrate(request: NarrativeRequest) -> NarrativeResponse:
    narrative = await narrate_with_ollama([reason.model_dump() for reason in request.reasons])
    return NarrativeResponse(narrative=narrative)


@app.on_event("startup")
async def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    validate_chain_config()

    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
    except Exception as exc:
        logger.exception("migration_failed")
        raise RuntimeError("Migration failed") from exc
