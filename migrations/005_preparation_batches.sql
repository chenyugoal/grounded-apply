-- Durable immutable preparation requests and append-only item checkpoints.
CREATE TABLE preparation_batches (
    id TEXT PRIMARY KEY,
    manifest_json TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
    created_at TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(id) ON DELETE RESTRICT
);
CREATE TABLE preparation_batch_items (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES preparation_batches(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK (position >= 0),
    job_id TEXT NOT NULL REFERENCES job_snapshots(id) ON DELETE RESTRICT,
    spec_json TEXT NOT NULL,
    spec_sha256 TEXT NOT NULL CHECK (length(spec_sha256) = 64),
    UNIQUE(batch_id, position),
    UNIQUE(batch_id, job_id)
);
CREATE TABLE preparation_batch_events (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES preparation_batch_items(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK (position >= 0),
    at TEXT NOT NULL,
    state_json TEXT NOT NULL,
    previous_sha256 TEXT,
    event_sha256 TEXT NOT NULL CHECK (length(event_sha256) = 64),
    UNIQUE(item_id, position)
);
CREATE TABLE preparation_batch_leases (
    batch_id TEXT PRIMARY KEY REFERENCES preparation_batches(id) ON DELETE RESTRICT,
    owner TEXT,
    expires_at TEXT,
    epoch INTEGER NOT NULL DEFAULT 0 CHECK (epoch >= 0),
    stop_reason TEXT CHECK (stop_reason IN ('item_budget', 'time_budget', 'capacity_reached', 'shared_failure')),
    CHECK ((owner IS NULL AND expires_at IS NULL) OR (owner IS NOT NULL AND expires_at IS NOT NULL))
);
CREATE TRIGGER preparation_batches_no_update BEFORE UPDATE ON preparation_batches BEGIN SELECT RAISE(ABORT, 'Immutable preparation batch'); END;
CREATE TRIGGER preparation_batches_no_delete BEFORE DELETE ON preparation_batches BEGIN SELECT RAISE(ABORT, 'Immutable preparation batch'); END;
CREATE TRIGGER preparation_items_no_update BEFORE UPDATE ON preparation_batch_items BEGIN SELECT RAISE(ABORT, 'Immutable preparation item'); END;
CREATE TRIGGER preparation_items_no_delete BEFORE DELETE ON preparation_batch_items BEGIN SELECT RAISE(ABORT, 'Immutable preparation item'); END;
CREATE TRIGGER preparation_events_no_update BEFORE UPDATE ON preparation_batch_events BEGIN SELECT RAISE(ABORT, 'Append-only preparation events'); END;
CREATE TRIGGER preparation_events_no_delete BEFORE DELETE ON preparation_batch_events BEGIN SELECT RAISE(ABORT, 'Append-only preparation events'); END;
CREATE TRIGGER preparation_leases_no_delete BEFORE DELETE ON preparation_batch_leases BEGIN SELECT RAISE(ABORT, 'Durable preparation lease'); END;
