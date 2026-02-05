import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
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
    __table_args__ = (
        Index("ix_wallets_tier", "tier"),
        Index("ix_wallets_merit_score", "merit_score"),
    )

    chain: Mapped[str] = mapped_column(String(32), primary_key=True)
    address: Mapped[str] = mapped_column(String(128), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), default="autopilot")
    prior_weight: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.0"))
    merit_score: Mapped[Decimal] = mapped_column(Numeric, default=Decimal("0.0"))
    tier: Mapped[str | None] = mapped_column(String(32))
    tier_reason: Mapped[dict | None] = mapped_column(JSONB)
    ignore_reason: Mapped[str | None] = mapped_column(Text)
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


class WatchPair(Base):
    __tablename__ = "watch_pairs"
    __table_args__ = (
        Index("ix_watch_pairs_chain_expires_at", "chain", "expires_at"),
        Index("ix_watch_pairs_chain_priority", "chain", "priority"),
        Index("ix_watch_pairs_last_seen", "last_seen"),
    )

    chain: Mapped[str] = mapped_column(String(32), primary_key=True)
    pair_address: Mapped[str] = mapped_column(String(128), primary_key=True)
    dex: Mapped[str | None] = mapped_column(String(64))
    token0_symbol: Mapped[str | None] = mapped_column(String(32))
    token0_address: Mapped[str | None] = mapped_column(String(128))
    token1_symbol: Mapped[str | None] = mapped_column(String(32))
    token1_address: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(32))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        Index("ix_trades_wallet_address", "wallet_address"),
        Index("ix_trades_block_time", "block_time"),
    )

    chain: Mapped[str] = mapped_column(String(32), primary_key=True)
    tx_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    log_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    block_number: Mapped[int | None] = mapped_column(Integer)
    wallet_address: Mapped[str | None] = mapped_column(String(128))
    token_address: Mapped[str | None] = mapped_column(String(128))
    side: Mapped[str | None] = mapped_column(String(16))
    amount: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    usd_value: Mapped[float | None] = mapped_column(Float)
    block_time: Mapped[datetime | None] = mapped_column(DateTime)
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
    narrative: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SignalOutcome(Base):
    __tablename__ = "signal_outcomes"
    __table_args__ = (UniqueConstraint("alert_id", "horizon_minutes", name="uq_signal_outcomes_alert_horizon"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="CASCADE"))
    horizon_minutes: Mapped[int] = mapped_column(Integer)
    was_sellable_entire_window: Mapped[bool | None] = mapped_column(Boolean)
    min_exit_slippage_1k: Mapped[Decimal | None] = mapped_column(Numeric)
    max_exit_slippage_1k: Mapped[Decimal | None] = mapped_column(Numeric)
    tradeable_peak_gain: Mapped[Decimal | None] = mapped_column(
        Numeric, comment="Decimal fraction (1.0 = 100%)."
    )
    tradeable_drawdown: Mapped[Decimal | None] = mapped_column(Numeric)
    net_tradeable_return_est: Mapped[Decimal | None] = mapped_column(Numeric)
    trap_flag: Mapped[bool | None] = mapped_column(Boolean)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
