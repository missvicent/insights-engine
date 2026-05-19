"""Unit tests for the account-deletion helpers added to app/db/client.py.

Covers the six service-role helpers from Phase 2 of the account-deletion
plan: build_service_role_client, fetch_profile_for_deletion, profile_exists,
insert_audit_event, record_webhook_event, call_delete_user_data.

All helpers receive their Supabase client as an argument (except
build_service_role_client itself), so we drive them with MagicMock and
assert on the call chain. No real network or DB.
"""

import hashlib
from unittest.mock import MagicMock

import pytest

from app.config import get_settings
from app.db.client import (
    build_service_role_client,
    call_delete_user_data,
    fetch_profile_for_deletion,
    insert_audit_event,
    profile_exists,
    record_webhook_event,
)
from app.models.schemas import AuditEvent


class TestBuildServiceRoleClient:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setenv("CLERK_ISSUER", "https://test.clerk.test")
        monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test")
        monkeypatch.setenv("RESEND_TEMPLATE_WELCOME", "tpl_welcome")
        monkeypatch.setenv("RESEND_TEMPLATE_ACCOUNT_DELETED", "tpl_deleted")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.test")
        monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_k")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srv_k")
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def test_uses_service_role_key(self, monkeypatch):
        sentinel = object()
        captured: dict[str, str] = {}

        def fake_create_client(url: str, key: str) -> object:
            captured["url"] = url
            captured["key"] = key
            return sentinel

        monkeypatch.setattr("app.db.client.create_client", fake_create_client)

        result = build_service_role_client()

        assert result is sentinel
        assert captured == {
            "url": "https://x.supabase.test",
            "key": "srv_k",
        }


class TestFetchProfileForDeletion:
    def test_returns_email_and_full_name(self):
        client = MagicMock()
        limit = client.table.return_value.select.return_value.eq.return_value.limit
        execute = limit.return_value.execute
        execute.return_value.data = [{"email": "u@x.com", "full_name": "Jane Doe"}]

        result = fetch_profile_for_deletion(client, "user_abc")

        assert result == ("u@x.com", "Jane Doe")
        client.table.assert_called_once_with("profiles")
        client.table.return_value.select.assert_called_once_with("email, full_name")
        client.table.return_value.select.return_value.eq.assert_called_once_with(
            "clerk_user_id", "user_abc"
        )
        (
            client.table.return_value.select.return_value.eq.return_value.limit
        ).assert_called_once_with(1)

    def test_returns_email_and_none_when_full_name_missing(self):
        client = MagicMock()
        limit = client.table.return_value.select.return_value.eq.return_value.limit
        execute = limit.return_value.execute
        execute.return_value.data = [{"email": "u@x.com"}]

        result = fetch_profile_for_deletion(client, "user_abc")

        assert result == ("u@x.com", None)

    def test_returns_none_when_profile_missing(self):
        client = MagicMock()
        limit = client.table.return_value.select.return_value.eq.return_value.limit
        execute = limit.return_value.execute
        execute.return_value.data = []

        result = fetch_profile_for_deletion(client, "user_abc")

        assert result is None


class TestProfileExists:
    def test_true_when_row_present(self):
        client = MagicMock()
        limit = client.table.return_value.select.return_value.eq.return_value.limit
        execute = limit.return_value.execute
        execute.return_value.data = [{"clerk_user_id": "user_abc"}]

        assert profile_exists(client, "user_abc") is True
        client.table.assert_called_once_with("profiles")
        client.table.return_value.select.assert_called_once_with("clerk_user_id")
        client.table.return_value.select.return_value.eq.assert_called_once_with(
            "clerk_user_id", "user_abc"
        )

    def test_false_when_no_row(self):
        client = MagicMock()
        limit = client.table.return_value.select.return_value.eq.return_value.limit
        execute = limit.return_value.execute
        execute.return_value.data = []

        assert profile_exists(client, "user_abc") is False


class TestInsertAuditEvent:
    def test_inserts_hashed_user_id_and_event_value(self):
        client = MagicMock()
        user_id = "user_abc_123"
        expected_hash = hashlib.sha256(user_id.encode()).hexdigest()

        insert_audit_event(client, user_id, AuditEvent.REQUEST_INITIATED)

        client.table.assert_called_once_with("account_deletion_audit")
        client.table.return_value.insert.assert_called_once_with(
            {
                "user_id_hash": expected_hash,
                "event": "request_initiated",
                "metadata": None,
            }
        )
        client.table.return_value.insert.return_value.execute.assert_called_once_with()

    def test_passes_metadata_dict(self):
        client = MagicMock()
        meta = {"reason": "user_request"}

        insert_audit_event(
            client, "user_x", AuditEvent.CLERK_DELETE_FAILED, metadata=meta
        )

        payload = client.table.return_value.insert.call_args[0][0]
        assert payload["metadata"] == meta
        assert payload["event"] == "clerk_delete_failed"

    def test_raw_user_id_never_in_payload(self):
        client = MagicMock()
        secret_id = "user_secret_xyz"

        insert_audit_event(client, secret_id, AuditEvent.REQUEST_INITIATED)

        payload = client.table.return_value.insert.call_args[0][0]
        assert secret_id not in str(payload)


class TestRecordWebhookEvent:
    def test_returns_true_on_insert(self):
        client = MagicMock()
        execute = client.table.return_value.insert.return_value.execute
        execute.return_value.data = [{"svix_id": "msg_1"}]

        assert record_webhook_event(client, "msg_1") is True
        client.table.assert_called_once_with("webhook_events")
        client.table.return_value.insert.assert_called_once_with({"svix_id": "msg_1"})

    def test_returns_false_on_duplicate_via_code_attr(self):
        client = MagicMock()
        err = Exception("duplicate key")
        err.code = "23505"  # type: ignore[attr-defined]
        client.table.return_value.insert.return_value.execute.side_effect = err

        assert record_webhook_event(client, "msg_1") is False

    def test_returns_false_when_23505_in_message(self):
        client = MagicMock()
        err = Exception("23505 unique violation")
        client.table.return_value.insert.return_value.execute.side_effect = err

        assert record_webhook_event(client, "msg_1") is False

    def test_raises_on_other_error(self):
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.side_effect = (
            RuntimeError("boom")
        )

        with pytest.raises(RuntimeError):
            record_webhook_event(client, "msg_1")


class TestCallDeleteUserData:
    def test_calls_rpc_with_correct_params(self):
        client = MagicMock()

        call_delete_user_data(client, "user_abc")

        client.rpc.assert_called_once_with(
            "delete_user_data", {"p_clerk_user_id": "user_abc"}
        )
        client.rpc.return_value.execute.assert_called_once_with()

    def test_propagates_rpc_exceptions(self):
        client = MagicMock()
        client.rpc.return_value.execute.side_effect = RuntimeError("rpc fail")

        with pytest.raises(RuntimeError):
            call_delete_user_data(client, "user_abc")
