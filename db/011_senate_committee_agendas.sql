CREATE TABLE IF NOT EXISTS senate_committee_agendas (
 id TEXT PRIMARY KEY,committee_id TEXT NOT NULL REFERENCES senate_committees(id),
 title TEXT NOT NULL,created_by TEXT NOT NULL,published_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS senate_committee_agenda_items (
 agenda_id TEXT NOT NULL REFERENCES senate_committee_agendas(id),
 review_id TEXT NOT NULL UNIQUE REFERENCES senate_reviews(id),position INTEGER NOT NULL,
 snapshot_json TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(agenda_id,review_id)
);
