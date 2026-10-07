ALTER TABLE models DROP CONSTRAINT IF EXISTS promoted_needs_passed_gate;
ALTER TABLE models DROP COLUMN IF EXISTS artifact_sha256;
ALTER TABLE finetune_jobs DROP COLUMN IF EXISTS progress;
