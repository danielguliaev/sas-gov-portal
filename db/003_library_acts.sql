-- v3: explicit inclusion of standalone normative acts in the legal library.
-- Ordinary governmental news and draft publications are NOT automatically legislation.
CREATE TABLE IF NOT EXISTS library_acts (
  publication_id TEXT PRIMARY KEY REFERENCES portal_publications(id),
  classified_by TEXT NOT NULL,
  classified_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_library_acts_classified ON library_acts(classified_at,publication_id);
