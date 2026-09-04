-- Add durable profile-import review associations and terminal decisions.
--
-- The creating import workflow and proposal position identify the review slot.
-- Claim/evidence state remains a projection; this row preserves the immutable
-- origin, reviewed-record hash, deciding workflow, actor, and decision time.

CREATE TABLE profile_import_review_items (
    import_workflow_run_id TEXT NOT NULL
        REFERENCES workflow_runs (id) ON DELETE RESTRICT,
    proposal_index INTEGER NOT NULL CHECK (proposal_index >= 0),
    claim_id TEXT NOT NULL UNIQUE
        REFERENCES claims (id) ON DELETE RESTRICT,
    evidence_id TEXT NOT NULL UNIQUE
        REFERENCES evidence (id) ON DELETE RESTRICT,
    record_sha256 TEXT NOT NULL
        CHECK (
            record_sha256 = lower(record_sha256)
            AND length(record_sha256) = 64
            AND record_sha256 NOT GLOB '*[^0-9a-f]*'
        ),
    decision TEXT
        CHECK (decision IS NULL OR decision IN ('approved', 'rejected')),
    decision_workflow_run_id TEXT UNIQUE
        REFERENCES workflow_runs (id) ON DELETE RESTRICT,
    decided_by TEXT,
    decided_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (import_workflow_run_id, proposal_index),
    CHECK (
        (
            decision IS NULL
            AND decision_workflow_run_id IS NULL
            AND decided_by IS NULL
            AND decided_at IS NULL
            AND updated_at = created_at
        )
        OR (
            decision IS NOT NULL
            AND decision_workflow_run_id IS NOT NULL
            AND length(trim(coalesce(decided_by, ''))) > 0
            AND decided_at IS NOT NULL
            AND updated_at = decided_at
        )
    ),
    CHECK (
        decision_workflow_run_id IS NULL
        OR decision_workflow_run_id <> import_workflow_run_id
    ),
    CHECK (updated_at >= created_at)
);

CREATE INDEX ix_profile_import_review_items_decision_created
    ON profile_import_review_items (decision, created_at, import_workflow_run_id, proposal_index);
