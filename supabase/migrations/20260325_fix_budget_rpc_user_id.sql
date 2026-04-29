-- Fix: use auth.jwt()->>'sub' (Clerk user ID) instead of auth.uid() (Supabase UUID)

DROP FUNCTION IF EXISTS get_budgets_with_progress();
CREATE FUNCTION get_budgets_with_progress()
RETURNS TABLE (
  budget_id uuid,
  budget_name text,
  budget_amount numeric,
  period text,
  start_date date,
  end_date date,
  is_active boolean,
  item_id uuid,
  category_id uuid,
  amount numeric,
  alert_enabled boolean,
  alert_threshold numeric,
  category_name text,
  category_type text,
  category_color text,
  category_icon text,
  progress numeric
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
    bi.id AS item_id,
    bi.category_id,
    bi.amount,
    bi.alert_enabled,
    bi.alert_threshold,
    c.name AS category_name,
    c.category_type,
    c.color AS category_color,
    c.icon AS category_icon,
    COALESCE(SUM(t.amount) FILTER (
      WHERE t.type = 'expense'
        AND t.budget_id = b.id
        AND t.category_id = bi.category_id
    ), 0) AS progress
  FROM budgets b
  JOIN budget_items bi ON bi.budget_id = b.id
  JOIN categories c ON c.id = bi.category_id
  LEFT JOIN transactions t ON t.budget_id = b.id AND t.category_id = bi.category_id
  WHERE b.user_id = (auth.jwt()->>'sub')
  GROUP BY b.id, bi.id, c.id
  ORDER BY b.created_at DESC, c.name;
$$;

DROP FUNCTION IF EXISTS get_budgets_overview();
CREATE FUNCTION get_budgets_overview()
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
