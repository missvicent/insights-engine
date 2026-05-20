# Clerk Webhook — Single Dispatcher

**Date:** 2026-05-19
**Owner:** vicentbnf@gmail.com
**Related:** [2026-05-06-account-deletion-simplified-design.md](2026-05-06-account-deletion-simplified-design.md)

## Problem

`app/routes/webhooks_clerk.py` currently exposes two endpoints:

- `POST /webhooks/clerk/welcome` → handles `user.created`
- `POST /webhooks/clerk/delete_account` → handles `user.deleted`

This duplicates the Svix-verify + idempotency-check preamble in both
handlers and forces Clerk to manage two separate webhook registrations
(each with its own URL but the same secret). The original spec
([account-deletion-simplified, deviation note](../plans/2026-05-06-account-deletion-simplified.md#L63-L69))
called for a single dispatcher; the two-endpoint split was a deliberate
deviation that has not paid off. This spec reverses that deviation.

## Goal

One webhook URL — `POST /webhooks/clerk` — that verifies the Svix
signature once, enforces idempotency once, and dispatches on
`event.type` to the correct service call and the correct Resend
template.

## Non-goals

- Changing what `send_welcome_email` or `send_account_deleted_email`
  do internally.
- Changing the Svix verification mechanism or env var
  (`CLERK_WEBHOOK_SECRET` stays shared, as it already is).
- Adding new Clerk event subscriptions beyond `user.created` and
  `user.deleted`.
- Frontend changes.

## Design

### Route

Replace both handlers in `app/routes/webhooks_clerk.py` with a single
handler:

```
POST /webhooks/clerk → 204 (always, on success or duplicate or unknown event)
                   → 401 on Svix signature failure
```

### Handler flow

1. Read the raw request body and the three Svix headers
   (`svix-id`, `svix-timestamp`, `svix-signature`).
2. Verify with `Webhook(get_settings().clerk_webhook_secret).verify(...)`.
   Raise `HTTPException(401, "Invalid signature")` on
   `WebhookVerificationError`.
3. Build a service-role client (`build_service_role_client()`).
4. `record_webhook_event(sr_client, svix_id)` → if `False` (duplicate),
   log at INFO and return `None` (the route is declared
   `status_code=204`).
5. Parse the JSON payload, read `event.type`, log it at INFO.
6. Dispatch:
   - **`user.created`** → extract the primary email by matching
     `data.primary_email_address_id` against `data.email_addresses[].id`.
     If no primary email, log a warning and return.
     Otherwise `background_tasks.add_task(send_welcome_email,
     primary_email, data.get("first_name"))`.
   - **`user.deleted`** → read `data.id`. If missing, log a warning
     and return. Otherwise:
     a. `profile = fetch_profile_for_deletion(sr_client, user_id)`
     b. `call_delete_user_data(sr_client, user_id)`
     c. If `profile`: `background_tasks.add_task(send_account_deleted_email,
        email, full_name)`.
   - **Anything else** (`session.*`, `email.*`, …) → log
     `"Unhandled Clerk event type=%s"` at INFO and return. Returning
     204 prevents Clerk from retrying events we don't care about.

### Template mapping (the "correct template" requirement)

The dispatcher is the single place that decides which event triggers
which email. The mapping is hardcoded by the dispatch branches:

| Event | Service call | Resend template env var |
| --- | --- | --- |
| `user.created` | `send_welcome_email` | `RESEND_TEMPLATE_WELCOME` |
| `user.deleted` | `send_account_deleted_email` | `RESEND_TEMPLATE_ACCOUNT_DELETED` |

Neither service function changes; the dispatcher just routes to the
right one. This is the part the previous split risked drifting:
collapsing into one handler makes the mapping obvious in one place.

### Email delivery — both events use BackgroundTasks

Both email sends go through `background_tasks.add_task(...)` so the
webhook returns 204 immediately after the synchronous database work
(idempotency insert, plus `call_delete_user_data` for the delete path)
completes. Rationale:

- Resend latency does not delay the 204, which keeps us well inside
  Clerk's webhook timeout budget.
- `email_service._send` already wraps Resend in `try/except Exception`
  and returns `False`, so a Resend outage can't fail the deletion. The
  background task inherits that safety.
- Accepted risk: if the API process is killed between queueing and the
  HTTP call to Resend, the email is lost (FastAPI BackgroundTasks have
  no persistence or retry). For both events this is acceptable — the
  destructive work (DB wipe on `user.deleted`) has already committed,
  and the worst case for `user.created` is a missed welcome email,
  which is non-critical. We accept this rather than adding a job queue
  for two non-essential email sends.

### Files changed

- `app/routes/webhooks_clerk.py` — rewrite to a single handler.
  Remove `webhooks_delete_account` and `welcome_webhook`.
- `app/main.py` — no router include change (the router itself is
  unchanged, only its routes).
- `docs/superpowers/plans/2026-05-06-account-deletion-simplified.md` —
  update Phase 3 deviation note (lines 63-69) and Phase 4 Clerk
  dashboard checklist (line 87) to reflect a single endpoint
  subscribed to both event types.
- `tests/test_webhooks_clerk.py` (new) — five test cases below.

### Tests

New file `tests/test_webhooks_clerk.py`, using FastAPI's `TestClient`
and stubbing `svix.webhooks.Webhook.verify` plus the db/service
helpers:

1. **Bad signature → 401.** `Webhook.verify` raises
   `WebhookVerificationError`; no db or service calls happen.
2. **Duplicate svix-id → 204, no work.** `record_webhook_event`
   returns `False`; assert neither `call_delete_user_data` nor any
   email task is queued.
3. **`user.created` → 204 + welcome email queued.** Payload has
   `primary_email_address_id` matching one entry in `email_addresses`;
   assert `background_tasks` contains a task calling
   `send_welcome_email` with that email and the `first_name`.
4. **`user.deleted` → 204 + delete + account-deleted email queued.**
   `fetch_profile_for_deletion` returns `("alice@example.com", "Alice")`;
   assert `call_delete_user_data` called with the user_id and a task
   queued for `send_account_deleted_email(email, full_name)`.
5. **Unknown event type → 204, no work.** Payload `type: "session.created"`;
   assert no db writes beyond the idempotency insert and no email tasks.

Edge cases covered by 3 and 4: `user.created` with no primary email
(early-return after logging — assert no task queued) and
`user.deleted` with no `data.id` (early-return — assert no
`call_delete_user_data`). These can be parametrized into cases 3
and 4 or added as 3b/4b — implementer's call.

### Ops (manual, after merge)

This is **not code**. Listed here so the spec is the single source for
the end-to-end change.

- **Clerk dashboard:** delete the two existing webhook registrations
  (`…/webhooks/clerk/welcome` and `…/webhooks/clerk/delete_account`).
  Create one registration pointing at `…/webhooks/clerk` subscribed to
  both `user.created` and `user.deleted`. Keep the same signing
  secret (the existing `CLERK_WEBHOOK_SECRET` is reused).
- **Verify:** Clerk "Send test event" for each of the two subscribed
  events; confirm 204 in both cases and the expected email lands.

## Out of scope

- Retry/persistence for background-task email sends.
- Other Clerk events (organization membership, sessions, etc.).
- Reverting any Resend template content — the templates themselves
  are correct; only the dispatcher routing needs to be unified.
