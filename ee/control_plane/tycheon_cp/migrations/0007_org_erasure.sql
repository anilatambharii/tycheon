-- Organisation erasure (right to erasure). Deleting an organisation removes every row that belongs
-- to it: all tenant tables reference orgs(id) ON DELETE CASCADE, including the audit log, which the
-- application itself cannot delete from. The erasure is therefore one SECURITY DEFINER function
-- that only runs inside the organisation's own tenant context, refuses while a fine-tune job is
-- running, and leaves behind a receipt that holds no personal data: an opaque organisation id, a
-- hash of the slug, the role that asked, and when the rows and the stored files were removed.
CREATE TABLE erasure_receipts (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL,
  slug_sha256      text NOT NULL,
  actor_role       text NOT NULL,
  db_erased_at     timestamptz NOT NULL DEFAULT now(),
  blobs_erased_at  timestamptz
);

CREATE FUNCTION cp_erase_org(p_org uuid, p_actor_role text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE r uuid; s text;
BEGIN
  IF p_org IS NULL OR p_org IS DISTINCT FROM cp_current_org() THEN
    RAISE EXCEPTION 'an organisation can only be erased from its own tenant context';
  END IF;
  SELECT slug INTO s FROM orgs WHERE id = p_org;
  IF s IS NULL THEN
    RAISE EXCEPTION 'no such organisation';
  END IF;
  IF EXISTS (SELECT 1 FROM finetune_jobs WHERE org_id = p_org AND status = 'running') THEN
    RAISE EXCEPTION 'a fine-tune job is running; cancel it and retry';
  END IF;
  INSERT INTO erasure_receipts (org_id, slug_sha256, actor_role)
    VALUES (p_org, encode(sha256(convert_to(s, 'UTF8')), 'hex'), p_actor_role)
    RETURNING id INTO r;
  DELETE FROM orgs WHERE id = p_org;
  RETURN r;
END $$;

CREATE FUNCTION cp_pending_erasures() RETURNS TABLE (receipt_id uuid, org_id uuid)
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT id, org_id FROM erasure_receipts WHERE blobs_erased_at IS NULL ORDER BY db_erased_at
$$;

CREATE FUNCTION cp_mark_blobs_erased(p_receipt uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  UPDATE erasure_receipts SET blobs_erased_at = now() WHERE id = p_receipt AND blobs_erased_at IS NULL;
  RETURN FOUND;
END $$;

REVOKE ALL ON FUNCTION cp_erase_org, cp_pending_erasures, cp_mark_blobs_erased FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cp_erase_org, cp_pending_erasures, cp_mark_blobs_erased TO {app_role};
