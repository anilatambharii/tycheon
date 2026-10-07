-- Single-use browser sign-in tickets: a ticket id can be redeemed once, without the application
-- being able to list them.
CREATE TABLE sso_tickets (
  jti         text PRIMARY KEY,
  redeemed_at timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION cp_redeem_ticket(p_jti text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  INSERT INTO sso_tickets (jti) VALUES (p_jti) ON CONFLICT DO NOTHING;
  RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION cp_redeem_ticket FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cp_redeem_ticket TO {app_role};
