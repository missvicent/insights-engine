from functools import lru_cache
from pydantic_settings import BaseSettings
from supabase import create_client, Client

class Settings(BaseSettings):
    supabase_url: str
    supabase_service_key: str

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()


# service key bypasses RLS — safe for backend only, never expose to frontend
@lru_cache
def get_supabase() -> Client:
    s = get_settings()

    return create_client(s.supabase_url, s.supabase_service_key)
    