"""Centralized settings, loaded from environment / .env.

Usage:
    from kb_fabric.config import get_settings
    settings = get_settings()
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Postgres ---
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "kb_fabric"
    postgres_user: str = "kb_fabric"
    postgres_password: str = ""
    database_url: str = ""

    # --- Redis / Celery ---
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_url: str = "redis://127.0.0.1:6379/0"
    celery_broker_url: str = "redis://127.0.0.1:6379/0"
    celery_result_backend: str = "redis://127.0.0.1:6379/1"

    # --- LLM (Landmark LiteLLM proxy) ---
    litellm_base_url: str = "https://lmlitellm.landmarkgroup.com"
    litellm_api_key: str = ""
    chat_model: str = "gpt-5.5"
    embedding_model: str = "landmark-text-embedding-3-large"

    # --- Local data paths ---
    raw_docs_dir: str = "./data/raw"
    processed_dir: str = "./data/processed"

    @property
    def sqlalchemy_database_url(self) -> str:
        """Prefer explicit DATABASE_URL if set, else build from parts."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
