CREATE TABLE IF NOT EXISTS senate_settings (
  setting_key TEXT PRIMARY KEY,
  setting_value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO senate_settings(setting_key,setting_value,updated_at)
VALUES('committee_review_days','10',datetime('now'));
CREATE TABLE IF NOT EXISTS senate_committees (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
INSERT OR IGNORE INTO senate_committees(id,name,description,created_at)
VALUES('rules','Rules Committee','Первичное рассмотрение всех поданных проектов.',datetime('now'));
