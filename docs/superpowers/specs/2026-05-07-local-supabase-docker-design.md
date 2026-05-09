# Local Supabase + API in Docker — Design

**Date:** 2026-05-07
**Status:** Draft (awaiting user review)
**Author:** brainstorm with nily

## Problem

Two pain points:

1. **Local dev** — devs run the FastAPI service on the host (or in a partial setup) while pointing at a hosted Supabase project, or at `supabase start` with mismatched env vars. Every new contributor has to wire URLs, anon keys, and Clerk env by hand. There is no one-command spin-up of the whole stack.
2. **CI** — the GitHub Actions workflow runs unit tests with fakes (`tests/conftest.py:14-17` autouse env defaults), so a broken migration or a wiring regression between the API and PostgREST is only caught after deploy. We want a hermetic smoke that brings up Postgres + PostgREST + the API and verifies (a) migrations apply cleanly and (b) the API can reach Supabase.

Goals diverge enough that one mechanism cannot serve both well: dev wants Studio, hot-reload, `supabase db reset`, and migration tooling; CI wants a single `docker compose up --wait` with no extra binaries on the runner.

## Goal

Add a hybrid local-Docker setup that:

- For **local dev**: keeps `supabase start` (CLI-managed Supabase containers) and adds a thin `docker-compose.yml` that runs only the API container, connecting to host-side Supabase.
- For **CI**: provides a self-contained `docker-compose.ci.yml` that declares Postgres + PostgREST + a one-shot migration runner + the API, with no Supabase CLI dependency. CI hits `GET /health` and a new `GET /health/db` and tears down.

Out of scope (deferred):

- Integration tests that issue authenticated requests (requires a test JWKS or auth-bypass mode — not designed here).
- Self-hosted Supabase production stack (Kong, GoTrue, Storage, Realtime, Edge Functions, Studio). CI runs only what `/health/db` actually exercises.
- Seeded fixture data for end-to-end flows.

## Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| 1 | Hybrid orchestration: `supabase start` for dev, custom compose for CI | The CLI is the best dev UX (Studio, `db reset`, migration tooling) but installing it on CI runners adds a dependency and version-couples CI to the CLI release cadence. CI gets a hermetic compose; dev gets the CLI ergonomics. |
| 2 | API runs in Docker locally too | Same packaging as CI and prod (`Dockerfile` already exists). Removes "works on my Python version" drift. Costs one `docker compose up api` step but normalises the env. |
| 3 | API container reaches Supabase via `host.docker.internal:54321` | `supabase start` exposes its services on host ports. `extra_hosts: ["host.docker.internal:host-gateway"]` makes this work on macOS Docker Desktop (no-op there) and Linux Docker Engine alike. |
| 4 | CI smoke = migrations apply + API boots + `/health/db` reaches PostgREST | Smallest scope that proves the docker-compose stack is wired correctly end-to-end. Doesn't pull us into auth/RLS territory. |
| 5 | CI Postgres image: vanilla `postgres:17-alpine` + `supabase/ci/bootstrap.sql` | Vanilla is ~80 MB vs ~1.5 GB for `supabase/postgres:17.6.1.005`; CI cold-start matters. The bootstrap is small (auth schema, `auth.jwt()` stub, `auth.users(id)` shim, three roles) and stable — only a *new* Supabase-namespace dependency in a future migration would force an edit. |
| 6 | CI migration runner = same `postgres:17-alpine` image, one-shot service running `psql` over `supabase/migrations/*.sql` | Reuses the image already pulled (no extra layer). `depends_on: { postgres: service_healthy }` and the API's `depends_on: { migrate: service_completed_successfully }` chain gives a deterministic boot order. |
| 7 | Add `GET /health/db` and `db.client.ping()` | Static `/health` proves uvicorn is up but says nothing about Supabase connectivity. The DB-touching probe catches the most valuable class of regressions for ~10 lines of code, respecting the rule that all Supabase queries live in `db/client.py`. |
| 8 | Probe target: `categories` table | System-only table per repo memory (`memory/project_categories_system_only.md`) — anon-key read is RLS-safe regardless of `auth.jwt()` returning NULL in CI. Falls back to `transactions` with `.limit(0)` if `categories` ever grows user-restrictive RLS. |
| 9 | Pin all images to the tags `supabase start` currently uses (CLI 2.98.2) | Dev and CI run identical Postgres / PostgREST builds. Concretely: `postgrest/postgrest:v13.0.5`. (Postgres tag deferred — see Decision 5; CI uses `postgres:17-alpine`, dev uses whatever `supabase start` gives.) |
| 10 | `supabase/seed.sql` runs in CI after migrations | Inserts 21 system `categories` and ~40 `service_templates`. The probe target depends on at least one row existing, and the API's reads of these tables would otherwise behave differently in CI vs dev. |
| 11 | No new tests in this iteration | The smoke is the test (CI runs the compose stack and curls the endpoints). Engine unit tests stay as-is with fakes. |
| 12 | Add an `nginx:alpine` `gateway` service in CI; API points at `http://gateway:8080` | supabase-py always constructs PostgREST calls as `{SUPABASE_URL}/rest/v1/...` (matching the real Supabase Kong layout). Without a path-stripping proxy, calls hit `/rest/v1/categories` on PostgREST, which returns `PGRST125 Invalid path`. The gateway proxies `/rest/v1/*` → `postgrest:3000/`, mirroring what Kong does in production. Adds one small service; keeps the API code identical to dev/prod. |

## End State

```text
docker-compose.yml                                NEW   (local dev: api only)
docker-compose.ci.yml                             NEW   (CI: postgres + migrate + postgrest + gateway + api)
.env.docker.example                               NEW   (committed; values for API-in-Docker dev)
.env.ci                                           NEW   (committed; CI env, no real secrets)
supabase/ci/bootstrap.sql                         NEW   (auth schema/function/users + roles stubs)
app/db/client.py                                  EDIT  (+ ping())
app/routes/health.py                              EDIT  (+ GET /health/db)
.github/workflows/ci.yml                          EDIT  (+ docker-compose-smoke job)
docs/superpowers/specs/2026-05-07-local-supabase-docker-design.md   NEW (this doc)
```

Untouched: `Dockerfile`, `supabase/config.toml`, `supabase/migrations/*`, `supabase/seed.sql`, application code outside the two files above.

## Architecture

### Local dev path

```
┌─────────────────────────────────────────────────────────┐
│  host machine                                           │
│                                                         │
│   supabase start  ──▶  postgres :54322                  │
│                        postgrest :54321  ◀──┐           │
│                        studio :54323        │           │
│                        (kong, gotrue, ...)  │           │
│                                             │           │
│   docker compose up api                     │           │
│   ┌──────────────────────────────────────┐  │           │
│   │  api container                       │  │           │
│   │  SUPABASE_URL=                       │  │           │
│   │    http://host.docker.internal:54321 │──┘           │
│   │  uvicorn :8000  ◀── host :8000                      │
│   └──────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────┘
```

Two commands: `supabase start` then `docker compose up api`. Devs interact with Studio in the browser, edit migrations in `supabase/migrations/`, run `supabase db reset` to apply changes. The API container picks up code edits via volume mount + `--reload` (TBD in plan).

### CI path

```
┌──────────────────────────────────────────────────────────────────┐
│  CI runner                                                       │
│                                                                  │
│   docker compose -f docker-compose.ci.yml up -d --wait           │
│                                                                  │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐   │
│   │ postgres │◀───│  migrate │    │ postgrest│◀───│ gateway  │   │
│   │ :5432    │    │ (one-shot│    │ :3000    │    │ :8080    │   │
│   │          │    │  psql)   │    │          │    │ /rest/v1 │   │
│   └────┬─────┘    └────┬─────┘    └────┬─────┘    └────┬─────┘   │
│        │ healthy       │ done          │ healthy       │ healthy │
│        │       ┌───────┘               │               │         │
│        │       ▼                       │               │         │
│        │   (apply SQL)                 │               │         │
│        ▼                               ▼               ▼         │
│   ┌──────────────────────────────────────────────────────────┐   │
│   │  api  :8000  (SUPABASE_URL=http://gateway:8080)          │   │
│   └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│   curl http://localhost:8000/health     → 200                    │
│   curl http://localhost:8000/health/db  → 200                    │
│   docker compose down -v                                         │
└──────────────────────────────────────────────────────────────────┘
```

All services on a private compose network. Dependency chain: `postgres` healthy → `migrate` runs to completion → `postgrest` healthy → `gateway` healthy → `api` starts. The `--wait` flag blocks until all services with healthchecks are healthy.

## Components

### `docker-compose.yml` (local dev)

Single service: `api`. Builds from `Dockerfile`. Mounts `./app:/app/app` for live reload. Reads from `.env.docker` (gitignored, copied from `.env.docker.example`). Exposes `8000:8000`. Includes the `extra_hosts` block so it works on Linux without ceremony.

The Supabase containers themselves are NOT in this file — `supabase start` owns them. The user runs both commands; the file is intentionally minimal.

### `docker-compose.ci.yml` (CI)

Five services: `postgres`, `migrate`, `postgrest`, `gateway`, `api`. The gateway is a thin nginx that proxies `/rest/v1/*` → PostgREST so supabase-py's URL pattern works without a real Kong. Skeleton (full image tags, env, volumes finalised in the plan):

```yaml
services:
  postgres:
    image: postgres:17-alpine
    environment: { POSTGRES_USER: postgres, POSTGRES_PASSWORD: postgres, POSTGRES_DB: postgres }
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U postgres"], ... }

  migrate:
    image: postgres:17-alpine
    depends_on: { postgres: { condition: service_healthy } }
    volumes:
      - ./supabase/ci/bootstrap.sql:/sql/000_bootstrap.sql:ro
      - ./supabase/migrations:/sql/migrations:ro
      - ./supabase/seed.sql:/sql/zzz_seed.sql:ro
    entrypoint: ["/bin/sh", "-eu", "-c"]
    command:
      - |
        psql -v ON_ERROR_STOP=1 -f /sql/000_bootstrap.sql
        for f in $$(ls /sql/migrations/*.sql | sort); do
          psql -v ON_ERROR_STOP=1 -f "$$f"
        done
        psql -v ON_ERROR_STOP=1 -f /sql/zzz_seed.sql

  postgrest:
    image: postgrest/postgrest:v13.0.5
    depends_on: { migrate: { condition: service_completed_successfully } }
    environment:
      PGRST_DB_URI: postgres://postgres:postgres@postgres:5432/postgres
      PGRST_DB_ANON_ROLE: anon
      PGRST_DB_SCHEMAS: public
      # JWT secret intentionally left as a benign placeholder; smoke makes
      # no authenticated requests, so PostgREST never has to verify a token.

  gateway:
    image: nginx:1.29.8-alpine
    depends_on: { postgrest: { condition: service_healthy } }
    # Inline nginx config: proxy /rest/v1/* → postgrest:3000
    healthcheck: { test: ["CMD-SHELL", "nc -z 127.0.0.1 8080"], ... }

  api:
    build: .
    depends_on: { gateway: { condition: service_healthy } }
    env_file: .env.ci
    environment:
      SUPABASE_URL: http://gateway:8080
    healthcheck: { test: ["CMD", "curl", "-f", "http://localhost:8000/health"], ... }
    ports: ["8000:8000"]
```

### `supabase/ci/bootstrap.sql`

```sql
CREATE ROLE anon NOINHERIT NOLOGIN;
CREATE ROLE authenticated NOINHERIT NOLOGIN;
CREATE ROLE service_role NOINHERIT NOLOGIN BYPASSRLS;

CREATE SCHEMA IF NOT EXISTS auth;
GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb
  LANGUAGE sql STABLE AS $$ SELECT NULL::jsonb $$;

CREATE TABLE IF NOT EXISTS auth.users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid()
);
```

### `app/db/client.py` — `ping()`

```python
def ping() -> bool:
    s = get_settings()
    client = create_client(s.supabase_url, s.supabase_anon_key)
    client.table("categories").select("id").limit(1).execute()
    return True
```

Anon-key client (no per-request user JWT). Picks `categories` because system-only — RLS-safe with NULL `auth.jwt()` in CI. Raises on failure; the route maps to 503.

### `app/routes/health.py` — `/health/db`

```python
@router.get("/health/db")
def health_db() -> dict[str, str]:
    try:
        ping()
    except Exception as e:
        logger.warning("health/db failed: %s", e)
        raise HTTPException(status_code=503, detail="db unreachable") from None
    return {"db": "ok"}
```

No auth dependency, no engine call. Detail string is generic; full error goes to logs.

### `.env.ci`

Committed (no real secrets). Just enough to boot:

```
SUPABASE_URL=http://gateway:8080
# HS256 JWT (role=anon) signed with PGRST_JWT_SECRET. Regenerate with:
#   python3 -c "import jwt; print(jwt.encode({'role':'anon'}, 'ci-smoke-not-verified-by-anonymous-requests', algorithm='HS256'))"
SUPABASE_ANON_KEY=<HS256 JWT — see comment in file>
SUPABASE_SERVICE_ROLE_KEY=ci-dummy-service-role-key
CLERK_ISSUER=https://fake.clerk.test
CLERK_SECRET_KEY=ci-dummy-clerk-secret
DELETION_COMPLETED_TEMPLATE_ID=ci-dummy-template-id
ANTHROPIC_API_KEY=ci-dummy
APP_ENV=test
CORS_ORIGINS=
```

JWKS fetch is lazy (`app/auth/jwks.py:8-12`), Supabase client construction is lazy, AI service settings are read in-function. `app/config.py` requires seven fields at boot (`clerk_issuer`, `clerk_secret_key`, `deletion_completed_template_id`, `supabase_service_role_key`, `supabase_anon_key`, `supabase_url`, plus `account_deletion_enabled` which defaults to false) — all are populated above with dummy values that satisfy the type contract. The anon key must be a real HS256 JWT signed with `PGRST_JWT_SECRET`, otherwise PostgREST rejects requests with `PGRST301`; we use a static fixture (regeneration command in the file).

### `.env.docker.example`

For local dev. Same shape as `.env.example` but with Docker-network URLs:

```
SUPABASE_URL=http://host.docker.internal:54321
SUPABASE_ANON_KEY=<paste from `supabase status`>
CLERK_ISSUER=<your dev Clerk tenant>
ANTHROPIC_API_KEY=<real>
APP_ENV=development
```

### `.github/workflows/ci.yml`

New job `docker-compose-smoke`:

1. `docker compose -f docker-compose.ci.yml up -d --wait`
2. `curl -fsS http://localhost:8000/health`
3. `curl -fsS http://localhost:8000/health/db`
4. `docker compose -f docker-compose.ci.yml logs` (always, for debugging)
5. `docker compose -f docker-compose.ci.yml down -v` (always)

Runs on every PR alongside the existing unit-test job.

## Risks and open questions

- **Gateway parity with production**: the CI gateway is `nginx`, not Kong. They share the path-stripping behavior we depend on, but the rest of Kong's behavior (rate-limiting, auth plugins, request transformation) is intentionally absent from CI. If a future bug depends on Kong-specific behavior, the CI smoke won't catch it. Dev users `supabase start` (real Kong) so the gap is CI-only.
- **`auth.jwt()` returning NULL in CI** means RLS-protected rows are invisible under the `authenticated` role. Acceptable for `/health/db` (probes `categories`, system-only) and any future unauthenticated probes. If CI ever needs to read user-owned data, the stub will need to honour `current_setting('request.jwt.claims', true)::jsonb`, or the test will need to issue requests as `service_role`.
- **PostgREST schema cache**: PostgREST caches the schema at startup. Because `migrate` runs to completion before `postgrest` starts, the cache is populated correctly. If we ever switch to running migrations *while* PostgREST is up, we'll need `NOTIFY pgrst, 'reload schema'`. Not relevant today.
- **Image-tag drift** between dev and CI: `postgrest/postgrest:v13.0.5` is pinned to what CLI 2.98.2 ships *today*. When the team upgrades the Supabase CLI, the CI compose tag must be bumped in lockstep. Worth a note in the README and ideally a Renovate rule (out of scope here).
- **`supabase/migrations/20260508025626_remove_account_deletion_request.sql`** is empty (in-progress on a different branch). Applies as a no-op in CI.
- **Hot reload in dev**: `docker compose up api` with a volume mount and `uvicorn --reload` works, but Python file-watching in mounted volumes can be flaky on macOS. If this bites, fall back to running the API on the host for local dev (Docker only for parity smoke). Decided in the plan, not here.

## Migration / rollout

- Single PR. Adds new files, edits two (`app/db/client.py`, `app/routes/health.py`, plus `.github/workflows/ci.yml`). No existing behaviour changes; no migration to existing data; no env var renames. Rolling back is `git revert`.
- Verification before merging:
  1. Run `supabase start && docker compose up api` locally; hit `/health` and `/health/db`.
  2. Run `docker compose -f docker-compose.ci.yml up -d --wait` locally; hit both endpoints; tear down.
  3. CI passes the new smoke job on the PR.

## References

- `supabase/config.toml` — current CLI config (`major_version = 17`, port 54321 / 54322).
- `app/auth/jwks.py:8-12` — JWKS client factory; lazy fetch.
- `app/db/client.py:22-35` — `build_user_client`; only call site of `create_client`.
- `app/routes/deps.py:40,62` — only consumers of JWKS and Supabase client.
- `app/config.py:18-22` — Settings required fields.
- `tests/conftest.py:14-17` — autouse env defaults that let unit tests boot the app.
- Repo memory: `memory/project_categories_system_only.md` — `categories` is system-only.
