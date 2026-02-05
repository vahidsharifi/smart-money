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
