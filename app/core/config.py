"""Application settings, loaded only from environment variables (Replit Secrets in production)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "LaunchLens"
    env: str = "dev"  # dev | test | prod
    database_url: str = "sqlite:///./launchlens.db"
    secret_key: str = "dev-only-change-me"  # noqa: S105 — dev default; prod refuses to start with it
    fernet_key: str = ""  # required in prod; generated ephemeral key in dev
    base_url: str = "http://localhost:8000"

    admin_email: str = "admin@launchlens.app"
    admin_password: str = ""
    admin_notify_email: str = ""

    # Shared default AI keys (restricted by policy)
    default_anthropic_key: str = ""
    default_openai_key: str = ""
    default_google_key: str = ""

    # Public data API keys (all free)
    openfda_api_key: str = ""
    census_api_key: str = ""
    regulations_api_key: str = ""

    # Dataset identifiers that change between releases (overridable)
    places_dataset_id: str = "swc5-untb"
    open_payments_dataset_id: str = ""
    acs_year: int = 2023
    formulary_zip_url: str = ""

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@launchlens.app"

    http_timeout: float = 30.0
    user_agent: str = "LaunchLens/1.0 (research prototype; contact via app owner)"

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
