# Migrations Baseline Squash — Design

**Date:** 2026-05-07
**Status:** Approved
**Author:** brainstorm with nily

## Problem

`supabase/migrations/` does not match the remote DB. Of 17 tables in remote `public`, only 2 (`debts`, `debt_payments`) are created by a migration file. The other 15 — including the foundational `budgets`, `categories`, `transactions`, `accounts`, `allocations`, `goals`, `recurring_transactions`, `service_templates`, `profiles`, `user_settings`, `webhook_events`, `account_deletion_requests`, `account_deletion_audit`, `budget_archive_reports`, `_backup_views` — were created directly on the remote (Dashboard / ad-hoc SQL) and never captured.

Consequences observed:

- `supabase start` / `supabase db reset` fails on the very first migration (`20260110044241_get_budgets_with_progress.sql`) with `relation "budgets" does not exist`.
- `supabase db pull` fails for the same reason — its shadow DB cannot apply the existing migrations.
- A teammate / CI / fresh laptop cannot rebuild the schema from this repo.

Root cause: the migration history is a list of *changes* on top of an unwritten baseline.

## Goal

Squash everything into a single new initial migration that captures the current remote schema verbatim, so any empty Postgres can be brought up to match production by running the migrations folder.

## Decisions (from brainstorm)

| # | Decision | Rationale |
| --- | --- | --- |
| 1 | Source of truth: `supabase db dump --schema public` against remote | Only option that captures dashboard-created RLS, indexes, triggers, grants. Hand-writing from CREATE TABLE dump silently loses them. |
| 2 | Baseline includes `account_deletion_requests` + `account_deletion_audit` | Snapshot current remote as-is. Removal of those tables is sequenced as a separate follow-on migration (`20260508025626_*.sql`) to preserve audit trail and avoid coupling unrelated work. |
| 3 | Reference data → `supabase/seed.sql` (not inlined in baseline) | Keeps schema and data separate. `seed.sql` runs automatically after migrations on `db reset`. Idempotent via `ON CONFLICT DO NOTHING`. |
| 4 | Delete the 12 existing migration files; keep the empty `20260508025626_*.sql` | The 12 are fully captured by the baseline. Git log preserves their history. The empty future migration represents pending work and stays. |
| 5 | Reconcile remote with `supabase migration repair` (bookkeeping only) | Edits `supabase_migrations.schema_migrations` only. Zero DDL on `public.*`. Production schema and data untouched. The alternative (`db reset --linked`) would wipe production data. |
| 6 | Exclude `_backup_views` from baseline | One-off operational backup, not part of the application. Stays on remote untouched; not recreated locally. |

## End State

```text
supabase/
├── config.toml                                   (unchanged)
├── seed.sql                                      NEW
└── migrations/
    ├── 00000000000000_initial_schema.sql        NEW (baseline)
    └── 20260508025626_remove_account_deletion_request.sql  KEPT (still empty; future work)
```

Deleted from `supabase/migrations/`:

- `20260110044241_get_budgets_with_progress.sql`
- `20260313_create_debt_tables.sql`
- `20260321_add_budget_id_to_transactions.sql`
- `20260322_rewrite_get_budgets_with_progress.sql`
- `20260323_add_amount_to_budgets.sql`
- `20260324120000_electric_user_grants.sql`
- `20260324130000_get_budgets_overview.sql`
- `20260325_fix_budget_rpc_user_id.sql`
- `20260330_auto_assign_budget_trigger.sql`
- `20260401223244_filter_transactions_by_budget.sql`
- `20260402_rename_budget_items_to_allocations.sql`
- `20260409205443_savings_goals_integration.sql`

Filename `00000000000000_initial_schema.sql` chosen so it sorts before any real timestamp.

## Pipeline

### Step 1 — Dump

```bash
supabase stop
supabase db dump --schema public --file supabase/migrations/00000000000000_initial_schema.sql
supabase db dump --data-only --schema public --file /tmp/remote_data.sql
```

`supabase db dump` runs `pg_dump` against the linked remote and prompts for the DB password. Output captures: tables, columns, defaults, CHECK constraints, sequences, indexes, foreign keys, functions, triggers, RLS policies, grants.

### Step 2 — Clean baseline file

Manual but small. Remove from the dump:

| Pattern | Reason |
| --- | --- |
| `SET` statements at the top (`SET search_path`, `SET lock_timeout`, etc.) | Boilerplate; defaults are fine |
| `OWNER TO postgres` lines | Local stack uses different roles; harmless but noisy |
| Anything referencing `_backup_views` (the table, related grants/policies) | Excluded per decision 6 |
| `CREATE SCHEMA public` (if present) | Already exists |
| `CREATE SCHEMA auth` / `CREATE SCHEMA extensions` (if present) | Supabase manages these |

Keep:

- FK references to `auth.users` (e.g., `categories.user_id_fkey`) — `auth.users` exists in the local stack.
- `auth.jwt()` calls in column defaults — same reason.
- `electric_user` grants. Add a comment noting the role itself is created via Dashboard and must be manually created for local development.

### Step 3 — Build seed.sql

From `/tmp/remote_data.sql`, extract `INSERT` / `COPY` blocks for:

- `service_templates` — all rows
- `categories` — `WHERE is_system = true` only

Wrap each block so re-running is safe:

- Convert `COPY` to multi-row `INSERT … ON CONFLICT (id) DO NOTHING` if needed
- Add `BEGIN; … COMMIT;` per table

Save to `supabase/seed.sql`.

### Step 4 — Delete old migrations

```bash
git rm supabase/migrations/20260110044241_*.sql \
       supabase/migrations/20260313_*.sql \
       supabase/migrations/20260321_*.sql \
       supabase/migrations/20260322_*.sql \
       supabase/migrations/20260323_*.sql \
       supabase/migrations/20260324120000_*.sql \
       supabase/migrations/20260324130000_*.sql \
       supabase/migrations/20260325_*.sql \
       supabase/migrations/20260330_*.sql \
       supabase/migrations/20260401223244_*.sql \
       supabase/migrations/20260402_*.sql \
       supabase/migrations/20260409205443_*.sql
```

Keep `20260508025626_remove_account_deletion_request.sql` (empty placeholder for follow-on work).

### Step 5 — Reconcile remote bookkeeping

```bash
for ts in 20260110044241 20260313 20260321 20260322 20260323 \
          20260324120000 20260324130000 20260325 20260330 \
          20260401223244 20260402 20260409205443; do
  supabase migration repair --status reverted "$ts"
done

supabase migration repair --status applied 00000000000000
```

Each `--status reverted` deletes one row from `supabase_migrations.schema_migrations`. The final `--status applied` inserts one row for the new baseline. **No DDL** runs on `public.*`. Production schema and data are unchanged.

## Verification

Three checks, in order. Each must pass before the squash is considered complete.

### Check 1 — Local rebuilds cleanly

```bash
supabase start
supabase db reset
```

Must complete with no errors. Catches `pg_dump` ordering bugs (FK before referenced table, function before its dependency). Fix by reordering the baseline file by hand.

### Check 2 — Local matches remote

```bash
supabase db diff --linked --schema public > /tmp/schema-diff.sql
wc -l /tmp/schema-diff.sql
```

Expect 0 (or only comment lines). Non-empty diff means the baseline is missing something on remote (likely a dashboard-created RLS policy or index). Edit the baseline to add the missing object, re-run check 1.

### Check 3 — Migration ledgers agree

```bash
supabase migration list
```

Expected output:

```text
LOCAL          | REMOTE         | TIME
00000000000000 | 00000000000000 | <baseline timestamp>
20260508025626 |                | <future migration not yet pushed>
```

Anything else means the repair step missed a timestamp — re-run `migration repair` for stragglers.

### Optional — App smoke test

Start the API against the freshly-reset local DB and exercise `/insights` and `/ai-insights` with a real Clerk JWT. Confirms RLS, function permissions, and column defaults work end-to-end. Not strictly required for the squash but recommended before declaring the local stack usable.

## What's out of scope

- Verifying functions return identical results on local vs remote (data-dependent; trust the schema match).
- Dashboard-only artifacts that don't appear in `pg_dump`: API keys, dashboard users, edge functions, storage buckets, auth providers configuration. Manual setup if needed for local.
- Any change to remote `public.*` data or schema.
- The actual content of `20260508025626_remove_account_deletion_request.sql` (kept empty as future work).

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| `pg_dump` emits objects in an order Postgres can't replay | Check 1 catches it; reorder by hand |
| Dashboard-created RLS policy missed by `pg_dump` | Check 2 catches it; add to baseline manually |
| Collaborator runs `supabase db push` mid-squash | Coordinate before starting; the repair sequence is fast |
| `migration repair` partially completes (network drop) | Fully reversible — toggle status back; bookkeeping only |
| `electric_user` role missing on local | Documented as manual step in baseline comment |

## Rollback

If anything goes sideways and we want to abandon the squash:

1. `git checkout supabase/migrations/` — restores the 12 deleted files.
2. Re-run `migration repair --status applied <ts>` for each of the 12 timestamps.
3. Delete `00000000000000_initial_schema.sql` and `seed.sql`.
4. `supabase migration repair --status reverted 00000000000000`.

State is back to where it was before the squash (still broken locally, but no production damage).
