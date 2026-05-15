-- Baseline schema generated from remote on 2026-05-07.
-- Squashes 12 prior migrations (see git log for history).
-- NOTE: The `electric_user` role is granted access below but is NOT created here.
-- The role is created via the Supabase Dashboard on remote. For local dev, run:
--   CREATE ROLE electric_user;
-- before `supabase db reset`, or comment out the GRANTs in this file.

-- Defer function body validation so forward references (functions referencing
-- tables created later in this file) don't fail at load time.
SET check_function_bodies = false;


CREATE OR REPLACE FUNCTION "public"."assign_budget_to_transaction"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  -- Existing: auto-assign budget for expense transactions with a category
  IF NEW.type = 'expense' AND NEW.budget_id IS NULL AND NEW.category_id IS NOT NULL THEN
    SELECT b.id INTO NEW.budget_id
    FROM budgets b
    JOIN allocations a ON a.budget_id = b.id
    WHERE a.category_id = NEW.category_id
      AND b.user_id = NEW.user_id
      AND b.is_active = true
      AND NEW.transaction_date >= b.start_date
      AND (b.end_date IS NULL OR NEW.transaction_date <= b.end_date)
    ORDER BY b.created_at DESC
    LIMIT 1;
  END IF;

  -- New: auto-assign budget for transactions with a goal_id
  IF NEW.budget_id IS NULL AND NEW.goal_id IS NOT NULL THEN
    SELECT b.id INTO NEW.budget_id
    FROM budgets b
    JOIN allocations a ON a.budget_id = b.id
    WHERE a.goal_id = NEW.goal_id
      AND b.user_id = NEW.user_id
      AND b.is_active = true
      AND NEW.transaction_date >= b.start_date
      AND (b.end_date IS NULL OR NEW.transaction_date <= b.end_date)
    ORDER BY b.created_at DESC
    LIMIT 1;
  END IF;

  RETURN NEW;
END;
$$;




CREATE OR REPLACE FUNCTION "public"."calculate_next_occurrence"("p_frequency" character varying, "p_current_date" "date", "p_billing_day" integer DEFAULT NULL::integer) RETURNS "date"
    LANGUAGE "plpgsql" IMMUTABLE
    AS $$
DECLARE
  v_next_date date;
BEGIN
  CASE p_frequency
    WHEN 'weekly' THEN
      v_next_date := p_current_date + INTERVAL '7 days';
    WHEN 'biweekly' THEN
      v_next_date := p_current_date + INTERVAL '14 days';
    WHEN 'monthly' THEN
      v_next_date := p_current_date + INTERVAL '1 month';
      -- Adjust to billing day if specified
      IF p_billing_day IS NOT NULL THEN
        v_next_date := make_date(
          EXTRACT(YEAR FROM v_next_date)::int,
          EXTRACT(MONTH FROM v_next_date)::int,
          LEAST(p_billing_day, EXTRACT(DAY FROM (date_trunc('month', v_next_date) + INTERVAL '1 month - 1 day'))::int)
        );
      END IF;
    WHEN 'yearly' THEN
      v_next_date := p_current_date + INTERVAL '1 year';
    ELSE
      v_next_date := p_current_date + INTERVAL '1 month';
  END CASE;
  
  RETURN v_next_date;
END;
$$;




CREATE OR REPLACE FUNCTION "public"."check_goal_achievement"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
DECLARE
  total numeric;
  goal_record RECORD;
  check_goal_id uuid;
BEGIN
  -- Determine which goal_id to check
  IF TG_OP = 'DELETE' THEN
    check_goal_id := OLD.goal_id;
  ELSE
    check_goal_id := NEW.goal_id;
  END IF;

  -- Also check old goal_id on UPDATE if it changed
  IF TG_OP = 'UPDATE' AND OLD.goal_id IS DISTINCT FROM NEW.goal_id AND OLD.goal_id IS NOT NULL THEN
    SELECT target_amount, is_achieved INTO goal_record FROM public.goals WHERE id = OLD.goal_id;
    IF goal_record.is_achieved THEN
      SELECT COALESCE(SUM(amount), 0) INTO total FROM public.transactions WHERE goal_id = OLD.goal_id;
      IF total < goal_record.target_amount THEN
        UPDATE public.goals SET is_achieved = false, achieved_date = NULL, updated_at = now() WHERE id = OLD.goal_id;
      END IF;
    END IF;
  END IF;

  IF check_goal_id IS NULL THEN
    RETURN COALESCE(NEW, OLD);
  END IF;

  SELECT target_amount, is_achieved INTO goal_record FROM public.goals WHERE id = check_goal_id;

  SELECT COALESCE(SUM(amount), 0) INTO total FROM public.transactions WHERE goal_id = check_goal_id;

  IF total >= goal_record.target_amount AND NOT goal_record.is_achieved THEN
    UPDATE public.goals SET is_achieved = true, achieved_date = CURRENT_DATE, updated_at = now() WHERE id = check_goal_id;
  ELSIF total < goal_record.target_amount AND goal_record.is_achieved THEN
    UPDATE public.goals SET is_achieved = false, achieved_date = NULL, updated_at = now() WHERE id = check_goal_id;
  END IF;

  RETURN COALESCE(NEW, OLD);
END;
$$;




CREATE OR REPLACE FUNCTION "public"."delete_user_data"("p_clerk_user_id" "text") RETURNS "void"
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO 'public', 'pg_temp'
    AS $$
begin
    -- FK-respecting deletion order. DELETE on a non-existent user is a no-op.
    delete from transactions where user_id = p_clerk_user_id;
    delete from debt_payments where user_id = p_clerk_user_id;
    -- allocations has no user_id; scope via budget_id.
    delete from allocations
        where budget_id in (
            select id from budgets where user_id = p_clerk_user_id
        );
    delete from budget_archive_reports where user_id = p_clerk_user_id;
    delete from recurring_transactions where user_id = p_clerk_user_id;
    delete from debts where user_id = p_clerk_user_id;
    delete from goals where user_id = p_clerk_user_id;
    delete from budgets where user_id = p_clerk_user_id;
    delete from accounts where user_id = p_clerk_user_id;
    delete from user_settings where user_id = p_clerk_user_id;
    delete from profiles where clerk_user_id = p_clerk_user_id;

    update public.account_deletion_requests
        set status = 'completed',
            completed_at = now()
        where user_id = p_clerk_user_id
        and status in ('clerk_called', 'scheduled', 'processing');

    insert into public.account_deletion_audit (user_id_hash, event, metadata)
        values (
            extensions.digest(p_clerk_user_id, 'sha256'),
            'user_data_deleted',
            jsonb_build_object('called_at', now())
        );
end;
$$;




CREATE OR REPLACE FUNCTION "public"."drop_inactive_electric_replication_slots"() RETURNS integer
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  dropped INTEGER := 0;
  slot RECORD;
BEGIN
  FOR slot IN
    SELECT slot_name
    FROM pg_replication_slots
    WHERE NOT active
      AND slot_name LIKE 'electric_%'
  LOOP
    PERFORM pg_drop_replication_slot(slot.slot_name);
    dropped := dropped + 1;
  END LOOP;

  RETURN dropped;
END;
$$;




CREATE OR REPLACE FUNCTION "public"."get_budgets_overview"() RETURNS TABLE("budget_id" "uuid", "budget_name" "text", "budget_amount" numeric, "period" "text", "start_date" "date", "end_date" "date", "is_active" boolean, "total_spent" numeric)
    LANGUAGE "sql" STABLE SECURITY DEFINER
    AS $$
  SELECT
    b.id AS budget_id,
    b.name AS budget_name,
    b.amount AS budget_amount,
    b.period,
    b.start_date,
    b.end_date,
    b.is_active,
    COALESCE(SUM(t.amount) FILTER (
      WHERE t.type = 'expense'
    ), 0) AS total_spent
  FROM budgets b
  LEFT JOIN transactions t ON t.budget_id = b.id
  WHERE b.user_id = (auth.jwt()->>'sub')
  GROUP BY b.id
  ORDER BY b.created_at DESC;
$$;




CREATE OR REPLACE FUNCTION "public"."get_budgets_with_progress"() RETURNS TABLE("budget_id" "uuid", "budget_name" "text", "budget_amount" numeric, "period" "text", "start_date" "date", "end_date" "date", "is_active" boolean, "allocation_id" "uuid", "category_id" "uuid", "goal_id" "uuid", "amount" numeric, "alert_enabled" boolean, "alert_threshold" numeric, "category_name" "text", "category_type" "text", "category_color" "text", "category_icon" "text", "goal_name" "text", "progress" numeric)
    LANGUAGE "sql" STABLE SECURITY DEFINER
    SET "search_path" TO ''
    AS $$
  SELECT
    b.id AS budget_id,
    b.name AS budget_name,
    b.amount AS budget_amount,
    b.period,
    b.start_date,
    b.end_date,
    b.is_active,
    a.id AS allocation_id,
    a.category_id,
    a.goal_id,
    a.amount,
    a.alert_enabled,
    a.alert_threshold,
    c.name AS category_name,
    c.category_type,
    c.color AS category_color,
    c.icon AS category_icon,
    g.name AS goal_name,
    COALESCE(SUM(t.amount) FILTER (
      WHERE t.budget_id = b.id
        AND (
          (a.category_id IS NOT NULL AND t.type = 'expense' AND t.category_id = a.category_id)
          OR (a.goal_id IS NOT NULL AND t.goal_id = a.goal_id)
        )
    ), 0) AS progress
  FROM public.budgets b
  JOIN public.allocations a ON a.budget_id = b.id
  LEFT JOIN public.categories c ON c.id = a.category_id
  LEFT JOIN public.goals g ON g.id = a.goal_id
  LEFT JOIN public.transactions t ON t.budget_id = b.id
    AND (
      (a.category_id IS NOT NULL AND t.category_id = a.category_id)
      OR (a.goal_id IS NOT NULL AND t.goal_id = a.goal_id)
    )
  WHERE b.user_id = (auth.jwt()->>'sub')
  GROUP BY b.id, a.id, c.id, g.id
  ORDER BY b.created_at DESC, COALESCE(c.name, g.name);
$$;




CREATE OR REPLACE FUNCTION "public"."get_goals_with_progress"() RETURNS TABLE("id" "uuid", "name" "text", "target_amount" numeric, "current_amount" numeric, "target_date" "date", "category" "text", "notes" "text", "is_achieved" boolean, "achieved_date" "date", "created_at" timestamp with time zone, "budget_contributions" numeric, "direct_contributions" numeric)
    LANGUAGE "sql" STABLE SECURITY DEFINER
    SET "search_path" TO ''
    AS $$
  SELECT
    g.id,
    g.name::text,
    g.target_amount,
    COALESCE(SUM(t.amount), 0) AS current_amount,
    g.target_date,
    g.category::text,
    g.notes,
    g.is_achieved,
    g.achieved_date,
    g.created_at,
    COALESCE(SUM(t.amount) FILTER (WHERE t.budget_id IS NOT NULL), 0) AS budget_contributions,
    COALESCE(SUM(t.amount) FILTER (WHERE t.budget_id IS NULL), 0) AS direct_contributions
  FROM public.goals g
  LEFT JOIN public.transactions t ON t.goal_id = g.id
  WHERE g.user_id = (auth.jwt()->>'sub')
  GROUP BY g.id
  ORDER BY g.is_achieved ASC, g.created_at DESC;
$$;




CREATE OR REPLACE FUNCTION "public"."get_transactions_with_categories"("p_budget_id" "uuid") RETURNS TABLE("id" "uuid", "amount" numeric, "category_id" "uuid", "name" "text", "icon" "text", "color" "text", "description" "text", "transaction_date" "date", "category_type" "text", "is_recurring" boolean)
    LANGUAGE "sql" STABLE SECURITY DEFINER
    AS $$
  SELECT
    t.id,
    t.amount,
    t.category_id,
    COALESCE(c.name, 'Uncategorized') AS name,
    COALESCE(c.icon, '📦') AS icon,
    COALESCE(c.color, '#9E9E9E') AS color,
    t.description,
    t.transaction_date,
    COALESCE(c.category_type, 'general') AS category_type,
    t.is_recurring
  FROM transactions t
  LEFT JOIN categories c ON t.category_id = c.id
  WHERE t.user_id = (auth.jwt() ->> 'sub')::text
    AND t.budget_id = p_budget_id;
$$;




CREATE OR REPLACE FUNCTION "public"."process_recurring_transactions"() RETURNS TABLE("recurring_id" "uuid", "transaction_id" "uuid", "amount" numeric, "processed_date" "date")
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  r RECORD;
  new_transaction_id uuid;
BEGIN
  -- Find all due recurring transactions
  FOR r IN 
    SELECT rt.*
    FROM recurring_transactions rt
    WHERE rt.is_active = true
      AND rt.is_paused = false
      AND rt.next_occurrence <= CURRENT_DATE
      AND (rt.end_date IS NULL OR rt.end_date >= CURRENT_DATE)
  LOOP
    -- Create the transaction
    INSERT INTO transactions (
      user_id,
      account_id,
      category_id,
      type,
      amount,
      description,
      merchant,
      transaction_date,
      recurring_id,
      is_recurring,
      note
    ) VALUES (
      r.user_id,
      r.account_id,
      r.category_id,
      r.type,
      r.amount,
      r.name,
      r.name,
      r.next_occurrence,
      r.id,
      true,
      r.note
    )
    RETURNING id INTO new_transaction_id;
    
    -- Update the recurring transaction
    UPDATE recurring_transactions
    SET 
      last_processed = r.next_occurrence,
      next_occurrence = calculate_next_occurrence(r.frequency, r.next_occurrence, r.billing_day),
      times_processed = times_processed + 1,
      updated_at = now()
    WHERE id = r.id;
    
    -- Return the result
    recurring_id := r.id;
    transaction_id := new_transaction_id;
    amount := r.amount;
    processed_date := r.next_occurrence;
    RETURN NEXT;
  END LOOP;
END;
$$;




CREATE OR REPLACE FUNCTION "public"."record_debt_payment"("p_debt_id" "uuid", "p_amount_paid" numeric, "p_principal_paid" numeric, "p_interest_paid" numeric, "p_payment_date" "date", "p_notes" "text" DEFAULT NULL::"text") RETURNS "void"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
  INSERT INTO debt_payments (debt_id, user_id, amount_paid, principal_paid, interest_paid, payment_date, notes)
  VALUES (p_debt_id, (auth.jwt()->>'sub'), p_amount_paid, p_principal_paid, p_interest_paid, p_payment_date, p_notes);

  UPDATE debts SET current_balance = GREATEST(current_balance - p_principal_paid, 0), updated_at = now()
  WHERE id = p_debt_id AND user_id = (auth.jwt()->>'sub');
END;
$$;




CREATE OR REPLACE FUNCTION "public"."rls_auto_enable"() RETURNS "event_trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO 'pg_catalog'
    AS $$
DECLARE
  cmd record;
BEGIN
  FOR cmd IN
    SELECT *
    FROM pg_event_trigger_ddl_commands()
    WHERE command_tag IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
      AND object_type IN ('table','partitioned table')
  LOOP
     IF cmd.schema_name IS NOT NULL AND cmd.schema_name IN ('public') AND cmd.schema_name NOT IN ('pg_catalog','information_schema') AND cmd.schema_name NOT LIKE 'pg_toast%' AND cmd.schema_name NOT LIKE 'pg_temp%' THEN
      BEGIN
        EXECUTE format('alter table if exists %s enable row level security', cmd.object_identity);
        RAISE LOG 'rls_auto_enable: enabled RLS on %', cmd.object_identity;
      EXCEPTION
        WHEN OTHERS THEN
          RAISE LOG 'rls_auto_enable: failed to enable RLS on %', cmd.object_identity;
      END;
     ELSE
        RAISE LOG 'rls_auto_enable: skip % (either system schema or not in enforced list: %.)', cmd.object_identity, cmd.schema_name;
     END IF;
  END LOOP;
END;
$$;




CREATE OR REPLACE FUNCTION "public"."terminate_idle_electric_connections"("idle_threshold" interval DEFAULT '00:05:00'::interval) RETURNS integer
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  terminated INTEGER;
BEGIN
  WITH terminated_pids AS (
    SELECT pg_terminate_backend(pid)
    FROM pg_stat_activity
    WHERE usename = 'electric_user'
      AND state = 'idle'
      AND state_change < NOW() - idle_threshold
      AND pid <> pg_backend_pid()
  )
  SELECT COUNT(*) INTO terminated FROM terminated_pids;

  RETURN terminated;
END;
$$;




CREATE OR REPLACE FUNCTION "public"."update_debt_updated_at"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;




CREATE OR REPLACE FUNCTION "public"."update_updated_at"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;




CREATE OR REPLACE FUNCTION "public"."update_updated_at_column"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;



SET default_tablespace = '';

SET default_table_access_method = "heap";






CREATE TABLE IF NOT EXISTS "public"."account_deletion_audit" (
    "id" bigint NOT NULL,
    "user_id_hash" "bytea" NOT NULL,
    "event" "text" NOT NULL,
    "occurred_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "metadata" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    CONSTRAINT "account_deletion_audit_event_check" CHECK (("event" = ANY (ARRAY['request_created'::"text", 'request_confirmed'::"text", 'request_cancelled'::"text", 'clerk_delete_called'::"text", 'user_data_deleted'::"text", 'request_failed'::"text"])))
);




CREATE SEQUENCE IF NOT EXISTS "public"."account_deletion_audit_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;




ALTER SEQUENCE "public"."account_deletion_audit_id_seq" OWNED BY "public"."account_deletion_audit"."id";



CREATE TABLE IF NOT EXISTS "public"."account_deletion_requests" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "text" DEFAULT ("auth"."jwt"() ->> 'sub'::"text") NOT NULL,
    "email" "text" NOT NULL,
    "status" "text" NOT NULL,
    "confirmation_token_hash" "text",
    "confirmation_token_expires_at" timestamp with time zone,
    "scheduled_deletion_at" timestamp with time zone,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "confirmed_at" timestamp with time zone,
    "cancelled_at" timestamp with time zone,
    "clerk_called_at" timestamp with time zone,
    "completed_at" timestamp with time zone,
    "failed_at" timestamp with time zone,
    "failure_reason" "text",
    "last_error_at" timestamp with time zone,
    "retry_count" integer DEFAULT 0 NOT NULL,
    CONSTRAINT "account_deletion_requests_status_check" CHECK (("status" = ANY (ARRAY['pending_confirmation'::"text", 'scheduled'::"text", 'cancelled'::"text", 'processing'::"text", 'clerk_called'::"text", 'completed'::"text", 'failed'::"text"])))
);




CREATE TABLE IF NOT EXISTS "public"."accounts" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "text" NOT NULL,
    "name" character varying(100) NOT NULL,
    "type" character varying(30) NOT NULL,
    "balance" numeric(12,2) DEFAULT 0,
    "initial_balance" numeric(12,2) DEFAULT 0,
    "currency" character varying(3) DEFAULT 'USD'::character varying,
    "color" character varying(7),
    "icon" character varying(50),
    "is_active" boolean DEFAULT true,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"()
);




CREATE TABLE IF NOT EXISTS "public"."allocations" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "budget_id" "uuid" NOT NULL,
    "category_id" "uuid",
    "amount" numeric NOT NULL,
    "alert_enabled" boolean DEFAULT true,
    "alert_threshold" integer DEFAULT 80,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    "goal_id" "uuid",
    CONSTRAINT "allocation_category_or_goal_check" CHECK (((("category_id" IS NOT NULL) AND ("goal_id" IS NULL)) OR (("category_id" IS NULL) AND ("goal_id" IS NOT NULL)))),
    CONSTRAINT "budget_items_amount_check" CHECK (("amount" > (0)::numeric))
);




CREATE TABLE IF NOT EXISTS "public"."budget_archive_reports" (
    "budget_id" "uuid" NOT NULL,
    "user_id" "text" NOT NULL,
    "summary" "jsonb" NOT NULL,
    "ai_report" "jsonb" NOT NULL,
    "generated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);




CREATE TABLE IF NOT EXISTS "public"."budgets" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "text" DEFAULT ("auth"."jwt"() ->> 'sub'::"text") NOT NULL,
    "name" character varying(100) NOT NULL,
    "period" character varying(20) DEFAULT 'monthly'::character varying NOT NULL,
    "start_date" "date" NOT NULL,
    "end_date" "date",
    "is_active" boolean DEFAULT true,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    "amount" numeric DEFAULT 0 NOT NULL,
    "archived_at" timestamp with time zone
);




CREATE TABLE IF NOT EXISTS "public"."categories" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid",
    "name" character varying(100) NOT NULL,
    "category_type" character varying(20) NOT NULL,
    "icon" character varying(50),
    "color" character varying(7),
    "parent_id" "uuid",
    "is_system" boolean DEFAULT false,
    "display_order" integer DEFAULT 0,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "is_editable" boolean DEFAULT true,
    "is_visible" boolean DEFAULT true,
    "clerk_user_id" "text",
    CONSTRAINT "categories_system_or_user_check" CHECK (((("is_system" = true) AND ("user_id" IS NULL)) OR (("is_system" = false) AND ("user_id" IS NOT NULL)) OR (("is_system" IS NULL) AND ("user_id" IS NOT NULL))))
);




COMMENT ON COLUMN "public"."categories"."is_system" IS 'System categories are defaults, cannot be deleted';



COMMENT ON COLUMN "public"."categories"."is_editable" IS 'Whether users can customize this category (name, icon, color)';



COMMENT ON COLUMN "public"."categories"."is_visible" IS 'Whether this category appears in the user interface';



CREATE TABLE IF NOT EXISTS "public"."debt_payments" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "debt_id" "uuid" NOT NULL,
    "user_id" "text" DEFAULT ("auth"."jwt"() ->> 'sub'::"text") NOT NULL,
    "amount_paid" numeric(12,2) NOT NULL,
    "principal_paid" numeric(12,2) NOT NULL,
    "interest_paid" numeric(12,2) NOT NULL,
    "payment_date" "date" NOT NULL,
    "notes" "text",
    "created_at" timestamp with time zone DEFAULT "now"()
);




CREATE TABLE IF NOT EXISTS "public"."debts" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "text" DEFAULT ("auth"."jwt"() ->> 'sub'::"text") NOT NULL,
    "name" "text" NOT NULL,
    "type" "text" NOT NULL,
    "principal_amount" numeric(12,2) NOT NULL,
    "interest_rate" numeric(5,3) NOT NULL,
    "current_balance" numeric(12,2) NOT NULL,
    "minimum_payment" numeric(12,2) NOT NULL,
    "start_date" "date" NOT NULL,
    "is_active" boolean DEFAULT true,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    CONSTRAINT "debts_current_balance_check" CHECK (("current_balance" >= (0)::numeric)),
    CONSTRAINT "debts_type_check" CHECK (("type" = ANY (ARRAY['credit_card'::"text", 'personal_loan'::"text", 'auto_loan'::"text", 'student_loan'::"text", 'mortgage'::"text"])))
);

ALTER TABLE ONLY "public"."debts" REPLICA IDENTITY FULL;




CREATE TABLE IF NOT EXISTS "public"."goals" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "text" DEFAULT ("auth"."jwt"() ->> 'sub'::"text") NOT NULL,
    "name" character varying(100) NOT NULL,
    "target_amount" numeric(12,2) NOT NULL,
    "current_amount" numeric(12,2) DEFAULT 0,
    "target_date" "date",
    "category" character varying(50),
    "notes" "text",
    "is_achieved" boolean DEFAULT false,
    "achieved_date" "date",
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    CONSTRAINT "goals_target_amount_check" CHECK (("target_amount" > (0)::numeric))
);




CREATE TABLE IF NOT EXISTS "public"."profiles" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "email" "text" NOT NULL,
    "full_name" "text",
    "avatar_url" "text",
    "currency" character varying(3) DEFAULT 'USD'::character varying,
    "timezone" character varying(50) DEFAULT 'UTC'::character varying,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    "clerk_user_id" "text"
);




CREATE TABLE IF NOT EXISTS "public"."recurring_transactions" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "text" NOT NULL,
    "account_id" "uuid",
    "category_id" "uuid",
    "name" character varying(255) NOT NULL,
    "type" character varying(20) NOT NULL,
    "amount" numeric(12,2) NOT NULL,
    "currency" character varying(3) DEFAULT 'USD'::character varying,
    "frequency" character varying(20) NOT NULL,
    "billing_day" integer,
    "start_date" "date" NOT NULL,
    "end_date" "date",
    "next_occurrence" "date" NOT NULL,
    "last_processed" "date",
    "times_processed" integer DEFAULT 0,
    "service_template_id" "uuid",
    "notify_before_days" integer DEFAULT 1,
    "notify_enabled" boolean DEFAULT true,
    "is_active" boolean DEFAULT true,
    "is_paused" boolean DEFAULT false,
    "note" "text",
    "tags" "text"[],
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"()
);




COMMENT ON TABLE "public"."recurring_transactions" IS 'Manages recurring income and expense rules';



COMMENT ON COLUMN "public"."recurring_transactions"."billing_day" IS 'Day of month (1-31) for monthly, day of week (1-7) for weekly';



COMMENT ON COLUMN "public"."recurring_transactions"."next_occurrence" IS 'Next date this transaction will be created';



COMMENT ON COLUMN "public"."recurring_transactions"."service_template_id" IS 'Reference to service template if created from quick-add';



CREATE TABLE IF NOT EXISTS "public"."service_templates" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "name" character varying(100) NOT NULL,
    "icon" character varying(10),
    "logo_url" "text",
    "category_id" "uuid",
    "service_type" character varying(50) NOT NULL,
    "default_amount" numeric(12,2),
    "country_code" character varying(2) NOT NULL,
    "currency" character varying(3) DEFAULT 'USD'::character varying NOT NULL,
    "localized_amount" numeric(12,2) NOT NULL,
    "billing_cycle" character varying(20) DEFAULT 'monthly'::character varying,
    "website_url" "text",
    "price_selector" "text",
    "last_price_update" timestamp with time zone,
    "is_active" boolean DEFAULT true,
    "display_order" integer DEFAULT 0,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"()
);




COMMENT ON TABLE "public"."service_templates" IS 'Pre-filled service/subscription costs by country for quick transaction entry';



COMMENT ON COLUMN "public"."service_templates"."service_type" IS 'Category: streaming, music, cloud, gaming, productivity, utilities';



COMMENT ON COLUMN "public"."service_templates"."price_selector" IS 'CSS selector for future automated price scraping';



CREATE TABLE IF NOT EXISTS "public"."transactions" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "text" DEFAULT ("auth"."jwt"() ->> 'sub'::"text") NOT NULL,
    "account_id" "uuid",
    "category_id" "uuid",
    "type" character varying(20) NOT NULL,
    "amount" numeric(12,2) NOT NULL,
    "description" "text",
    "merchant" character varying(255),
    "transaction_date" "date" DEFAULT CURRENT_DATE NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    "recurring_id" "uuid",
    "note" "text",
    "is_recurring" boolean DEFAULT false,
    "tags" "text"[],
    "budget_id" "uuid",
    "goal_id" "uuid",
    CONSTRAINT "transactions_amount_check" CHECK (("amount" > (0)::numeric))
);




COMMENT ON COLUMN "public"."transactions"."recurring_id" IS 'Reference to the recurring rule that created this transaction';



COMMENT ON COLUMN "public"."transactions"."is_recurring" IS 'Whether this transaction was auto-created from a recurring rule';



CREATE TABLE IF NOT EXISTS "public"."user_settings" (
    "user_id" "text" DEFAULT ("auth"."jwt"() ->> 'sub'::"text") NOT NULL,
    "dark_mode" boolean DEFAULT false,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"()
);




CREATE OR REPLACE VIEW "public"."v_upcoming_recurring" WITH ("security_invoker"='on') AS
 SELECT;




CREATE OR REPLACE VIEW "public"."v_user_categories" WITH ("security_invoker"='on') AS
 SELECT "id",
    "user_id",
    "name",
    "category_type" AS "type",
    "icon",
    "color",
    "parent_id",
    "is_system",
    "display_order",
    "created_at",
    "is_editable",
    "is_visible",
        CASE
            WHEN "is_system" THEN 'system'::"text"
            ELSE 'custom'::"text"
        END AS "category_source"
   FROM "public"."categories" "c"
  WHERE ("is_visible" = true)
  ORDER BY "is_system" DESC, "display_order";




CREATE TABLE IF NOT EXISTS "public"."webhook_events" (
    "svix_id" "text" NOT NULL,
    "received_at" timestamp with time zone DEFAULT "now"() NOT NULL
);




ALTER TABLE ONLY "public"."account_deletion_audit" ALTER COLUMN "id" SET DEFAULT "nextval"('"public"."account_deletion_audit_id_seq"'::"regclass");



ALTER TABLE ONLY "public"."account_deletion_audit"
    ADD CONSTRAINT "account_deletion_audit_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."account_deletion_requests"
    ADD CONSTRAINT "account_deletion_requests_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."accounts"
    ADD CONSTRAINT "accounts_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."accounts"
    ADD CONSTRAINT "accounts_user_id_name_key" UNIQUE ("user_id", "name");



ALTER TABLE ONLY "public"."budget_archive_reports"
    ADD CONSTRAINT "budget_archive_reports_pkey" PRIMARY KEY ("budget_id");



ALTER TABLE ONLY "public"."allocations"
    ADD CONSTRAINT "budget_items_budget_id_category_id_key" UNIQUE ("budget_id", "category_id");



ALTER TABLE ONLY "public"."allocations"
    ADD CONSTRAINT "budget_items_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."budgets"
    ADD CONSTRAINT "budgets_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."categories"
    ADD CONSTRAINT "categories_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."categories"
    ADD CONSTRAINT "categories_user_id_name_parent_id_key" UNIQUE ("user_id", "name", "parent_id");



ALTER TABLE ONLY "public"."debt_payments"
    ADD CONSTRAINT "debt_payments_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."debts"
    ADD CONSTRAINT "debts_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."goals"
    ADD CONSTRAINT "goals_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."profiles"
    ADD CONSTRAINT "profiles_clerk_user_id_key" UNIQUE ("clerk_user_id");



ALTER TABLE ONLY "public"."profiles"
    ADD CONSTRAINT "profiles_email_key" UNIQUE ("email");



ALTER TABLE ONLY "public"."profiles"
    ADD CONSTRAINT "profiles_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."recurring_transactions"
    ADD CONSTRAINT "recurring_transactions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."service_templates"
    ADD CONSTRAINT "service_templates_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."transactions"
    ADD CONSTRAINT "transactions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."user_settings"
    ADD CONSTRAINT "user_settings_pkey" PRIMARY KEY ("user_id");



ALTER TABLE ONLY "public"."webhook_events"
    ADD CONSTRAINT "webhook_events_pkey" PRIMARY KEY ("svix_id");



CREATE UNIQUE INDEX "budgets_user_id_name_idx" ON "public"."budgets" USING "btree" ("user_id", "name");



CREATE INDEX "idx_account_deletion_audit_user_hash" ON "public"."account_deletion_audit" USING "btree" ("user_id_hash", "occurred_at" DESC);



CREATE INDEX "idx_accounts_active" ON "public"."accounts" USING "btree" ("user_id", "is_active");



CREATE INDEX "idx_accounts_user" ON "public"."accounts" USING "btree" ("user_id");



CREATE INDEX "idx_allocations_goal_id" ON "public"."allocations" USING "btree" ("goal_id");



CREATE INDEX "idx_budgets_archived_at_null" ON "public"."budgets" USING "btree" ("end_date") WHERE ("archived_at" IS NULL);



CREATE INDEX "idx_categories_type" ON "public"."categories" USING "btree" ("user_id", "category_type");



CREATE INDEX "idx_categories_user" ON "public"."categories" USING "btree" ("user_id");



CREATE INDEX "idx_debt_payments_debt" ON "public"."debt_payments" USING "btree" ("debt_id");



CREATE INDEX "idx_debt_payments_user" ON "public"."debt_payments" USING "btree" ("user_id");



CREATE INDEX "idx_debts_active" ON "public"."debts" USING "btree" ("user_id", "is_active");



CREATE INDEX "idx_debts_user" ON "public"."debts" USING "btree" ("user_id");



CREATE UNIQUE INDEX "idx_deletion_requests_active_per_user" ON "public"."account_deletion_requests" USING "btree" ("user_id") WHERE ("status" = ANY (ARRAY['pending_confirmation'::"text", 'scheduled'::"text", 'processing'::"text", 'clerk_called'::"text"]));



CREATE INDEX "idx_deletion_requests_due" ON "public"."account_deletion_requests" USING "btree" ("scheduled_deletion_at") WHERE ("status" = 'scheduled'::"text");



CREATE INDEX "idx_deletion_requests_user_status" ON "public"."account_deletion_requests" USING "btree" ("user_id", "status");



CREATE INDEX "idx_goals_active" ON "public"."goals" USING "btree" ("user_id", "is_achieved");



CREATE INDEX "idx_goals_user" ON "public"."goals" USING "btree" ("user_id");



CREATE INDEX "idx_recurring_next_occurrence" ON "public"."recurring_transactions" USING "btree" ("next_occurrence") WHERE ("is_active" = true);



CREATE INDEX "idx_recurring_type" ON "public"."recurring_transactions" USING "btree" ("user_id", "type");



CREATE INDEX "idx_recurring_user" ON "public"."recurring_transactions" USING "btree" ("user_id");



CREATE INDEX "idx_service_templates_active" ON "public"."service_templates" USING "btree" ("is_active") WHERE ("is_active" = true);



CREATE INDEX "idx_service_templates_country" ON "public"."service_templates" USING "btree" ("country_code", "service_type");



CREATE INDEX "idx_transactions_account" ON "public"."transactions" USING "btree" ("account_id");



CREATE INDEX "idx_transactions_budget_id" ON "public"."transactions" USING "btree" ("budget_id");



CREATE INDEX "idx_transactions_category" ON "public"."transactions" USING "btree" ("category_id");



CREATE INDEX "idx_transactions_goal_id" ON "public"."transactions" USING "btree" ("goal_id");



CREATE INDEX "idx_transactions_recurring" ON "public"."transactions" USING "btree" ("recurring_id") WHERE ("recurring_id" IS NOT NULL);



CREATE INDEX "idx_transactions_user_date" ON "public"."transactions" USING "btree" ("user_id", "transaction_date" DESC);



CREATE OR REPLACE TRIGGER "debts_updated_at" BEFORE UPDATE ON "public"."debts" FOR EACH ROW EXECUTE FUNCTION "public"."update_debt_updated_at"();



CREATE OR REPLACE TRIGGER "trg_assign_budget_to_transaction" BEFORE INSERT OR UPDATE ON "public"."transactions" FOR EACH ROW EXECUTE FUNCTION "public"."assign_budget_to_transaction"();



CREATE OR REPLACE TRIGGER "trg_check_goal_achievement" AFTER INSERT OR DELETE OR UPDATE OF "amount", "goal_id" ON "public"."transactions" FOR EACH ROW EXECUTE FUNCTION "public"."check_goal_achievement"();



CREATE OR REPLACE TRIGGER "update_recurring_transactions_updated_at" BEFORE UPDATE ON "public"."recurring_transactions" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_service_templates_updated_at" BEFORE UPDATE ON "public"."service_templates" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "user_settings_updated_at" BEFORE UPDATE ON "public"."user_settings" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at"();



ALTER TABLE ONLY "public"."allocations"
    ADD CONSTRAINT "allocations_goal_id_fkey" FOREIGN KEY ("goal_id") REFERENCES "public"."goals"("id");



ALTER TABLE ONLY "public"."budget_archive_reports"
    ADD CONSTRAINT "budget_archive_reports_budget_id_fkey" FOREIGN KEY ("budget_id") REFERENCES "public"."budgets"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."allocations"
    ADD CONSTRAINT "budget_items_budget_id_fkey" FOREIGN KEY ("budget_id") REFERENCES "public"."budgets"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."allocations"
    ADD CONSTRAINT "budget_items_category_id_fkey" FOREIGN KEY ("category_id") REFERENCES "public"."categories"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."categories"
    ADD CONSTRAINT "categories_parent_id_fkey" FOREIGN KEY ("parent_id") REFERENCES "public"."categories"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."categories"
    ADD CONSTRAINT "categories_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."debt_payments"
    ADD CONSTRAINT "debt_payments_debt_id_fkey" FOREIGN KEY ("debt_id") REFERENCES "public"."debts"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."recurring_transactions"
    ADD CONSTRAINT "recurring_transactions_account_id_fkey" FOREIGN KEY ("account_id") REFERENCES "public"."accounts"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."recurring_transactions"
    ADD CONSTRAINT "recurring_transactions_category_id_fkey" FOREIGN KEY ("category_id") REFERENCES "public"."categories"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."recurring_transactions"
    ADD CONSTRAINT "recurring_transactions_service_template_id_fkey" FOREIGN KEY ("service_template_id") REFERENCES "public"."service_templates"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."service_templates"
    ADD CONSTRAINT "service_templates_category_id_fkey" FOREIGN KEY ("category_id") REFERENCES "public"."categories"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."transactions"
    ADD CONSTRAINT "transactions_account_id_fkey" FOREIGN KEY ("account_id") REFERENCES "public"."accounts"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."transactions"
    ADD CONSTRAINT "transactions_budget_id_fkey" FOREIGN KEY ("budget_id") REFERENCES "public"."budgets"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."transactions"
    ADD CONSTRAINT "transactions_category_id_fkey" FOREIGN KEY ("category_id") REFERENCES "public"."categories"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."transactions"
    ADD CONSTRAINT "transactions_goal_id_fkey" FOREIGN KEY ("goal_id") REFERENCES "public"."goals"("id");



ALTER TABLE ONLY "public"."transactions"
    ADD CONSTRAINT "transactions_recurring_id_fkey" FOREIGN KEY ("recurring_id") REFERENCES "public"."recurring_transactions"("id") ON DELETE SET NULL;



CREATE POLICY "Service templates are viewable by authenticated users" ON "public"."service_templates" FOR SELECT TO "authenticated" USING (("is_active" = true));



CREATE POLICY "Users can create categories" ON "public"."categories" FOR INSERT WITH CHECK (("clerk_user_id" = ("auth"."jwt"() ->> 'sub'::"text")));



CREATE POLICY "Users can create own recurring transactions" ON "public"."recurring_transactions" FOR INSERT TO "authenticated" WITH CHECK ((("auth"."jwt"() ->> 'sub'::"text") = "user_id"));



CREATE POLICY "Users can delete
  own budget items" ON "public"."allocations" FOR DELETE USING ((EXISTS ( SELECT 1
   FROM "public"."budgets"
  WHERE (("budgets"."id" = "allocations"."budget_id") AND ("budgets"."user_id" = ("auth"."jwt"() ->> 'sub'::"text"))))));



CREATE POLICY "Users can delete own categories" ON "public"."categories" FOR DELETE USING ((("clerk_user_id" = ("auth"."jwt"() ->> 'sub'::"text")) AND ("is_system" = false)));



CREATE POLICY "Users can delete own recurring transactions" ON "public"."recurring_transactions" FOR DELETE TO "authenticated" USING ((("auth"."jwt"() ->> 'sub'::"text") = "user_id"));



CREATE POLICY "Users can insert
  own budget items" ON "public"."allocations" FOR INSERT WITH CHECK ((EXISTS ( SELECT 1
   FROM "public"."budgets"
  WHERE (("budgets"."id" = "allocations"."budget_id") AND ("budgets"."user_id" = ("auth"."jwt"() ->> 'sub'::"text"))))));



CREATE POLICY "Users can insert their own profile" ON "public"."profiles" FOR INSERT WITH CHECK (("clerk_user_id" = ("auth"."jwt"() ->> 'sub'::"text")));



CREATE POLICY "Users can manage own settings" ON "public"."user_settings" USING ((("auth"."jwt"() ->> 'sub'::"text") = "user_id")) WITH CHECK ((("auth"."jwt"() ->> 'sub'::"text") = "user_id"));



CREATE POLICY "Users can read their own profile" ON "public"."profiles" FOR SELECT USING (("clerk_user_id" = ("auth"."jwt"() ->> 'sub'::"text")));



CREATE POLICY "Users can update
  own budget items" ON "public"."allocations" FOR UPDATE USING ((EXISTS ( SELECT 1
   FROM "public"."budgets"
  WHERE (("budgets"."id" = "allocations"."budget_id") AND ("budgets"."user_id" = ("auth"."jwt"() ->> 'sub'::"text"))))));



CREATE POLICY "Users can update own categories" ON "public"."categories" FOR UPDATE USING ((("clerk_user_id" = ("auth"."jwt"() ->> 'sub'::"text")) AND ("is_system" = false)));



CREATE POLICY "Users can update own recurring transactions" ON "public"."recurring_transactions" FOR UPDATE TO "authenticated" USING ((("auth"."jwt"() ->> 'sub'::"text") = "user_id")) WITH CHECK ((("auth"."jwt"() ->> 'sub'::"text") = "user_id"));



CREATE POLICY "Users can update their own profile" ON "public"."profiles" FOR UPDATE USING (("clerk_user_id" = ("auth"."jwt"() ->> 'sub'::"text"))) WITH CHECK (("clerk_user_id" = ("auth"."jwt"() ->> 'sub'::"text")));



CREATE POLICY "Users can view categories" ON "public"."categories" FOR SELECT USING ((("is_system" = true) OR ("user_id" IS NULL) OR ("clerk_user_id" = ("auth"."jwt"() ->> 'sub'::"text"))));



CREATE POLICY "Users can view own
  budget items" ON "public"."allocations" FOR SELECT USING ((EXISTS ( SELECT 1
   FROM "public"."budgets"
  WHERE (("budgets"."id" = "allocations"."budget_id") AND ("budgets"."user_id" = ("auth"."jwt"() ->> 'sub'::"text"))))));



CREATE POLICY "Users can view own recurring transactions" ON "public"."recurring_transactions" FOR SELECT TO "authenticated" USING ((("auth"."jwt"() ->> 'sub'::"text") = "user_id"));



CREATE POLICY "Users manage own accounts" ON "public"."accounts" TO "authenticated" USING ((("auth"."jwt"() ->> 'sub'::"text") = "user_id")) WITH CHECK ((("auth"."jwt"() ->> 'sub'::"text") = "user_id"));



CREATE POLICY "Users manage own budgets" ON "public"."budgets" TO "authenticated" USING ((("auth"."jwt"() ->> 'sub'::"text") = "user_id")) WITH CHECK ((("auth"."jwt"() ->> 'sub'::"text") = "user_id"));



CREATE POLICY "Users manage own debt payments" ON "public"."debt_payments" USING ((("auth"."jwt"() ->> 'sub'::"text") = "user_id")) WITH CHECK ((("auth"."jwt"() ->> 'sub'::"text") = "user_id"));



CREATE POLICY "Users manage own debts" ON "public"."debts" USING ((("auth"."jwt"() ->> 'sub'::"text") = "user_id")) WITH CHECK ((("auth"."jwt"() ->> 'sub'::"text") = "user_id"));



CREATE POLICY "Users manage own goals" ON "public"."goals" TO "authenticated" USING ((("auth"."jwt"() ->> 'sub'::"text") = "user_id")) WITH CHECK ((("auth"."jwt"() ->> 'sub'::"text") = "user_id"));



CREATE POLICY "Users manage own transactions" ON "public"."transactions" TO "authenticated" USING ((("auth"."jwt"() ->> 'sub'::"text") = "user_id")) WITH CHECK ((("auth"."jwt"() ->> 'sub'::"text") = "user_id"));





ALTER TABLE "public"."account_deletion_audit" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."account_deletion_requests" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."accounts" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."allocations" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."budget_archive_reports" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."budgets" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."categories" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."debt_payments" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."debts" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "delete_requests_cancel_own" ON "public"."account_deletion_requests" FOR UPDATE TO "authenticated" USING (("user_id" = ("auth"."jwt"() ->> 'sub'::"text"))) WITH CHECK ((("user_id" = ("auth"."jwt"() ->> 'sub'::"text")) AND ("status" = 'cancelled'::"text")));



CREATE POLICY "delete_requests_insert_own" ON "public"."account_deletion_requests" FOR INSERT TO "authenticated" WITH CHECK (("user_id" = ("auth"."jwt"() ->> 'sub'::"text")));



CREATE POLICY "deletion_requests_select_own" ON "public"."account_deletion_requests" FOR SELECT TO "authenticated" USING (("user_id" = ("auth"."jwt"() ->> 'sub'::"text")));



ALTER TABLE "public"."goals" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."profiles" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."recurring_transactions" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."service_templates" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."transactions" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."user_settings" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "users read own archive reports" ON "public"."budget_archive_reports" FOR SELECT USING (("user_id" = ("auth"."jwt"() ->> 'sub'::"text")));



ALTER TABLE "public"."webhook_events" ENABLE ROW LEVEL SECURITY;


GRANT USAGE ON SCHEMA "public" TO "postgres";
GRANT USAGE ON SCHEMA "public" TO "anon";
GRANT USAGE ON SCHEMA "public" TO "authenticated";
GRANT USAGE ON SCHEMA "public" TO "service_role";

-- Bootstrap electric_user role if missing (created via Dashboard on remote;
-- needs creating on local stacks before the GRANTs below).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'electric_user') THEN
    CREATE ROLE "electric_user";
  END IF;
END $$;

GRANT USAGE ON SCHEMA "public" TO "electric_user";



GRANT ALL ON FUNCTION "public"."assign_budget_to_transaction"() TO "anon";
GRANT ALL ON FUNCTION "public"."assign_budget_to_transaction"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."assign_budget_to_transaction"() TO "service_role";



GRANT ALL ON FUNCTION "public"."calculate_next_occurrence"("p_frequency" character varying, "p_current_date" "date", "p_billing_day" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."calculate_next_occurrence"("p_frequency" character varying, "p_current_date" "date", "p_billing_day" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."calculate_next_occurrence"("p_frequency" character varying, "p_current_date" "date", "p_billing_day" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."check_goal_achievement"() TO "anon";
GRANT ALL ON FUNCTION "public"."check_goal_achievement"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."check_goal_achievement"() TO "service_role";



REVOKE ALL ON FUNCTION "public"."delete_user_data"("p_clerk_user_id" "text") FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."delete_user_data"("p_clerk_user_id" "text") TO "service_role";



REVOKE ALL ON FUNCTION "public"."drop_inactive_electric_replication_slots"() FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."drop_inactive_electric_replication_slots"() TO "anon";
GRANT ALL ON FUNCTION "public"."drop_inactive_electric_replication_slots"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."drop_inactive_electric_replication_slots"() TO "service_role";



GRANT ALL ON FUNCTION "public"."get_budgets_overview"() TO "anon";
GRANT ALL ON FUNCTION "public"."get_budgets_overview"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_budgets_overview"() TO "service_role";



GRANT ALL ON FUNCTION "public"."get_budgets_with_progress"() TO "anon";
GRANT ALL ON FUNCTION "public"."get_budgets_with_progress"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_budgets_with_progress"() TO "service_role";



GRANT ALL ON FUNCTION "public"."get_goals_with_progress"() TO "anon";
GRANT ALL ON FUNCTION "public"."get_goals_with_progress"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_goals_with_progress"() TO "service_role";



GRANT ALL ON FUNCTION "public"."get_transactions_with_categories"("p_budget_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "public"."get_transactions_with_categories"("p_budget_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_transactions_with_categories"("p_budget_id" "uuid") TO "service_role";



GRANT ALL ON FUNCTION "public"."process_recurring_transactions"() TO "anon";
GRANT ALL ON FUNCTION "public"."process_recurring_transactions"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."process_recurring_transactions"() TO "service_role";



GRANT ALL ON FUNCTION "public"."record_debt_payment"("p_debt_id" "uuid", "p_amount_paid" numeric, "p_principal_paid" numeric, "p_interest_paid" numeric, "p_payment_date" "date", "p_notes" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."record_debt_payment"("p_debt_id" "uuid", "p_amount_paid" numeric, "p_principal_paid" numeric, "p_interest_paid" numeric, "p_payment_date" "date", "p_notes" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."record_debt_payment"("p_debt_id" "uuid", "p_amount_paid" numeric, "p_principal_paid" numeric, "p_interest_paid" numeric, "p_payment_date" "date", "p_notes" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."rls_auto_enable"() TO "anon";
GRANT ALL ON FUNCTION "public"."rls_auto_enable"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."rls_auto_enable"() TO "service_role";



REVOKE ALL ON FUNCTION "public"."terminate_idle_electric_connections"("idle_threshold" interval) FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."terminate_idle_electric_connections"("idle_threshold" interval) TO "anon";
GRANT ALL ON FUNCTION "public"."terminate_idle_electric_connections"("idle_threshold" interval) TO "authenticated";
GRANT ALL ON FUNCTION "public"."terminate_idle_electric_connections"("idle_threshold" interval) TO "service_role";



GRANT ALL ON FUNCTION "public"."update_debt_updated_at"() TO "anon";
GRANT ALL ON FUNCTION "public"."update_debt_updated_at"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_debt_updated_at"() TO "service_role";



GRANT ALL ON FUNCTION "public"."update_updated_at"() TO "anon";
GRANT ALL ON FUNCTION "public"."update_updated_at"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_updated_at"() TO "service_role";



GRANT ALL ON FUNCTION "public"."update_updated_at_column"() TO "anon";
GRANT ALL ON FUNCTION "public"."update_updated_at_column"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_updated_at_column"() TO "service_role";






GRANT ALL ON TABLE "public"."account_deletion_audit" TO "anon";
GRANT ALL ON TABLE "public"."account_deletion_audit" TO "authenticated";
GRANT ALL ON TABLE "public"."account_deletion_audit" TO "service_role";



GRANT ALL ON SEQUENCE "public"."account_deletion_audit_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."account_deletion_audit_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."account_deletion_audit_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."account_deletion_requests" TO "anon";
GRANT ALL ON TABLE "public"."account_deletion_requests" TO "authenticated";
GRANT ALL ON TABLE "public"."account_deletion_requests" TO "service_role";



GRANT ALL ON TABLE "public"."accounts" TO "anon";
GRANT ALL ON TABLE "public"."accounts" TO "authenticated";
GRANT ALL ON TABLE "public"."accounts" TO "service_role";



GRANT ALL ON TABLE "public"."allocations" TO "anon";
GRANT ALL ON TABLE "public"."allocations" TO "authenticated";
GRANT ALL ON TABLE "public"."allocations" TO "service_role";



GRANT ALL ON TABLE "public"."budget_archive_reports" TO "anon";
GRANT ALL ON TABLE "public"."budget_archive_reports" TO "authenticated";
GRANT ALL ON TABLE "public"."budget_archive_reports" TO "service_role";



GRANT ALL ON TABLE "public"."budgets" TO "anon";
GRANT ALL ON TABLE "public"."budgets" TO "authenticated";
GRANT ALL ON TABLE "public"."budgets" TO "service_role";



GRANT ALL ON TABLE "public"."categories" TO "anon";
GRANT ALL ON TABLE "public"."categories" TO "authenticated";
GRANT ALL ON TABLE "public"."categories" TO "service_role";



GRANT ALL ON TABLE "public"."debt_payments" TO "anon";
GRANT ALL ON TABLE "public"."debt_payments" TO "authenticated";
GRANT ALL ON TABLE "public"."debt_payments" TO "service_role";
GRANT SELECT ON TABLE "public"."debt_payments" TO "electric_user";



GRANT ALL ON TABLE "public"."debts" TO "anon";
GRANT ALL ON TABLE "public"."debts" TO "authenticated";
GRANT ALL ON TABLE "public"."debts" TO "service_role";
GRANT SELECT ON TABLE "public"."debts" TO "electric_user";



GRANT ALL ON TABLE "public"."goals" TO "anon";
GRANT ALL ON TABLE "public"."goals" TO "authenticated";
GRANT ALL ON TABLE "public"."goals" TO "service_role";



GRANT ALL ON TABLE "public"."profiles" TO "anon";
GRANT ALL ON TABLE "public"."profiles" TO "authenticated";
GRANT ALL ON TABLE "public"."profiles" TO "service_role";



GRANT ALL ON TABLE "public"."recurring_transactions" TO "anon";
GRANT ALL ON TABLE "public"."recurring_transactions" TO "authenticated";
GRANT ALL ON TABLE "public"."recurring_transactions" TO "service_role";



GRANT ALL ON TABLE "public"."service_templates" TO "anon";
GRANT ALL ON TABLE "public"."service_templates" TO "authenticated";
GRANT ALL ON TABLE "public"."service_templates" TO "service_role";



GRANT ALL ON TABLE "public"."transactions" TO "anon";
GRANT ALL ON TABLE "public"."transactions" TO "authenticated";
GRANT ALL ON TABLE "public"."transactions" TO "service_role";



GRANT ALL ON TABLE "public"."user_settings" TO "anon";
GRANT ALL ON TABLE "public"."user_settings" TO "authenticated";
GRANT ALL ON TABLE "public"."user_settings" TO "service_role";



GRANT ALL ON TABLE "public"."v_upcoming_recurring" TO "anon";
GRANT ALL ON TABLE "public"."v_upcoming_recurring" TO "authenticated";
GRANT ALL ON TABLE "public"."v_upcoming_recurring" TO "service_role";



GRANT ALL ON TABLE "public"."v_user_categories" TO "anon";
GRANT ALL ON TABLE "public"."v_user_categories" TO "authenticated";
GRANT ALL ON TABLE "public"."v_user_categories" TO "service_role";



GRANT ALL ON TABLE "public"."webhook_events" TO "anon";
GRANT ALL ON TABLE "public"."webhook_events" TO "authenticated";
GRANT ALL ON TABLE "public"."webhook_events" TO "service_role";



ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "service_role";









-- CHECK constraints on character varying columns, kept separate from CREATE TABLE
-- for readability. NOTE: `supabase db diff --linked` will report a phantom
-- "drop + re-add" for these 6 constraints on every run. This is harmless --
-- the constraints are functionally identical between local and remote, but
-- PG 17 normalizes the stored definition (element-wise casts) differently
-- from how remote stored them historically (array-wise cast). Semantic
-- behavior is the same. To eliminate the noise, the constraints would need
-- to be dropped + re-created on remote (out of scope for the squash).
ALTER TABLE "public"."accounts" ADD CONSTRAINT "accounts_type_check" CHECK (((type)::text = ANY ((ARRAY['checking'::character varying, 'savings'::character varying, 'credit_card'::character varying, 'cash'::character varying, 'investment'::character varying])::text[]))) NOT VALID;
ALTER TABLE "public"."accounts" VALIDATE CONSTRAINT "accounts_type_check";

ALTER TABLE "public"."budgets" ADD CONSTRAINT "budgets_period_check" CHECK (((period)::text = ANY ((ARRAY['monthly'::character varying, 'yearly'::character varying])::text[]))) NOT VALID;
ALTER TABLE "public"."budgets" VALIDATE CONSTRAINT "budgets_period_check";

ALTER TABLE "public"."categories" ADD CONSTRAINT "categories_type_check" CHECK (((category_type)::text = ANY ((ARRAY['income'::character varying, 'expense'::character varying])::text[]))) NOT VALID;
ALTER TABLE "public"."categories" VALIDATE CONSTRAINT "categories_type_check";

ALTER TABLE "public"."recurring_transactions" ADD CONSTRAINT "recurring_transactions_frequency_check" CHECK (((frequency)::text = ANY ((ARRAY['weekly'::character varying, 'biweekly'::character varying, 'monthly'::character varying, 'yearly'::character varying])::text[]))) NOT VALID;
ALTER TABLE "public"."recurring_transactions" VALIDATE CONSTRAINT "recurring_transactions_frequency_check";

ALTER TABLE "public"."recurring_transactions" ADD CONSTRAINT "recurring_transactions_type_check" CHECK (((type)::text = ANY ((ARRAY['income'::character varying, 'expense'::character varying])::text[]))) NOT VALID;
ALTER TABLE "public"."recurring_transactions" VALIDATE CONSTRAINT "recurring_transactions_type_check";

ALTER TABLE "public"."transactions" ADD CONSTRAINT "transactions_type_check" CHECK (((type)::text = ANY ((ARRAY['income'::character varying, 'expense'::character varying])::text[]))) NOT VALID;
ALTER TABLE "public"."transactions" VALIDATE CONSTRAINT "transactions_type_check";
