CREATE TABLE portal_test_accounts (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 roles_json TEXT NOT NULL DEFAULT '["user"]', created_at TEXT NOT NULL
);
CREATE TABLE portal_test_characters (
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES portal_test_accounts(id),
 created_at TEXT NOT NULL
);
