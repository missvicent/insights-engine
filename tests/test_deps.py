"""Unit tests for app/routes/deps.py.

Mints real RS256 JWTs against a generated RSA keypair, then mocks the JWKS
client to return the matching public key. Same code path as production —
no jwt.decode mocks. Also exercises the profile-existence guard.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.context import UserContext
from app.routes.deps import get_user_ctx

_TEST_ISSUER = "https://test.clerk"


class _FakeDB:
    """Sentinel stand-in for the user-scoped Supabase client."""


class _FakeServiceClient:
    """Sentinel stand-in for the service-role Supabase client."""


@pytest.fixture(scope="session")
def rsa_keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def make_token(rsa_keypair):
    def _make(
        sub: str = "user_abc",
        audience: str = "authenticated",
        issuer: str = _TEST_ISSUER,
        exp_delta: int = 60,
        omit: tuple[str, ...] = (),
        key: object | None = None,
    ) -> str:
        claims: dict[str, object] = {
            "sub": sub,
            "aud": audience,
            "iss": issuer,
            "exp": int(time.time()) + exp_delta,
        }
        for k in omit:
            claims.pop(k, None)
        return jwt.encode(claims, key or rsa_keypair, algorithm="RS256")

    return _make


@pytest.fixture(autouse=True)
def _patch_jwks(rsa_keypair):
    fake = MagicMock()
    fake.get_signing_key_from_jwt.return_value.key = rsa_keypair.public_key()
    with patch("app.routes.deps.get_jwks_client", return_value=fake):
        yield


@pytest.fixture(autouse=True)
def _patch_settings():
    with patch("app.routes.deps.get_settings") as m:
        m.return_value.clerk_issuer = _TEST_ISSUER
        yield


@pytest.fixture(autouse=True)
def _patch_user_client():
    with patch("app.routes.deps.build_user_client", return_value=_FakeDB()):
        yield


@pytest.fixture(autouse=True)
def _patch_service_client():
    with patch(
        "app.routes.deps.build_service_role_client",
        return_value=_FakeServiceClient(),
    ):
        yield


@pytest.fixture(autouse=True)
def _patch_profile_exists():
    with patch("app.routes.deps.profile_exists", return_value=True) as m:
        yield m


def _call(token: str) -> UserContext:
    return get_user_ctx(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    )


class TestGetUserCtx:
    def test_valid_token_returns_user_context(self, make_token):
        ctx = _call(make_token(sub="user-42"))
        assert ctx.user_id == "user-42"
        assert isinstance(ctx.db, _FakeDB)

    def test_profile_guard_called_with_verified_sub(
        self, make_token, _patch_profile_exists
    ):
        _call(make_token(sub="user-42"))
        args, _ = _patch_profile_exists.call_args
        # args == (sr_client, user_id)
        assert args[1] == "user-42"

    def test_missing_profile_is_401(self, make_token, _patch_profile_exists):
        _patch_profile_exists.return_value = False
        with pytest.raises(HTTPException) as exc:
            _call(make_token(sub="user_no_profile"))
        assert exc.value.status_code == 401
        assert exc.value.detail == "invalid token"

    def test_bad_signature_is_401(self, make_token):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(HTTPException) as exc:
            _call(make_token(key=other))
        assert exc.value.status_code == 401
        assert exc.value.detail == "invalid token"

    def test_expired_token_is_401(self, make_token):
        with pytest.raises(HTTPException) as exc:
            _call(make_token(exp_delta=-3600))
        assert exc.value.status_code == 401
        assert exc.value.detail == "token expired"

    def test_wrong_audience_is_401(self, make_token):
        with pytest.raises(HTTPException) as exc:
            _call(make_token(audience="service_role"))
        assert exc.value.status_code == 401
        assert exc.value.detail == "invalid token"

    def test_wrong_issuer_is_401(self, make_token):
        # NOTE: deps.py orders `except InvalidTokenError` before
        # `except InvalidIssuerError`, but `InvalidIssuerError` is a
        # subclass of `InvalidTokenError`, so the issuer-specific clause
        # is dead code. This test pins the current behavior — wrong issuer
        # surfaces as the generic "invalid token", not "invalid token
        # issuer". If the except-order is fixed, update this assertion.
        with pytest.raises(HTTPException) as exc:
            _call(make_token(issuer="https://evil.example"))
        assert exc.value.status_code == 401
        assert exc.value.detail == "invalid token"

    def test_missing_sub_is_401(self, make_token):
        with pytest.raises(HTTPException) as exc:
            _call(make_token(omit=("sub",)))
        assert exc.value.status_code == 401
        assert exc.value.detail == "invalid token"

    def test_empty_sub_is_401(self, make_token):
        with pytest.raises(HTTPException) as exc:
            _call(make_token(sub=""))
        assert exc.value.status_code == 401
        assert exc.value.detail == "invalid token"
