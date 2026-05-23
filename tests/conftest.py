"""Session-wide pytest setup and shared test factories.

Two responsibilities:

1. Seed harmless defaults for every required Settings field BEFORE any
   `app.*` module is imported. `app.main` constructs `Settings()` at
   import time (for CORS configuration), so test modules that import
   `app.main` would fail collection if any required env var were unset.
   Doing this in `conftest.py` guarantees it runs before pytest imports
   any test module, regardless of collection order or invocation.
2. Provide the shared factory functions (`make_expense`, `make_income`,
   `make_allocation`, `make_budget`, `make_goal`), the fake-Supabase
   pair (`FakeQuery`, `FakeDB`), and `make_user_ctx` -- used across
   `test_insights_engine.py`, `test_insights_route.py`, and any future
   service-layer test that needs a `UserContext` with a stubbed DB.
"""

from __future__ import annotations

import os
import uuid
from datetime import date

os.environ.setdefault("CLERK_ISSUER", "https://test.clerk.test")
os.environ.setdefault("CLERK_SECRET_KEY", "sk_test")
os.environ.setdefault("CRON_SHARED_SECRET", "shh_test_secret")
os.environ.setdefault("RESEND_TEMPLATE_WELCOME", "tpl_welcome")
os.environ.setdefault("RESEND_TEMPLATE_ACCOUNT_DELETED", "tpl_deleted")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.test")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "srv_test")
os.environ.setdefault("CORS_ORIGINS", "")

from app.context import UserContext  # noqa: E402  -- must follow setdefault block
from app.models.schemas import (  # noqa: E402
    AllocationRow,
    BudgetRow,
    GoalRow,
    TransactionRow,
)


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def make_expense(
    amount: float = 10.0,
    *,
    category_id: str | None = "cat-g",
    category_name: str | None = "Groceries",
    category_icon: str | None = None,
    category_color: str | None = None,
    transaction_date: date = date(2026, 4, 1),
    merchant: str | None = None,
    description: str | None = None,
    id: str | None = None,
    user_id: str = "user-1",
) -> TransactionRow:
    return TransactionRow(
        id=id or _uid("tx"),
        user_id=user_id,
        category_id=category_id,
        amount=amount,
        transaction_date=transaction_date,
        type="expense",
        merchant=merchant,
        description=description,
        category_name=category_name,
        category_icon=category_icon,
        category_color=category_color,
    )


def make_income(
    amount: float = 1000.0,
    *,
    transaction_date: date = date(2026, 4, 1),
    id: str | None = None,
    user_id: str = "user-1",
) -> TransactionRow:
    return TransactionRow(
        id=id or _uid("tx"),
        user_id=user_id,
        category_id="cat-salary",
        amount=amount,
        transaction_date=transaction_date,
        type="income",
        category_name="Salary",
    )


def make_allocation(
    *,
    category_id: str = "cat-g",
    amount: float = 100.0,
    budget_id: str = "budget-1",
    id: str | None = None,
    alert_threshold: int = 80,
) -> AllocationRow:
    return AllocationRow(
        id=id or _uid("alloc"),
        budget_id=budget_id,
        category_id=category_id,
        amount=amount,
        alert_threshold=alert_threshold,
    )


def make_budget(
    *,
    start_date: date = date(2026, 4, 1),
    end_date: date = date(2026, 4, 30),
    amount: float = 5000.0,
    id: str | None = None,
    user_id: str = "user-1",
) -> BudgetRow:
    return BudgetRow(
        id=id or _uid("budget"),
        user_id=user_id,
        name="April 2026",
        period="monthly",
        amount=amount,
        start_date=start_date,
        end_date=end_date,
    )


def make_goal(
    *,
    name: str = "Emergency fund",
    target_amount: float = 1000.0,
    current_amount: float = 250.0,
    target_date: date | None = None,
    is_achieved: bool = False,
    id: str | None = None,
) -> GoalRow:
    return GoalRow(
        id=id or _uid("goal"),
        name=name,
        target_amount=target_amount,
        current_amount=current_amount,
        target_date=target_date,
        is_achieved=is_achieved,
    )


class FakeQuery:
    """Chainable no-op query; returns an object with `.data` when executed.

    Mirrors the subset of the supabase-py builder that db/client.py uses:
    select, eq, gte, lte, limit, execute. Filters are recorded but ignored;
    the caller seeds rows per (schema, table).
    """

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def select(self, *_a: object, **_kw: object) -> "FakeQuery":
        return self

    def eq(self, *_a: object, **_kw: object) -> "FakeQuery":
        return self

    def gte(self, *_a: object, **_kw: object) -> "FakeQuery":
        return self

    def lte(self, *_a: object, **_kw: object) -> "FakeQuery":
        return self

    def limit(self, *_a: object, **_kw: object) -> "FakeQuery":
        return self

    def order(self, *_a: object, **_kw: object) -> "FakeQuery":
        return self

    def execute(self) -> object:
        class _Resp:
            data = self._rows

        return _Resp()


class FakeDB:
    """Minimal stand-in for a Supabase client. `table(name)` returns a
    FakeQuery over whatever rows the test seeded for that table."""

    def __init__(self, tables: dict[str, list[dict]] | None = None) -> None:
        self._tables = tables or {}

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self._tables.get(name, []))


def make_user_ctx(
    user_id: str = "user-1", tables: dict[str, list[dict]] | None = None
) -> UserContext:
    return UserContext(user_id=user_id, db=FakeDB(tables))
