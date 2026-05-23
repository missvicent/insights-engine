"""Tests for required-env-var enforcement in `app.config.Settings`.

Each test re-seeds the env via `monkeypatch` and clears the `Settings`
cache so it reads the per-test environment cleanly. These are the only
tests that exercise Settings boot-time validation; everything else
relies on the defaults seeded in `tests/conftest.py`.
"""

import pytest
from pydantic import ValidationError


def test_cron_shared_secret_is_required(monkeypatch):
    """CRON_SHARED_SECRET is required at boot — missing value fails."""
    from app.config import Settings, get_settings

    # Provide every other required field except CRON_SHARED_SECRET.
    monkeypatch.setenv("CLERK_ISSUER", "https://test.clerk.test")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test")
    monkeypatch.setenv("RESEND_TEMPLATE_WELCOME", "tpl_welcome")
    monkeypatch.setenv("RESEND_TEMPLATE_ACCOUNT_DELETED", "tpl_deleted")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_k")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srv_k")
    monkeypatch.delenv("CRON_SHARED_SECRET", raising=False)
    get_settings.cache_clear()

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_cron_shared_secret_loads(monkeypatch):
    from app.config import Settings, get_settings

    monkeypatch.setenv("CLERK_ISSUER", "https://test.clerk.test")
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test")
    monkeypatch.setenv("RESEND_TEMPLATE_WELCOME", "tpl_welcome")
    monkeypatch.setenv("RESEND_TEMPLATE_ACCOUNT_DELETED", "tpl_deleted")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.test")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon_k")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "srv_k")
    monkeypatch.setenv("CRON_SHARED_SECRET", "shh_local_dev_secret")
    get_settings.cache_clear()

    s = Settings(_env_file=None)
    assert s.cron_shared_secret == "shh_local_dev_secret"
