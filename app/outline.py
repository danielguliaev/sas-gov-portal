"""Persistent mixed article/section outline for each code and hierarchy level."""
import base64
import json

from fastapi import APIRouter, Depends, Query
from app.core import (actor, audit, dumps, fail, one, allrows, read_db,
                      require, transaction, present_article)

router = APIRouter()


def outline_scope(code_id, parent_id):
    return code_id + ":OUTLINE:" + (parent_id or "ROOT")


def encode_cursor(code_id, parent_id, position, item_id):
    payload = {"scope": outline_scope(code_id, parent_id),
               "sort": position, "id": item_id}
    return base64.urlsafe_b64encode(dumps(payload).encode()).decode().rstrip("=")


def decode_cursor(value, code_id, parent_id):
    if not value:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if (data["scope"] != outline_scope(code_id, parent_id) or
                type(data["sort"]) is not int or data["sort"] < 0 or
                not isinstance(data["id"], str)):
            raise ValueError("scope")
        return data["sort"], data["id"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, base64.binascii.Error):
        fail(422, "INVALID_CURSOR", "Неверный курсор содержания кодекса.")


def validate_scope(c, code_id, parent_id):
    one(c, "SELECT id FROM codes WHERE id=? AND status='active'", (code_id,))
    if parent_id is not None:
        one(c, """SELECT id FROM structure_nodes
                WHERE id=? AND code_id=? AND status='active'""",
            (parent_id, code_id), "Раздел для изменения порядка не найден.")


def outline_rows(c, code_id, parent_id):
    return allrows(c, """SELECT kind,item_id AS id,position FROM code_outline
                          WHERE code_id=? AND parent_key=?
                          ORDER BY position,kind,item_id""",
                   (code_id, parent_id or "ROOT"))


def set_outline_order(c, code_id, parent_id, items):
    for index, item in enumerate(items, start=1):
        c.execute("""UPDATE code_outline SET position=?
                     WHERE code_id=? AND parent_key=? AND kind=? AND item_id=?""",
                  (index * 1000, code_id, parent_id or "ROOT",
                   item["kind"], item["id"]))


def place_outline_item(c, code_id, parent_id, kind, item_id, insert_at):
    if type(insert_at) is not int:
        fail(422, "INVALID_POSITION", "Позиция должна быть целым числом.")
    items = [dict(row) for row in outline_rows(c, code_id, parent_id)
             if not (row["kind"] == kind and row["id"] == item_id)]
    if insert_at < 0 or insert_at > len(items):
        fail(422, "INVALID_POSITION", "Позиция выходит за пределы содержания.")
    items.insert(insert_at, {"kind": kind, "id": item_id})
    set_outline_order(c, code_id, parent_id, items)


def sync_node_group(c, code_id, parent_id, type_id, ordered_ids):
    """Keep legacy within-type sort operations consistent with mixed order."""
    slots = allrows(c, """SELECT o.position,o.item_id FROM code_outline o
             JOIN structure_nodes n ON n.id=o.item_id
             WHERE o.code_id=? AND o.parent_key=? AND o.kind='node'
               AND n.type_id=? ORDER BY o.position,o.item_id""",
             (code_id, parent_id or "ROOT", type_id))
    if set(row["item_id"] for row in slots) != set(ordered_ids):
        fail(409, "OUTLINE_CONFLICT", "Состав разделов изменился.")
    for slot, item_id in zip(slots, ordered_ids):
        c.execute("UPDATE code_outline SET position=? WHERE kind='node' AND item_id=?",
                  (slot["position"], item_id))


@router.get("/codes/{code_id}/outline")
def get_outline(code_id: str, parent_id: str | None = None,
                cursor: str | None = None,
                limit: int = Query(24, ge=1, le=200),
                include_articles: bool = False, current=Depends(actor)):
    """Lazily return one unified direct-child sequence, with optional card data."""
    with read_db() as c:
        validate_scope(c, code_id, parent_id)
        after = decode_cursor(cursor, code_id, parent_id)
        key = parent_id or "ROOT"
        sql = """SELECT kind,item_id,position FROM code_outline
                 WHERE code_id=? AND parent_key=?"""
        params = [code_id, key]
        if after:
            sql += " AND (position>? OR (position=? AND item_id>?))"
            params.extend((after[0], after[0], after[1]))
        rows = allrows(c, sql + " ORDER BY position,kind,item_id LIMIT ?",
                       params + [limit + 1])
        has_more = len(rows) > limit
        rows = rows[:limit]
        result = []
        for entry in rows:
            if entry["kind"] == "article":
                article = one(c, """SELECT * FROM articles
                                    WHERE id=? AND status='active'""", (entry["item_id"],))
                item = {"kind": "article", "id": article["id"],
                        "number": article["number"], "title": article["title"]}
                if include_articles:
                    item["article"] = present_article(c, article, current)
            else:
                node = one(c, """SELECT n.*,t.name AS type_name
                                 FROM structure_nodes n
                                 JOIN hierarchy_types t ON t.id=n.type_id
                                 WHERE n.id=? AND n.status='active'""",
                           (entry["item_id"],))
                child_count = c.execute("""SELECT COUNT(*) FROM structure_nodes
                                  WHERE parent_id=? AND status='active'""",
                                        (node["id"],)).fetchone()[0]
                article_count = c.execute("""SELECT COUNT(*) FROM articles
                                  WHERE parent_id=? AND status='active'""",
                                          (node["id"],)).fetchone()[0]
                item = {"kind": "node", "id": node["id"],
                        "code_id": node["code_id"], "parent_id": node["parent_id"],
                        "type_id": node["type_id"], "type_name": node["type_name"],
                        "number": node["number"], "title": node["title"],
                        "child_count": child_count, "article_count": article_count}
            result.append(item)
        article_count = c.execute("""SELECT COUNT(*) FROM articles
                            WHERE code_id=? AND parent_id IS ? AND status='active'""",
                            (code_id, parent_id)).fetchone()[0]
        next_cursor = (encode_cursor(code_id, parent_id, rows[-1]["position"],
                                     rows[-1]["item_id"]) if has_more and rows else None)
        return {"items": result, "article_count": article_count,
                "has_more": has_more, "next_cursor": next_cursor}


@router.put("/codes/{code_id}/outline")
def reorder_outline(code_id: str, body: dict, current=Depends(actor)):
    require(current, "superadmin")
    parent_id = body.get("parent_id")
    if parent_id is not None and not isinstance(parent_id, str):
        fail(422, "INVALID_PARENT", "Недопустимый родитель содержания.")
    requested = body.get("items")
    if not isinstance(requested, list) or len(requested) > 10000:
        fail(422, "INVALID_SORT_ORDER", "Укажите полную последовательность элементов.")
    try:
        wanted = [(item["kind"], item["id"]) for item in requested]
    except (KeyError, TypeError):
        fail(422, "INVALID_SORT_ORDER", "Неверный формат последовательности.")
    if (any(kind not in ("node", "article") or not isinstance(item_id, str)
            for kind, item_id in wanted) or len(set(wanted)) != len(wanted)):
        fail(422, "INVALID_SORT_ORDER", "Порядок содержит неизвестные или повторные элементы.")
    with transaction() as c:
        validate_scope(c, code_id, parent_id)
        before = outline_rows(c, code_id, parent_id)
        existing = {(entry["kind"], entry["id"]) for entry in before}
        if existing != set(wanted):
            fail(409, "OUTLINE_CONFLICT",
                 "Состав статей и разделов изменился. Обновите список перед сохранением.")
        set_outline_order(c, code_id, parent_id,
                          [{"kind": kind, "id": item_id} for kind, item_id in wanted])
        audit(c, current, "code.outline.reorder", "code", code_id,
              {"parent_id": parent_id,
               "items": [{"kind": x["kind"], "id": x["id"]} for x in before]},
              {"parent_id": parent_id, "items": requested})
    return {"saved": True, "count": len(wanted)}


@router.post("/codes/{code_id}/outline/reset")
def reset_outline(code_id: str, body: dict, current=Depends(actor)):
    require(current, "superadmin")
    parent_id = body.get("parent_id")
    with transaction() as c:
        validate_scope(c, code_id, parent_id)
        rows = outline_rows(c, code_id, parent_id)
        nodes = {r["id"]: r for r in allrows(c, """SELECT n.id,n.sort_key,
                t.level_order FROM structure_nodes n
                JOIN hierarchy_types t ON t.id=n.type_id
                WHERE n.code_id=? AND n.parent_id IS ? AND n.status='active'""",
                (code_id, parent_id))}
        articles = {r["id"]: r for r in allrows(c, """SELECT id,sort_key FROM articles
                WHERE code_id=? AND parent_id IS ? AND status='active'""",
                (code_id, parent_id))}
        ordered = sorted(rows, key=lambda row:
            (0, nodes[row["id"]]["level_order"], nodes[row["id"]]["sort_key"], row["id"])
            if row["kind"] == "node" else
            (1, 0, articles[row["id"]]["sort_key"], row["id"]))
        set_outline_order(c, code_id, parent_id, ordered)
        audit(c, current, "code.outline.reset", "code", code_id, {},
              {"parent_id": parent_id, "count": len(ordered)})
    return {"saved": True, "count": len(ordered)}
