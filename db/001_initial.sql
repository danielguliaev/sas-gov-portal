PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS jurisdictions(id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS portals(id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS codes(
 id TEXT PRIMARY KEY, jurisdiction_id TEXT NOT NULL REFERENCES jurisdictions(id),
 name TEXT NOT NULL, abbreviation TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
 is_demo INTEGER NOT NULL DEFAULT 1, UNIQUE(jurisdiction_id,name));
CREATE TABLE IF NOT EXISTS hierarchy_types(
 id TEXT PRIMARY KEY, code_id TEXT NOT NULL REFERENCES codes(id), name TEXT NOT NULL,
 level_order INTEGER NOT NULL DEFAULT 0, allowed_parents TEXT NOT NULL DEFAULT '[]',
 number_format TEXT NOT NULL DEFAULT 'mixed', active INTEGER NOT NULL DEFAULT 1);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_type ON hierarchy_types(code_id, name) WHERE active=1;
CREATE TABLE IF NOT EXISTS structure_nodes(
 id TEXT PRIMARY KEY, code_id TEXT NOT NULL REFERENCES codes(id),
 type_id TEXT NOT NULL REFERENCES hierarchy_types(id), parent_id TEXT REFERENCES structure_nodes(id),
 parent_key TEXT NOT NULL, number TEXT NOT NULL, number_key TEXT NOT NULL, sort_key TEXT NOT NULL,
 title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
 manual_order INTEGER, archive_batch_id TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_node ON structure_nodes(code_id,type_id,parent_key,number_key) WHERE status='active';
CREATE INDEX IF NOT EXISTS ix_node_parent ON structure_nodes(code_id,parent_key,status,sort_key);
CREATE TABLE IF NOT EXISTS sort_modes(parent_key TEXT PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'auto');
CREATE TABLE IF NOT EXISTS articles(
 id TEXT PRIMARY KEY, code_id TEXT NOT NULL REFERENCES codes(id), parent_id TEXT REFERENCES structure_nodes(id),
 number TEXT NOT NULL, number_key TEXT NOT NULL, sort_key TEXT NOT NULL,
 title TEXT NOT NULL, text_rich TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
 is_demo INTEGER NOT NULL DEFAULT 1, revision_id TEXT,
 categories_json TEXT NOT NULL DEFAULT '[]', criminal_class TEXT,
 custom_values TEXT NOT NULL DEFAULT '{}',
 prison_min_months INTEGER, prison_max_months INTEGER, fine_min_usd INTEGER, fine_max_usd INTEGER,
 archive_batch_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 CHECK(prison_min_months IS NULL OR (typeof(prison_min_months)='integer' AND prison_min_months>=0)),
 CHECK(prison_max_months IS NULL OR (typeof(prison_max_months)='integer' AND prison_max_months>=0)),
 CHECK(fine_min_usd IS NULL OR (typeof(fine_min_usd)='integer' AND fine_min_usd>=0)),
 CHECK(fine_max_usd IS NULL OR (typeof(fine_max_usd)='integer' AND fine_max_usd>=0)),
 CHECK(prison_min_months IS NULL OR prison_max_months IS NULL OR prison_min_months<=prison_max_months),
 CHECK(fine_min_usd IS NULL OR fine_max_usd IS NULL OR fine_min_usd<=fine_max_usd));
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_article ON articles(code_id,number_key) WHERE status='active';
CREATE INDEX IF NOT EXISTS ix_article_position ON articles(code_id,parent_id,status,sort_key,id);
CREATE TABLE IF NOT EXISTS article_revisions(
 id TEXT PRIMARY KEY, article_id TEXT NOT NULL REFERENCES articles(id), sequence INTEGER NOT NULL,
 number TEXT NOT NULL, title TEXT NOT NULL, text_rich TEXT NOT NULL,
 categories_json TEXT NOT NULL, criminal_class TEXT, custom_values TEXT NOT NULL,
 sanctions_json TEXT NOT NULL, relations_json TEXT NOT NULL,
 source_json TEXT NOT NULL DEFAULT '{}', change_reason TEXT NOT NULL DEFAULT '',
 published_by TEXT NOT NULL, published_at TEXT NOT NULL,
 UNIQUE(article_id,sequence));
CREATE TABLE IF NOT EXISTS categories(
 id TEXT PRIMARY KEY, code_id TEXT REFERENCES codes(id), name TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS category_fields(
 id TEXT PRIMARY KEY, category_id TEXT NOT NULL REFERENCES categories(id),
 name TEXT NOT NULL, field_type TEXT NOT NULL, selection_mode TEXT,
 used_ever INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS field_options(
 id TEXT PRIMARY KEY, field_id TEXT NOT NULL REFERENCES category_fields(id),
 name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS relation_types(
 id TEXT PRIMARY KEY, name TEXT NOT NULL,
 source_category TEXT REFERENCES categories(id), target_category TEXT REFERENCES categories(id),
 exclusive_target INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS article_relations(
 id TEXT PRIMARY KEY, type_id TEXT NOT NULL REFERENCES relation_types(id),
 source_id TEXT NOT NULL REFERENCES articles(id), target_id TEXT NOT NULL REFERENCES articles(id),
 exclusive_target INTEGER NOT NULL DEFAULT 0,
 active INTEGER NOT NULL DEFAULT 1, disabled_reason TEXT,
 created_at TEXT NOT NULL, disabled_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_relation ON article_relations(type_id,source_id,target_id) WHERE active=1;
CREATE UNIQUE INDEX IF NOT EXISTS uq_unique_punishment_target ON article_relations(target_id) WHERE active=1 AND exclusive_target=1;
CREATE INDEX IF NOT EXISTS ix_relation_source ON article_relations(source_id,active);
CREATE INDEX IF NOT EXISTS ix_relation_target ON article_relations(target_id,active);
CREATE TABLE IF NOT EXISTS edit_locks(
 article_id TEXT PRIMARY KEY REFERENCES articles(id), user_id TEXT NOT NULL,
 editor_session_id TEXT NOT NULL, token_hash TEXT NOT NULL,
 heartbeat_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS archive_batches(
 id TEXT PRIMARY KEY, root_node_id TEXT, article_ids TEXT NOT NULL,
 node_ids TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS favorites(
 user_id TEXT NOT NULL, article_id TEXT NOT NULL REFERENCES articles(id),
 PRIMARY KEY(user_id,article_id));
CREATE TABLE IF NOT EXISTS annotations(
 id TEXT PRIMARY KEY, author_id TEXT NOT NULL,
 article_id TEXT NOT NULL REFERENCES articles(id),
 revision_id TEXT NOT NULL REFERENCES article_revisions(id),
 start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
 quote TEXT NOT NULL, note_text TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS user_preferences(user_id TEXT PRIMARY KEY, font_scale INTEGER NOT NULL DEFAULT 100);
CREATE TABLE IF NOT EXISTS notifications(
 id TEXT PRIMARY KEY, recipient_id TEXT NOT NULL, portal_id TEXT NOT NULL REFERENCES portals(id),
 event_key TEXT NOT NULL, title TEXT NOT NULL, object_id TEXT NOT NULL,
 created_at TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0,
 UNIQUE(recipient_id,portal_id,event_key));
CREATE TABLE IF NOT EXISTS audit_events(
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, action TEXT NOT NULL, object_type TEXT NOT NULL,
 object_id TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
 created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS idempotency(
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, route_key TEXT NOT NULL, request_hash TEXT NOT NULL,
 result_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS portal_publications(
 id TEXT PRIMARY KEY, portal_id TEXT NOT NULL REFERENCES portals(id),
 jurisdiction_id TEXT NOT NULL REFERENCES jurisdictions(id),
 title TEXT NOT NULL, document_type TEXT NOT NULL, issuer TEXT NOT NULL,
 text_rich TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
 issued_at TEXT NOT NULL, published_at TEXT,
 related_article_id TEXT REFERENCES articles(id), is_precedent INTEGER NOT NULL DEFAULT 0,
 external_reference TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS editorial_reviews(
 id TEXT PRIMARY KEY, publication_id TEXT NOT NULL REFERENCES portal_publications(id),
 article_id TEXT NOT NULL REFERENCES articles(id), base_revision_id TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', reviewed_by TEXT, reviewed_at TEXT);
CREATE TABLE IF NOT EXISTS bills(
 id TEXT PRIMARY KEY, title TEXT NOT NULL, author_id TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'project', text_rich TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bill_revisions(
 id TEXT PRIMARY KEY, bill_id TEXT NOT NULL REFERENCES bills(id), version INTEGER NOT NULL,
 text_rich TEXT NOT NULL, author_id TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(bill_id,version));
CREATE TABLE IF NOT EXISTS bill_events(
 id TEXT PRIMARY KEY, bill_id TEXT NOT NULL REFERENCES bills(id),
 event_type TEXT NOT NULL, body TEXT NOT NULL, actor_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discussions(
 id TEXT PRIMARY KEY, bill_id TEXT NOT NULL REFERENCES bills(id),
 author_id TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS legislative_sessions(id TEXT PRIMARY KEY, title TEXT NOT NULL, held_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS votes(
 id TEXT PRIMARY KEY, bill_id TEXT NOT NULL REFERENCES bills(id),
 session_id TEXT REFERENCES legislative_sessions(id), result TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vote_choices(
 vote_id TEXT NOT NULL REFERENCES votes(id), voter_id TEXT NOT NULL,
 choice TEXT NOT NULL, PRIMARY KEY(vote_id,voter_id));
CREATE TABLE IF NOT EXISTS adopted_acts(
 id TEXT PRIMARY KEY, bill_id TEXT NOT NULL UNIQUE REFERENCES bills(id),
 publication_id TEXT NOT NULL REFERENCES portal_publications(id));
