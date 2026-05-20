from unittest.mock import MagicMock, call, patch

import pytest

from app.context import UserContext
from app.models.schemas import AuditEvent
from app.services.clerk_admin import ClerkAPIError
from app.services.deletion_service import ClerkDeleteFailed, delete_account


def test_happy_path_enqueues_account_deleted_email() -> None:
    # ── arrange ──
    sr_client = MagicMock()
    user_ctx = MagicMock(user_id="user_abc")
    with (
        patch("app.services.deletion_service.fetch_profile_for_deletion") as fp,
        patch("app.services.deletion_service.insert_audit_event") as audit,
        patch("app.services.deletion_service.call_delete_user_data") as cdud,
        patch("app.services.deletion_service.delete_clerk_user") as dcu,
        patch("app.services.deletion_service.enqueue_email") as enq,
    ):
        fp.return_value = ("u@x.com", "Jane")
        enq.return_value = 77

        # ── act ──
        row_id = delete_account(user_ctx, sr_client)

    # ── assert ──
    assert row_id == 77
    fp.assert_called_once_with(sr_client, "user_abc")
    audit.assert_called_once()
    cdud.assert_called_once_with(sr_client, "user_abc")
    dcu.assert_called_once_with("user_abc")
    enq.assert_called_once_with(
        sr_client,
        template="account_deleted",
        to_email="u@x.com",
        payload={"first_name": "Jane"},
    )


def test_profile_not_found() -> None:
    sr_client = MagicMock()
    ctx = UserContext(user_id="user_abc", db=MagicMock())

    with (
        patch(
            "app.services.deletion_service.fetch_profile_for_deletion",
            return_value=None,
        ) as mock_fetch_profile,
        patch(
            "app.services.deletion_service.insert_audit_event"
        ) as mock_insert_audit_event,
        patch(
            "app.services.deletion_service.call_delete_user_data"
        ) as mock_call_delete_user_data,
        patch(
            "app.services.deletion_service.delete_clerk_user"
        ) as mock_delete_clerk_user,
        patch("app.services.deletion_service.enqueue_email") as mock_enqueue_email,
    ):
        result = delete_account(ctx, sr_client)

        assert result is None
        mock_fetch_profile.assert_called_once_with(sr_client, "user_abc")
        mock_insert_audit_event.assert_called_once_with(
            sr_client, "user_abc", AuditEvent.REQUEST_INITIATED
        )
        mock_call_delete_user_data.assert_called_once_with(sr_client, "user_abc")
        mock_delete_clerk_user.assert_called_once_with("user_abc")
        mock_enqueue_email.assert_not_called()


def test_clerk_delete_failed() -> None:
    sr_client = MagicMock()
    ctx = UserContext(user_id="user_abc", db=MagicMock())

    with (
        patch(
            "app.services.deletion_service.fetch_profile_for_deletion",
            return_value=("u@x.com", "Jane Doe"),
        ) as mock_fetch_profile,
        patch(
            "app.services.deletion_service.insert_audit_event"
        ) as mock_insert_audit_event,
        patch(
            "app.services.deletion_service.call_delete_user_data"
        ) as mock_call_delete_user_data,
        patch(
            "app.services.deletion_service.delete_clerk_user",
            side_effect=ClerkAPIError("boom"),
        ) as mock_delete_clerk_user,
        patch("app.services.deletion_service.enqueue_email") as mock_enqueue_email,
    ):
        with pytest.raises(ClerkDeleteFailed) as exc_info:
            delete_account(ctx, sr_client)

        assert "boom" in str(exc_info.value)
        mock_fetch_profile.assert_called_once_with(sr_client, "user_abc")
        mock_call_delete_user_data.assert_called_once_with(sr_client, "user_abc")
        mock_delete_clerk_user.assert_called_once_with("user_abc")
        assert mock_insert_audit_event.call_count == 2
        assert mock_insert_audit_event.call_args_list[0] == call(
            sr_client, "user_abc", AuditEvent.REQUEST_INITIATED
        )
        assert mock_insert_audit_event.call_args_list[1] == call(
            sr_client, "user_abc", AuditEvent.CLERK_DELETE_FAILED, {"error": "boom"}
        )
        mock_enqueue_email.assert_not_called()
