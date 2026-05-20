"""Unit tests for the pending_emails db/client helpers."""

from unittest.mock import MagicMock

from app.db.client import enqueue_email


class TestEnqueueEmail:
    def test_inserts_with_defaults_and_returns_id(self):
        client = MagicMock()
        # supabase-py: client.table(...).insert(...).execute().data
        execute = client.table.return_value.insert.return_value.execute
        execute.return_value.data = [{"id": 42}]

        row_id = enqueue_email(
            client,
            template="welcome",
            to_email="alice@example.com",
            payload={"first_name": "Alice"},
        )

        assert row_id == 42
        client.table.assert_called_once_with("pending_emails")
        client.table.return_value.insert.assert_called_once_with(
            {
                "template": "welcome",
                "to_email": "alice@example.com",
                "payload": {"first_name": "Alice"},
            }
        )

    def test_payload_defaults_to_empty_dict(self):
        client = MagicMock()
        execute = client.table.return_value.insert.return_value.execute
        execute.return_value.data = [{"id": 7}]

        enqueue_email(client, template="account_deleted", to_email="b@x.com")

        client.table.return_value.insert.assert_called_once_with(
            {
                "template": "account_deleted",
                "to_email": "b@x.com",
                "payload": {},
            }
        )
