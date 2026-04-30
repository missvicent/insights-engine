from functools import lru_cache

from jwt import PyJWKClient

from app.config import get_settings


@lru_cache(maxsize=1)
def get_jwks_client() -> PyJWKClient:
    s = get_settings()
    url = s.clerk_jwks_url or f"{s.clerk_issuer.rstrip('/')}/.well-known/jwks.json"
    return PyJWKClient(url, cache_keys=True, max_cached_keys=16, lifespan=3600)
