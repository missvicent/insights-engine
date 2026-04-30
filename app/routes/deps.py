"""FastAPI dependencies for the routes layer.

`get_user_ctx` is the single edge-of-system auth dependency: it verifies a
Supabase-issued JWT and bundles the verified user id with a per-request
user-scoped Supabase client.
"""

import logging
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwks import get_jwks_client
from app.config import get_settings
from app.context import UserContext
from app.db.client import build_user_client

logger = logging.getLogger(__name__)


bearer_scheme = HTTPBearer(
    auto_error=True,
    scheme_name="ClerkBearer",
    description=(
        "Clerk-issued JWT (Third-Party Auth, RS256, verified against Clerk's JWKS). "
        "Paste the token only — Swagger prepends 'Bearer '. "
        "Clerk tokens typically live ~60s; refresh from the frontend if it expires."
    ),
)


def get_user_ctx(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> UserContext:
    settings = get_settings()
    token = credentials.credentials
    try:
        signing_key = get_jwks_client().get_signing_key_from_jwt(token).key
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience="authenticated",
            issuer=settings.clerk_issuer,
            leeway=5,  # matches Clerk's dashboard "Allowed clock skew: 5s"
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid token") from None
    except jwt.InvalidIssuerError:
        raise HTTPException(status_code=401, detail="invalid token issuer") from None
    except Exception:
        raise HTTPException(status_code=401, detail="unable to verify token") from None

    user_id = payload["sub"]
    if not user_id:
        raise HTTPException(status_code=401, detail="invalid token")
    return UserContext(user_id=user_id, db=build_user_client(token))
