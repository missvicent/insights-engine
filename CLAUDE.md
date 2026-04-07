# Finance Insights Engine — Claude Code Guidelines

## Project
FastAPI backend that computes financial insights from Supabase
and generates AI recommendations via LiteLLM + Anthropic Claude.

## Stack
- Python 3.12
- FastAPI + Uvicorn
- Supabase (PostgreSQL) via supabase-py
- Pandas for data aggregation
- LiteLLM for AI provider abstraction
- Pydantic v2 for validation
- pytest for tests

## Structure

app/
  db/client.py           # Supabase connection + all query functions
  models/schemas.py      # All Pydantic models
  services/
    insights_engine.py   # Core logic — no AI here
    ai_service.py        # AI layer — no computation here
  routes/
    insights.py          # GET /insights
    ai.py                # GET /ai-insights
  main.py                # FastAPI app + CORS
tests/
  test_insights_engine.py

## Code style — PEP 8 + type hints

- snake_case for variables and functions, PascalCase for classes
- Type hints on every function signature — parameters and return type
- 4 spaces indentation, never tabs
- Two blank lines between top-level functions
- Imports grouped: stdlib → third-party → local
- Max line length: 88 characters

```python
# Correct
def calculate_totals(transactions: list[TransactionRow]) -> dict[str, float]:
    income = sum(t.amount for t in transactions if t.type == "income")
    return {"income": income}

# Wrong — no type hints, camelCase
def calculateTotals(transactions):
    ...
```

## Architecture rules — enforce these strictly

1. insights_engine.py is pure Python functions — no AI calls, no DB calls
2. ai_service.py receives InsightSummary only — never raw transactions
3. db/client.py owns all Supabase queries — routes never query DB directly
4. Routes are thin — fetch data, call engine, return response, nothing else
5. All Pydantic models live in schemas.py — no inline model definitions in routes

Core principle: Raw transactions → deterministic engine → structured summary → AI explains it. AI never touches numbers. The engine never guesses.

## Naming conventions

- DB fetch functions: `fetch_*` (e.g. `fetch_transactions`, `fetch_budgets`)
- Engine functions: verb + noun (e.g. `detect_anomalies`, `calculate_totals`)
- Pydantic input models: `*Row` (e.g. `TransactionRow`, `CategoryRow`)
- Pydantic output models: descriptive noun (e.g. `InsightSummary`, `Anomaly`)
- Route files: named after the resource (e.g. `insights.py`, `ai.py`)

## Tests

- Use pytest, keep fixtures simple
- Engine functions must have unit tests — no Supabase or AI needed
- Test with fake data — no live DB connections in tests
- One test file per service file

## Environment

- All secrets in .env — never hardcoded
- .env is gitignored, .env.example is committed
- Settings loaded via pydantic-settings Settings class in db/client.py
- Switch AI providers by changing AI_MODEL in .env only — no code changes

## What NOT to do

- Don't put business logic in routes
- Don't send raw transactions to the AI
- Don't query Supabase from services/ or models/
- Don't use camelCase for variables or functions
- Don't skip type hints to save time
- Don't catch bare `except:` — always `except Exception as e:`
