-- Grant minimal required access to electric_user (role created via Dashboard)
GRANT USAGE ON SCHEMA public TO electric_user;
GRANT SELECT ON debts TO electric_user;
GRANT SELECT ON debt_payments TO electric_user;

-- Function to clean up idle ElectricSQL connections
CREATE OR REPLACE FUNCTION terminate_idle_electric_connections(idle_threshold INTERVAL DEFAULT '5 minutes')
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
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

-- Function to drop inactive replication slots from ElectricSQL
CREATE OR REPLACE FUNCTION drop_inactive_electric_replication_slots()
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
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

-- Restrict cleanup functions to postgres role only
REVOKE EXECUTE ON FUNCTION terminate_idle_electric_connections(INTERVAL) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION drop_inactive_electric_replication_slots() FROM PUBLIC;
