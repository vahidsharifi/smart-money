import asyncio
import logging
from datetime import datetime
import time
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings, validate_chain_config
from app.db import async_session, get_session
from app.logging import configure_logging
from app.models import Alert, ScoreRecord, SettingsStore, SignalOutcome, TokenRisk, Trade, Wallet, WalletMetric
from app.schemas import (
    AlertResponse,
    NarrativeRequest,
    NarrativeResponse,
    OpsHealthResponse,
    OpsMetricsResponse,
    TuningPreviewRequest,
    TuningPreviewResponse,
    TuningResponse,
    RegimeResponse,
    ScoreRequest,
    ScoreResponse,
    TokenRiskResponse,
    WalletDetail,
    WalletSummary,
    WalletTier,
)
from app.scoring import deterministic_score
from app.services import close_http_client, fetch_dexscreener, fetch_goplus, narrate_with_ollama

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




OPS_WORKERS: dict[str, tuple[str | None, str | None]] = {
    "worker-1": ("score_jobs", "scorers"),
    "decoder-1": ("titan:raw_events", "decoders"),
    "risk-worker-1": ("titan:risk_jobs", "risk-workers"),
    "listener-evm": (None, None),
    "alerts-worker": (None, None),
    "outcome-evaluator": (None, None),
    "profiler-worker": (None, None),
    "watchlist-autopilot": (None, None),
}

DEFAULT_TUNING_THRESHOLDS: dict[str, float] = {
    "min_conviction": 45.0,
    "min_tss": 35.0,
    "min_netev_usd": 0.0,
}


def _thresholds_from_env() -> dict[str, float]:
    return {
        "min_conviction": float(settings.netev_min_roi_eth * 100.0),
        "min_tss": 35.0,
        "min_netev_usd": float(settings.netev_min_usd_profit_eth),
    }


def _alert_would_trigger(alert: Alert, thresholds: dict[str, float]) -> bool:
    reasons = alert.reasons if isinstance(alert.reasons, dict) else {}
    conviction = float(alert.conviction or reasons.get("conviction") or 0.0)
    tss = float(alert.tss or reasons.get("tss") or 0.0)
    netev_payload = reasons.get("netev") if isinstance(reasons, dict) else {}
    netev_usd = float((netev_payload or {}).get("netev_usd") or 0.0)
    return (
        conviction >= float(thresholds.get("min_conviction", 0.0))
        and tss >= float(thresholds.get("min_tss", 0.0))
        and netev_usd >= float(thresholds.get("min_netev_usd", 0.0))
    )

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
        query = query.where(Alert.chain == chain.lower())
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
    normalized_address = address.lower()
    result = await session.execute(
        select(WalletMetric).where(WalletMetric.wallet_address == normalized_address)
    )
    metrics = result.scalars().all()
    if not metrics:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return WalletDetail(
        address=normalized_address,
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
    normalized_chain = chain.lower()
    normalized_address = address.lower()
    result = await session.execute(
        select(TokenRisk)
        .where(TokenRisk.chain == normalized_chain, TokenRisk.address == normalized_address)
        .limit(1)
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




@app.get("/ops/health", response_model=OpsHealthResponse)
async def ops_health(redis: Redis = Depends(get_redis)) -> OpsHealthResponse:
    now_ts = int(time.time())
    heartbeats: dict[str, float | None] = {}
    stream_lag: dict[str, int] = {}

    for worker_name, (stream_name, group_name) in OPS_WORKERS.items():
        value = await redis.get(f"titan:hb:{worker_name}")
        if value is None:
            heartbeats[worker_name] = None
        else:
            try:
                heartbeats[worker_name] = max(0.0, float(now_ts - int(value)))
            except ValueError:
                heartbeats[worker_name] = None

        if stream_name and group_name:
            try:
                pending = await redis.xpending(stream_name, group_name)
                stream_lag[f"{stream_name}:{group_name}"] = int(pending.get("pending", 0))
            except Exception:
                stream_lag[f"{stream_name}:{group_name}"] = 0

    return OpsHealthResponse(heartbeats=heartbeats, stream_lag=stream_lag)


@app.get("/ops/metrics", response_model=OpsMetricsResponse)
async def ops_metrics(session: AsyncSession = Depends(get_session)) -> OpsMetricsResponse:
    regime_rows = await session.execute(select(Alert.alert_type, func.count()).group_by(Alert.alert_type))
    alerts_by_regime = {str(k): int(v) for k, v in regime_rows.all()}

    trap_row = await session.execute(
        select(func.avg(cast(SignalOutcome.trap_flag, Float))).where(SignalOutcome.trap_flag.is_not(None))
    )
    trap_rate = float(trap_row.scalar_one_or_none() or 0.0)

    horizon_rows = await session.execute(
        select(SignalOutcome.horizon_minutes, func.avg(SignalOutcome.net_tradeable_return_est))
        .where(SignalOutcome.net_tradeable_return_est.is_not(None))
        .group_by(SignalOutcome.horizon_minutes)
    )
    avg_net_return_by_horizon = {str(h): float(v or 0.0) for h, v in horizon_rows.all()}

    wallet_rows = await session.execute(
        select(Wallet.address, Wallet.chain, Wallet.merit_score)
        .order_by(Wallet.merit_score.desc())
        .limit(10)
    )
    top_wallets = [
        {"address": address, "chain": chain, "merit_score": float(merit_score or 0.0)}
        for address, chain, merit_score in wallet_rows.all()
    ]

    pair_rows = await session.execute(
        select(Trade.pair_address, func.count())
        .where(Trade.pair_address.is_not(None))
        .group_by(Trade.pair_address)
        .order_by(func.count().desc())
        .limit(10)
    )
    top_pairs = [{"pair_address": pair, "count": int(count)} for pair, count in pair_rows.all()]

    return OpsMetricsResponse(
        alerts_by_regime=alerts_by_regime,
        trap_rate=trap_rate,
        avg_net_return_by_horizon=avg_net_return_by_horizon,
        top_wallets=top_wallets,
        top_pairs=top_pairs,
    )


@app.get("/ops/tuning", response_model=TuningResponse)
async def get_tuning(session: AsyncSession = Depends(get_session)) -> TuningResponse:
    row = await session.get(SettingsStore, "alert_thresholds")
    if row is None:
        return TuningResponse(source="env", warning="DB settings missing, using env-backed defaults", thresholds=_thresholds_from_env())
    return TuningResponse(source="db", warning=None, thresholds=row.value if isinstance(row.value, dict) else DEFAULT_TUNING_THRESHOLDS)


@app.put("/ops/tuning", response_model=TuningResponse)
async def put_tuning(payload: TuningPreviewRequest, session: AsyncSession = Depends(get_session)) -> TuningResponse:
    thresholds = payload.thresholds
    row = await session.get(SettingsStore, "alert_thresholds")
    if row is None:
        row = SettingsStore(key="alert_thresholds", value=thresholds, updated_at=datetime.utcnow())
        session.add(row)
    else:
        row.value = thresholds
        row.updated_at = datetime.utcnow()
    await session.commit()
    return TuningResponse(source="db", warning=None, thresholds=thresholds)


@app.post("/ops/tuning/preview", response_model=TuningPreviewResponse)
async def preview_tuning(payload: TuningPreviewRequest, session: AsyncSession = Depends(get_session)) -> TuningPreviewResponse:
    result = await session.execute(select(Alert).order_by(Alert.created_at.desc()).limit(50))
    alerts = result.scalars().all()
    triggered = sum(1 for alert in alerts if _alert_would_trigger(alert, payload.thresholds))
    return TuningPreviewResponse(total_considered=len(alerts), would_trigger=triggered, thresholds=payload.thresholds)


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


@app.on_event("shutdown")
async def shutdown_resources() -> None:
    await close_http_client()
