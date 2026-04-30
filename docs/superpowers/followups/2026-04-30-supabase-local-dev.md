---
status: open
severity: important
---

# Follow-up: Set up local Supabase for migration testing

**Severity:** Important — process gap. We are testing migrations in the live remote Supabase project because there is no local stack. This already cost us four iterative fix migrations on Task 1.4 (account deletion).

## Problem

The project has a `supabase/migrations/` directory and uses `supabase db push`, but there is no local Supabase stack. Every migration is therefore tested against the hosted dev project — meaning runtime errors only surface after the migration has been applied to a shared environment, recorded by the migration tracker, and (if the function compiles) potentially exercised in the wild.

On 2026-04-30, writing `delete_user_data()` for the account-deletion feature, we hit this loop **four times**:

1. `delete from allocations where user_id = ...` — column doesn't exist.
2. `delete from profiles where user_id = ...` — column is `clerk_user_id`.
3. `revoke ... from public` — Supabase's explicit grants to `anon` and `authenticated` survived, leaving the destructive function callable by any signed-in user via PostgREST RPC. **Critical security gap that landed in production briefly.**
4. `digest(...)` — pgcrypto lives in `extensions`, not on the function's hardened search_path.

Each round was: edit → push → run in dashboard → read error → write a new migration. Net result: four migration files for what is logically a single function definition.

`supabase db reset` (which replays migrations from scratch on a local DB) would have surfaced #1, #2, and #4 at edit time. #3 would have been caught by the same routine permission-verification query we ran post-push (`information_schema.routine_privileges`).

## Why now

Phases 2–6 of the account-deletion plan add: a SECURITY DEFINER function modification, two `pg_cron` jobs, FastAPI/Render integration tests, Clerk webhook plumbing, and a feature-flag rollout. Every one of those touches schema or auth surface. Continuing to test in remote prod-like Supabase will compound risk and PR-review noise.

## Proposed fix

1. **Install local Supabase stack:**
   ```bash
   supabase start
   ```
   First run pulls Docker images (~2 min). Subsequent starts are instant.

2. **Adopt the local-first dev loop:**
   ```bash
   # Edit migration in supabase/migrations/
   supabase db reset       # drops local DB, replays all migrations clean
   # Smoke-test against http://localhost:54323 (Studio) or psql
   supabase db push        # only after green locally
   ```

3. **Document the loop** in `CLAUDE.md` under a new "Database migrations" section so it's institutionalised. Should also update plan line 72 (the "no Supabase CLI" caveat is wrong).

4. **Add a CI check** (later) that runs `supabase db reset` against a clean container so PRs can't merge with migrations that don't replay cleanly.

## Out of scope for this followup

- Squashing the four Task 1.4 migrations into a clean `20260430055524_delete_user_data.sql`. Track separately if pursued — `supabase migration repair --status reverted` is the tool. Keeping all four is also acceptable for an unmerged solo branch.
- Seeding local Supabase with anonymised prod data — useful eventually but not required to unblock migration testing.

## Acceptance criteria

- `supabase start` works from a fresh checkout on the team's primary dev machines.
- `supabase db reset` succeeds end-to-end against the current migrations directory (proves no migration is broken in isolation).
- README / CLAUDE.md describes the edit → reset → push loop.
- New migrations land with evidence (smoke-test output or screenshot) that they were tested locally first.
