-- Bootstrap stubs for CI Postgres (vanilla postgres:17-alpine).
-- Creates the minimum Supabase artifacts that the squashed initial
-- schema depends on. Production / dev use the real Supabase Postgres
-- image and never run this file.

CREATE ROLE anon NOINHERIT NOLOGIN;
CREATE ROLE authenticated NOINHERIT NOLOGIN;
CREATE ROLE service_role NOINHERIT NOLOGIN BYPASSRLS;

CREATE SCHEMA IF NOT EXISTS auth;
GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;

-- Stub: returns NULL in CI (no real JWT). RLS policies that compare
-- auth.jwt()->>'sub' to user_id won't match any rows — fine for a
-- smoke test that hits only public health endpoints.
CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb
  LANGUAGE sql STABLE AS $$ SELECT NULL::jsonb $$;

-- Stub: satisfies categories.user_id_fkey -> auth.users(id).
CREATE TABLE IF NOT EXISTS auth.users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid()
);
