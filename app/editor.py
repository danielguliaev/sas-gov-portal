"""Administrative API: editor locks, revisions, hierarchy, relations and atomic archive."""
import hashlib
import secrets
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Header
from app.core import (
    actor, audit, append_revision, clean, dumps, fail, future, idempotent,
    loads, maybe, one, allrows, normalize_number, number_sort_key, plain,
    present_article, read_db, require, sanctions_of, transaction, ts, uid,
    validate_article, validate_parent, validate_relations, notify
)

router = APIRouter()


def _admin(current):
    require(current, "superadmin")


def _fingerprint(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def _check_lock(c, article_id, current, token, editor_session_id):
    if not isinstance(token, str) or not isinstance(editor_session_id, str):
        fail(409, "EDIT_LOCK_REQUIRED", "Требуется блокировка редактора.")
    lock = maybe(c, "SELECT * FROM edit_locks WHERE article_id=?", (article_id,))
    if (not lock or lock["user_id"] != current["id"] or
        lock["editor_session_id"] != editor_session_id or lock["revoked_at"] or
        lock["expires_at"] <= ts() or
        not secrets.compare_digest(lock["token_hash"], hashlib.sha256(token.encode()).hexdigest())):
        fail(409, "EDIT_LOCK_CONFLICT", "Блокировка истекла или принадлежит другой сессии.")


def _notify_favorites(c, article_id, new_revision, official=False):
    for row in allrows(c, "SELECT user_id FROM favorites WHERE article_id=?", (article_id,)):
        notify(c, row["user_id"], "library", "rev:" + new_revision,
               "Официальное изменение статьи" if official else "Изменение статьи из избранного",
               article_id)
    if official:
        # Known lawyer accounts with a library notification/favorite receive the event.
        users = allrows(c, """SELECT DISTINCT user_id FROM favorites
             UNION SELECT DISTINCT recipient_id AS user_id FROM notifications WHERE portal_id='library'""")
        for row in users:
            notify(c, row["user_id"], "library", "official:" + new_revision,
                   "Опубликовано официальное изменение законодательства", article_id)


def _add_article(c, code_id, body, current):
    one(c, "SELECT id FROM codes WHERE id=? AND status='active'", (code_id,))
    parent_id = body.get("parent_id")
    validate_parent(c, code_id, parent_id)
    number = plain(body.get("number"), 100)
    number_key = normalize_number(number)
    title = plain(body.get("title"), 300)
    rich = clean(body.get("text_rich", ""))
    categories, criminal_class, custom, sanctions = validate_article(c, body, code_id)
    article_id, now = uid(), ts()
    c.execute("""INSERT INTO articles(
      id,code_id,parent_id,number,number_key,sort_key,title,text_rich,is_demo,
      categories_json,criminal_class,custom_values,prison_min_months,prison_max_months,
      fine_min_usd,fine_max_usd,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (article_id, code_id, parent_id, number, number_key, number_sort_key(number),
       title, rich, 1 if body.get("is_demo", True) else 0,
       dumps(categories), criminal_class, dumps(custom),
       sanctions["prison_min_months"], sanctions["prison_max_months"],
       sanctions["fine_min_usd"], sanctions["fine_max_usd"], now, now))
    revision_id = append_revision(c, article_id, current["id"], body.get("reason", "Создание статьи"))
    audit(c, current, "article.create", "article", article_id, {}, {"revision_id": revision_id})
    return article_id


def _code_publication_metadata(body, old=None):
    """Optional code provenance; absence is not equivalent to official publication."""
    def field(key, limit):
        previous = (old.get(key) or "") if old else ""
        return plain(body.get(key, previous), limit)
    issuer = field("publication_issuer", 200)
    reference = field("publication_reference", 250)
    published = field("published_at", 10)
    if published:
        try:
            if len(published) != 10 or date.fromisoformat(published).isoformat() != published:
                raise ValueError("Date must be YYYY-MM-DD")
        except ValueError:
            fail(422, "INVALID_PUBLICATION_DATE", "Дата публикации должна быть в формате ГГГГ-ММ-ДД.")
    return issuer or None, reference or None, published or None


@router.post("/codes")
def create_code(body: dict, current=Depends(actor)):
    """Atomically create a code and, optionally, its editable hierarchy template."""
    _admin(current)
    name = plain(body.get("name"), 200).strip()
    abbreviation = plain(body.get("abbreviation", ""), 40).strip()
    jurisdiction_id = body.get("jurisdiction_id")
    template = body.get("hierarchy_template", "empty")
    issuer, reference, published = _code_publication_metadata(body)
    if not name:
        fail(422, "INVALID_CODE_NAME", "Введите название кодекса.")
    if template not in ("empty", "standard"):
        fail(422, "INVALID_HIERARCHY_TEMPLATE", "Неизвестный шаблон структуры кодекса.")
    with transaction() as c:
        one(c, "SELECT id FROM jurisdictions WHERE id=?", (jurisdiction_id,))
        names = allrows(c, "SELECT name FROM codes WHERE jurisdiction_id=?", (jurisdiction_id,))
        if any(row["name"].casefold() == name.casefold() for row in names):
            fail(409, "DUPLICATE_CODE", "В этой юрисдикции кодекс с таким названием уже существует.")
        code_id = uid()
        c.execute("""INSERT INTO codes(id,jurisdiction_id,name,abbreviation,is_demo,
                     publication_issuer,publication_reference,published_at)
                     VALUES(?,?,?,?,0,?,?,?)""",
                  (code_id, jurisdiction_id, name, abbreviation, issuer, reference, published))
        initial_types = []
        if template == "standard":
            # Hierarchy types are editable per code; a code can also begin empty.
            for order, (level_name, parent_names, number_format) in enumerate((
                ("Часть", ["ROOT"], "roman"),
                ("Раздел", ["ROOT", "Часть"], "mixed"),
                ("Глава", ["ROOT", "Часть", "Раздел"], "mixed"),
                ("Подраздел", ["ROOT", "Часть", "Раздел", "Глава"], "mixed"),
            ), start=1):
                type_id = uid()
                parent_ids = [p if p == "ROOT" else next(
                    item["id"] for item in initial_types if item["name"] == p)
                    for p in parent_names]
                c.execute("""INSERT INTO hierarchy_types
                          (id,code_id,name,level_order,allowed_parents,number_format)
                          VALUES(?,?,?,?,?,?)""",
                          (type_id, code_id, level_name, order, dumps(parent_ids), number_format))
                initial_types.append({"id": type_id, "name": level_name})
        audit(c, current, "code.create", "code", code_id, {},
              {"jurisdiction_id": jurisdiction_id, "name": name, "abbreviation": abbreviation,
               "hierarchy_template": template, "created_type_ids": [t["id"] for t in initial_types],
               "publication_issuer": issuer, "publication_reference": reference,
               "published_at": published})
    return {"id": code_id, "name": name, "hierarchy_template": template,
            "type_count": len(initial_types)}


@router.patch("/codes/{code_id}")
def update_code_metadata(code_id: str, body: dict, current=Depends(actor)):
    """Edit source information without changing the code's ID or jurisdiction."""
    _admin(current)
    if any(key in body for key in ("id", "jurisdiction_id", "status", "is_demo")):
        fail(422, "IMMUTABLE_CODE", "Идентификатор, юрисдикцию и статус кодекса нельзя изменить здесь.")
    with transaction() as c:
        old = one(c, "SELECT * FROM codes WHERE id=? AND status='active'", (code_id,))
        name = plain(body.get("name", old["name"]), 200)
        abbreviation = plain(body.get("abbreviation", old["abbreviation"]), 40)
        if not name:
            fail(422, "INVALID_CODE_NAME", "Название кодекса не может быть пустым.")
        others = allrows(c, "SELECT name FROM codes WHERE jurisdiction_id=? AND id<>?",
                         (old["jurisdiction_id"], code_id))
        if any(row["name"].casefold() == name.casefold() for row in others):
            fail(409, "DUPLICATE_CODE", "В этой юрисдикции кодекс с таким названием уже существует.")
        issuer, reference, published = _code_publication_metadata(body, old)
        c.execute("""UPDATE codes SET name=?,abbreviation=?,publication_issuer=?,
                     publication_reference=?,published_at=? WHERE id=?""",
                  (name, abbreviation, issuer, reference, published, code_id))
        audit(c, current, "code.metadata.update", "code", code_id, old,
              {"name": name, "abbreviation": abbreviation, "publication_issuer": issuer,
               "publication_reference": reference, "published_at": published})
        return {"id": code_id, "name": name, "abbreviation": abbreviation,
                "publication_issuer": issuer, "publication_reference": reference,
                "published_at": published}


@router.post("/codes/{code_id}/hierarchy-types")
def create_type(code_id: str, body: dict, current=Depends(actor)):
    _admin(current)
    with transaction() as c:
        one(c, "SELECT id FROM codes WHERE id=?", (code_id,))
        allowed = body.get("allowed_parents", ["ROOT"])
        if not isinstance(allowed, list) or not allowed or any(not isinstance(x, str) for x in allowed):
            fail(422, "INVALID_HIERARCHY", "Укажите допустимые родительские типы.")
        for parent_type in allowed:
            if parent_type != "ROOT":
                one(c, "SELECT id FROM hierarchy_types WHERE code_id=? AND id=? AND active=1",
                    (code_id, parent_type))
        level_order = body.get("level_order", 0)
        if type(level_order) is not int:
            fail(422, "INVALID_HIERARCHY", "Приоритет уровня должен быть целым.")
        number_format = body.get("number_format", "mixed")
        if number_format not in ("mixed", "numeric", "roman", "alpha"):
            fail(422, "INVALID_HIERARCHY", "Неверный формат номера.")
        type_id = uid()
        c.execute("""INSERT INTO hierarchy_types
                   (id,code_id,name,level_order,allowed_parents,number_format)
                   VALUES(?,?,?,?,?,?)""",
                  (type_id, code_id, plain(body.get("name"), 100), level_order,
                   dumps(allowed), number_format))
        audit(c, current, "hierarchy_type.create", "hierarchy_type", type_id, {}, body)
    return {"id": type_id}


def _number_for_type(number, typ):
    import re
    key = normalize_number(number)
    fmt = typ["number_format"]
    if fmt == "numeric" and not key.isdecimal():
        fail(422, "INVALID_NUMBER", "Ожидается цифровой номер.")
    if fmt == "roman" and not re.fullmatch(r"M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})", key):
        fail(422, "INVALID_NUMBER", "Ожидается римский номер.")
    if fmt == "alpha" and not key.isalpha():
        fail(422, "INVALID_NUMBER", "Ожидается буквенный номер.")
    return key


@router.patch("/hierarchy-types/{type_id}")
def update_type(type_id: str, body: dict, current=Depends(actor)):
    _admin(current)
    with transaction() as c:
        old = one(c, "SELECT * FROM hierarchy_types WHERE id=? AND active=1", (type_id,))
        new_name = plain(body.get("name", old["name"]), 100)
        parents = body.get("allowed_parents", loads(old["allowed_parents"], []))
        if not isinstance(parents, list) or not all(isinstance(x, str) for x in parents):
            fail(422, "INVALID_HIERARCHY", "Неверные типы родителей.")
        for p in parents:
            if p != "ROOT":
                one(c, "SELECT id FROM hierarchy_types WHERE id=? AND code_id=? AND active=1",
                    (p, old["code_id"]))
        rows = allrows(c, """SELECT n.id,n.parent_id,p.type_id AS parent_type
                       FROM structure_nodes n LEFT JOIN structure_nodes p ON p.id=n.parent_id
                       WHERE n.type_id=? AND n.status='active'""", (type_id,))
        conflicts = [x["id"] for x in rows if (x["parent_type"] or "ROOT") not in parents]
        if conflicts:
            fail(409, "HIERARCHY_CONFLICT", "Изменение нарушит структуру.",
                 {"node_ids": conflicts})
        level_order = body.get("level_order", old["level_order"])
        if type(level_order) is not int:
            fail(422, "INVALID_HIERARCHY", "Неверный порядок типа.")
        c.execute("""UPDATE hierarchy_types SET name=?,allowed_parents=?,level_order=?
                     WHERE id=?""", (new_name, dumps(parents), level_order, type_id))
        audit(c, current, "hierarchy_type.update", "hierarchy_type", type_id, old, body)
    return {"id": type_id}


@router.post("/codes/{code_id}/nodes")
def create_node(code_id: str, body: dict, current=Depends(actor)):
    _admin(current)
    with transaction() as c:
        typ = one(c, "SELECT * FROM hierarchy_types WHERE id=? AND code_id=? AND active=1",
                  (body.get("type_id"), code_id))
        parent_id = body.get("parent_id")
        validate_parent(c, code_id, parent_id, typ["id"])
        number = plain(body.get("number"), 100)
        key = _number_for_type(number, typ)
        parent_key = parent_id or "ROOT"
        sort_mode = maybe(c, "SELECT mode FROM sort_modes WHERE parent_key=?",
                          (code_id + ":" + parent_key + ":" + typ["id"],))
        manual = None
        if sort_mode and sort_mode["mode"] == "manual":
            manual = c.execute("""SELECT COALESCE(MAX(manual_order),-1)+1 FROM structure_nodes
                                  WHERE parent_key=? AND type_id=? AND status='active'""",
                               (parent_key, typ["id"])).fetchone()[0]
        node_id = uid()
        c.execute("""INSERT INTO structure_nodes
                   (id,code_id,type_id,parent_id,parent_key,number,number_key,sort_key,title,manual_order)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (node_id, code_id, typ["id"], parent_id, parent_key,
                   number, key, number_sort_key(number, typ["number_format"]), plain(body.get("title"), 250), manual))
        audit(c, current, "node.create", "structure_node", node_id, {}, body)
    return {"id": node_id}


def _subtree(c, node_id):
    nodes = allrows(c, """WITH RECURSIVE tree(id) AS (
         SELECT id FROM structure_nodes WHERE id=?
         UNION ALL SELECT n.id FROM structure_nodes n JOIN tree t ON n.parent_id=t.id
         ) SELECT n.* FROM structure_nodes n JOIN tree t ON n.id=t.id""", (node_id,))
    ids = [n["id"] for n in nodes]
    articles = []
    if ids:
        placeholders = ",".join("?" for _ in ids)
        articles = allrows(c, "SELECT * FROM articles WHERE parent_id IN (" + placeholders + ")", ids)
    return nodes, articles


def _move_preview(c, node_id, parent_id):
    node = one(c, "SELECT * FROM structure_nodes WHERE id=? AND status='active'", (node_id,))
    descendants, articles = _subtree(c, node_id)
    if parent_id in {n["id"] for n in descendants}:
        fail(422, "HIERARCHY_CYCLE", "Нельзя переместить узел в собственного потомка.")
    validate_parent(c, node["code_id"], parent_id, node["type_id"])
    snapshot = {"node_id": node_id, "parent_id": parent_id,
                "nodes": [n["id"] for n in descendants], "articles": [a["id"] for a in articles],
                "revision_ids": [a["revision_id"] for a in articles]}
    return {"node": node_id, "new_parent_id": parent_id,
            "node_count": len(descendants), "article_count": len(articles),
            "article_ids": snapshot["articles"], "preview_hash": _fingerprint(snapshot)}


@router.post("/nodes/{node_id}/move/preview")
def node_move_preview(node_id: str, body: dict, current=Depends(actor)):
    _admin(current)
    with read_db() as c:
        return _move_preview(c, node_id, body.get("new_parent_id"))


@router.post("/nodes/{node_id}/move")
def move_node(node_id: str, body: dict, current=Depends(actor)):
    _admin(current)
    if body.get("confirm") is not True:
        fail(422, "CONFIRMATION_REQUIRED", "Подтвердите перенос.")
    with transaction() as c:
        preview = _move_preview(c, node_id, body.get("new_parent_id"))
        if preview["preview_hash"] != body.get("preview_hash"):
            fail(409, "PREVIEW_STALE", "Состав операции изменился.")
        node = one(c, "SELECT * FROM structure_nodes WHERE id=?", (node_id,))
        parent_id = body.get("new_parent_id")
        parent_key = parent_id or "ROOT"
        dest_key = node["code_id"] + ":" + parent_key + ":" + node["type_id"]
        mode = maybe(c, "SELECT mode FROM sort_modes WHERE parent_key=?", (dest_key,))
        manual = None
        if mode and mode["mode"] == "manual":
            pos = body.get("insert_at")
            if type(pos) is not int or pos < 0:
                fail(422, "INSERT_POSITION_REQUIRED", "Укажите позицию вставки.")
            siblings = allrows(c, """SELECT id FROM structure_nodes
                    WHERE code_id=? AND parent_key=? AND type_id=? AND status='active' AND id<>?
                    ORDER BY COALESCE(manual_order,999999),sort_key,id""",
                    (node["code_id"], parent_key, node["type_id"], node_id))
            if pos > len(siblings):
                fail(422, "INVALID_POSITION", "Недопустимая позиция.")
            siblings.insert(pos, {"id": node_id})
            for i, sibling in enumerate(siblings):
                c.execute("UPDATE structure_nodes SET manual_order=? WHERE id=?", (i, sibling["id"]))
            manual = pos
        c.execute("""UPDATE structure_nodes SET parent_id=?,parent_key=?,manual_order=?
                     WHERE id=?""", (parent_id, parent_key, manual, node_id))
        audit(c, current, "node.move", "structure_node", node_id, node, preview)
    return {"id": node_id, "moved": True}


@router.post("/nodes/order")
def reorder_nodes(body: dict, current=Depends(actor)):
    _admin(current)
    code_id, parent_id, type_id = body.get("code_id"), body.get("parent_id"), body.get("type_id")
    mode = body.get("mode")
    if mode not in ("auto", "manual"):
        fail(422, "INVALID_SORT_MODE", "Выберите auto или manual.")
    with transaction() as c:
        validate_parent(c, code_id, parent_id, type_id)
        siblings = allrows(c, """SELECT id FROM structure_nodes WHERE code_id=? AND parent_key=?
                      AND type_id=? AND status='active' ORDER BY sort_key,id""",
                           (code_id, parent_id or "ROOT", type_id))
        ids = [r["id"] for r in siblings]
        ordered = body.get("ordered_ids", ids)
        if mode == "manual" and (not isinstance(ordered, list) or
                                 len(ordered) != len(ids) or set(ordered) != set(ids)):
            fail(422, "INVALID_SORT_ORDER", "Порядок должен содержать все элементы группы.")
        key = code_id + ":" + (parent_id or "ROOT") + ":" + type_id
        c.execute("""INSERT INTO sort_modes(parent_key,mode) VALUES(?,?)
                  ON CONFLICT(parent_key) DO UPDATE SET mode=excluded.mode""", (key, mode))
        for i, node_id in enumerate(ordered if mode == "manual" else ids):
            c.execute("UPDATE structure_nodes SET manual_order=? WHERE id=?",
                      (i if mode == "manual" else None, node_id))
        audit(c, current, "nodes.reorder", "code", code_id, {}, {"mode": mode, "ids": ordered})
    return {"mode": mode}


@router.post("/articles")
def create_article(body: dict, current=Depends(actor)):
    _admin(current)
    with transaction() as c:
        article_id = _add_article(c, body.get("code_id"), body, current)
        a = one(c, "SELECT * FROM articles WHERE id=?", (article_id,))
        return present_article(c, a, current)


@router.post("/articles/{article_id}/edit-lock")
def acquire_lock(article_id: str, body: dict, current=Depends(actor)):
    require(current, "superadmin", "library_editor")
    session_id = plain(body.get("editor_session_id"), 120)
    if not session_id:
        fail(422, "SESSION_REQUIRED", "Требуется ID сессии редактора.")
    token = secrets.token_urlsafe(32)
    with transaction() as c:
        one(c, "SELECT id FROM articles WHERE id=? AND status='active'", (article_id,))
        old = maybe(c, "SELECT * FROM edit_locks WHERE article_id=?", (article_id,))
        if old and old["revoked_at"] is None and old["expires_at"] > ts():
            fail(409, "EDIT_LOCK_CONFLICT", "Статья уже открыта в другом редакторе.")
        c.execute("DELETE FROM edit_locks WHERE article_id=?", (article_id,))
        expires = future()
        c.execute("INSERT INTO edit_locks VALUES(?,?,?,?,?,?,NULL)",
                  (article_id, current["id"], session_id, hashlib.sha256(token.encode()).hexdigest(),
                   ts(), expires))
        audit(c, current, "lock.acquire", "article", article_id)
    return {"token": token, "expires_at": expires, "editor_session_id": session_id}


@router.post("/articles/{article_id}/edit-lock/heartbeat")
def heartbeat_lock(article_id: str, body: dict, current=Depends(actor)):
    require(current, "superadmin", "library_editor")
    with transaction() as c:
        _check_lock(c, article_id, current, body.get("token"), body.get("editor_session_id"))
        expires = future()
        c.execute("UPDATE edit_locks SET heartbeat_at=?,expires_at=? WHERE article_id=?",
                  (ts(), expires, article_id))
    return {"expires_at": expires}


@router.delete("/articles/{article_id}/edit-lock")
def release_lock(article_id: str, body: dict, current=Depends(actor)):
    require(current, "superadmin", "library_editor")
    with transaction() as c:
        lock = maybe(c, "SELECT * FROM edit_locks WHERE article_id=?", (article_id,))
        if not lock or lock["user_id"] != current["id"] or lock["editor_session_id"] != body.get("editor_session_id") or not isinstance(body.get("token"), str) or not secrets.compare_digest(
            lock["token_hash"], hashlib.sha256(body["token"].encode()).hexdigest()):
            fail(409, "EDIT_LOCK_CONFLICT", "Не принадлежит этой сессии.")
        c.execute("DELETE FROM edit_locks WHERE article_id=?", (article_id,))
        audit(c, current, "lock.release", "article", article_id)
    return {"released": True}


@router.post("/articles/{article_id}/edit-lock/revoke")
def revoke_own_lock(article_id: str, body: dict, current=Depends(actor)):
    _admin(current)
    if body.get("confirm") is not True:
        fail(422, "CONFIRMATION_REQUIRED", "Подтвердите снятие своей блокировки.")
    with transaction() as c:
        lock = one(c, "SELECT * FROM edit_locks WHERE article_id=?", (article_id,))
        if lock["user_id"] != current["id"]:
            fail(403, "FOREIGN_LOCK", "Чужую блокировку снимать запрещено.")
        c.execute("UPDATE edit_locks SET revoked_at=? WHERE article_id=?", (ts(), article_id))
        audit(c, current, "lock.revoke-own", "article", article_id)
    return {"revoked": True}


@router.post("/articles/{article_id}/revisions")
def revise_article(article_id: str, body: dict, current=Depends(actor)):
    _admin(current)
    with transaction() as c:
        a = one(c, "SELECT * FROM articles WHERE id=? AND status='active'", (article_id,))
        _check_lock(c, article_id, current, body.get("lock_token"), body.get("editor_session_id"))
        if body.get("base_revision_id") != a["revision_id"]:
            fail(409, "STALE_REVISION", "Появилась новая редакция. Перезагрузите исходный текст.")
        if "code_id" in body and body["code_id"] != a["code_id"]:
            fail(422, "IMMUTABLE_CODE", "Перенос статьи между кодексами запрещён.")
        parent_id = body.get("parent_id", a["parent_id"])
        validate_parent(c, a["code_id"], parent_id)
        number = plain(body.get("number", a["number"]), 100)
        number_key = normalize_number(number)
        title = plain(body.get("title", a["title"]), 300)
        rich = clean(body.get("text_rich", a["text_rich"]))
        categories, criminal_class, custom, sanctions = validate_article(c, body, a["code_id"], a)
        is_demo = 1 if body.get("is_demo", bool(a["is_demo"])) else 0
        legal_changed = (number != a["number"] or title != a["title"] or rich != a["text_rich"] or
                         dumps(categories) != a["categories_json"] or
                         criminal_class != a["criminal_class"] or dumps(custom) != a["custom_values"] or
                         sanctions != sanctions_of(a) or is_demo != a["is_demo"])
        c.execute("""UPDATE articles SET parent_id=?,number=?,number_key=?,sort_key=?,title=?,
                    text_rich=?,categories_json=?,criminal_class=?,custom_values=?,
                    prison_min_months=?,prison_max_months=?,fine_min_usd=?,fine_max_usd=?,
                    is_demo=?,updated_at=? WHERE id=?""",
                  (parent_id, number, number_key, number_sort_key(number), title, rich,
                   dumps(categories), criminal_class, dumps(custom),
                   sanctions["prison_min_months"], sanctions["prison_max_months"],
                   sanctions["fine_min_usd"], sanctions["fine_max_usd"],
                   is_demo, ts(), article_id))
        linked_before = allrows(c, """SELECT id,source_id,target_id FROM article_relations
                                     WHERE active=1 AND (source_id=? OR target_id=?)""",
                                (article_id, article_id))
        validate_relations(c, article_id)
        inactive = {r["id"] for r in allrows(c, """SELECT id FROM article_relations WHERE active=0
                           AND (source_id=? OR target_id=?)""", (article_id, article_id))}
        affected = {x["source_id"] if x["source_id"] != article_id else x["target_id"]
                    for x in linked_before if x["id"] in inactive}
        for other_id in affected:
            append_revision(c, other_id, current["id"], "Отключение несовместимой юридической связи")
        revision_id = a["revision_id"]
        if legal_changed or affected:
            revision_id = append_revision(c, article_id, current["id"], body.get("reason", "Редактирование"))
            _notify_favorites(c, article_id, revision_id)
        audit(c, current, "article.revise" if legal_changed else "article.move",
              "article", article_id, {"revision_id": a["revision_id"]},
              {"revision_id": revision_id, "parent_id": parent_id})
        return present_article(c, one(c, "SELECT * FROM articles WHERE id=?", (article_id,)), current)


@router.post("/relation-types")
def create_relation_type(body: dict, current=Depends(actor)):
    _admin(current)
    with transaction() as c:
        for key in ("source_category", "target_category"):
            if body.get(key):
                one(c, "SELECT id FROM categories WHERE id=? AND active=1", (body[key],))
        kind_id = uid()
        c.execute("""INSERT INTO relation_types(id,name,source_category,target_category,exclusive_target)
                     VALUES(?,?,?,?,?)""",
                  (kind_id, plain(body.get("name"), 200), body.get("source_category"),
                   body.get("target_category"), 1 if body.get("exclusive_target") else 0))
        audit(c, current, "relation_type.create", "relation_type", kind_id, {}, body)
    return {"id": kind_id}


@router.patch("/relation-types/{type_id}")
def update_relation_type(type_id: str, body: dict, current=Depends(actor)):
    _admin(current)
    with transaction() as c:
        old = one(c, "SELECT * FROM relation_types WHERE id=? AND active=1", (type_id,))
        source_cat = body.get("source_category", old["source_category"])
        target_cat = body.get("target_category", old["target_category"])
        for cat in (source_cat, target_cat):
            if cat:
                one(c, "SELECT id FROM categories WHERE id=? AND active=1", (cat,))
        exclusive = bool(body.get("exclusive_target", old["exclusive_target"]))
        conflicts = []
        seen = set()
        for rel in allrows(c, "SELECT * FROM article_relations WHERE type_id=? AND active=1", (type_id,)):
            s = one(c, "SELECT categories_json FROM articles WHERE id=?", (rel["source_id"],))
            t = one(c, "SELECT categories_json FROM articles WHERE id=?", (rel["target_id"],))
            competing = maybe(c, """SELECT id FROM article_relations WHERE active=1
                        AND exclusive_target=1 AND target_id=? AND type_id<>? LIMIT 1""",
                        (rel["target_id"], type_id)) if exclusive else None
            if ((source_cat and source_cat not in loads(s["categories_json"], [])) or
                (target_cat and target_cat not in loads(t["categories_json"], [])) or
                (exclusive and (rel["target_id"] in seen or competing))):
                conflicts.append({"relation_id": rel["id"], "source_id": rel["source_id"],
                                  "target_id": rel["target_id"],
                                  "competing_relation_id": competing["id"] if competing else None})
            seen.add(rel["target_id"])
        if conflicts:
            fail(409, "RELATION_RULE_CONFLICT", "Правило несовместимо с действующими связями.",
                 {"conflicts": conflicts})
        c.execute("""UPDATE relation_types SET name=?,source_category=?,target_category=?,exclusive_target=?
                     WHERE id=?""", (plain(body.get("name", old["name"]), 200), source_cat,
                                    target_cat, 1 if exclusive else 0, type_id))
        # The denormalized column also powers a filtered unique index at the DB layer.
        c.execute("UPDATE article_relations SET exclusive_target=? WHERE type_id=?",
                  (1 if exclusive else 0, type_id))
        audit(c, current, "relation_type.update", "relation_type", type_id, old, body)
    return {"id": type_id}


@router.post("/relations")
def create_relation(body: dict, current=Depends(actor)):
    _admin(current)
    with transaction() as c:
        kind = one(c, "SELECT * FROM relation_types WHERE id=? AND active=1", (body.get("type_id"),))
        source = one(c, "SELECT * FROM articles WHERE id=? AND status='active'", (body.get("source_id"),))
        target = one(c, "SELECT * FROM articles WHERE id=? AND status='active'", (body.get("target_id"),))
        if source["id"] == target["id"]:
            fail(422, "SELF_RELATION", "Связь с самой собой не требуется для собственных санкций.")
        if (kind["source_category"] and kind["source_category"] not in loads(source["categories_json"], [])) or (
            kind["target_category"] and kind["target_category"] not in loads(target["categories_json"], [])):
            fail(422, "RELATION_RULE", "Несовместимые категории источника и цели.")
        rel_id = uid()
        c.execute("""INSERT INTO article_relations(id,type_id,source_id,target_id,exclusive_target,created_at)
                     VALUES(?,?,?,?,?,?)""", (rel_id, kind["id"], source["id"], target["id"],
                                                kind["exclusive_target"], ts()))
        for aid in (source["id"], target["id"]):
            rev = append_revision(c, aid, current["id"], "Добавление юридической связи")
            _notify_favorites(c, aid, rev)
        audit(c, current, "relation.create", "article_relation", rel_id, {}, body)
    return {"id": rel_id}


@router.post("/relations/{relation_id}/deactivate")
def deactivate_relation(relation_id: str, body: dict, current=Depends(actor)):
    _admin(current)
    with transaction() as c:
        old = one(c, "SELECT * FROM article_relations WHERE id=? AND active=1", (relation_id,))
        c.execute("UPDATE article_relations SET active=0,disabled_reason=?,disabled_at=? WHERE id=?",
                  (plain(body.get("reason", "manual"), 100), ts(), relation_id))
        for aid in (old["source_id"], old["target_id"]):
            append_revision(c, aid, current["id"], "Отключение юридической связи")
        audit(c, current, "relation.deactivate", "article_relation", relation_id, old, {})
    return {"active": False}


def _relation_restore_plan(c, relation_id):
    relation = one(c, "SELECT * FROM article_relations WHERE id=? AND active=0", (relation_id,))
    kind = one(c, "SELECT * FROM relation_types WHERE id=? AND active=1", (relation["type_id"],))
    source = one(c, "SELECT id,status,categories_json,revision_id,number,title FROM articles WHERE id=?",
                 (relation["source_id"],))
    target = one(c, "SELECT id,status,categories_json,revision_id,number,title FROM articles WHERE id=?",
                 (relation["target_id"],))
    causes = []
    if source["status"] != "active" or target["status"] != "active":
        causes.append("Одна из статей находится в архиве.")
    if kind["source_category"] and kind["source_category"] not in loads(source["categories_json"], []):
        causes.append("Исходная статья больше не имеет требуемой категории.")
    if kind["target_category"] and kind["target_category"] not in loads(target["categories_json"], []):
        causes.append("Целевая статья больше не имеет требуемой категории.")
    existing = maybe(c, """SELECT id FROM article_relations WHERE active=1
                        AND type_id=? AND source_id=? AND target_id=?""",
                     (relation["type_id"],relation["source_id"],relation["target_id"]))
    if existing:
        causes.append("Эквивалентная действующая связь уже существует.")
    if kind["exclusive_target"]:
        assigned = maybe(c, """SELECT id FROM article_relations WHERE active=1 AND exclusive_target=1
                               AND target_id=?""", (relation["target_id"],))
        if assigned:
            causes.append("Мера наказания уже назначена другому обвинению.")
    snapshot = {"relation":relation,"type":kind,
                "source":source,"target":target,"active_equivalent":existing,
                "allowed": not causes}
    return {"relation_id":relation_id,"type_name":kind["name"],"source_id":source["id"],
            "target_id":target["id"],"source_label":source["number"]+" · "+source["title"],
            "target_label":target["number"]+" · "+target["title"],
            "disabled_reason":relation["disabled_reason"],"conflicts":causes,
            "restorable":not causes,"preview_hash":_fingerprint(snapshot)}


@router.get("/articles/{article_id}/relations/restore-candidates")
def relation_restore_candidates(article_id: str, current=Depends(actor)):
    _admin(current)
    with read_db() as c:
        one(c, "SELECT id FROM articles WHERE id=?", (article_id,))
        archived = allrows(c, """SELECT id FROM article_relations WHERE active=0
                  AND (source_id=? OR target_id=?) ORDER BY disabled_at DESC,id DESC LIMIT 100""",
                  (article_id,article_id))
        return [_relation_restore_plan(c,row["id"]) for row in archived]


@router.post("/relations/{relation_id}/restore")
def restore_relation(relation_id: str, body: dict,
                     idempotency_key: str | None = Header(None), current=Depends(actor)):
    _admin(current)
    if body.get("confirm") is not True:
        fail(422, "CONFIRMATION_REQUIRED", "Подтвердите выборочное восстановление юридической связи.")
    with transaction() as c:
        def apply():
            preview = _relation_restore_plan(c,relation_id)
            if body.get("preview_hash") != preview["preview_hash"]:
                fail(409,"PREVIEW_STALE","Связи или юридические категории изменились после проверки.")
            if not preview["restorable"]:
                fail(409,"RELATION_RESTORE_CONFLICT","Связь несовместима с действующим законодательством.",
                     {"conflicts":preview["conflicts"]})
            old = one(c,"SELECT * FROM article_relations WHERE id=?",(relation_id,))
            kind = one(c,"SELECT exclusive_target FROM relation_types WHERE id=?",(old["type_id"],))
            c.execute("""UPDATE article_relations SET active=1,exclusive_target=?,
                         disabled_reason=NULL,disabled_at=NULL WHERE id=?""",
                      (kind["exclusive_target"],relation_id))
            for article in (old["source_id"],old["target_id"]):
                new_rev = append_revision(c,article,current["id"],
                                          "Выборочное восстановление юридической связи")
                _notify_favorites(c,article,new_rev)
            audit(c,current,"relation.restore","article_relation",relation_id,old,{
                "active":True,"preview_hash":preview["preview_hash"]})
            return {"id":relation_id,"active":True}
        return idempotent(c,current,"relation.restore:"+relation_id,idempotency_key,body,apply)


@router.post("/categories")
def create_category(body: dict, current=Depends(actor)):
    _admin(current)
    with transaction() as c:
        code_id = body.get("code_id")
        if code_id:
            one(c, "SELECT id FROM codes WHERE id=?", (code_id,))
        name = plain(body.get("name"), 150)
        if not name:
            fail(422, "INVALID_CATEGORY", "Введите название категории.")
        existing = allrows(c,
            "SELECT name FROM categories WHERE code_id IS ? AND active=1", (code_id,))
        if any(row["name"].casefold() == name.casefold() for row in existing):
            fail(409, "DUPLICATE_CATEGORY", "Такая категория уже существует.")
        cat_id = uid()
        c.execute("INSERT INTO categories(id,code_id,name) VALUES(?,?,?)", (cat_id, code_id, name))
        audit(c, current, "category.create", "category", cat_id, {}, body)
    return {"id": cat_id}


@router.patch("/categories/{category_id}")
def update_category(category_id: str, body: dict, current=Depends(actor)):
    _admin(current)
    with transaction() as c:
        old = one(c, "SELECT * FROM categories WHERE id=? AND active=1", (category_id,))
        name = plain(body.get("name", old["name"]), 150)
        if not name:
            fail(422, "INVALID_CATEGORY", "Введите название категории.")
        existing = allrows(c, """SELECT name FROM categories WHERE code_id IS ?
                          AND active=1 AND id<>?""", (old["code_id"], category_id))
        if any(row["name"].casefold() == name.casefold() for row in existing):
            fail(409, "DUPLICATE_CATEGORY", "Категория с таким названием уже существует.")
        c.execute("UPDATE categories SET name=? WHERE id=?", (name, category_id))
        audit(c, current, "category.update", "category", category_id, old, {"name": name})
    return {"id": category_id, "name": name}


@router.post("/categories/{category_id}/fields")
def create_field(category_id: str, body: dict, current=Depends(actor)):
    _admin(current)
    with transaction() as c:
        one(c, "SELECT id FROM categories WHERE id=? AND active=1", (category_id,))
        field_type = body.get("field_type")
        selection_mode = body.get("selection_mode") if field_type == "choice" else None
        if field_type not in ("string", "rich_text", "number", "range", "boolean", "choice"):
            fail(422, "INVALID_FIELD_TYPE", "Тип поля не поддерживается.")
        if field_type == "choice" and selection_mode not in ("single", "multiple"):
            fail(422, "INVALID_SELECTION_MODE", "Укажите single или multiple.")
        name = plain(body.get("name"), 150)
        if not name:
            fail(422, "INVALID_FIELD_NAME", "Введите название нового поля.")
        siblings = allrows(c,"SELECT name FROM category_fields WHERE category_id=? AND active=1",
                           (category_id,))
        if any(row["name"].casefold() == name.casefold() for row in siblings):
            fail(409, "DUPLICATE_FIELD", "Поле с таким названием уже существует в категории.")
        field_id = uid()
        c.execute("""INSERT INTO category_fields(id,category_id,name,field_type,selection_mode)
                     VALUES(?,?,?,?,?)""",
                  (field_id, category_id, name, field_type, selection_mode))
        audit(c, current, "field.create", "category_field", field_id, {}, body)
    return {"id": field_id}


@router.patch("/category-fields/{field_id}")
def update_field(field_id: str, body: dict, current=Depends(actor)):
    _admin(current)
    with transaction() as c:
        old = one(c, "SELECT * FROM category_fields WHERE id=? AND active=1", (field_id,))
        new_type = body.get("field_type", old["field_type"])
        new_mode = body.get("selection_mode", old["selection_mode"])
        if old["used_ever"] and (new_type != old["field_type"] or new_mode != old["selection_mode"]):
            fail(409, "USED_FIELD_IMMUTABLE", "Тип использованного поля нельзя менять.")
        if new_type not in ("string", "rich_text", "number", "range", "boolean", "choice"):
            fail(422, "INVALID_FIELD_TYPE", "Неизвестный тип поля.")
        if new_type == "choice":
            if new_mode not in ("single", "multiple"):
                fail(422, "INVALID_SELECTION_MODE", "Укажите single или multiple.")
        else:
            new_mode = None
        name = plain(body.get("name", old["name"]), 150)
        if not name:
            fail(422, "INVALID_FIELD_NAME", "Введите название поля.")
        siblings = allrows(c,
            "SELECT name FROM category_fields WHERE category_id=? AND id<>? AND active=1",
            (old["category_id"], field_id))
        if any(row["name"].casefold() == name.casefold() for row in siblings):
            fail(409, "DUPLICATE_FIELD", "Поле с таким названием уже существует в категории.")
        c.execute("UPDATE category_fields SET name=?,field_type=?,selection_mode=? WHERE id=?",
                  (name, new_type, new_mode, field_id))
        audit(c, current, "field.update", "category_field", field_id, old, body)
    return {"id": field_id}


@router.post("/category-fields/{field_id}/options")
def create_option(field_id: str, body: dict, current=Depends(actor)):
    _admin(current)
    with transaction() as c:
        one(c, "SELECT id FROM category_fields WHERE id=? AND field_type='choice'", (field_id,))
        name = plain(body.get("name"), 200)
        if not name:
            fail(422, "INVALID_OPTION", "Введите значение справочника.")
        siblings = allrows(c,"SELECT name FROM field_options WHERE field_id=?", (field_id,))
        if any(row["name"].casefold() == name.casefold() for row in siblings):
            fail(409, "DUPLICATE_OPTION", "Такое значение уже есть в справочнике.")
        oid = uid()
        c.execute("INSERT INTO field_options(id,field_id,name) VALUES(?,?,?)",
                  (oid, field_id, name))
        audit(c, current, "option.create", "field_option", oid, {}, body)
    return {"id": oid}


@router.patch("/field-options/{option_id}")
def update_option(option_id: str, body: dict, current=Depends(actor)):
    _admin(current)
    with transaction() as c:
        old = one(c, "SELECT * FROM field_options WHERE id=?", (option_id,))
        name = plain(body.get("name", old["name"]), 200)
        if not name:
            fail(422, "INVALID_OPTION", "Введите значение справочника.")
        siblings = allrows(c,
            "SELECT name FROM field_options WHERE field_id=? AND id<>?",
            (old["field_id"], option_id))
        if any(row["name"].casefold() == name.casefold() for row in siblings):
            fail(409, "DUPLICATE_OPTION", "Такое значение уже есть в справочнике.")
        if "active" in body and type(body["active"]) is not bool:
            fail(422, "INVALID_OPTION", "Доступность варианта должна быть логическим значением.")
        c.execute("UPDATE field_options SET name=?,active=? WHERE id=?",
                  (name, 1 if body.get("active", bool(old["active"])) else 0, option_id))
        audit(c, current, "option.update", "field_option", option_id, old, body)
    return {"id": option_id}


def _archive_preview(c, article_id=None, node_id=None):
    if bool(article_id) == bool(node_id):
        fail(422, "TARGET_REQUIRED", "Укажите ровно один article_id или node_id.")
    if node_id:
        node = one(c, "SELECT * FROM structure_nodes WHERE id=? AND status='active'", (node_id,))
        nodes, articles = _subtree(c, node_id)
        nodes = [n for n in nodes if n["status"] == "active"]
        articles = [a for a in articles if a["status"] == "active"]
    else:
        one(c, "SELECT id FROM articles WHERE id=? AND status='active'", (article_id,))
        nodes, articles = [], [one(c, "SELECT * FROM articles WHERE id=?", (article_id,))]
    snapshot = {"article_id": article_id, "node_id": node_id,
                "nodes": [(n["id"], n["status"]) for n in nodes],
                "articles": [(a["id"], a["revision_id"], a["status"]) for a in articles]}
    return {"node_ids": [n["id"] for n in nodes],
            "article_ids": [a["id"] for a in articles],
            "node_count": len(nodes), "article_count": len(articles),
            "preview_hash": _fingerprint(snapshot)}


@router.post("/archive/preview")
def archive_preview(body: dict, current=Depends(actor)):
    _admin(current)
    with read_db() as c:
        return _archive_preview(c, body.get("article_id"), body.get("node_id"))


@router.post("/archive-batches")
def archive_batch(body: dict, idempotency_key: str | None = Header(None), current=Depends(actor)):
    _admin(current)
    if body.get("confirm") is not True:
        fail(422, "CONFIRMATION_REQUIRED", "Для архивирования требуется подтверждение.")
    with transaction() as c:
        def do_archive():
            preview = _archive_preview(c, body.get("article_id"), body.get("node_id"))
            if preview["preview_hash"] != body.get("preview_hash"):
                fail(409, "PREVIEW_STALE", "Состав архива изменился.")
            batch_id = uid()
            c.execute("INSERT INTO archive_batches VALUES(?,?,?,?,?)",
                      (batch_id, body.get("node_id"), dumps(preview["article_ids"]),
                       dumps(preview["node_ids"]), ts()))
            for article_id in preview["article_ids"]:
                c.execute("UPDATE articles SET status='archived',archive_batch_id=?,updated_at=? WHERE id=?",
                          (batch_id, ts(), article_id))
                validate_relations(c, article_id)
            for node_id in preview["node_ids"]:
                c.execute("UPDATE structure_nodes SET status='archived',archive_batch_id=? WHERE id=?",
                          (batch_id, node_id))
            audit(c, current, "archive.batch", "archive_batch", batch_id, {}, preview)
            return {"id": batch_id, **preview}
        return idempotent(c, current, "archive", idempotency_key, body, do_archive)


def _restore_preview(c, batch_id):
    batch = one(c, "SELECT * FROM archive_batches WHERE id=?", (batch_id,))
    article_ids = loads(batch["article_ids"], [])
    node_ids = loads(batch["node_ids"], [])
    articles = [a for aid in article_ids if (a := maybe(c,
        "SELECT * FROM articles WHERE id=? AND status='archived' AND archive_batch_id=?",
        (aid, batch_id)))]
    nodes = [n for nid in node_ids if (n := maybe(c,
        "SELECT * FROM structure_nodes WHERE id=? AND status='archived' AND archive_batch_id=?",
        (nid, batch_id)))]
    conflicts = []
    for a in articles:
        other = maybe(c, """SELECT id,number,title FROM articles WHERE code_id=? AND number_key=?
                            AND status='active' AND id<>?""",
                      (a["code_id"], a["number_key"], a["id"]))
        if other:
            conflicts.append({"restoring_id": a["id"], "active_id": other["id"],
                              "number": a["number"]})
    snapshot = {"batch": batch_id, "articles": [(a["id"], a["revision_id"]) for a in articles],
                "nodes": [n["id"] for n in nodes], "conflicts": conflicts}
    return {"batch_id": batch_id, "article_ids": [a["id"] for a in articles],
            "node_ids": [n["id"] for n in nodes], "conflicts": conflicts,
            "preview_hash": _fingerprint(snapshot)}


@router.get("/archive-batches/{batch_id}/preview")
def restore_preview(batch_id: str, current=Depends(actor)):
    _admin(current)
    with read_db() as c:
        return _restore_preview(c, batch_id)


@router.post("/restore-batches")
def restore_batch(body: dict, idempotency_key: str | None = Header(None), current=Depends(actor)):
    _admin(current)
    if body.get("confirm") is not True:
        fail(422, "CONFIRMATION_REQUIRED", "Подтвердите восстановление.")
    with transaction() as c:
        def do_restore():
            preview = _restore_preview(c, body.get("batch_id"))
            if preview["preview_hash"] != body.get("preview_hash"):
                fail(409, "PREVIEW_STALE", "Состав восстановления изменился.")
            if preview["conflicts"] and body.get("confirm_swap") is not True:
                fail(409, "RESTORE_NUMBER_CONFLICT", "Нужна отдельная санкция на замену действующих статей.",
                     {"conflicts": preview["conflicts"]})
            for conflict in preview["conflicts"]:
                c.execute("UPDATE articles SET status='archived',updated_at=? WHERE id=?",
                          (ts(), conflict["active_id"]))
                validate_relations(c, conflict["active_id"])
            # Nodes retain their original IDs and structure; archived-before-batch items stay archived.
            for node_id in preview["node_ids"]:
                c.execute("UPDATE structure_nodes SET status='active',archive_batch_id=NULL WHERE id=?",
                          (node_id,))
            # Placement can be changed only for the archived root of a cascade, or
            # a standalone article. This never changes code identity or a revision.
            batch = one(c, "SELECT root_node_id FROM archive_batches WHERE id=?",
                        (body["batch_id"],))
            new_parent_provided = "new_parent_id" in body
            new_parent_id = body.get("new_parent_id")
            if new_parent_provided:
                if batch["root_node_id"]:
                    root = one(c, "SELECT * FROM structure_nodes WHERE id=?",
                               (batch["root_node_id"],))
                    if new_parent_id in preview["node_ids"]:
                        fail(422, "HIERARCHY_CYCLE", "Нельзя восстановить раздел внутри его же поддерева.")
                    validate_parent(c, root["code_id"], new_parent_id, root["type_id"])
                    conflict = maybe(c, """SELECT id FROM structure_nodes WHERE code_id=? AND
                            type_id=? AND parent_key=? AND number_key=? AND status='active'
                            AND id<>?""", (root["code_id"], root["type_id"],
                            new_parent_id or "ROOT", root["number_key"], root["id"]))
                    if conflict:
                        fail(409, "RESTORE_NODE_CONFLICT", "В выбранном разделе уже есть элемент с таким номером.",
                             {"node_id": conflict["id"]})
                    c.execute("""UPDATE structure_nodes SET parent_id=?,parent_key=? WHERE id=?""",
                              (new_parent_id, new_parent_id or "ROOT", root["id"]))
                elif len(preview["article_ids"]) == 1:
                    target = one(c, "SELECT code_id FROM articles WHERE id=?",
                                 (preview["article_ids"][0],))
                    validate_parent(c, target["code_id"], new_parent_id)
                else:
                    fail(422, "RESTORE_POSITION", "Нельзя изменить расположение нескольких независимых статей.")
            for article_id in preview["article_ids"]:
                a = one(c, "SELECT * FROM articles WHERE id=?", (article_id,))
                parent_id = new_parent_id if new_parent_provided and not batch["root_node_id"] else a["parent_id"]
                if parent_id:
                    one(c, "SELECT id FROM structure_nodes WHERE id=? AND status='active'",
                        (parent_id,))
                c.execute("""UPDATE articles SET status='active',parent_id=?,archive_batch_id=NULL,
                             updated_at=? WHERE id=?""", (parent_id, ts(), article_id))
            audit(c, current, "restore.batch", "archive_batch", body["batch_id"], {}, {
                **preview, "new_parent_id": new_parent_id if new_parent_provided else "unchanged"
            })
            return {"restored_articles": preview["article_ids"],
                    "restored_nodes": preview["node_ids"], "relations_reactivated": 0}
        return idempotent(c, current, "restore", idempotency_key, body, do_restore)


@router.get("/admin/archives")
def archive_inventory(code_id: str | None = None, current=Depends(actor)):
    """Small administrative archive inventory; no archived text sent to the reader."""
    _admin(current)
    with read_db() as c:
        sql = """SELECT b.id,b.root_node_id,b.created_at,b.article_ids,b.node_ids
                 FROM archive_batches b ORDER BY b.created_at DESC,b.id DESC LIMIT 200"""
        batches = allrows(c, sql)
        entries = []
        for batch in batches:
            article_ids = loads(batch.pop("article_ids"), [])
            node_ids = loads(batch.pop("node_ids"), [])
            if code_id:
                matching = c.execute("""SELECT 1 FROM articles WHERE id IN (
                    SELECT value FROM json_each(?)) AND code_id=? LIMIT 1""",
                    (dumps(article_ids), code_id)).fetchone()
                if not matching and node_ids:
                    matching = c.execute("""SELECT 1 FROM structure_nodes WHERE id IN (
                        SELECT value FROM json_each(?)) AND code_id=? LIMIT 1""",
                        (dumps(node_ids), code_id)).fetchone()
                if not matching:
                    continue
            batch["article_count"] = len(article_ids)
            batch["node_count"] = len(node_ids)
            batch["archived_articles"] = allrows(c, """SELECT id,code_id,number,title,parent_id
                FROM articles WHERE archive_batch_id=? AND status='archived'
                ORDER BY sort_key,id LIMIT 100""", (batch["id"],))
            batch["archived_nodes"] = allrows(c, """SELECT id,code_id,number,title,parent_id
                FROM structure_nodes WHERE archive_batch_id=? AND status='archived'
                ORDER BY sort_key,id LIMIT 100""", (batch["id"],))
            entries.append(batch)
        return entries


@router.get("/admin/audit")
def audit_log(limit: int = 100, current=Depends(actor)):
    _admin(current)
    with read_db() as c:
        return allrows(c, "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?",
                       (min(max(limit, 1), 200),))
