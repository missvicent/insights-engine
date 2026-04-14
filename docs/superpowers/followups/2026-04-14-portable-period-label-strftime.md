# Follow-up: Make `_format_period_label` Windows-portable

**Severity:** Minor — works on macOS/Linux, breaks on Windows with `ValueError`.

## Problem

`app/services/insights_engine.py` → `_format_period_label` uses `%-d` (no leading zero), which is a GNU/BSD strftime extension. On Windows, `strftime("%-d")` raises `ValueError: Invalid format string`.

Current code:
```python
return f"{start.strftime('%b %-d')} – {end.strftime('%b %-d, %Y')}"
```

## Proposed fix

Use `date.day` directly instead of relying on a platform-specific directive:

```python
def _format_period_label(start: date, end: date) -> str:
    """Human-readable window label, e.g. 'Mar 15 – Apr 14, 2026'."""
    if start.year == end.year:
        return (
            f"{start.strftime('%b')} {start.day} – "
            f"{end.strftime('%b')} {end.day}, {end.year}"
        )
    return (
        f"{start.strftime('%b')} {start.day}, {start.year} – "
        f"{end.strftime('%b')} {end.day}, {end.year}"
    )
```

## Tests

- `TestBuildSummary.test_period_label_matches_window` already covers the same-year branch. It will keep passing with the new implementation.
- **Add** a cross-year test:
  ```python
  def test_period_label_crosses_year(self):
      summary = build_summary(
          budget=make_budget(),
          allocations=[], current=[], previous=[], goals=[],
          window_start=date(2025, 12, 15),
          window_end=date(2026, 1, 14),
      )
      assert summary.period_label == "Dec 15, 2025 – Jan 14, 2026"
  ```

## Verification

- `pytest tests/test_insights_engine.py::TestBuildSummary -v` → all cases green.
- Spot-check once on a Windows host if / when the project picks one up.
