# Local Supabase + API in Docker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a hybrid Docker setup — `supabase start` + a thin API compose for local dev, and a self-contained `docker-compose.ci.yml` for CI — so any contributor can spin up the full stack with one command, and CI catches migration / wiring regressions via a `/health/db` smoke.

**Architecture:** Two compose files share one source of truth (`supabase/migrations/`). Local dev uses the Supabase CLI for the data plane and adds a single API container that reaches Supabase via `host.docker.internal:54321`. CI runs a minimal Postgres + PostgREST + one-shot migration runner + API stack on a private Docker network, applies migrations via `psql` plus a small bootstrap that stubs Supabase auth artifacts, then verifies `/health` and a new `/health/db` endpoint.

**Tech Stack:** FastAPI 0.115+, supabase-py 2.x, Docker Compose v2, `postgres:17-alpine`, `postgrest/postgrest:v13.0.5`, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-05-07-local-supabase-docker-design.md`

---

## File Structure

Files this plan creates or modifies:

| Path | Status | Responsibility |
|---|---|---|
| `app/db/client.py` | modify | Add `ping()` — anon-key reachability probe for PostgREST. |
| `app/routes/health.py` | modify | Add `GET /health/db` route that calls `ping()`. |
| `supabase/ci/bootstrap.sql` | create | SQL stubs for `auth` schema, `auth.jwt()`, `auth.users`, and the three Supabase roles. |
| `docker-compose.ci.yml` | create | CI stack: `postgres` + `migrate` + `postgrest` + `api`. |
| `.env.ci` | create | Committed CI env (no real secrets). |
| `docker-compose.yml` | create | Local dev: API container only; talks to host's `supabase start`. |
| `.env.docker.example` | create | Template for local-dev API env (gitignored real one is `.env.docker`). |
| `.gitignore` | modify | Ignore `.env.docker`. |
| `.github/workflows/ci.yml` | modify | Add `docker-compose-smoke` job. |

---

## Task 1: Add `ping()` to `app/db/client.py`

**Files:**
- Modify: `app/db/client.py` (add `ping()` near `build_user_client`)

- [ ] **Step 1: Add the `ping()` function**

Insert this block in `app/db/client.py` immediately after the `build_user_client` function (after line 35, before `def fetch_transactions`):

```python
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
```

`get_settings` and `create_client` are already imported at the top of the file (lines 3 and 5) — no new imports needed.

- [ ] **Step 2: Verify the file parses**

Run: `python -c "from app.db.client import ping; print('ok')"`
Expected output: `ok`

- [ ] **Step 3: Verify existing tests still pass**

Run: `pytest --deselect tests/test_deps.py -q`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add app/db/client.py
git commit -m "feat(db): add ping() reachability probe for PostgREST"
```

---

## Task 2: Add `GET /health/db` route

**Files:**
- Modify: `app/routes/health.py`

- [ ] **Step 1: Replace the contents of `app/routes/health.py`**

Replace the entire file with:

```python
import logging

from fastapi import APIRouter, HTTPException

from app.db.client import ping

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.get("/health/db")
def health_db() -> dict[str, str]:
    try:
        ping()
    except Exception as e:
        logger.warning("health/db failed: %s", e)
        raise HTTPException(status_code=503, detail="db unreachable") from None
    return {"db": "ok"}
```

- [ ] **Step 2: Boot the app locally and confirm `/health` still works**

Run (in one terminal): `uvicorn app.main:app --port 8000`
In another terminal: `curl -s http://localhost:8000/health`
Expected: `{"ok":true}`

Stop uvicorn (Ctrl-C). We won't curl `/health/db` here because there's no Supabase running yet — that's what Task 6 verifies end-to-end.

- [ ] **Step 3: Run lint + tests**

Run: `ruff format --check app && ruff check app && pytest --deselect tests/test_deps.py -q`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add app/routes/health.py
git commit -m "feat(routes): add GET /health/db endpoint"
```

---

## Task 3: Create `supabase/ci/bootstrap.sql`

**Files:**
- Create: `supabase/ci/bootstrap.sql`

This file stubs the four Supabase artifacts that `00000000000000_initial_schema.sql` depends on but vanilla Postgres lacks: the `auth` schema, an `auth.jwt()` function returning NULL, an `auth.users(id uuid)` shim (FK target on `categories`), and three roles (`anon`, `authenticated`, `service_role`).

- [ ] **Step 1: Create the directory**

Run: `mkdir -p supabase/ci`

- [ ] **Step 2: Write the file**

Create `supabase/ci/bootstrap.sql` with these contents:

```sql
-- Bootstrap stubs for CI Postgres (vanilla postgres:17-alpine).
-- Creates the minimum Supabase artifacts that the squashed initial
-- schema depends on. Production / dev use the real Supabase Postgres
-- image and never run this file.

CREATE ROLE anon NOINHERIT NOLOGIN;
CREATE ROLE authenticated NOINHERIT NOLOGIN;
CREATE ROLE service_role NOINHERIT NOLOGIN BYPASSRLS;

CREATE SCHEMA IF NOT EXISTS auth;
GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;

-- Stub: returns NULL in CI (no real JWT). RLS policies that compare
-- auth.jwt()->>'sub' to user_id won't match any rows — fine for a
-- smoke test that hits only public health endpoints.
CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb
  LANGUAGE sql STABLE AS $$ SELECT NULL::jsonb $$;

-- Stub: satisfies categories.user_id_fkey -> auth.users(id).
CREATE TABLE IF NOT EXISTS auth.users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid()
);
```

- [ ] **Step 3: Smoke-test the bootstrap against a throwaway Postgres**

Run:

```bash
docker run --rm -d --name pg-bootstrap-test \
  -e POSTGRES_PASSWORD=postgres -p 55432:5432 postgres:17-alpine
sleep 3
docker exec -i pg-bootstrap-test psql -U postgres -v ON_ERROR_STOP=1 \
  < supabase/ci/bootstrap.sql
docker exec -i pg-bootstrap-test psql -U postgres -v ON_ERROR_STOP=1 \
  < supabase/migrations/00000000000000_initial_schema.sql
docker rm -f pg-bootstrap-test
```

Expected: the bootstrap and the squashed migration both apply without errors. If the squashed migration fails, the error message will name a missing artifact; add it to the bootstrap and re-run.

- [ ] **Step 4: Commit**

```bash
git add supabase/ci/bootstrap.sql
git commit -m "feat(ci): add Supabase auth/role stubs for vanilla Postgres"
```

---

## Task 4: Create `.env.ci`

**Files:**
- Create: `.env.ci`

Committed with non-secret placeholder values. The smoke makes no authenticated requests, so dummy values for Clerk and Anthropic never have to verify.

- [ ] **Step 1: Write the file**

Create `.env.ci` with:

```
SUPABASE_URL=http://postgrest:3000
SUPABASE_ANON_KEY=ci-dummy-anon-key
CLERK_ISSUER=https://fake.clerk.test
ANTHROPIC_API_KEY=ci-dummy
APP_ENV=test
CORS_ORIGINS=
```

- [ ] **Step 2: Confirm it is NOT matched by `.gitignore`**

Run: `git check-ignore -v .env.ci || echo "not ignored — good"`
Expected: `not ignored — good`

- [ ] **Step 3: Commit**

```bash
git add .env.ci
git commit -m "feat(ci): add committed .env.ci with non-secret placeholders"
```

---

## Task 5: Create `docker-compose.ci.yml`

**Files:**
- Create: `docker-compose.ci.yml`

Four services on a private network: `postgres` (vanilla 17-alpine), `migrate` (one-shot psql), `postgrest` (image-pinned to v13.0.5 to match `supabase start`), `api` (built from this repo's `Dockerfile`).

- [ ] **Step 1: Write the file**

Create `docker-compose.ci.yml` with:

```yaml
# CI stack: hermetic, no Supabase CLI dependency.
# Brings up Postgres + PostgREST + the API; applies migrations via psql.
# Health smoke: curl /health and /health/db on localhost:8000.

services:
  postgres:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: postgres
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d postgres"]
      interval: 2s
      timeout: 3s
      retries: 20

  migrate:
    image: postgres:17-alpine
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      PGHOST: postgres
      PGUSER: postgres
      PGPASSWORD: postgres
      PGDATABASE: postgres
    volumes:
      - ./supabase/ci/bootstrap.sql:/sql/000_bootstrap.sql:ro
      - ./supabase/migrations:/sql/migrations:ro
      - ./supabase/seed.sql:/sql/zzz_seed.sql:ro
    entrypoint: ["/bin/sh", "-eu", "-c"]
    command:
      - |
        psql -v ON_ERROR_STOP=1 -f /sql/000_bootstrap.sql
        for f in $$(ls /sql/migrations/*.sql | sort); do
          echo "==> applying $$f"
          psql -v ON_ERROR_STOP=1 -f "$$f"
        done
        psql -v ON_ERROR_STOP=1 -f /sql/zzz_seed.sql

  postgrest:
    image: postgrest/postgrest:v13.0.5
    depends_on:
      migrate:
        condition: service_completed_successfully
    environment:
      PGRST_DB_URI: postgres://postgres:postgres@postgres:5432/postgres
      PGRST_DB_ANON_ROLE: anon
      PGRST_DB_SCHEMAS: public
      PGRST_JWT_SECRET: ci-smoke-not-verified-by-anonymous-requests
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:3000/ >/dev/null || exit 1"]
      interval: 2s
      timeout: 3s
      retries: 20

  api:
    build: .
    depends_on:
      postgrest:
        condition: service_healthy
    env_file: .env.ci
    environment:
      SUPABASE_URL: http://postgrest:3000
    ports:
      - "8000:8000"
    healthcheck:
      test:
        - CMD-SHELL
        - 'python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen(''http://localhost:8000/health'').status==200 else 1)"'
      interval: 3s
      timeout: 3s
      retries: 20
```

Notes on choices:
- `migrate` reuses `postgres:17-alpine` (psql is in the image already — no extra layer to pull).
- `PGRST_JWT_SECRET` is a placeholder string; PostgREST requires *some* value for the var to start, but the smoke makes no authenticated requests so the value is never used.
- The API's healthcheck uses `python` (already in the image) instead of `curl` (not in `python:3.13-slim`).

- [ ] **Step 2: Validate the compose file**

Run: `docker compose -f docker-compose.ci.yml config -q`
Expected: exit 0 (no output on success).

- [ ] **Step 3: Commit (file only — end-to-end run is the next task)**

```bash
git add docker-compose.ci.yml
git commit -m "feat(ci): add docker-compose.ci.yml smoke stack"
```

---

## Task 6: End-to-end CI smoke locally

This task is verification, not new files. We confirm the stack actually works before wiring CI.

- [ ] **Step 1: Bring the stack up**

Run: `docker compose -f docker-compose.ci.yml up -d --wait --build`

Expected: command exits 0 once all services are healthy. If it hangs or fails, jump to Step 4.

- [ ] **Step 2: Hit `/health`**

Run: `curl -fsS http://localhost:8000/health`
Expected: `{"ok":true}`

- [ ] **Step 3: Hit `/health/db`**

Run: `curl -fsS http://localhost:8000/health/db`
Expected: `{"db":"ok"}`

- [ ] **Step 4: If anything failed, dump logs**

Run: `docker compose -f docker-compose.ci.yml logs --no-color`

Common issues and fixes:
- `migrate` failed with "function auth.jwt() does not exist" → bootstrap didn't run; check the volume mount path.
- `postgrest` exits with "schema cache load failed" → migration probably failed silently; re-check `migrate` logs.
- `api` 503 on `/health/db` → PostgREST not reachable; verify `SUPABASE_URL=http://postgrest:3000` is set in the `api` container (`docker compose -f docker-compose.ci.yml exec api env | grep SUPABASE`).

Apply fixes inline (most likely a typo in the compose file or bootstrap), re-run Step 1.

- [ ] **Step 5: Tear down**

Run: `docker compose -f docker-compose.ci.yml down -v`
Expected: containers + volumes removed.

- [ ] **Step 6: Commit any fixes from Step 4 (skip if nothing changed)**

```bash
git add -A
git commit -m "fix(ci): <one-line description of the fix>"
```

---

## Task 7: Add `docker-compose-smoke` job to CI

**Files:**
- Modify: `.github/workflows/ci.yml`

The existing workflow has three jobs: `lint-and-test`, `apply-migrations` (main only), `regen-types` (main only). We add a fourth that runs on every PR alongside `lint-and-test`.

- [ ] **Step 1: Append the new job**

In `.github/workflows/ci.yml`, after the `lint-and-test` job (after line 34) and before the `apply-migrations` job (line 36), insert:

```yaml
  docker-compose-smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Bring up the stack
        run: docker compose -f docker-compose.ci.yml up -d --wait --build

      - name: Smoke /health
        run: curl -fsS http://localhost:8000/health

      - name: Smoke /health/db
        run: curl -fsS http://localhost:8000/health/db

      - name: Dump logs (always)
        if: always()
        run: docker compose -f docker-compose.ci.yml logs --no-color

      - name: Tear down (always)
        if: always()
        run: docker compose -f docker-compose.ci.yml down -v
```

- [ ] **Step 2: Validate the YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`
Expected: no output (success).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "feat(ci): add docker-compose smoke job to CI workflow"
```

---

## Task 8: Create `docker-compose.yml` for local dev (API only)

**Files:**
- Create: `docker-compose.yml`

This file runs ONLY the API. The Supabase data plane is owned by `supabase start` on the host. Live-reload uses a volume mount + `--reload`.

- [ ] **Step 1: Write the file**

Create `docker-compose.yml` with:

```yaml
# Local dev: runs only the API container.
# Run `supabase start` separately to bring up the Supabase data plane;
# the API reaches it on the host via host.docker.internal:54321.

services:
  api:
    build: .
    env_file: .env.docker
    ports:
      - "8000:8000"
    extra_hosts:
      # macOS: no-op (Docker Desktop maps host.docker.internal natively).
      # Linux: required — `host-gateway` resolves to the host's gateway IP.
      - "host.docker.internal:host-gateway"
    volumes:
      # Live-reload: mount the source so uvicorn --reload picks up edits.
      - ./app:/app/app:ro
    command:
      - sh
      - -c
      - "uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
```

- [ ] **Step 2: Validate the compose file**

Run: `docker compose -f docker-compose.yml config -q`
Expected: exit 0. (You may see a warning that `.env.docker` doesn't exist yet — that's fine; we create the example next.)

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(dev): add docker-compose.yml for API-only local dev"
```

---

## Task 9: Create `.env.docker.example`

**Files:**
- Create: `.env.docker.example`

The committed template. Each dev copies it to `.env.docker` (gitignored — done in Task 10) and fills in their own Clerk + Anthropic values plus the anon key from `supabase status`.

- [ ] **Step 1: Write the file**

Create `.env.docker.example` with:

```
# Copy to .env.docker and fill in real values.
# .env.docker is gitignored — never commit it.

# Supabase services run on the host via `supabase start`.
# host.docker.internal resolves to the host on macOS (native) and Linux
# (via the extra_hosts entry in docker-compose.yml).
SUPABASE_URL=http://host.docker.internal:54321

# From: `supabase status` → "anon key"
SUPABASE_ANON_KEY=

# Your Clerk dev tenant: dashboard → JWT Templates → supabase → Issuer
CLERK_ISSUER=

# AI provider — see .env.example for the full model list
AI_MODEL=anthropic/claude-haiku-4-5-20251001
ANTHROPIC_API_KEY=

APP_ENV=development
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```

- [ ] **Step 2: Commit**

```bash
git add .env.docker.example
git commit -m "feat(dev): add .env.docker.example template for API-in-Docker dev"
```

---

## Task 10: Update `.gitignore` to ignore `.env.docker`

**Files:**
- Modify: `.gitignore`

The current `.gitignore` ignores `.env`, `.env.backup.*`, `.env.*.backup`, `.env.bak`, `.env.local`. None of these match `.env.docker`. We need to add it explicitly. (We do NOT add `.env.ci` — that file is committed.)

- [ ] **Step 1: Add the line**

Edit `.gitignore`. Find this block:

```
.env
.env.backup.*
.env.*.backup
.env.bak
.env.local
```

Add `.env.docker` so the block reads:

```
.env
.env.backup.*
.env.*.backup
.env.bak
.env.local
.env.docker
```

- [ ] **Step 2: Verify**

Run: `git check-ignore -v .env.docker && git check-ignore -v .env.ci || echo "ci NOT ignored — correct"`
Expected: `.env.docker` is reported as ignored (with the gitignore line number); the second command prints `ci NOT ignored — correct`.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore .env.docker (per-dev local-Docker env)"
```

---

## Task 11: Manual verification of local dev path

Final verification before the PR. No new files; just confirm the dev workflow works on this machine.

- [ ] **Step 1: Ensure `supabase start` is running**

Run: `supabase status`
If "supabase local development setup is not running": `supabase start`. Wait for it to finish.

- [ ] **Step 2: Create `.env.docker` from the example**

Run: `cp .env.docker.example .env.docker`

Open `.env.docker` and fill in:
- `SUPABASE_ANON_KEY` — paste from `supabase status` output (the "anon key" value).
- `CLERK_ISSUER` — your Clerk dev tenant's issuer URL.
- `ANTHROPIC_API_KEY` — your real key.

- [ ] **Step 3: Bring up the API container**

Run: `docker compose up -d --build api`

Wait ~5s for uvicorn to boot. Check: `docker compose ps`
Expected: `api` is `Up`.

- [ ] **Step 4: Hit both endpoints**

```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/health/db
```
Expected: `{"ok":true}` and `{"db":"ok"}`. The second confirms the API container reached the host's PostgREST through `host.docker.internal:54321`.

- [ ] **Step 5: Tear down**

Run: `docker compose down`
Run (optional): `supabase stop`

- [ ] **Step 6: Confirm CI smoke still passes**

Run: `docker compose -f docker-compose.ci.yml up -d --wait --build && \
       curl -fsS http://localhost:8000/health && \
       curl -fsS http://localhost:8000/health/db && \
       docker compose -f docker-compose.ci.yml down -v`
Expected: both curls return 200; final teardown is clean.

- [ ] **Step 7: Push and open PR**

```bash
git push -u origin <current-branch>
gh pr create --title "feat: local Supabase + API in Docker (hybrid dev/CI)" \
  --body "Implements docs/superpowers/specs/2026-05-07-local-supabase-docker-design.md"
```

The CI run should include the new `docker-compose-smoke` job alongside `lint-and-test`. Both must pass before merge.
