"""Tests for POST /internal/emails/flush."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
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


def test_missing_bearer_returns_401():
    with TestClient(app) as client:
        resp = client.post("/internal/emails/flush")
    # FastAPI's HTTPBearer with auto_error=True returns 403 if no Authorization
    # header is sent. Accept either 401 or 403 as "rejected".
    assert resp.status_code in (401, 403)


def test_wrong_bearer_returns_401():
    with TestClient(app) as client:
        resp = client.post(
            "/internal/emails/flush",
            headers={"Authorization": "Bearer wrong"},
        )
    assert resp.status_code == 401


def test_valid_bearer_no_rows_returns_zero_counts():
    with (
        patch("app.routes.internal_emails.build_service_role_client"),
        patch("app.routes.internal_emails.fetch_ready_pending_emails") as fr,
        patch("app.routes.internal_emails.try_send_pending_email") as send,
    ):
        fr.return_value = []

        with TestClient(app) as client:
            resp = client.post(
                "/internal/emails/flush",
                headers={"Authorization": "Bearer shh"},
            )

    assert resp.status_code == 200
    assert resp.json() == {"scanned": 0, "sent": 0, "failed": 0}
    send.assert_not_called()


def test_valid_bearer_two_rows_one_succeeds_one_fails():
    with (
        patch("app.routes.internal_emails.build_service_role_client"),
        patch("app.routes.internal_emails.fetch_ready_pending_emails") as fr,
        patch("app.routes.internal_emails.try_send_pending_email") as send,
    ):
        fr.return_value = [
            {
                "id": 1,
                "template": "welcome",
                "to_email": "a@x.com",
                "payload": {},
                "attempts": 0,
                "max_attempts": 8,
            },
            {
                "id": 2,
                "template": "account_deleted",
                "to_email": "b@x.com",
                "payload": {},
                "attempts": 0,
                "max_attempts": 8,
            },
        ]
        send.side_effect = [True, False]

        with TestClient(app) as client:
            resp = client.post(
                "/internal/emails/flush",
                headers={"Authorization": "Bearer shh"},
            )

    assert resp.status_code == 200
    assert resp.json() == {"scanned": 2, "sent": 1, "failed": 1}
    assert send.call_count == 2
    assert send.call_args_list[0][0][0] == 1
    assert send.call_args_list[1][0][0] == 2


def test_body_batch_size_clamps_to_200():
    with (
        patch("app.routes.internal_emails.build_service_role_client"),
        patch("app.routes.internal_emails.fetch_ready_pending_emails") as fr,
        patch("app.routes.internal_emails.try_send_pending_email"),
    ):
        fr.return_value = []

        with TestClient(app) as client:
            resp = client.post(
                "/internal/emails/flush",
                headers={"Authorization": "Bearer shh"},
                json={"batch_size": 999},
            )

    assert resp.status_code == 200
    fr.assert_called_once()
    # Second positional arg is the limit.
    assert fr.call_args[0][1] == 200 or fr.call_args.kwargs.get("limit") == 200
