-- Durable email outbox for welcome + account-deleted notifications.
--
-- Producers (POST /webhooks/clerk and deletion_service.delete_account)
-- INSERT a row and queue a fast-path send. A pg_cron job hitting
-- /internal/emails/flush every minute is the safety net. The two SQL
-- helper functions use FOR UPDATE SKIP LOCKED so the fast path and the
-- worker never double-send the same row.

CREATE TABLE IF NOT EXISTS public.pending_emails (
    id                 bigserial PRIMARY KEY,
    template           text NOT NULL
        CHECK (template IN ('welcome', 'account_deleted')),
    to_email           text NOT NULL,
    payload            jsonb NOT NULL DEFAULT '{}'::jsonb,
    attempts           int   NOT NULL DEFAULT 0,
    max_attempts       int   NOT NULL DEFAULT 8,
    next_run_at        timestamptz NOT NULL DEFAULT now(),
    last_attempted_at  timestamptz,
    last_error         text,
    sent_at            timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now()
);

-- Hot-path worker query: ready and unsent rows, ordered by readiness.
-- Partial index keeps it tiny once rows are sent.
CREATE INDEX IF NOT EXISTS idx_pending_emails_ready
    ON public.pending_emails (next_run_at)
    WHERE sent_at IS NULL;

-- Service-role only. No policies — authenticated users must never see
-- or modify this table.
ALTER TABLE public.pending_emails ENABLE ROW LEVEL SECURITY;


-- ─── Helper functions ────────────────────────────────────────────────

-- Claim a single row for the fast path.
-- Returns the row if it exists, is unsent, is due, and is not over
-- max_attempts. SKIP LOCKED means a parallel worker observing the same
-- row will skip it and get nothing (caller treats that as "already
-- claimed, nothing to do").
CREATE OR REPLACE FUNCTION public.claim_pending_email(p_id bigint)
RETURNS SETOF public.pending_emails
LANGUAGE sql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
    SELECT *
    FROM public.pending_emails
    WHERE id = p_id
      AND sent_at IS NULL
      AND next_run_at <= now()
      AND attempts < max_attempts
    FOR UPDATE SKIP LOCKED
$$;

-- Claim a batch of ready rows for the cron-driven sweeper.
-- Same SKIP LOCKED semantics; ordered by next_run_at so the oldest
-- ready row goes first.
CREATE OR REPLACE FUNCTION public.fetch_ready_pending_emails(p_limit int)
RETURNS SETOF public.pending_emails
LANGUAGE sql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
    SELECT *
    FROM public.pending_emails
    WHERE sent_at IS NULL
      AND next_run_at <= now()
      AND attempts < max_attempts
    ORDER BY next_run_at
    LIMIT p_limit
    FOR UPDATE SKIP LOCKED
$$;

-- Atomic failure bookkeeping: increment attempts, set last_error and
-- last_attempted_at, push next_run_at out per exponential backoff
-- (2 ^ new_attempts * 30 seconds). Done in SQL so we don't need to
-- read-then-write from Python.
CREATE OR REPLACE FUNCTION public.record_pending_email_failure(
    p_id    bigint,
    p_error text
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
BEGIN
    UPDATE public.pending_emails
    SET attempts          = attempts + 1,
        last_attempted_at = now(),
        last_error        = p_error,
        next_run_at       = now()
                            + (interval '30 seconds')
                              * power(2, attempts + 1)
    WHERE id = p_id;
END;
$$;


-- ─── Grants ──────────────────────────────────────────────────────────
-- Service-role only. anon/authenticated must not see the table or
-- call the helpers.
REVOKE ALL ON FUNCTION public.claim_pending_email(bigint)        FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fetch_ready_pending_emails(int)    FROM PUBLIC;
REVOKE ALL ON FUNCTION public.record_pending_email_failure(bigint, text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION public.claim_pending_email(bigint)     TO service_role;
GRANT EXECUTE ON FUNCTION public.fetch_ready_pending_emails(int) TO service_role;
GRANT EXECUTE ON FUNCTION public.record_pending_email_failure(bigint, text) TO service_role;

GRANT SELECT, INSERT, UPDATE ON public.pending_emails TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.pending_emails_id_seq TO service_role;
