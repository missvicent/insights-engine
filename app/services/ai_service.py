"""
ai_service.py
─────────────
Uses LiteLLM — one interface for any AI provider.
To switch providers, change AI_MODEL in .env only. No code changes.

  "anthropic/claude-haiku-4-5-20251001"  → Anthropic (current, fast + cheap)
  "anthropic/claude-sonnet-4-6"          → Anthropic (smarter, costs more)
  "gpt-4o-mini"                          → OpenAI
  "gemini/gemini-1.5-flash"              → Google
  "groq/llama-3.1-8b-instant"            → Groq (cheapest, free tier)

Architecture rule:
  - This file receives InsightSummary only — never raw transactions
  - All numbers come from the engine, AI only explains them
  - Always returns AIRecommendation — never raises an exception
"""

import json
import logging
import re
from pathlib import Path

import litellm
from litellm.exceptions import (
    APIError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)
from pydantic import ValidationError

from app.config import get_settings
from app.models.schemas import AIRecommendation, InsightSummary

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "insights_system.md").read_text(
    encoding="utf-8"
)


AI_FALLBACK = AIRecommendation(
    insights="AI analysis temporarily unavailable.",
    problems="No problems identified.",
    recommendations="No recommendations provided.",
    one_action="No one-action provided.",
)


def build_ai_prompt(summary: InsightSummary) -> str:
    """Serialize an InsightSummary into the JSON payload the AI sees."""
    payload = {
        "period": summary.period_label,
        "income": summary.total_income,
        "expenses": summary.total_expenses,
        "net": summary.net,
        "savings_rate": summary.savings_rate,
        "income_change_pct": summary.income_change_pct,
        "expenses_change_pct": summary.expenses_change_pct,
        "next_action_horizon_days": summary.next_action_horizon_days,
        "category_breakdown": [
            {
                "category_name": c.category_name,
                "total": c.total,
                "pct_of_total": c.pct_of_total,
                "budget_limit": c.budget_limit,
                "budget_used_pct": c.budget_used_pct,
                "transaction_count": c.transaction_count,
            }
            for c in summary.category_breakdown
        ],
        "anomalies": [
            {
                "type": a.type,
                "category_name": a.category_name,
                "message": a.message,
                "severity": a.severity,
                "amount": a.amount,
            }
            for a in summary.anomalies
        ],
        "patterns": [
            {
                "type": p.type,
                "message": p.message,
                "data": p.data,
            }
            for p in summary.patterns
        ],
        "goals": [
            {
                "name": g.name,
                "target_amount": g.target_amount,
                "current_amount": g.current_amount,
                "progress_pct": g.progress_pct,
                "days_remaining": g.days_remaining,
                "on_track": g.on_track,
            }
            for g in summary.goals
        ],
        "transaction_count": summary.transaction_count,
        "recurring_count": summary.recurring_count,
    }
    return json.dumps(payload, indent=2)


_CODE_FENCE_RE = re.compile(r"\A```(?:json)?\s*|```\s*\Z", re.MULTILINE)


def _strip_code_fences(raw: str) -> str:
    """Defensive: strip ```json ... ``` fences if the model added them.

    The leading fence may carry a `json` language hint; the trailing fence
    may have surrounding whitespace. Anchored to start/end so embedded
    triple-backticks inside a string field aren't accidentally clipped.
    """
    return _CODE_FENCE_RE.sub("", raw).strip()


async def generate_ai_insights(summary: InsightSummary) -> AIRecommendation:
    """Return an AIRecommendation — never raises. Falls back on any failure."""
    settings = get_settings()
    raw = ""

    try:
        response = await litellm.acompletion(
            model=settings.ai_model,
            max_tokens=1024,
            temperature=0.2,
            timeout=15,
            num_retries=2,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_ai_prompt(summary)},
            ],
        )
        raw = _strip_code_fences(response.choices[0].message.content.strip())
        data = json.loads(raw)
        return AIRecommendation.model_validate(data)

    except (RateLimitError, ServiceUnavailableError, Timeout, APIError) as e:
        logger.warning(
            "AI provider error (model=%s, budget=%s): %s",
            settings.ai_model,
            summary.budget_id,
            e,
        )
        return AI_FALLBACK
    except json.JSONDecodeError:
        logger.error(
            "AI returned non-JSON (model=%s, budget=%s): %r",
            settings.ai_model,
            summary.budget_id,
            raw,
        )
        return AI_FALLBACK
    except ValidationError as e:
        logger.warning(
            "AI response missing/invalid fields (model=%s, budget=%s): %s",
            settings.ai_model,
            summary.budget_id,
            e,
        )
        return AI_FALLBACK
    except Exception as e:
        logger.exception(
            "Unexpected failure in generate_ai_insights (budget=%s): %s",
            summary.budget_id,
            e,
        )
        return AI_FALLBACK
