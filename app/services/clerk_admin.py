import logging
import time

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.0


class ClerkAPIError(Exception):
    """Non-recoverable error after retry budget exhausted."""


def delete_clerk_user(clerk_user_id: str) -> None:
    settings = get_settings()

    headers = {
        "Authorization": f"Bearer {settings.clerk_secret_key}",
        "Content-Type": "application/json",
    }
    url = f"https://api.clerk.com/v1/users/{clerk_user_id}"

    last_error: str | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = httpx.delete(url, headers=headers, timeout=15.0)
            if response.status_code in (200, 204):
                logger.info(
                    "Clerk delete OK user=%s attempt=%d", clerk_user_id[-4:], attempt
                )
                return
            if response.status_code == 404:
                logger.info(
                    "Clerk delete: user already absent user=%s", clerk_user_id[-4:]
                )
                return
            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                logger.warning(
                    "Clerk 5xx, will retry: user=%s attempt=%d %s",
                    clerk_user_id[-4:],
                    attempt,
                    last_error,
                )
            else:
                # 4xx other than 404 — non-retryable
                raise ClerkAPIError(
                    f"Clerk DELETE returned {response.status_code}: {response.text[:200]}"
                )
        except httpx.HTTPError as e:
            last_error = repr(e)
            logger.warning(
                "Clerk network error user=%s attempt=%d %s",
                clerk_user_id[-4:],
                attempt,
                last_error,
            )
        if attempt < MAX_RETRIES:
            time.sleep(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)))
    raise ClerkAPIError(f"Clerk DELETE exhausted retries: {last_error}")
