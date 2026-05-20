import logging

import resend

from app.config import get_settings
from app.db.client import (
    build_service_role_client,
    claim_pending_email,
    mark_pending_email_failed,
    mark_pending_email_sent,
)

logger = logging.getLogger(__name__)


def _send(to: str, template_id: str, variables: dict[str, str]) -> bool:
    settings = get_settings()
    logger.info("Sending email to %s (template_id=%s)", to, template_id)
    try:
        response = resend.Emails.send(
            {
                "from": settings.resend_from_email,
                "to": [to],
                "template": {
                    "id": template_id,
                    "variables": variables,
                },
            }
        )
        logger.info("Resend accepted email to %s: %s", to, response)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to)
        return False


def _template_id_for(template: str) -> str | None:
    s = get_settings()
    if template == "welcome":
        return s.resend_template_welcome
    if template == "account_deleted":
        return s.resend_template_account_deleted
    return None


def try_send_pending_email(row_id: int) -> bool:
    """Attempt to send one pending_emails row.

    Used by both the fast-path BackgroundTask (queued by the producer)
    and the cron-driven flush worker. Claims the row via SKIP LOCKED;
    if another caller already holds the row, returns False without
    side effects.
    """
    sr_client = build_service_role_client()
    row = claim_pending_email(sr_client, row_id)
    if row is None:
        return False

    template_id = _template_id_for(row["template"])
    if template_id is None:
        logger.error(
            "Unknown pending_emails.template=%s on row id=%s",
            row["template"],
            row_id,
        )
        mark_pending_email_failed(
            sr_client, row_id, f"unknown template: {row['template']}"
        )
        return False

    first_name = (row.get("payload") or {}).get("first_name")
    ok = _send(row["to_email"], template_id, {"USER": first_name})
    if ok:
        mark_pending_email_sent(sr_client, row_id)
        return True
    mark_pending_email_failed(sr_client, row_id, "resend rejected")
    return False


# Kept as a thin convenience for any caller that already has the
# email + first_name in hand and just wants to enqueue+send synchronously.
# Producers should prefer enqueue_email + try_send_pending_email.
def send_welcome_email(to: str, first_name: str | None = None) -> bool:
    settings = get_settings()
    return _send(to, settings.resend_template_welcome, {"USER": first_name})


def send_account_deleted_email(to: str, first_name: str | None = None) -> bool:
    settings = get_settings()
    return _send(to, settings.resend_template_account_deleted, {"USER": first_name})
