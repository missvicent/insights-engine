from unittest.mock import MagicMock

import pytest

from app.context import UserContext
from app.models.schemas import AuditEvent
from app.services.clerk_admin import ClerkAPIError
from app.services.deletion_service import delete_account, ClerkDeleteFailed


def _ctx(self, sr_client) -> UserContext:
    return UserContext(user_id="user_abc", db=sr_client)


def test_happy_path(self, monkeypatch) -> None:
    sr_client = MagicMock()
    ctx = _ctx(sr_client)
    