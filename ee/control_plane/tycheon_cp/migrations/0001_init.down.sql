-- DESTRUCTIVE: removes every table and therefore all customer data. The rollback command refuses to
-- run this without an explicit flag. Restore from a backup instead unless the database is empty.
DROP FUNCTION IF EXISTS cp_claim_finetune_job(text, text);
DROP FUNCTION IF EXISTS cp_list_orgs();
DROP FUNCTION IF EXISTS cp_org_for_stripe_customer(text);
DROP FUNCTION IF EXISTS cp_org_by_slug(text);
DROP FUNCTION IF EXISTS cp_api_key_lookup(text);
DROP FUNCTION IF EXISTS cp_login_lookup(text);
DROP FUNCTION IF EXISTS cp_signup(text, text, text, text);
DROP TABLE IF EXISTS audit_log, routing_configs, models, finetune_jobs, oidc_providers,
  oneoff_invoices, stripe_events, subscriptions, usage_events, usage_counters, data_files,
  data_sources, credentials, api_keys, users, orgs CASCADE;
DROP FUNCTION IF EXISTS cp_current_org();
