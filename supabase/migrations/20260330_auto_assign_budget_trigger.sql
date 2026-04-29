-- Auto-assign budget_id to expense transactions when not explicitly provided.
-- Matches via category_id + budget_items + date range, same logic as backfill in 20260321.

CREATE OR REPLACE FUNCTION assign_budget_to_transaction()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  -- Only auto-assign for expenses without an explicit budget_id
  IF NEW.type = 'expense' AND NEW.budget_id IS NULL AND NEW.category_id IS NOT NULL THEN
    SELECT b.id INTO NEW.budget_id
    FROM budgets b
    JOIN budget_items bi ON bi.budget_id = b.id
    WHERE bi.category_id = NEW.category_id
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

CREATE TRIGGER trg_assign_budget_to_transaction
  BEFORE INSERT OR UPDATE ON transactions
  FOR EACH ROW
  EXECUTE FUNCTION assign_budget_to_transaction();
