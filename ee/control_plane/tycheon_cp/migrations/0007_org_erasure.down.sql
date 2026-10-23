DROP FUNCTION IF EXISTS cp_reapply_erasure(uuid, uuid, text, uuid, text, timestamptz, timestamptz);
DROP FUNCTION IF EXISTS cp_erasure_stats();
DROP FUNCTION IF EXISTS cp_mark_blobs_erased(uuid);
DROP FUNCTION IF EXISTS cp_list_receipts();
DROP FUNCTION IF EXISTS cp_pending_erasures();
DROP FUNCTION IF EXISTS cp_due_erasures();
DROP FUNCTION IF EXISTS cp_erase_org(uuid, uuid, text);
DROP TABLE IF EXISTS erasure_receipts;
DROP TABLE IF EXISTS erasure_requests;
