"""Application settings.

Top of the dependency graph: imported by auth, db, routes, and services.
Keeping config out of `db/client.py` lets `auth/jwks.py` and `ai_service.py`
load settings without pulling in supabase as a transitive import.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Required ────────────────────────────────────────────────────────
    clerk_issuer: str
    supabase_anon_key: str
    supabase_url: str

    # ── Required-with-default ───────────────────────────────────────────
    ai_model: str = "anthropic/claude-haiku-4-5-20251001"
    clerk_jwks_url: str | None = None

    # Comma-separated in env (`CORS_ORIGINS=http://a,http://b`); `NoDecode`
    # opts out of pydantic-settings' default JSON-decode for list fields
    # so the validator below can do plain comma-splitting.
    cors_origins: Annotated[list[str], NoDecode] = []

    # ── Optional features (boot proceeds without them) ──────────────────
    # Email + webhook signatures are only needed when the welcome webhook
    # is wired. Leave unset in environments that don't ship email.
    clerk_webhook_secret: str | None = None
    resend_api_key: str | None = None
    resend_from_email: str | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
