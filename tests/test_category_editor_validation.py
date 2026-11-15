"""User-facing category and field validation must match the redesigned editor."""


def api(value):
    return "/api/v1" + value


def test_category_field_and_option_names_are_nonblank_and_unique(client, headers):
    admin = headers["superadmin"]
    reader = headers["user"]
    category_route = api("/categories")

    assert client.post(category_route, headers=reader, json={"name":"Временная категория"}).status_code == 403
    for blank in ("", "   "):
        result = client.post(category_route, headers=admin, json={"name": blank})
        assert result.status_code == 422, result.text

    new = client.post(category_route, headers=admin, json={"name": "Учебная категория", "code_id":None})
    assert new.status_code == 200, new.text
    cat_id = new.json()["id"]
    duplicate = client.post(category_route, headers=admin, json={"name":"учебная категория"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "DUPLICATE_CATEGORY"
    rename_conflict = client.patch(category_route + "/definition", headers=admin,
                                   json={"name":"учебная категория"})
    assert rename_conflict.status_code == 409

    fields_route = api("/categories/"+cat_id+"/fields")
    for blank in ("", " \t "):
        assert client.post(fields_route, headers=admin, json={
            "name":blank,"field_type":"string"}).status_code == 422
    field = client.post(fields_route, headers=admin,json={
        "name":"Вариант характеристики", "field_type":"choice", "selection_mode":"multiple"})
    assert field.status_code == 200, field.text
    fid = field.json()["id"]
    assert client.post(fields_route, headers=admin,json={
        "name":"вариант характеристики","field_type":"string"}).status_code == 409

    opt_route = api("/category-fields/"+fid+"/options")
    for blank in ("", " \t "):
        assert client.post(opt_route, headers=admin, json={"name":blank}).status_code==422
    option = client.post(opt_route, headers=admin,json={"name":"Первый"})
    assert option.status_code == 200
    oid = option.json()["id"]
    assert client.post(opt_route, headers=admin,json={"name":"первый"}).status_code == 409
    assert client.patch(api("/field-options/"+oid),headers=admin,
                        json={"name":"", "active":False}).status_code == 422
    assert client.patch(api("/field-options/"+oid),headers=admin,
                        json={"name":"Первый", "active":"false"}).status_code == 422
    result = client.patch(api("/field-options/"+oid),headers=admin,
                          json={"name":"Исправленный", "active":False})
    assert result.status_code == 200
    current = next(c for c in client.get(category_route,headers=admin).json()
                   if c["id"] == cat_id)
    options=current["fields"][0]["options"]
    assert len(options) == 1
    assert options[0]["name"] == "Исправленный"
    assert options[0]["active"] == 0


def test_category_name_is_unique_only_within_its_scope(client, headers):
    admin = headers["superadmin"]
    body = {"name":"Тематические разъяснения"}
    global_cat = client.post(api("/categories"),headers=admin,json=body)
    assert global_cat.status_code == 200
    scoped = client.post(api("/categories"),headers=admin,json={
        **body,"code_id":"demo-penal"})
    assert scoped.status_code == 200
    duplicate_scoped = client.post(api("/categories"),headers=admin,json={
        "name":"ТЕМАТИЧЕСКИЕ РАЗЪЯСНЕНИЯ","code_id":"demo-penal"})
    assert duplicate_scoped.status_code == 409
