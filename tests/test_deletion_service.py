from unittest.mock import MagicMock, call, patch

import pytest

from app.context import UserContext
from app.models.schemas import AuditEvent
from app.services.clerk_admin import ClerkAPIError
from app.services.deletion_service import ClerkDeleteFailed, delete_account


def test_happy_path() -> None:
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
            "app.services.deletion_service.delete_clerk_user"
        ) as mock_delete_clerk_user,
        patch(
            "app.services.deletion_service.send_account_deleted_email"
        ) as mock_send_account_deleted_email,
    ):
        delete_account(ctx, sr_client)

        mock_fetch_profile.assert_called_once_with(sr_client, "user_abc")
        mock_insert_audit_event.assert_called_once_with(
            sr_client, "user_abc", AuditEvent.REQUEST_INITIATED
        )
        mock_call_delete_user_data.assert_called_once_with(sr_client, "user_abc")
        mock_delete_clerk_user.assert_called_once_with("user_abc")
        mock_send_account_deleted_email.assert_called_once_with(
            "u@x.com", first_name="Jane Doe"
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
        patch(
            "app.services.deletion_service.send_account_deleted_email"
        ) as mock_send_account_deleted_email,
    ):
        delete_account(ctx, sr_client)

        mock_fetch_profile.assert_called_once_with(sr_client, "user_abc")
        mock_insert_audit_event.assert_called_once_with(
            sr_client, "user_abc", AuditEvent.REQUEST_INITIATED
        )
        mock_call_delete_user_data.assert_called_once_with(sr_client, "user_abc")
        mock_delete_clerk_user.assert_called_once_with("user_abc")
        mock_send_account_deleted_email.assert_not_called()


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
        patch(
            "app.services.deletion_service.send_account_deleted_email"
        ) as mock_send_account_deleted_email,
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
        mock_send_account_deleted_email.assert_not_called()
