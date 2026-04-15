from datetime import date
from functools import lru_cache

from pydantic_settings import BaseSettings
from supabase import Client, create_client

from app.models.schemas import (
    AllocationRow,
    BudgetRow,
    DebtRow,
    GoalRow,
    RecurringRow,
    TransactionRow,
)


class Settings(BaseSettings):
    supabase_url: str
    supabase_service_key: str

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()


# service key bypasses RLS — safe for backend only, never expose to frontend
@lru_cache
def get_supabase() -> Client:
    s = get_settings()

    return create_client(s.supabase_url, s.supabase_service_key)


def fetch_transactions(
    user_id: str,
    start: date,
    end: date,
    budget_id: str | None = None,
    db: Client | None = None,
) -> list[TransactionRow]:
    """Fetch transactions for `user_id` between `start` and `end` (inclusive).

    When `budget_id` is provided, results are scoped to that budget.
    When `budget_id` is None, all of the user's transactions in the window
    are returned — retained for callers that don't yet operate per-budget.
    """
    if db is None:
        db = get_supabase()

    query = (
        db.table("transactions")
        .select("*, categories(name, icon, color)")
        .eq("user_id", user_id)
        .gte("transaction_date", start.isoformat())
        .lte("transaction_date", end.isoformat())
    )
    if budget_id is not None:
        query = query.eq("budget_id", budget_id)
    response = query.execute()

    rows = []

    for row in response.data:
        cat = row.pop("categories", None) or {}
        rows.append(
            TransactionRow(
                **row,
                category_name=cat.get("name"),
                category_icon=cat.get("icon"),
                category_color=cat.get("color"),
            )
        )

    return rows


class BudgetNotFound(Exception):
    """Raised when a budget_id does not exist or is not owned by the user."""


def fetch_budget(
    user_id: str,
    budget_id: str,
    db: Client | None = None,
) -> tuple[BudgetRow, list[AllocationRow]]:
    """Fetch one budget (authorized to user_id) and its allocations.

    Raises BudgetNotFound when the row is missing or not owned by the user.
    """
    if db is None:
        db = get_supabase()

    budget_response = (
        db.table("budgets")
        .select("*")
        .eq("id", budget_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )

    if not budget_response.data:
        raise BudgetNotFound(budget_id)

    budget = BudgetRow(**budget_response.data[0])

    alloc_response = (
        db.table("allocations")
        .select("*, categories(name)")
        .eq("budget_id", budget.id)
        .execute()
    )

    allocations: list[AllocationRow] = []
    for alloc in alloc_response.data:
        cat = alloc.pop("categories", None) or {}
        allocations.append(AllocationRow(**alloc, category_name=cat.get("name")))

    return budget, allocations


def fetch_goals(
    user_id: str,
    db: Client | None = None,
) -> list[GoalRow]:
    if db is None:
        db = get_supabase()

    response = (
        db.table("goals")
        .select("id, name, target_amount, current_amount, target_date, is_achieved")
        .eq("user_id", user_id)
        .eq("is_achieved", False)
        .execute()
    )

    return [GoalRow(**row) for row in response.data]


def fetch_debt(
    user_id: str,
    db: Client | None = None,
) -> list[DebtRow]:
    if db is None:
        db = get_supabase()

    response = (
        db.table("debts")
        .select(
            "id, name, type, current_balance, interest_rate, minimum_payment, is_active"
        )
        .eq("user_id", user_id)
        .execute()
    )

    return [DebtRow(**row) for row in response.data]


def fetch_recurring(
    user_id: str,
    db: Client | None = None,
) -> list[RecurringRow]:
    if db is None:
        db = get_supabase()

    response = (
        db.table("recurring_transactions")
        .select(
            "id, name, type, amount, frequency, next_occurrence, is_active, is_paused"
        )
        .eq("user_id", user_id)
        .eq("is_active", True)
        .execute()
    )

    return [RecurringRow(**row) for row in response.data]
