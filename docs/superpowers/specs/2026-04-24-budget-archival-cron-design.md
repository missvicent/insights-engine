# Budget Archival Cron + Final AI Report — Design

**Status:** Draft
**Date:** 2026-04-24
**Branch:** `ft/cron-job`

## Problem

Budgets have a `start_date` and `end_date`, but no representation of "the period
is over." The `is_active` boolean today conflates two concepts: "user has
this budget enabled" and "this budget's period is current." Once `end_date`
passes, the budget should leave the active state but remain accessible as an
archive, with a final AI-generated summary of how the period played out.

## Goals

1. After a budget's `end_date` passes, transition it to an archived state
   automatically — without user action and without a request from the
   FastAPI app.
2. For each archived budget, generate a one-time final AI report covering
   the entire budget lifetime.
3. Persist that report so the frontend can display it on demand.
4. Survive transient failures (FastAPI down, AI provider rate-limited)
   without manual intervention.
5. Keep the existing architecture rules intact: engine stays pure, AI never
   sees raw transactions, JWT verification stays in `deps.py`/`jwks.py`.

## Non-goals

- Live regeneration of archive reports (YAGNI for v1 — cron self-heals on
  AI failure).
- User-facing "regenerate now" UX.
- Email/push notifications when a report is ready.
- Versioning of archive report shape.
- Per-user manual archival ("archive this budget now even though it hasn't
  ended").

## Decisions reached during brainstorming

| # | Decision | Rationale |
|---|---|---|
| 1 | State model is `is_active boolean` + new `archived_at timestamptz` | Keeps existing `is_active` semantics ("user-enabled"); `archived_at` is the idempotency anchor for the cron. |
| 2 | pg_cron triggers FastAPI via `pg_net.http_post` to a new internal endpoint | Reuses all existing engine/AI code; keeps Clerk as the only JWT verifier. |
| 3 | New `InsightWindow = "full"` covering `[budget.start_date, budget.end_date]` | Output stays an `InsightSummary` so the AI prompt is unchanged. |
| 4 | New `budget_archive_reports` table (one row per archived budget) | Write-once, queryable on its own, doesn't bloat `budgets`. |
| 5 | Hybrid order — SQL flips state first, then HTTP fan-out; daily sweep retries failures | State transition stays cheap and reliable; reports become eventually consistent. |
| 6 | On AI fallback, do not insert; cron retries tomorrow | Keeps the data model clean — every row is a real, AI-generated report. |
| 7 | Internal endpoint auth via `CRON_SHARED_SECRET` bearer header | Lowest friction; preserves the rule "JWT verification lives only in deps.py". |

## Architecture

```
                       ┌─────────────────────────────────────────┐
                       │ Supabase Postgres                       │
                       │                                         │
   ┌──────────────┐    │  pg_cron (daily 00:30 UTC)              │
   │  budgets     │◄───┤    1. UPDATE budgets                    │
   │    +         │    │       SET is_active=false,              │
   │ archived_at  │    │           archived_at=now()             │
   └──────────────┘    │       WHERE end_date < today            │
          ▲            │         AND archived_at IS NULL         │
          │            │                                         │
          │            │    2. SELECT budgets needing reports    │
          │            │       (archived_at IS NOT NULL          │
          │            │        AND no row in archive_reports)   │
          │            │                                         │
   ┌──────┴───────┐    │    3. for each → pg_net.http_post(      │
   │ budget_      │    │         '/internal/budgets/{id}         │
   │  archive_    │◄───┤          /archive-report',              │
   │  reports     │    │         header: Bearer <secret>)        │
   └──────────────┘    └────────────────────┬────────────────────┘
          ▲                                 │ HTTP
          │                                 ▼
          │                       ┌─────────────────────┐
          │                       │ FastAPI             │
          │                       │  POST /internal/... │
          └─── write report ──────┤  - verify secret    │
                                  │  - load budget      │
                                  │  - build_summary    │
                                  │     (window=full)   │
                                  │  - generate_ai      │
                                  │  - INSERT report    │
                                  │     (skip on fallback)
                                  └─────────────────────┘
```

### Components

- **Postgres schema:** `budgets.archived_at timestamptz` + new
  `budget_archive_reports` table.
- **pg_cron job:** single daily job; flips state for newly-ended budgets and
  fires HTTP for any archived budget without a stored report. Self-healing.
- **FastAPI internal endpoint:**
  `POST /internal/budgets/{budget_id}/archive-report`, gated by
  `CRON_SHARED_SECRET`. Computes the summary using the new `"full"` window,
  calls AI, inserts the row only on AI success.
- **FastAPI read endpoint:**
  `GET /budgets/{budget_id}/archive-report`, behind normal Clerk auth,
  returns the stored row.
- **Engine:** `InsightWindow` gains `"full"`. The internal endpoint passes
  the budget bounds straight to `build_summary` (does not route through
  `resolve_window`).

## Data model

### Migration 1 — add `archived_at` to `budgets`

```sql
ALTER TABLE budgets
  ADD COLUMN archived_at timestamptz NULL;

CREATE INDEX idx_budgets_archived_at_null
  ON budgets (end_date)
  WHERE archived_at IS NULL;
```

- `NULL` = not yet archived; `NOT NULL` = archived at that moment.
- Partial index keeps the cron's nightly scan cheap as the table grows —
  only un-archived rows are indexed, and we look them up by `end_date`.
- `is_active` keeps its existing meaning ("user-enabled"). On archival, the
  cron sets `is_active = false` as a side effect. From then on, archival
  state is `archived_at IS NOT NULL`; `is_active` is no longer the source
  of truth for "is this budget current."

### Migration 2 — new `budget_archive_reports` table

```sql
CREATE TABLE budget_archive_reports (
  budget_id    uuid PRIMARY KEY REFERENCES budgets(id) ON DELETE CASCADE,
  user_id      text NOT NULL,
  summary      jsonb NOT NULL,
  ai_report    jsonb NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE budget_archive_reports ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users read own archive reports"
  ON budget_archive_reports FOR SELECT
  USING (user_id = (auth.jwt() ->> 'sub'));
```

- `budget_id` is the PK — naturally enforces "one report per archived
  budget" and makes idempotent inserts trivial.
- `user_id` is denormalized so RLS matches `auth.jwt() ->> 'sub'` directly
  without a join. Stored as `text`, matching the project's Clerk-sub
  convention.
- `summary` and `ai_report` are JSONB — rendered to the user, never queried
  by field; schemas evolve via Pydantic.
- `ON DELETE CASCADE` — deleting a budget removes its report.
- No INSERT/UPDATE/DELETE policy → only the FastAPI internal endpoint
  (using the service role) ever writes.

## pg_cron job

### Setup (one-time, in Supabase SQL editor)

```sql
CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_net;

SELECT vault.create_secret('CRON_SHARED_SECRET',  '<long-random-string>');
SELECT vault.create_secret('INSIGHTS_API_BASE',   'https://api.insights-engine.example');
```

### The job

```sql
SELECT cron.schedule(
  'archive-ended-budgets',
  '30 0 * * *',           -- 00:30 UTC daily
  $$
  -- 1. flip newly-ended budgets to archived
  UPDATE budgets
     SET is_active  = false,
         archived_at = now()
   WHERE end_date < current_date
     AND archived_at IS NULL;

  -- 2. fire one HTTP request per budget needing a report
  --    (newly archived this run + any prior failures)
  WITH secrets AS (
    SELECT
      (SELECT decrypted_secret FROM vault.decrypted_secrets
        WHERE name='CRON_SHARED_SECRET') AS bearer,
      (SELECT decrypted_secret FROM vault.decrypted_secrets
        WHERE name='INSIGHTS_API_BASE') AS base_url
  ),
  to_report AS (
    SELECT b.id::text AS budget_id
      FROM budgets b
      LEFT JOIN budget_archive_reports r ON r.budget_id = b.id
     WHERE b.archived_at IS NOT NULL
       AND r.budget_id IS NULL
  )
  SELECT net.http_post(
    url     := secrets.base_url
                 || '/internal/budgets/'
                 || to_report.budget_id
                 || '/archive-report',
    headers := jsonb_build_object(
                 'Authorization', 'Bearer ' || secrets.bearer,
                 'Content-Type',  'application/json'
               ),
    body    := '{}'::jsonb,
    timeout_milliseconds := 30000
  )
  FROM secrets, to_report;
  $$
);
```

### Behavior notes

- **Order matters:** state flip happens before the HTTP fan-out. A budget
  archived in the same run is included in step 2.
- **Self-healing:** the `LEFT JOIN ... WHERE r.budget_id IS NULL` clause
  re-fires for any budget whose AI generation failed previously. No
  separate retry job needed.
- **Fan-out is fire-and-forget:** `pg_net.http_post` returns immediately;
  responses land in `net._http_response` for debugging. We don't block on
  them.
- **Backfill on first deployment:** the first run picks up every budget
  with `end_date < today AND archived_at IS NULL` — exactly the historical
  set we want archived.
- **Manually-deactivated budgets:** if a user sets `is_active = false`
  while `end_date` is in the future, the cron leaves them alone.
  `archived_at` is only set when `end_date < current_date`.
- **Timezone:** `end_date < current_date` uses the database's date
  (UTC under default Supabase config). A budget ending "April 23" gets
  archived on the cron's first run at or after 00:00 UTC on April 24.
  Acceptable — budgets are date-only, not datetime.

### Operational queries (manual debugging)

```sql
-- recent HTTP responses
SELECT id, status_code, error_msg, created
  FROM net._http_response
 WHERE created > now() - interval '24 hours'
 ORDER BY created DESC;

-- cron run history
SELECT * FROM cron.job_run_details
 WHERE jobname = 'archive-ended-budgets'
 ORDER BY start_time DESC LIMIT 10;
```

## FastAPI changes

### New file: `app/routes/archive.py`

Two endpoints, two auth paths.

#### `POST /internal/budgets/{budget_id}/archive-report`

```python
@router.post("/internal/budgets/{budget_id}/archive-report", status_code=201)
async def generate_archive_report(
    budget_id: str,
    _: Annotated[None, Depends(verify_cron_secret)],
) -> ArchiveReportGenerated:
    db = build_service_client()

    budget, allocations = fetch_budget_by_id(db, budget_id)
    if budget.archived_at is None:
        raise HTTPException(409, "budget is not archived")

    if archive_report_exists(db, budget_id):
        return ArchiveReportGenerated(budget_id=budget_id, status="already_exists")

    current = fetch_transactions_for_budget(
        db, budget.user_id, budget.start_date, budget.end_date, budget_id
    )
    goals = fetch_goals_for_user(db, budget.user_id)
    summary = build_summary(
        budget=budget,
        allocations=allocations,
        current=current,
        previous=[],
        goals=goals,
        window="full",
        window_start=budget.start_date,
        window_end=budget.end_date,
    )

    ai = await generate_ai_insights(summary)
    if ai == AI_FALLBACK:
        raise HTTPException(503, "ai unavailable; will retry next cron tick")

    insert_archive_report(db, budget_id, budget.user_id, summary, ai)
    return ArchiveReportGenerated(budget_id=budget_id, status="created")
```

- **Auth:** new `verify_cron_secret` dependency (in `deps.py`) checks
  `Authorization: Bearer <CRON_SHARED_SECRET>`. No Clerk JWT involved.
- **DB:** uses a service-role Supabase client (bypasses RLS) — service-to-
  service call, not a user request. Used **only** by `/internal/*` routes.
- **503-on-fallback** is informational only; pg_net does not retry on 5xx
  and we don't want it to. Our retry is the next daily cron tick.

#### `GET /budgets/{budget_id}/archive-report`

```python
@router.get("/budgets/{budget_id}/archive-report",
            responses={404: {"description": "Report not yet generated"}})
def get_archive_report(
    budget_id: str,
    ctx: Annotated[UserContext, Depends(get_user_ctx)],
) -> ArchiveReportResponse:
    row = fetch_archive_report(ctx, budget_id)
    if row is None:
        raise HTTPException(404, "archive report not found")
    return row
```

- **Auth:** existing `get_user_ctx` (Clerk JWT). RLS on
  `budget_archive_reports` enforces user ownership; the user-token client
  cannot see other users' reports.
- 404 covers all "row not visible" cases (not yours, not archived, not
  yet generated) without leaking which.

### `app/routes/deps.py` — new dependency

```python
def verify_cron_secret(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected = get_settings().cron_shared_secret
    if authorization != f"Bearer {expected}":
        raise HTTPException(401, "invalid cron secret")
```

### `app/db/client.py` — new helpers

- `build_service_client() -> Client` — service-role variant of
  `build_user_client`. Used only by `/internal/*`.
- `fetch_budget_by_id(db, budget_id)` — service-role; no `user_id` filter.
- `fetch_transactions_for_budget(db, user_id, start, end, budget_id)` —
  service-role variant.
- `fetch_goals_for_user(db, user_id)` — service-role variant.
- `archive_report_exists(db, budget_id) -> bool`.
- `insert_archive_report(db, budget_id, user_id, summary, ai) -> None` —
  serializes both pydantic models with `model_dump(mode='json')`.
- `fetch_archive_report(ctx, budget_id) -> ArchiveReportResponse | None` —
  user-context variant; relies on RLS.

### `app/models/schemas.py` — additions

```python
InsightWindow = Literal["7d", "15d", "30d", "3m", "6m", "12m", "full"]


class BudgetRow(BaseModel):
    # ... existing fields ...
    archived_at: Optional[datetime] = None


class ArchiveReportResponse(BaseModel):
    """GET /budgets/{id}/archive-report"""
    budget_id: str
    summary: InsightSummary
    ai: AIRecommendation
    generated_at: datetime


class ArchiveReportGenerated(BaseModel):
    """POST /internal/.../archive-report"""
    budget_id: str
    status: Literal["created", "already_exists"]
```

### `app/services/insights_engine.py` — minimal changes

- Add `"full": 0` to `_HORIZON_DAYS`. The horizon concept is meaningless for
  an archived budget (it's history, not forward-looking); `0` is the
  sentinel and the AI prompt for archive reports ignores it.
- Update `detect_category_spikes` to short-circuit when `previous` is
  empty: `if not previous: return []`. Without this, every current-period
  category produces a `new_category` anomaly (because `previous_cats` is
  `{}` and every `prev_total` is `0`), which would flood the archive
  report. With the guard, the function correctly emits zero comparison
  anomalies when there is no baseline.
- `_format_period_label` is **unchanged** — it already produces a correct
  date-range label (e.g. `"Mar 15 – Apr 14, 2026"`) from any `(start, end)`
  pair, so passing `budget.start_date` and `budget.end_date` works without
  modification. No `build_summary` branch needed.
- `resolve_window` is **unchanged** — the internal endpoint already has the
  budget bounds and bypasses it.
- `allowed_windows_for_period` is **unchanged** — `"full"` is intentionally
  not exposed via `/insights`. The archive report has its own dedicated
  endpoint.

### `app/main.py`

```python
from app.routes import archive as archive_routes
app.include_router(archive_routes.router)
```

### Settings — new env vars

Added to `Settings` in `app/db/client.py` and `.env.example`:

- `CRON_SHARED_SECRET` — long random string. Also stored in Supabase Vault.
- `SUPABASE_SERVICE_KEY` — Supabase service-role key. The existing
  `SUPABASE_ANON_KEY` stays for user-facing requests.

## Error handling

| Failure mode | Detection | Behavior |
|---|---|---|
| Budget already archived | `archived_at IS NOT NULL` filter on cron `UPDATE` | UPDATE no-ops. |
| HTTP request fails (FastAPI down, network) | `pg_net._http_response` non-2xx / null status | No row written. Tomorrow's cron's `LEFT JOIN` re-fires. |
| Internal endpoint receives bad secret | `verify_cron_secret` 401 | Operator sees 401 in `net._http_response`. No state change. |
| Budget not yet archived when endpoint runs | `archived_at IS NULL` check inside endpoint | 409. Defensive — shouldn't happen in normal flow. |
| Report row already exists | `archive_report_exists` check | 200 `{status: "already_exists"}`. Idempotent. |
| AI returns fallback | `ai == AI_FALLBACK` equality | 503 from endpoint, no insert. Retried tomorrow. |
| AI takes >30s | pg_net 30s timeout | pg_net logs timeout. If row landed server-side, tomorrow's `LEFT JOIN` excludes it; otherwise retried. |
| Budget has zero transactions | Engine handles (totals 0, breakdowns empty) | Report still generates. AI either describes "no activity" or returns fallback → 503 → retry. Acceptable v1. |

### Idempotency

- **State flip:** `WHERE archived_at IS NULL` makes the UPDATE a no-op on
  subsequent runs.
- **Report insertion:** `budget_id` is the PK; the existence check + PK
  guarantee no duplicates.
- **Whole cron:** running the job twice in a day produces the same result
  as running it once.

### Logging

- `verify_cron_secret` failures → WARNING with redacted header preview.
- 503-on-AI-fallback → WARNING (expected during AI provider outages, not
  an error).
- Insert failures (constraint violations) → ERROR with `budget_id`.
- Engine and `ai_service` keep existing logging.

## Testing

Following the project's `tests/test_*.py` convention. Engine logic is
prioritized; HTTP integration is light (the hard parts are pure functions).

### `tests/test_insights_engine.py` — extend

- `test_build_summary_full_window_uses_budget_bounds` — `period_label`
  formatted from `[start, end]` via the existing `_format_period_label`.
- `test_detect_category_spikes_empty_previous_returns_empty` — guard works:
  `detect_category_spikes(current=[...], previous=[])` returns `[]` instead
  of a `new_category` anomaly per category.
- `test_build_summary_full_window_no_previous_period` — `previous=[]`
  through `build_summary` → `expenses_change_pct is None`,
  `income_change_pct is None`, anomalies list contains no `new_category` /
  `category_removed` / `spike` entries.
- `test_horizon_for_full_window` — `_horizon_for_window("full")` returns
  `0`.

### `tests/test_archive_routes.py` — new

- `test_internal_endpoint_rejects_missing_secret` → 401.
- `test_internal_endpoint_rejects_wrong_secret` → 401.
- `test_internal_endpoint_409_on_unarchived_budget` → 409.
- `test_internal_endpoint_returns_already_exists_when_row_present` → 200
  `{status: "already_exists"}`.
- `test_internal_endpoint_503_on_ai_fallback` — monkeypatch
  `generate_ai_insights` to return `AI_FALLBACK` → 503, no row inserted.
- `test_internal_endpoint_inserts_on_success` — happy path; row in stub DB.
- `test_get_archive_report_returns_404_when_missing` → 404.
- `test_get_archive_report_returns_row_when_present` → 200 with full
  payload.

### `tests/test_archive_db.py` — new

- `insert_archive_report` serializes `InsightSummary` and
  `AIRecommendation` via `model_dump(mode='json')`.
- `archive_report_exists` returns `True` / `False` correctly.

### Out of scope for tests

- pg_cron / pg_net SQL — Supabase-side, not exercisable from pytest.
  Smoke-tested manually via the operational queries above.
- Live HTTP from Postgres → FastAPI — verified via a one-off `curl` with
  the shared secret during deployment.

## Documentation changes

- `.env.example` — add `CRON_SHARED_SECRET` and `SUPABASE_SERVICE_KEY`
  placeholders.
- `README.md` — short "Operations" section describing the cron, Vault
  setup, and recovery queries.
- `CLAUDE.md` — add: "Service-role Supabase client is used **only** in
  `/internal/*` routes; user-facing routes always use `build_user_client`."

## YAGNI'd for v1

- Manual regenerate endpoint.
- Per-user "regenerate now" UX.
- Notifications when an archive report is ready.
- Versioning of archive report shape.
- Special handling for budgets archived before this feature shipped — the
  cron's `LEFT JOIN` picks them up automatically on first run.

## Files touched (summary)

- `app/models/schemas.py` — `InsightWindow` adds `"full"`; `BudgetRow`
  gains `archived_at`; new `ArchiveReportResponse`,
  `ArchiveReportGenerated`.
- `app/db/client.py` — `Settings` gains two env vars; new service-role
  helpers and CRUD functions for the new table.
- `app/services/insights_engine.py` — one entry in `_HORIZON_DAYS`
  (`"full": 0`); one guard in `detect_category_spikes` for empty
  `previous`. `build_summary` and `_format_period_label` unchanged.
- `app/routes/archive.py` — new file (two endpoints).
- `app/routes/deps.py` — new `verify_cron_secret` dependency.
- `app/main.py` — register the new router.
- `tests/test_insights_engine.py` — three new tests.
- `tests/test_archive_routes.py` — new file.
- `tests/test_archive_db.py` — new file.
- `.env.example`, `README.md`, `CLAUDE.md` — docs.
- Supabase migrations — two SQL files (schema) + one SQL block (cron).
