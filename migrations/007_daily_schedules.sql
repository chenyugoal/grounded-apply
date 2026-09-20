-- Explicit daily tick policy. This schema does not install an external trigger.
CREATE TABLE daily_schedules (
    id TEXT PRIMARY KEY,
    search_id TEXT NOT NULL REFERENCES saved_searches(id) ON DELETE RESTRICT,
    manifest_json TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    search_manifest_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(id) ON DELETE RESTRICT
);
CREATE TABLE schedule_occurrences (
    id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL REFERENCES daily_schedules(id) ON DELETE RESTRICT,
    local_date TEXT NOT NULL,
    due_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    record_sha256 TEXT NOT NULL,
    UNIQUE(schedule_id, local_date)
);
CREATE TABLE schedule_events (
    id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL REFERENCES daily_schedules(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK (position >= 0),
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    state_json TEXT NOT NULL,
    workflow_run_id TEXT REFERENCES workflow_runs(id) ON DELETE RESTRICT,
    previous_sha256 TEXT,
    event_sha256 TEXT NOT NULL,
    UNIQUE(schedule_id, position)
);
CREATE TABLE occurrence_events (
    id TEXT PRIMARY KEY,
    occurrence_id TEXT NOT NULL REFERENCES schedule_occurrences(id) ON DELETE RESTRICT,
    position INTEGER NOT NULL CHECK (position >= 0),
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    state_json TEXT NOT NULL,
    previous_sha256 TEXT,
    event_sha256 TEXT NOT NULL,
    UNIQUE(occurrence_id, position)
);
CREATE TABLE schedule_leases (
    schedule_id TEXT PRIMARY KEY REFERENCES daily_schedules(id) ON DELETE RESTRICT,
    occurrence_id TEXT REFERENCES schedule_occurrences(id) ON DELETE RESTRICT,
    owner TEXT,
    expires_at TEXT,
    epoch INTEGER NOT NULL DEFAULT 0 CHECK (epoch >= 0),
    CHECK ((owner IS NULL AND expires_at IS NULL AND occurrence_id IS NULL)
        OR (owner IS NOT NULL AND expires_at IS NOT NULL AND occurrence_id IS NOT NULL))
);
CREATE TABLE scheduled_search_links (
    run_id TEXT PRIMARY KEY REFERENCES search_runs(id) ON DELETE RESTRICT,
    occurrence_id TEXT NOT NULL UNIQUE REFERENCES schedule_occurrences(id) ON DELETE RESTRICT
);
CREATE TABLE schedule_notifications (
    id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL REFERENCES daily_schedules(id) ON DELETE RESTRICT,
    occurrence_id TEXT NOT NULL REFERENCES schedule_occurrences(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    delta_json TEXT NOT NULL,
    summary_sha256 TEXT NOT NULL,
    previous_summary_sha256 TEXT NOT NULL,
    notification_sha256 TEXT NOT NULL
);
CREATE TABLE schedule_notification_acks (
    notification_id TEXT PRIMARY KEY REFERENCES schedule_notifications(id) ON DELETE RESTRICT,
    acknowledged_at TEXT NOT NULL,
    workflow_run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(id) ON DELETE RESTRICT
);
CREATE TRIGGER daily_schedules_no_update BEFORE UPDATE ON daily_schedules BEGIN SELECT RAISE(ABORT, 'Immutable schedule'); END;
CREATE TRIGGER daily_schedules_no_delete BEFORE DELETE ON daily_schedules BEGIN SELECT RAISE(ABORT, 'Immutable schedule'); END;
CREATE TRIGGER schedule_occurrences_no_update BEFORE UPDATE ON schedule_occurrences BEGIN SELECT RAISE(ABORT, 'Immutable occurrence'); END;
CREATE TRIGGER schedule_occurrences_no_delete BEFORE DELETE ON schedule_occurrences BEGIN SELECT RAISE(ABORT, 'Immutable occurrence'); END;
CREATE TRIGGER schedule_events_no_update BEFORE UPDATE ON schedule_events BEGIN SELECT RAISE(ABORT, 'Append-only schedule events'); END;
CREATE TRIGGER schedule_events_no_delete BEFORE DELETE ON schedule_events BEGIN SELECT RAISE(ABORT, 'Append-only schedule events'); END;
CREATE TRIGGER occurrence_events_no_update BEFORE UPDATE ON occurrence_events BEGIN SELECT RAISE(ABORT, 'Append-only occurrence events'); END;
CREATE TRIGGER occurrence_events_no_delete BEFORE DELETE ON occurrence_events BEGIN SELECT RAISE(ABORT, 'Append-only occurrence events'); END;
CREATE TRIGGER schedule_leases_no_delete BEFORE DELETE ON schedule_leases BEGIN SELECT RAISE(ABORT, 'Durable schedule lease'); END;
CREATE TRIGGER scheduled_search_links_no_update BEFORE UPDATE ON scheduled_search_links BEGIN SELECT RAISE(ABORT, 'Immutable scheduled search'); END;
CREATE TRIGGER scheduled_search_links_no_delete BEFORE DELETE ON scheduled_search_links BEGIN SELECT RAISE(ABORT, 'Immutable scheduled search'); END;
CREATE TRIGGER schedule_notifications_no_update BEFORE UPDATE ON schedule_notifications BEGIN SELECT RAISE(ABORT, 'Immutable notification'); END;
CREATE TRIGGER schedule_notifications_no_delete BEFORE DELETE ON schedule_notifications BEGIN SELECT RAISE(ABORT, 'Immutable notification'); END;
CREATE TRIGGER schedule_acks_no_update BEFORE UPDATE ON schedule_notification_acks BEGIN SELECT RAISE(ABORT, 'Immutable notification acknowledgment'); END;
CREATE TRIGGER schedule_acks_no_delete BEFORE DELETE ON schedule_notification_acks BEGIN SELECT RAISE(ABORT, 'Immutable notification acknowledgment'); END;
