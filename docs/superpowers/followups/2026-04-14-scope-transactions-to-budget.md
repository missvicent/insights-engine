---
status: open
severity: important
resolved_date:
resolved_by:
---

# Follow-up: Scope transactions to the selected budget

**Severity:** Important — correctness gap in the multi-budget case.

## Problem

`fetch_transactions(user_id, start, end)` has no `budget_id` filter. When a user owns multiple concurrent budgets (the exact case this feature was built for), `GET /insights?budget_id=april-monthly&window=3m` pulls **every** transaction for that user inside the window — regardless of which budget each transaction belongs to.

Downstream:
- `category_breakdown` sums the merged set.
- Anomaly detectors run against the merged set.
- Meanwhile `allocations` ARE correctly scoped to the selected budget.

Net effect: `summary.total_expenses` can exceed the selected budget's total because it includes transactions tied to other budgets. The spec's stated principle — "no silent allocation merging" — fails on the transaction side.

## Relevant code

- `app/db/client.py` — `fetch_transactions` (~line 39).
- `app/routes/insights.py:25–29` — two `fetch_transactions(user_id, …)` calls.
- `app/routes/ai.py` — same two calls.
- `app/models/schemas.py:28` — `TransactionRow.budget_id: Optional[str]` already exists.

## Proposed fix

1. Extend `fetch_transactions`:
   ```python
   def fetch_transactions(
       user_id: str,
       start: date,
       end: date,
       budget_id: str | None = None,
       db: Client | None = None,
   ) -> list[TransactionRow]:
       q = (
           db.table("transactions")
           .select("*, categories(name, icon, color)")
           .eq("user_id", user_id)
           .gte("transaction_date", start.isoformat())
           .lte("transaction_date", end.isoformat())
       )
       if budget_id is not None:
           q = q.eq("budget_id", budget_id)
       response = q.execute()
       ...
   ```
2. Route passes `q.budget_id` to both `fetch_transactions` calls.
3. Document the semantics: `budget_id=None` keeps the old "all user transactions" behaviour for any other caller; the insights routes always pass it.

## Verification

- Seed two budgets for the same user, each with its own allocations and transactions across the same window.
- Hit `GET /insights?budget_id=<first>` and `GET /insights?budget_id=<second>`.
- Assert `total_expenses` for each matches only the transactions tied to that budget.
