"""Safe structural transformations; physical deletion of legal nodes is never used."""
from collections import defaultdict
from fastapi import APIRouter, Depends

from app.core import (
    actor, audit, dumps, fail, loads, maybe, one, allrows, normalize_number,
    number_sort_key, plain, read_db, require, transaction
)
from app.editor import _fingerprint, _number_for_type

router = APIRouter()


@router.patch("/nodes/{node_id}")
def rename_node(node_id: str, body: dict, current=Depends(actor)):
    require(current, "superadmin")
    with transaction() as c:
        node = one(c, "SELECT * FROM structure_nodes WHERE id=? AND status='active'", (node_id,))
        typ = one(c, "SELECT * FROM hierarchy_types WHERE id=? AND active=1", (node["type_id"],))
        number = plain(body.get("number", node["number"]), 100)
        number_key = _number_for_type(number, typ)
        title = plain(body.get("title", node["title"]), 250)
        if not title:
            fail(422, "INVALID_TITLE", "Укажите название раздела.")
        if typ["number_format"] == "none":
            number_key = node["number_key"]
        # An existing manual index remains unchanged on renumbering.
        c.execute("""UPDATE structure_nodes SET number=?,number_key=?,sort_key=?,title=? WHERE id=?""",
                  (number, number_key, number_sort_key(title if typ["number_format"] == "none" else number), title, node_id))
        audit(c, current, "node.update", "structure_node", node_id, node,
              {"number": number, "title": title})
    return {"id": node_id, "number": number, "title": title}


def retire_preview(c, type_id):
    typ = one(c, "SELECT * FROM hierarchy_types WHERE id=? AND active=1", (type_id,))
    all_nodes = allrows(c, "SELECT * FROM structure_nodes WHERE code_id=? AND status='active'",
                        (typ["code_id"],))
    by_id = {x["id"]: x for x in all_nodes}
    retired = {n["id"] for n in all_nodes if n["type_id"] == type_id}
    article_rows = allrows(c, """SELECT id,parent_id,revision_id FROM articles
                        WHERE code_id=? AND status='active'""", (typ["code_id"],))
    types = {x["id"]: x for x in allrows(c, "SELECT * FROM hierarchy_types WHERE code_id=?",
                                         (typ["code_id"],))}
    moves_nodes, moves_articles, conflicts = [], [], []

    def surviving_parent(node, child_type=None):
        cursor = node.get("parent_id")
        allowed = loads(types[child_type]["allowed_parents"], []) if child_type else None
        while cursor:
            parent = by_id.get(cursor)
            if parent is None:
                return None, "Недоступный родительский элемент"
            if cursor not in retired and (allowed is None or parent["type_id"] in allowed):
                return cursor, None
            cursor = parent["parent_id"]
        if allowed is None or "ROOT" in allowed:
            return None, None
        return None, "Отсутствует допустимый родитель"

    for node in all_nodes:
        if node["id"] not in retired and node["parent_id"] in retired:
            new_parent, error = surviving_parent(node, node["type_id"])
            if error:
                conflicts.append({"node_id": node["id"], "reason": error})
            else:
                moves_nodes.append({"id": node["id"], "old_parent_id": node["parent_id"],
                                    "new_parent_id": new_parent, "number": node["number"],
                                    "title": node["title"]})
    for article in article_rows:
        if article["parent_id"] in retired:
            new_parent, error = surviving_parent(article)
            if error:
                conflicts.append({"article_id": article["id"], "reason": error})
            else:
                moves_articles.append({"id": article["id"], "old_parent_id": article["parent_id"],
                                       "new_parent_id": new_parent})
    proposed = {x["id"]: x["new_parent_id"] for x in moves_nodes}
    unique = {}
    for node in all_nodes:
        if node["id"] in retired:
            continue
        target = proposed.get(node["id"], node["parent_id"]) or "ROOT"
        key = (node["type_id"], target, node["number_key"])
        if key in unique:
            conflicts.append({"node_id": node["id"], "conflicts_with": unique[key],
                              "reason": "Номер уже занят в целевой группе"})
        else:
            unique[key] = node["id"]
    sort_modes = allrows(c, "SELECT * FROM sort_modes ORDER BY parent_key")
    snapshot = {"type_id": type_id,
                "nodes": sorted((n["id"], n["parent_id"], n["number_key"], n["manual_order"])
                                for n in all_nodes),
                "articles": sorted((a["id"], a["parent_id"], a["revision_id"]) for a in article_rows),
                "types": sorted((t["id"], t["active"], t["allowed_parents"])
                                for t in types.values()),
                "sort_modes": sort_modes}
    return {"type_id": type_id, "retired_node_ids": sorted(retired),
            "moved_nodes": moves_nodes, "moved_articles": moves_articles,
            "conflicts": conflicts, "preview_hash": _fingerprint(snapshot)}


@router.get("/hierarchy-types/{type_id}/retire/preview")
def preview_retire(type_id: str, current=Depends(actor)):
    require(current, "superadmin")
    with read_db() as c:
        return retire_preview(c, type_id)


@router.post("/hierarchy-types/{type_id}/retire")
def retire_type(type_id: str, body: dict, current=Depends(actor)):
    require(current, "superadmin")
    if body.get("confirm") is not True:
        fail(422, "CONFIRMATION_REQUIRED", "Удаление типа требует подтверждения.")
    with transaction() as c:
        plan = retire_preview(c, type_id)
        if plan["preview_hash"] != body.get("preview_hash"):
            fail(409, "PREVIEW_STALE", "Структура изменилась после предварительного просмотра.")
        if plan["conflicts"]:
            fail(409, "HIERARCHY_CONFLICT", "Сначала разрешите конфликты структурных элементов.",
                 {"conflicts": plan["conflicts"]})
        typ = one(c, "SELECT * FROM hierarchy_types WHERE id=?", (type_id,))
        moved = {x["id"]: x for x in plan["moved_nodes"]}
        current_nodes = allrows(c, """SELECT * FROM structure_nodes WHERE code_id=? AND status='active'""",
                                (typ["code_id"],))
        retiring = set(plan["retired_node_ids"])
        # Parent groups receiving lifted nodes are frozen in manual order.
        touched = {(item["new_parent_id"] or "ROOT", next(n["type_id"] for n in current_nodes if n["id"] == item["id"]))
                   for item in plan["moved_nodes"]}
        group_order = {}
        for parent_key, child_type in touched:
            existing = [n for n in current_nodes if n["id"] not in retiring and
                        n["id"] not in moved and n["parent_key"] == parent_key and
                        n["type_id"] == child_type]
            mode = maybe(c, "SELECT mode FROM sort_modes WHERE parent_key=?",
                         (typ["code_id"] + ":" + parent_key + ":" + child_type,))
            if mode and mode["mode"] == "manual":
                existing.sort(key=lambda n: (n["manual_order"] if n["manual_order"] is not None else 999999, n["id"]))
            else:
                existing.sort(key=lambda n: (n["sort_key"], n["id"]))
            incoming = [n for n in current_nodes if n["id"] in moved and
                        n["type_id"] == child_type and (moved[n["id"]]["new_parent_id"] or "ROOT") == parent_key]
            incoming.sort(key=lambda n: (n["parent_key"], n["manual_order"] if n["manual_order"] is not None else 999999,
                                          n["sort_key"], n["id"]))
            group_order[(parent_key, child_type)] = [n["id"] for n in existing + incoming]
        # Mark retired records inactive before moving surviving children; IDs remain available in history.
        for node_id in retiring:
            c.execute("UPDATE structure_nodes SET status='archived' WHERE id=?", (node_id,))
        for item in plan["moved_nodes"]:
            c.execute("UPDATE structure_nodes SET status='archived' WHERE id=?", (item["id"],))
        for item in plan["moved_nodes"]:
            c.execute("""UPDATE structure_nodes SET parent_id=?,parent_key=?,status='active'
                         WHERE id=?""",
                      (item["new_parent_id"], item["new_parent_id"] or "ROOT", item["id"]))
        for item in plan["moved_articles"]:
            c.execute("UPDATE articles SET parent_id=? WHERE id=?", (item["new_parent_id"], item["id"]))
        for (parent_key, child_type), ids in group_order.items():
            key = typ["code_id"] + ":" + parent_key + ":" + child_type
            c.execute("""INSERT INTO sort_modes(parent_key,mode) VALUES(?,'manual')
                         ON CONFLICT(parent_key) DO UPDATE SET mode='manual'""", (key,))
            for i, node_id in enumerate(ids):
                c.execute("UPDATE structure_nodes SET manual_order=? WHERE id=?", (i, node_id))
        c.execute("UPDATE hierarchy_types SET active=0 WHERE id=?", (type_id,))
        audit(c, current, "hierarchy_type.retire", "hierarchy_type", type_id, {}, plan)
        return {"id": type_id, "retired_node_ids": plan["retired_node_ids"],
                "moved_nodes": plan["moved_nodes"], "moved_articles": plan["moved_articles"]}
