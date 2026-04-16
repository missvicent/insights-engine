"""Unit tests for app/routes/deps.py.

We mint real JWTs against a test secret — no mocking of jwt.decode. The
Supabase client returned inside UserContext is replaced with a sentinel so
tests don't hit the network.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.routes.deps import get_user_ctx


class _FakeDB:
    """Sentinel stand-in for the Supabase client."""


@pytest.fixture(autouse=True)
def _patch_build_user_client(jwt_secret):
    """Stop get_user_ctx from building a real Supabase client in tests."""
    with patch("app.routes.deps.build_user_client", return_value=_FakeDB()):
        yield


@pytest.fixture(autouse=True)
def _patch_settings(jwt_secret):
    """Point get_user_ctx at the test JWT secret."""
    with patch("app.routes.deps.get_settings") as m:
        m.return_value.supabase_jwt_secret = jwt_secret
        yield


def _call(header_value: str | None):
    return get_user_ctx(authorization=header_value)


class TestGetUserCtx:
    def test_valid_token_returns_user_context(self, make_token):
        token = make_token(sub="user-42")
        ctx = _call(f"Bearer {token}")
        assert ctx.user_id == "user-42"
        assert isinstance(ctx.db, _FakeDB)

    def test_missing_header_is_401(self):
        with pytest.raises(HTTPException) as exc:
            _call(None)
        assert exc.value.status_code == 401
        assert exc.value.detail == "missing authorization header"

    def test_wrong_scheme_is_401(self, make_token):
        token = make_token()
        with pytest.raises(HTTPException) as exc:
            _call(f"Basic {token}")
        assert exc.value.status_code == 401
        assert exc.value.detail == "invalid authorization header"

    def test_bearer_without_token_is_401(self):
        with pytest.raises(HTTPException) as exc:
            _call("Bearer ")
        assert exc.value.status_code == 401
        assert exc.value.detail == "invalid authorization header"

    def test_wrong_secret_is_401(self, make_token):
        token = make_token(secret="not-the-real-secret")
        with pytest.raises(HTTPException) as exc:
            _call(f"Bearer {token}")
        assert exc.value.status_code == 401
        assert exc.value.detail == "invalid token"

    def test_expired_token_is_401(self, make_token):
        token = make_token(exp_delta=-10)
        with pytest.raises(HTTPException) as exc:
            _call(f"Bearer {token}")
        assert exc.value.status_code == 401
        assert exc.value.detail == "token expired"

    def test_wrong_audience_is_401(self, make_token):
        token = make_token(audience="service_role")
        with pytest.raises(HTTPException) as exc:
            _call(f"Bearer {token}")
        assert exc.value.status_code == 401
        assert exc.value.detail == "invalid token"

    def test_missing_sub_is_401(self, make_token):
        token = make_token(omit=("sub",))
        with pytest.raises(HTTPException) as exc:
            _call(f"Bearer {token}")
        assert exc.value.status_code == 401
        assert exc.value.detail == "invalid token"
