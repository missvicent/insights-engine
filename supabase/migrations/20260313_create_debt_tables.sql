-- Create debts table
CREATE TABLE debts (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id TEXT NOT NULL DEFAULT (auth.jwt()->>'sub'),
  name TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('credit_card', 'personal_loan', 'auto_loan', 'student_loan', 'mortgage')),
  principal_amount NUMERIC(12,2) NOT NULL,
  interest_rate NUMERIC(5,3) NOT NULL,
  current_balance NUMERIC(12,2) NOT NULL CHECK (current_balance >= 0),
  minimum_payment NUMERIC(12,2) NOT NULL,
  start_date DATE NOT NULL,
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_debts_user ON debts(user_id);
CREATE INDEX idx_debts_active ON debts(user_id, is_active);

-- Create debt_payments table
CREATE TABLE debt_payments (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  debt_id UUID NOT NULL REFERENCES debts(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL DEFAULT (auth.jwt()->>'sub'),
  amount_paid NUMERIC(12,2) NOT NULL,
  principal_paid NUMERIC(12,2) NOT NULL,
  interest_paid NUMERIC(12,2) NOT NULL,
  payment_date DATE NOT NULL,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_debt_payments_debt ON debt_payments(debt_id);
CREATE INDEX idx_debt_payments_user ON debt_payments(user_id);

-- RLS policies
ALTER TABLE debts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users manage own debts" ON debts
  FOR ALL USING ((auth.jwt()->>'sub') = user_id)
  WITH CHECK ((auth.jwt()->>'sub') = user_id);

ALTER TABLE debt_payments ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users manage own debt payments" ON debt_payments
  FOR ALL USING ((auth.jwt()->>'sub') = user_id)
  WITH CHECK ((auth.jwt()->>'sub') = user_id);

-- Atomic payment RPC
CREATE OR REPLACE FUNCTION record_debt_payment(
  p_debt_id UUID,
  p_amount_paid NUMERIC,
  p_principal_paid NUMERIC,
  p_interest_paid NUMERIC,
  p_payment_date DATE,
  p_notes TEXT DEFAULT NULL
) RETURNS void AS $$
BEGIN
  INSERT INTO debt_payments (debt_id, user_id, amount_paid, principal_paid, interest_paid, payment_date, notes)
  VALUES (p_debt_id, (auth.jwt()->>'sub'), p_amount_paid, p_principal_paid, p_interest_paid, p_payment_date, p_notes);

  UPDATE debts SET current_balance = GREATEST(current_balance - p_principal_paid, 0), updated_at = now()
  WHERE id = p_debt_id AND user_id = (auth.jwt()->>'sub');
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Updated_at trigger
CREATE OR REPLACE FUNCTION update_debt_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER debts_updated_at
  BEFORE UPDATE ON debts
  FOR EACH ROW EXECUTE FUNCTION update_debt_updated_at();

-- Logical replication publication for ElectricSQL
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'electric_debt_pub') THEN
    CREATE PUBLICATION electric_debt_pub FOR TABLE debts, debt_payments;
  END IF;
END $$;
