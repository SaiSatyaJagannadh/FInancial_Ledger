from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment or a .env file."""

    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "sqlite:///./ledger.db"
    base_currency: str = "USD"

    # NVIDIA NIM is OpenAI-compatible; an absent key disables the AI routes
    # rather than breaking the app. See app/ai.py.
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "meta/llama-3.3-70b-instruct"

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def ai_enabled(self) -> bool:
        return bool(self.nvidia_api_key.strip())

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
