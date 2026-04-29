-- Global budget overview: one row per budget with total spending
CREATE OR REPLACE FUNCTION get_budgets_overview()
RETURNS TABLE (
  budget_id uuid,
  budget_name text,
  budget_amount numeric,
  period text,
  start_date date,
  end_date date,
  is_active boolean,
  total_spent numeric
)
LANGUAGE sql
STABLE
SECURITY DEFINER
AS $$
  SELECT
    b.id AS budget_id,
    b.name AS budget_name,
    b.amount AS budget_amount,
    b.period,
    b.start_date,
    b.end_date,
    b.is_active,
    COALESCE(SUM(t.amount) FILTER (
      WHERE t.type = 'expense'
    ), 0) AS total_spent
  FROM budgets b
  LEFT JOIN transactions t ON t.budget_id = b.id
  WHERE b.user_id = (auth.jwt()->>'sub')
  GROUP BY b.id
  ORDER BY b.created_at DESC;
$$;
