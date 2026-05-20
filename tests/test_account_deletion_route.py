"""Tests for POST /account/delete."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ACCOUNT_DELETION_ENABLED", "true")
    monkeypatch.setenv("CLERK_ISSUER", "https://test.clerk.test")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test")
    monkeypatch.setenv("CLERK_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("CRON_SHARED_SECRET", "shh")
    monkeypatch.setenv("RESEND_TEMPLATE_WELCOME", "tpl_w")
    monkeypatch.setenv("RESEND_TEMPLATE_ACCOUNT_DELETED", "tpl_d")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_k")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srv_k")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _override_user_ctx():
    from app.context import UserContext
    from app.routes.deps import get_user_ctx

    app.dependency_overrides[get_user_ctx] = lambda: UserContext(
        user_id="user_abc", db=MagicMock()
    )
    yield
    app.dependency_overrides.pop(get_user_ctx, None)


@pytest.fixture(autouse=True)
def _override():
    yield from _override_user_ctx()


def test_returns_204_and_queues_fast_path_send():
    with (
        patch("app.routes.account_deletion.build_service_role_client") as bsrc,
        patch("app.routes.account_deletion.delete_account_service") as svc,
        patch("app.routes.account_deletion.try_send_pending_email") as send,
    ):
        bsrc.return_value = MagicMock()
        svc.return_value = 55

        with TestClient(app) as client:
            resp = client.post(
                "/account/delete",
                headers={"Authorization": "Bearer placeholder"},
            )

    assert resp.status_code == 204
    svc.assert_called_once()
    send.assert_called_once_with(55)


def test_returns_204_when_no_email_to_send():
    """delete_account returns None when there's no profile row — the
    route still returns 204 and just doesn't queue a send."""
    with (
        patch("app.routes.account_deletion.build_service_role_client"),
        patch("app.routes.account_deletion.delete_account_service") as svc,
        patch("app.routes.account_deletion.try_send_pending_email") as send,
    ):
        svc.return_value = None

        with TestClient(app) as client:
            resp = client.post(
                "/account/delete",
                headers={"Authorization": "Bearer placeholder"},
            )

    assert resp.status_code == 204
    send.assert_not_called()


def test_maps_clerk_delete_failed_to_502():
    from app.services.deletion_service import ClerkDeleteFailed

    with (
        patch("app.routes.account_deletion.build_service_role_client"),
        patch("app.routes.account_deletion.delete_account_service") as svc,
    ):
        svc.side_effect = ClerkDeleteFailed("clerk 5xx")

        with TestClient(app) as client:
            resp = client.post(
                "/account/delete",
                headers={"Authorization": "Bearer placeholder"},
            )

    assert resp.status_code == 502


def test_returns_503_when_flag_disabled(monkeypatch):
    monkeypatch.setenv("ACCOUNT_DELETION_ENABLED", "false")
    get_settings.cache_clear()

    with TestClient(app) as client:
        resp = client.post(
            "/account/delete",
            headers={"Authorization": "Bearer placeholder"},
        )

    assert resp.status_code == 503
