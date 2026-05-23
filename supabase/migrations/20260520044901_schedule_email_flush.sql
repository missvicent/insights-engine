-- Schedule the nightly drain of public.pending_emails.
--
-- Producers (POST /webhooks/clerk and deletion_service.delete_account)
-- INSERT a row and queue a fast-path BackgroundTask that sends within
-- seconds in the happy case. This nightly cron is the durability
-- backstop: if the API process crashed between enqueue and the
-- BackgroundTask completing, the email sits in pending_emails until
-- 23:00 UTC, when the cron fires and the sweep picks it up.
--
-- The schedule body reads two values from Supabase Vault at run time,
-- so they stay out of git but the cron definition itself is
-- version-controlled. These two secrets must be set MANUALLY per
-- environment (local / staging / prod) before the cron will fire
-- successfully:
--
--     SELECT vault.create_secret('<value>', 'CRON_SHARED_SECRET');
--     SELECT vault.create_secret('<value>', 'API_BASE_URL');
--
-- CRON_SHARED_SECRET must match what the FastAPI app has in its
-- environment (Render env var, .env locally); see
-- app/routes/deps.py:verify_cron_secret.
--
-- API_BASE_URL must be the deployed host without trailing slash,
-- e.g. 'https://insights-engine.onrender.com'.

-- pg_cron and pg_net are Supabase-managed extensions; they aren't
-- available in vanilla Postgres (the image CI's docker-compose-smoke
-- uses). Guard the entire body so this file is a no-op when pg_cron
-- isn't available, and a real schedule when it is. Mirrors the
-- "shim in bootstrap when small / guard in migration when small"
-- pattern already used in supabase/ci/bootstrap.sql.
DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_available_extensions WHERE name = 'pg_cron'
    ) THEN
        RAISE NOTICE 'pg_cron unavailable - skipping schedule (CI/dev shim)';
        RETURN;
    END IF;

    EXECUTE 'CREATE EXTENSION IF NOT EXISTS pg_cron';
    EXECUTE 'CREATE EXTENSION IF NOT EXISTS pg_net';

    -- Idempotent: re-applying replaces the schedule rather than erroring on
    -- "job already exists". cron.unschedule(jobname) errors when missing,
    -- so guard with EXISTS.
    IF EXISTS (
        SELECT 1 FROM cron.job WHERE jobname = 'flush-pending-emails'
    ) THEN
        PERFORM cron.unschedule('flush-pending-emails');
    END IF;

    PERFORM cron.schedule(
        'flush-pending-emails',
        '0 23 * * *',  -- 23:00 UTC nightly
        $body$
        SELECT net.http_post(
            url := (
                SELECT decrypted_secret
                FROM vault.decrypted_secrets
                WHERE name = 'API_BASE_URL'
            ) || '/internal/emails/flush',
            headers := jsonb_build_object(
                'Authorization',
                'Bearer ' || (
                    SELECT decrypted_secret
                    FROM vault.decrypted_secrets
                    WHERE name = 'CRON_SHARED_SECRET'
                ),
                'Content-Type', 'application/json'
            ),
            body := '{}'::jsonb
        );
        $body$
    );
END
$migration$;
