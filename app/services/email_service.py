import logging
import resend
from app.db.client import get_settings

logger = logging.getLogger(__name__)

def send_welcome_email(to: str, first_name: str | None = None) -> bool:
    settings = get_settings()
    resend.api_key = settings.resend_api_key
    try:
        resend.Emails.send({
            "from": settings.resend_from_email,
            "to": [to],
            "template": {
                "id": "welcome-personal-budget",
                "variables": {
                    "USER": first_name,
                },
            },
        })
        return True
    except Exception as e:
        logger.error("Failed to send welcome email to %s: %s", to, e)
        return False
