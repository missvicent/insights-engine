-- Race fix: atomic claim with lease for pending_emails.
--
-- Original design (migration 20260520032657_pending_emails.sql) used
-- SELECT ... FOR UPDATE SKIP LOCKED inside claim_pending_email. That
-- held the row lock only for the duration of the PostgREST RPC
-- transaction — once PostgREST committed and returned the row to
-- Python, the lock was released. The subsequent Resend HTTP call
-- (hundreds of ms) and mark_pending_email_sent UPDATE happened in
-- *separate* transactions, leaving a window where a concurrent
-- claimant (e.g. the /internal/emails/flush worker firing while the
-- fast-path BackgroundTask was still awaiting Resend) could claim the
-- same row and send the email a second time.
--
-- Replace the body with an atomic UPDATE ... RETURNING that pushes
-- next_run_at 5 minutes into the future as a lease. Concurrent claim
-- attempts then see the row as "not due" and the WHERE clause filters
-- it out. On send success, mark_pending_email_sent sets sent_at —
-- future claims are excluded by `sent_at IS NULL`. On send failure,
-- record_pending_email_failure overrides next_run_at per the
-- exponential backoff. If a worker crashes mid-send, the 5-minute
-- lease expires and the row becomes claimable again — the same
-- recovery behaviour the original design intended.
--
-- The lease also touches last_attempted_at so observers can see when
-- the row was last touched. record_pending_email_failure overwrites
-- this on failure (no semantic change).
--
-- fetch_ready_pending_emails stays SELECT FOR UPDATE SKIP LOCKED —
-- it returns a candidate list; the per-row claim_pending_email is
-- the gate. (The cron flush worker iterates candidates and calls
-- claim_pending_email per row.)

CREATE OR REPLACE FUNCTION public.claim_pending_email(p_id bigint)
RETURNS SETOF public.pending_emails
LANGUAGE sql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
AS $$
    UPDATE public.pending_emails
    SET next_run_at       = now() + interval '5 minutes',
        last_attempted_at = now()
    WHERE id          = p_id
      AND sent_at     IS NULL
      AND next_run_at <= now()
      AND attempts    < max_attempts
    RETURNING *;
$$;
