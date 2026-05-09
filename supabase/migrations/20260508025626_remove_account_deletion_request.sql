-- Drop account_deletion_requests + its indexes, and update delete_user_data
-- to stop referencing the dropped table.
--
-- Background: the simplified inline-wipe deletion design (see
-- docs/superpowers/specs/2026-05-06-account-deletion-simplified-design.md)
-- removes the 7-state lifecycle. account_deletion_audit stays for the
-- compliance trail; account_deletion_requests goes.
--
-- Order matters: replace delete_user_data() FIRST so there's no window
-- where the function references a non-existent table. plpgsql doesn't
-- validate references at parse time, but we still want a clean rollout.

-- 1. Replace delete_user_data: drop the account_deletion_requests UPDATE,
--    keep the FK-correct DELETE chain and the audit INSERT.
CREATE OR REPLACE FUNCTION public.delete_user_data(p_clerk_user_id text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
BEGIN
    -- FK-respecting deletion order. DELETE on a non-existent user is a no-op.
    DELETE FROM transactions             WHERE user_id       = p_clerk_user_id;
    DELETE FROM debt_payments            WHERE user_id       = p_clerk_user_id;
    DELETE FROM allocations              WHERE budget_id IN (
        SELECT id FROM budgets WHERE user_id = p_clerk_user_id
    );
    DELETE FROM budget_archive_reports   WHERE user_id       = p_clerk_user_id;
    DELETE FROM recurring_transactions   WHERE user_id       = p_clerk_user_id;
    DELETE FROM debts                    WHERE user_id       = p_clerk_user_id;
    DELETE FROM goals                    WHERE user_id       = p_clerk_user_id;
    DELETE FROM budgets                  WHERE user_id       = p_clerk_user_id;
    DELETE FROM accounts                 WHERE user_id       = p_clerk_user_id;
    DELETE FROM user_settings            WHERE user_id       = p_clerk_user_id;
    DELETE FROM profiles                 WHERE clerk_user_id = p_clerk_user_id;

    INSERT INTO public.account_deletion_audit (user_id_hash, event, metadata)
    VALUES (
        extensions.digest(p_clerk_user_id, 'sha256'),
        'user_data_deleted',
        jsonb_build_object('called_at', now())
    );
END;
$$;

-- 2. Drop the table. CASCADE not needed: no FK points at it. The 3 RLS
--    policies, the PK, and the CHECK constraint auto-drop with the table.
DROP TABLE IF EXISTS public.account_deletion_requests;

-- 3. The DROP TABLE above already removed these indexes. Kept here as
--    explicit no-ops so the migration's intent is obvious from reading.
DROP INDEX IF EXISTS public.idx_deletion_requests_due;
DROP INDEX IF EXISTS public.idx_deletion_requests_active_per_user;
DROP INDEX IF EXISTS public.idx_deletion_requests_user_status;
