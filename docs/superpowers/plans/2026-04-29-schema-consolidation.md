# Schema Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **CRITICAL — TWO REPOS.** Every task names absolute repo paths. The plan crosses:
> - Backend (this repo): `/Users/nily/Documents/Tech/budget-app/insights-engine/`
> - Frontend: `/Users/nily/Documents/Tech/budget-app/personal-budget/`
> Read the path on every task. Never assume cwd.

**Goal:** Move ownership of the Supabase schema and generated TypeScript types from the frontend repo (`personal-budget`) to the backend repo (`insights-engine`), and establish a CI pipeline that applies migrations on merge and PRs the regenerated types into the frontend.

**Architecture:** Backend GH Actions runs lint+test on every push, and on `main` additionally runs `supabase db push` followed by `supabase gen types typescript --linked` and opens a PR in `personal-budget` with the regenerated types. Render keeps deploying the backend; AWS S3+CloudFront keeps deploying the frontend; GitHub Actions never deploys. Frontend hand-rolled types refactor to import row shapes from the auto-generated file (hybrid model), preserving domain projections like `BudgetWithProgress`.

**Tech Stack:** Supabase CLI ≥ 1.200, GitHub Actions, `peter-evans/create-pull-request@v6`, TypeScript 5+, pnpm 10, Vite, Vitest. Backend Python 3.13 + pytest unchanged.

**Spec reference:** `docs/superpowers/specs/2026-04-29-schema-consolidation-design.md`.

---

## File map

### Backend (`/Users/nily/Documents/Tech/budget-app/insights-engine/`)

#### New files
| Path | Purpose |
|---|---|
| `supabase/config.toml` | `supabase init` output, project_id linked. |
| `supabase/migrations/20260110044241_get_budgets_with_progress.sql` | Moved from frontend. |
| `supabase/migrations/20260313_create_debt_tables.sql` | Moved. |
| `supabase/migrations/20260321_add_budget_id_to_transactions.sql` | Moved. |
| `supabase/migrations/20260322_rewrite_get_budgets_with_progress.sql` | Moved. |
| `supabase/migrations/20260323_add_amount_to_budgets.sql` | Moved. |
| `supabase/migrations/20260324120000_electric_user_grants.sql` | Moved. |
| `supabase/migrations/20260324130000_get_budgets_overview.sql` | Moved. |
| `supabase/migrations/20260325_fix_budget_rpc_user_id.sql` | Moved. |
| `supabase/migrations/20260330_auto_assign_budget_trigger.sql` | Moved. |
| `supabase/migrations/20260401223244_filter_transactions_by_budget.sql` | Moved. |
| `supabase/migrations/20260402_rename_budget_items_to_allocations.sql` | Moved. |
| `supabase/migrations/20260409205443_savings_goals_integration.sql` | Moved. |
| `.github/workflows/ci.yml` | Lint + test on every push; on main, db push + gen-types + PR bot. |
| `.gitignore` (modify) | Add `supabase/.temp/`, `supabase/.branches/` to ignore CLI scratch state. |

#### Modified files
None to backend Python. The plan does not touch `app/`.

### Frontend (`/Users/nily/Documents/Tech/budget-app/personal-budget/`)

#### New files
| Path | Purpose |
|---|---|
| `src/lib/supabase/types.ts` | Auto-generated `Database` type from `supabase gen types`. Committed by PR bot. |

#### Modified files
| Path | Change |
|---|---|
| `src/lib/supabaseClient.ts` | Type-parameterise `createClient<Database>(...)`. |
| `src/types/account.types.ts` | Refactor to `Tables<'accounts'>` + helpers. |
| `src/types/budget.types.ts` | Refactor `Budget` + `Allocation`; keep `BudgetWithProgress`, `BudgetOverview` hand-rolled. |
| `src/types/category.types.ts` | Refactor to `Tables<'categories'>`. |
| `src/types/debt.types.ts` | Refactor `Debt`, `DebtPayment`, `CreateDebt`, `UpdateDebt`; keep `DebtType` if not a DB enum. |
| `src/types/goal.types.ts` | Refactor `Goal`; keep `GoalWithProgress` hand-rolled. |
| `src/types/profile.types.ts` | **SPECIAL CASE:** existing camelCase fields (`avatarUrl`, `createdAt`) don't match DB snake_case. Plan handles this. |
| `src/types/transaction.types.ts` | Refactor `Transaction`; keep `TransactionWithCategory`, `TransactionFilters`, `PaginatedResponse` hand-rolled. |
| `src/types/user-settings.types.ts` | Refactor to `Tables<'user_settings'>`. |
| `src/types/index.ts` | No content change — exports stay identical. Verify after refactor. |
| `src/types/database.types.ts` | No content change — exports stay identical. Verify after refactor. |

#### Deleted files
| Path | When |
|---|---|
| `supabase/migrations/*.sql` (12 files) | Phase 7, after backend CI proves migrations apply cleanly. |
| `supabase/` (directory) | Phase 7, full removal. |

### Files NOT touched
- `src/types/insights.types.ts` — domain projections from FastAPI; not DB rows.
- `src/types/selectOptions.types.ts` — UI types.
- `src/types/user.ts` — third-party auth shape.

---

## Conventions

- **Each task ends with a commit.** Frequent commits = recoverable state.
- **Each task names the cwd in shell snippets** via absolute paths or explicit `cd` (avoid stale-shell mistakes when crossing repos).
- **TypeScript build is the source of truth for "did we break anything"** in the frontend. Run `pnpm build` (which runs `tsc`) at every checkpoint in Phase 6.
- **No live-DB writes from the engineer's machine.** All `supabase db push` runs from CI. Locally we only `supabase migration list` (read-only) and `supabase gen types typescript` (read-only).
- **Commits in the frontend use Conventional Commits matching its existing style;** check `git log --oneline -20` before the first commit there.

---

## Phase 1 — Backend CI bootstrap

### Task 1.1 — Create lint+test workflow

**Repo:** `/Users/nily/Documents/Tech/budget-app/insights-engine/`

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1.1.1: Verify the directory doesn't exist yet**

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && ls -la .github 2>/dev/null
```
Expected: `ls: .github: No such file or directory`. (Confirmed earlier — the backend has no GH Actions yet.)

- [ ] **Step 1.1.2: Create the workflow file**

Create `/Users/nily/Documents/Tech/budget-app/insights-engine/.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
          cache: "pip"

      - name: Install dependencies
        run: |
          pip install --upgrade pip
          pip install -r requirements.txt
          pip install -r requirements-dev.txt
          pip install ruff

      - name: Ruff format check
        run: ruff format --check app tests

      - name: Ruff lint
        run: ruff check app tests

      - name: Pytest
        run: pytest -q
```

- [ ] **Step 1.1.3: Push a no-op branch and watch the workflow run**

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  git checkout -b ci/bootstrap && \
  git add .github/workflows/ci.yml && \
  git commit -m "ci: bootstrap GitHub Actions lint+test workflow" && \
  git push -u origin ci/bootstrap
```

Open the GH Actions tab in the repo. The workflow should run.

Expected results:
- ruff format check: may fail if existing code has formatting drift. If so, run `ruff format app tests` locally, commit, push.
- ruff lint: same — `ruff check --fix app tests` if needed.
- pytest: should pass (existing test_deps.py may be stale per the account-deletion plan; if it fails on `main`, the CI failure is pre-existing and not our concern in this PR).

If the existing suite has pre-existing failures, **do not fix them in this PR**. Open a follow-up issue noting the breakage.

- [ ] **Step 1.1.4: Open a PR, get green CI**

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  gh pr create --title "ci: bootstrap lint+test workflow" \
    --body "Phase 1 of schema consolidation — adds .github/workflows/ci.yml for ruff + pytest on push/PR. Render deploy unchanged."
```

Wait for green. Merge.

```bash
gh pr merge --squash
git checkout main && git pull
```

---

## Phase 2 — Migration consolidation

### Task 2.1 — Install Supabase CLI locally

**Repo:** any.

- [ ] **Step 2.1.1: Install via brew**

```bash
brew install supabase/tap/supabase
supabase --version
```
Expected: `1.x.y`. Note the version — pin it in CI later.

If brew install fails, fall back to a binary download from <https://github.com/supabase/cli/releases>.

- [ ] **Step 2.1.2: Log in**

```bash
supabase login
```
Opens a browser; paste the returned token. This stores creds in `~/.supabase/`.

- [ ] **Step 2.1.3: Find your project ref**

In the Supabase dashboard, project settings → General → Reference ID. It's a 20-char string like `abcdefghijklmnopqrst`.

```bash
echo "PROJECT_REF=<paste>" > /tmp/supabase-ref
```

(Used in subsequent steps. Don't commit this.)

---

### Task 2.2 — Initialise `supabase/` in the backend

**Repo:** `/Users/nily/Documents/Tech/budget-app/insights-engine/`

**Files:**
- Create: `supabase/config.toml`

- [ ] **Step 2.2.1: Run init**

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  supabase init
```

Answer:
- "Generate VS Code workspace settings?" → No
- "Generate IntelliJ Datagrip settings?" → No

Expected output: `supabase/config.toml` created, `supabase/.gitignore` created.

- [ ] **Step 2.2.2: Link to the live project**

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  source /tmp/supabase-ref && \
  supabase link --project-ref "$PROJECT_REF"
```

You'll be prompted for the database password (Supabase dashboard → Settings → Database → "Reset database password" if you don't remember it; **do not reset on production unless you intend to**).

Expected output: `Finished supabase link.`

- [ ] **Step 2.2.3: Confirm link**

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  cat supabase/.temp/project-ref
```
Should print your project ref. (`.temp/` is gitignored — that's correct.)

- [ ] **Step 2.2.4: Add CLI scratch state to .gitignore**

If `supabase init` already added a `supabase/.gitignore`, leave it. Verify:

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  cat supabase/.gitignore
```

Expected to contain `.temp` and `.branches`. If missing, append:

```
.temp/
.branches/
```

- [ ] **Step 2.2.5: Commit**

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  git checkout -b db/init-supabase && \
  git add supabase/config.toml supabase/.gitignore && \
  git commit -m "feat(db): initialise supabase/ — link to live project

Adds supabase/config.toml + supabase/.gitignore. Live project ref is
stored in supabase/.temp/ which is gitignored. No migrations yet — those
move from personal-budget in the next task."
```

---

### Task 2.3 — Move 12 SQL migrations from frontend to backend

**Repos:** both — `personal-budget` (source), `insights-engine` (destination).

**Files:**
- Move 12 SQL files from `/Users/nily/Documents/Tech/budget-app/personal-budget/supabase/migrations/` to `/Users/nily/Documents/Tech/budget-app/insights-engine/supabase/migrations/`.

> **Critical:** preserve filenames byte-for-byte. The Supabase CLI tracks migrations by timestamp prefix; renaming would cause re-application.

- [ ] **Step 2.3.1: Create the destination directory**

```bash
mkdir -p /Users/nily/Documents/Tech/budget-app/insights-engine/supabase/migrations
```

- [ ] **Step 2.3.2: Copy each file (don't move yet — keep frontend intact until Phase 7)**

```bash
cp /Users/nily/Documents/Tech/budget-app/personal-budget/supabase/migrations/*.sql \
   /Users/nily/Documents/Tech/budget-app/insights-engine/supabase/migrations/
```

- [ ] **Step 2.3.3: Verify file count and names**

```bash
ls /Users/nily/Documents/Tech/budget-app/insights-engine/supabase/migrations/ | wc -l
```
Expected: `12`.

```bash
diff <(ls /Users/nily/Documents/Tech/budget-app/personal-budget/supabase/migrations/) \
     <(ls /Users/nily/Documents/Tech/budget-app/insights-engine/supabase/migrations/)
```
Expected: no output (identical lists).

- [ ] **Step 2.3.4: Verify byte-for-byte equality**

```bash
for f in /Users/nily/Documents/Tech/budget-app/personal-budget/supabase/migrations/*.sql; do
  fname=$(basename "$f")
  diff "$f" "/Users/nily/Documents/Tech/budget-app/insights-engine/supabase/migrations/$fname" \
    && echo "OK: $fname" || echo "MISMATCH: $fname"
done
```
Expected: all `OK:` lines, no `MISMATCH`.

- [ ] **Step 2.3.5: Commit copies in backend**

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  git add supabase/migrations/ && \
  git commit -m "feat(db): import 12 migrations from personal-budget

Byte-for-byte copies, original filenames preserved so the Supabase CLI
recognises them as already applied (timestamp prefix is the key in
supabase_migrations.schema_migrations).

Migrations are NOT removed from personal-budget yet — that happens in
Phase 7 once backend CI has proven the apply pipeline works."
```

---

### Task 2.4 — Verify migrations are recognised as already-applied

**Repo:** `/Users/nily/Documents/Tech/budget-app/insights-engine/`

> This task is the integrity check the spec calls out as a Phase 2 → Phase 3 blocker. Do not push migrations to CI until this passes.

- [ ] **Step 2.4.1: List remote migrations**

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  supabase migration list --linked
```

Expected output: a table with three columns — `Local`, `Remote`, `Time`. **Every one of our 12 migrations should appear with timestamps in BOTH `Local` and `Remote` columns.**

If any row shows a value in `Remote` but blank in `Local` → the live DB has a migration we don't have locally. **Stop. Investigate.**

If any row shows `Local` but blank in `Remote` → our file is unknown to the live DB. **Stop. The CLI would try to apply it. Investigate.**

- [ ] **Step 2.4.2: If drift exists, baseline via `db pull`**

If Step 2.4.1 showed unexpected drift, capture the live state into a fresh migration:

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  supabase db pull
```

This creates a new migration in `supabase/migrations/` representing the diff. Inspect it carefully. If it captures real schema (not noise), commit it. If it's empty, delete it.

If drift cannot be reconciled cleanly → escalate; do not proceed.

- [ ] **Step 2.4.3: Confirm clean state**

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  supabase migration list --linked | tail -20
```

Final state: every Local has a matching Remote. No outliers.

- [ ] **Step 2.4.4: Open the PR for the migration import**

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  git push -u origin db/init-supabase && \
  gh pr create --title "feat(db): consolidate Supabase migrations into backend" \
    --body "Phase 2 of schema consolidation. Imports 12 migrations from
personal-budget byte-for-byte. Frontend's supabase/ stays put until
Phase 7. Verified via 'supabase migration list --linked' that all 12
already exist in supabase_migrations.schema_migrations on the live DB."
```

Wait for green CI (lint+test from Phase 1). Merge with squash.

```bash
gh pr merge --squash && git checkout main && git pull
```

---

## Phase 3 — Add `supabase db push` to CI

### Task 3.1 — Provision GitHub repo secrets and variables

**Repo:** `/Users/nily/Documents/Tech/budget-app/insights-engine/` (GitHub settings)

> No code changes. This is a runbook step.

- [ ] **Step 3.1.1: Generate a Supabase access token**

Supabase dashboard → Account → Access Tokens → "Generate new token". Name it `insights-engine-ci`. Copy the value.

- [ ] **Step 3.1.2: Set GitHub secrets**

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  gh secret set SUPABASE_ACCESS_TOKEN --body "<paste-token>" && \
  gh secret set SUPABASE_DB_PASSWORD --body "<the-db-password-used-during-supabase-link>"
```

- [ ] **Step 3.1.3: Set GitHub variable for project ref**

(Variables are non-secret — easier to inspect than secrets, fine for the project ref.)

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  gh variable set SUPABASE_PROJECT_REF --body "<paste-ref>"
```

- [ ] **Step 3.1.4: Confirm**

```bash
gh secret list
gh variable list
```

Expected: `SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD` in secrets; `SUPABASE_PROJECT_REF` in variables.

---

### Task 3.2 — Extend `ci.yml` with `supabase db push`

**Repo:** `/Users/nily/Documents/Tech/budget-app/insights-engine/`

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 3.2.1: Pin the Supabase CLI version we'll use**

```bash
supabase --version
```

Whatever it prints (e.g. `1.234.5`), use that exact version in the workflow below.

- [ ] **Step 3.2.2: Update the workflow**

Replace the entire contents of `/Users/nily/Documents/Tech/budget-app/insights-engine/.github/workflows/ci.yml` with:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
          cache: "pip"

      - name: Install dependencies
        run: |
          pip install --upgrade pip
          pip install -r requirements.txt
          pip install -r requirements-dev.txt
          pip install ruff

      - name: Ruff format check
        run: ruff format --check app tests

      - name: Ruff lint
        run: ruff check app tests

      - name: Pytest
        run: pytest -q

  apply-migrations:
    needs: lint-and-test
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    runs-on: ubuntu-latest
    env:
      SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}
      SUPABASE_DB_PASSWORD: ${{ secrets.SUPABASE_DB_PASSWORD }}
      SUPABASE_PROJECT_REF: ${{ vars.SUPABASE_PROJECT_REF }}
    steps:
      - uses: actions/checkout@v4

      - name: Install Supabase CLI
        uses: supabase/setup-cli@v1
        with:
          version: 1.234.5  # PIN: replace with the version printed by `supabase --version` locally

      - name: Link to project
        run: supabase link --project-ref "$SUPABASE_PROJECT_REF"

      - name: List migration state
        run: supabase migration list --linked

      - name: Apply pending migrations
        run: supabase db push --linked
```

> Replace `1.234.5` with the exact version from Step 3.2.1.

- [ ] **Step 3.2.3: Test on a throwaway branch**

The first time we run `supabase db push --linked`, the live DB will already have all 12 migrations applied. The push command should be a no-op.

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  git checkout -b db/wire-push && \
  git add .github/workflows/ci.yml && \
  git commit -m "ci: add supabase db push job (main-only)" && \
  git push -u origin db/wire-push && \
  gh pr create --title "ci: wire supabase db push to GH Actions" \
    --body "Phase 3. apply-migrations job runs only on push to main, after lint-and-test. First run is a no-op — live DB already has all 12 migrations from the consolidation."
```

The PR's CI run will execute lint-and-test only (the apply-migrations job is gated to `main` push events). Confirm lint+test green.

- [ ] **Step 3.2.4: Merge to main, watch apply-migrations succeed**

```bash
gh pr merge --squash && git checkout main && git pull
```

Watch the post-merge workflow run on `main`. The `apply-migrations` job should:
- Install CLI ✓
- Link successfully ✓
- `migration list` shows all 12 with Local + Remote populated ✓
- `db push` reports `No new migrations to apply.`

If `db push` tries to apply something, **immediately mark the workflow as cancelled** (GH Actions UI → cancel workflow). Inspect the migration it tried to apply. The integrity check in Phase 2.4 should have caught this — investigate why it didn't.

---

## Phase 4 — Add types regen + PR bot to CI

### Task 4.1 — Provision the frontend repo PAT

**Repo:** `/Users/nily/Documents/Tech/budget-app/insights-engine/` (GitHub settings)

- [ ] **Step 4.1.1: Create a fine-grained PAT**

GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → "Generate new token".
- Name: `insights-engine-types-bot`
- Expires: 90 days
- Repository access: **Only select repositories** → `personal-budget`
- Permissions:
  - Repository → **Contents:** Read and write
  - Repository → **Pull requests:** Read and write
- Generate. Copy the token.

- [ ] **Step 4.1.2: Set as a secret in the backend repo**

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  gh secret set FRONTEND_REPO_TOKEN --body "<paste-pat>"
```

- [ ] **Step 4.1.3: Confirm**

```bash
gh secret list | grep FRONTEND_REPO_TOKEN
```

---

### Task 4.2 — Extend CI with types regen + PR bot

**Repo:** `/Users/nily/Documents/Tech/budget-app/insights-engine/`

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 4.2.1: Add the new job**

Append to the `jobs:` section in `/Users/nily/Documents/Tech/budget-app/insights-engine/.github/workflows/ci.yml`:

```yaml
  regen-types:
    needs: apply-migrations
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    runs-on: ubuntu-latest
    env:
      SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}
      SUPABASE_PROJECT_REF: ${{ vars.SUPABASE_PROJECT_REF }}
    steps:
      - uses: actions/checkout@v4

      - name: Install Supabase CLI
        uses: supabase/setup-cli@v1
        with:
          version: 1.234.5  # match Phase 3 pin

      - name: Generate types
        run: |
          supabase gen types typescript \
            --project-id "$SUPABASE_PROJECT_REF" \
            > /tmp/database.ts
          ls -la /tmp/database.ts

      - name: Checkout frontend
        uses: actions/checkout@v4
        with:
          repository: <YOUR_GH_USER>/personal-budget   # replace with the actual owner/repo
          token: ${{ secrets.FRONTEND_REPO_TOKEN }}
          path: frontend

      - name: Install generated file into frontend
        run: |
          mkdir -p frontend/src/lib/supabase
          cp /tmp/database.ts frontend/src/lib/supabase/types.ts

      - name: Open PR if changed
        uses: peter-evans/create-pull-request@v6
        with:
          path: frontend
          token: ${{ secrets.FRONTEND_REPO_TOKEN }}
          branch: bot/regen-supabase-types
          title: "chore(types): regenerate Supabase types"
          commit-message: "chore(types): regenerate Supabase types from live schema"
          body: |
            Auto-generated by insights-engine CI after schema migration on main.

            Run: ${{ github.run_id }}
            Source commit: ${{ github.sha }}

            Review the diff in `src/lib/supabase/types.ts`. If a column
            type changed in a way that breaks consumers, expect TS
            errors in the affected files — fix them before merging.
          delete-branch: true
```

> Replace `<YOUR_GH_USER>/personal-budget` with the actual repo (e.g. `nilyvicent/personal-budget`). Find it via `cd /Users/nily/Documents/Tech/budget-app/personal-budget && gh repo view --json nameWithOwner -q .nameWithOwner`.

- [ ] **Step 4.2.2: Look up the actual frontend repo coordinates**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  gh repo view --json nameWithOwner -q .nameWithOwner
```
Use the printed value to replace `<YOUR_GH_USER>/personal-budget` in the workflow.

- [ ] **Step 4.2.3: Push, merge, watch the PR appear (or not — if no schema changes, no PR)**

Since no migrations changed in this PR, the regenerated types should match what would have been generated previously. **If `src/lib/supabase/types.ts` doesn't yet exist in the frontend, the PR bot will open one with the file as a net-new addition** — this is the desired Phase 5 outcome.

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  git checkout -b db/regen-types-job && \
  git add .github/workflows/ci.yml && \
  git commit -m "ci: regenerate Supabase types and PR them into frontend" && \
  git push -u origin db/regen-types-job && \
  gh pr create --title "ci: types regen + PR bot to personal-budget" \
    --body "Phase 4. regen-types runs after apply-migrations on main, generates fresh TS types, opens PR in personal-budget."
```

Merge after green CI:
```bash
gh pr merge --squash && git checkout main && git pull
```

- [ ] **Step 4.2.4: Watch the post-merge workflow on main**

In the backend Actions tab, the workflow should run all three jobs. After `regen-types` finishes:
- Visit `https://github.com/<owner>/personal-budget/pulls`
- Expect a PR titled `chore(types): regenerate Supabase types` from `bot/regen-supabase-types`
- The PR adds `src/lib/supabase/types.ts` with the full `Database` type

If the PR doesn't appear:
- Check the `regen-types` job logs.
- "Resource not accessible by integration" → PAT permissions wrong; revisit Task 4.1.1.
- "no changes to commit" → file already exists in frontend; safe to ignore.

> **Hard checkpoint:** do NOT proceed to Phase 5 until this PR exists or you've manually verified `personal-budget/src/lib/supabase/types.ts` is present and current.

---

## Phase 5 — Generated types in frontend

### Task 5.1 — Merge the bot's PR (or create the file manually)

**Repo:** `/Users/nily/Documents/Tech/budget-app/personal-budget/`

- [ ] **Step 5.1.1: If the bot's PR exists, review and merge**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  gh pr list --head bot/regen-supabase-types
```

If listed, view the diff:
```bash
gh pr diff <pr-number>
```

The diff should add a single new file `src/lib/supabase/types.ts` with content roughly like:
```typescript
export type Json = ...
export type Database = {
  public: {
    Tables: { ... }
    Views: { ... }
    Functions: { ... }
    ...
  }
}
```

Merge:
```bash
gh pr merge <pr-number> --squash && git checkout main && git pull
```

- [ ] **Step 5.1.2: If no PR appeared, generate locally and commit manually**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  source /tmp/supabase-ref && \
  mkdir -p src/lib/supabase && \
  supabase gen types typescript --project-id "$PROJECT_REF" \
    > src/lib/supabase/types.ts
```

- [ ] **Step 5.1.3: Confirm the file compiles**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  pnpm tsc --noEmit
```

Expected: no new errors. (Pre-existing errors are OK; just don't introduce new ones from this file.)

- [ ] **Step 5.1.4: Commit if it was a manual add**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  git checkout -b types/add-generated && \
  git add src/lib/supabase/types.ts && \
  git commit -m "feat(types): add generated Supabase Database type"
```

(If the bot already merged, skip this step.)

---

## Phase 6 — Hybrid type refactor

> **Strategy for this phase:** make many small commits. Each refactor is one type-file at a time, each ends with `pnpm tsc --noEmit` passing. If a refactor breaks consumers, fix them in the same commit or revert just that file.
>
> Run `cd /Users/nily/Documents/Tech/budget-app/personal-budget && pnpm tsc --noEmit > /tmp/tsc.before 2>&1` once at the start of Phase 6 to capture pre-existing errors as a baseline. Compare against this baseline at each checkpoint.

### Task 6.1 — Capture baseline

**Repo:** `/Users/nily/Documents/Tech/budget-app/personal-budget/`

- [ ] **Step 6.1.1: Branch**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  git checkout main && git pull && \
  git checkout -b types/hybrid-refactor
```

- [ ] **Step 6.1.2: Capture pre-refactor compile baseline**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  pnpm tsc --noEmit 2>&1 | tee /tmp/tsc.before
```

Note the count of errors at the bottom (or "Found 0 errors"). Use this as the threshold — any task that increases the count is a regression.

---

### Task 6.2 — Refactor `supabaseClient.ts` to type-parameterise `createClient`

**Repo:** `/Users/nily/Documents/Tech/budget-app/personal-budget/`

**Files:**
- Modify: `src/lib/supabaseClient.ts`

- [ ] **Step 6.2.1: Apply the change**

Replace the contents of `/Users/nily/Documents/Tech/budget-app/personal-budget/src/lib/supabaseClient.ts` with:

```typescript
import { createClient } from '@supabase/supabase-js'
import type { Database } from '@/lib/supabase/types'

export function createSupabaseClient(getToken: () => Promise<string | null>) {
  return createClient<Database>(
    import.meta.env.VITE_SUPABASE_URL,
    import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY,
    {
      async accessToken() {
        return await getToken()
      },
    },
  )
}
```

- [ ] **Step 6.2.2: Verify**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  pnpm tsc --noEmit 2>&1 | tee /tmp/tsc.client
```

Compare error count to `/tmp/tsc.before`. Threading `<Database>` through every `.from('table')` call **may surface real type errors in consumer files** — places where the existing hand-rolled `Account` or `Transaction` types disagreed with the actual DB shape. Note any new errors but **do not fix them yet** — they get resolved as we refactor each `*.types.ts` file.

If the count is dramatically higher (e.g., 50+ new errors), pause and inspect: it likely means consumers extensively rely on type assertions that now fail. Decide whether to refactor consumers in line, or revert this file and stage `<Database>` parameterisation for after Task 6.10.

- [ ] **Step 6.2.3: Commit**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  git add src/lib/supabaseClient.ts && \
  git commit -m "feat(supabase): type-parameterise client with Database"
```

---

### Task 6.3 — Refactor `account.types.ts`

**Repo:** `/Users/nily/Documents/Tech/budget-app/personal-budget/`

**Files:**
- Modify: `src/types/account.types.ts`

- [ ] **Step 6.3.1: Apply the change**

Replace the contents of `/Users/nily/Documents/Tech/budget-app/personal-budget/src/types/account.types.ts` with:

```typescript
import type { Tables, TablesInsert, TablesUpdate } from '@/lib/supabase/types'

export type Account = Tables<'accounts'>
export type CreateAccount = TablesInsert<'accounts'>
export type UpdateAccount = TablesUpdate<'accounts'>
```

> The original `type` field was a TypeScript union (`'checking' | 'savings' | 'credit' | 'cash'`). If the DB column is a check constraint or enum, the generated types will already encode this. If it's plain text, `Tables<'accounts'>['type']` will be `string`. Either is acceptable; consumers that relied on the union narrowing may need adjustment.

- [ ] **Step 6.3.2: Verify**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  pnpm tsc --noEmit 2>&1 | tail -20
```

Note new errors (if any) — they're consumers expecting the old shape.

- [ ] **Step 6.3.3: Fix any consumers in this commit**

If errors trace to `src/components/...` or `src/routes/...` referencing `Account`, fix them in this commit. Common cases:
- `account.type === 'savings'` → still works if the DB encodes the enum
- `Account` field that the hand-rolled type had as required but DB has as nullable → handle the `null` case
- `Omit<Account, 'created_at'>` → may need `TablesInsert<'accounts'>` instead

- [ ] **Step 6.3.4: Commit**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  git add -u && \
  git commit -m "refactor(types): account.types extends generated Tables<'accounts'>"
```

---

### Task 6.4 — Refactor `category.types.ts`

**Repo:** `/Users/nily/Documents/Tech/budget-app/personal-budget/`

**Files:**
- Modify: `src/types/category.types.ts`

- [ ] **Step 6.4.1: Apply**

Replace contents of `/Users/nily/Documents/Tech/budget-app/personal-budget/src/types/category.types.ts`:

```typescript
import type { Tables } from '@/lib/supabase/types'

export type Category = Tables<'categories'>
```

- [ ] **Step 6.4.2: Verify, fix consumers, commit**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  pnpm tsc --noEmit 2>&1 | tail -20
```

Fix any new errors in the same commit. Then:

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  git add -u && \
  git commit -m "refactor(types): category.types extends generated Tables<'categories'>"
```

---

### Task 6.5 — Refactor `user-settings.types.ts`

**Repo:** `/Users/nily/Documents/Tech/budget-app/personal-budget/`

**Files:**
- Modify: `src/types/user-settings.types.ts`

- [ ] **Step 6.5.1: Apply**

Replace contents of `/Users/nily/Documents/Tech/budget-app/personal-budget/src/types/user-settings.types.ts`:

```typescript
import type { Tables } from '@/lib/supabase/types'

export type UserSettings = Tables<'user_settings'>
```

- [ ] **Step 6.5.2: Verify, fix consumers, commit**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  pnpm tsc --noEmit 2>&1 | tail -20 && \
  git add -u && \
  git commit -m "refactor(types): user-settings.types extends generated Tables<'user_settings'>"
```

---

### Task 6.6 — Refactor `goal.types.ts` (preserve `GoalWithProgress` projection)

**Repo:** `/Users/nily/Documents/Tech/budget-app/personal-budget/`

**Files:**
- Modify: `src/types/goal.types.ts`

- [ ] **Step 6.6.1: Apply**

Replace contents of `/Users/nily/Documents/Tech/budget-app/personal-budget/src/types/goal.types.ts`:

```typescript
import type { Tables } from '@/lib/supabase/types'

export type Goal = Tables<'goals'>

// Domain projection — output of get_goals_with_progress RPC, not a raw row.
export interface GoalWithProgress extends Goal {
  current_amount: number
  budget_contributions: number
  direct_contributions: number
}
```

- [ ] **Step 6.6.2: Verify, fix consumers, commit**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  pnpm tsc --noEmit 2>&1 | tail -20 && \
  git add -u && \
  git commit -m "refactor(types): goal.types extends generated Tables<'goals'>; preserve GoalWithProgress"
```

---

### Task 6.7 — Refactor `debt.types.ts`

**Repo:** `/Users/nily/Documents/Tech/budget-app/personal-budget/`

**Files:**
- Modify: `src/types/debt.types.ts`

- [ ] **Step 6.7.1: Apply**

Replace contents of `/Users/nily/Documents/Tech/budget-app/personal-budget/src/types/debt.types.ts`:

```typescript
import type { Tables, TablesInsert, TablesUpdate } from '@/lib/supabase/types'

// DebtType is a TS union here because the DB stores it as text with a CHECK
// constraint, not a Postgres enum. Generated types will type the column as
// `string`; we narrow back to the union for consumer ergonomics.
export type DebtType =
  | 'credit_card'
  | 'personal_loan'
  | 'auto_loan'
  | 'student_loan'
  | 'mortgage'

export type Debt = Tables<'debts'> & { type: DebtType }
export type DebtPayment = Tables<'debt_payments'>
export type CreateDebt = TablesInsert<'debts'>
export type UpdateDebt = TablesUpdate<'debts'>
```

> If `supabase gen types` produced `type: DebtType` already (because the DB has it as a Postgres enum, not a CHECK constraint), the `& { type: DebtType }` intersection is harmless. If `supabase gen types` produced `type: string`, the intersection is the narrowing.

- [ ] **Step 6.7.2: Verify, fix consumers, commit**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  pnpm tsc --noEmit 2>&1 | tail -20 && \
  git add -u && \
  git commit -m "refactor(types): debt.types extends generated tables; preserve DebtType union"
```

---

### Task 6.8 — Refactor `budget.types.ts` (preserve `BudgetWithProgress`, `BudgetOverview`)

**Repo:** `/Users/nily/Documents/Tech/budget-app/personal-budget/`

**Files:**
- Modify: `src/types/budget.types.ts`

- [ ] **Step 6.8.1: Apply**

Replace contents of `/Users/nily/Documents/Tech/budget-app/personal-budget/src/types/budget.types.ts`:

```typescript
import type { Tables } from '@/lib/supabase/types'

export type Budget = Tables<'budgets'>
export type Allocation = Tables<'allocations'>

// Domain projection — output of get_budgets_with_progress RPC, joins
// budgets + allocations + categories + goals.
export interface BudgetWithProgress {
  budget_id: string
  budget_name: string
  budget_amount: number
  period: 'monthly' | 'yearly'
  start_date: string
  end_date: string | null
  is_active: boolean
  allocation_id: string
  category_id: string | null
  goal_id: string | null
  amount: number
  alert_enabled: boolean
  alert_threshold: number
  category_name: string | null
  category_type: string | null
  category_color: string | null
  category_icon: string | null
  goal_name: string | null
  progress: number
}

// Domain projection — output of get_budgets_overview RPC.
export interface BudgetOverview {
  budget_id: string
  budget_name: string
  budget_amount: number
  period: 'monthly' | 'yearly'
  start_date: string
  end_date: string | null
  is_active: boolean
  total_spent: number
}
```

- [ ] **Step 6.8.2: Verify, fix consumers, commit**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  pnpm tsc --noEmit 2>&1 | tail -20 && \
  git add -u && \
  git commit -m "refactor(types): budget.types extends generated; preserve projections"
```

---

### Task 6.9 — Refactor `transaction.types.ts` (preserve projections)

**Repo:** `/Users/nily/Documents/Tech/budget-app/personal-budget/`

**Files:**
- Modify: `src/types/transaction.types.ts`

- [ ] **Step 6.9.1: Apply**

Replace contents of `/Users/nily/Documents/Tech/budget-app/personal-budget/src/types/transaction.types.ts`:

```typescript
import type { Tables } from '@/lib/supabase/types'

export type Transaction = Tables<'transactions'>

// Domain projection — output of joined fetch with categories.
export interface TransactionWithCategory {
  amount: number
  budget_id?: string
  category_id: string
  category_type: 'income' | 'expense'
  color: string
  description: string
  icon: string
  id: string
  is_recurring?: boolean
  name: string
  transaction_date: string
}

// UI filter shape — not a row.
export interface TransactionFilters {
  accountId?: string
  categoryId?: string
  endDate?: string
  page?: number
  pageSize?: number
  startDate?: string
  type?: 'income' | 'expense'
}

// Generic pagination wrapper — not a row.
export interface PaginatedResponse<T> {
  data: Array<T>
  hasMore: boolean
  total: number
}
```

> The original hand-rolled `Transaction.transaction_date` was `Date | string`. The generated row will have `string` only. **If any consumer passes a `Date` instance to `.from('transactions').insert(...)`, TS will now error.** Fix by converting at the call site: `transaction_date: someDate.toISOString()`.

- [ ] **Step 6.9.2: Verify, fix consumers, commit**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  pnpm tsc --noEmit 2>&1 | tail -30 && \
  git add -u && \
  git commit -m "refactor(types): transaction.types extends generated; preserve projections"
```

---

### Task 6.10 — Refactor `profile.types.ts` (camelCase mismatch — special case)

**Repo:** `/Users/nily/Documents/Tech/budget-app/personal-budget/`

**Files:**
- Modify: `src/types/profile.types.ts`

> The existing `Profile` interface uses camelCase fields (`avatarUrl`, `createdAt`, `fullName`, `userId`) that **don't match** the DB columns. The generated `Tables<'profiles'>` will have snake_case (`avatar_url`, `created_at`, `full_name`, `user_id`).
>
> Two paths:
> - **(a)** Refactor consumers to use snake_case (`profile.full_name`).
> - **(b)** Keep the camelCase shape and document a runtime mapper.
>
> This plan picks **(a)** because (b) hides the mapping and rots. Refactoring consumers is mechanical.

- [ ] **Step 6.10.1: Apply the type change**

Replace contents of `/Users/nily/Documents/Tech/budget-app/personal-budget/src/types/profile.types.ts`:

```typescript
import type { Tables } from '@/lib/supabase/types'

export type Profile = Tables<'profiles'>
```

- [ ] **Step 6.10.2: Capture all camelCase consumers**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  grep -rn "\.\(avatarUrl\|createdAt\|fullName\|userId\|updatedAt\)\b" src/ --include="*.ts" --include="*.tsx" | \
  grep -i profile
```

Each match is a consumer reading a Profile field by camelCase name.

- [ ] **Step 6.10.3: Rewrite each consumer**

| camelCase | snake_case |
|---|---|
| `profile.avatarUrl` | `profile.avatar_url` |
| `profile.createdAt` | `profile.created_at` |
| `profile.fullName` | `profile.full_name` |
| `profile.userId` | `profile.user_id` |
| `profile.updatedAt` | `profile.updated_at` |

Apply per-file edits. Run `pnpm tsc --noEmit` after each file to confirm progress.

> If `email` and `id` were also fields, they're already snake-case-compatible (single words), no change.

- [ ] **Step 6.10.4: Verify clean compile**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  pnpm tsc --noEmit
```

Expected: error count back to baseline.

- [ ] **Step 6.10.5: Commit**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  git add -u && \
  git commit -m "refactor(types): profile.types snake_case to match DB

Was hand-rolled with camelCase (avatarUrl, fullName, userId) which
silently disagreed with DB columns. Generated Tables<'profiles'> uses
snake_case; consumers updated to match."
```

---

### Task 6.11 — Verify `index.ts` and `database.types.ts` barrels still re-export everything

**Repo:** `/Users/nily/Documents/Tech/budget-app/personal-budget/`

**Files:**
- Verify (no change expected): `src/types/index.ts`, `src/types/database.types.ts`

- [ ] **Step 6.11.1: Confirm barrel exports compile**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  pnpm tsc --noEmit 2>&1 | grep -E "(index|database)\.types?\.ts"
```

Expected: no output (no errors in barrels).

- [ ] **Step 6.11.2: Run full build to catch anything stragglers**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  pnpm build
```

Expected: clean build. If errors, they're real consumer errors that must be fixed. Fix them and amend the most recent type-refactor commit, or commit fixes as a separate `fix(types):` commit.

- [ ] **Step 6.11.3: Run tests**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  pnpm test
```

Expected: green. Vitest catches runtime regressions the types missed.

- [ ] **Step 6.11.4: Smoke test in dev mode (manual)**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  pnpm dev
```

Open the app, log in, navigate: budgets, transactions, debts, goals, settings. Watch the console for errors. Anything red = consumer bug from the refactor; fix and commit.

- [ ] **Step 6.11.5: Open the PR**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  git push -u origin types/hybrid-refactor && \
  gh pr create --title "refactor(types): hybrid model — generated rows + hand-rolled projections" \
    --body "Phase 6 of schema consolidation. Each type file now imports
    Tables<'tablename'> from src/lib/supabase/types.ts. Domain projections
    (BudgetWithProgress, TransactionWithCategory, GoalWithProgress, etc.)
    stay hand-rolled. Profile refactored snake_case to match DB columns —
    affected consumers updated.

    Locally: pnpm build green, pnpm test green, smoke-tested in dev."
```

After review, merge:
```bash
gh pr merge --squash && git checkout main && git pull
```

---

## Phase 7 — Decommission frontend `supabase/` directory

### Task 7.1 — Confirm backend has run an apply-migrations cycle on main

**Repo:** `/Users/nily/Documents/Tech/budget-app/insights-engine/`

- [ ] **Step 7.1.1: Verify the backend's most recent main-push workflow has all three jobs green**

```bash
cd /Users/nily/Documents/Tech/budget-app/insights-engine && \
  gh run list --workflow=ci.yml --branch=main --limit=5
```

The most recent successful run must show `lint-and-test`, `apply-migrations`, and `regen-types` all green.

If `apply-migrations` was a no-op (`No new migrations to apply.`), that's the desired state.

If anything is yellow/red, **stop**. The backend is the source of truth now; if its CI is broken, deleting the frontend's `supabase/` removes our last fallback.

---

### Task 7.2 — Delete the frontend `supabase/` directory

**Repo:** `/Users/nily/Documents/Tech/budget-app/personal-budget/`

**Files:**
- Delete: `supabase/migrations/*.sql` (12 files)
- Delete: `supabase/` (entire directory)

- [ ] **Step 7.2.1: Branch**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  git checkout main && git pull && \
  git checkout -b cleanup/remove-supabase-dir
```

- [ ] **Step 7.2.2: Confirm what's there**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  ls -la supabase/ && ls -la supabase/migrations/
```

- [ ] **Step 7.2.3: Remove**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  git rm -r supabase/
```

- [ ] **Step 7.2.4: Verify nothing in the frontend references the old folder**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  grep -rn "supabase/migrations\|supabase/config" --exclude-dir=node_modules --exclude-dir=dist
```

Expected: no matches (the migrations and config were never imported by app code — they were Supabase CLI artefacts).

- [ ] **Step 7.2.5: Build + test once more**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  pnpm build && pnpm test
```

Expected: both green.

- [ ] **Step 7.2.6: Commit**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  git add -u && \
  git commit -m "chore(db): remove supabase/ — schema lives in insights-engine

Backend (insights-engine) now owns the schema. The CI pipeline there
applies migrations on merge to main and PRs regenerated types into
src/lib/supabase/types.ts here. This directory is no longer needed."
```

- [ ] **Step 7.2.7: Open PR + merge**

```bash
cd /Users/nily/Documents/Tech/budget-app/personal-budget && \
  git push -u origin cleanup/remove-supabase-dir && \
  gh pr create --title "chore(db): remove supabase/ — schema migrated to insights-engine" \
    --body "Phase 7. Final step of the schema consolidation. Migrations and Supabase CLI config now live in insights-engine. CI there applies + regenerates types and PRs them into src/lib/supabase/types.ts."
gh pr merge --squash && git checkout main && git pull
```

---

## Acceptance criteria

- [ ] Backend has `.github/workflows/ci.yml` with three jobs: `lint-and-test`, `apply-migrations` (main only), `regen-types` (main only).
- [ ] Backend has `supabase/config.toml` and 12 migrations under `supabase/migrations/`.
- [ ] `supabase migration list --linked` from backend shows all 12 with Local + Remote populated.
- [ ] First post-merge workflow on main has all three jobs green, with `apply-migrations` reporting "No new migrations to apply."
- [ ] `personal-budget/src/lib/supabase/types.ts` exists and exports `Database`.
- [ ] `personal-budget/src/lib/supabaseClient.ts` uses `createClient<Database>(...)`.
- [ ] All 8 refactored `*.types.ts` files import from `@/lib/supabase/types`.
- [ ] `personal-budget` `pnpm build` and `pnpm test` are both green.
- [ ] `personal-budget/supabase/` no longer exists.
- [ ] No grep hit for `supabase/migrations` in `personal-budget/src/`.
- [ ] Render's auto-deploy of the backend still triggers on push to main (verify with one synthetic backend commit after Phase 7).
- [ ] AWS S3+CloudFront deploy of the frontend still triggers on push to main (verify with one synthetic frontend commit after Phase 7).

## Out of scope

- Pinning Supabase CLI version in the workflow file: Step 3.2.1 captures the version; the engineer fills in the literal in 3.2.2 and 4.2.1.
- PAT rotation: 90-day expiry. Add a calendar reminder.
- Migrating `insights.types.ts`, `selectOptions.types.ts`, `user.ts` — none of these are DB-row types; out of scope.
- Account deletion implementation — separate plan (`docs/superpowers/plans/2026-04-29-account-deletion.md`). Its Phase 1 migration (`20260429000000_account_deletion.sql`) lands as a normal new migration in `insights-engine/supabase/migrations/` after this consolidation completes.
