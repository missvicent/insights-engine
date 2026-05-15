import logging

import resend

from app.config import get_settings

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


def send_welcome_email(to: str, first_name: str | None = None) -> bool:
    """Send the welcome email via Resend.

    `resend.api_key` is set once in the FastAPI lifespan (see app/main.py),
    so this function only needs the `from` address from settings.
    """
    settings = get_settings()
    return _send(to, settings.resend_template_welcome, {"USER": first_name})


def send_account_deleted_email(to: str, first_name: str | None = None) -> bool:
    """Send the account deleted email via Resend.

    `resend.api_key` is set once in the FastAPI lifespan (see app/main.py),
    so this function only needs the `from` address from settings.
    """
    settings = get_settings()
    return _send(to, settings.resend_template_account_deleted, {"USER": first_name})
