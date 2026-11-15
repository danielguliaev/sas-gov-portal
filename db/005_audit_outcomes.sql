-- v5: immutable source/outcome metadata for administrative audit history.
ALTER TABLE audit_events ADD COLUMN source TEXT NOT NULL DEFAULT 'api';
ALTER TABLE audit_events ADD COLUMN result TEXT NOT NULL DEFAULT 'success';
ALTER TABLE audit_events ADD COLUMN http_status INTEGER NOT NULL DEFAULT 200;
CREATE INDEX IF NOT EXISTS ix_audit_result ON audit_events(result,created_at);
