"""Session-wide pytest setup.

The only job here is to seed harmless defaults for every required Settings
field before any app module is imported. `app.main` constructs `Settings()`
at import time (for CORS configuration), so test modules that import
`app.main` would fail collection if any required env var is unset.

Putting these `os.environ.setdefault` calls in `tests/conftest.py` (rather
than inside a specific test module) guarantees they run before pytest
imports any test module, regardless of collection order or which file
pytest is invoked with.
"""

import os

os.environ.setdefault("CLERK_ISSUER", "https://test.clerk.test")
os.environ.setdefault("CLERK_SECRET_KEY", "sk_test")
os.environ.setdefault("CRON_SHARED_SECRET", "shh_test_secret")
os.environ.setdefault("RESEND_TEMPLATE_WELCOME", "tpl_welcome")
os.environ.setdefault("RESEND_TEMPLATE_ACCOUNT_DELETED", "tpl_deleted")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.test")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "srv_test")
os.environ.setdefault("CORS_ORIGINS", "")
