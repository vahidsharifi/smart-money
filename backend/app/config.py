import json
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChainConfig(BaseModel):
    chain_id: int = Field(..., ge=1)
    rpc_http: str | None = None
    rpc_ws: str | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str
    redis_url: str
    ollama_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.1"
    alchemy_ws_url: str | None = None
    alchemy_http_url: str | None = None
    bsc_ws_url: str | None = None
    bsc_http_url: str | None = None
    dexscreener_base_url: str = "https://api.dexscreener.com/latest/dex"
    goplus_base_url: str = "https://api.gopluslabs.io/api/v1"
    log_level: str = "info"
    chain_config: dict[str, ChainConfig] = Field(default_factory=dict, validation_alias="CHAIN_CONFIG")
    watched_addresses_eth: list[str] = Field(
        default_factory=list, validation_alias="WATCHED_ADDRESSES_ETH"
    )
    watched_addresses_bsc: list[str] = Field(
        default_factory=list, validation_alias="WATCHED_ADDRESSES_BSC"
    )
    autopilot_liquidity_floor_eth: float = Field(
        50_000, validation_alias="AUTOPILOT_LIQUIDITY_FLOOR_ETH"
    )
    autopilot_liquidity_floor_bsc: float = Field(
        25_000, validation_alias="AUTOPILOT_LIQUIDITY_FLOOR_BSC"
    )
    autopilot_volume_floor_24h: float = Field(
        50_000, validation_alias="AUTOPILOT_VOLUME_FLOOR_24H"
    )
    autopilot_min_age_hours: float = Field(
        1.0, validation_alias="AUTOPILOT_MIN_AGE_HOURS"
    )
    autopilot_age_fallback_multiplier: float = Field(
        1.5, validation_alias="AUTOPILOT_AGE_FALLBACK_MULTIPLIER"
    )
    autopilot_max_pairs_per_chain: int = Field(
        200, validation_alias="AUTOPILOT_MAX_PAIRS_PER_CHAIN"
    )
    autopilot_min_sleep_seconds: int = Field(
        600, validation_alias="AUTOPILOT_MIN_SLEEP_SECONDS"
    )
    autopilot_max_sleep_seconds: int = Field(
        1800, validation_alias="AUTOPILOT_MAX_SLEEP_SECONDS"
    )
    tier_ocean_threshold: float = Field(1_000_000, validation_alias="TIER_OCEAN_THRESHOLD")
    tier_shadow_threshold: float = Field(100_000, validation_alias="TIER_SHADOW_THRESHOLD")
    tier_titan_threshold: float = Field(10_000, validation_alias="TIER_TITAN_THRESHOLD")
    merit_decay: float = Field(0.85, validation_alias="MERIT_DECAY")
    merit_prior_constant: float = Field(0.015, validation_alias="MERIT_PRIOR_CONSTANT")
    merit_return_clamp_min: float = Field(-0.5, validation_alias="MERIT_RETURN_CLAMP_MIN")
    merit_return_clamp_max: float = Field(0.5, validation_alias="MERIT_RETURN_CLAMP_MAX")
    merit_ocean_to_shadow_positive_min: int = Field(3, validation_alias="MERIT_OCEAN_TO_SHADOW_POSITIVE_MIN")
    merit_shadow_to_titan_sample_min: int = Field(20, validation_alias="MERIT_SHADOW_TO_TITAN_SAMPLE_MIN")
    merit_shadow_to_titan_threshold: float = Field(0.08, validation_alias="MERIT_SHADOW_TO_TITAN_THRESHOLD")
    merit_integrity_min: float = Field(0.8, validation_alias="MERIT_INTEGRITY_MIN")
    merit_seed_decay_min_outcomes: int = Field(12, validation_alias="MERIT_SEED_DECAY_MIN_OUTCOMES")
    merit_seed_decay_threshold: float = Field(-0.02, validation_alias="MERIT_SEED_DECAY_THRESHOLD")
    merit_seed_decay_target_tier: str = Field("ocean", validation_alias="MERIT_SEED_DECAY_TARGET_TIER")
    netev_expected_move_eth: float = Field(0.08, validation_alias="NETEV_EXPECTED_MOVE_ETH")
    netev_expected_move_bsc: float = Field(0.05, validation_alias="NETEV_EXPECTED_MOVE_BSC")
    netev_min_usd_profit_eth: float = Field(20.0, validation_alias="NETEV_MIN_USD_PROFIT_ETH")
    netev_min_usd_profit_bsc: float = Field(6.0, validation_alias="NETEV_MIN_USD_PROFIT_BSC")
    netev_min_roi_eth: float = Field(0.08, validation_alias="NETEV_MIN_ROI_ETH")
    netev_min_roi_bsc: float = Field(0.05, validation_alias="NETEV_MIN_ROI_BSC")
    netev_gas_cost_usd_eth: float = Field(14.0, validation_alias="NETEV_GAS_COST_USD_ETH")
    netev_gas_cost_usd_bsc: float = Field(1.2, validation_alias="NETEV_GAS_COST_USD_BSC")
    netev_default_slippage: float = Field(0.02, validation_alias="NETEV_DEFAULT_SLIPPAGE")

    @field_validator("chain_config", mode="before")
    @classmethod
    def parse_chain_config(cls, value: Any) -> Any:
        if value in (None, ""):
            return {}
        if isinstance(value, str):
            return json.loads(value)
        return value

    @field_validator("watched_addresses_eth", "watched_addresses_bsc", mode="before")
    @classmethod
    def parse_watched_addresses(cls, value: Any) -> Any:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            if value.strip().startswith("["):
                return json.loads(value)
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


settings = Settings()

REQUIRED_CHAINS = {"ethereum", "bsc"}


def validate_chain_config() -> None:
    if not settings.chain_config:
        raise RuntimeError("CHAIN_CONFIG is required and must include ethereum and bsc")

    missing = REQUIRED_CHAINS - set(settings.chain_config.keys())
    if missing:
        raise RuntimeError(f"CHAIN_CONFIG missing chains: {', '.join(sorted(missing))}")

    for chain_name, config in settings.chain_config.items():
        if not config.rpc_http and not config.rpc_ws:
            raise RuntimeError(
                f"CHAIN_CONFIG for {chain_name} must include rpc_http or rpc_ws"
            )
