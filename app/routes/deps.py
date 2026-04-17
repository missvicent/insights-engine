"""FastAPI dependencies for the routes layer.

`get_user_ctx` is the single edge-of-system auth dependency: it verifies a
Supabase-issued JWT and bundles the verified user id with a per-request
user-scoped Supabase client.
"""

from typing import Annotated

import jwt
from fastapi import Header, HTTPException

from app.context import UserContext
from app.db.client import build_user_client, get_settings


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="missing authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="invalid authorization header")
    return token


def get_user_ctx(
    authorization: Annotated[str | None, Header()] = None,
) -> UserContext:
    token = _extract_bearer(authorization)
    try:
        payload = jwt.decode(
            token,
            get_settings().supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
            options={"require": ["exp", "sub", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="invalid token") from None

    return UserContext(user_id=payload["sub"], db=build_user_client(token))
