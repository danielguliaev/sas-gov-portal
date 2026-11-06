"""Database, authentication and shared legal invariants. Times use server UTC."""
import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import bleach
import jwt
from jwt import PyJWKClient
from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse


DB_PATH = os.getenv("DATABASE_PATH", "./sas_portal.db")
AUTH_MODE = os.getenv("AUTH_MODE", "demo")
APP_ENV = os.getenv("APP_ENV", "development")
if APP_ENV == "production" and AUTH_MODE != "oidc":
    raise RuntimeError("В production необходим AUTH_MODE=oidc")
if AUTH_MODE == "oidc" and not all(os.getenv(k) for k in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL")):
    raise RuntimeError("Не настроены OIDC_ISSUER, OIDC_AUDIENCE, OIDC_JWKS_URL")
JWKS = PyJWKClient(os.getenv("OIDC_JWKS_URL"), cache_keys=True) if AUTH_MODE == "oidc" else None

DEMO_ROLES = {
    "user": {"user"},
    "lawyer": {"user", "lawyer"},
    "superadmin": {"user", "lawyer", "superadmin"},
    "editor": {"user", "library_editor"},
    "publisher": {"user", "portal_publisher"},
    "legislator": {"user", "legislator"},
}
CATEGORIES = {
    "accusation": "Обвинение", "punishment_measure": "Мера наказания",
    "aggravating": "Отягчающие обстоятельства", "mitigating": "Смягчающие обстоятельства",
    "auxiliary": "Вспомогательная мера", "definition": "Определение",
}
SANCTION_FIELDS = ("prison_min_months", "prison_max_months", "fine_min_usd", "fine_max_usd")
HTML_TAGS = ["p", "br", "h3", "h4", "ul", "ol", "li", "strong", "em", "b", "i", "u", "blockquote", "a"]
HTML_ATTRS = {"a": ["href", "title"]}


def ts():
    return datetime.now(timezone.utc).isoformat()


def future(minutes=5):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def uid():
    return uuid4().hex


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads(value, default=None):
    return json.loads(value) if value else default


def clean(value):
    if not isinstance(value, str):
        fail(422, "INVALID_TEXT", "Текст должен быть строкой.")
    return bleach.clean(value, tags=HTML_TAGS, attributes=HTML_ATTRS,
                        protocols=["https", "http"], strip=True)


def plain(value, limit=1000):
    if not isinstance(value, str) or len(value) > limit:
        fail(422, "INVALID_TEXT", "Некорректная длина текста.")
    return value.strip()


def normalize_number(number):
    if not isinstance(number, str) or not number.strip() or len(number) > 100:
        fail(422, "INVALID_NUMBER", "Требуется юридический номер длиной до 100 символов.")
    return re.sub(r"[\s.\-–_]+", "", unicodedata.normalize("NFKC", number).upper())


def number_sort_key(number, number_format="mixed"):
    normalized = normalize_number(number) if number_format == "roman" else unicodedata.normalize("NFKC", number).upper()
    if number_format == "roman":
        values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
        total, previous = 0, 0
        for char in reversed(normalized):
            amount = values[char]
            total += -amount if amount < previous else amount
            previous = max(previous, amount)
        return "0" + str(total).zfill(20)
    parts = re.findall(r"\d+|[A-ZА-Я]+|[^A-ZА-Я\d\s.\-–_]+", normalized)
    return "".join("0" + p.zfill(20) if p.isdecimal() else "1" + p + "\x01" for p in parts)


def fail(status, code, message, details=None):
    raise HTTPException(status, {"code": code, "message": message, "details": details or {}})


async def error_handler(_request: Request, exc: HTTPException):
    body = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "message": str(exc.detail)}
    return JSONResponse({"error": body}, status_code=exc.status_code)


def conn():
    c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=30000")
    return c


@contextmanager
def transaction():
    c = conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.commit()
    except sqlite3.IntegrityError as error:
        c.rollback()
        fail(409, "INTEGRITY_CONFLICT", "Конфликт уникальности или ссылочной целостности.", {"cause": str(error)})
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


@contextmanager
def read_db():
    c = conn()
    try:
        yield c
    finally:
        c.close()


def one(c, sql, args=(), missing="Объект не найден."):
    row = c.execute(sql, args).fetchone()
    if row is None:
        fail(404, "NOT_FOUND", missing)
    return dict(row)


def maybe(c, sql, args=()):
    row = c.execute(sql, args).fetchone()
    return dict(row) if row else None


def allrows(c, sql, args=()):
    return [dict(r) for r in c.execute(sql, args)]


def audit(c, current, action, obj_type, obj_id, before=None, after=None):
    # A successful event is committed atomically with the underlying legal change.
    c.execute("""INSERT INTO audit_events
              (id,user_id,action,object_type,object_id,before_json,after_json,
               created_at,source,result,http_status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (uid(), current["id"], action, obj_type, obj_id, dumps(before or {}),
               dumps(after or {}), ts(), current.get("audit_source","api"),"success",200))


def notify(c, recipient_id, portal_id, event_key, title, object_id):
    c.execute("""INSERT OR IGNORE INTO notifications
              (id,recipient_id,portal_id,event_key,title,object_id,created_at)
              VALUES(?,?,?,?,?,?,?)""",
              (uid(), recipient_id, portal_id, event_key, title, object_id, ts()))


def actor(request: Request, authorization: str | None = Header(None),
          x_demo_user: str | None = Header(None)):
    # Keep request-derived audit context outside client-editable payloads.
    source = request.method + " " + request.url.path[:240]
    if APP_ENV != "production" and AUTH_MODE == "demo":
        if x_demo_user and x_demo_user.startswith('test-'):
            account_id, _, character_id = x_demo_user.partition('@')
            with read_db() as c:
                account = maybe(c, 'SELECT * FROM portal_test_accounts WHERE id=?', (account_id,))
                if not account or not account['active']:
                    fail(401, 'TEST_ACCOUNT_DISABLED', 'Тестовая учётная запись недоступна.')
                character = maybe(c, 'SELECT id FROM portal_test_characters WHERE account_id=?' +
                                  (' AND id=?' if character_id else '') + ' ORDER BY id LIMIT 1',
                                  (account_id, character_id) if character_id else (account_id,))
                if not character:
                    fail(401, 'CHARACTER_NOT_OWNED', 'Персонаж не принадлежит выбранной учётной записи.')
            identity = {'id': character['id'], 'account_id': account_id,
                        'roles': set(loads(account['roles_json'])) | {'user'},
                        'audit_source': source + ' account=' + account_id}
            request.state.audit_identity = identity
            return identity
        if x_demo_user in DEMO_ROLES:
            identity = {"id": "demo-" + x_demo_user, "roles": DEMO_ROLES[x_demo_user],
                        "audit_source": source}
            request.state.audit_identity = identity
            return identity
        fail(401, "AUTH_REQUIRED", "Для разработки выберите тестовую роль.")
    if AUTH_MODE != "oidc":
        fail(401, "AUTH_DISABLED", "Аутентификация не настроена.")
    if not authorization or not authorization.startswith("Bearer "):
        fail(401, "AUTH_REQUIRED", "Требуется вход.")
    try:
        token = authorization.removeprefix("Bearer ").strip()
        key = JWKS.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=os.getenv("OIDC_AUDIENCE"),
                            issuer=os.getenv("OIDC_ISSUER"))
        roles = claims.get(os.getenv("OIDC_ROLES_CLAIM", "roles"), [])
        if not claims.get("sub") or not isinstance(roles, list) or not all(isinstance(x, str) for x in roles):
            raise ValueError("Некорректные claims")
        identity = {"id": claims["sub"], "roles": set(roles) | {"user"},
                    "audit_source": source}
        request.state.audit_identity = identity
        return identity
    except Exception:
        fail(401, "INVALID_TOKEN", "Неверный токен доступа.")


def require(current, *roles):
    if not current["roles"].intersection(roles):
        fail(403, "FORBIDDEN", "Недостаточно прав.")


def sanctions_of(article):
    return {key: article[key] for key in SANCTION_FIELDS}


def validate_article(c, data, code_id, old=None, *, mark_used=True):
    categories = data.get("categories", loads(old["categories_json"], []) if old else [])
    if not isinstance(categories, list) or any(not isinstance(x, str) for x in categories):
        fail(422, "INVALID_CATEGORIES", "Некорректные категории.")
    categories = list(dict.fromkeys(categories))
    if categories:
        allowed = {x["id"] for x in allrows(c,
            "SELECT id FROM categories WHERE active=1 AND (code_id IS NULL OR code_id=?)", (code_id,))}
        if not set(categories) <= allowed:
            fail(422, "INVALID_CATEGORY", "Категория недоступна для кодекса.")
    sanctions = data.get("sanctions", sanctions_of(old) if old else dict.fromkeys(SANCTION_FIELDS))
    if not isinstance(sanctions, dict) or set(sanctions) - set(SANCTION_FIELDS):
        fail(422, "INVALID_SANCTIONS", "Недопустимые поля санкций.")
    sanctions = {k: sanctions.get(k) for k in SANCTION_FIELDS}
    for value in sanctions.values():
        if value is not None and (type(value) is not int or value < 0):
            fail(422, "INVALID_SANCTIONS", "Санкции: целое неотрицательное число либо null.")
    for a, b in (("prison_min_months", "prison_max_months"), ("fine_min_usd", "fine_max_usd")):
        if sanctions[a] is not None and sanctions[b] is not None and sanctions[a] > sanctions[b]:
            fail(422, "INVALID_SANCTIONS", "Минимум больше максимума.")
    if any(v is not None for v in sanctions.values()):
        categories = list(dict.fromkeys(categories + ["punishment_measure"]))
    else:
        categories = [x for x in categories if x != "punishment_measure"]
    criminal_class = data.get("criminal_class", old["criminal_class"] if old else None)
    if "accusation" in categories:
        if criminal_class not in ("felony", "misdemeanor", "infraction"):
            fail(422, "CRIMINAL_CLASS_REQUIRED", "Для обвинения нужен уголовно-правовой класс.")
    else:
        criminal_class = None
    old_custom = loads(old["custom_values"], {}) if old else {}
    custom = data.get("custom_values", old_custom)
    if not isinstance(custom, dict):
        fail(422, "INVALID_CUSTOM_VALUES", "Некорректные пользовательские поля.")
    custom = dict(custom)
    fields = {f["id"]: f for f in allrows(c,
        "SELECT * FROM category_fields WHERE active=1") if f["category_id"] in categories}
    removed = set(custom) - set(fields)
    if removed - set(old_custom):
        fail(422, "INVALID_CUSTOM_FIELD", "Поле не принадлежит назначенной категории.")
    # Removing a category removes only its current values; immutable revisions retain snapshots.
    for field_id in removed:
        custom.pop(field_id, None)
    for field_id, value in custom.items():
        f = fields[field_id]
        if value is None:
            continue
        if f["field_type"] == "number" and type(value) not in (int, float):
            fail(422, "INVALID_CUSTOM_FIELD", "Ожидалось число.")
        if f["field_type"] == "boolean" and type(value) is not bool:
            fail(422, "INVALID_CUSTOM_FIELD", "Ожидалось логическое значение.")
        if f["field_type"] == "range" and (
            not isinstance(value, list) or len(value) != 2
            or any(type(x) not in (int, float) for x in value) or value[0] > value[1]
        ):
            fail(422, "INVALID_CUSTOM_FIELD", "Некорректный диапазон.")
        if f["field_type"] in ("string", "rich_text") and not isinstance(value, str):
            fail(422, "INVALID_CUSTOM_FIELD", "Ожидался текст.")
        if f["field_type"] == "rich_text":
            custom[field_id] = clean(value)
        if f["field_type"] == "choice":
            options = value if f["selection_mode"] == "multiple" else [value]
            if not isinstance(options, list) or not options or any(not isinstance(x, str) for x in options):
                fail(422, "INVALID_CUSTOM_FIELD", "Некорректный выбор.")
            known = {x["id"] for x in allrows(c, "SELECT id FROM field_options WHERE field_id=?",
                                                 (field_id,))}
            if not set(options) <= known:
                fail(422, "INVALID_CUSTOM_FIELD", "Неизвестный вариант.")
        if mark_used:
            c.execute("UPDATE category_fields SET used_ever=1 WHERE id=?", (field_id,))
    return categories, criminal_class, custom, sanctions


def relation_snapshot(c, article_id):
    return allrows(c, """SELECT id,type_id,source_id,target_id FROM article_relations
                         WHERE active=1 AND (source_id=? OR target_id=?) ORDER BY id""",
                   (article_id, article_id))


def display_snapshot(c, article):
    """Capture display names now so dictionary renames cannot distort old revisions."""
    category_ids = loads(article["categories_json"], [])
    values = loads(article["custom_values"], {})
    cats = {}
    for category_id in category_ids:
        row = maybe(c, "SELECT name FROM categories WHERE id=?", (category_id,))
        cats[category_id] = row["name"] if row else category_id
    fields = {}
    for field_id, value in values.items():
        f = maybe(c, "SELECT id,name,category_id,field_type FROM category_fields WHERE id=?",
                  (field_id,))
        if not f:
            fields[field_id] = {"name": field_id, "value": value}
            continue
        resolved = value
        if f["field_type"] == "choice":
            def selected(option_id):
                opt = maybe(c, "SELECT name FROM field_options WHERE id=?", (option_id,))
                return {"id": option_id, "name": opt["name"] if opt else option_id}
            resolved = [selected(x) for x in value] if isinstance(value, list) else selected(value)
        fields[field_id] = {"name": f["name"], "category_id": f["category_id"],
                            "field_type": f["field_type"], "value": resolved}
    return {"categories": cats, "fields": fields}


def append_revision(c, article_id, user_id, reason="", provenance=None):
    article = one(c, "SELECT * FROM articles WHERE id=?", (article_id,))
    seq = c.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM article_revisions WHERE article_id=?",
                    (article_id,)).fetchone()[0]
    revision_id = uid()
    c.execute("""INSERT INTO article_revisions(
      id,article_id,sequence,number,title,text_rich,categories_json,criminal_class,
      custom_values,sanctions_json,relations_json,source_json,change_reason,published_by,published_at,
      display_snapshot)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (revision_id, article_id, seq, article["number"], article["title"], article["text_rich"],
       article["categories_json"], article["criminal_class"], article["custom_values"],
       dumps(sanctions_of(article)), dumps(relation_snapshot(c, article_id)),
       dumps(provenance or {}), plain(reason, 2000), user_id, ts(),
       dumps(display_snapshot(c, article))))
    c.execute("UPDATE articles SET revision_id=?,updated_at=? WHERE id=?",
              (revision_id, ts(), article_id))
    return revision_id


def validate_parent(c, code_id, parent_id, type_id=None):
    if parent_id is None:
        if type_id:
            typ = one(c, "SELECT * FROM hierarchy_types WHERE id=? AND code_id=? AND active=1",
                      (type_id, code_id))
            if "ROOT" not in loads(typ["allowed_parents"], []):
                fail(422, "HIERARCHY_RULE", "Тип нельзя размещать в корне.")
        return
    parent = one(c, "SELECT * FROM structure_nodes WHERE id=? AND code_id=? AND status='active'",
                 (parent_id, code_id))
    if type_id:
        typ = one(c, "SELECT * FROM hierarchy_types WHERE id=? AND code_id=? AND active=1",
                  (type_id, code_id))
        if parent["type_id"] not in loads(typ["allowed_parents"], []):
            fail(422, "HIERARCHY_RULE", "Недопустимое сочетание уровней.")


def validate_relations(c, article_id):
    for rel in allrows(c, "SELECT * FROM article_relations WHERE active=1 AND (source_id=? OR target_id=?)",
                       (article_id, article_id)):
        kind = one(c, "SELECT * FROM relation_types WHERE id=?", (rel["type_id"],))
        source = one(c, "SELECT * FROM articles WHERE id=?", (rel["source_id"],))
        target = one(c, "SELECT * FROM articles WHERE id=?", (rel["target_id"],))
        compatible = (source["status"] == "active" and target["status"] == "active" and
                      (not kind["source_category"] or kind["source_category"] in loads(source["categories_json"], [])) and
                      (not kind["target_category"] or kind["target_category"] in loads(target["categories_json"], [])))
        if not compatible:
            c.execute("UPDATE article_relations SET active=0,disabled_reason='incompatible',disabled_at=? WHERE id=?",
                      (ts(), rel["id"]))


def present_article(c, a, current):
    result = {key: a[key] for key in
              ("id", "code_id", "parent_id", "number", "title", "text_rich", "status",
               "is_demo", "revision_id")}
    result["permalink"] = "/articles/" + a["id"]
    rev = maybe(c, "SELECT source_json,published_at FROM article_revisions WHERE id=?", (a["revision_id"],))
    result["provenance"] = loads(rev["source_json"], {}) if rev else {}
    if current["roles"].intersection({"lawyer", "superadmin"}):
        result.update(categories=loads(a["categories_json"], []),
                      criminal_class=a["criminal_class"], sanctions=sanctions_of(a),
                      custom_values=loads(a["custom_values"], {}))
    return result


def idempotent(c, current, route, key, payload, action):
    if not key or len(key) > 120:
        fail(422, "IDEMPOTENCY_KEY_REQUIRED", "Укажите ключ идемпотентности.")
    identity = current["id"] + ":" + route + ":" + key
    digest = hashlib.sha256(dumps(payload).encode()).hexdigest()
    old = maybe(c, "SELECT * FROM idempotency WHERE id=?", (identity,))
    if old:
        if old["request_hash"] != digest:
            fail(409, "IDEMPOTENCY_MISMATCH", "Ключ уже использован с другими данными.")
        return loads(old["result_json"])
    result = action()
    c.execute("INSERT INTO idempotency VALUES(?,?,?,?,?,?)",
              (identity, current["id"], route, digest, dumps(result), ts()))
    return result


def init_database():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    c = conn()
    try:
        c.execute("PRAGMA journal_mode=WAL")
        base = Path(__file__).resolve().parent.parent / "db"
        c.executescript((base / "001_initial.sql").read_text())
        c.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES(1)")
        if not c.execute("SELECT 1 FROM schema_migrations WHERE version=7").fetchone():
            c.execute("BEGIN IMMEDIATE")
            try:
                migration = (base / "007_senate.sql").read_text(encoding="utf-8")
                for part in migration.split(";"):
                    statement = "\n".join(line for line in part.splitlines()
                                           if not line.lstrip().startswith("--")).strip()
                    if statement:
                        c.execute(statement)
                c.execute("INSERT INTO schema_migrations(version) VALUES(7)")
                c.commit()
            except Exception:
                c.rollback()
                raise
        # Upgrade existing local databases exactly once; the DDL and version marker
        # share a single transaction so a failed upgrade cannot partially apply.
        if not c.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone():
            c.execute("BEGIN IMMEDIATE")
            try:
                statement = next(line for line in
                    (base / "002_revision_labels.sql").read_text().splitlines()
                    if line.strip().upper().startswith("ALTER TABLE"))
                c.execute(statement.rstrip(";"))
                c.execute("INSERT INTO schema_migrations(version) VALUES(2)")
                c.commit()
            except Exception:
                c.rollback()
                raise
        if not c.execute("SELECT 1 FROM schema_migrations WHERE version=3").fetchone():
            c.execute("BEGIN IMMEDIATE")
            try:
                migration = (base / "003_library_acts.sql").read_text(encoding="utf-8")
                for part in migration.split(";"):
                    statement = "\n".join(line for line in part.splitlines()
                                           if not line.lstrip().startswith("--")).strip()
                    if statement:
                        c.execute(statement)
                c.execute("INSERT INTO schema_migrations(version) VALUES(3)")
                c.commit()
            except Exception:
                c.rollback()
                raise
        if not c.execute("SELECT 1 FROM schema_migrations WHERE version=4").fetchone():
            c.execute("BEGIN IMMEDIATE")
            try:
                for line in (base / "004_code_publication.sql").read_text(
                    encoding="utf-8").splitlines():
                    statement = line.strip()
                    if statement.upper().startswith("ALTER TABLE"):
                        c.execute(statement.rstrip(";"))
                c.execute("INSERT INTO schema_migrations(version) VALUES(4)")
                c.commit()
            except Exception:
                c.rollback()
                raise
        if not c.execute("SELECT 1 FROM schema_migrations WHERE version=5").fetchone():
            c.execute("BEGIN IMMEDIATE")
            try:
                migration = (base / "005_audit_outcomes.sql").read_text(encoding="utf-8")
                for part in migration.split(";"):
                    statement = "\n".join(line for line in part.splitlines()
                                           if not line.lstrip().startswith("--")).strip()
                    if statement:
                        c.execute(statement)
                c.execute("INSERT INTO schema_migrations(version) VALUES(5)")
                c.commit()
            except Exception:
                c.rollback()
                raise
        if not c.execute("SELECT 1 FROM schema_migrations WHERE version=6").fetchone():
            c.execute("BEGIN IMMEDIATE")
            try:
                # sqlite3.complete_statement handles multi-statement triggers,
                # unlike splitting the SQL file at every semicolon.
                pending = []
                for line in (base / "006_code_outline.sql").read_text(
                        encoding="utf-8").splitlines():
                    if line.lstrip().startswith("--"):
                        continue
                    pending.append(line)
                    statement = "\n".join(pending).strip()
                    if sqlite3.complete_statement(statement):
                        c.execute(statement)
                        pending = []
                if "\n".join(pending).strip():
                    raise ValueError("Незавершённая миграция 006")
                c.execute("INSERT INTO schema_migrations(version) VALUES(6)")
                c.commit()
            except Exception:
                c.rollback()
                raise
        for version, filename in ((8, "008_senate_portal.sql"), (9, "009_senate_sessions.sql"),
                                  (10, "010_senate_officers.sql"), (11, "011_senate_committee_agendas.sql"),
                                  (12, "012_senate_flag_definitions.sql"), (13, "013_test_accounts.sql"),
                                  (14, "014_senate_feedback.sql"), (15, "015_documents_and_agendas.sql")):
            if c.execute("SELECT 1 FROM schema_migrations WHERE version=?", (version,)).fetchone():
                continue
            c.execute("BEGIN IMMEDIATE")
            try:
                pending = []
                for line in (base / filename).read_text(encoding="utf-8-sig").splitlines():
                    if line.lstrip().startswith("--"):
                        continue
                    pending.append(line)
                    statement = "\n".join(pending).strip()
                    if sqlite3.complete_statement(statement):
                        c.execute(statement)
                        pending = []
                if "\n".join(pending).strip():
                    raise ValueError("Незавершённая миграция " + filename)
                c.execute("INSERT INTO schema_migrations(version) VALUES(?)", (version,))
                c.commit()
            except Exception:
                c.rollback()
                raise
        if not c.execute('SELECT 1 FROM schema_migrations WHERE version=16').fetchone():
            from app.code_identity import migrate_code_ids
            c.execute('BEGIN IMMEDIATE')
            try:
                migrate_code_ids(c)
                c.execute('INSERT INTO schema_migrations(version) VALUES(16)')
                c.commit()
            except Exception:
                c.rollback()
                raise
        c.commit()
    finally:
        c.close()


def purge_old_audit_events():
    """Delete audit events older than six calendar months (UTC).

    SQLite performs calendar-month subtraction rather than approximating six
    months as 180 days. Full legal revisions, archival history and editorial
    provenance are deliberately outside this retention policy.
    """
    with transaction() as c:
        result = c.execute("""DELETE FROM audit_events
            WHERE datetime(created_at) < datetime('now', '-6 months')
            AND action NOT LIKE 'senate.%'""")
        return result.rowcount


def seed_demo(include_sample=True):
    with transaction() as c:
        for id_, name in (("state", "Штат Сан-Андреас"), ("county", "Округ"), ("city", "Город Лос-Сантос")):
            c.execute("INSERT OR IGNORE INTO jurisdictions VALUES(?,?)", (id_, name))
        for id_, name in (("library", "Правовая библиотека штата Сан Андреас"),
                          ("legislature", "Законодательный орган"), ("government", "Правительство штата"),
                          ("secretary", "Секретарь штата"), ("judiciary", "Судебная система"),
                          ("los-santos", "Город Лос-Сантос")):
            c.execute("INSERT OR IGNORE INTO portals(id,name) VALUES(?,?)", (id_, name))
        # Rebrand only the original bundled portal name; preserve custom names.
        c.execute("""UPDATE portals SET name='Правовая библиотека штата Сан Андреас'
                     WHERE id='library' AND name='Законодательная библиотека'""")
        for id_, name in CATEGORIES.items():
            c.execute("INSERT OR IGNORE INTO categories(id,name) VALUES(?,?)", (id_, name))
        c.execute("""INSERT OR IGNORE INTO relation_types
                   (id,name,source_category,target_category,exclusive_target)
                   VALUES('accusation_punishment','Обвинение — мера наказания','accusation','punishment_measure',1)""")
        if include_sample:
            c.execute("""INSERT OR IGNORE INTO codes(id,jurisdiction_id,name,abbreviation)
                       VALUES('PEN','state','Penal Code','PEN')""")
            c.execute("""INSERT OR IGNORE INTO hierarchy_types
                       (id,code_id,name,level_order,allowed_parents)
                       VALUES('demo-chapter','PEN','Глава',1,'["ROOT"]')""")
            c.execute("""INSERT OR IGNORE INTO structure_nodes
                       (id,code_id,type_id,parent_key,number,number_key,sort_key,title)
                       VALUES('demo-intro','PEN','demo-chapter','ROOT','I','I','1I','Общие положения (демо)')""")
            if not maybe(c, "SELECT id FROM articles WHERE id='demo-article-1'"):
                now = ts()
                c.execute("""INSERT INTO articles
                           (id,code_id,parent_id,number,number_key,sort_key,title,text_rich,
                           categories_json,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                           ("demo-article-1", "PEN", "demo-intro", "1", "1",
                            number_sort_key("1"), "Демонстрационная норма",
                            "<p>Пример структуры правового текста. <strong>Не является действующим законом.</strong></p>",
                            dumps(["definition"]), now, now))
                append_revision(c, "demo-article-1", "system", "Создание демонстрационных данных")
