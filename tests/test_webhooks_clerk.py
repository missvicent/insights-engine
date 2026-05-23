"""Unit tests for the single Clerk webhook dispatcher."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from svix.webhooks import WebhookVerificationError

from app.config import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_ISSUER", "https://test.clerk.test")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test")
    monkeypatch.setenv("CLERK_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("CRON_SHARED_SECRET", "shh")
    monkeypatch.setenv("RESEND_TEMPLATE_WELCOME", "tpl_welcome")
    monkeypatch.setenv("RESEND_TEMPLATE_ACCOUNT_DELETED", "tpl_deleted")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_k")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srv_k")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _headers():
    return {
        "svix-id": "msg_test_1",
        "svix-timestamp": "1700000000",
        "svix-signature": "v1,sig",
    }


def _post(body: dict) -> Response:
    with TestClient(app) as client:
        return client.post("/webhooks/clerk", json=body, headers=_headers())


@patch("app.routes.webhooks_clerk.Webhook")
def test_bad_signature_returns_401(MockWebhook):
    MockWebhook.return_value.verify.side_effect = WebhookVerificationError("bad")

    with (
        patch("app.routes.webhooks_clerk.build_service_role_client") as bsrc,
        patch("app.routes.webhooks_clerk.record_webhook_event") as rec,
        patch("app.routes.webhooks_clerk.enqueue_email") as enq,
    ):
        resp = _post({"type": "user.created", "data": {}})

    assert resp.status_code == 401
    bsrc.assert_not_called()
    rec.assert_not_called()
    enq.assert_not_called()


@patch("app.routes.webhooks_clerk.Webhook")
def test_duplicate_svix_id_returns_204_no_enqueue(MockWebhook):
    MockWebhook.return_value.verify.return_value = None

    with (
        patch("app.routes.webhooks_clerk.build_service_role_client") as bsrc,
        patch("app.routes.webhooks_clerk.record_webhook_event") as rec,
        patch("app.routes.webhooks_clerk.enqueue_email") as enq,
        patch("app.routes.webhooks_clerk.call_delete_user_data") as cdud,
    ):
        bsrc.return_value = MagicMock()
        rec.return_value = False  # duplicate

        resp = _post({"type": "user.created", "data": {}})

    assert resp.status_code == 204
    enq.assert_not_called()
    cdud.assert_not_called()


@patch("app.routes.webhooks_clerk.Webhook")
def test_user_created_enqueues_welcome(MockWebhook):
    MockWebhook.return_value.verify.return_value = None
    body = {
        "type": "user.created",
        "data": {
            "id": "user_abc",
            "first_name": "Alice",
            "primary_email_address_id": "idn_1",
            "email_addresses": [
                {"id": "idn_0", "email_address": "old@x.com"},
                {"id": "idn_1", "email_address": "alice@x.com"},
            ],
        },
    }

    with (
        patch("app.routes.webhooks_clerk.build_service_role_client") as bsrc,
        patch("app.routes.webhooks_clerk.record_webhook_event") as rec,
        patch("app.routes.webhooks_clerk.enqueue_email") as enq,
        patch("app.routes.webhooks_clerk.try_send_pending_email") as send,
    ):
        bsrc.return_value = MagicMock()
        rec.return_value = True
        enq.return_value = 99

        resp = _post(body)

    assert resp.status_code == 204
    enq.assert_called_once_with(
        bsrc.return_value,
        template="welcome",
        to_email="alice@x.com",
        payload={"first_name": "Alice"},
    )
    # The fast-path BackgroundTask runs after the response, so by the
    # time TestClient returns, try_send_pending_email(99) has been called.
    send.assert_called_once_with(99)


@patch("app.routes.webhooks_clerk.Webhook")
def test_user_created_no_primary_email_skips_enqueue(MockWebhook):
    MockWebhook.return_value.verify.return_value = None
    body = {
        "type": "user.created",
        "data": {
            "id": "user_abc",
            "first_name": "Alice",
            "primary_email_address_id": "idn_missing",
            "email_addresses": [
                {"id": "idn_0", "email_address": "other@x.com"},
            ],
        },
    }

    with (
        patch("app.routes.webhooks_clerk.build_service_role_client") as bsrc,
        patch("app.routes.webhooks_clerk.record_webhook_event") as rec,
        patch("app.routes.webhooks_clerk.enqueue_email") as enq,
    ):
        bsrc.return_value = MagicMock()
        rec.return_value = True

        resp = _post(body)

    assert resp.status_code == 204
    enq.assert_not_called()


@patch("app.routes.webhooks_clerk.Webhook")
def test_user_deleted_wipes_and_enqueues(MockWebhook):
    MockWebhook.return_value.verify.return_value = None
    body = {"type": "user.deleted", "data": {"id": "user_xyz"}}

    with (
        patch("app.routes.webhooks_clerk.build_service_role_client") as bsrc,
        patch("app.routes.webhooks_clerk.record_webhook_event") as rec,
        patch("app.routes.webhooks_clerk.fetch_profile_for_deletion") as fp,
        patch("app.routes.webhooks_clerk.call_delete_user_data") as cdud,
        patch("app.routes.webhooks_clerk.enqueue_email") as enq,
        patch("app.routes.webhooks_clerk.try_send_pending_email") as send,
    ):
        bsrc.return_value = MagicMock()
        rec.return_value = True
        fp.return_value = ("xyz@x.com", "Ex User")
        enq.return_value = 101

        resp = _post(body)

    assert resp.status_code == 204
    cdud.assert_called_once_with(bsrc.return_value, "user_xyz")
    enq.assert_called_once_with(
        bsrc.return_value,
        template="account_deleted",
        to_email="xyz@x.com",
        payload={"first_name": "Ex User"},
    )
    send.assert_called_once_with(101)


@patch("app.routes.webhooks_clerk.Webhook")
def test_user_deleted_missing_id_skips_wipe(MockWebhook):
    MockWebhook.return_value.verify.return_value = None
    body = {"type": "user.deleted", "data": {}}

    with (
        patch("app.routes.webhooks_clerk.build_service_role_client") as bsrc,
        patch("app.routes.webhooks_clerk.record_webhook_event") as rec,
        patch("app.routes.webhooks_clerk.call_delete_user_data") as cdud,
        patch("app.routes.webhooks_clerk.enqueue_email") as enq,
    ):
        bsrc.return_value = MagicMock()
        rec.return_value = True

        resp = _post(body)

    assert resp.status_code == 204
    cdud.assert_not_called()
    enq.assert_not_called()


@patch("app.routes.webhooks_clerk.Webhook")
def test_unknown_event_type_returns_204_no_work(MockWebhook):
    MockWebhook.return_value.verify.return_value = None
    body = {"type": "session.created", "data": {"id": "sess_1"}}

    with (
        patch("app.routes.webhooks_clerk.build_service_role_client") as bsrc,
        patch("app.routes.webhooks_clerk.record_webhook_event") as rec,
        patch("app.routes.webhooks_clerk.enqueue_email") as enq,
        patch("app.routes.webhooks_clerk.call_delete_user_data") as cdud,
    ):
        bsrc.return_value = MagicMock()
        rec.return_value = True

        resp = _post(body)

    assert resp.status_code == 204
    enq.assert_not_called()
    cdud.assert_not_called()
