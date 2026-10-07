-- Fine-tuned model artifacts are verified against this hash before they are loaded.
ALTER TABLE models ADD COLUMN artifact_sha256 text;
ALTER TABLE finetune_jobs ADD COLUMN progress jsonb NOT NULL DEFAULT '{}'::jsonb;
-- A model can only be 'promoted' if the stored gate evidence says it passed: no code path,
-- bug or manual UPDATE by the application role can promote a model that failed its gate.
ALTER TABLE models ADD CONSTRAINT promoted_needs_passed_gate
  CHECK (status <> 'promoted' OR (gate ->> 'passed') = 'true');
