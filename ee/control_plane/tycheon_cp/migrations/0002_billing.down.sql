DROP FUNCTION IF EXISTS cp_record_stripe_event(text);
GRANT INSERT ON stripe_events TO {app_role};
ALTER TABLE usage_events DROP COLUMN IF EXISTS stripe_identifier;
ALTER TABLE subscriptions DROP COLUMN IF EXISTS cancel_at_period_end;
ALTER TABLE subscriptions DROP COLUMN IF EXISTS stripe_event_created;
