-- Grounded Apply schema version 1.
--
-- Migration transactions and PRAGMA user_version are managed by
-- SQLiteRepository.  Keep this file free of BEGIN/COMMIT statements so a
-- failed migration can be rolled back with its schema_migrations record.

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
    checksum_sha256 TEXT NOT NULL
        CHECK (
            length(checksum_sha256) = 64
            AND lower(checksum_sha256) NOT GLOB '*[^0-9a-f]*'
        ),
    applied_at TEXT NOT NULL
);

CREATE TABLE workflow_runs (
    id TEXT PRIMARY KEY CHECK (length(trim(id)) > 0),
    workflow_type TEXT NOT NULL CHECK (length(trim(workflow_type)) > 0),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (
            status IN (
                'queued',
                'running',
                'waiting_for_input',
                'succeeded',
                'failed',
                'cancelled'
            )
        ),
    idempotency_key TEXT,
    input_hash_sha256 TEXT
        CHECK (
            input_hash_sha256 IS NULL
            OR (
                length(input_hash_sha256) = 64
                AND lower(input_hash_sha256) NOT GLOB '*[^0-9a-f]*'
            )
        ),
    input_json TEXT NOT NULL DEFAULT '{}',
    current_step TEXT,
    completed_steps_json TEXT NOT NULL DEFAULT '[]',
    generated_artifacts_json TEXT NOT NULL DEFAULT '[]',
    outstanding_need_info_json TEXT NOT NULL DEFAULT '[]',
    model_name TEXT,
    prompt_version TEXT,
    retry_policy_json TEXT NOT NULL DEFAULT '{}',
    failure_code TEXT,
    failure_reason TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (idempotency_key IS NULL OR length(trim(idempotency_key)) > 0),
    CHECK (current_step IS NULL OR length(trim(current_step)) > 0),
    CHECK (
        status NOT IN ('running', 'waiting_for_input', 'succeeded', 'failed')
        OR started_at IS NOT NULL
    ),
    CHECK (
        status NOT IN ('succeeded', 'failed', 'cancelled')
        OR finished_at IS NOT NULL
    ),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at),
    CHECK (updated_at >= created_at)
);

CREATE UNIQUE INDEX uq_workflow_runs_idempotency
    ON workflow_runs (workflow_type, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX ix_workflow_runs_status_updated
    ON workflow_runs (status, updated_at DESC);

CREATE INDEX ix_workflow_runs_type_created
    ON workflow_runs (workflow_type, created_at DESC);

CREATE TABLE artifacts (
    id TEXT PRIMARY KEY CHECK (length(trim(id)) > 0),
    artifact_type TEXT NOT NULL CHECK (length(trim(artifact_type)) > 0),
    uri TEXT,
    local_path TEXT,
    original_name TEXT,
    media_type TEXT,
    byte_size INTEGER CHECK (byte_size IS NULL OR byte_size >= 0),
    content_sha256 TEXT
        CHECK (
            content_sha256 IS NULL
            OR (
                length(content_sha256) = 64
                AND lower(content_sha256) NOT GLOB '*[^0-9a-f]*'
            )
        ),
    sensitivity TEXT NOT NULL DEFAULT 'personal'
        CHECK (sensitivity IN ('public', 'personal', 'confidential', 'highly_sensitive')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    captured_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (uri IS NOT NULL OR local_path IS NOT NULL),
    CHECK (uri IS NULL OR length(trim(uri)) > 0),
    CHECK (local_path IS NULL OR length(trim(local_path)) > 0),
    CHECK (updated_at >= created_at)
);

CREATE INDEX ix_artifacts_type_captured
    ON artifacts (artifact_type, captured_at DESC);

CREATE INDEX ix_artifacts_content_sha256
    ON artifacts (content_sha256)
    WHERE content_sha256 IS NOT NULL;

CREATE TABLE evidence (
    id TEXT PRIMARY KEY CHECK (length(trim(id)) > 0),
    artifact_id TEXT REFERENCES artifacts (id) ON DELETE RESTRICT,
    locator_json TEXT NOT NULL DEFAULT '{}',
    source_text TEXT,
    extraction_method TEXT NOT NULL CHECK (length(trim(extraction_method)) > 0),
    captured_at TEXT NOT NULL,
    checksum_sha256 TEXT
        CHECK (
            checksum_sha256 IS NULL
            OR (
                length(checksum_sha256) = 64
                AND lower(checksum_sha256) NOT GLOB '*[^0-9a-f]*'
            )
        ),
    source_type TEXT NOT NULL
        CHECK (
            source_type IN (
                'user_statement',
                'imported_resume',
                'transcript',
                'portfolio',
                'derivation',
                'application_answer',
                'other'
            )
        ),
    source_ref TEXT NOT NULL CHECK (length(trim(source_ref)) > 0),
    confirmation_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (confirmation_status IN ('pending', 'confirmed', 'rejected')),
    confirmed_at TEXT,
    confirmed_by TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (artifact_id IS NOT NULL OR length(trim(coalesce(source_text, ''))) > 0),
    CHECK (confirmation_status <> 'confirmed' OR confirmed_at IS NOT NULL),
    CHECK (updated_at >= created_at)
);

CREATE INDEX ix_evidence_artifact
    ON evidence (artifact_id)
    WHERE artifact_id IS NOT NULL;

CREATE INDEX ix_evidence_confirmation_captured
    ON evidence (confirmation_status, captured_at DESC);

CREATE TABLE claims (
    id TEXT PRIMARY KEY CHECK (length(trim(id)) > 0),
    claim_type TEXT NOT NULL CHECK (length(trim(claim_type)) > 0),
    subject_type TEXT NOT NULL DEFAULT 'person' CHECK (length(trim(subject_type)) > 0),
    subject_id TEXT,
    value_json TEXT NOT NULL,
    canonical_text TEXT NOT NULL CHECK (length(trim(canonical_text)) > 0),
    status TEXT NOT NULL DEFAULT 'needs_review'
        CHECK (
            status IN (
                'verified',
                'derived',
                'needs_review',
                'contradicted',
                'superseded',
                'withdrawn'
            )
        ),
    approval_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (approval_status IN ('pending', 'approved', 'rejected')),
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    sensitivity TEXT NOT NULL DEFAULT 'personal'
        CHECK (sensitivity IN ('public', 'personal', 'confidential', 'highly_sensitive')),
    scope_type TEXT NOT NULL DEFAULT 'global'
        CHECK (
            scope_type IN (
                'global',
                'role_family',
                'geography',
                'company',
                'job',
                'application',
                'one_time'
            )
        ),
    scope_id TEXT,
    effective_from TEXT,
    effective_to TEXT,
    source_type TEXT NOT NULL
        CHECK (
            source_type IN (
                'user_statement',
                'imported_resume',
                'transcript',
                'portfolio',
                'derivation',
                'application_answer',
                'other'
            )
        ),
    source_ref TEXT,
    verified_at TEXT,
    verified_by TEXT,
    derivation_rule_name TEXT,
    derivation_rule_version TEXT,
    derivation_staleness_policy TEXT,
    derivation_calculated_at TEXT,
    supersedes_id TEXT REFERENCES claims (id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (scope_type = 'global' AND scope_id IS NULL)
        OR (scope_type <> 'global' AND length(trim(coalesce(scope_id, ''))) > 0)
    ),
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to > effective_from),
    CHECK (status <> 'verified' OR verified_at IS NOT NULL),
    CHECK (
        (
            status = 'derived'
            AND length(trim(coalesce(derivation_rule_name, ''))) > 0
            AND length(trim(coalesce(derivation_rule_version, ''))) > 0
            AND length(trim(coalesce(derivation_staleness_policy, ''))) > 0
            AND derivation_calculated_at IS NOT NULL
        )
        OR (
            status <> 'derived'
            AND derivation_rule_name IS NULL
            AND derivation_rule_version IS NULL
            AND derivation_staleness_policy IS NULL
            AND derivation_calculated_at IS NULL
        )
    ),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK (updated_at >= created_at)
);

CREATE INDEX ix_claims_status_type
    ON claims (status, approval_status, claim_type);

CREATE INDEX ix_claims_subject
    ON claims (subject_type, subject_id);

CREATE INDEX ix_claims_scope
    ON claims (scope_type, scope_id, status);

CREATE INDEX ix_claims_supersedes
    ON claims (supersedes_id)
    WHERE supersedes_id IS NOT NULL;

CREATE INDEX ix_claims_effective_dates
    ON claims (effective_from, effective_to);

CREATE TABLE claim_derivation_inputs (
    derived_claim_id TEXT NOT NULL REFERENCES claims (id) ON DELETE CASCADE,
    input_claim_id TEXT NOT NULL REFERENCES claims (id) ON DELETE RESTRICT,
    input_order INTEGER NOT NULL CHECK (input_order >= 0),
    PRIMARY KEY (derived_claim_id, input_claim_id),
    UNIQUE (derived_claim_id, input_order),
    CHECK (derived_claim_id <> input_claim_id)
);

CREATE INDEX ix_claim_derivation_inputs_input
    ON claim_derivation_inputs (input_claim_id);

CREATE TABLE claim_evidence (
    claim_id TEXT NOT NULL REFERENCES claims (id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidence (id) ON DELETE RESTRICT,
    relationship TEXT NOT NULL DEFAULT 'supports'
        CHECK (relationship IN ('supports', 'qualifies', 'contradicts', 'derives_from')),
    strength REAL NOT NULL DEFAULT 1.0 CHECK (strength >= 0.0 AND strength <= 1.0),
    note TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (claim_id, evidence_id, relationship)
);

CREATE INDEX ix_claim_evidence_evidence
    ON claim_evidence (evidence_id, relationship);

CREATE TABLE memory_proposals (
    id TEXT PRIMARY KEY CHECK (length(trim(id)) > 0),
    proposal_type TEXT NOT NULL CHECK (length(trim(proposal_type)) > 0),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'edited', 'rejected')),
    question_intent TEXT,
    verbatim_answer TEXT,
    proposal_json TEXT NOT NULL,
    proposed_scope_type TEXT
        CHECK (
            proposed_scope_type IS NULL
            OR proposed_scope_type IN (
                'global',
                'role_family',
                'geography',
                'company',
                'job',
                'application',
                'one_time'
            )
        ),
    proposed_scope_id TEXT,
    proposed_sensitivity TEXT
        CHECK (
            proposed_sensitivity IS NULL
            OR proposed_sensitivity IN (
                'public',
                'personal',
                'confidential',
                'highly_sensitive'
            )
        ),
    retention_policy TEXT,
    reuse_preview TEXT,
    contradictions_json TEXT NOT NULL DEFAULT '[]',
    source_workflow_run_id TEXT REFERENCES workflow_runs (id) ON DELETE SET NULL,
    source_evidence_id TEXT REFERENCES evidence (id) ON DELETE SET NULL,
    resulting_claim_id TEXT REFERENCES claims (id) ON DELETE SET NULL,
    decision_note TEXT,
    decided_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (proposed_scope_type = 'global' AND proposed_scope_id IS NULL)
        OR proposed_scope_type IS NULL
        OR (
            proposed_scope_type <> 'global'
            AND length(trim(coalesce(proposed_scope_id, ''))) > 0
        )
    ),
    CHECK (status NOT IN ('approved', 'rejected') OR decided_at IS NOT NULL),
    CHECK (status = 'approved' OR resulting_claim_id IS NULL),
    CHECK (updated_at >= created_at)
);

CREATE INDEX ix_memory_proposals_status_created
    ON memory_proposals (status, created_at DESC);

CREATE INDEX ix_memory_proposals_workflow
    ON memory_proposals (source_workflow_run_id)
    WHERE source_workflow_run_id IS NOT NULL;

CREATE INDEX ix_memory_proposals_resulting_claim
    ON memory_proposals (resulting_claim_id)
    WHERE resulting_claim_id IS NOT NULL;
