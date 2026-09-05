-- Immutable local job/material versions and append-only manual application history.
CREATE TABLE job_snapshots (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    source_text TEXT NOT NULL,
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    extractor_version TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(id) ON DELETE RESTRICT
);
CREATE TABLE job_requirements (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES job_snapshots(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK (position >= 0),
    start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK (end_offset > start_offset),
    quoted_text TEXT NOT NULL,
    category TEXT NOT NULL,
    classification_basis TEXT NOT NULL,
    UNIQUE(job_id, position)
);
CREATE TABLE material_versions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES job_snapshots(id) ON DELETE RESTRICT,
    structure_json TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    validation_json TEXT NOT NULL,
    pdf_bytes BLOB NOT NULL,
    latex_text TEXT NOT NULL,
    extracted_text TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL CHECK (length(bundle_sha256) = 64),
    created_at TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(id) ON DELETE RESTRICT
);
CREATE TABLE material_claims (
    material_id TEXT NOT NULL REFERENCES material_versions(id) ON DELETE RESTRICT,
    claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE RESTRICT,
    PRIMARY KEY(material_id, claim_id)
);
CREATE TABLE material_approvals (
    material_id TEXT PRIMARY KEY REFERENCES material_versions(id) ON DELETE RESTRICT,
    bundle_sha256 TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(id) ON DELETE RESTRICT
);
CREATE TABLE applications (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES job_snapshots(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(id) ON DELETE RESTRICT
);
CREATE TABLE application_events (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK (position >= 0),
    state TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    at TEXT NOT NULL,
    previous_sha256 TEXT,
    event_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    UNIQUE(application_id, position)
);
CREATE TABLE submission_snapshots (
    application_id TEXT PRIMARY KEY REFERENCES applications(id) ON DELETE RESTRICT,
    event_id TEXT NOT NULL UNIQUE REFERENCES application_events(id) ON DELETE RESTRICT,
    material_id TEXT NOT NULL REFERENCES material_versions(id) ON DELETE RESTRICT,
    snapshot_json TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    submitted_at TEXT NOT NULL
);

CREATE TRIGGER jobs_no_update BEFORE UPDATE ON job_snapshots BEGIN SELECT RAISE(ABORT, 'Immutable job snapshot'); END;
CREATE TRIGGER jobs_no_delete BEFORE DELETE ON job_snapshots BEGIN SELECT RAISE(ABORT, 'Immutable job snapshot'); END;
CREATE TRIGGER requirements_no_update BEFORE UPDATE ON job_requirements BEGIN SELECT RAISE(ABORT, 'Immutable job requirements'); END;
CREATE TRIGGER requirements_no_delete BEFORE DELETE ON job_requirements BEGIN SELECT RAISE(ABORT, 'Immutable job requirements'); END;
CREATE TRIGGER materials_no_update BEFORE UPDATE ON material_versions BEGIN SELECT RAISE(ABORT, 'Immutable material version'); END;
CREATE TRIGGER materials_no_delete BEFORE DELETE ON material_versions BEGIN SELECT RAISE(ABORT, 'Immutable material version'); END;
CREATE TRIGGER material_claims_no_update BEFORE UPDATE ON material_claims BEGIN SELECT RAISE(ABORT, 'Immutable material provenance'); END;
CREATE TRIGGER material_claims_no_delete BEFORE DELETE ON material_claims BEGIN SELECT RAISE(ABORT, 'Immutable material provenance'); END;
CREATE TRIGGER approvals_no_update BEFORE UPDATE ON material_approvals BEGIN SELECT RAISE(ABORT, 'Immutable material approval'); END;
CREATE TRIGGER approvals_no_delete BEFORE DELETE ON material_approvals BEGIN SELECT RAISE(ABORT, 'Immutable material approval'); END;
CREATE TRIGGER applications_no_update BEFORE UPDATE ON applications BEGIN SELECT RAISE(ABORT, 'Immutable application origin'); END;
CREATE TRIGGER applications_no_delete BEFORE DELETE ON applications BEGIN SELECT RAISE(ABORT, 'Immutable application origin'); END;
CREATE TRIGGER events_no_update BEFORE UPDATE ON application_events BEGIN SELECT RAISE(ABORT, 'Append-only application events'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON application_events BEGIN SELECT RAISE(ABORT, 'Append-only application events'); END;
CREATE TRIGGER submissions_no_update BEFORE UPDATE ON submission_snapshots BEGIN SELECT RAISE(ABORT, 'Immutable submission snapshot'); END;
CREATE TRIGGER submissions_no_delete BEFORE DELETE ON submission_snapshots BEGIN SELECT RAISE(ABORT, 'Immutable submission snapshot'); END;
