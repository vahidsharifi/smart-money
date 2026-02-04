from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str
    redis_url: str
    ollama_url: str = "http://ollama:11434"
    alchemy_wss_url: str | None = None
    dexscreener_base_url: str = "https://api.dexscreener.com/latest/dex"
    goplus_base_url: str = "https://api.gopluslabs.io/api/v1"
    log_level: str = "info"


settings = Settings()
