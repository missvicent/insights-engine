import logging

from supabase.client import Client

from app.context import UserContext
from app.db.client import (
    call_delete_user_data,
    enqueue_email,
    fetch_profile_for_deletion,
    insert_audit_event,
)
from app.models.schemas import AuditEvent
from app.services.clerk_admin import ClerkAPIError, delete_clerk_user

logger = logging.getLogger(__name__)


class ClerkDeleteFailed(Exception):
    """Raised when the Clerk user deletion fails."""


def delete_account(user_ctx: UserContext, sr_client: Client) -> int | None:
    """Wipe the user's data, delete them from Clerk, enqueue the
    confirmation email. Returns the pending_emails row id so the route
    can queue a fast-path BackgroundTask; returns None when no profile
    row exists (no email to send).
    """
    user_id = user_ctx.user_id
    profile = fetch_profile_for_deletion(sr_client, user_id)
    if not profile:
        logger.warning("delete_account: no profile row for user_id=%s", user_id)

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

    if email is None:
        return None
    return enqueue_email(
        sr_client,
        template="account_deleted",
        to_email=email,
        payload={"first_name": full_name},
    )
