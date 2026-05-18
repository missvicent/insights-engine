from supabase.client import Client

from app.context import UserContext
from app.db.client import (
    call_delete_user_data,
    fetch_profile_for_deletion,
    insert_audit_event,
)
from app.models.schemas import AuditEvent
from app.services.clerk_admin import ClerkAPIError, delete_clerk_user
from app.services.email_service import send_account_deleted_email


class ClerkDeleteFailed(Exception):
    """Raised when the Clerk user deletion fails."""


def delete_account(user_ctx: UserContext, sr_client: Client) -> None:
    user_id = user_ctx.user_id
    profile = fetch_profile_for_deletion(sr_client, user_id)
    email, full_name = profile if profile else (None, None)

    insert_audit_event(sr_client, user_id, AuditEvent.REQUEST_INITIATED)
    call_delete_user_data(sr_client, user_id)

    try:
        delete_clerk_user(user_id)
    except ClerkAPIError as e:
        insert_audit_event(
            sr_client, user_id, AuditEvent.CLERK_DELETE_FAILED, {"error": str(e)}
        )
        raise ClerkDeleteFailed(str(e)) from e

    if email:
        send_account_deleted_email(email, first_name=full_name)
