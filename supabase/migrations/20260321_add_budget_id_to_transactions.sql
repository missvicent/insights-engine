-- Add budget_id column to transactions table
ALTER TABLE transactions
  ADD COLUMN budget_id uuid REFERENCES budgets(id) ON DELETE SET NULL;

-- Index for join performance
CREATE INDEX idx_transactions_budget_id ON transactions(budget_id);

-- Backfill existing expense transactions:
-- Match via category_id + budget_items + date range
-- Prefer most recently created budget when ambiguous
UPDATE transactions t
SET budget_id = matched.budget_id
FROM (
  SELECT DISTINCT ON (t2.id)
    t2.id AS transaction_id,
    b.id AS budget_id
  FROM transactions t2
  JOIN budget_items bi ON bi.category_id = t2.category_id
  JOIN budgets b ON b.id = bi.budget_id
  WHERE t2.type = 'expense'
    AND t2.transaction_date >= b.start_date
    AND (b.end_date IS NULL OR t2.transaction_date <= b.end_date)
  ORDER BY t2.id, b.created_at DESC
) matched
WHERE t.id = matched.transaction_id;
