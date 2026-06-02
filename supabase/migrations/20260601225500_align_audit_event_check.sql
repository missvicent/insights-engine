-- Align account_deletion_audit_event_check with the AuditEvent enum
-- the code actually emits.
--
-- The initial schema's constraint listed six values
-- (request_created, request_confirmed, request_cancelled,
-- clerk_delete_called, user_data_deleted, request_failed) for a
-- richer confirmation/cancellation flow. The simplified inline-wipe
-- deletion (2026-05-06 design) only emits three events:
--   - request_initiated   (user invoked delete)
--   - user_data_deleted   (delete_user_data() finished)
--   - clerk_delete_failed (Clerk admin API call raised)
--
-- The mismatch caused 23514 check_violation on POST /account/delete
-- whenever the route inserted AUDIT_REQUEST_INITIATED.
--
-- Note: the delete_user_data() SQL function (see
-- 20260508025626_remove_account_deletion_request.sql) inserts
-- 'user_data_deleted', which remains allowed.
--
-- Historical rows: any pre-existing rows holding the dropped values
-- are remapped to the closest current equivalent before the new
-- constraint is added, so the audit trail is preserved:
--   request_created       -> request_initiated  (same semantic: "delete begun")
--   request_confirmed     -> request_initiated  (closest: confirmation = initiation under the simplified flow)
--   clerk_delete_called   -> request_initiated  (we got as far as initiating; clerk_delete_failed would mean the call failed)
--   request_cancelled     -> request_initiated  (closest available; cancellation isn't tracked anymore)
--   request_failed        -> clerk_delete_failed (only failure mode the current code emits)

ALTER TABLE public.account_deletion_audit
    DROP CONSTRAINT IF EXISTS account_deletion_audit_event_check;

UPDATE public.account_deletion_audit
SET event = 'request_initiated'
WHERE event IN (
    'request_created',
    'request_confirmed',
    'clerk_delete_called',
    'request_cancelled'
);

UPDATE public.account_deletion_audit
SET event = 'clerk_delete_failed'
WHERE event = 'request_failed';

ALTER TABLE public.account_deletion_audit
    ADD CONSTRAINT account_deletion_audit_event_check
    CHECK (event = ANY (ARRAY[
        'request_initiated'::text,
        'user_data_deleted'::text,
        'clerk_delete_failed'::text
    ]));
