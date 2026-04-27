# Finance Insights Engine

Personal budget API that computes financial insights and uses AI
to explain them in plain language.

## Tech Stack

- **FastAPI** — async Python web framework
- **Supabase** — managed PostgreSQL database (Clerk configured as
  Third-Party Auth provider; RLS keyed on `auth.jwt() ->> 'sub'`)
- **[Clerk](https://clerk.com)** — auth provider. Issues RS256 JWTs;
  backend verifies them against Clerk's JWKS endpoint. Wired up via
  [Clerk's Supabase integration](https://clerk.com/docs/integrations/databases/supabase)
- **Pandas** — data aggregation and analysis
- **LiteLLM** — provider-agnostic AI (Claude, GPT, Gemini, Groq)
- **Pydantic v2** — data validation and settings

## Prerequisites

- Python 3.13+
- A Supabase project (free tier works)
- An API key from at least one AI provider (Anthropic, OpenAI, Google, or Groq)

## Setup

### 1. Clone and enter the project

```bash
git clone <your-repo-url>
cd personal-budget-api
```

### 2. Create a virtual environment

A virtual environment keeps this project's packages
separate from your system Python. **Use Python 3.13 explicitly** —
macOS ships Python 3.9 as `python3`, and this project uses modern
union type syntax (`str | None`) that older Python versions cannot
parse.

```bash
python3.13 -m venv venv
source venv/bin/activate
```

If `python3.13` is not found, install it first:

```bash
# macOS
brew install python@3.13

# or with pyenv (reads .python-version automatically)
pyenv install 3.13
```

Verify the venv is on 3.13:

```bash
python --version   # → Python 3.13.x
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy the example file and fill in your keys.

```bash
cp .env.example .env
```

Open `.env` and set:
- `SUPABASE_URL` — your project URL from Supabase dashboard
- `SUPABASE_ANON_KEY` — your anon key (the per-request user JWT carries
  the actual authorization; the anon key is just the baseline client)
- `CLERK_ISSUER` — your Clerk instance URL (e.g.
  `https://worthy-hornet-72.clerk.accounts.dev`). Required — app refuses
  to start without it
- `CLERK_JWKS_URL` — optional; defaults to
  `{CLERK_ISSUER}/.well-known/jwks.json`
- `AI_MODEL` — which model to use (default: `anthropic/claude-haiku-4-5-20251001`)
- Your provider's API key (e.g. `ANTHROPIC_API_KEY`)

## Running

First activate the virtual environment (this puts `uvicorn` and the
project's pinned dependencies on your `PATH`):

```bash
source venv/bin/activate
```

Your shell prompt should now be prefixed with `(venv)`. Then start the
development server:

```bash
uvicorn app.main:app --reload --reload-dir app
```

`--reload-dir app` scopes the file watcher to `app/`, so writes under
`venv/`, `.pytest_cache/`, or `__pycache__/` don't trigger spurious
restarts. Override host or port with `--host` / `--port`.

The API will be available at `http://localhost:8000`.
Browse the interactive API docs at `http://localhost:8000/docs`.

To stop, press `Ctrl+C`. To leave the venv afterwards, run `deactivate`.

### Running without activating

If you'd rather skip activation, invoke the venv's `uvicorn` directly:

```bash
venv/bin/uvicorn app.main:app --reload --reload-dir app
```

### Troubleshooting

- **`zsh: command not found: uvicorn`** — the venv isn't activated.
  Run `source venv/bin/activate` first, or use the
  `venv/bin/uvicorn ...` form above.
- **`TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`**
  at startup — you're running uvicorn with macOS's bundled Python 3.9
  instead of the venv's 3.13. Activate the venv (or run
  `venv/bin/uvicorn ...` explicitly) — the error comes from
  `str | None` syntax that requires Python 3.10+.

## Auth

Requests are authenticated with **Clerk JWTs (RS256)**. The frontend
obtains a token from the Clerk SDK and sends it as
`Authorization: Bearer <token>`; the backend verifies it against Clerk's
JWKS endpoint (cached per process) and enforces `iss`, `aud`, `exp`, and
`sub` claims. Supabase separately re-verifies the same token through its
Third-Party Auth (Clerk) provider, so RLS policies authorize each row by
comparing `auth.jwt() ->> 'sub'` to `user_id`.

```
Client (Clerk SDK)
  │  Authorization: Bearer <clerk-rs256-jwt>
  ▼
FastAPI  →  get_user_ctx()  →  PyJWKClient (cached) → jwt.decode(RS256, iss, aud)
  │                                                                │
  ▼                                                                ▼
UserContext(user_id, per-request Supabase client)          401 on any failure
  │
  ▼
Supabase PostgREST (RLS: auth.jwt() ->> 'sub' = user_id)
```

All JWT verification lives in `app/routes/deps.py` and `app/auth/jwks.py`;
services never see the raw token.

### Grabbing a token for local curl testing

In the frontend DevTools console, while signed in:

```js
await window.Clerk.session.getToken({ template: 'supabase' })
```

Then:

```bash
curl -H "Authorization: Bearer <token>" http://localhost:8000/insights
```

Clerk tokens live ~60 s — refresh via the same snippet if yours expires.

## Testing

```bash
pytest
```

## Switching AI Providers

Change the `AI_MODEL` variable in `.env` to swap providers
with zero code changes:

```bash
# Anthropic (default)
AI_MODEL=anthropic/claude-haiku-4-5-20251001

# OpenAI
AI_MODEL=gpt-4o-mini

# Google
AI_MODEL=gemini/gemini-1.5-flash

# Groq (has free tier)
AI_MODEL=groq/llama-3.1-8b-instant
```

Make sure to set the matching API key for your chosen provider.

## License

MIT
