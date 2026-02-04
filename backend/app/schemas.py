from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ScoreRequest(BaseModel):
    token_address: str = Field(..., min_length=3)
    chain: str = "ethereum"


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
