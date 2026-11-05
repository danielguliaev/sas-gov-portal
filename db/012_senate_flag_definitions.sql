CREATE TABLE IF NOT EXISTS senate_flag_definitions (
 id TEXT PRIMARY KEY,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,
 permissions_json TEXT NOT NULL DEFAULT '[]'
);
