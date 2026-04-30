CREATE TABLE IF NOT EXISTS account_deletion_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL DEFAULT (auth.jwt()->>'sub'),
    email TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'pending_confirmation',
        'scheduled',
        'cancelled',
        'processing',
        'clerk_called',
        'completed',
        'failed'
    )),
    confirmation_token_hash TEXT,
    confirmation_token_expires_at TIMESTAMPTZ,
    scheduled_deletion_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    clerk_called_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    failure_reason TEXT,
    last_error_at TIMESTAMPTZ,
    retry_count INT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS 
    idx_deletion_requests_due  
    ON account_deletion_requests (scheduled_deletion_at) 
    WHERE status = 'scheduled';

CREATE UNIQUE INDEX IF NOT EXISTS 
    idx_deletion_requests_active_per_user 
    ON account_deletion_requests (user_id) 
    WHERE status IN ('pending_confirmation', 'scheduled', 'processing', 'clerk_called');

CREATE INDEX IF NOT EXISTS
    idx_deletion_requests_user_status 
    ON account_deletion_requests (user_id, status);

ALTER TABLE account_deletion_requests ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "deletion_requests_select_own" ON account_deletion_requests;
CREATE POLICY "deletion_requests_select_own"
    ON account_deletion_requests
    FOR SELECT
    TO authenticated
    USING (user_id = auth.jwt()->>'sub');

DROP POLICY IF EXISTS "delete_requests_insert_own" ON account_deletion_requests;
CREATE POLICY "delete_requests_insert_own"
    ON account_deletion_requests
    FOR INSERT
    TO authenticated
    WITH CHECK (user_id = auth.jwt()->>'sub');

DROP POLICY IF EXISTS "delete_requests_cancel_own" ON account_deletion_requests;
CREATE POLICY "delete_requests_cancel_own"
    ON account_deletion_requests
    FOR UPDATE
    TO authenticated
    USING (user_id = auth.jwt()->>'sub')
    WITH CHECK (user_id = auth.jwt()->>'sub' AND status = 'cancelled');

