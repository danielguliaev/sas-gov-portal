ALTER TABLE senate_bill_meta ADD COLUMN effective_rule TEXT NOT NULL DEFAULT 'standard';
UPDATE senate_bill_meta SET effective_rule='specified_date' WHERE effective_at IS NOT NULL;
ALTER TABLE senate_committee_agendas ADD COLUMN starts_at TEXT;

-- Election questions can wait for an agenda and share a session with other items.
CREATE TABLE senate_elections_new (
 id TEXT PRIMARY KEY,session_id TEXT REFERENCES legislative_sessions(id),
 bill_id TEXT NOT NULL UNIQUE REFERENCES bills(id),vote_id TEXT UNIQUE REFERENCES votes(id),
 parent_election_id TEXT REFERENCES senate_elections_new(id),candidates_json TEXT NOT NULL DEFAULT '[]',
 rule TEXT,threshold INTEGER,winner_id TEXT,creator_id TEXT NOT NULL,created_at TEXT NOT NULL
);
INSERT INTO senate_elections_new SELECT * FROM senate_elections;
DROP TABLE senate_elections;
ALTER TABLE senate_elections_new RENAME TO senate_elections;
INSERT OR IGNORE INTO senate_session_items
 (session_id,bill_id,vote_id,position,version,rule,threshold,category_json)
 SELECT session_id,bill_id,vote_id,0,1,COALESCE(rule,'simple_majority'),threshold,'{}'
 FROM senate_elections WHERE session_id IS NOT NULL;
UPDATE senate_settings SET setting_value='"simple_majority"' WHERE setting_key='pro_tempore_rule' AND setting_value='null';
