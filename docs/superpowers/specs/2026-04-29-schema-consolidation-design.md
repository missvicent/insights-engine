# Schema Consolidation — Design

**Date:** 2026-04-29
**Status:** Approved (brainstorming) — pending implementation
**Owner:** <vicentbnf@gmail.com>

## Goal

Move ownership of the Supabase schema (migrations + generated types) from
the frontend repo (`personal-budget`) to the backend repo
(`insights-engine`). Establish a CI pipeline that applies migrations on
merge, regenerates TypeScript row types, and opens a pull request in the
frontend with the updated types. Refactor the frontend's hand-rolled type
files to extend from the auto-generated row shapes (hybrid model), so
domain projections like `BudgetWithProgress` survive while raw row types
become a single source of truth.

## Non-goals

- Touching the destructive logic of any existing migration. Files move
  byte-for-byte; no rewrites.
- Setting up a deployment pipeline in GitHub Actions. Render keeps
  deploying the backend; AWS S3+CloudFront keeps deploying the frontend.
  GH Actions runs validation and schema side-effects only.
- Migrating the application *before* the consolidation lands — account
  deletion (`b7975b1`) targets the backend's `supabase/migrations/` so it
  benefits from this work but doesn't depend on it executing first.
- Restructuring into a monorepo or a dedicated `db/` repo. Two repos with
  clear schema ownership in the backend is the chosen end state.

## Architectural decisions

| Decision | Choice | Why |
|---|---|---|
| Schema location | `insights-engine/supabase/migrations/` | Backend owns the contract: RLS policies, RPCs, `SECURITY DEFINER` functions. |
| Type strategy | Hybrid: generated row types + hand-rolled domain projections | Single source of truth for tables; preserves derived types (`BudgetWithProgress`, `TransactionWithCategory`, etc.). |
| Migration application | `supabase db push` from backend GH Actions on merge to `main` | Reproducible, audit trail, no manual dashboard work. Render keeps deploying separately. |
| Type sync FE/BE | PR bot from backend CI opens PR in `personal-budget` | Frontend always sees a human-reviewable diff before merging. No build-time live-DB dependency. |
| Backwards compat with live DB | Preserve migration filenames + use `supabase migration list` to baseline | `supabase_migrations.schema_migrations` keys on the timestamp prefix — moving files with intact names lets the CLI recognise them as already-applied. |
| Frontend deployment | Unchanged (`deploy.yml` → AWS S3 + CloudFront) | Out of scope. |
| Backend deployment | Unchanged (Render auto-deploy on push to `main`) | GH Actions does NOT deploy. It validates and side-effects only. |

## Repos and their roles

```text
insights-engine/                  ← BACKEND (owns schema)
├── supabase/
│   ├── config.toml               ← supabase init; project_id linked
│   └── migrations/
│       ├── 20260110044241_get_budgets_with_progress.sql   (moved)
│       ├── ... (11 more, all preserving original filenames)
│       └── 20260429000000_account_deletion.sql            (new — already in plan)
├── .github/workflows/
│   ├── ci.yml                    ← lint + test + supabase db push + types PR bot
│   └── (no deploy workflow — Render handles deploy)
├── app/                          (FastAPI, unchanged)
└── docs/superpowers/...

personal-budget/                  ← FRONTEND (consumes types)
├── supabase/
│   └── (DELETED — config.toml + migrations folder removed)
├── src/
│   ├── lib/supabase/types.ts     ← NEW: auto-generated, committed via PR bot
│   ├── lib/supabaseClient.ts     ← updated to createClient<Database>(...)
│   └── types/
│       ├── account.types.ts      ← rewrites to extend Tables<'accounts'>
│       ├── budget.types.ts       ← rewrites; keeps BudgetWithProgress projection
│       ├── ... (each *.types.ts updated to import from generated file)
│       └── database.types.ts     ← unchanged (still the barrel export)
├── .github/workflows/
│   └── deploy.yml                (unchanged)
└── package.json
```

## CI design (backend, new)

`.github/workflows/ci.yml` runs on every push to a branch and every PR:

1. **lint + test (always run)**
   - Ruff format check + ruff lint
   - pytest

2. **db push (main branch only, post-merge)**
   - Install Supabase CLI
   - Authenticate via `SUPABASE_ACCESS_TOKEN` secret
   - `supabase link --project-ref ${{ secrets.SUPABASE_PROJECT_REF }}`
   - `supabase db push` — applies any new migration files; no-op for already-applied ones

3. **types regen (main branch only, post-merge, depends on db push)**
   - `supabase gen types typescript --linked > /tmp/database.ts`
   - Compute hash; only proceed if file changed vs the last published version
   - Commit the file to a new branch in `personal-budget` and open a PR via `peter-evans/create-pull-request@v6`
   - Uses a fine-grained PAT (`FRONTEND_REPO_TOKEN`) scoped to `pull-requests:write` + `contents:write` on `personal-budget` only

The PR bot is gated on the file actually changing — running on a feature merge that didn't touch schema is a no-op (no PR opened).

## CI failure modes and how the pipeline handles them

| Failure | Behaviour |
|---|---|
| Migration syntax error | `supabase db push` fails; pipeline red; PR bot doesn't run; live DB untouched |
| Live DB drifts from migration history | `supabase db push` reports diff; pipeline red. Fix: human investigates, runs `supabase db pull` to capture, fixes the migrations folder. |
| Types unchanged | Hash matches; PR bot exits cleanly with no PR opened |
| PR bot can't push | PAT permissions wrong; pipeline red but live DB already updated. Manual recovery: regen types locally, commit, push directly. |
| GitHub Actions down | Render deploy continues; types drift until next migration merge. Acceptable — types are not security-critical. |

## Hybrid type refactor

The generated `database.ts` from `supabase gen types typescript` produces a
single `Database` type with `public.Tables`, `public.Views`, `public.Functions`.
Helper aliases via `Tables<'accounts'>`.

Each existing `src/types/*.types.ts` file refactors as follows. Example —
`account.types.ts`:

**Before** (hand-rolled):
```ts
export type Account = {
  id: string
  user_id: string
  name: string
  // ...
}
export type CreateAccount = Omit<Account, 'id'>
export type UpdateAccount = Partial<Account>
```

**After** (hybrid):
```ts
import type { Tables, TablesInsert, TablesUpdate } from '@/lib/supabase/types'

export type Account = Tables<'accounts'>
export type CreateAccount = TablesInsert<'accounts'>
export type UpdateAccount = TablesUpdate<'accounts'>
```

Domain projections stay hand-rolled. Example — `budget.types.ts`:

```ts
import type { Tables } from '@/lib/supabase/types'

export type Budget = Tables<'budgets'>
export type Allocation = Tables<'allocations'>

// Domain projection — output of a join / RPC, not a raw row.
export type BudgetWithProgress = Budget & {
  total_spent: number
  pct_used: number
  is_over_budget: boolean
}

export type BudgetOverview = {
  // ... whatever the get_budgets_overview RPC returns
}
```

`database.types.ts` (the barrel) stays unchanged — same exports, same names.
Consumers don't change.

## supabaseClient.ts update

**Before:**
```ts
import { createClient } from '@supabase/supabase-js'

export function createSupabaseClient(getToken: () => Promise<string | null>) {
  return createClient(
    import.meta.env.VITE_SUPABASE_URL,
    import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY,
    { async accessToken() { return await getToken() } },
  )
}
```

**After:**
```ts
import { createClient } from '@supabase/supabase-js'
import type { Database } from '@/lib/supabase/types'

export function createSupabaseClient(getToken: () => Promise<string | null>) {
  return createClient<Database>(
    import.meta.env.VITE_SUPABASE_URL,
    import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY,
    { async accessToken() { return await getToken() } },
  )
}
```

That single generic threads typing through every `.from('table')` call.

## Migration move procedure

The 12 existing migrations have already been applied to the live DB. The
`supabase_migrations.schema_migrations` table tracks them by timestamp
prefix. To avoid re-applying:

1. Locally, `supabase init` in `insights-engine` (creates `supabase/config.toml`).
2. `supabase link --project-ref <ref>` — connects local config to live project.
3. `git mv personal-budget/supabase/migrations/*.sql insights-engine/supabase/migrations/` (preserving filenames exactly).
4. Run `supabase migration list` against the live project — confirms each file's prefix is already in `schema_migrations`.
5. If any drift, run `supabase db pull` to baseline a fresh migration capturing whatever's in the live DB but not in our files. Inspect; commit if safe.
6. Delete `personal-budget/supabase/`.

Step 4 is the integrity check. We do not run `supabase db push` until Step 4
passes — it's the difference between "we moved files" and "we re-applied
production migrations."

## Secrets

| Secret | Where | Purpose |
|---|---|---|
| `SUPABASE_ACCESS_TOKEN` | GitHub repo secrets (`insights-engine`) | `supabase login` for CI |
| `SUPABASE_PROJECT_REF` | GitHub repo variables (`insights-engine`) | `supabase link` target |
| `SUPABASE_DB_PASSWORD` | GitHub repo secrets (`insights-engine`) | required by `supabase db push` |
| `FRONTEND_REPO_TOKEN` | GitHub repo secrets (`insights-engine`) | fine-grained PAT, scoped to `pull-requests:write` + `contents:write` on `personal-budget` only. Never used for write-back to `insights-engine` itself. |

## Test approach

- **Migration move integrity:** locally run `supabase migration list`; assert all 12 prefixes appear as applied.
- **Backend CI lint+test:** `pytest -q` and `ruff check` must stay green throughout the consolidation.
- **Type-gen smoke test:** locally run `supabase gen types typescript --linked` and confirm a `Database` type is emitted with all 12 tables.
- **Frontend type compile:** after the hybrid refactor, `pnpm run build` must succeed end-to-end. `tsc` is the source of truth for "did we break anything."
- **Frontend runtime smoke:** `pnpm dev`, log in, navigate budgets/transactions/goals/debt — visual confirmation no Pydantic-style runtime errors from changed types.
- **PR bot dry-run:** bump a column type in a throwaway migration on a branch, push, watch CI, confirm the PR opens against `personal-budget`. Close the PR + revert the migration.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Migration drift between filesystem and live DB | Step 4 + 5 of move procedure baselines via `supabase db pull` |
| PAT leak | Fine-grained PAT scoped to one repo, expires in 90 days, rotated via dashboard |
| Hybrid refactor breaks an import | TypeScript compile + frontend smoke test catch this; barrel export preserved means import paths in consumers don't change |
| Account deletion plan and consolidation collide | Account deletion plan already targets `insights-engine/supabase/migrations/` — landing consolidation first means the directory exists; landing consolidation second is a clean merge |
| Render deploy fires before GH Actions migration applies | Acceptable — Render only redeploys backend code; if a backend release expects a column that hasn't been applied yet, that's a coordination bug we'd catch in PR review. Consider gating on schema-touching backend changes: comment in PR description noting the migration must merge first. |
| Supabase CLI version drift between local and CI | Pin CLI version in CI workflow (`uses: supabase/setup-cli@v1` with `version: 1.x.y`) |

## What we deliberately don't do

- Don't introduce a dedicated `db/` repo. Two services, one schema owner — backend is sufficient.
- Don't replace Render with GitHub Actions deploy. Two CIs serving different purposes.
- Don't auto-merge the PR bot's PR into the frontend. A human reviews schema changes.
- Don't run `supabase db push` from local dev. Migrations land via PR + CI only.

## Phased execution

1. **Phase 1 — Backend CI bootstrap.** Create `.github/workflows/ci.yml` with lint + test only. Verify on a no-op branch. Commit.
2. **Phase 2 — Migration consolidation.** Locally `supabase init` + `supabase link`. Move the 12 SQL files. Run `supabase migration list` to verify. Commit.
3. **Phase 3 — Add `supabase db push` to CI.** Set the three secrets. Test on a throwaway migration branch (add + remove a comment-only migration to confirm push runs).
4. **Phase 4 — Add types-regen + PR bot to CI.** Set `FRONTEND_REPO_TOKEN`. Test by re-triggering the workflow on Phase 3's commit; confirm PR opens (or no-op if types unchanged).
5. **Phase 5 — Generated types in frontend.** Merge the bot's first PR (or commit `src/lib/supabase/types.ts` manually if PR didn't open).
6. **Phase 6 — Hybrid type refactor.** Update `supabaseClient.ts` to `createClient<Database>`. Refactor all 11 type files to import from generated. `pnpm build` + smoke test.
7. **Phase 7 — Decommission frontend supabase folder.** `rm -rf personal-budget/supabase/`. Commit.

## Cross-cutting blockers

| Item | Blocks |
|---|---|
| Phase 2 (move integrity check) | Phase 3 — don't push until baseline confirmed |
| Phase 3 (db push working) | Phase 4 — types regen depends on a successful link |
| Phase 5 (generated file in frontend) | Phase 6 — refactor depends on the file existing |
| Phase 7 | Final state only — do not delete frontend `supabase/` until Phase 6 builds clean |

## Open items (acknowledged, deferred)

- Pinning Supabase CLI version in CI: pick exact version when wiring Phase 3.
- PAT rotation runbook: documented when generated.
- The `account_deletion` plan's Phase 5.4 step about disabling Account Portal in Clerk dashboard remains unchanged by this consolidation.

## Approval

Approved 2026-04-29 by vicentbnf@gmail.com over the brainstorming session.
Decisions: Q1=c (hybrid types), Q2=a (CI applies migrations), Q3=a (PR bot
sync), Q4=yes (bootstrap backend GH Actions for validation only).
Implementation plan to follow via the `writing-plans` skill.
