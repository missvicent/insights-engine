# Followups

Tracked items surfaced during reviews/brainstorms that aren't blocking the current change. Each file carries `status` frontmatter; this index mirrors the state for quick scanning.

**Workflow when resolving:**
1. Flip `status: open` → `resolved` in the file's frontmatter, fill `resolved_date` + `resolved_by` (commit SHA or PR).
2. Move the entry below from **Open** to **Resolved** with date + ref.
3. Commit.

## Open

- [scope-transactions-to-budget](2026-04-14-scope-transactions-to-budget.md) — _important_ — transactions query ignores selected `budget_id` (multi-budget correctness gap)
- [compute-goal-progress-logic](2026-04-14-compute-goal-progress-logic.md) — _minor_ — `compute_goal_progress` returns `[]`; needs real logic + tests
- [delete-insight-period](2026-04-14-delete-insight-period.md) — _minor_ — remove unreferenced `InsightPeriod` Pydantic model
- [portable-period-label-strftime](2026-04-14-portable-period-label-strftime.md) — _minor_ — `_format_period_label` breaks on Windows (`%-d`)
- [rename-end-of-month-concentration](2026-04-14-rename-end-of-month-concentration.md) — _minor_ — rename `end_of_period_concentration` for accuracy
- [plan-typo-test-3m](2026-04-14-plan-typo-test-3m.md) — _trivial_ — off-by-one typo in plan doc

## Resolved

- [unregister-ai-insights-stub](2026-04-14-unregister-ai-insights-stub.md) — resolved 2026-04-14 (commit `5991750`) — `/ai-insights` router removed
