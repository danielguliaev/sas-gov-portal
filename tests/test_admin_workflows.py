"""End-to-end API contracts for the previously missing library administration functions."""
import app.core as core


def url(path):
    return "/api/v1" + path


def test_revision_freezes_category_field_and_option_names(client, headers, admin):
    auth=headers["superadmin"]
    field=client.post(url("/categories/definition/fields"),headers=auth,json={
        "name":"Условия применения","field_type":"choice","selection_mode":"single"})
    assert field.status_code==200,field.text
    fid=field.json()["id"]
    option=client.post(url("/category-fields/"+fid+"/options"),headers=auth,
        json={"name":"Первоначальное значение"})
    oid=option.json()["id"]
    response=client.post(url("/articles"),headers=auth,json={
        "code_id":"demo-penal","parent_id":None,"number":"801",
        "title":"Статья с атрибутом","categories":["definition"],
        "custom_values":{fid:oid},"text_rich":"<p>Текст</p>"})
    assert response.status_code==200,response.text
    first=response.json()
    previous=client.get(url("/articles/"+first["id"]+"/revisions/"+first["revision_id"]),
                        headers=headers["lawyer"])
    snapshot=previous.json()["display_snapshot"]
    assert snapshot["categories"]["definition"]=="Определение"
    assert snapshot["fields"][fid]["name"]=="Условия применения"
    assert snapshot["fields"][fid]["value"]["name"]=="Первоначальное значение"
    updated=client.patch(url("/categories/definition"),headers=auth,
                         json={"name":"Толкование"})
    assert updated.status_code==200,updated.text
    updated=client.patch(url("/field-options/"+oid),headers=auth,
                         json={"name":"Переименовано"})
    assert updated.status_code==200,updated.text
    previous=client.get(url("/articles/"+first["id"]+"/revisions/"+first["revision_id"]),
                        headers=headers["lawyer"]).json()
    assert previous["display_snapshot"]["fields"][fid]["value"]["name"]=="Первоначальное значение"
    lock=client.post(url("/articles/"+first["id"]+"/edit-lock"),headers=auth,
                     json={"editor_session_id":"snapshot"})
    assert lock.status_code==200
    next_rev=client.post(url("/articles/"+first["id"]+"/revisions"),headers=auth,json={
        "editor_session_id":"snapshot","lock_token":lock.json()["token"],
        "base_revision_id":first["revision_id"],"text_rich":"<p>Новая редакция</p>"})
    assert next_rev.status_code==200,next_rev.text
    current=client.get(url("/articles/"+first["id"]+"/revisions/"+next_rev.json()["revision_id"]),
                       headers=headers["lawyer"]).json()
    assert current["display_snapshot"]["fields"][fid]["value"]["name"]=="Переименовано"
    assert current["display_snapshot"]["categories"]["definition"]=="Толкование"


def test_typed_field_search_and_history(client,headers,admin):
    auth=headers["superadmin"]
    multiple=client.post(url("/categories/definition/fields"),headers=auth,json={
        "name":"Признаки","field_type":"choice","selection_mode":"multiple"}).json()["id"]
    o1=client.post(url("/category-fields/"+multiple+"/options"),headers=auth,
                   json={"name":"Вариант А"}).json()["id"]
    o2=client.post(url("/category-fields/"+multiple+"/options"),headers=auth,
                   json={"name":"Вариант Б"}).json()["id"]
    new=client.post(url("/articles"),headers=auth,json={
        "code_id":"demo-penal","number":"805","title":"Особая редакция",
        "categories":["definition"],"custom_values":{multiple:[o1,o2]},
        "text_rich":"<p>Редкий исторический оборот</p>"})
    assert new.status_code==200,new.text
    aid=new.json()["id"]
    filtered=client.get(url("/search"),headers=headers["lawyer"],
                        params={"field_id":multiple,"field_value":o2})
    assert filtered.status_code==200,filtered.text
    assert aid in [row["id"] for row in filtered.json()["items"]]
    basic=client.get(url("/search"),headers=headers["user"],
                     params={"field_id":multiple,"field_value":o2})
    assert basic.status_code==403
    revisions=client.get(url("/search/revisions"),headers=headers["lawyer"],
                         params={"query":"Редкий исторический"})
    assert revisions.status_code==200,revisions.text
    assert aid in [r["article_id"] for r in revisions.json()["items"]]
    assert client.get(url("/search/revisions"),headers=headers["user"],
                      params={"query":"Редкий"}).status_code==403


def test_restore_can_choose_another_valid_node_location(client,headers,admin):
    auth=headers["superadmin"]
    type_id=client.post(url("/codes/demo-penal/hierarchy-types"),headers=auth,json={
        "name":"Дополнительный раздел","allowed_parents":["ROOT"],
        "level_order":1}).json()["id"]
    parent=client.post(url("/codes/demo-penal/nodes"),headers=auth,json={
        "type_id":type_id,"number":"3","title":"Новая группа"}).json()["id"]
    article=admin("806",parent="demo-intro")
    original=article["revision_id"]
    plan=client.post(url("/archive/preview"),headers=auth,
                     json={"article_id":article["id"]}).json()
    batch=client.post(url("/archive-batches"),
        headers={**auth,"Idempotency-Key":"location-archive"},
        json={"article_id":article["id"],"confirm":True,
              "preview_hash":plan["preview_hash"]})
    assert batch.status_code==200,batch.text
    inventory=client.get(url("/admin/archives"),headers=auth)
    assert inventory.status_code==200
    assert batch.json()["id"] in [b["id"] for b in inventory.json()]
    assert client.get(url("/admin/archives"),headers=headers["lawyer"]).status_code==403
    restore_plan=client.get(url("/archive-batches/"+batch.json()["id"]+"/preview"),
                            headers=auth).json()
    restored=client.post(url("/restore-batches"),
        headers={**auth,"Idempotency-Key":"location-restore"},json={
        "batch_id":batch.json()["id"],"confirm":True,
        "preview_hash":restore_plan["preview_hash"],"new_parent_id":parent})
    assert restored.status_code==200,restored.text
    actual=client.get(url("/articles/"+article["id"]),headers=headers["lawyer"]).json()
    assert actual["parent_id"]==parent
    assert actual["revision_id"]==original


def test_restore_nested_subtree_blocks_own_descendant(client,headers,admin):
    auth=headers["superadmin"]
    kind=client.post(url("/codes/demo-penal/hierarchy-types"),headers=auth,json={
        "name":"Вложенный тип","allowed_parents":["ROOT","demo-chapter"]}).json()["id"]
    child=client.post(url("/codes/demo-penal/nodes"),headers=auth,json={
        "type_id":kind,"parent_id":"demo-intro","number":"2",
        "title":"Вложенный элемент"}).json()["id"]
    # Archive a root that contains the nested element; self-placement must fail.
    plan=client.post(url("/archive/preview"),headers=auth,
                     json={"node_id":"demo-intro"}).json()
    batch=client.post(url("/archive-batches"),
        headers={**auth,"Idempotency-Key":"subtree-archive"},
        json={"node_id":"demo-intro","confirm":True,
              "preview_hash":plan["preview_hash"]}).json()["id"]
    restore_plan=client.get(url("/archive-batches/"+batch+"/preview"),headers=auth).json()
    error=client.post(url("/restore-batches"),
        headers={**auth,"Idempotency-Key":"subtree-restore"},
        json={"batch_id":batch,"confirm":True,"preview_hash":restore_plan["preview_hash"],
              "new_parent_id":child})
    assert error.status_code==422,error.text
    assert error.json()["error"]["code"]=="HIERARCHY_CYCLE"
    assert client.get(url("/nodes/"+child+"/article-index"),
                      headers=headers["user"]).status_code==404


def test_readonly_service_api_has_separate_auth_and_no_private_data(client,headers,admin,monkeypatch):
    import hashlib
    key="random-key-for-integration-test"
    monkeypatch.setenv("SERVICE_API_KEY_SHA256",hashlib.sha256(key.encode()).hexdigest())
    endpoint=url("/integration/articles/demo-article-1")
    assert client.get(endpoint,headers=headers["superadmin"]).status_code==401
    wrong=client.get(endpoint,headers={"Authorization":"Bearer wrong"})
    assert wrong.status_code==401
    response=client.get(endpoint,headers={"Authorization":"Bearer "+key})
    assert response.status_code==200,response.text
    item=response.json()
    assert item["id"]=="demo-article-1"
    assert "criminal_class" in item and "sanctions" in item and "relations" in item
    assert "annotations" not in item and "audit_events" not in item
    pages=client.get(url("/integration/articles"),params={"code_id":"demo-penal"},
                     headers={"Authorization":"Bearer "+key})
    assert pages.status_code==200
    assert pages.json()["items"]
    assert client.post(url("/articles"),json={"code_id":"demo-penal","number":"900"},
                       headers={"Authorization":"Bearer "+key}).status_code==401


def test_throttle_returns_429_and_security_headers(client,headers,monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE","2")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS","10")
    from app.main import _rate_windows
    _rate_windows.clear()
    urlpath=url("/codes")
    assert client.get(urlpath,headers=headers["user"]).status_code==200
    second=client.get(urlpath,headers=headers["user"])
    assert second.status_code==200
    assert second.headers["X-Content-Type-Options"]=="nosniff"
    assert second.headers["Cache-Control"]=="no-store"
    third=client.get(urlpath,headers=headers["user"])
    assert third.status_code==429
    assert third.json()["error"]["code"]=="RATE_LIMITED"
    monkeypatch.delenv("RATE_LIMIT_PER_MINUTE")
    _rate_windows.clear()
