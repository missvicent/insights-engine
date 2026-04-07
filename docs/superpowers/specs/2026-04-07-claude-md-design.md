# Design Spec: CLAUDE.md for Finance Insights Engine

## Goal

Create a `CLAUDE.md` file that gives Claude Code (and the developer) clear
guidelines for working in this project. It captures the stack, structure,
architecture rules, code style, and anti-patterns — so every session starts
with the right context.

## Approach

Architecture-aware CLAUDE.md: essentials + the core design principle
("engine owns the math, AI only narrates") + library-specific guidance.
~80 lines, concise enough to maintain as the project evolves.

---

## Full CLAUDE.md Content

### Header

```
# Finance Insights Engine — Claude Code Guidelines

## Project
FastAPI backend that computes financial insights from Supabase
and generates AI recommendations via LiteLLM + Anthropic Claude.
```

### Stack

```
## Stack
- Python 3.12
- FastAPI + Uvicorn
- Supabase (PostgreSQL) via supabase-py
- Pandas for data aggregation
- LiteLLM for AI provider abstraction
- Pydantic v2 for validation
- pytest for tests
```

### Structure

```
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
```

### Code Style

```
## Code style — PEP 8 + type hints

- snake_case for variables and functions, PascalCase for classes
- Type hints on every function signature — parameters and return type
- 4 spaces indentation, never tabs
- Two blank lines between top-level functions
- Imports grouped: stdlib → third-party → local
- Max line length: 88 characters
```

Example:
```python
# Correct
def calculate_totals(transactions: list[TransactionRow]) -> dict[str, float]:
    income = sum(t.amount for t in transactions if t.type == "income")
    return {"income": income}

# Wrong — no type hints, camelCase
def calculateTotals(transactions):
    ...
```

### Architecture Rules

```
## Architecture rules — enforce these strictly

1. insights_engine.py is pure Python functions — no AI calls, no DB calls
2. ai_service.py receives InsightSummary only — never raw transactions
3. db/client.py owns all Supabase queries — routes never query DB directly
4. Routes are thin — fetch data, call engine, return response, nothing else
5. All Pydantic models live in schemas.py — no inline model definitions in routes
```

Core principle: **Raw transactions → deterministic engine → structured summary → AI explains it.** AI never touches numbers. The engine never guesses.

### Naming Conventions

```
## Naming conventions

- DB fetch functions: fetch_* (e.g. fetch_transactions, fetch_budgets)
- Engine functions: verb + noun (e.g. detect_anomalies, calculate_totals)
- Pydantic input models: *Row (e.g. TransactionRow, CategoryRow)
- Pydantic output models: descriptive noun (e.g. InsightSummary, Anomaly)
- Route files: named after the resource (e.g. insights.py, ai.py)
```

### Tests

```
## Tests

- Use pytest, keep fixtures simple
- Engine functions must have unit tests — no Supabase or AI needed
- Test with fake data — no live DB connections in tests
- One test file per service file
```

### Environment

```
## Environment

- All secrets in .env — never hardcoded
- .env is gitignored, .env.example is committed
- Settings loaded via pydantic-settings Settings class in db/client.py
- Switch AI providers by changing AI_MODEL in .env only — no code changes
```

### What NOT to Do

```
## What NOT to do

- Don't put business logic in routes
- Don't send raw transactions to the AI
- Don't query Supabase from services/ or models/
- Don't use camelCase for variables or functions
- Don't skip type hints to save time
- Don't catch bare except: — always except Exception as e:
```

---

## Decisions Made

| Decision | Choice | Rationale |
|---|---|---|
| Framework | FastAPI | Async, auto OpenAPI docs, Pydantic-native |
| Database | Supabase (supabase-py client) | Managed Postgres, auth, easy setup |
| AI integration | LiteLLM | Provider-agnostic, swap models via env var |
| AI role | Narration only | Engine computes truth, AI explains it — no hallucinated numbers |
| Structure | Modular flat (db/, models/, services/, routes/) | Clear separation without deep nesting |
| Code style | PEP 8 + type hints | Helps learning, catches mistakes early |
| Testing | pytest minimal | Simple while learning Python |
| Python version | 3.12 | Matches the venv already created |
