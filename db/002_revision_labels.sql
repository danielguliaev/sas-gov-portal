-- v2: retain the display names of legal classification dictionaries at publication time.
-- Only applied once by app.core.init_database; existing revision rows retain an empty snapshot.
ALTER TABLE article_revisions ADD COLUMN display_snapshot TEXT NOT NULL DEFAULT '{}';
