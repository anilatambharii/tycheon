-- Billing state: ordering of Stripe events and the Stripe meter each usage event was sent to.
ALTER TABLE subscriptions ADD COLUMN stripe_event_created bigint NOT NULL DEFAULT 0;
ALTER TABLE subscriptions ADD COLUMN cancel_at_period_end boolean NOT NULL DEFAULT false;
ALTER TABLE usage_events ADD COLUMN stripe_identifier text;

-- Webhook de-duplication without letting the application read the table: the function reports
-- whether this event id is new.
CREATE FUNCTION cp_record_stripe_event(p_id text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  INSERT INTO stripe_events (id) VALUES (p_id) ON CONFLICT DO NOTHING;
  RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION cp_record_stripe_event FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cp_record_stripe_event TO {app_role};
REVOKE INSERT ON stripe_events FROM {app_role};
