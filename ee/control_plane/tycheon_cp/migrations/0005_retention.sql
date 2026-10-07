-- Retention. The audit log is append-only for the application, so deleting expired entries is
-- one narrow SECURITY DEFINER function: it can only remove an organisation's entries older than a
-- number of days, computed on the database clock, and never anything newer than 30 days.
CREATE FUNCTION cp_purge_audit(p_org uuid, p_days integer) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE n bigint;
BEGIN
  IF p_days < 30 THEN
    RAISE EXCEPTION 'audit entries are kept for at least 30 days';
  END IF;
  DELETE FROM audit_log WHERE org_id = p_org AND occurred_at < now() - make_interval(days => p_days);
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;
REVOKE ALL ON FUNCTION cp_purge_audit FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cp_purge_audit TO {app_role};
