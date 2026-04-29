-- Rename budget_items table to allocations for consistent naming across the codebase.
-- Using IF EXISTS since the rename may have already been applied in a partial run.

ALTER TABLE IF EXISTS budget_items RENAME TO allocations;

-- Drop and recreate get_budgets_with_progress (return type changed: item_id -> allocation_id)
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
  allocation_id uuid,
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
    a.id AS allocation_id,
    a.category_id,
    a.amount,
    a.alert_enabled,
    a.alert_threshold,
    c.name AS category_name,
    c.category_type,
    c.color AS category_color,
    c.icon AS category_icon,
    COALESCE(SUM(t.amount) FILTER (
      WHERE t.type = 'expense'
        AND t.budget_id = b.id
        AND t.category_id = a.category_id
    ), 0) AS progress
  FROM budgets b
  JOIN allocations a ON a.budget_id = b.id
  JOIN categories c ON c.id = a.category_id
  LEFT JOIN transactions t ON t.budget_id = b.id AND t.category_id = a.category_id
  WHERE b.user_id = (auth.jwt()->>'sub')
  GROUP BY b.id, a.id, c.id
  ORDER BY b.created_at DESC, c.name;
$$;

-- Recreate assign_budget_to_transaction trigger function with allocations table
CREATE OR REPLACE FUNCTION assign_budget_to_transaction()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.type = 'expense' AND NEW.budget_id IS NULL AND NEW.category_id IS NOT NULL THEN
    SELECT b.id INTO NEW.budget_id
    FROM budgets b
    JOIN allocations a ON a.budget_id = b.id
    WHERE a.category_id = NEW.category_id
      AND b.user_id = NEW.user_id
      AND b.is_active = true
      AND NEW.transaction_date >= b.start_date
      AND (b.end_date IS NULL OR NEW.transaction_date <= b.end_date)
    ORDER BY b.created_at DESC
    LIMIT 1;
  END IF;

  RETURN NEW;
END;
$$;
