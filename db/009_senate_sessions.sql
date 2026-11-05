CREATE TABLE IF NOT EXISTS senate_session_details (
 session_id TEXT PRIMARY KEY REFERENCES legislative_sessions(id),
 starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft', quorum INTEGER NOT NULL,
 creator_id TEXT NOT NULL, published_at TEXT,
 settings_json TEXT NOT NULL DEFAULT '{}', transcript TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS senate_session_items (
 session_id TEXT NOT NULL REFERENCES legislative_sessions(id),
 bill_id TEXT NOT NULL REFERENCES bills(id),
 vote_id TEXT NOT NULL UNIQUE REFERENCES votes(id),
 position INTEGER NOT NULL, version INTEGER NOT NULL,
 rule TEXT NOT NULL, threshold INTEGER,
 category_json TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(session_id,bill_id), UNIQUE(session_id,position)
);
CREATE TABLE IF NOT EXISTS senate_session_electorate (
 session_id TEXT NOT NULL REFERENCES legislative_sessions(id),
 user_id TEXT NOT NULL, is_lieutenant INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(session_id,user_id)
);
CREATE TABLE IF NOT EXISTS senate_session_transcripts (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES legislative_sessions(id),
 text TEXT NOT NULL, author_id TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS senate_session_end ON senate_session_details(status,ends_at);
