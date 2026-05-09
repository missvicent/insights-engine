# Migrations Baseline Squash Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken 13-file migration history with a single baseline migration generated from the remote DB, so any empty Postgres can rebuild the schema from this repo.

**Architecture:** `supabase db dump` against the linked remote produces a faithful schema snapshot. That becomes `00000000000000_initial_schema.sql`. Reference data (`service_templates`, system `categories`) goes into `supabase/seed.sql`. The 12 obsolete migrations are deleted; the empty `20260508025626_remove_account_deletion_request.sql` is kept as a placeholder for follow-on work. Remote's migration tracking table is reconciled via `supabase migration repair` — bookkeeping only, zero DDL on production data.

**Tech Stack:** Supabase CLI (≥ v2.98), `pg_dump` (via `supabase db dump`), Postgres, bash.

**Spec:** `docs/superpowers/specs/2026-05-07-migrations-baseline-squash-design.md`

---

## File Structure

| Path | Action | Responsibility |
| --- | --- | --- |
| `supabase/migrations/00000000000000_initial_schema.sql` | Create | Full snapshot of remote `public` schema. Sole baseline. |
| `supabase/seed.sql` | Create | Idempotent inserts for `service_templates` (all rows) and `categories WHERE is_system = true`. Runs after migrations on `db reset`. |
| `supabase/migrations/20260508025626_remove_account_deletion_request.sql` | Keep (still empty) | Reserved for follow-on work to drop `account_deletion_*` tables. |
| `supabase/migrations/20260110044241_*.sql` … `20260409205443_*.sql` | Delete (12 files) | Captured by the new baseline. Git history preserves them. |
| `supabase/config.toml` | Touch (only if CLI upgrade requires) | Already references `health_timeout` which old CLIs reject. |

**Snapshots created during execution** (gitignored / temporary):

- `/tmp/migrations-baseline-backup-<timestamp>/` — local copy of the 12 files before deletion (rollback safety net).
- `/tmp/remote_data.sql` — full data dump used to extract seed rows.
- `/tmp/schema-diff.sql` — output of Check 2.

---

## Pre-execution requirements

The executor must confirm before starting:

1. The repo is on a fresh branch off `ft/cron-job` (e.g. `ft/migrations-baseline-squash`). All work is committed there.
2. `supabase` CLI is installed and on PATH. Version ≥ v2.98 (older versions reject `health_timeout` in `config.toml`).
3. The repo is linked to the remote (`cat supabase/.temp/project-ref` returns `qedjccrexwvmcbzvcejh`).
4. The remote DB password is on hand (Supabase Dashboard → Project Settings → Database → Connection string).
5. **No collaborator is going to run `supabase db push` while this is in flight.** The repair sequence in Task 8 must complete uninterrupted.

If any precondition fails, stop and report. Do not proceed.

---

## Task 1: Pre-flight — branch, upgrade, snapshot current state

**Files:**
- No file edits in this task. Creates a backup directory under `/tmp/`.

- [ ] **Step 1: Create a working branch off the current branch**

```bash
git checkout -b ft/migrations-baseline-squash
git status
```

Expected: `On branch ft/migrations-baseline-squash` with the existing untracked files from `git status` at the top of the conversation still present (the empty `20260508025626_*.sql`, the modified spec).

- [ ] **Step 2: Upgrade Supabase CLI if version < v2.98**

```bash
supabase --version
```

If output starts with `2.67`, `2.6X`, or anything below `2.98`, upgrade:

```bash
brew upgrade supabase
supabase --version
```

Expected: version `2.98.x` or newer. If on a non-Homebrew install, follow https://supabase.com/docs/guides/cli/getting-started#updating-the-supabase-cli.

- [ ] **Step 3: Stop the local Supabase stack if running**

```bash
supabase stop || true
docker ps --filter "name=supabase_" --format "{{.Names}}"
```

Expected: empty output from the `docker ps` line. If any `supabase_*` container is still running, run `docker stop <name>` for each.

- [ ] **Step 4: Snapshot the current migrations folder to `/tmp/`**

```bash
SNAPSHOT_DIR="/tmp/migrations-baseline-backup-$(date +%Y%m%d%H%M%S)"
mkdir -p "$SNAPSHOT_DIR"
cp -p supabase/migrations/*.sql "$SNAPSHOT_DIR"/
ls -la "$SNAPSHOT_DIR"
echo "Snapshot at: $SNAPSHOT_DIR"
```

Expected: 13 files copied. Record the path — it's the rollback escape hatch if Task 8 goes wrong.

- [ ] **Step 5: Confirm linked project**

```bash
cat supabase/.temp/project-ref
```

Expected: `qedjccrexwvmcbzvcejh`. If the file is missing, run `supabase link --project-ref qedjccrexwvmcbzvcejh` and re-check.

- [ ] **Step 6: Capture the current remote ledger for later comparison**

```bash
supabase migration list > /tmp/migration-list-before.txt
cat /tmp/migration-list-before.txt
```

Expected: 12 timestamps marked applied on REMOTE, 13 timestamps present on LOCAL (the 12 plus the empty future one). Save this file — used for verification in Task 9 and rollback in case of issues.

- [ ] **Step 7: Commit (ask user first per project memory)**

There is nothing to commit yet — this task only creates `/tmp/` artifacts and a new branch. Skip commit.

---

## Task 2: Dump remote schema and data

**Files:**
- Create: `supabase/migrations/00000000000000_initial_schema.sql` (raw dump; cleanup in Task 3)
- Create: `/tmp/remote_data.sql` (used by Task 4)

- [ ] **Step 1: Dump the remote `public` schema**

```bash
supabase db dump --schema public --file supabase/migrations/00000000000000_initial_schema.sql
```

You will be prompted for the remote DB password.

Expected: command exits 0. File `supabase/migrations/00000000000000_initial_schema.sql` is created with several hundred to a few thousand lines.

- [ ] **Step 2: Sanity-check the dump captured the foundational tables**

```bash
grep -c "CREATE TABLE" supabase/migrations/00000000000000_initial_schema.sql
grep -E "^CREATE TABLE.*\b(budgets|categories|transactions|accounts|allocations|goals|debts|debt_payments|recurring_transactions|service_templates|profiles|user_settings|webhook_events|account_deletion_requests|account_deletion_audit|budget_archive_reports)\b" supabase/migrations/00000000000000_initial_schema.sql
```

Expected: count ≥ 16 (the 17 tables minus excluded `_backup_views` if pg_dump skipped it; otherwise 17). The grep prints one line per foundational table.

If a foundational table is missing, stop. Re-run the dump or escalate.

- [ ] **Step 3: Sanity-check RLS policies and functions are present**

```bash
grep -c "CREATE POLICY" supabase/migrations/00000000000000_initial_schema.sql
grep -c "CREATE FUNCTION\|CREATE OR REPLACE FUNCTION" supabase/migrations/00000000000000_initial_schema.sql
grep -c "CREATE INDEX" supabase/migrations/00000000000000_initial_schema.sql
grep -c "CREATE TRIGGER" supabase/migrations/00000000000000_initial_schema.sql
```

Expected: all four counts > 0. RLS in particular — if `CREATE POLICY` is 0, the dump didn't capture security policies and the baseline is unusable. Stop and investigate.

- [ ] **Step 4: Dump the remote data (for seed extraction)**

```bash
supabase db dump --data-only --schema public --file /tmp/remote_data.sql
```

Expected: command exits 0. File created in `/tmp/`. Size depends on user data — could be small (KBs) or large (MBs).

- [ ] **Step 5: Confirm seed-relevant tables have data in the dump**

```bash
grep -E "^COPY public\.(service_templates|categories) " /tmp/remote_data.sql | head
```

Expected: at least one `COPY public.service_templates` line and one `COPY public.categories` line. If neither exists, the seed step (Task 4) will produce empty inserts.

- [ ] **Step 6: Commit the raw dump (ask user first)**

```bash
git add supabase/migrations/00000000000000_initial_schema.sql
git status
```

Show diff to user. With approval:

```bash
git commit -m "chore(db): add raw remote schema dump as baseline (pre-cleanup)"
```

Two commits (raw dump now, cleaned dump in Task 3) make the cleanup diff easy to review.

---

## Task 3: Clean the baseline file

**Files:**
- Modify: `supabase/migrations/00000000000000_initial_schema.sql`

The pg_dump output contains boilerplate that's noisy or local-incompatible. Remove only the safe-to-strip patterns.

- [ ] **Step 1: Inspect what `SET` statements are at the top**

```bash
head -30 supabase/migrations/00000000000000_initial_schema.sql
```

Expected output: a header block of `SET` statements (timezone, search_path, lock_timeout, etc.) followed by `SELECT pg_catalog.set_config(...)` and possibly `CREATE EXTENSION` lines.

- [ ] **Step 2: Remove the `SET` header block**

Open the file in your editor. Delete every line from the start of the file up to (but not including) the first `--` comment that introduces a real object (typically `-- Name: <something>; Type: TABLE; ...`).

Concretely, delete lines matching any of:
- `SET ...;`
- `SELECT pg_catalog.set_config(...);`
- Bare `--` separator lines that surround them.

Keep `CREATE EXTENSION IF NOT EXISTS` lines if present (they're safe and idempotent).

- [ ] **Step 3: Remove `OWNER TO` lines**

```bash
sed -i.bak '/^ALTER .* OWNER TO /d' supabase/migrations/00000000000000_initial_schema.sql
diff supabase/migrations/00000000000000_initial_schema.sql.bak supabase/migrations/00000000000000_initial_schema.sql | head -40
rm supabase/migrations/00000000000000_initial_schema.sql.bak
```

Expected: diff shows only `ALTER … OWNER TO postgres;` (or similar) lines being removed. No `CREATE TABLE` / `CREATE FUNCTION` / `CREATE POLICY` lines should appear in the diff.

- [ ] **Step 4: Remove `_backup_views` references (per spec decision 6)**

```bash
grep -n "_backup_views" supabase/migrations/00000000000000_initial_schema.sql
```

Inspect each match. Open the file, locate the block(s) — typically a `CREATE TABLE public._backup_views (...)` block and possibly grants on it — and delete them by hand. Do not blanket-`sed` because the table may span multiple lines.

After editing, verify:

```bash
grep -c "_backup_views" supabase/migrations/00000000000000_initial_schema.sql
```

Expected: `0`.

- [ ] **Step 5: Remove `CREATE SCHEMA public/auth/extensions` if present**

```bash
grep -n "^CREATE SCHEMA " supabase/migrations/00000000000000_initial_schema.sql
```

If any matches: delete each line (and any subsequent `ALTER SCHEMA ... OWNER TO` if present). Supabase manages these schemas.

- [ ] **Step 6: Add a header comment to the baseline**

Open `supabase/migrations/00000000000000_initial_schema.sql` and prepend:

```sql
-- Baseline schema generated from remote on 2026-05-07.
-- Squashes 12 prior migrations (see git log for history).
-- NOTE: The `electric_user` role is granted access below but is NOT created here.
-- The role is created via the Supabase Dashboard on remote. For local dev, run:
--   CREATE ROLE electric_user;
-- before `supabase db reset`, or comment out the GRANTs in this file.
```

- [ ] **Step 7: Final sanity check — file still parses as SQL**

```bash
wc -l supabase/migrations/00000000000000_initial_schema.sql
grep -c "CREATE TABLE" supabase/migrations/00000000000000_initial_schema.sql
grep -c "CREATE POLICY" supabase/migrations/00000000000000_initial_schema.sql
grep -c "CREATE FUNCTION\|CREATE OR REPLACE FUNCTION" supabase/migrations/00000000000000_initial_schema.sql
```

Expected: all counts unchanged from Task 2 Step 3 (except `CREATE TABLE` may have dropped by 1 if `_backup_views` was in the dump).

- [ ] **Step 8: Commit the cleanup (ask user first)**

```bash
git add supabase/migrations/00000000000000_initial_schema.sql
git diff --cached --stat
```

Show the diff. With approval:

```bash
git commit -m "chore(db): clean baseline (strip SET, OWNER TO, _backup_views)"
```

---

## Task 4: Build `seed.sql`

**Files:**
- Create: `supabase/seed.sql`

- [ ] **Step 1: Extract the `service_templates` rows from the data dump**

```bash
awk '/^COPY public\.service_templates /{flag=1} flag; /^\\\.$/ && flag{flag=0; print "" }' /tmp/remote_data.sql > /tmp/seed_service_templates.sql
wc -l /tmp/seed_service_templates.sql
head -5 /tmp/seed_service_templates.sql
```

Expected: file contains a `COPY public.service_templates ... FROM stdin;` header, tab-separated rows, and a terminating `\.` line.

- [ ] **Step 2: Extract the system `categories` rows**

```bash
awk '/^COPY public\.categories /{flag=1} flag; /^\\\.$/ && flag{flag=0; print "" }' /tmp/remote_data.sql > /tmp/seed_categories_all.sql
wc -l /tmp/seed_categories_all.sql
head -5 /tmp/seed_categories_all.sql
```

This dumps **all** category rows; we need only `is_system = true`. The pg_dump `COPY` format doesn't support filtering, so we filter inline.

- [ ] **Step 3: Identify the column position of `is_system` in the categories COPY**

```bash
head -1 /tmp/seed_categories_all.sql
```

Expected: a line like `COPY public.categories (id, user_id, name, category_type, icon, color, parent_id, is_system, display_order, created_at, is_editable, is_visible, clerk_user_id) FROM stdin;`

Count the column position of `is_system`. Based on the schema dump in the spec, it should be **column 8** (1-indexed). Verify by counting commas in your output.

- [ ] **Step 4: Filter categories to system-only rows**

Replace `8` below if Step 3 showed a different column position.

```bash
{ head -1 /tmp/seed_categories_all.sql; \
  awk -F'\t' 'NR>1 && $0 != "\\." && $8 == "t" {print}' /tmp/seed_categories_all.sql; \
  echo '\.'; } > /tmp/seed_categories_system.sql
wc -l /tmp/seed_categories_system.sql
head -3 /tmp/seed_categories_system.sql
```

Expected: file contains the COPY header, only rows with `t` (Postgres true) in the `is_system` column, and the terminating `\.`. Row count > 0 (you do have system categories on remote).

If row count is 0, stop — either the column position is wrong or remote has no system categories. Investigate.

- [ ] **Step 5: Assemble `supabase/seed.sql`**

`COPY` is faster than `INSERT` and is what `pg_dump` produced. We use it directly. Idempotency: wrap each table in a transaction with `TRUNCATE` so re-running `db reset` always restores to the dumped state.

Create `supabase/seed.sql` with the following structure:

```sql
-- Seed data for local development and CI.
-- Re-runnable: each table is truncated before reload.
-- Generated from remote on 2026-05-07.

BEGIN;
TRUNCATE TABLE public.service_templates CASCADE;
-- <paste contents of /tmp/seed_service_templates.sql here>
COMMIT;

BEGIN;
DELETE FROM public.categories WHERE is_system = true;
-- <paste contents of /tmp/seed_categories_system.sql here>
COMMIT;
```

For categories, use `DELETE WHERE is_system = true` (not `TRUNCATE`) so user-created categories are preserved — though per project memory categories are system-only, this defends against accidental loss.

Concretely:

```bash
{
  echo "-- Seed data for local development and CI."
  echo "-- Re-runnable: each table is truncated/cleared before reload."
  echo "-- Generated from remote on 2026-05-07."
  echo ""
  echo "BEGIN;"
  echo "TRUNCATE TABLE public.service_templates CASCADE;"
  cat /tmp/seed_service_templates.sql
  echo "COMMIT;"
  echo ""
  echo "BEGIN;"
  echo "DELETE FROM public.categories WHERE is_system = true;"
  cat /tmp/seed_categories_system.sql
  echo "COMMIT;"
} > supabase/seed.sql
```

- [ ] **Step 6: Verify the seed file**

```bash
wc -l supabase/seed.sql
grep -c "^COPY public\." supabase/seed.sql
grep -c "^COMMIT;" supabase/seed.sql
```

Expected: 2 `COPY` lines, 2 `COMMIT;` lines. Line count is the sum of the two extracted files plus ~10 lines of wrapper.

- [ ] **Step 7: Commit (ask user first)**

```bash
git add supabase/seed.sql
git diff --cached --stat
```

Show the diff. With approval:

```bash
git commit -m "chore(db): add seed.sql with service_templates and system categories"
```

---

## Task 5: Verify local rebuild — Check 1 from spec

**Files:**
- No file edits. Runs the local stack to validate the baseline.

- [ ] **Step 1: Start the local Supabase stack**

```bash
supabase start
```

Expected: Docker pulls/starts containers (Postgres, Auth, Studio, etc.). Final output prints API URL, anon key, etc. Takes 30-90 seconds.

If this fails with a `config.toml` error (e.g., `health_timeout`), revisit Task 1 Step 2 — the CLI is too old.

If `electric_user` GRANTs in the baseline cause a startup error (`role "electric_user" does not exist`), connect to the local DB and create the role:

```bash
psql "$(supabase status -o json | jq -r '.DB_URL')" -c "CREATE ROLE electric_user;"
```

Then re-run `supabase db reset` (next step) instead of full restart.

- [ ] **Step 2: Reset the local DB to apply the baseline + seed from scratch**

```bash
supabase db reset
```

Expected: output shows:
- `Applying migration 00000000000000_initial_schema.sql...` → success
- `Applying migration 20260508025626_remove_account_deletion_request.sql...` → success (file is empty, no-op)
- `Seeding data from supabase/seed.sql...` → success

If any step errors:
- **`relation "X" does not exist`** → pg_dump emitted FK before referenced table. Open the baseline, find the offending `ALTER TABLE … ADD CONSTRAINT` or `CREATE INDEX`, move it after the referenced object's `CREATE TABLE`. Re-run `supabase db reset`.
- **`function "X" does not exist`** → same problem with a function dependency. Move the function definition earlier.
- **Other errors** → read the error, fix the baseline, re-run.

Repeat until the reset completes cleanly. Each fix is a small move-block edit.

- [ ] **Step 3: Verify table count matches expectations**

```bash
psql "$(supabase status -o json | jq -r '.DB_URL')" -c "\dt public.*"
```

Expected: 16 tables listed (17 minus `_backup_views`). Names: `accounts`, `account_deletion_audit`, `account_deletion_requests`, `allocations`, `budget_archive_reports`, `budgets`, `categories`, `debt_payments`, `debts`, `goals`, `profiles`, `recurring_transactions`, `service_templates`, `transactions`, `user_settings`, `webhook_events`.

- [ ] **Step 4: Verify seed data loaded**

```bash
psql "$(supabase status -o json | jq -r '.DB_URL')" -c "SELECT COUNT(*) AS service_templates FROM public.service_templates;"
psql "$(supabase status -o json | jq -r '.DB_URL')" -c "SELECT COUNT(*) AS system_categories FROM public.categories WHERE is_system = true;"
```

Expected: both counts > 0 and match what's on remote (rough check — exact match verified by Check 2 in Task 6).

- [ ] **Step 5: Commit any baseline reorderings (ask user first)**

If you had to reorder anything in the baseline during Step 2, commit:

```bash
git diff supabase/migrations/00000000000000_initial_schema.sql
```

Show the diff. With approval:

```bash
git add supabase/migrations/00000000000000_initial_schema.sql
git commit -m "chore(db): reorder baseline statements for clean apply"
```

If no edits were needed, skip the commit.

---

## Task 6: Verify schema parity — Check 2 from spec

**Files:**
- Output: `/tmp/schema-diff.sql`

- [ ] **Step 1: Diff local vs remote for the `public` schema**

```bash
supabase db diff --linked --schema public > /tmp/schema-diff.sql
wc -l /tmp/schema-diff.sql
cat /tmp/schema-diff.sql
```

Expected: 0 lines, or only lines that are SQL comments / blank.

- [ ] **Step 2: If the diff is non-empty, classify each block**

For every non-comment block in `/tmp/schema-diff.sql`:

- **Object missing locally** (e.g., `CREATE POLICY ...` that exists on remote but not local): pg_dump missed it. Add the statement to `supabase/migrations/00000000000000_initial_schema.sql` at an appropriate location.
- **Object on local but not remote**: should not happen. If it does, the cleanup pass in Task 3 added or kept something inadvertently. Remove it.
- **Object differs subtly** (e.g., column default reformatted): usually safe to ignore if semantically identical, but document the deviation in the file's header comment.

- [ ] **Step 3: Re-run Check 1 after edits**

If you edited the baseline in Step 2, repeat:

```bash
supabase db reset
supabase db diff --linked --schema public > /tmp/schema-diff.sql
wc -l /tmp/schema-diff.sql
```

Loop until the diff is empty (or only comments).

- [ ] **Step 4: Commit any baseline patches (ask user first)**

If Step 2 made edits:

```bash
git diff supabase/migrations/00000000000000_initial_schema.sql
```

Show the diff. With approval:

```bash
git add supabase/migrations/00000000000000_initial_schema.sql
git commit -m "chore(db): patch baseline with objects missed by pg_dump"
```

---

## Task 7: Delete obsolete migration files

**Files:**
- Delete: 12 `.sql` files in `supabase/migrations/` (listed below)
- Keep: `supabase/migrations/00000000000000_initial_schema.sql`
- Keep: `supabase/migrations/20260508025626_remove_account_deletion_request.sql` (still empty)

- [ ] **Step 1: List the files about to be deleted**

```bash
ls supabase/migrations/2026{0110044241,0313,0321,0322,0323,0324120000,0324130000,0325,0330,0401223244,0402,0409205443}_*.sql
```

Expected: 12 files listed. Confirm the snapshot in `/tmp/migrations-baseline-backup-*` from Task 1 still exists:

```bash
ls /tmp/migrations-baseline-backup-*/
```

- [ ] **Step 2: Remove from git**

```bash
git rm supabase/migrations/20260110044241_get_budgets_with_progress.sql \
       supabase/migrations/20260313_create_debt_tables.sql \
       supabase/migrations/20260321_add_budget_id_to_transactions.sql \
       supabase/migrations/20260322_rewrite_get_budgets_with_progress.sql \
       supabase/migrations/20260323_add_amount_to_budgets.sql \
       supabase/migrations/20260324120000_electric_user_grants.sql \
       supabase/migrations/20260324130000_get_budgets_overview.sql \
       supabase/migrations/20260325_fix_budget_rpc_user_id.sql \
       supabase/migrations/20260330_auto_assign_budget_trigger.sql \
       supabase/migrations/20260401223244_filter_transactions_by_budget.sql \
       supabase/migrations/20260402_rename_budget_items_to_allocations.sql \
       supabase/migrations/20260409205443_savings_goals_integration.sql
```

Expected: 12 files staged for deletion.

- [ ] **Step 3: Verify the resulting state**

```bash
ls supabase/migrations/
```

Expected output (exactly two files):

```text
00000000000000_initial_schema.sql
20260508025626_remove_account_deletion_request.sql
```

- [ ] **Step 4: Re-verify local stack still rebuilds (sanity check)**

```bash
supabase db reset
```

Expected: success (this is essentially a re-run of Task 5 Step 2, but with the obsolete files now physically gone). Confirms nothing in the local execution path quietly depended on them.

- [ ] **Step 5: Commit (ask user first)**

```bash
git status
git diff --cached --stat
```

Show the diff. With approval:

```bash
git commit -m "chore(db): remove 12 migration files now captured in baseline"
```

---

## Task 8: Reconcile remote bookkeeping — `migration repair`

**Files:**
- No file edits. Touches only `supabase_migrations.schema_migrations` on remote.

This is the only step in the plan that modifies remote. Read the spec section "Step 5 — Reconcile remote bookkeeping" before running.

- [ ] **Step 1: Re-confirm pre-state**

```bash
supabase migration list
```

Expected: 12 timestamps applied on REMOTE; LOCAL shows the new baseline + the empty future migration. The 12 old timestamps are now LOCAL-missing (because we deleted them) but still REMOTE-applied (because we haven't repaired yet). The CLI may flag this as out-of-sync — that's the expected state right before Task 8.

- [ ] **Step 2: Mark the 13 old timestamps as reverted on remote**

Run as a single shell loop. You may be prompted for the DB password on the first call.

The list includes `20260508025626` (the empty file we're keeping). Pre-state in `/tmp/migration-list-before.txt` showed it as already applied on remote — meaning when this file is later filled in with real `DROP TABLE` statements, `supabase db push` would silently skip it. Reverting the bookkeeping entry now lets the future fill-in apply normally. The file itself stays in `supabase/migrations/`.

```bash
for ts in 20260110044241 20260313 20260321 20260322 20260323 \
          20260324120000 20260324130000 20260325 20260330 \
          20260401223244 20260402 20260409205443 20260508025626; do
  echo "--- Reverting $ts"
  supabase migration repair --status reverted "$ts" || { echo "FAILED on $ts"; break; }
done
```

Expected: each iteration prints success. No DDL runs against `public.*` — only one row deleted per call from `supabase_migrations.schema_migrations`.

If any iteration fails, **stop**. Inspect the error. Common causes:
- Network drop → re-run the loop; already-reverted timestamps will no-op or error harmlessly.
- Auth issue → re-run `supabase login` and retry.

- [ ] **Step 3: Mark the new baseline as applied on remote**

```bash
supabase migration repair --status applied 00000000000000
```

Expected: success message. One row inserted into `supabase_migrations.schema_migrations`.

This tells remote "the baseline migration has already been run" — which is true, because the schema was already there and we never modified it.

- [ ] **Step 4: No commit needed**

This task changes nothing in the repo. Move to Task 9.

---

## Task 9: Verify migration ledger — Check 3 from spec

**Files:**
- Output: `/tmp/migration-list-after.txt`

- [ ] **Step 1: List migrations**

```bash
supabase migration list > /tmp/migration-list-after.txt
cat /tmp/migration-list-after.txt
```

Expected output (exact format may vary slightly by CLI version):

```text
LOCAL          | REMOTE         | TIME
00000000000000 | 00000000000000 | <baseline timestamp>
20260508025626 |                | <future migration not yet pushed>
```

Both sides agree on the baseline. The empty future migration is local-only (not yet pushed — correct).

- [ ] **Step 2: If the output shows leftover old timestamps on REMOTE**

Re-run the relevant `supabase migration repair --status reverted <ts>` for each straggler. Then re-list.

- [ ] **Step 3: If the output shows the baseline missing on REMOTE**

Re-run `supabase migration repair --status applied 00000000000000`. Then re-list.

- [ ] **Step 4: Final compare to pre-state**

```bash
diff /tmp/migration-list-before.txt /tmp/migration-list-after.txt
```

Expected: a diff that reflects the squash — 12 old REMOTE rows gone, 1 new baseline row present, LOCAL column now matches REMOTE.

---

## Task 10: Optional — App smoke test

**Files:**
- No file edits.

Skip this task if you don't need a usable local stack right now. Recommended before declaring the squash complete.

- [ ] **Step 1: Start the API against local Supabase**

In a new terminal (keep the local stack running):

```bash
source venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Expected: Uvicorn boots without error. Watch for any "missing column" / "function not found" startup logs — those would indicate the baseline missed something.

- [ ] **Step 2: Hit `/insights` with a real Clerk JWT**

```bash
# Replace <JWT> with a real Clerk token
curl -s -H "Authorization: Bearer <JWT>" http://localhost:8000/insights | jq .
```

Expected: 200 response with computed insights JSON. (The user's RLS policies must permit the request; test data may be empty since you haven't loaded user-scoped data into local.)

A 401/403 means JWT verification failed — check `CLERK_ISSUER` env var, not the migration squash.

A 500 with a Postgres error means the baseline is missing something runtime needs. Note the error, return to Task 6 to patch.

- [ ] **Step 3: Stop the API and local stack**

```bash
# Ctrl-C the uvicorn process
supabase stop
```

---

## Task 11: Final verification & branch handoff

**Files:**
- No file edits. Final consolidation of state.

- [ ] **Step 1: Inventory the diff against `ft/cron-job`**

```bash
git log --oneline ft/cron-job..HEAD
git diff --stat ft/cron-job..HEAD
```

Expected commits (in order):
1. raw remote schema dump
2. baseline cleanup
3. seed.sql
4. (optional) baseline reorderings from Task 5
5. (optional) baseline patches from Task 6
6. removal of 12 obsolete migrations

Diff stat: 13 files deleted (12 old migrations + the cleanup churn within `00000000000000_*.sql`), 2 files created (`00000000000000_*.sql`, `seed.sql`).

- [ ] **Step 2: Confirm no stray temp files were committed**

```bash
git ls-files | grep -E "(/tmp/|\.bak$|migrations-baseline-backup)" || echo "clean"
```

Expected: `clean`.

- [ ] **Step 3: Push the branch (ask user first)**

Per project memory: never run git push without explicit ask. With user approval:

```bash
git push -u origin ft/migrations-baseline-squash
```

- [ ] **Step 4: Open PR (optional — ask user first)**

Defer to user. If they want a PR, follow the standard project PR template.

---

## Rollback procedure

If anything in Tasks 5–9 goes irrecoverably wrong:

1. **Undo remote bookkeeping** (if you ran any of Task 8):

   ```bash
   supabase migration repair --status reverted 00000000000000
   for ts in 20260110044241 20260313 20260321 20260322 20260323 \
             20260324120000 20260324130000 20260325 20260330 \
             20260401223244 20260402 20260409205443 20260508025626; do
     supabase migration repair --status applied "$ts"
   done
   ```

2. **Restore local migration files**:

   ```bash
   git checkout ft/cron-job -- supabase/migrations/
   ```

3. **Remove the new baseline and seed** (only if step 2 didn't already):

   ```bash
   rm -f supabase/migrations/00000000000000_initial_schema.sql supabase/seed.sql
   ```

4. **Verify ledger restored**:

   ```bash
   supabase migration list
   diff /tmp/migration-list-before.txt <(supabase migration list)
   ```

State is now back to where it was before Task 1. Local stack is still broken (the original problem), but production is unchanged.

If the snapshot in `/tmp/migrations-baseline-backup-*/` is needed (e.g., the branch is gone), copy files from there back into `supabase/migrations/`.
