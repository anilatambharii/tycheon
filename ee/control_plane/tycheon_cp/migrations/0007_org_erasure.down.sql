DROP FUNCTION IF EXISTS cp_mark_blobs_erased(uuid);
DROP FUNCTION IF EXISTS cp_pending_erasures();
DROP FUNCTION IF EXISTS cp_erase_org(uuid, text);
DROP TABLE IF EXISTS erasure_receipts;
