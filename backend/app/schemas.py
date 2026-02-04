from datetime import datetime
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
