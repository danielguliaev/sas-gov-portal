CREATE TABLE IF NOT EXISTS senate_elections (
 id TEXT PRIMARY KEY,session_id TEXT NOT NULL UNIQUE REFERENCES legislative_sessions(id),
 bill_id TEXT NOT NULL UNIQUE REFERENCES bills(id),vote_id TEXT NOT NULL UNIQUE REFERENCES votes(id),
 parent_election_id TEXT REFERENCES senate_elections(id),candidates_json TEXT NOT NULL DEFAULT '[]',
 rule TEXT,threshold INTEGER,winner_id TEXT,creator_id TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS senate_committee_proposals (
 bill_id TEXT PRIMARY KEY REFERENCES bills(id),committee_id TEXT NOT NULL,
 name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',chair_id TEXT NOT NULL,
 members_json TEXT NOT NULL,applied_at TEXT
);
INSERT OR IGNORE INTO senate_settings VALUES('pro_tempore_rule','null',datetime('now'));
INSERT OR IGNORE INTO senate_settings VALUES('pro_tempore_threshold','null',datetime('now'));
