# Email Template Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the brand-forward email redesign and from-address change defined in `docs/superpowers/specs/2026-04-28-email-template-redesign-design.md`.

**Architecture:** Two layers. Code layer: a defensive `first_name or "there"` fallback in both `send_welcome_email` and `send_goodbye_email` so missing first names render naturally; a value change in `.env.example` for `RESEND_FROM_EMAIL`. Operational layer: paste new HTML and plain-text bodies into the two existing Resend dashboard templates (`welcome-personal-budget`, `account-deleted`) and update the production env var on Render. The HTML/text bodies are not duplicated here — they are the source of truth in the spec.

**Tech Stack:** Python 3.13, pytest, `unittest.mock.patch` (stdlib, no extra deps), FastAPI (existing), Resend hosted templates (referenced by ID from `app/services/email_service.py`).

---

## Precondition

There are uncommitted local changes on `ft/email-clerk-config` to `app/routes/emails.py` and `app/services/email_service.py` that add `send_goodbye_email` and the `/emails/goodbye` webhook. **These must be committed before Task 1.** Task 1 modifies the same `send_goodbye_email` function and a clean working tree before then keeps the diffs reviewable.

If you're picking this plan up cold, run `git status` first; if either of those files appears as `M` and unstaged, commit them on this branch with their own message before starting Task 1.

---

## File Structure

| File | Responsibility | Touched in |
|---|---|---|
| `app/services/email_service.py` | Resend call site for both transactional emails. Owns the `USER` variable mapping. | Task 1 |
| `tests/test_email_service.py` | Unit tests for the variable mapping (mocks Resend; no network). New file. | Task 1 |
| `.env.example` | Committed example env file. Carries the new `RESEND_FROM_EMAIL` display-name format. | Task 2 |
| Resend dashboard — `welcome-personal-budget` template | HTML + plain-text bodies for the welcome email. External system, paste-in. | Tasks 3–4 |
| Resend dashboard — `account-deleted` template | HTML + plain-text bodies for the deletion email. External system, paste-in. | Tasks 5–6 |
| Render env var `RESEND_FROM_EMAIL` | Production sender. External system, manual edit. | Task 8 |

`app/routes/emails.py` is **not** modified by this plan (the `/emails/goodbye` webhook handler is in the precondition commit).

---

## Task 1: First-name fallback with TDD

Pass `"there"` as the `USER` template variable when Clerk's webhook payload has no `first_name`, so neither template renders a `null` or a literal `{{USER}}`.

**Files:**
- Create: `tests/test_email_service.py`
- Modify: `app/services/email_service.py:21` and `app/services/email_service.py:42`

- [ ] **Step 1: Create the test file with two failing tests**

`tests/test_email_service.py`:

```python
"""Unit tests for app.services.email_service.

Mocks `resend.Emails.send` so no network calls are made. Each test asserts
the structure of the payload handed to Resend, including the USER variable
that drives template rendering.
"""

from __future__ import annotations

from unittest.mock import patch

from app.services.email_service import (
    send_goodbye_email,
    send_welcome_email,
)


def _patch_send():
    """Patch `resend.Emails.send` and `get_settings` so calls are inert."""
    send_patch = patch("app.services.email_service.resend.Emails.send")
    settings_patch = patch(
        "app.services.email_service.get_settings",
        return_value=type(
            "S",
            (),
            {
                "resend_api_key": "test-key",
                "resend_from_email": "Personal Budget <nily@bynily.dev>",
            },
        )(),
    )
    return send_patch, settings_patch


def test_welcome_email_passes_first_name_through() -> None:
    send_patch, settings_patch = _patch_send()
    with settings_patch, send_patch as mock_send:
        mock_send.return_value = {"id": "test"}
        ok = send_welcome_email("user@example.com", first_name="Nilyan")
    assert ok is True
    payload = mock_send.call_args.args[0]
    assert payload["template"]["variables"]["USER"] == "Nilyan"


def test_welcome_email_falls_back_when_first_name_is_none() -> None:
    send_patch, settings_patch = _patch_send()
    with settings_patch, send_patch as mock_send:
        mock_send.return_value = {"id": "test"}
        ok = send_welcome_email("user@example.com", first_name=None)
    assert ok is True
    payload = mock_send.call_args.args[0]
    assert payload["template"]["variables"]["USER"] == "there"


def test_goodbye_email_passes_first_name_through() -> None:
    send_patch, settings_patch = _patch_send()
    with settings_patch, send_patch as mock_send:
        mock_send.return_value = {"id": "test"}
        ok = send_goodbye_email("user@example.com", first_name="Nilyan")
    assert ok is True
    payload = mock_send.call_args.args[0]
    assert payload["template"]["variables"]["USER"] == "Nilyan"


def test_goodbye_email_falls_back_when_first_name_is_none() -> None:
    send_patch, settings_patch = _patch_send()
    with settings_patch, send_patch as mock_send:
        mock_send.return_value = {"id": "test"}
        ok = send_goodbye_email("user@example.com", first_name=None)
    assert ok is True
    payload = mock_send.call_args.args[0]
    assert payload["template"]["variables"]["USER"] == "there"
```

- [ ] **Step 2: Run the tests and confirm the two fallback tests fail**

Run: `pytest tests/test_email_service.py -v`

Expected: 2 of 4 tests pass (the "passes through" tests). The two "falls back" tests fail with an assertion like `AssertionError: assert None == 'there'` because `first_name` is forwarded as `None` today.

- [ ] **Step 3: Apply the fallback in both functions**

In `app/services/email_service.py`, change line 21 from:

```python
                "USER": first_name,
```

to:

```python
                "USER": first_name or "there",
```

And the matching change at line 42 (`send_goodbye_email`) — same edit, same line content.

After both edits, `app/services/email_service.py` should look like this in the relevant blocks:

```python
def send_welcome_email(to: str, first_name: str | None = None) -> bool:
    settings = get_settings()
    resend.api_key = settings.resend_api_key
    logger.info("Sending welcome email to %s (first_name=%s)", to, first_name)
    try:
        response = resend.Emails.send({
            "from": settings.resend_from_email,
            "to": [to],
            "template": {
                "id": "welcome-personal-budget",
                "variables": {
                    "USER": first_name or "there",
                },
            },
        })
        logger.info("Resend accepted email to %s: %s", to, response)
        return True
    except Exception:
        logger.exception("Failed to send welcome email to %s", to)
        return False

def send_goodbye_email(to: str, first_name: str | None = None) -> bool:
    settings = get_settings()
    resend.api_key = settings.resend_api_key
    logger.info("Sending goodbye email to %s (first_name=%s)", to, first_name)
    try:
        response = resend.Emails.send({
            "from": settings.resend_from_email,
            "to": [to],
            "template": {
                "id": "account-deleted",
                "variables": {
                    "USER": first_name or "there",
                },
            },
        })
        logger.info("Resend accepted email to %s: %s", to, response)
        return True
    except Exception:
        logger.exception("Failed to send goodbye email to %s", to)
        return False
```

- [ ] **Step 4: Run the tests and confirm all four pass**

Run: `pytest tests/test_email_service.py -v`

Expected: 4 passed.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `pytest -q`

Expected: all tests pass (existing + the four new ones).

- [ ] **Step 6: Commit**

```bash
git add tests/test_email_service.py app/services/email_service.py
git commit -m "feat(email): default USER variable to 'there' when first_name is missing"
```

---

## Task 2: From-address change in `.env.example`

Update the committed example so future contributors and the deploy template carry the new display-name format.

**Files:**
- Modify: `.env.example:63`

- [ ] **Step 1: Replace the value**

In `.env.example`, change line 63 from:

```
RESEND_FROM_EMAIL=hello@bynily.dev
```

to:

```
RESEND_FROM_EMAIL=Personal Budget <nily@bynily.dev>
```

- [ ] **Step 2: Verify the change**

Run: `grep -n "RESEND_FROM_EMAIL" .env.example`

Expected output: `63:RESEND_FROM_EMAIL=Personal Budget <nily@bynily.dev>`

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "chore(env): switch RESEND_FROM_EMAIL to 'Personal Budget <nily@bynily.dev>'"
```

---

## Task 3: Paste the welcome HTML into Resend

Manual operations task — done in Resend's dashboard, no commits.

- [ ] **Step 1: Open the spec and the Resend template side-by-side**

Open `docs/superpowers/specs/2026-04-28-email-template-redesign-design.md` and locate the section heading `## Final HTML — welcome template`. The fenced ` ```html ` block immediately under it is the body to paste.

In a separate tab, log in to Resend and open the template named `welcome-personal-budget` (the ID hard-coded at `app/services/email_service.py:19`).

- [ ] **Step 2: Paste the HTML**

Copy the entire `<!DOCTYPE html ...>...</html>` block from the spec into the template's HTML body field, replacing whatever HTML is currently there. Save the template.

- [ ] **Step 3: Verify the subject line**

The template's subject line should remain whatever you have configured today (the redesign does not change it). If your dashboard shows the old "Your Personal Budget: Track, Manage, Overview" subject visible in the screenshot the user provided, leave it; subject changes are out of scope for this plan.

---

## Task 4: Paste the welcome plain-text into Resend

- [ ] **Step 1: Locate the plain-text block in the spec**

In `docs/superpowers/specs/2026-04-28-email-template-redesign-design.md`, find the section heading `## Final plain-text — welcome template`. The fenced ` ``` ` block under it is the body.

- [ ] **Step 2: Paste the plain-text**

In Resend's `welcome-personal-budget` template, switch to the plain-text body field. Paste the text block from the spec, replacing existing content. Save.

---

## Task 5: Paste the account-deleted HTML into Resend

- [ ] **Step 1: Open the deletion template**

In Resend, open the template named `account-deleted` (the ID at `app/services/email_service.py:40`).

- [ ] **Step 2: Paste the HTML**

In `docs/superpowers/specs/2026-04-28-email-template-redesign-design.md`, find the section heading `## Final HTML — account-deleted template`. Copy the `<!DOCTYPE html ...>...</html>` block under it.

Paste into the template's HTML body field, replacing existing content. Save.

- [ ] **Step 3: Set the subject line**

Set the template's subject line to:

```
Your Personal Budget account is gone
```

(The spec calls this out under "Final HTML — account-deleted template".)

---

## Task 6: Paste the account-deleted plain-text into Resend

- [ ] **Step 1: Locate the plain-text block in the spec**

In `docs/superpowers/specs/2026-04-28-email-template-redesign-design.md`, find `## Final plain-text — account-deleted template`. The fenced block under it is the body.

- [ ] **Step 2: Paste the plain-text**

In Resend's `account-deleted` template, paste the text block into the plain-text body field, replacing existing content. Save.

---

## Task 7: Resend dashboard preview

Catch obvious rendering bugs before pushing the from-address change to production.

- [ ] **Step 1: Preview welcome with a normal name**

In Resend's preview tool for `welcome-personal-budget`, set the `USER` variable to `Nilyan` and render. Confirm:
- Headline reads "You're in, Nilyan."
- Wordmark "Personal Budget" with a purple "P" tile renders left-aligned at the top of the card.
- Three numbered steps render with `01 / 02 / 03` numerals on the left.
- The italic AI-quote callout has a left purple rule.
- The "Open your dashboard" button is purple with white text.
- Footer reads "— Nily" above an underlined "budget.bynily.dev" link.

- [ ] **Step 2: Preview welcome with a missing name**

In the same preview tool, set the `USER` variable to `there` (the literal string the Python fallback will pass when Clerk's payload has no `first_name`). Confirm:
- Headline reads "You're in, there."
- Nothing else changes.

- [ ] **Step 3: Preview account-deleted with a normal name**

In Resend's preview tool for `account-deleted`, set `USER` to `Nilyan`. Confirm:
- Headline reads "Hey Nilyan, your account is gone."
- Three body paragraphs render with the spec copy.
- Footer reads "— Nily" above the budget.bynily.dev link.

- [ ] **Step 4: Preview account-deleted with a missing name**

Set `USER` to `there`. Confirm headline reads "Hey there, your account is gone."

If any preview shows an unrendered `{{USER}}`, a literal `null`, broken layout, missing wordmark, or wrong colors — stop and fix the dashboard paste before continuing. Compare the pasted HTML byte-for-byte against the spec.

---

## Task 8: Update Render production env var

Manual ops in the Render dashboard. No git changes.

- [ ] **Step 1: Open the Render service**

In Render, open the service whose env vars are listed in `render.yaml`. Locate the `RESEND_FROM_EMAIL` env var (the key declared at `render.yaml:27`).

- [ ] **Step 2: Change the value**

Edit the value from `hello@bynily.dev` to:

```
Personal Budget <nily@bynily.dev>
```

Save. Render will redeploy automatically.

- [ ] **Step 3: Confirm the redeploy completes**

Wait for the new build to go live (Render shows a green "Live" badge on the latest deploy). The previous deploy stays serving traffic until the new one is healthy.

---

## Task 9: Production smoke test

- [ ] **Step 1: Sign up a throwaway Clerk user with no first name**

Use a fresh email (e.g. a Gmail "+" alias) and complete Clerk sign-up without filling in the optional first-name field. The `user.created` webhook will hit `/emails/welcome` in production.

- [ ] **Step 2: Verify the welcome email lands**

In the throwaway inbox, confirm:
- Sender displays as "Personal Budget" with the address `nily@bynily.dev` underneath.
- Subject is your existing welcome subject (unchanged by this plan).
- Headline reads "You're in, there." (verifying the Python fallback in production).
- Card layout, wordmark, numbered steps, AI-quote callout, button, privacy line, and footer all render as expected.

If the headline reads `You're in, .` (empty), `You're in, None.`, or `You're in, {{USER}}.`, the fallback didn't deploy — check that Task 1's commit is on `main` (or whichever branch Render tracks) and that the build completed.

- [ ] **Step 3: Delete the throwaway user from Clerk**

In Clerk's dashboard, delete the user. The `user.deleted` webhook will hit `/emails/goodbye`.

- [ ] **Step 4: Verify the deletion email lands**

In the same throwaway inbox, confirm:
- Sender is "Personal Budget <nily@bynily.dev>".
- Headline reads "Hey there, your account is gone."
- Three body paragraphs render with the spec copy.
- Card, wordmark, footer all render correctly.

- [ ] **Step 5: Cross-client spot check (optional but recommended)**

Forward both received emails to a second inbox you can read on Outlook desktop (Windows). Confirm the card border, the purple "P" tile, and the CTA button all render — Outlook is the limiting client and the table-based markup in the spec is specifically engineered for it.

---

## Out-of-scope reminders (do not do as part of this plan)

- Migrating templates from Resend's dashboard into the codebase as Jinja2 files.
- Adding a verified second domain (e.g. `personalbudget.app`).
- Promoting `— Nily` and `budget.bynily.dev` to settings.
- Email tracking pixels, list-unsubscribe headers, marketing footers.
- Changes to `app/routes/emails.py` (the precondition commit handles the goodbye webhook).

These are listed as follow-ups in the spec and should each get their own spec/plan if pursued.
