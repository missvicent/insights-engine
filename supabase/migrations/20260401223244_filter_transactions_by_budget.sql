-- Redefine get_transactions_with_categories to require a budget_id parameter
-- so the expenses page only shows transactions belonging to the selected budget.

DROP FUNCTION IF EXISTS get_transactions_with_categories();

CREATE FUNCTION get_transactions_with_categories(p_budget_id uuid)
RETURNS TABLE (
  id uuid,
  amount numeric,
  category_id uuid,
  name text,
  icon text,
  color text,
  description text,
  transaction_date date,
  category_type text,
  is_recurring boolean
)
LANGUAGE sql
STABLE
SECURITY DEFINER
AS $$
  SELECT
    t.id,
    t.amount,
    t.category_id,
    COALESCE(c.name, 'Uncategorized') AS name,
    COALESCE(c.icon, '📦') AS icon,
    COALESCE(c.color, '#9E9E9E') AS color,
    t.description,
    t.transaction_date,
    COALESCE(c.category_type, 'general') AS category_type,
    t.is_recurring
  FROM transactions t
  LEFT JOIN categories c ON t.category_id = c.id
  WHERE t.user_id = (auth.jwt() ->> 'sub')::text
    AND t.budget_id = p_budget_id;
$$;
