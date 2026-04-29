---
date: 2026-04-28
status: draft
title: Email Template Redesign — Welcome and Account-Deleted
supersedes: visual aspects of docs/superpowers/specs/2026-04-25-welcome-email-content-design.md
---

# Email Template Redesign — Welcome and Account-Deleted

## Goal

Replace the current Resend-hosted templates for `welcome-personal-budget`
and `account-deleted` (template IDs from `app/services/email_service.py`)
with a single, professional, modern
visual system, and change the production from-address from
`hello@bynily.dev` to `Personal Budget <nily@bynily.dev>`.

The user assessment of the current state: "looks poorly", the from-address
"looks unprofessional", and the background needs to be "professional and
modern". The 2026-04-25 welcome content spec was already approved but the
deployed Resend template drifted from it during initial setup. This spec
realigns both templates on the approved copy and gives them a coherent
brand-forward visual treatment.

## Why this design

Decisions made during brainstorming, in order:

1. **Templates stay in Resend's dashboard.** The user chose dashboard-edit
   over migrating templates into the codebase. No code changes to
   `email_service.py` template-id references are needed.
2. **Visual direction: Brand-forward.** A wordmark + single accent color +
   one card on a neutral wash, modeled on Vercel/Resend's own emails.
   Rejected: bare-ledger (too utilitarian for a consumer product) and
   editorial (too "newsletter", not enough product trust signaling).
3. **Palette: refined lilac.** Keeps existing brand continuity (purple is
   already "Personal Budget" in users' heads) but replaces the soft
   `#fbfaff` tint with a tighter token system around `#5b5bd6`. Rejected:
   indigo pivot (loses brand continuity) and charcoal-only (loses
   personality).
4. **Logo: HTML+CSS tile, not S3 image.** A `<td>`-rendered solid square
   with a serif "P" displays even when the inbox blocks remote images;
   the current S3 attachment depends on image-loading consent.
5. **From-address: `Personal Budget <nily@bynily.dev>`.** The display name
   gives it brand authority; the prefix swap from `hello@` to `nily@`
   matches the "— Nily" signature already in the body. Domain stays the
   same — Resend already verifies `bynily.dev`, so no DNS work.
6. **Email-safety: tables + inline styles.** Outlook desktop is the
   limiting client; modern flexbox/grid is unsafe. System font stack
   only — no web fonts.

## Scope

**In scope**

- New HTML for the Resend `welcome-personal-budget` template.
- New plain-text version for the Resend `welcome-personal-budget` template.
- New HTML for the Resend `account-deleted` template (the template ID
  used by `send_goodbye_email`).
- New plain-text version for the deletion template.
- Update `RESEND_FROM_EMAIL` value in `.env.example` and on Render.
- Defensive null-fallback for `first_name` in
  `app/services/email_service.py` so missing names render as "there"
  rather than a literal `{{USER}}` or empty string.

**Out of scope**

- Migrating templates from Resend's dashboard into the codebase as
  Jinja2 files (option B during brainstorming). If desired later, the
  HTML in this spec ports to `app/templates/emails/` directly.
- Changes to `email_service.py` template-id references, the Clerk webhook
  handler, Resend SDK calls, or background-task wiring — all already
  correct.
- The "P." S3 attachment image — leave it on S3 untouched, just stop
  referencing it from the new templates.
- Adding a verified second domain (e.g., `personalbudget.app`).
- Email tracking pixels, unsubscribe links, list-unsubscribe headers —
  these are transactional emails and don't need them.

## Design tokens

| Token | Value | Used for |
|---|---|---|
| `--bg-wash` | `#faf9fc` | Outer background behind the card |
| `--card-bg` | `#ffffff` | Card surface |
| `--card-border` | `#ece9f3` | 1px card border |
| `--card-radius` | `10px` | Card corner radius |
| `--text-strong` | `#1c1a2b` | Headlines, wordmark, signature |
| `--text-body` | `#4b4860` | Body copy |
| `--text-muted` | `#9b96b0` | Footer, step numerals, muted links |
| `--rule` | `#f4f2f8` | Hairline rules between numbered steps and footer |
| `--brand` | `#5b5bd6` | Logo tile, quote rule, CTA background |
| `--cta-text` | `#ffffff` | CTA label color |
| `--font` | `-apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, Helvetica, Arial, sans-serif` | All text |
| `--serif` | `Georgia, "Iowan Old Style", serif` | Logo "P" only |

Type scale (px): headline 24/1.25/-0.02em/600 · body 14/1.6/400 · meta 11/1.4/600 (uppercase). Container max-width 600px, card padding 36px 32px, outer wrapper padding 36px 16px.

## Final HTML — welcome template

Paste this into the `welcome-personal-budget` template's HTML body in the
Resend dashboard. The `{{USER}}` token is substituted by Resend with the
`USER` variable that `send_welcome_email` already passes.

```html
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html dir="ltr" lang="en">
<head>
  <meta content="width=device-width" name="viewport" />
  <meta content="text/html; charset=UTF-8" http-equiv="Content-Type" />
  <meta name="x-apple-disable-message-reformatting" />
  <meta content="IE=edge" http-equiv="X-UA-Compatible" />
  <meta content="telephone=no,address=no,email=no,date=no,url=no" name="format-detection" />
  <title>Welcome to Personal Budget</title>
</head>
<body style="margin:0;padding:0;background-color:#faf9fc;">
  <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color:#faf9fc;">
    <tr>
      <td align="center" style="padding:36px 16px;">
        <table role="presentation" width="600" border="0" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
          <tr>
            <td style="background-color:#ffffff;border:1px solid #ece9f3;border-radius:10px;padding:36px 32px;font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1c1a2b;">

              <!-- Wordmark -->
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" style="margin:0 0 4px;">
                <tr>
                  <td width="24" height="24" align="center" valign="middle" style="background-color:#5b5bd6;border-radius:6px;color:#ffffff;font-family:Georgia,'Iowan Old Style',serif;font-weight:700;font-size:14px;line-height:24px;">P</td>
                  <td style="padding-left:10px;vertical-align:middle;font-size:14px;font-weight:600;color:#1c1a2b;letter-spacing:-0.01em;line-height:24px;">Personal Budget</td>
                </tr>
              </table>

              <!-- Headline -->
              <h1 style="margin:22px 0 12px;font-size:24px;line-height:1.25;font-weight:600;letter-spacing:-0.02em;color:#1c1a2b;">
                You're in, {{USER}}.
              </h1>

              <!-- Intro -->
              <p style="margin:0 0 18px;font-size:14px;line-height:1.6;color:#4b4860;">
                Personal Budget turns your spending into plain-language advice. Here's how it works in three steps:
              </p>

              <!-- Steps (table-based for Outlook) -->
              <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="padding:12px 0;border-top:1px solid #f4f2f8;">
                    <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0">
                      <tr>
                        <td width="32" valign="top" style="font-size:11px;color:#9b96b0;font-weight:600;letter-spacing:0.04em;line-height:1.6;padding-top:2px;">01</td>
                        <td style="font-size:14px;line-height:1.6;color:#1c1a2b;">
                          <strong style="font-weight:600;">Create a budget.</strong>
                          <span style="color:#4b4860;"> Pick weekly or monthly, then split it into categories (rent, food, savings).</span>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr>
                  <td style="padding:12px 0;border-top:1px solid #f4f2f8;">
                    <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0">
                      <tr>
                        <td width="32" valign="top" style="font-size:11px;color:#9b96b0;font-weight:600;letter-spacing:0.04em;line-height:1.6;padding-top:2px;">02</td>
                        <td style="font-size:14px;line-height:1.6;color:#1c1a2b;">
                          <strong style="font-weight:600;">Add your transactions.</strong>
                          <span style="color:#4b4860;"> Tag each one with a category and amount.</span>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr>
                  <td style="padding:12px 0;border-top:1px solid #f4f2f8;border-bottom:1px solid #f4f2f8;">
                    <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0">
                      <tr>
                        <td width="32" valign="top" style="font-size:11px;color:#9b96b0;font-weight:600;letter-spacing:0.04em;line-height:1.6;padding-top:2px;">03</td>
                        <td style="font-size:14px;line-height:1.6;color:#1c1a2b;">
                          <strong style="font-weight:600;">Open your insights.</strong>
                          <span style="color:#4b4860;"> The engine compares your spending to your plan, and the AI explains it like this:</span>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>

              <!-- AI quote -->
              <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="margin:18px 0;">
                <tr>
                  <td style="background-color:#faf9fc;border-left:3px solid #5b5bd6;padding:14px 16px;border-radius:0 6px 6px 0;font-size:13px;line-height:1.55;color:#403e55;font-style:italic;">
                    "You're $78 over on Food this week. Three of those were restaurant orders on Saturday. If the pattern holds, you'll miss your savings goal by about $120 this month."
                  </td>
                </tr>
              </table>

              <!-- CTA -->
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" style="margin:8px 0 22px;">
                <tr>
                  <td align="center" style="background-color:#5b5bd6;border-radius:6px;">
                    <a href="https://budget.bynily.dev/overview" target="_blank" style="display:inline-block;padding:11px 22px;color:#ffffff;font-size:14px;font-weight:600;text-decoration:none;font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',Roboto,Helvetica,Arial,sans-serif;">Open your dashboard</a>
                  </td>
                </tr>
              </table>

              <!-- Privacy reassurance -->
              <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="background-color:#faf9fc;border-radius:6px;padding:12px 14px;font-size:12px;line-height:1.55;color:#6c6880;">
                    Your data stays yours. We never share it, never sell it, and we won't send marketing email.
                  </td>
                </tr>
              </table>

              <!-- Footer -->
              <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-top:28px;">
                <tr>
                  <td style="border-top:1px solid #f4f2f8;padding-top:16px;font-size:11px;line-height:1.55;color:#9b96b0;">
                    <span style="font-weight:600;color:#1c1a2b;">— Nily</span><br>
                    <a href="https://budget.bynily.dev" target="_blank" style="color:#9b96b0;text-decoration:underline;">budget.bynily.dev</a>
                  </td>
                </tr>
              </table>

            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
```

## Final plain-text — welcome template

```
You're in, {{USER}}.

Personal Budget turns your spending into plain-language advice.
Here's how it works in three steps:

  01  Create a budget. Pick weekly or monthly, then split it into
      categories (rent, food, savings).
  02  Add your transactions. Tag each one with a category and amount.
  03  Open your insights. The engine compares your spending to your
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

## Final HTML — account-deleted template

Paste into the deletion template in the Resend dashboard. Subject line:
`Your Personal Budget account is gone`.

```html
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html dir="ltr" lang="en">
<head>
  <meta content="width=device-width" name="viewport" />
  <meta content="text/html; charset=UTF-8" http-equiv="Content-Type" />
  <meta name="x-apple-disable-message-reformatting" />
  <meta content="IE=edge" http-equiv="X-UA-Compatible" />
  <meta content="telephone=no,address=no,email=no,date=no,url=no" name="format-detection" />
  <title>Your Personal Budget account is gone</title>
</head>
<body style="margin:0;padding:0;background-color:#faf9fc;">
  <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color:#faf9fc;">
    <tr>
      <td align="center" style="padding:36px 16px;">
        <table role="presentation" width="600" border="0" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
          <tr>
            <td style="background-color:#ffffff;border:1px solid #ece9f3;border-radius:10px;padding:36px 32px;font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1c1a2b;">

              <!-- Wordmark -->
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" style="margin:0 0 4px;">
                <tr>
                  <td width="24" height="24" align="center" valign="middle" style="background-color:#5b5bd6;border-radius:6px;color:#ffffff;font-family:Georgia,'Iowan Old Style',serif;font-weight:700;font-size:14px;line-height:24px;">P</td>
                  <td style="padding-left:10px;vertical-align:middle;font-size:14px;font-weight:600;color:#1c1a2b;letter-spacing:-0.01em;line-height:24px;">Personal Budget</td>
                </tr>
              </table>

              <!-- Headline -->
              <h1 style="margin:22px 0 16px;font-size:24px;line-height:1.25;font-weight:600;letter-spacing:-0.02em;color:#1c1a2b;">
                Hey {{USER}}, your account is gone.
              </h1>

              <!-- Body -->
              <p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:#4b4860;">
                All clear on my side — your Personal Budget account, transactions, budgets, and goals have been permanently deleted. Nothing left to worry about.
              </p>
              <p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:#4b4860;">
                If life ever brings you back, the door's open. And if there's something I could have done better, I'd genuinely love to hear it — just reply to this email. I read every one.
              </p>
              <p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:#4b4860;">
                Take care of yourself.
              </p>

              <!-- Footer -->
              <table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="margin-top:28px;">
                <tr>
                  <td style="border-top:1px solid #f4f2f8;padding-top:16px;font-size:11px;line-height:1.55;color:#9b96b0;">
                    <span style="font-weight:600;color:#1c1a2b;">— Nily</span><br>
                    <a href="https://budget.bynily.dev" target="_blank" style="color:#9b96b0;text-decoration:underline;">budget.bynily.dev</a>
                  </td>
                </tr>
              </table>

            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
```

## Final plain-text — account-deleted template

```
Hey {{USER}}, your account is gone.

All clear on my side — your Personal Budget account, transactions,
budgets, and goals have been permanently deleted. Nothing left to
worry about.

If life ever brings you back, the door's open. And if there's
something I could have done better, I'd genuinely love to hear it —
just reply to this email. I read every one.

Take care of yourself.

— Nily
budget.bynily.dev
```

## From-address change

In `.env.example` (line 63), change:

```
RESEND_FROM_EMAIL=hello@bynily.dev
```

to:

```
RESEND_FROM_EMAIL=Personal Budget <nily@bynily.dev>
```

The Resend Python SDK accepts the RFC 5322 `Display Name <addr>` format
in the `from` field, and the existing call site in
`app/services/email_service.py` (`"from": settings.resend_from_email`)
passes the value through unmodified.

In production, update the `RESEND_FROM_EMAIL` env var on Render
(see `render.yaml:27`) to the same value.

DNS verification: Resend already verifies `bynily.dev` (it's the current
sender domain). Switching the prefix from `hello@` to `nily@` requires no
new DNS work.

## First-name fallback

`send_welcome_email` and `send_goodbye_email` in
`app/services/email_service.py` currently pass `first_name` directly into
the `USER` variable. If Clerk's `user.created` payload has no
`first_name`, the value is `None`, which Resend's templating engine
substitutes inconsistently (some engines render `null`, others leave the
literal `{{USER}}`).

Change both calls from:

```python
"variables": {
    "USER": first_name,
},
```

to:

```python
"variables": {
    "USER": first_name or "there",
},
```

This keeps the rendered text natural ("You're in, there." / "Hey there,
your account is gone.") for users who didn't provide a first name during
sign-up. The pattern is the same one ratified in
`docs/superpowers/specs/2026-04-25-welcome-email-content-design.md`.

## Verification

1. **Inbox preview.** After pasting both HTML bodies into Resend's
   dashboard, send a test email from Resend's preview tool (or via the
   `/emails/welcome` and `/emails/goodbye` endpoints with a stub Clerk
   payload) to a test address. Inspect rendering in:
   - Gmail web (Chrome/Safari)
   - Apple Mail (macOS)
   - Outlook desktop (Windows) — the limiting client; verify card
     border, button, and step-row rendering specifically.
2. **Image-blocked rendering.** In Gmail, use "Display images: ask
   before displaying" mode and confirm the wordmark "P" tile still
   renders (CSS-only, no remote image dependency).
3. **Variable substitution.** Send a test with `first_name = null` and
   confirm the email reads "You're in, there." (verifies the Python
   fallback) and not "You're in, ." or "You're in, {{USER}}."
4. **From-address.** Confirm inbox shows sender as
   "Personal Budget" with the email "nily@bynily.dev" beneath it (Gmail
   format) or "Personal Budget <nily@bynily.dev>" (Apple Mail format).
5. **Plain-text fallback.** Force-disable HTML in your client (or use
   `mutt`/Apple Mail's "View → Plain Text Alternative") and confirm the
   text version reads coherently.

## Migration & deployment

In order:

1. **Paste new HTML and text** into the `welcome-personal-budget` and
   `account-deleted` templates in Resend's dashboard. Save each.
2. **Resend preview** for both templates with `USER = "Nilyan"` and
   `USER = "there"` (the fallback string). Visually verify both render.
3. **Update `.env.example`** with the new `RESEND_FROM_EMAIL` value and
   commit.
4. **Update Render env var** for `RESEND_FROM_EMAIL` in production to
   match.
5. **Apply the `first_name or "there"` fallback** to both functions in
   `app/services/email_service.py` and commit.
6. **Smoke test in production** — sign up a throwaway Clerk user with no
   first name set, confirm the welcome email arrives with the correct
   from-address and "there" fallback. Then delete that user and confirm
   the goodbye email arrives.

## Follow-ups (separate specs, if ever)

- **Migrate templates into the codebase** as Jinja2 files under
  `app/templates/emails/` (option B from brainstorming) once the design
  is stable. Buys: PR review, version control, unit tests against the
  rendered HTML. The HTML in this spec ports unchanged.
- **Add a verified second domain** (e.g., `personalbudget.app`) and
  switch the from-address to it. Buys: stronger brand separation from
  `bynily.dev`. Cost: DNS work + Resend domain verification.
- **Promote `— Nily` and `budget.bynily.dev` to settings** so the
  templates are reusable if the brand changes.
