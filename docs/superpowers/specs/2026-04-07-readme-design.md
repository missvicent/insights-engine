# Design Spec: README.md for Finance Insights Engine

## Goal

Create a README.md that serves as both a personal reference (with step-by-step
explanations) and a polished portfolio piece for GitHub visitors.

## Approach

Portfolio-friendly README: clear tagline, tech stack, step-by-step setup with
explanations, run/test commands, AI provider switching section, link to /docs
for API reference. ~70 lines.

## Decisions

- Audience: personal reference + portfolio visitors
- Setup detail: step-by-step with brief explanations of each command
- API docs: link to FastAPI auto-generated /docs (no endpoint listing)
- Architecture: not covered here (lives in CLAUDE.md)
- License: MIT

---

## Full README.md Content

```markdown
# Finance Insights Engine

Personal budget API that computes financial insights and uses AI
to explain them in plain language.

## Tech Stack

- **FastAPI** — async Python web framework
- **Supabase** — managed PostgreSQL database
- **Pandas** — data aggregation and analysis
- **LiteLLM** — provider-agnostic AI (Claude, GPT, Gemini, Groq)
- **Pydantic v2** — data validation and settings

## Prerequisites

- Python 3.12+
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
separate from your system Python.

```bash
python3 -m venv venv
source venv/bin/activate
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
- `SUPABASE_SERVICE_KEY` — your service role key (keep secret)
- `AI_MODEL` — which model to use (default: `anthropic/claude-haiku-4-5-20251001`)
- Your provider's API key (e.g. `ANTHROPIC_API_KEY`)

## Running

Start the development server:

```bash
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`.
Browse the interactive API docs at `http://localhost:8000/docs`.

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
```
