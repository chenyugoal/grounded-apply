-- Retire use authority while preserving original import/approval projections.
CREATE TABLE claim_retirements (
    claim_id TEXT PRIMARY KEY REFERENCES claims(id) ON DELETE RESTRICT,
    replacement_claim_id TEXT REFERENCES claims(id) ON DELETE RESTRICT,
    workflow_run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 256),
    preview_token TEXT NOT NULL CHECK (length(preview_token) = 64 AND preview_token NOT GLOB '*[^0-9a-f]*'),
    claim_sha256 TEXT NOT NULL CHECK (length(claim_sha256) = 64),
    replacement_sha256 TEXT,
    idempotency_sha256 TEXT NOT NULL UNIQUE CHECK (length(idempotency_sha256) = 64),
    retired_at TEXT NOT NULL,
    CHECK (replacement_claim_id IS NULL OR replacement_claim_id <> claim_id),
    CHECK ((replacement_claim_id IS NULL) = (replacement_sha256 IS NULL))
);

CREATE TRIGGER claim_retirements_no_update BEFORE UPDATE ON claim_retirements
BEGIN SELECT RAISE(ABORT, 'Claim retirement history is immutable'); END;
CREATE TRIGGER claim_retirements_no_delete BEFORE DELETE ON claim_retirements
BEGIN SELECT RAISE(ABORT, 'Claim retirement history is immutable'); END;
