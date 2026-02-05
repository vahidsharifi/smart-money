from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.config import settings


class ScoreRequest(BaseModel):
    token_address: str = Field(..., min_length=3)
    chain: str = "ethereum"

    @field_validator("chain")
    @classmethod
    def validate_chain(cls, value: str) -> str:
        if settings.chain_config and value not in settings.chain_config:
            raise ValueError(f"Unsupported chain: {value}")
        return value


class ScoreReason(BaseModel):
    source: str
    message: str
    severity: str
    data: dict[str, Any] = Field(default_factory=dict)


class ScoreResponse(BaseModel):
    id: UUID
    token_address: str
    chain: str
    score: float
    reasons: list[ScoreReason]
    created_at: datetime


class NarrativeRequest(BaseModel):
    reasons: list[ScoreReason]


class NarrativeResponse(BaseModel):
    narrative: str


class AlertResponse(BaseModel):
    id: UUID
    chain: str
    wallet_address: str
    token_address: str | None
    alert_type: str
    reasons: dict[str, Any]
    narrative: str | None
    created_at: datetime


class WalletTier(str, Enum):
    ocean = "ocean"
    shadow = "shadow"
    titan = "titan"
    ignore = "ignore"


class WalletSummary(BaseModel):
    chain: str
    address: str
    total_value: float | None
    pnl: float | None
    tier: WalletTier
    updated_at: datetime


class WalletDetail(BaseModel):
    address: str
    wallets: list[WalletSummary]


class TokenRiskResponse(BaseModel):
    chain: str
    address: str
    score: float | None
    components: dict[str, Any]
    updated_at: datetime


class RegimeResponse(BaseModel):
    regime: str
    updated_at: datetime | None


class OpsHealthResponse(BaseModel):
    heartbeats: dict[str, float | None]
    stream_lag: dict[str, int]


class OpsMetricsResponse(BaseModel):
    alerts_by_regime: dict[str, int]
    trap_rate: float
    avg_net_return_by_horizon: dict[str, float]
    top_wallets: list[dict[str, Any]]
    top_pairs: list[dict[str, Any]]


class TuningResponse(BaseModel):
    source: str
    warning: str | None
    thresholds: dict[str, float]


class TuningPreviewRequest(BaseModel):
    thresholds: dict[str, float]


class TuningPreviewResponse(BaseModel):
    total_considered: int
    would_trigger: int
    thresholds: dict[str, float]
