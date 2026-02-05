import asyncio
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings, validate_chain_config
from app.db import async_session, get_session
from app.logging import configure_logging
from app.models import Alert, ScoreRecord, TokenRisk, WalletMetric
from app.schemas import (
    AlertResponse,
    NarrativeRequest,
    NarrativeResponse,
    RegimeResponse,
    ScoreRequest,
    ScoreResponse,
    TokenRiskResponse,
    WalletDetail,
    WalletSummary,
    WalletTier,
)
from app.scoring import deterministic_score
from app.services import fetch_dexscreener, fetch_goplus, narrate_with_ollama

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="Project Titan API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://frontend:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def get_redis() -> Redis:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield redis
    finally:
        await redis.close()


def _tier_for_value(total_value: float | None) -> WalletTier:
    if total_value is None:
        return WalletTier.ignore
    if total_value >= settings.tier_ocean_threshold:
        return WalletTier.ocean
    if total_value >= settings.tier_shadow_threshold:
        return WalletTier.shadow
    if total_value >= settings.tier_titan_threshold:
        return WalletTier.titan
    return WalletTier.ignore


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


@app.get("/alerts", response_model=list[AlertResponse])
async def list_alerts(
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    chain: str | None = Query(None, min_length=1),
    session: AsyncSession = Depends(get_session),
) -> list[AlertResponse]:
    query = select(Alert).order_by(Alert.created_at.desc()).offset(offset).limit(limit)
    if chain:
        query = query.where(Alert.chain == chain)
    result = await session.execute(query)
    alerts = result.scalars().all()
    return [
        AlertResponse(
            id=alert.id,
            chain=alert.chain,
            wallet_address=alert.wallet_address,
            token_address=alert.token_address,
            alert_type=alert.alert_type,
            reasons=alert.reasons if isinstance(alert.reasons, dict) else {},
            narrative=alert.narrative,
            created_at=alert.created_at,
        )
        for alert in alerts
    ]


@app.get("/alerts/{alert_id}", response_model=AlertResponse)
async def get_alert(
    alert_id: UUID = Path(...),
    session: AsyncSession = Depends(get_session),
) -> AlertResponse:
    result = await session.execute(select(Alert).where(Alert.id == alert_id).limit(1))
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return AlertResponse(
        id=alert.id,
        chain=alert.chain,
        wallet_address=alert.wallet_address,
        token_address=alert.token_address,
        alert_type=alert.alert_type,
        reasons=alert.reasons if isinstance(alert.reasons, dict) else {},
        narrative=alert.narrative,
        created_at=alert.created_at,
    )


@app.get("/wallets", response_model=list[WalletSummary])
async def list_wallets(
    tier: WalletTier | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[WalletSummary]:
    result = await session.execute(select(WalletMetric))
    wallets = result.scalars().all()
    summaries: list[WalletSummary] = []
    for metric in wallets:
        summary = WalletSummary(
            chain=metric.chain,
            address=metric.wallet_address,
            total_value=metric.total_value,
            pnl=metric.pnl,
            tier=_tier_for_value(metric.total_value),
            updated_at=metric.updated_at,
        )
        if tier and summary.tier != tier:
            continue
        summaries.append(summary)
    return summaries


@app.get("/wallets/{address}", response_model=WalletDetail)
async def get_wallet(
    address: str = Path(..., min_length=3),
    session: AsyncSession = Depends(get_session),
) -> WalletDetail:
    result = await session.execute(select(WalletMetric).where(WalletMetric.wallet_address == address))
    metrics = result.scalars().all()
    if not metrics:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return WalletDetail(
        address=address,
        wallets=[
            WalletSummary(
                chain=metric.chain,
                address=metric.wallet_address,
                total_value=metric.total_value,
                pnl=metric.pnl,
                tier=_tier_for_value(metric.total_value),
                updated_at=metric.updated_at,
            )
            for metric in metrics
        ],
    )


@app.get("/tokens/{chain}/{address}/risk", response_model=TokenRiskResponse)
async def get_token_risk(
    chain: str = Path(..., min_length=1),
    address: str = Path(..., min_length=3),
    session: AsyncSession = Depends(get_session),
) -> TokenRiskResponse:
    result = await session.execute(
        select(TokenRisk).where(TokenRisk.chain == chain, TokenRisk.address == address).limit(1)
    )
    token_risk = result.scalar_one_or_none()
    if token_risk is None:
        raise HTTPException(status_code=404, detail="Token risk not found")
    return TokenRiskResponse(
        chain=token_risk.chain,
        address=token_risk.address,
        score=token_risk.score,
        components=token_risk.components if isinstance(token_risk.components, dict) else {},
        updated_at=token_risk.updated_at,
    )


@app.get("/regime", response_model=RegimeResponse)
async def get_regime() -> RegimeResponse:
    return RegimeResponse(regime="neutral", updated_at=datetime.utcnow())


@app.on_event("startup")
async def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    validate_chain_config()

    config = Config("alembic.ini")
    try:
        # Run Alembic in a thread to avoid asyncio.run() in a running loop.
        await asyncio.to_thread(command.upgrade, config, "head")
    except Exception as exc:
        logger.exception("migration_failed")
        raise RuntimeError("Migration failed") from exc
