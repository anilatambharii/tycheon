-- Tycheon Cloud control plane schema. Proprietary: see ee/LICENSE.
--
-- Isolation model: every tenant table carries org_id and has ROW LEVEL SECURITY enabled with one
-- policy, `org_id = cp_current_org()`, for reads and writes. The application connects as a role
-- that owns nothing and bypasses nothing, and sets `app.org_id` per transaction (SET LOCAL) from
-- the authenticated principal only. With no org set the policy matches no rows (fail closed).
-- Lookups that must happen before an org is known (login, API-key authentication, webhooks,
-- the fine-tune queue) are the SECURITY DEFINER functions below, each returning the minimum.
-- Tables are NOT FORCEd, so these functions (owned by the migration role) can see across orgs
-- while the application role cannot.

CREATE FUNCTION cp_current_org() RETURNS uuid
  LANGUAGE sql STABLE
  AS $$ SELECT nullif(current_setting('app.org_id', true), '')::uuid $$;

CREATE TABLE orgs (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug        text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,38}$'),
  name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
  plan        text NOT NULL DEFAULT 'developer',
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  email          text NOT NULL CHECK (email = lower(email) AND length(email) <= 254),
  password_hash  text,
  oidc_subject   text,
  role           text NOT NULL CHECK (role IN ('owner','admin','member','viewer')),
  created_at     timestamptz NOT NULL DEFAULT now(),
  disabled_at    timestamptz
);
CREATE UNIQUE INDEX users_email_key ON users (email);
CREATE UNIQUE INDEX users_oidc_key ON users (org_id, oidc_subject) WHERE oidc_subject IS NOT NULL;

CREATE TABLE api_keys (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  name         text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
  prefix       text NOT NULL UNIQUE,
  secret_hash  text NOT NULL,
  scopes       text[] NOT NULL,
  created_by   uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz,
  revoked_at   timestamptz
);
CREATE INDEX api_keys_org ON api_keys (org_id);

CREATE TABLE credentials (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  name         text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
  provider     text NOT NULL CHECK (length(provider) BETWEEN 1 AND 40),
  kms_key_id   text NOT NULL,
  wrapped_dek  bytea NOT NULL,
  nonce        bytea NOT NULL,
  ciphertext   bytea NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, name)
);

CREATE TABLE data_sources (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
  kind          text NOT NULL CHECK (kind IN ('csv','provider')),
  credential_id uuid REFERENCES credentials(id) ON DELETE SET NULL,
  is_default    boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, name)
);
CREATE UNIQUE INDEX data_sources_one_default ON data_sources (org_id) WHERE is_default;

CREATE TABLE data_files (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  data_source_id uuid NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
  symbol         text NOT NULL CHECK (symbol ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,23}$'),
  blob_key       text NOT NULL,
  n_rows         integer NOT NULL,
  first_ts       timestamptz NOT NULL,
  last_ts        timestamptz NOT NULL,
  sha256         text NOT NULL,
  uploaded_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (data_source_id, symbol)
);

CREATE TABLE usage_counters (
  org_id  uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  period  date NOT NULL,
  kind    text NOT NULL,
  used    bigint NOT NULL CHECK (used >= 0),
  PRIMARY KEY (org_id, period, kind)
);

CREATE TABLE usage_events (
  id               bigserial PRIMARY KEY,
  org_id           uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  kind             text NOT NULL,
  quantity         bigint NOT NULL CHECK (quantity > 0),
  occurred_at      timestamptz NOT NULL DEFAULT now(),
  idempotency_key  text NOT NULL,
  meta             jsonb NOT NULL DEFAULT '{}'::jsonb,
  reported_at      timestamptz,
  UNIQUE (org_id, idempotency_key)
);
CREATE INDEX usage_events_org_time ON usage_events (org_id, occurred_at);

CREATE TABLE subscriptions (
  org_id                  uuid PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
  plan                    text NOT NULL,
  status                  text NOT NULL DEFAULT 'active',
  stripe_customer_id      text UNIQUE,
  stripe_subscription_id  text UNIQUE,
  trial_end               timestamptz,
  current_period_end      timestamptz,
  limits_override         jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at              timestamptz NOT NULL DEFAULT now()
);

-- Webhook de-duplication. Not tenant data: the application may insert and nothing else.
CREATE TABLE stripe_events (
  id           text PRIMARY KEY,
  received_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE oneoff_invoices (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  description        text NOT NULL CHECK (length(description) BETWEEN 1 AND 300),
  amount_cents       bigint NOT NULL CHECK (amount_cents > 0),
  currency           text NOT NULL DEFAULT 'usd',
  status             text NOT NULL DEFAULT 'draft',
  stripe_invoice_id  text,
  created_by         uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE oidc_providers (
  org_id                 uuid PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
  issuer                 text NOT NULL,
  client_id              text NOT NULL,
  jwks_uri               text NOT NULL,
  authorization_endpoint text NOT NULL,
  token_endpoint         text NOT NULL,
  client_secret_credential_id uuid REFERENCES credentials(id) ON DELETE SET NULL,
  allowed_domains        text[] NOT NULL DEFAULT '{}',
  default_role           text NOT NULL DEFAULT 'member' CHECK (default_role IN ('admin','member','viewer'))
);

CREATE TABLE finetune_jobs (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  data_source_id uuid NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
  symbols        text[] NOT NULL,
  params         jsonb NOT NULL,
  gpu_pool       text NOT NULL DEFAULT 'shared',
  status         text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
  worker         text,
  error          text,
  gpu_seconds    numeric NOT NULL DEFAULT 0,
  model_id       uuid,
  created_by     uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  started_at     timestamptz,
  finished_at    timestamptz
);
CREATE INDEX finetune_jobs_queue ON finetune_jobs (status, gpu_pool, created_at);

CREATE TABLE models (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  name         text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
  base_model   text NOT NULL,
  status       text NOT NULL DEFAULT 'candidate'
               CHECK (status IN ('candidate','promoted','rejected','archived')),
  artifact_key text NOT NULL,
  job_id       uuid REFERENCES finetune_jobs(id) ON DELETE SET NULL,
  metrics      jsonb NOT NULL DEFAULT '{}'::jsonb,
  gate         jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at   timestamptz NOT NULL DEFAULT now(),
  promoted_at  timestamptz
);
ALTER TABLE finetune_jobs ADD FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE SET NULL;

CREATE TABLE routing_configs (
  org_id     uuid PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
  config     jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
  id          bigserial PRIMARY KEY,
  org_id      uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  actor       text NOT NULL,
  action      text NOT NULL,
  target      text,
  details     jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_org_time ON audit_log (org_id, occurred_at);

-- ---------------------------------------------------------------- row level security
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['users','api_keys','credentials','data_sources','data_files',
      'usage_counters','usage_events','subscriptions','oneoff_invoices','oidc_providers',
      'finetune_jobs','models','routing_configs','audit_log']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (org_id = cp_current_org()) '
                   'WITH CHECK (org_id = cp_current_org())', t);
  END LOOP;
END $$;
ALTER TABLE orgs ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON orgs USING (id = cp_current_org()) WITH CHECK (id = cp_current_org());

-- ------------------------------------------------------ pre-tenant lookups (SECURITY DEFINER)
CREATE FUNCTION cp_signup(p_slug text, p_name text, p_email text, p_password_hash text)
RETURNS TABLE (org_id uuid, user_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE o uuid; u uuid;
BEGIN
  INSERT INTO orgs (slug, name) VALUES (p_slug, p_name) RETURNING id INTO o;
  INSERT INTO users (org_id, email, password_hash, role) VALUES (o, p_email, p_password_hash, 'owner')
    RETURNING id INTO u;
  INSERT INTO subscriptions (org_id, plan) VALUES (o, 'developer');
  INSERT INTO audit_log (org_id, actor, action, target) VALUES (o, 'user:' || u, 'org.signup', o::text);
  RETURN QUERY SELECT o, u;
END $$;

CREATE FUNCTION cp_login_lookup(p_email text)
RETURNS TABLE (user_id uuid, org_id uuid, password_hash text, role text)
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT id, org_id, password_hash, role FROM users WHERE email = p_email AND disabled_at IS NULL
$$;

CREATE FUNCTION cp_api_key_lookup(p_prefix text)
RETURNS TABLE (key_id uuid, org_id uuid, secret_hash text, scopes text[], revoked boolean)
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT id, org_id, secret_hash, scopes, revoked_at IS NOT NULL FROM api_keys WHERE prefix = p_prefix
$$;

CREATE FUNCTION cp_org_by_slug(p_slug text)
RETURNS TABLE (org_id uuid, plan text)
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT id, plan FROM orgs WHERE slug = p_slug
$$;

CREATE FUNCTION cp_org_for_stripe_customer(p_customer text) RETURNS uuid
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT org_id FROM subscriptions WHERE stripe_customer_id = p_customer
$$;

CREATE FUNCTION cp_list_orgs() RETURNS TABLE (org_id uuid, plan text)
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT id, plan FROM orgs
$$;

-- The fine-tune queue is cross-tenant by nature. A worker claims the oldest queued job in its
-- GPU pool and from then on works inside that job's own tenant context.
CREATE FUNCTION cp_claim_finetune_job(p_pool text, p_worker text)
RETURNS TABLE (job_id uuid, org_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE j uuid; o uuid;
BEGIN
  SELECT f.id, f.org_id INTO j, o FROM finetune_jobs f
   WHERE f.status = 'queued' AND f.gpu_pool = p_pool
   ORDER BY f.created_at FOR UPDATE SKIP LOCKED LIMIT 1;
  IF j IS NULL THEN RETURN; END IF;
  UPDATE finetune_jobs SET status = 'running', worker = p_worker, started_at = now() WHERE id = j;
  RETURN QUERY SELECT j, o;
END $$;

-- ------------------------------------------------------------------------------- grants
GRANT USAGE ON SCHEMA public TO {app_role};
GRANT SELECT, INSERT, UPDATE, DELETE ON
  orgs, users, api_keys, credentials, data_sources, data_files, usage_counters, usage_events,
  subscriptions, oneoff_invoices, oidc_providers, finetune_jobs, models, routing_configs
  TO {app_role};
-- The audit log is append-only for the application.
GRANT SELECT, INSERT ON audit_log TO {app_role};
GRANT INSERT ON stripe_events TO {app_role};
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {app_role};
REVOKE ALL ON FUNCTION cp_signup, cp_login_lookup, cp_api_key_lookup, cp_org_by_slug,
  cp_org_for_stripe_customer, cp_list_orgs, cp_claim_finetune_job FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cp_current_org, cp_signup, cp_login_lookup, cp_api_key_lookup,
  cp_org_by_slug, cp_org_for_stripe_customer, cp_list_orgs, cp_claim_finetune_job TO {app_role};
