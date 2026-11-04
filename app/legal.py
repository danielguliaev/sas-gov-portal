"""Catalog, reader and lawyer-facing HTTP API."""
import base64
import json
import sqlite3

import bleach
from fastapi import APIRouter, Depends, Query
from app.core import (
    actor, audit, clean, conn, dumps, fail, loads, maybe, one, allrows,
    read_db, require, uid, ts, transaction, present_article, CATEGORIES
)

router = APIRouter()


@router.get("/me")
def me(current=Depends(actor)):
    return {"user_id": current["id"], "roles": sorted(current["roles"])}


@router.get("/jurisdictions")
def jurisdictions(current=Depends(actor)):
    with read_db() as c:
        return allrows(c, "SELECT * FROM jurisdictions ORDER BY id")


@router.get("/portals")
def portals(current=Depends(actor)):
    with read_db() as c:
        return allrows(c, "SELECT * FROM portals ORDER BY id")


@router.get("/codes")
def codes(jurisdiction: str | None = None, current=Depends(actor)):
    """Catalog metadata without legal fields; counts include only active content."""
    with read_db() as c:
        sql = """SELECT c.*,
                    COALESCE(a.article_count,0) AS article_count,
                    COALESCE(n.section_count,0) AS section_count
                 FROM codes c
                 LEFT JOIN (
                    SELECT code_id, COUNT(*) AS article_count FROM articles
                    WHERE status='active' GROUP BY code_id
                 ) a ON a.code_id=c.id
                 LEFT JOIN (
                    SELECT code_id, COUNT(*) AS section_count FROM structure_nodes
                    WHERE status='active' GROUP BY code_id
                 ) n ON n.code_id=c.id
                 WHERE c.status='active'"""
        args = []
        if jurisdiction:
            sql += " AND c.jurisdiction_id=?"
            args.append(jurisdiction)
        return allrows(c, sql + " ORDER BY c.name,c.id", args)


@router.get("/library/overview")
def library_overview(current=Depends(actor)):
    """Overview metrics for the reader homepage; no restricted legal attributes."""
    with read_db() as c:
        totals = one(c, """SELECT
              (SELECT COUNT(*) FROM codes WHERE status='active') AS code_count,
              (SELECT COUNT(*) FROM structure_nodes WHERE status='active') AS section_count,
              (SELECT COUNT(*) FROM articles WHERE status='active') AS article_count,
              (SELECT COUNT(*) FROM portal_publications WHERE status='published') AS publication_count""")
        latest = allrows(c, """SELECT a.id,a.code_id,a.number,a.title,a.is_demo,
                                 r.published_at,r.sequence,c.name AS code_name
                              FROM articles a JOIN article_revisions r ON r.id=a.revision_id
                              JOIN codes c ON c.id=a.code_id
                              WHERE a.status='active' AND c.status='active'
                              ORDER BY r.published_at DESC,r.id DESC LIMIT 6""")
        return {"totals": totals, "recent_revisions": latest}


@router.get("/codes/{code_id}/tree")
def code_tree(code_id: str, current=Depends(actor)):
    with read_db() as c:
        code = one(c, "SELECT * FROM codes WHERE id=?", (code_id,))
        types = allrows(c, "SELECT * FROM hierarchy_types WHERE code_id=? AND active=1 ORDER BY level_order,id",
                        (code_id,))
        nodes = allrows(c, "SELECT * FROM structure_nodes WHERE code_id=? AND status='active'", (code_id,))
        by_type = {t["id"]: t for t in types}
        modes = {r["parent_key"]: r["mode"] for r in allrows(c, "SELECT * FROM sort_modes")}
        def sort_node(n):
            mode = modes.get(code_id + ":" + n["parent_key"] + ":" + n["type_id"], "auto")
            sort_part = (0, n["manual_order"] if n["manual_order"] is not None else 999999) if mode == "manual" else (1, n["sort_key"])
            return (n["parent_key"], by_type.get(n["type_id"], {}).get("level_order", 999),
                    n["type_id"], sort_part, n["id"])
        nodes.sort(key=sort_node)
        return {"code": code, "types": types, "nodes": nodes}


def _cursor(value, scope):
    if not value:
        return None
    try:
        decoded = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if decoded["scope"] != scope:
            raise ValueError("scope")
        return decoded["sort"], decoded["id"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, base64.binascii.Error):
        fail(422, "INVALID_CURSOR", "Неверный курсор продолжения.")


def _paginate(c, code_id, parent_id, limit, cursor, current):
    scope = code_id + ":" + (parent_id or "ROOT")
    position = _cursor(cursor, scope)
    sql = "SELECT * FROM articles WHERE code_id=? AND status='active' AND parent_id IS ?"
    args = [code_id, parent_id]
    if position:
        sql += " AND (sort_key > ? OR (sort_key=? AND id>?))"
        args += [position[0], position[0], position[1]]
    rows = allrows(c, sql + " ORDER BY sort_key,id LIMIT ?", args + [limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        payload = {"sort": last["sort_key"], "id": last["id"], "scope": scope}
        next_cursor = base64.urlsafe_b64encode(dumps(payload).encode()).decode().rstrip("=")
    return {"items": [present_article(c, r, current) for r in rows],
            "has_more": has_more, "next_cursor": next_cursor}


@router.get("/codes/{code_id}/root/article-index")
def root_article_index(code_id: str, cursor: str | None = None,
                       limit: int = Query(100, ge=1, le=200), current=Depends(actor)):
    """Metadata-only sidebar index, scoped to articles directly in the code root."""
    with read_db() as c:
        one(c, "SELECT id FROM codes WHERE id=? AND status='active'", (code_id,))
        return _article_index(c, code_id, None, limit, cursor)


@router.get("/nodes/{node_id}/article-index")
def node_article_index(node_id: str, cursor: str | None = None,
                       limit: int = Query(100, ge=1, le=200), current=Depends(actor)):
    with read_db() as c:
        node = one(c, "SELECT id,code_id FROM structure_nodes WHERE id=? AND status='active'", (node_id,))
        return _article_index(c, node["code_id"], node_id, limit, cursor)


def _article_index(c, code_id, parent_id, limit, cursor):
    scope = code_id + ":" + (parent_id or "ROOT")
    position = _cursor(cursor, scope)
    sql = """SELECT id,number,title,sort_key FROM articles
             WHERE code_id=? AND parent_id IS ? AND status='active'"""
    args = [code_id, parent_id]
    if position:
        sql += " AND (sort_key > ? OR (sort_key=? AND id>?))"
        args += [position[0], position[0], position[1]]
    rows = allrows(c, sql + " ORDER BY sort_key,id LIMIT ?", args + [limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        payload = {"sort": rows[-1]["sort_key"], "id": rows[-1]["id"], "scope": scope}
        next_cursor = base64.urlsafe_b64encode(dumps(payload).encode()).decode().rstrip("=")
    return {"items": [{"id": a["id"], "number": a["number"], "title": a["title"]} for a in rows],
            "has_more": has_more, "next_cursor": next_cursor}


@router.get("/codes/{code_id}/root/articles")
def root_articles(code_id: str, cursor: str | None = None,
                  limit: int = Query(12, ge=1, le=50), current=Depends(actor)):
    with read_db() as c:
        one(c, "SELECT id FROM codes WHERE id=? AND status='active'", (code_id,))
        return _paginate(c, code_id, None, limit, cursor, current)


@router.get("/nodes/{node_id}/articles")
def node_articles(node_id: str, cursor: str | None = None,
                  limit: int = Query(12, ge=1, le=50), current=Depends(actor)):
    with read_db() as c:
        node = one(c, "SELECT * FROM structure_nodes WHERE id=? AND status='active'", (node_id,))
        return _paginate(c, node["code_id"], node_id, limit, cursor, current)


@router.get("/articles/{article_id}")
def article(article_id: str, current=Depends(actor)):
    with read_db() as c:
        a = one(c, "SELECT * FROM articles WHERE id=?", (article_id,))
        if a["status"] != "active":
            require(current, "lawyer", "superadmin")
        return present_article(c, a, current)


@router.get("/articles/{article_id}/position")
def article_position(article_id: str, current=Depends(actor)):
    with read_db() as c:
        a = one(c, "SELECT * FROM articles WHERE id=? AND status='active'", (article_id,))
        before = c.execute("""SELECT COUNT(*) FROM articles WHERE code_id=? AND parent_id IS ?
                              AND status='active' AND (sort_key<? OR (sort_key=? AND id<?))""",
                           (a["code_id"], a["parent_id"], a["sort_key"], a["sort_key"], article_id)).fetchone()[0]
        # Seek directly to the group containing this stable article ID.
        page_start = before - before % 12
        start_cursor = None
        if page_start:
            predecessor = one(c, """SELECT sort_key,id FROM articles
                           WHERE code_id=? AND parent_id IS ? AND status='active'
                           ORDER BY sort_key,id LIMIT 1 OFFSET ?""",
                           (a["code_id"], a["parent_id"], page_start - 1))
            scope = a["code_id"] + ":" + (a["parent_id"] or "ROOT")
            payload = {"sort": predecessor["sort_key"], "id": predecessor["id"], "scope": scope}
            start_cursor = base64.urlsafe_b64encode(dumps(payload).encode()).decode().rstrip("=")
        return {"code_id": a["code_id"], "parent_id": a["parent_id"],
                "index": before, "cursor_before": start_cursor}


@router.get("/articles/{article_id}/adjacent")
def adjacent_articles(article_id: str, current=Depends(actor)):
    with read_db() as c:
        a = one(c, "SELECT * FROM articles WHERE id=? AND status='active'", (article_id,))
        previous = maybe(c, """SELECT id,number,title FROM articles WHERE code_id=?
                     AND status='active' AND (sort_key<? OR (sort_key=? AND id<?))
                     ORDER BY sort_key DESC,id DESC LIMIT 1""",
                     (a["code_id"], a["sort_key"], a["sort_key"], a["id"]))
        following = maybe(c, """SELECT id,number,title FROM articles WHERE code_id=?
                     AND status='active' AND (sort_key>? OR (sort_key=? AND id>?))
                     ORDER BY sort_key,id LIMIT 1""",
                     (a["code_id"], a["sort_key"], a["sort_key"], a["id"]))
        return {"previous": previous, "next": following}


@router.get("/articles/{article_id}/revisions")
def revisions(article_id: str, current=Depends(actor)):
    require(current, "lawyer", "superadmin")
    with read_db() as c:
        one(c, "SELECT id FROM articles WHERE id=?", (article_id,))
        return allrows(c, """SELECT id,article_id,sequence,number,title,published_by,published_at,
                              change_reason,source_json FROM article_revisions WHERE article_id=?
                              ORDER BY sequence DESC""", (article_id,))


@router.get("/articles/{article_id}/revisions/{revision_id}")
def revision(article_id: str, revision_id: str, current=Depends(actor)):
    require(current, "lawyer", "superadmin")
    with read_db() as c:
        result = one(c, "SELECT * FROM article_revisions WHERE id=? AND article_id=?",
                     (revision_id, article_id))
        for key in ("categories_json", "custom_values", "sanctions_json", "relations_json", "source_json"):
            result[key] = loads(result[key])
        return result


@router.get("/articles/{article_id}/relations")
def relations(article_id: str, current=Depends(actor)):
    with read_db() as c:
        a = one(c, "SELECT * FROM articles WHERE id=?", (article_id,))
        if a["status"] != "active":
            require(current, "lawyer", "superadmin")
        rows = allrows(c, """SELECT r.id,r.type_id,r.source_id,r.target_id,
                             s.number AS source_number,s.title AS source_title,
                             t.number AS target_number,t.title AS target_title
                             FROM article_relations r
                             JOIN articles s ON s.id=r.source_id JOIN articles t ON t.id=r.target_id
                             WHERE r.active=1 AND (r.source_id=? OR r.target_id=?)
                             ORDER BY CASE WHEN r.source_id=? THEN t.sort_key ELSE s.sort_key END,r.id""",
                       (article_id, article_id, article_id))
        linked = any(r["target_id"] == article_id and r["type_id"] == "accusation_punishment" for r in rows)
        cats = loads(a["categories_json"], [])
        return {"items": rows, "unassigned_punishment": cats == ["punishment_measure"] and not linked}


@router.get("/categories")
def categories(code_id: str | None = None, current=Depends(actor)):
    with read_db() as c:
        rows = allrows(c, """SELECT * FROM categories WHERE active=1
                          AND (code_id IS NULL OR code_id=?) ORDER BY name COLLATE NOCASE,id""", (code_id,))
        if not current["roles"].intersection({"lawyer", "superadmin"}):
            return [{"id": r["id"], "name": r["name"]} for r in rows]
        for category in rows:
            category["fields"] = allrows(c, "SELECT * FROM category_fields WHERE category_id=? AND active=1 ORDER BY name",
                                         (category["id"],))
            for field in category["fields"]:
                field["options"] = allrows(c, """SELECT id,name,active FROM field_options
                                                 WHERE field_id=? ORDER BY name COLLATE NOCASE""", (field["id"],))
        return rows


@router.get("/relation-types")
def relation_types(current=Depends(actor)):
    with read_db() as c:
        return allrows(c, "SELECT * FROM relation_types WHERE active=1 ORDER BY name")


@router.get("/search")
def search(query: str = "", jurisdiction: str | None = None, code_id: str | None = None,
           number: str | None = None, category: str | None = None, criminal_class: str | None = None,
           has_sanctions: bool | None = None, min_months: int | None = None,
           max_months: int | None = None, min_usd: int | None = None,
           max_usd: int | None = None, field_id: str | None = None, field_value: str | None = None,
           archive: bool = False, current=Depends(actor)):
    advanced = any(x is not None for x in
                   (jurisdiction, code_id, number, category, criminal_class,
                    has_sanctions, min_months, max_months, min_usd, max_usd, field_id, field_value)) or archive
    if advanced:
        require(current, "lawyer", "superadmin")
    if len(query) > 200:
        fail(422, "INVALID_QUERY", "Поисковая строка слишком длинная.")
    with read_db() as c:
        sql = "SELECT a.* FROM articles a JOIN codes c ON c.id=a.code_id WHERE "
        conditions, params = [], []
        conditions.append("a.status=?" if archive else "a.status='active'")
        if archive:
            params.append("archived")
        if query.strip():
            conditions.append("(a.title LIKE ? ESCAPE '\\' OR a.text_rich LIKE ? ESCAPE '\\' OR a.number LIKE ? ESCAPE '\\')")
            escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.extend(["%" + escaped + "%"] * 3)
        if jurisdiction:
            conditions.append("c.jurisdiction_id=?"); params.append(jurisdiction)
        if code_id:
            conditions.append("a.code_id=?"); params.append(code_id)
        if number:
            conditions.append("a.number_key=?")
            from app.core import normalize_number
            params.append(normalize_number(number))
        if category:
            conditions.append("EXISTS (SELECT 1 FROM json_each(a.categories_json) WHERE value=?)")
            params.append(category)
        if criminal_class:
            conditions.append("a.criminal_class=?"); params.append(criminal_class)
        if has_sanctions is not None:
            condition = "(" + " OR ".join("a." + k + " IS NOT NULL" for k in
                       ("prison_min_months", "prison_max_months", "fine_min_usd", "fine_max_usd")) + ")"
            conditions.append(condition if has_sanctions else "NOT " + condition)
        for param, column, sign in (
            (min_months, "prison_min_months", ">="), (max_months, "prison_max_months", "<="),
            (min_usd, "fine_min_usd", ">="), (max_usd, "fine_max_usd", "<=")
        ):
            if param is not None:
                if param < 0:
                    fail(422, "INVALID_RANGE", "Отрицательный диапазон недопустим.")
                conditions.append("a." + column + sign + "?"); params.append(param)
        if field_id:
            one(c, "SELECT id FROM category_fields WHERE id=? AND active=1", (field_id,))
            conditions.append("json_extract(a.custom_values, ?) = ?")
            params.extend(["$." + field_id, field_value])
        if not query.strip() and not advanced:
            fail(422, "EMPTY_QUERY", "Введите поисковый запрос.")
        rows = allrows(c, sql + " AND ".join(conditions) + " ORDER BY a.code_id,a.sort_key,a.id LIMIT 100", params)
        return {"items": [present_article(c, a, current) for a in rows], "limit": 100}


@router.get("/me/preferences")
def get_preferences(current=Depends(actor)):
    with read_db() as c:
        data = maybe(c, "SELECT font_scale FROM user_preferences WHERE user_id=?", (current["id"],))
        return data or {"font_scale": 100}


@router.put("/me/preferences")
def update_preferences(body: dict, current=Depends(actor)):
    scale = body.get("font_scale")
    if type(scale) is not int or not 75 <= scale <= 175:
        fail(422, "INVALID_FONT_SCALE", "Масштаб шрифта: от 75 до 175 процентов.")
    with transaction() as c:
        c.execute("""INSERT INTO user_preferences(user_id,font_scale) VALUES(?,?)
                     ON CONFLICT(user_id) DO UPDATE SET font_scale=excluded.font_scale""",
                  (current["id"], scale))
    return {"font_scale": scale}


@router.get("/me/favorites")
def favorites(current=Depends(actor)):
    require(current, "lawyer", "superadmin")
    with read_db() as c:
        rows = allrows(c, """SELECT a.* FROM articles a
                             JOIN favorites f ON f.article_id=a.id
                             WHERE f.user_id=? AND a.status='active'
                             ORDER BY a.code_id,a.sort_key,a.id""", (current["id"],))
        return [present_article(c, r, current) for r in rows]


@router.put("/me/favorites/{article_id}")
def add_favorite(article_id: str, current=Depends(actor)):
    require(current, "lawyer", "superadmin")
    with transaction() as c:
        one(c, "SELECT id FROM articles WHERE id=? AND status='active'", (article_id,))
        c.execute("INSERT OR IGNORE INTO favorites VALUES(?,?)", (current["id"], article_id))
    return {"saved": True}


@router.delete("/me/favorites/{article_id}")
def remove_favorite(article_id: str, current=Depends(actor)):
    require(current, "lawyer", "superadmin")
    with transaction() as c:
        c.execute("DELETE FROM favorites WHERE user_id=? AND article_id=?", (current["id"], article_id))
    return {"saved": False}


@router.get("/me/notifications")
def notifications(portal_id: str = "library", current=Depends(actor)):
    # The library's notification center must never aggregate other agency portals.
    if portal_id != "library":
        fail(403, "PORTAL_SCOPE", "Центр уведомлений относится только к правовой библиотеке.")
    with read_db() as c:
        return allrows(c, """SELECT * FROM notifications WHERE recipient_id=? AND portal_id='library'
                             ORDER BY created_at DESC LIMIT 100""", (current["id"],))


@router.post("/me/notifications/{notification_id}/read")
def read_notification(notification_id: str, current=Depends(actor)):
    with transaction() as c:
        updated = c.execute("""UPDATE notifications SET is_read=1
                               WHERE id=? AND recipient_id=? AND portal_id='library'""",
                            (notification_id, current["id"]))
        if not updated.rowcount:
            fail(404, "NOT_FOUND", "Уведомление не найдено.")
    return {"is_read": True}


@router.get("/articles/{article_id}/annotations")
def annotations(article_id: str, revision_id: str, current=Depends(actor)):
    require(current, "lawyer", "superadmin")
    with read_db() as c:
        one(c, "SELECT id FROM article_revisions WHERE article_id=? AND id=?",
            (article_id, revision_id))
        # Notes are private to their author; superadmin may inspect for moderation.
        sql = "SELECT * FROM annotations WHERE article_id=? AND revision_id=?"
        args = [article_id, revision_id]
        if "superadmin" not in current["roles"]:
            sql += " AND author_id=?"
            args.append(current["id"])
        return allrows(c, sql + " ORDER BY created_at", args)


@router.post("/articles/{article_id}/annotations")
def create_annotation(article_id: str, body: dict, current=Depends(actor)):
    require(current, "lawyer", "superadmin")
    revision_id = body.get("revision_id")
    start, end = body.get("start_offset"), body.get("end_offset")
    quote, note = body.get("quote"), body.get("note_text")
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        fail(422, "INVALID_ANCHOR", "Некорректные границы выделения.")
    if not isinstance(quote, str) or not quote or len(quote) > 1000:
        fail(422, "INVALID_ANCHOR", "Требуется контрольный фрагмент.")
    note = plain_note(note)
    with transaction() as c:
        rev = one(c, "SELECT * FROM article_revisions WHERE article_id=? AND id=?",
                  (article_id, revision_id))
        visible = bleach.clean(rev["text_rich"], tags=[], strip=True)
        if end > len(visible) or visible[start:end] != quote:
            fail(422, "INVALID_ANCHOR", "Выделение не совпадает с текстом данной редакции.")
        annotation_id = uid()
        c.execute("INSERT INTO annotations VALUES(?,?,?,?,?,?,?,?,?)",
                  (annotation_id, current["id"], article_id, revision_id, start, end, quote, note, ts()))
    return {"id": annotation_id, "revision_id": revision_id}


def plain_note(note):
    from app.core import plain
    return plain(note, 5000)
