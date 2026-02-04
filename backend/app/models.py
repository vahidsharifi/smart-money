import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ScoreRecord(Base):
    __tablename__ = "score_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_address: Mapped[str] = mapped_column(String(128), index=True)
    chain: Mapped[str] = mapped_column(String(32), default="ethereum")
    score: Mapped[float] = mapped_column(Float)
    reasons: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Wallet(Base):
    __tablename__ = "wallets"

    chain: Mapped[str] = mapped_column(String(32), primary_key=True)
    address: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Token(Base):
    __tablename__ = "tokens"

    chain: Mapped[str] = mapped_column(String(32), primary_key=True)
    address: Mapped[str] = mapped_column(String(128), primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(256))
    decimals: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        Index("ix_trades_wallet_address", "wallet_address"),
        Index("ix_trades_block_time", "block_time"),
    )

    chain: Mapped[str] = mapped_column(String(32), primary_key=True)
    tx_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    log_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet_address: Mapped[str] = mapped_column(String(128))
    token_address: Mapped[str] = mapped_column(String(128))
    side: Mapped[str | None] = mapped_column(String(16))
    amount: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    block_time: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chain: Mapped[str] = mapped_column(String(32))
    wallet_address: Mapped[str] = mapped_column(String(128))
    token_address: Mapped[str] = mapped_column(String(128))
    quantity: Mapped[float] = mapped_column(Float)
    average_price: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WalletMetric(Base):
    __tablename__ = "wallet_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chain: Mapped[str] = mapped_column(String(32))
    wallet_address: Mapped[str] = mapped_column(String(128))
    total_value: Mapped[float | None] = mapped_column(Float)
    pnl: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TokenRisk(Base):
    __tablename__ = "token_risk"
    __table_args__ = (Index("ix_token_risk_updated_at", "updated_at"),)

    chain: Mapped[str] = mapped_column(String(32), primary_key=True)
    address: Mapped[str] = mapped_column(String(128), primary_key=True)
    score: Mapped[float | None] = mapped_column(Float)
    components: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_created_at", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chain: Mapped[str] = mapped_column(String(32))
    wallet_address: Mapped[str] = mapped_column(String(128))
    token_address: Mapped[str | None] = mapped_column(String(128))
    alert_type: Mapped[str] = mapped_column(String(64))
    reasons: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
