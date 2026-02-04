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
    alchemy_ws_url: str | None = None
    alchemy_http_url: str | None = None
    bsc_ws_url: str | None = None
    bsc_http_url: str | None = None
    dexscreener_base_url: str = "https://api.dexscreener.com/latest/dex"
    goplus_base_url: str = "https://api.gopluslabs.io/api/v1"
    log_level: str = "info"
    chain_config: dict[str, ChainConfig] = Field(default_factory=dict, validation_alias="CHAIN_CONFIG")

    @field_validator("chain_config", mode="before")
    @classmethod
    def parse_chain_config(cls, value: Any) -> Any:
        if value in (None, ""):
            return {}
        if isinstance(value, str):
            return json.loads(value)
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
