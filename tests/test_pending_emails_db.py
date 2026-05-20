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


from app.db.client import claim_pending_email


class TestClaimPendingEmail:
    def test_returns_row_when_rpc_returns_one(self):
        client = MagicMock()
        rpc_execute = client.rpc.return_value.execute
        rpc_execute.return_value.data = [
            {
                "id": 42,
                "template": "welcome",
                "to_email": "a@x.com",
                "payload": {"first_name": "Alice"},
                "attempts": 0,
                "max_attempts": 8,
            }
        ]

        row = claim_pending_email(client, 42)

        assert row is not None
        assert row["id"] == 42
        assert row["template"] == "welcome"
        client.rpc.assert_called_once_with("claim_pending_email", {"p_id": 42})

    def test_returns_none_when_rpc_returns_empty(self):
        client = MagicMock()
        client.rpc.return_value.execute.return_value.data = []

        row = claim_pending_email(client, 42)

        assert row is None


from app.db.client import fetch_ready_pending_emails


class TestFetchReadyPendingEmails:
    def test_calls_rpc_with_limit(self):
        client = MagicMock()
        client.rpc.return_value.execute.return_value.data = [
            {
                "id": 1,
                "template": "welcome",
                "to_email": "a@x.com",
                "payload": {},
                "attempts": 0,
                "max_attempts": 8,
            },
            {
                "id": 2,
                "template": "account_deleted",
                "to_email": "b@x.com",
                "payload": {"first_name": "Bob"},
                "attempts": 3,
                "max_attempts": 8,
            },
        ]

        rows = fetch_ready_pending_emails(client, limit=50)

        assert len(rows) == 2
        assert [r["id"] for r in rows] == [1, 2]
        client.rpc.assert_called_once_with(
            "fetch_ready_pending_emails", {"p_limit": 50}
        )

    def test_returns_empty_when_no_rows(self):
        client = MagicMock()
        client.rpc.return_value.execute.return_value.data = []

        rows = fetch_ready_pending_emails(client, limit=50)

        assert rows == []


from datetime import datetime, timezone

from app.db.client import mark_pending_email_sent


class TestMarkPendingEmailSent:
    def test_updates_sent_at(self):
        client = MagicMock()
        eq = client.table.return_value.update.return_value.eq
        eq.return_value.execute.return_value.data = [{"id": 42}]

        before = datetime.now(timezone.utc)
        mark_pending_email_sent(client, 42)
        after = datetime.now(timezone.utc)

        client.table.assert_called_once_with("pending_emails")
        update_args = client.table.return_value.update.call_args
        assert list(update_args[0][0].keys()) == ["sent_at"]
        # Stored as ISO-8601 UTC string; parse it back to verify it's
        # between before and after.
        sent_at = datetime.fromisoformat(update_args[0][0]["sent_at"])
        assert before <= sent_at <= after

        eq.assert_called_once_with("id", 42)
