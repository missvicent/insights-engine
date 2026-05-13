import httpx

from unittest.mock import patch

from app.services.clerk_admin import delete_clerk_user


class TestDeleteClerkUser:
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
    

### 5xx ###
#    def test_5xx_exhausted_raises(self):
#        pass
    
#   def test_4xx_raises_immediately(self):
        pass

    def test_404_is_treated_as_success(self):
        pass