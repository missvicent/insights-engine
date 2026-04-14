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
    db: Client | None = None,
) -> list[TransactionRow]:
    if db is None:
        db = get_supabase()

    response = (
        db.table("transactions")
        .select("*, categories(name, icon, color)")
        .eq("user_id", user_id)
        .gte("transaction_date", start.isoformat())
        .lte("transaction_date", end.isoformat())
        .execute()
    )

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


def fetch_budgets(
    user_id: str,
    db: Client | None = None,
) -> tuple[list[BudgetRow], list[AllocationRow]]:

    if db is None:
        db = get_supabase()

    response = db.table("budgets").select("*").eq("user_id", user_id).execute()

    budgets = [BudgetRow(**row) for row in response.data]

    if not budgets:
        return [], []

    budget_ids = [b.id for b in budgets]

    alloc_response = (
        db.table("allocations")
        .select("*, categories(name)")
        .in_("budget_id", budget_ids)
        .execute()
    )

    allocations = []
    for alloc in alloc_response.data:
        cat = alloc.pop("categories", None) or {}
        allocations.append(AllocationRow(**alloc, category_name=cat.get("name")))

    return budgets, allocations


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
