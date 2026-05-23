"""Unit tests for the email_service consumer (try_send_pending_email)."""

from unittest.mock import patch

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_ISSUER", "https://test.clerk.test")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test")
    monkeypatch.setenv("CRON_SHARED_SECRET", "shh")
    monkeypatch.setenv("RESEND_TEMPLATE_WELCOME", "tpl_welcome")
    monkeypatch.setenv("RESEND_TEMPLATE_ACCOUNT_DELETED", "tpl_deleted")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_k")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srv_k")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestTrySendPendingEmail:
    def test_returns_false_when_row_already_claimed(self):
        from app.services.email_service import try_send_pending_email

        with (
            patch("app.services.email_service.build_service_role_client") as bsrc,
            patch("app.services.email_service.claim_pending_email") as claim,
        ):
            claim.return_value = None

            result = try_send_pending_email(42)

        assert result is False
        bsrc.assert_called_once()
        claim.assert_called_once()

    def test_welcome_template_path_marks_sent_on_resend_success(self):
        from app.services.email_service import try_send_pending_email

        with (
            patch("app.services.email_service.build_service_role_client"),
            patch("app.services.email_service.claim_pending_email") as claim,
            patch("app.services.email_service._send") as send,
            patch("app.services.email_service.mark_pending_email_sent") as mark_sent,
            patch(
                "app.services.email_service.mark_pending_email_failed"
            ) as mark_failed,
        ):
            claim.return_value = {
                "id": 42,
                "template": "welcome",
                "to_email": "alice@x.com",
                "payload": {"first_name": "Alice"},
            }
            send.return_value = True

            result = try_send_pending_email(42)

        assert result is True
        send.assert_called_once_with("alice@x.com", "tpl_welcome", {"USER": "Alice"})
        mark_sent.assert_called_once()
        mark_failed.assert_not_called()

    def test_account_deleted_template_path_uses_deleted_template_id(self):
        from app.services.email_service import try_send_pending_email

        with (
            patch("app.services.email_service.build_service_role_client"),
            patch("app.services.email_service.claim_pending_email") as claim,
            patch("app.services.email_service._send") as send,
            patch("app.services.email_service.mark_pending_email_sent"),
            patch("app.services.email_service.mark_pending_email_failed"),
        ):
            claim.return_value = {
                "id": 7,
                "template": "account_deleted",
                "to_email": "ex@x.com",
                "payload": {"first_name": "Ex"},
            }
            send.return_value = True

            try_send_pending_email(7)

        send.assert_called_once_with("ex@x.com", "tpl_deleted", {"USER": "Ex"})

    def test_resend_failure_marks_failed_not_sent(self):
        from app.services.email_service import try_send_pending_email

        with (
            patch("app.services.email_service.build_service_role_client"),
            patch("app.services.email_service.claim_pending_email") as claim,
            patch("app.services.email_service._send") as send,
            patch("app.services.email_service.mark_pending_email_sent") as mark_sent,
            patch(
                "app.services.email_service.mark_pending_email_failed"
            ) as mark_failed,
        ):
            claim.return_value = {
                "id": 9,
                "template": "welcome",
                "to_email": "a@x.com",
                "payload": {},
            }
            send.return_value = False

            result = try_send_pending_email(9)

        assert result is False
        mark_sent.assert_not_called()
        mark_failed.assert_called_once()
        args = mark_failed.call_args[0]
        assert args[1] == 9
        assert isinstance(args[2], str) and args[2]

    def test_unknown_template_marks_failed(self):
        """Defense in depth: the CHECK constraint forbids unknown
        templates, but if a row somehow has one, fail explicitly
        instead of silently looping."""
        from app.services.email_service import try_send_pending_email

        with (
            patch("app.services.email_service.build_service_role_client"),
            patch("app.services.email_service.claim_pending_email") as claim,
            patch("app.services.email_service._send") as send,
            patch("app.services.email_service.mark_pending_email_sent") as mark_sent,
            patch(
                "app.services.email_service.mark_pending_email_failed"
            ) as mark_failed,
        ):
            claim.return_value = {
                "id": 11,
                "template": "marketing_blast",
                "to_email": "a@x.com",
                "payload": {},
            }

            result = try_send_pending_email(11)

        assert result is False
        send.assert_not_called()
        mark_sent.assert_not_called()
        mark_failed.assert_called_once()
