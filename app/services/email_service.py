import logging

import resend

from app.config import get_settings

logger = logging.getLogger(__name__)


def send_welcome_email(to: str, first_name: str | None = None) -> bool:
    """Send the welcome email via Resend.

    `resend.api_key` is set once in the FastAPI lifespan (see app/main.py),
    so this function only needs the `from` address from settings.
    """
    settings = get_settings()
    logger.info("Sending welcome email to %s (first_name=%s)", to, first_name)
    try:
        response = resend.Emails.send(
            {
                "from": settings.resend_from_email,
                "to": [to],
                "template": {
                    "id": "welcome-personal-budget",
                    "variables": {
                        "USER": first_name,
                    },
                },
            }
        )
        logger.info("Resend accepted email to %s: %s", to, response)
        return True
    except Exception:
        logger.exception("Failed to send welcome email to %s", to)
        return False
