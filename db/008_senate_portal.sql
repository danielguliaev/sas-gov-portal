CREATE TABLE IF NOT EXISTS senate_user_flags (
 user_id TEXT NOT NULL, flag TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(user_id,flag)
);
CREATE TABLE IF NOT EXISTS senate_profiles (
 user_id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', capitol_office TEXT NOT NULL DEFAULT '',
 district_office TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS senate_categories (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 committee_rule TEXT NOT NULL DEFAULT 'predominantly_for', committee_threshold INTEGER,
 plenary_rule TEXT NOT NULL DEFAULT 'simple_majority', plenary_threshold INTEGER
);
INSERT OR IGNORE INTO senate_categories(id,name) VALUES('general','Общие вопросы');
CREATE TABLE IF NOT EXISTS senate_document_types (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 template TEXT NOT NULL DEFAULT '', required_fields_json TEXT NOT NULL DEFAULT '[]'
);
INSERT OR IGNORE INTO senate_document_types(id,name) VALUES('senate_bill','Законопроект Сената');
INSERT OR IGNORE INTO senate_document_types(id,name) VALUES('resolution_simple','Простая резолюция Сената');
INSERT OR IGNORE INTO senate_document_types(id,name) VALUES('resolution_joint','Совместная резолюция');
INSERT OR IGNORE INTO senate_document_types(id,name) VALUES('resolution_concurrent','Параллельная резолюция');
INSERT OR IGNORE INTO senate_document_types(id,name) VALUES('constitutional_amendment','Конституционная поправка');
CREATE TABLE IF NOT EXISTS senate_bill_meta (
 bill_id TEXT PRIMARY KEY REFERENCES bills(id), number TEXT NOT NULL UNIQUE,
 document_type TEXT NOT NULL, category_id TEXT NOT NULL, effective_at TEXT,
 version INTEGER NOT NULL DEFAULT 1, changes_json TEXT NOT NULL DEFAULT '[]', submitted_at TEXT,
 current_committee_id TEXT REFERENCES senate_committees(id)
);
CREATE TABLE IF NOT EXISTS senate_revision_meta (
 revision_id TEXT PRIMARY KEY REFERENCES bill_revisions(id), metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS senate_coauthors (
 bill_id TEXT NOT NULL REFERENCES bills(id),user_id TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', invited_at TEXT NOT NULL, responded_at TEXT,
 PRIMARY KEY(bill_id,user_id)
);
CREATE TABLE IF NOT EXISTS senate_reviews (
 id TEXT PRIMARY KEY, bill_id TEXT NOT NULL REFERENCES bills(id), committee_id TEXT NOT NULL REFERENCES senate_committees(id),
 cycle INTEGER NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'awaiting_report',
 assigned_at TEXT NOT NULL, due_at TEXT NOT NULL, report_text TEXT, report_by TEXT, recommendation TEXT,
 decision TEXT, chair_id TEXT, comment TEXT, transcript TEXT, tally_json TEXT NOT NULL DEFAULT '{}',
 report_at TEXT, consideration_due_at TEXT,
 UNIQUE(bill_id,committee_id,cycle)
);
CREATE TABLE IF NOT EXISTS senate_news (
 id TEXT PRIMARY KEY,title TEXT NOT NULL,body TEXT NOT NULL,author_id TEXT NOT NULL,published_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS senate_audit (
 id TEXT PRIMARY KEY,user_id TEXT NOT NULL,action TEXT NOT NULL,object_type TEXT NOT NULL,
 object_id TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,created_at TEXT NOT NULL
);
INSERT OR IGNORE INTO senate_settings VALUES('quorum','1',datetime('now'));
INSERT OR IGNORE INTO senate_settings VALUES('min_notice_hours','0',datetime('now'));
INSERT OR IGNORE INTO senate_settings VALUES('disputed_policies','false',datetime('now'));
INSERT OR IGNORE INTO senate_settings VALUES('news_admin_only','true',datetime('now'));
INSERT OR IGNORE INTO senate_settings VALUES('consideration_days','10',datetime('now'));
