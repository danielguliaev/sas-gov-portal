"""Regression tests for structural operations and stable reading positions."""


def route(path):
    return "/api/v1" + path


def test_hierarchy_retire_lifts_children_without_changing_article_revision(client, headers, admin):
    auth = headers["superadmin"]
    parent_type = client.post(route("/codes/PEN/hierarchy-types"), json={
        "name": "Раздел для исключения", "level_order": 2, "allowed_parents": ["ROOT"]
    }, headers=auth)
    assert parent_type.status_code == 200, parent_type.text
    parent_type_id = parent_type.json()["id"]
    child_type = client.post(route("/codes/PEN/hierarchy-types"), json={
        "name": "Подраздел для переноса", "level_order": 3,
        "allowed_parents": ["ROOT", parent_type_id]
    }, headers=auth)
    assert child_type.status_code == 200, child_type.text
    child_type_id = child_type.json()["id"]
    parent = client.post(route("/codes/PEN/nodes"), json={
        "type_id": parent_type_id, "number": "A", "title": "Исходный раздел"
    }, headers=auth)
    assert parent.status_code == 200, parent.text
    parent_id = parent.json()["id"]
    child = client.post(route("/codes/PEN/nodes"), json={
        "type_id": child_type_id, "parent_id": parent_id,
        "number": "B", "title": "Подраздел"
    }, headers=auth)
    assert child.status_code == 200, child.text
    child_id = child.json()["id"]
    article = admin("820", parent=parent_id)
    preview = client.get(route("/hierarchy-types/" + parent_type_id + "/retire/preview"),
                         headers=auth)
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["conflicts"] == []
    assert any(x["id"] == child_id and x["new_parent_id"] is None for x in plan["moved_nodes"])
    assert any(x["id"] == article["id"] and x["new_parent_id"] is None for x in plan["moved_articles"])
    assert client.post(route("/hierarchy-types/" + parent_type_id + "/retire"),
                       headers=auth,json={"confirm": False,
                                          "preview_hash": plan["preview_hash"]}).status_code == 422
    applied = client.post(route("/hierarchy-types/" + parent_type_id + "/retire"),
                          headers=auth,json={"confirm": True,
                                             "preview_hash": plan["preview_hash"]})
    assert applied.status_code == 200, applied.text
    changed = client.get(route("/articles/" + article["id"]), headers=headers["user"])
    assert changed.status_code == 200
    assert changed.json()["revision_id"] == article["revision_id"]
    assert changed.json()["parent_id"] is None
    tree = client.get(route("/codes/PEN/tree"), headers=auth).json()
    assert child_id in {x["id"] for x in tree["nodes"] if x["parent_id"] is None}
    assert parent_id not in {x["id"] for x in tree["nodes"]}


def test_roman_order_and_manual_renumbering(client, headers):
    h = headers["superadmin"]
    typ = client.post(route("/codes/PEN/hierarchy-types"), json={
        "name": "Римский тип", "level_order": 1,
        "allowed_parents": ["ROOT"], "number_format": "roman"
    }, headers=h)
    assert typ.status_code == 200,typ.text
    t = typ.json()["id"]
    ids = []
    for number in ("IV", "IX", "V"):
        node = client.post(route("/codes/PEN/nodes"), json={
            "type_id": t, "number": number, "title": "Римский раздел " + number
        }, headers=h)
        assert node.status_code == 200,node.text
        ids.append(node.json()["id"])
    order = client.post(route("/nodes/order"), json={
        "code_id":"PEN","type_id":t,"parent_id":None,
        "mode":"manual","ordered_ids":ids
    },headers=h)
    assert order.status_code == 200,order.text
    new_number = client.patch(route("/nodes/" + ids[0]), json={"number": "X"},
                              headers=h)
    assert new_number.status_code == 200,new_number.text
    tree = client.get(route("/codes/PEN/tree"), headers=h).json()
    nodes = [x for x in tree["nodes"] if x["type_id"] == t]
    assert [x["id"] for x in nodes] == ids
    reset = client.post(route("/nodes/order"), json={
        "code_id":"PEN","type_id":t,"parent_id":None,"mode":"auto"
    },headers=h)
    assert reset.status_code == 200,reset.text
    tree = client.get(route("/codes/PEN/tree"), headers=h).json()
    nodes = [x for x in tree["nodes"] if x["type_id"] == t]
    assert [x["number"] for x in nodes] == ["V", "IX", "X"]


def test_large_chapter_position_returns_seek_cursor(client, headers, admin):
    articles = [admin(str(i)) for i in range(300, 336)]
    target = articles[27]
    position = client.get(route("/articles/" + target["id"] + "/position"),
                          headers=headers["user"])
    assert position.status_code == 200, position.text
    data = position.json()
    assert data["cursor_before"]
    page = client.get(route("/nodes/demo-intro/articles"), headers=headers["user"],
                      params={"cursor":data["cursor_before"],"limit":12})
    assert page.status_code == 200,page.text
    assert target["id"] in [x["id"] for x in page.json()["items"]]


def test_unused_custom_field_can_change_type_and_used_cannot(client, headers):
    h = headers["superadmin"]
    field = client.post(route("/categories/definition/fields"), headers=h,json={
        "name":"Вспомогательный атрибут","field_type":"string"
    })
    assert field.status_code == 200,field.text
    field_id = field.json()["id"]
    updated = client.patch(route("/category-fields/" + field_id), headers=h,json={
        "field_type":"choice","selection_mode":"single"
    })
    assert updated.status_code == 200,updated.text
    opt = client.post(route("/category-fields/" + field_id + "/options"),
                      headers=h,json={"name":"Значение"})
    assert opt.status_code == 200,opt.text
    created = client.post(route("/articles"), headers=h, json={
        "code_id":"PEN","number":"941","title":"Пользовательские поля",
        "categories":["definition"],"text_rich":"<p>Демонстрация</p>",
        "custom_values":{field_id:opt.json()["id"]}
    })
    assert created.status_code == 200,created.text
    refused = client.patch(route("/category-fields/" + field_id), headers=h,json={
        "field_type":"number"
    })
    assert refused.status_code == 409
    article = created.json()
    lock = client.post(route("/articles/" + article["id"] + "/edit-lock"),
                       headers=h,json={"editor_session_id":"field-edit"})
    assert lock.status_code == 200,lock.text
    change = client.post(route("/articles/" + article["id"] + "/revisions"),headers=h,json={
        "base_revision_id":article["revision_id"],
        "lock_token":lock.json()["token"],"editor_session_id":"field-edit",
        "categories":[], "reason":"Снятие категории"
    })
    assert change.status_code == 200,change.text
    assert change.json()["custom_values"] == {}
    previous = client.get(route("/articles/" + article["id"] + "/revisions/" +
                                 article["revision_id"]),headers=headers["lawyer"])
    assert previous.status_code == 200
    assert previous.json()["custom_values"][field_id] == opt.json()["id"]


def test_exclusive_relation_rule_change_is_transactional(client, headers, admin):
    h = headers["superadmin"]
    kind = client.post(route("/relation-types"), headers=h, json={
        "name":"Экспериментальная связь","source_category":"accusation",
        "target_category":"punishment_measure","exclusive_target":False
    })
    assert kind.status_code == 200, kind.text
    kind_id = kind.json()["id"]
    first = admin("951",cats=["accusation"],crime="felony")
    second = admin("952",cats=["accusation"],crime="felony")
    punishment = admin("953",sanctions={"fine_min_usd":0,"fine_max_usd":50})
    def create_link(source):
        return client.post(route("/relations"), headers=h,json={
            "type_id":kind_id,"source_id":source["id"],"target_id":punishment["id"]
        })
    r1 = create_link(first)
    r2 = create_link(second)
    assert r1.status_code == 200,r1.text
    assert r2.status_code == 200,r2.text
    conflict = client.patch(route("/relation-types/" + kind_id),headers=h,
                            json={"exclusive_target":True})
    assert conflict.status_code == 409, conflict.text
    disabled = client.post(route("/relations/" + r2.json()["id"] + "/deactivate"),
                           headers=h,json={"reason":"Тестовое отключение"})
    assert disabled.status_code == 200,disabled.text
    updated = client.patch(route("/relation-types/" + kind_id),headers=h,
                           json={"exclusive_target":True})
    assert updated.status_code == 200,updated.text
    repeat = create_link(second)
    assert repeat.status_code == 409


def test_unnumbered_root_sections_coexist_with_numbered_parts(client, headers, admin):
    """Introductory pages and Part I are siblings, without synthetic numbering."""
    auth = headers["superadmin"]
    introductory = client.post(route("/codes/PEN/hierarchy-types"), json={
        "name": "Вводный раздел", "level_order": 0, "number_format": "none",
        "allowed_parents": ["ROOT"],
    }, headers=auth)
    assert introductory.status_code == 200, introductory.text
    intro_type = introductory.json()["id"]

    part_type = client.post(route("/codes/PEN/hierarchy-types"), json={
        "name": "Часть", "level_order": 1, "number_format": "roman",
        "allowed_parents": ["ROOT"],
    }, headers=auth)
    assert part_type.status_code == 200, part_type.text

    intro_ids = []
    for title in ("Название кодекса", "Предварительные положения"):
        created = client.post(route("/codes/PEN/nodes"), json={
            "type_id": intro_type, "parent_id": None, "title": title, "number": "",
        }, headers=auth)
        assert created.status_code == 200, created.text
        intro_ids.append(created.json()["id"])

    part = client.post(route("/codes/PEN/nodes"), json={
        "type_id": part_type.json()["id"], "parent_id": None,
        "title": "О преступлениях и наказаниях", "number": "I",
    }, headers=auth)
    assert part.status_code == 200, part.text

    index = client.get(route("/codes/PEN/structure-index"),
                       headers=headers["user"]).json()["items"]
    relevant = [n for n in index if n["id"] in set(intro_ids + [part.json()["id"]])]
    assert [n["title"] for n in relevant] == [
        "Название кодекса", "Предварительные положения", "О преступлениях и наказаниях"]
    assert [n["number"] for n in relevant] == ["", "", "I"]

    # Renaming retains the ID and does not make an implicit number appear.
    renamed = client.patch(route("/nodes/" + intro_ids[0]), json={
        "title": "Название Penal Code"}, headers=auth)
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["number"] == ""

    # Legal articles can be attached to unnumbered sections.
    article = admin("850", parent=intro_ids[1])
    assert article["parent_id"] == intro_ids[1]

    numbered = client.post(route("/codes/PEN/nodes"), json={
        "type_id": intro_type, "title": "Недопустимый номер", "number": "0",
    }, headers=auth)
    assert numbered.status_code == 422
