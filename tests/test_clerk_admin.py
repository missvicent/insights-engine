from unittest.mock import patch

import httpx
import pytest

from app.config import get_settings
from app.services.clerk_admin import ClerkAPIError, delete_clerk_user


class TestDeleteClerkUser:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test")
        monkeypatch.setenv("RESEND_TEMPLATE_WELCOME", "tpl_welcome")
        monkeypatch.setenv("RESEND_TEMPLATE_ACCOUNT_DELETED", "tpl_deleted")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srv_x")
        get_settings.cache_clear()  # if it's @lru_cache'd

    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        # Skip the 1s + 2s exponential backoff so retry tests don't add
        # ~3s of real wall time each.
        monkeypatch.setattr("app.services.clerk_admin.time.sleep", lambda _: None)

    def test_retry_on_5xx(self):
        responses = [
            httpx.Response(status_code=500, json={"error": "Internal Server Error"}),
            httpx.Response(status_code=500, json={"error": "Internal Server Error"}),
            httpx.Response(status_code=204, json={"message": "User deleted"}),
        ]

        with patch("app.services.clerk_admin.httpx.delete") as mock_delete:
            mock_delete.side_effect = responses

            result = delete_clerk_user("user_123")

            assert result is None
            assert mock_delete.call_count == 3

    def test_5xx_exhausted_raises(self):
        responses = [
            httpx.Response(status_code=500, json={"error": "Internal Server Error"}),
            httpx.Response(status_code=500, json={"error": "Internal Server Error"}),
            httpx.Response(status_code=500, json={"error": "Internal Server Error"}),
        ]

        with patch("app.services.clerk_admin.httpx.delete") as mock_delete:
            mock_delete.side_effect = responses

            with pytest.raises(ClerkAPIError):
                delete_clerk_user("user_123")

            assert mock_delete.call_count == 3

    def test_4xx_raises_immediately(self):
        responses = [
            httpx.Response(status_code=400, json={"error": "Bad Request"}),
        ]

        with patch("app.services.clerk_admin.httpx.delete") as mock_delete:
            mock_delete.side_effect = responses

            with pytest.raises(ClerkAPIError):
                delete_clerk_user("user_123")

            assert mock_delete.call_count == 1

    def test_404_is_treated_as_success(self):
        responses = [
            httpx.Response(
                status_code=404,
                json={
                    "errors": [
                        {
                            "code": "resource_not_found",
                            "message": "Not Found",
                            "long_message": "User not found",
                        }
                    ]
                },
            ),
        ]

        with patch("app.services.clerk_admin.httpx.delete") as mock_delete:
            mock_delete.side_effect = responses

            result = delete_clerk_user("user_123")

            assert result is None
            assert mock_delete.call_count == 1
