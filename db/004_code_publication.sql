-- v4: optional publication provenance for a code; never infer official status.
ALTER TABLE codes ADD COLUMN publication_issuer TEXT;
ALTER TABLE codes ADD COLUMN publication_reference TEXT;
ALTER TABLE codes ADD COLUMN published_at TEXT;
