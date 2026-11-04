"""REQ-124: wide hierarchies can be fetched by cursor without a whole-code response."""


def api(path):
    return "/api/v1"+path


def test_structure_index_paginates_direct_children_and_is_scope_safe(client,headers):
    admin=headers["superadmin"]
    reader=headers["user"]
    created=[]
    for n in range(8):
        response=client.post(api("/codes/demo-penal/nodes"),headers=admin,json={
            "type_id":"demo-chapter","parent_id":None,
            "number":str(n+1),"title":"Корневой раздел "+str(n+1)})
        assert response.status_code==200,response.text
        created.append(response.json()["id"])
    seen=[]
    cursor=None
    for _ in range(8):
        params={"limit":3}
        if cursor:params["cursor"]=cursor
        response=client.get(api("/codes/demo-penal/structure-index"),
                            headers=reader,params=params)
        assert response.status_code==200,response.text
        page=response.json()
        assert len(page["items"])<=3
        assert all(set(item)=={
            "id","code_id","parent_id","type_id","type_name","number","title",
            "child_count"} for item in page["items"])
        assert all(item["parent_id"] is None for item in page["items"])
        assert all(item["code_id"]=="demo-penal" for item in page["items"])
        seen.extend(x["id"] for x in page["items"])
        cursor=page["next_cursor"]
        if not page["has_more"]:
            assert cursor is None
            break
    else:
        raise AssertionError("Hierarchy index failed to reach the end")
    assert len(seen)==9 # includes seeded chapter
    assert len(set(seen))==9
    assert set(created).issubset(set(seen))

    child=client.get(api("/codes/demo-penal/structure-index"),headers=reader,
                     params={"parent_id":"demo-intro"})
    assert child.status_code==200
    assert child.json()["items"]==[]
    assert client.get(api("/codes/demo-penal/structure-index"),
                      headers=reader,params={"cursor":"notbase64"}).status_code==422
    assert client.get(api("/codes/demo-penal/structure-index"),
                      headers=reader,params={"parent_id":"unknown"}).status_code==404
    assert client.get(api("/codes/demo-penal/structure-index"),
                      headers=reader,params={"limit":101}).status_code==422
    assert client.get(api("/codes/demo-penal/structure-index")).status_code==401
