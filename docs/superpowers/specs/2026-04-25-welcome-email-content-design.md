---
date: 2026-04-25
status: draft
title: Welcome Email — Content Design
supersedes: Task 9 (welcome templates) of docs/superpowers/plans/2026-04-24-clerk-lifecycle-emails.md
---

# Welcome Email — Content Design

## Goal

Replace the placeholder welcome-email copy in the Clerk lifecycle emails plan
with finalized, human-friendly content that explains how the app works in
three short steps, highlights the AI-narrated insight as the value
proposition, and reassures the user about data privacy.

This spec covers **only the content of `welcome.html` and `welcome.txt`** plus
two related settings adjustments (`APP_NAME` default, hardcoded CTA URL).
All wiring — the webhook handler, Resend integration, Jinja2 environment,
template rendering — is already covered by the existing lifecycle plan and
is not changed here.

## Why this design

The user landed on three constraints during brainstorming:

1. **Human-friendly, short.** Earlier drafts were too dense. Final copy is
   ~115 words.
2. **Explain how the app works.** A new user signing up has no mental model
   of the deterministic-engine + AI-narrative split, so the email walks
   through the three concrete steps that produce a first insight.
3. **Lead with the AI advice.** The differentiator vs. a spreadsheet is the
   plain-language summary, so a sample AI quote appears inline as the payoff
   for step 3.
4. **Privacy reassurance.** A short line stating we don't share, sell, or
   send marketing email — placed near the CTA where it matters.

The tone is direct and friendly without exclamation marks or emojis.

## Scope

**In scope**

- Final copy for `app/templates/emails/welcome.html` (HTML).
- Final copy for `app/templates/emails/welcome.txt` (plain text).
- Subject line.
- `APP_NAME` default value change to "Personal Budget".
- CTA URL — hardcoded to `https://budget.bynily.dev/overview`.

**Out of scope**

- The `account_deleted.html` / `account_deleted.txt` templates (covered by
  the lifecycle plan; no content change requested).
- Email-service code, Resend wiring, Jinja2 environment setup (covered by
  the lifecycle plan).
- Templating the CTA URL via an env var (`{{ app_url }}`) — deferred until
  there's a second deployment target.
- Templating the signature (`— Nily`) — deferred for the same reason.

## Final copy

### Subject

```
Welcome to Personal Budget, {{ first_name | default("there") }}
```

When `first_name` is missing the subject becomes `Welcome to Personal
Budget, there`. That's marginally awkward; if the AB-test cost of dropping
the comma is zero, prefer:

```
Welcome to {{ app_name }}{{ ", " ~ first_name if first_name else "" }}
```

Either form is acceptable; the simpler `default("there")` matches the
existing template style in the lifecycle plan and is the recommended choice
for v1.

### `welcome.html`

```html
<!doctype html>
<html lang="en">
  <body style="margin:0;padding:24px;background:#f7f7f8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#111;">
    <div style="max-width:560px;margin:0 auto;background:#fff;border:1px solid #e5e5ea;border-radius:8px;padding:36px 28px;line-height:1.6;">

      <h1 style="font-size:22px;margin:0 0 18px;font-weight:600;">
        You're in, {{ first_name | default("there") }}.
      </h1>

      <p style="margin:0 0 16px;font-size:15px;">
        {{ app_name }} turns your spending into plain-language advice.
        Here's how it works in three steps:
      </p>

      <ol style="margin:0 0 18px;padding-left:18px;font-size:15px;">
        <li style="margin-bottom:8px;">
          <strong>Create a budget.</strong> Pick weekly or monthly, then
          split it into categories (rent, food, savings).
        </li>
        <li style="margin-bottom:8px;">
          <strong>Add your transactions.</strong> Tag each one with a
          category and amount.
        </li>
        <li style="margin-bottom:8px;">
          <strong>Open your insights.</strong> The engine compares your
          spending to your plan, and the AI explains it like this:
        </li>
      </ol>

      <p style="background:#f9fafb;border-left:3px solid #111;padding:14px 16px;font-size:14px;color:#374151;font-style:italic;margin:12px 0 18px;">
        You're $78 over on Food this week. Three of those were restaurant
        orders on Saturday. If the pattern holds, you'll miss your savings
        goal by about $120 this month.
      </p>

      <p style="text-align:center;margin:28px 0;">
        <a href="https://budget.bynily.dev/overview"
           style="display:inline-block;background:#111;color:#fff;text-decoration:none;padding:12px 22px;border-radius:6px;font-weight:600;font-size:14px;">
          Open your dashboard
        </a>
      </p>

      <p style="font-size:13px;color:#4b5563;background:#fafafa;padding:10px 14px;border-radius:6px;margin:0 0 16px;">
        Your data stays yours. We never share it, never sell it, and we
        won't send marketing email.
      </p>

      <p style="font-size:12px;color:#6b7280;margin:28px 0 0;border-top:1px solid #f1f1f4;padding-top:16px;">
        — Nily<br>
        <a href="https://budget.bynily.dev/overview" style="color:#6b7280;">
          budget.bynily.dev
        </a>
      </p>

    </div>
  </body>
</html>
```

### `welcome.txt`

```
You're in, {{ first_name | default("there") }}.

{{ app_name }} turns your spending into plain-language advice.
Here's how it works in three steps:

  1. Create a budget. Pick weekly or monthly, then split it into
     categories (rent, food, savings).
  2. Add your transactions. Tag each one with a category and amount.
  3. Open your insights. The engine compares your spending to your
     plan, and the AI explains it like this:

     "You're $78 over on Food this week. Three of those were
     restaurant orders on Saturday. If the pattern holds, you'll
     miss your savings goal by about $120 this month."

Open your dashboard: https://budget.bynily.dev/overview

Your data stays yours. We never share it, never sell it, and we
won't send marketing email.

— Nily
budget.bynily.dev
```

## Settings adjustments

The lifecycle plan (Task 1) introduces an `APP_NAME` setting with default
`"Finance Insights"`. This spec changes the default to `"Personal Budget"`
to match the user-facing brand.

In `app/db/client.py`:

```python
app_name: str = "Personal Budget"
```

In `.env.example` (lifecycle plan Task 0.3 — change the value, keep the
key):

```
APP_NAME=Personal Budget
```

The README and CLAUDE.md continue to use the internal name "Finance
Insights Engine" for the codebase; only user-facing surfaces use
"Personal Budget".

## Template variables

| Variable        | Source                                       | Required |
|-----------------|----------------------------------------------|----------|
| `first_name`    | Clerk JWT claim (already wired in lifecycle) | No (defaults to "there") |
| `app_name`      | `Settings.app_name` (= "Personal Budget")    | Yes      |

The CTA URL and signature are intentionally hardcoded for v1 — see Scope.

## Verification

The lifecycle plan already includes a test for template rendering with and
without `first_name` (`tests/test_email_service.py`, Task 10). Four
assertions in that test must be updated to reflect the new copy:

| Existing assertion | Replacement |
|---|---|
| `assert "Hi Ada" in payload["html"]` | `assert "You're in, Ada" in payload["html"]` |
| `assert "Hi Ada" in payload["text"]` | `assert "You're in, Ada" in payload["text"]` |
| `assert "Hi there" in payload["html"]` | `assert "You're in, there" in payload["html"]` |
| `assert "Hi there" in payload["text"]` | `assert "You're in, there" in payload["text"]` |

A new assertion to add:

```python
# Privacy line is the user's explicit ask — guard it from regression.
assert "never share" in payload["html"]
assert "never share" in payload["text"]
```

A new assertion for the CTA URL:

```python
assert "https://budget.bynily.dev/overview" in payload["html"]
assert "https://budget.bynily.dev/overview" in payload["text"]
```

## Migration & deployment

This spec changes only template files and one settings default. To deploy:

1. Apply the template content above to `app/templates/emails/welcome.html`
   and `app/templates/emails/welcome.txt` when Task 9 of the lifecycle plan
   is executed (or as a follow-up if those templates already exist with the
   placeholder copy).
2. Update the `APP_NAME` default in `app/db/client.py` and the value in
   `.env.example`.
3. Update `tests/test_email_service.py` per the table above.
4. Re-run `pytest tests/test_email_service.py -v`.
5. Send a test email via Resend (use `onboarding@resend.dev` or the
   verified domain) and inspect the rendered HTML in a real inbox.

## Follow-ups (separate specs)

- **Goodbye email content** — `account_deleted.html` / `account_deleted.txt`
  currently use the placeholder copy from the lifecycle plan. A future spec
  can apply the same human-friendly tone if desired.
- **Templated CTA URL** — promote the hardcoded `budget.bynily.dev/overview`
  to a `Settings.app_url` field once a staging or alternate environment
  exists.
- **Templated signature** — promote `— Nily` to a setting or template
  variable if the team grows beyond one person.
