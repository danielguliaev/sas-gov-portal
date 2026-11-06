"""Mixed code outline: permissions, ordering, migration and reading links."""


def api(path):
    return "/api/v1" + path


def make_article(client, auth, number, **options):
    response = client.post(api("/articles"), headers=auth, json={
        "code_id": "PEN", "number": number, "title": "Норма " + number,
        "categories": ["definition"], "text_rich": "<p>Норма права.</p>",
        **options
    })
    assert response.status_code == 200, response.text
    return response.json()


def root_outline(client, auth):
    response = client.get(api("/codes/PEN/outline"), headers=auth,
                          params={"limit": 200})
    assert response.status_code == 200, response.text
    return response.json()


def test_description_can_be_edited_only_by_admin(client, headers):
    user, admin = headers["user"], headers["superadmin"]
    old = next(c for c in client.get(api("/codes"), headers=user).json()
               if c["id"] == "PEN")
    assert old["description"] == ""
    blocked = client.patch(api("/codes/PEN"), headers=user,
                           json={"description": "Текст читателя"})
    assert blocked.status_code == 403
    changed = client.patch(api("/codes/PEN"), headers=admin,
                           json={"description": "Общие положения уголовного закона."})
    assert changed.status_code == 200, changed.text
    assert changed.json()["description"] == "Общие положения уголовного закона."
    code = next(c for c in client.get(api("/codes"), headers=user).json()
                if c["id"] == "PEN")
    assert code["description"] == "Общие положения уголовного закона."
    assert code["abbreviation"] == old["abbreviation"]
    oversized = client.patch(api("/codes/PEN"), headers=admin,
                             json={"description": "x" * 3001})
    assert oversized.status_code == 422


def test_root_interleaving_and_adjacency_survive_reloads(client, headers):
    admin, user = headers["superadmin"], headers["user"]
    initial = root_outline(client, user)
    node = next(item for item in initial["items"] if item["kind"] == "node")
    first = make_article(client, admin, "980", insert_at=0)
    last = make_article(client, admin, "981")
    rows = root_outline(client, user)["items"]
    assert [(x["kind"], x["id"]) for x in rows] == [
        ("article", first["id"]), ("node", node["id"]), ("article", last["id"])
    ]
    assert root_outline(client, user)["article_count"] == 2
    assert client.put(api("/codes/PEN/outline"), headers=user, json={
        "items": [{"kind": r["kind"], "id": r["id"]} for r in rows]
    }).status_code == 403
    new_order = [{"kind": "article", "id": first["id"]},
                 {"kind": "node", "id": node["id"]},
                 {"kind": "article", "id": last["id"]}]
    changed = client.put(api("/codes/PEN/outline"), headers=admin,
                         json={"parent_id": None, "items": new_order})
    assert changed.status_code == 200, changed.text
    assert [(x["kind"], x["id"]) for x in root_outline(client, user)["items"]] == [
        (i["kind"], i["id"]) for i in new_order
    ]
    # A section's article is visited before the next root article.
    inside = client.get(api("/nodes/demo-intro/article-index"), headers=user).json()
    assert len(inside["items"]) == 1
    demo_article = inside["items"][0]["id"]
    assert client.get(api("/articles/" + first["id"] + "/adjacent"),
                      headers=user).json()["next"]["id"] == demo_article
    assert client.get(api("/articles/" + last["id"] + "/adjacent"),
                      headers=user).json()["previous"]["id"] == demo_article
    position = client.get(api("/articles/" + last["id"] + "/position"),
                          headers=user).json()
    assert position["outline_index"] == 2
    assert position["outline_cursor_before"] is None
    page = client.get(api("/codes/PEN/outline"), headers=user,
                      params={"include_articles": True, "limit": 2}).json()
    assert len(page["items"]) == 2
    assert page["has_more"] is True
    assert page["items"][0]["article"]["id"] == first["id"]
    final = client.get(api("/codes/PEN/outline"), headers=user,
                       params={"cursor": page["next_cursor"], "limit": 2}).json()
    assert [x["id"] for x in final["items"]] == [last["id"]]
    assert not final["has_more"]
    assert client.get(api("/codes/PEN/outline"), headers=user,
                      params={"cursor": "nonsense"}).status_code == 422


def test_reorder_validates_complete_scope_and_restores_auto(client, headers):
    admin = headers["superadmin"]
    start = root_outline(client, admin)["items"]
    a = make_article(client, admin, "982")
    rows = root_outline(client, admin)["items"]
    requested = [{"kind": x["kind"], "id": x["id"]} for x in rows]
    missing = client.put(api("/codes/PEN/outline"), headers=admin,
                         json={"items": requested[:-1]})
    assert missing.status_code == 409
    duplicated = client.put(api("/codes/PEN/outline"), headers=admin,
                            json={"items": requested + [requested[0]]})
    assert duplicated.status_code == 422
    invalid = client.put(api("/codes/PEN/outline"), headers=admin,
                         json={"items": [{"kind": "article", "id": "unknown"}]})
    assert invalid.status_code == 409
    reset = client.post(api("/codes/PEN/outline/reset"), headers=admin,
                        json={"parent_id": None})
    assert reset.status_code == 200, reset.text
    restored = root_outline(client, admin)["items"]
    assert restored[-1]["id"] == a["id"]
    assert restored[0]["kind"] == "node"
    assert len(restored) == len(start) + 1


def test_nested_mixed_order_is_scoped_to_selected_node(client, headers):
    admin, user = headers["superadmin"], headers["user"]
    tree = client.get(api("/codes/PEN/tree"), headers=admin).json()
    parent_type = next(n for n in tree["nodes"] if n["id"] == "demo-intro")["type_id"]
    kind = client.post(api("/codes/PEN/hierarchy-types"), headers=admin,
                       json={"name": "Подраздел для порядка", "level_order": 15,
                             "allowed_parents": [parent_type]})
    assert kind.status_code == 200, kind.text
    child = client.post(api("/codes/PEN/nodes"), headers=admin, json={
        "type_id": kind.json()["id"], "parent_id": "demo-intro",
        "number": "2", "title": "Середина"
    })
    assert child.status_code == 200, child.text
    first = make_article(client, admin, "983", parent_id="demo-intro", insert_at=0)
    last = make_article(client, admin, "984", parent_id="demo-intro")
    before = root_outline(client, user)["items"]
    nested = client.get(api("/codes/PEN/outline"), headers=user,
                        params={"parent_id": "demo-intro"}).json()["items"]
    assert nested[0]["id"] == first["id"]
    assert nested[-1]["id"] == last["id"]
    ordered = [dict(kind="article", id=first["id"]),
               dict(kind="node", id=child.json()["id"])]
    ordered.extend(dict(kind=x["kind"], id=x["id"])
                   for x in nested if x["id"] not in
                   (first["id"], child.json()["id"], last["id"]))
    ordered.append(dict(kind="article", id=last["id"]))
    assert client.put(api("/codes/PEN/outline"), headers=admin,json={
        "parent_id": "demo-intro", "items": ordered
    }).status_code == 200
    after = root_outline(client, user)["items"]
    assert [(x["kind"], x["id"]) for x in before] == [
        (x["kind"], x["id"]) for x in after]
