-- Immutable saved scopes and durable on-demand discovery/preparation runs.
CREATE TABLE saved_searches (
    id TEXT PRIMARY KEY,
    manifest_json TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
    created_at TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(id) ON DELETE RESTRICT
);
CREATE TABLE search_runs (
    id TEXT PRIMARY KEY,
    search_id TEXT NOT NULL REFERENCES saved_searches(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(id) ON DELETE RESTRICT
);
CREATE TABLE search_run_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES search_runs(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK (position >= 0),
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    state_json TEXT NOT NULL,
    previous_sha256 TEXT,
    event_sha256 TEXT NOT NULL CHECK (length(event_sha256) = 64),
    UNIQUE(run_id, position)
);
CREATE TABLE search_leases (
    search_id TEXT PRIMARY KEY REFERENCES saved_searches(id) ON DELETE RESTRICT,
    run_id TEXT REFERENCES search_runs(id) ON DELETE RESTRICT,
    owner TEXT,
    expires_at TEXT,
    epoch INTEGER NOT NULL DEFAULT 0 CHECK (epoch >= 0),
    CHECK ((owner IS NULL AND expires_at IS NULL AND run_id IS NULL)
        OR (owner IS NOT NULL AND expires_at IS NOT NULL AND run_id IS NOT NULL))
);
CREATE TABLE search_batch_links (
    batch_id TEXT PRIMARY KEY REFERENCES preparation_batches(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL UNIQUE REFERENCES search_runs(id) ON DELETE RESTRICT
);
CREATE TRIGGER saved_searches_no_update BEFORE UPDATE ON saved_searches BEGIN SELECT RAISE(ABORT, 'Immutable saved search'); END;
CREATE TRIGGER saved_searches_no_delete BEFORE DELETE ON saved_searches BEGIN SELECT RAISE(ABORT, 'Immutable saved search'); END;
CREATE TRIGGER search_runs_no_update BEFORE UPDATE ON search_runs BEGIN SELECT RAISE(ABORT, 'Immutable search run'); END;
CREATE TRIGGER search_runs_no_delete BEFORE DELETE ON search_runs BEGIN SELECT RAISE(ABORT, 'Immutable search run'); END;
CREATE TRIGGER search_events_no_update BEFORE UPDATE ON search_run_events BEGIN SELECT RAISE(ABORT, 'Append-only search events'); END;
CREATE TRIGGER search_events_no_delete BEFORE DELETE ON search_run_events BEGIN SELECT RAISE(ABORT, 'Append-only search events'); END;
CREATE TRIGGER search_leases_no_delete BEFORE DELETE ON search_leases BEGIN SELECT RAISE(ABORT, 'Durable search lease'); END;
CREATE TRIGGER search_links_no_update BEFORE UPDATE ON search_batch_links BEGIN SELECT RAISE(ABORT, 'Immutable search batch link'); END;
CREATE TRIGGER search_links_no_delete BEFORE DELETE ON search_batch_links BEGIN SELECT RAISE(ABORT, 'Immutable search batch link'); END;
