ALTER TABLE senate_bill_meta ADD COLUMN document_fields_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE senate_session_details ADD COLUMN venue TEXT NOT NULL DEFAULT '';
ALTER TABLE senate_session_details ADD COLUMN schedule_status TEXT NOT NULL DEFAULT 'scheduled';
ALTER TABLE senate_session_details ADD COLUMN schedule_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE senate_session_details ADD COLUMN voting_round INTEGER NOT NULL DEFAULT 1;
CREATE TABLE senate_session_discussions (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES legislative_sessions(id),
 position INTEGER NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT ''
);
CREATE TABLE senate_voting_rounds (
 session_id TEXT NOT NULL REFERENCES legislative_sessions(id), round INTEGER NOT NULL,
 archived_at TEXT NOT NULL, reason TEXT NOT NULL, snapshot_json TEXT NOT NULL,
 PRIMARY KEY(session_id,round)
);
ALTER TABLE senate_committee_agendas ADD COLUMN venue TEXT NOT NULL DEFAULT '';
ALTER TABLE senate_committee_agendas ADD COLUMN schedule_status TEXT NOT NULL DEFAULT 'scheduled';
ALTER TABLE senate_committee_agendas ADD COLUMN schedule_reason TEXT NOT NULL DEFAULT '';
CREATE TABLE senate_committee_discussions (
 id TEXT PRIMARY KEY, agenda_id TEXT NOT NULL REFERENCES senate_committee_agendas(id),
 position INTEGER NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT ''
);
CREATE TABLE code_id_aliases (old_id TEXT PRIMARY KEY, code_id TEXT NOT NULL REFERENCES codes(id));
ALTER TABLE senate_committee_agenda_items RENAME TO senate_committee_agenda_items_old;
CREATE TABLE senate_committee_agenda_items (
 agenda_id TEXT NOT NULL REFERENCES senate_committee_agendas(id),
 review_id TEXT NOT NULL REFERENCES senate_reviews(id), position INTEGER NOT NULL,
 snapshot_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(agenda_id,review_id)
);
INSERT INTO senate_committee_agenda_items SELECT * FROM senate_committee_agenda_items_old;
DROP TABLE senate_committee_agenda_items_old;
