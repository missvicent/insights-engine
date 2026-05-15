import hashlib
from datetime import date

from supabase import Client, create_client

from app.config import get_settings
from app.context import UserContext
from app.models.schemas import (
    AllocationRow,
    AuditEvent,
    BudgetRow,
    DebtRow,
    GoalRow,
    RecurringRow,
    TransactionRow,
)

# PostgREST defaults to a 1000-row ceiling silently; we set our own explicit
# upper bound so a runaway window surfaces as truncation rather than as a
# half-correct insight summary.
TRANSACTIONS_FETCH_LIMIT = 10_000


def build_user_client(access_token: str) -> Client:
    """Build a per-request Supabase client authenticated as the end user.

    The anon key admits the request to PostgREST (no grants on its own —
    RLS is enabled on every user-owned table). The Clerk JWT is attached
    via postgrest.auth so Supabase's Third-Party Auth verifier populates
    auth.jwt(); RLS policies then enforce access by comparing
    user_id = (auth.jwt() ->> 'sub') — stored as text, not UUID, because
    Clerk subs (e.g. user_33IZ...) aren't UUID-shaped.
    """
    s = get_settings()
    client = create_client(s.supabase_url, s.supabase_anon_key)
    client.postgrest.auth(access_token)
    return client


def ping() -> bool:
    """Anon-key reachability probe for PostgREST.

    Issues a zero-row select against `categories` (a system-only table
    with no per-user RLS dependency) to verify both the HTTP path to
    PostgREST and that its schema cache has loaded. Raises on failure
    so the caller can map the exception to a 503.
    """
    s = get_settings()
    client = create_client(s.supabase_url, s.supabase_anon_key)
    client.table("categories").select("id").limit(1).execute()
    return True


def fetch_transactions(
    ctx: UserContext,
    start: date,
    end: date,
    budget_id: str | None = None,
) -> list[TransactionRow]:
    """Fetch transactions for `ctx.user_id` between `start` and `end` (inclusive).

    When `budget_id` is provided, results are scoped to that budget.
    RLS enforces ownership; the explicit user_id filter is belt-and-suspenders.
    """
    query = (
        ctx.db.table("transactions")
        .select("*, categories(name, icon, color)")
        .eq("user_id", ctx.user_id)
        .gte("transaction_date", start.isoformat())
        .lte("transaction_date", end.isoformat())
        .order("transaction_date")
        .limit(TRANSACTIONS_FETCH_LIMIT)
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
    ctx: UserContext,
    budget_id: str,
) -> tuple[BudgetRow, list[AllocationRow]]:
    """Fetch one budget (authorized to ctx.user_id) and its allocations.

    Raises BudgetNotFound when the row is missing or not owned by the user.
    """
    budget_response = (
        ctx.db.table("budgets")
        .select("*")
        .eq("id", budget_id)
        .eq("user_id", ctx.user_id)
        .limit(1)
        .execute()
    )

    if not budget_response.data:
        raise BudgetNotFound(budget_id)

    budget = BudgetRow(**budget_response.data[0])

    alloc_response = (
        ctx.db.table("allocations")
        .select("*, categories(name)")
        .eq("budget_id", budget.id)
        .execute()
    )

    allocations: list[AllocationRow] = []
    for alloc in alloc_response.data:
        cat = alloc.pop("categories", None) or {}
        allocations.append(AllocationRow(**alloc, category_name=cat.get("name")))

    return budget, allocations


def fetch_goals(ctx: UserContext) -> list[GoalRow]:
    response = (
        ctx.db.table("goals")
        .select("id, name, target_amount, current_amount, target_date, is_achieved")
        .eq("user_id", ctx.user_id)
        .eq("is_achieved", False)
        .execute()
    )
    return [GoalRow(**row) for row in response.data]


def fetch_debt(ctx: UserContext) -> list[DebtRow]:
    response = (
        ctx.db.table("debts")
        .select(
            "id, name, type, current_balance, interest_rate, minimum_payment, is_active"
        )
        .eq("user_id", ctx.user_id)
        .execute()
    )
    return [DebtRow(**row) for row in response.data]


def fetch_recurring(ctx: UserContext) -> list[RecurringRow]:
    response = (
        ctx.db.table("recurring_transactions")
        .select(
            "id, name, type, amount, frequency, next_occurrence, is_active, is_paused"
        )
        .eq("user_id", ctx.user_id)
        .eq("is_active", True)
        .execute()
    )
    return [RecurringRow(**row) for row in response.data]


def build_service_role_client() -> Client:
    """Build a client authenticated as the service role.

    RLS is disabled for this client. Use it ONLY in code paths where the
    request itself authenticates via another mechanism (e.g. Svix-signed
    webhooks) and we therefore have no JWT to attach.
    """
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_service_role_key)


def fetch_profile_for_deletion(
    client: Client, user_id: str
) -> tuple[str, str | None] | None:
    """Look up `(email, full_name)` for the deletion confirmation email.

    Returns None when no profile exists for `user_id` — the deletion
    service treats that as "nothing to email" and proceeds. The
    `profiles` table stores `full_name`, not `first_name`; the email
    layer passes whatever we return through the template's `USER` token.
    """
    response = (
        client.table("profiles")
        .select("email, full_name")
        .eq("clerk_user_id", user_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    row = response.data[0]
    return row["email"], row.get("full_name")


def profile_exists(client: Client, user_id: str) -> bool:
    """Check if the user's profile exists in the profiles table."""
    response = (
        client.table("profiles")
        .select("clerk_user_id")
        .eq("clerk_user_id", user_id)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def insert_audit_event(
    client: Client,
    user_id: str,
    event: AuditEvent,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Insert into account_deletion_audit. user_id is sha256'd before
    storage so the table never holds raw Clerk subs. metadata MUST NOT
    contain email, IP, name, or raw user_id (per spec)."""
    client.table("account_deletion_audit").insert(
        {
            "user_id_hash": hashlib.sha256(user_id.encode()).hexdigest(),
            "event": event.value,
            "metadata": metadata,
        }
    ).execute()


def record_webhook_event(client: Client, svix_id: str) -> bool:
    """Record a webhook event for idempotency."""
    try:
        response = (
            client.table("webhook_events")
            .insert(
                {
                    "svix_id": svix_id,
                }
            )
            .execute()
        )
        return bool(response.data)
    except Exception as e:
        if getattr(e, "code", None) == "23505" or "23505" in str(e):
            return False
        raise


def call_delete_user_data(client: Client, user_id: str) -> None:
    """Trigger the SQL deletion. Returns void; success = no exception."""
    client.rpc("delete_user_data", {"p_clerk_user_id": user_id}).execute()
