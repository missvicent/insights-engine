CREATE TABLE IF NOT EXISTS account_deletion_audit (
    id BIGSERIAL PRIMARY KEY,
    user_id_hash bytea NOT NULL,
    event TEXT NOT NULL CHECK (event IN (
        'request_created',
        'request_confirmed',
        'request_cancelled',
        'clerk_delete_called',
        'user_data_deleted',
        'request_failed'
    )),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'
);

ALTER TABLE account_deletion_audit ENABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS idx_account_deletion_audit_user_hash 
    ON account_deletion_audit (user_id_hash, occurred_at DESC);

CREATE TABLE IF NOT EXISTS webhook_events (
    svix_id TEXT PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE webhook_events ENABLE ROW LEVEL SECURITY;
