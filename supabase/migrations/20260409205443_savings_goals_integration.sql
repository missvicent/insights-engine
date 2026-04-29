-- 1. Add goal_id to allocations (nullable FK to goals)
ALTER TABLE allocations
  ADD COLUMN goal_id uuid REFERENCES goals(id);

-- 2. Make category_id nullable (was implicitly NOT NULL)
ALTER TABLE allocations
  ALTER COLUMN category_id DROP NOT NULL;

-- 3. XOR constraint: allocation must have category_id OR goal_id, not both
ALTER TABLE allocations
  ADD CONSTRAINT allocation_category_or_goal_check
  CHECK (
    (category_id IS NOT NULL AND goal_id IS NULL)
    OR (category_id IS NULL AND goal_id IS NOT NULL)
  );

-- 4. Add goal_id to transactions (nullable FK to goals)
ALTER TABLE transactions
  ADD COLUMN goal_id uuid REFERENCES goals(id);

-- Indexes on new goal_id columns
CREATE INDEX idx_allocations_goal_id ON allocations(goal_id);
CREATE INDEX idx_transactions_goal_id ON transactions(goal_id);

-- 5. Drop and recreate get_budgets_with_progress to include savings allocations
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
  goal_id uuid,
  amount numeric,
  alert_enabled boolean,
  alert_threshold numeric,
  category_name text,
  category_type text,
  category_color text,
  category_icon text,
  goal_name text,
  progress numeric
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
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
    a.goal_id,
    a.amount,
    a.alert_enabled,
    a.alert_threshold,
    c.name AS category_name,
    c.category_type,
    c.color AS category_color,
    c.icon AS category_icon,
    g.name AS goal_name,
    COALESCE(SUM(t.amount) FILTER (
      WHERE t.budget_id = b.id
        AND (
          (a.category_id IS NOT NULL AND t.type = 'expense' AND t.category_id = a.category_id)
          OR (a.goal_id IS NOT NULL AND t.goal_id = a.goal_id)
        )
    ), 0) AS progress
  FROM public.budgets b
  JOIN public.allocations a ON a.budget_id = b.id
  LEFT JOIN public.categories c ON c.id = a.category_id
  LEFT JOIN public.goals g ON g.id = a.goal_id
  LEFT JOIN public.transactions t ON t.budget_id = b.id
    AND (
      (a.category_id IS NOT NULL AND t.category_id = a.category_id)
      OR (a.goal_id IS NOT NULL AND t.goal_id = a.goal_id)
    )
  WHERE b.user_id = (auth.jwt()->>'sub')
  GROUP BY b.id, a.id, c.id, g.id
  ORDER BY b.created_at DESC, COALESCE(c.name, g.name);
$$;

-- 6. Update auto-assign trigger to handle goal-based allocations
CREATE OR REPLACE FUNCTION assign_budget_to_transaction()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  -- Existing: auto-assign budget for expense transactions with a category
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

  -- New: auto-assign budget for transactions with a goal_id
  IF NEW.budget_id IS NULL AND NEW.goal_id IS NOT NULL THEN
    SELECT b.id INTO NEW.budget_id
    FROM budgets b
    JOIN allocations a ON a.budget_id = b.id
    WHERE a.goal_id = NEW.goal_id
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

-- 7. Create RPC to get goals with computed progress
CREATE OR REPLACE FUNCTION get_goals_with_progress()
RETURNS TABLE (
  id uuid,
  name text,
  target_amount numeric,
  current_amount numeric,
  target_date date,
  category text,
  notes text,
  is_achieved boolean,
  achieved_date date,
  created_at timestamptz,
  budget_contributions numeric,
  direct_contributions numeric
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT
    g.id,
    g.name::text,
    g.target_amount,
    COALESCE(SUM(t.amount), 0) AS current_amount,
    g.target_date,
    g.category::text,
    g.notes,
    g.is_achieved,
    g.achieved_date,
    g.created_at,
    COALESCE(SUM(t.amount) FILTER (WHERE t.budget_id IS NOT NULL), 0) AS budget_contributions,
    COALESCE(SUM(t.amount) FILTER (WHERE t.budget_id IS NULL), 0) AS direct_contributions
  FROM public.goals g
  LEFT JOIN public.transactions t ON t.goal_id = g.id
  WHERE g.user_id = (auth.jwt()->>'sub')
  GROUP BY g.id
  ORDER BY g.is_achieved ASC, g.created_at DESC;
$$;

-- 8. Auto-achieve trigger: mark goal achieved when contributions >= target, un-achieve on reversal
CREATE OR REPLACE FUNCTION check_goal_achievement()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
  total numeric;
  goal_record RECORD;
  check_goal_id uuid;
BEGIN
  -- Determine which goal_id to check
  IF TG_OP = 'DELETE' THEN
    check_goal_id := OLD.goal_id;
  ELSE
    check_goal_id := NEW.goal_id;
  END IF;

  -- Also check old goal_id on UPDATE if it changed
  IF TG_OP = 'UPDATE' AND OLD.goal_id IS DISTINCT FROM NEW.goal_id AND OLD.goal_id IS NOT NULL THEN
    SELECT target_amount, is_achieved INTO goal_record FROM public.goals WHERE id = OLD.goal_id;
    IF goal_record.is_achieved THEN
      SELECT COALESCE(SUM(amount), 0) INTO total FROM public.transactions WHERE goal_id = OLD.goal_id;
      IF total < goal_record.target_amount THEN
        UPDATE public.goals SET is_achieved = false, achieved_date = NULL, updated_at = now() WHERE id = OLD.goal_id;
      END IF;
    END IF;
  END IF;

  IF check_goal_id IS NULL THEN
    RETURN COALESCE(NEW, OLD);
  END IF;

  SELECT target_amount, is_achieved INTO goal_record FROM public.goals WHERE id = check_goal_id;

  SELECT COALESCE(SUM(amount), 0) INTO total FROM public.transactions WHERE goal_id = check_goal_id;

  IF total >= goal_record.target_amount AND NOT goal_record.is_achieved THEN
    UPDATE public.goals SET is_achieved = true, achieved_date = CURRENT_DATE, updated_at = now() WHERE id = check_goal_id;
  ELSIF total < goal_record.target_amount AND goal_record.is_achieved THEN
    UPDATE public.goals SET is_achieved = false, achieved_date = NULL, updated_at = now() WHERE id = check_goal_id;
  END IF;

  RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE TRIGGER trg_check_goal_achievement
  AFTER INSERT OR UPDATE OF amount, goal_id OR DELETE ON transactions
  FOR EACH ROW
  EXECUTE FUNCTION check_goal_achievement();
