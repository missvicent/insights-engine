-- Add total budget cap column to budgets table
ALTER TABLE budgets
  ADD COLUMN amount numeric NOT NULL DEFAULT 0;
