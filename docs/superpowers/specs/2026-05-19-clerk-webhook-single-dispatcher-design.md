# Clerk Webhook — Single Dispatcher + Durable Email Outbox

**Date:** 2026-05-19
**Owner:** vicentbnf@gmail.com
**Related:**
- [2026-05-06-account-deletion-simplified-design.md](2026-05-06-account-deletion-simplified-design.md) — the deviation this spec reverses (two webhook endpoints → one).
- [2026-04-24-budget-archival-cron-design.md](2026-04-24-budget-archival-cron-design.md) — defined the `pg_cron + pg_net → internal endpoint` pattern. **Not yet shipped.** This spec ships that infrastructure for the first time; the archival cron can adopt the same plumbing when it lands.

## Problem

Two issues to fix together, because they share files:

1. **Two webhook endpoints where one suffices.** `app/routes/webhooks_clerk.py:24-122` exposes `POST /webhooks/clerk/welcome` and `POST /webhooks/clerk/delete_account`. Each duplicates the Svix-verify + idempotency preamble. The original account-deletion spec called for a single dispatcher; the split was a deliberate deviation ([plan note](../plans/2026-05-06-account-deletion-simplified.md#L63-L69)) that has not paid off.
2. **Email delivery is lossy.** Both endpoints (and `deletion_service.delete_account` step 5) use `BackgroundTasks` or a synchronous Resend call. If the process is killed between the DB work and the Resend HTTP call, or if Resend itself fails transiently, the email is silently lost. There is no retry, no record, no operator visibility. For an account-deleted receipt (compliance-adjacent) and a welcome email (first impression), silent loss is not acceptable.

## Goals

- One Clerk webhook URL: `POST /webhooks/clerk` dispatches on `event.type`.
- **No silently lost emails** for `welcome` or `account_deleted`, on either the webhook-driven or route-driven path.
- Fast happy path: the welcome email still arrives within seconds for the typical case (Resend up, process alive).
- Operator visibility: a stuck email is queryable in Postgres.

## Non-goals

- Adding new Clerk event subscriptions beyond `user.created` and `user.deleted`.
- Re-authoring the Resend templates themselves.
- Generalizing the outbox to non-email notifications (push, in-app).
- A web UI for the dead-letter queue. Operators read it via SQL for now.
- Shipping the archival cron in this spec — only its `/internal/...` + `CRON_SHARED_SECRET` plumbing is shared.

## Design

### Architecture overview

```
┌──────────────────────┐         ┌───────────────────────┐
│ Producer (webhook    │         │ Producer (route       │
│  /webhooks/clerk)    │         │  /account/delete)     │
└──────────┬───────────┘         └───────────┬───────────┘
           │   1. INSERT pending_emails row  │
           └─────────────────┬───────────────┘
                             │
                             ▼
                ┌──────────────────────────┐
                │ public.pending_emails    │
                │  (durable Postgres row)  │
                └──────────┬───────────────┘
                           │
        ┌──────────────────┼──────────────────────────┐
        │ fast path        │  cron-driven safety net  │
        │ (same request)   │                          │
        ▼                  ▼                          ▼
┌─────────────────┐   ┌─────────────────────────┐
│ BackgroundTask: │   │ pg_cron (every minute): │
│ try_send_       │   │   pg_net.http_post →    │
│ pending_email   │   │ /internal/emails/flush  │
│ (row_id)        │   │   (Bearer secret)       │
└────────┬────────┘   └────────────┬────────────┘
         │                         │
         └────────────┬────────────┘
                      ▼
              ┌──────────────────┐
              │ email_service    │
              │ ._send → Resend  │
              └──────────────────┘
                      │
              ┌───────┴──────────┐
              ▼                  ▼
      mark_sent(row)     mark_failed(row,
                          error, backoff)
```

### `pending_emails` table

New migration `supabase/migrations/<ts>_pending_emails.sql`:

```sql
CREATE TABLE IF NOT EXISTS public.pending_emails (
    id                 bigserial PRIMARY KEY,
    template           text NOT NULL
        CHECK (template IN ('welcome', 'account_deleted')),
    to_email           text NOT NULL,
    payload            jsonb NOT NULL DEFAULT '{}'::jsonb,
    attempts           int   NOT NULL DEFAULT 0,
    max_attempts       int   NOT NULL DEFAULT 8,
    next_run_at        timestamptz NOT NULL DEFAULT now(),
    last_attempted_at  timestamptz,
    last_error         text,
    sent_at            timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now()
);

-- Worker query: rows ready to attempt, ordered by readiness.
-- Partial index keeps it tiny once rows are marked sent.
CREATE INDEX IF NOT EXISTS idx_pending_emails_ready
    ON public.pending_emails (next_run_at)
    WHERE sent_at IS NULL;

ALTER TABLE public.pending_emails ENABLE ROW LEVEL SECURITY;
-- No policies are created. The table is service-role-only by design;
-- authenticated users must never see or modify it.
```

**Field semantics:**
- `template` — drives which Resend template id is used at send time. Constrained to the two values we actually support. Adding a third later means a migration; that's a feature, not a bug.
- `payload` — variables for the template. For both current templates the only key is `first_name` (mapped to Resend variable `USER` inside `email_service`).
- `attempts` / `max_attempts` — cap retries. Default `max_attempts=8`. With the backoff schedule below this covers ~2h of outage.
- `next_run_at` — earliest moment to retry. Initially `now()` so the first attempt is immediate.
- `sent_at` — non-null = done. The partial index excludes these rows so the worker query stays fast.
- `last_attempted_at` / `last_error` — operator-facing debugging. Last error wins; no full attempt log (YAGNI for now).

**Backoff:** when an attempt fails, `mark_pending_email_failed` increments `attempts` and sets `next_run_at = now() + (2 ^ attempts) * interval '30 seconds'`. So gaps between successive attempts are: immediate (fast path) → 60s → 2m → 4m → 8m → 16m → 32m → 64m → 128m. With `max_attempts=8` the row burns through ~4.3h of retry window before getting stuck. After `attempts >= max_attempts` the row is stuck: `sent_at IS NULL`, no further work. Operators see it via `SELECT … WHERE attempts >= max_attempts AND sent_at IS NULL`.

### Route: `POST /webhooks/clerk`

Replaces both existing handlers in `app/routes/webhooks_clerk.py`.

```
1. Read body + svix-id/timestamp/signature headers.
2. Svix verify → 401 on WebhookVerificationError.
3. sr_client = build_service_role_client()
4. if not record_webhook_event(sr_client, svix_id): log + return (204)
5. event = json.loads(payload); type = event["type"]; log
6. dispatch on type:
     "user.created":
        primary_email = <pick from email_addresses by primary_email_address_id>
        if not primary_email: log warning + return
        row_id = enqueue_email(sr_client,
                               template="welcome",
                               to_email=primary_email,
                               payload={"first_name": data.get("first_name")})
        background_tasks.add_task(try_send_pending_email, row_id)

     "user.deleted":
        user_id = data.get("id")
        if not user_id: log warning + return
        profile = fetch_profile_for_deletion(sr_client, user_id)
        call_delete_user_data(sr_client, user_id)
        if profile:
            email, full_name = profile
            row_id = enqueue_email(sr_client,
                                   template="account_deleted",
                                   to_email=email,
                                   payload={"first_name": full_name})
            background_tasks.add_task(try_send_pending_email, row_id)

     anything else:
        log "unhandled Clerk event type=%s" + return (204)
7. Always 204 unless step 2 raised 401.
```

The destructive DB work (`call_delete_user_data`) stays in-request — that part already is idempotent and cannot be deferred without re-introducing the lost-work risk we just fixed for email.

### Route-driven path: `deletion_service.delete_account`

In `app/services/deletion_service.py`, step 5 of the existing flow (call `send_account_deleted_email` directly) becomes:

```python
row_id = enqueue_email(
    sr_client,
    template="account_deleted",
    to_email=email,
    payload={"first_name": full_name},
)
# Fast-path attempt happens in the route layer via BackgroundTasks; the
# service stays pure (no FastAPI BackgroundTasks dependency).
return row_id
```

The route (`POST /account/delete`) then does `background_tasks.add_task(try_send_pending_email, row_id)` after `delete_account` returns, mirroring the webhook handler.

This keeps `deletion_service` free of FastAPI types (per `CLAUDE.md` architecture rule that services receive plain data and don't hold framework references).

### Internal endpoint: `POST /internal/emails/flush`

New file `app/routes/internal_emails.py`. Registered in `app/main.py`.

```
POST /internal/emails/flush
  Auth: Authorization: Bearer <CRON_SHARED_SECRET>  (FastAPI dependency)
  Body: optional {"batch_size": int}  (default 50, capped at 200)
  Behavior:
    rows = fetch_ready_pending_emails(sr_client, limit=batch_size)
    for row in rows:
        ok = try_send_pending_email(row.id)   # reuses the same fn the fast path uses
    return {"sent": <n>, "failed": <n>, "scanned": len(rows)}
```

The endpoint is thin glue. All locking/state logic lives in
`try_send_pending_email(row_id)`:

```
def try_send_pending_email(row_id: int) -> bool:
    1. sr_client = build_service_role_client()
    2. row = claim_pending_email(sr_client, row_id)
       # SELECT … WHERE id = ? AND sent_at IS NULL
       #   AND next_run_at <= now() AND attempts < max_attempts
       #   FOR UPDATE SKIP LOCKED
       # → returns row or None
       # SKIP LOCKED ensures the fast path and the cron worker never
       # collide on the same row; whichever gets the lock first sends.
    3. if row is None: return False  # already sent, claimed, or not due
    4. ok = email_service._send(row.to_email, <template_id from row.template>,
                                {"USER": row.payload.get("first_name")})
    5. if ok: mark_pending_email_sent(sr_client, row_id)
       else:  mark_pending_email_failed(sr_client, row_id, error="resend rejected")
              # bumps attempts; computes next_run_at via backoff
    6. return ok
```

**Concurrency safety:** the producer's fast-path BackgroundTask and the cron-driven flush can both fire shortly after a row is inserted. `FOR UPDATE SKIP LOCKED` inside `claim_pending_email` makes that safe — only one wins the lock per call. The loser observes the locked row and returns `None`.

**Auth dependency:** new `verify_cron_secret` dependency in `app/routes/deps.py`. Reads `Authorization: Bearer …` and compares to `settings.cron_shared_secret`. 401 on mismatch. **No JWT decoding here** — preserves the `CLAUDE.md` rule that JWT verification only lives in `get_user_ctx`.

### Files changed

- `app/routes/webhooks_clerk.py` — rewrite to single dispatcher.
- `app/services/deletion_service.py` — swap `send_account_deleted_email` call for `enqueue_email`; return `row_id`.
- `app/routes/account_deletion.py` — after `delete_account` returns, queue `try_send_pending_email(row_id)` via BackgroundTasks.
- `app/services/email_service.py` — add `try_send_pending_email(row_id) -> bool` (consumer-side). Existing `send_welcome_email` / `send_account_deleted_email` stay (they're the actual Resend call sites; `try_send_pending_email` chooses between them based on `row.template`).
- `app/db/client.py` — add helpers: `enqueue_email`, `claim_pending_email`, `mark_pending_email_sent`, `mark_pending_email_failed`, `fetch_ready_pending_emails`.
- `app/routes/internal_emails.py` (new) — `POST /internal/emails/flush` + secret-bearer dependency.
- `app/routes/deps.py` — add `verify_cron_secret` dependency.
- `app/config.py` — add `cron_shared_secret: str` setting (required).
- `app/main.py` — include the new router.
- `supabase/migrations/<ts>_pending_emails.sql` — new table + index + RLS-on with no policies.
- `docs/superpowers/plans/2026-05-06-account-deletion-simplified.md` — update Phase 3 deviation note (lines 63-69) and Phase 4 Clerk dashboard line (87) to a single endpoint.
- `tests/test_webhooks_clerk.py` (new), `tests/test_internal_emails.py` (new), `tests/test_pending_emails_db.py` (new), updates to `tests/test_deletion_service.py`.

### Tests

**`tests/test_webhooks_clerk.py`** (5 cases, stubbing `Webhook.verify`, db helpers, and `try_send_pending_email`):
1. Bad signature → 401, no db writes.
2. Duplicate svix-id → 204, no enqueue.
3. `user.created` → 204, `enqueue_email("welcome", primary_email, {"first_name": ...})` called once, BackgroundTask queued with the returned `row_id`.
4. `user.deleted` → 204, `call_delete_user_data` called, `enqueue_email("account_deleted", profile_email, {"first_name": full_name})` called once, BackgroundTask queued.
5. Unknown event type → 204, no enqueue.

Edge cases (3b/4b): `user.created` with no primary email → no enqueue; `user.deleted` with no `data.id` → no enqueue, no DB wipe.

**`tests/test_deletion_service.py`** (updates):
- Happy path now asserts `enqueue_email` is called (not `send_account_deleted_email` directly), and the function returns `row_id`.

**`tests/test_internal_emails.py`** (new):
- Bad/missing bearer → 401.
- Valid bearer, no ready rows → 200 with `{"sent": 0, "failed": 0, "scanned": 0}`.
- Valid bearer, two ready rows, both Resend-succeed → 200 with `{"sent": 2, …}` and both rows have `sent_at` set.
- Resend fails → row's `attempts` incremented, `next_run_at` bumped per backoff, `last_error` recorded.

**`tests/test_pending_emails_db.py`** (new):
- `enqueue_email` inserts with defaults (attempts=0, next_run_at≈now).
- `claim_pending_email` returns the row when ready, returns None when `sent_at IS NOT NULL`, returns None when `next_run_at > now()`.
- `mark_pending_email_sent` sets `sent_at`.
- `mark_pending_email_failed` increments `attempts`, sets `last_error`, sets `next_run_at` per the backoff formula.

Unit tests use a fake supabase client (matching the existing `test_db_client.py` pattern). The `FOR UPDATE SKIP LOCKED` behavior is a Postgres feature; unit tests assert the query string includes it but don't simulate locking.

### Configuration

- New env var: `CRON_SHARED_SECRET` (string, required). Add to `.env.example` (with a placeholder value), declare in `render.yaml` with `sync: false`, set the real value as a secret in the Render dashboard, and store the same value in Supabase Vault as `CRON_SHARED_SECRET` (used by the pg_cron job to authenticate to the internal endpoint).
- `app/config.py` Settings adds `cron_shared_secret: str` with no default — missing value fails at boot, per CLAUDE.md ("Misconfiguration fails at app boot").

### Ops (manual)

This is the deployment runbook for the cron piece. **Not code.**

1. Deploy the app with the new `/internal/emails/flush` route and `CRON_SHARED_SECRET` env var set in Render.
2. In Supabase SQL editor, one-time setup:
   ```sql
   CREATE EXTENSION IF NOT EXISTS pg_cron;
   CREATE EXTENSION IF NOT EXISTS pg_net;
   SELECT vault.create_secret('CRON_SHARED_SECRET', '<same value as Render env var>');
   SELECT cron.schedule(
       'flush-pending-emails',
       '* * * * *',  -- every minute
       $$
       SELECT net.http_post(
           url := 'https://<render-host>/internal/emails/flush',
           headers := jsonb_build_object(
               'Authorization',
               'Bearer ' || (
                   SELECT decrypted_secret
                   FROM vault.decrypted_secrets
                   WHERE name = 'CRON_SHARED_SECRET'
               ),
               'Content-Type', 'application/json'
           ),
           body := '{}'::jsonb
       );
       $$
   );
   ```
3. Verify with `SELECT * FROM cron.job_run_details ORDER BY start_time DESC LIMIT 5;` after waiting a minute.
4. **Clerk dashboard:** delete the two webhook registrations (`…/webhooks/clerk/welcome` and `…/webhooks/clerk/delete_account`). Create one registration at `…/webhooks/clerk` subscribed to both `user.created` and `user.deleted`. Reuse the existing `CLERK_WEBHOOK_SECRET`.
5. Clerk "Send test event" for `user.created` and `user.deleted`; confirm 204 in both cases, that a `pending_emails` row appears, that the row's `sent_at` becomes non-null within one minute, and that the email arrives.

### Operator queries (post-deploy)

```sql
-- Anything stuck (exceeded max attempts):
SELECT id, template, to_email, attempts, last_error, last_attempted_at
FROM public.pending_emails
WHERE sent_at IS NULL AND attempts >= max_attempts
ORDER BY created_at DESC;

-- Anything waiting (not yet attempted or in backoff):
SELECT id, template, to_email, attempts, next_run_at, last_error
FROM public.pending_emails
WHERE sent_at IS NULL AND attempts < max_attempts
ORDER BY next_run_at;
```

## Risks and trade-offs

- **First pg_cron in the project.** Whoever runs the deploy needs Supabase SQL editor access and the secret. The archival cron design has already been reviewed for this pattern; we're just shipping it for real.
- **Two attempt paths (fast-path BackgroundTask + cron) per row.** Mitigated by `FOR UPDATE SKIP LOCKED` in `claim_pending_email`. Without that, both could send the same email twice.
- **No dead-letter alerting.** Stuck rows sit until an operator queries. Adding alerting (Sentry, Slack) is out of scope here; the SQL query is documented as the interim mechanism.
- **Resend permanent failures.** A 4xx response (e.g., template id wrong) will burn through all 8 attempts within ~2h. That's fine — the row stays for inspection — but it's a soft pager-level signal we don't currently page on.
- **No row TTL.** `pending_emails` grows forever (slowly). Add a cleanup job ("delete WHERE sent_at < now() - interval '30 days'") later if it matters. The partial index keeps the hot path fast regardless.

## Out of scope

- Generalizing the outbox to other event types (push, SMS, in-app).
- Email rate limiting per user.
- A retry-now operator action via API/UI.
- Alerting on stuck rows.
- Migrating the (not-yet-shipped) archival cron design to share this `/internal/...` plumbing — easy follow-up but separate.
