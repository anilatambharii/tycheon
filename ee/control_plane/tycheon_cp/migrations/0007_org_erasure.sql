-- Organisation erasure (right to erasure) in two phases.
--
-- 1. An owner REQUESTS erasure: one row in erasure_requests (tenant data, row-level security) with
--    an execute_after time. Until then anyone with admin rights can cancel it.
-- 2. When the time has passed, a scheduled job EXECUTES it through cp_erase_org. The database itself
--    refuses to erase before execute_after, from outside the organisation's own tenant context, or
--    while a fine-tune job is running. Every tenant table references orgs(id) ON DELETE CASCADE,
--    including the audit log, which the application cannot otherwise delete from.
--
-- erasure_receipts is what remains: opaque ids, a keyed hash of the slug (computed by the
-- application with a server-side key, so it cannot be guessed from a list of company names), the
-- role and pseudonymous id of the requester, and counts. No name, email or slug.
CREATE TABLE erasure_requests (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL UNIQUE REFERENCES orgs(id) ON DELETE CASCADE,
  requested_by      uuid,
  requested_by_role text NOT NULL,
  requested_at      timestamptz NOT NULL DEFAULT now(),
  execute_after     timestamptz NOT NULL,
  stripe_done_at    timestamptz,
  attempts          integer NOT NULL DEFAULT 0,
  last_error        text
);
ALTER TABLE erasure_requests ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erasure_requests
  USING (org_id = cp_current_org()) WITH CHECK (org_id = cp_current_org());
GRANT SELECT, INSERT, DELETE ON erasure_requests TO {app_role};
GRANT UPDATE (stripe_done_at, attempts, last_error) ON erasure_requests TO {app_role};

CREATE TABLE erasure_receipts (
  id               uuid PRIMARY KEY,
  org_id           uuid NOT NULL,
  slug_hmac        text NOT NULL,
  requested_by     uuid,
  actor_role       text NOT NULL,
  requested_at     timestamptz NOT NULL,
  db_erased_at     timestamptz NOT NULL DEFAULT now(),
  blobs_erased_at  timestamptz,
  audit_rows       bigint NOT NULL DEFAULT 0,
  audit_last_at    timestamptz
);

CREATE FUNCTION cp_erase_org(p_org uuid, p_receipt uuid, p_slug_hmac text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE r erasure_requests%ROWTYPE; n bigint; last_at timestamptz;
BEGIN
  IF p_org IS NULL OR p_org IS DISTINCT FROM cp_current_org() THEN
    RAISE EXCEPTION 'an organisation can only be erased from its own tenant context';
  END IF;
  SELECT * INTO r FROM erasure_requests WHERE org_id = p_org FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'erasure has not been requested';
  END IF;
  IF r.execute_after > now() THEN
    RAISE EXCEPTION 'the erasure grace period has not ended';
  END IF;
  IF EXISTS (SELECT 1 FROM finetune_jobs WHERE org_id = p_org AND status = 'running') THEN
    RAISE EXCEPTION 'a fine-tune job is running; cancel it and retry';
  END IF;
  SELECT count(*), max(occurred_at) INTO n, last_at FROM audit_log WHERE org_id = p_org;
  INSERT INTO erasure_receipts
      (id, org_id, slug_hmac, requested_by, actor_role, requested_at, audit_rows, audit_last_at)
    VALUES (p_receipt, p_org, p_slug_hmac, r.requested_by, r.requested_by_role, r.requested_at, n, last_at);
  DELETE FROM orgs WHERE id = p_org;
  RETURN p_receipt;
END $$;

-- Cross-tenant list of requests whose grace period has ended, for the scheduled job.
CREATE FUNCTION cp_due_erasures() RETURNS TABLE (org_id uuid, request_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT org_id, id FROM erasure_requests WHERE execute_after <= now() ORDER BY execute_after
$$;

CREATE FUNCTION cp_pending_erasures() RETURNS TABLE (receipt_id uuid, org_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT id, org_id FROM erasure_receipts WHERE blobs_erased_at IS NULL ORDER BY db_erased_at
$$;

-- Every receipt, so the scheduled job can write any tombstone that a crash left missing.
CREATE FUNCTION cp_list_receipts() RETURNS SETOF erasure_receipts
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT * FROM erasure_receipts ORDER BY db_erased_at
$$;

CREATE FUNCTION cp_mark_blobs_erased(p_receipt uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  UPDATE erasure_receipts SET blobs_erased_at = now() WHERE id = p_receipt AND blobs_erased_at IS NULL;
  RETURN FOUND;
END $$;

-- Counts only, for metrics and alerts: no identifiers leave the database.
CREATE FUNCTION cp_erasure_stats()
RETURNS TABLE (scheduled bigint, overdue bigint, oldest_overdue_seconds double precision, pending_files bigint)
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT (SELECT count(*) FROM erasure_requests),
         (SELECT count(*) FROM erasure_requests WHERE execute_after <= now() - interval '1 hour'),
         coalesce((SELECT extract(epoch FROM now() - min(execute_after)) FROM erasure_requests
                    WHERE execute_after <= now()), 0)::double precision,
         (SELECT count(*) FROM erasure_receipts WHERE blobs_erased_at IS NULL)
$$;

-- Owner-only (NOT granted to the application role): after restoring a backup, an operator replays
-- the durable tombstone log through this so erased organisations do not come back.
CREATE FUNCTION cp_reapply_erasure(
  p_org uuid, p_receipt uuid, p_slug_hmac text, p_requested_by uuid, p_actor_role text,
  p_requested_at timestamptz, p_erased_at timestamptz
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE n bigint;
BEGIN
  INSERT INTO erasure_receipts
      (id, org_id, slug_hmac, requested_by, actor_role, requested_at, db_erased_at)
    VALUES (p_receipt, p_org, p_slug_hmac, p_requested_by, p_actor_role, p_requested_at, p_erased_at)
    ON CONFLICT (id) DO NOTHING;
  DELETE FROM orgs WHERE id = p_org;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n > 0;
END $$;

REVOKE ALL ON FUNCTION cp_erase_org, cp_due_erasures, cp_pending_erasures, cp_mark_blobs_erased,
  cp_list_receipts, cp_erasure_stats, cp_reapply_erasure FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cp_erase_org, cp_due_erasures, cp_pending_erasures, cp_mark_blobs_erased,
  cp_list_receipts, cp_erasure_stats TO {app_role};
