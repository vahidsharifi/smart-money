import json
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChainSettings(BaseModel):
    http_url: str
    ws_url: str
    watched_addresses: list[str] = Field(default_factory=list)

    @field_validator("watched_addresses", mode="before")
    @classmethod
    def parse_watched_addresses(cls, value: Any) -> Any:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            if value.strip().startswith("["):
                return json.loads(value)
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
    )

    eth: ChainSettings
    bsc: ChainSettings


settings = Settings()

__all__ = ["ChainSettings", "Settings", "settings"]
