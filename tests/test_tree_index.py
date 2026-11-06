"""REQ-124: wide hierarchies can be fetched by cursor without a whole-code response."""


def api(path):
    return "/api/v1"+path


def test_structure_index_paginates_direct_children_and_is_scope_safe(client,headers):
    admin=headers["superadmin"]
    reader=headers["user"]
    created=[]
    for n in range(8):
        response=client.post(api("/codes/PEN/nodes"),headers=admin,json={
            "type_id":"demo-chapter","parent_id":None,
            "number":str(n+1),"title":"Корневой раздел "+str(n+1)})
        assert response.status_code==200,response.text
        created.append(response.json()["id"])
    seen=[]
    cursor=None
    for _ in range(8):
        params={"limit":3}
        if cursor:params["cursor"]=cursor
        response=client.get(api("/codes/PEN/structure-index"),
                            headers=reader,params=params)
        assert response.status_code==200,response.text
        page=response.json()
        assert len(page["items"])<=3
        assert all(set(item)=={
            "id","code_id","parent_id","type_id","type_name","number","title",
            "child_count"} for item in page["items"])
        assert all(item["parent_id"] is None for item in page["items"])
        assert all(item["code_id"]=="PEN" for item in page["items"])
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

    child=client.get(api("/codes/PEN/structure-index"),headers=reader,
                     params={"parent_id":"demo-intro"})
    assert child.status_code==200
    assert child.json()["items"]==[]
    assert client.get(api("/codes/PEN/structure-index"),
                      headers=reader,params={"cursor":"notbase64"}).status_code==422
    assert client.get(api("/codes/PEN/structure-index"),
                      headers=reader,params={"parent_id":"unknown"}).status_code==404
    assert client.get(api("/codes/PEN/structure-index"),
                      headers=reader,params={"limit":101}).status_code==422
    assert client.get(api("/codes/PEN/structure-index")).status_code==401


def test_reader_skeleton_and_node_context_avoid_whole_code(client,headers):
    user=headers["user"]
    skeleton=client.get(api("/codes/PEN/tree"),
                        params={"include_nodes":"false"},headers=user)
    assert skeleton.status_code==200,skeleton.text
    data=skeleton.json()
    assert data["code"]["id"]=="PEN"
    assert data["types"]
    assert data["nodes"]==[]
    context=client.get(api("/codes/PEN/node-context"),
                       params={"node_id":"demo-intro"},headers=user)
    assert context.status_code==200,context.text
    result=context.json()
    assert result["node"]["id"]=="demo-intro"
    assert [item["id"] for item in result["ancestors"]]==["demo-intro"]
    assert client.get(api("/codes/PEN/node-context"),
        params={"node_id":"wrong"},headers=user).status_code==404
    assert client.get(api("/codes/PEN/node-context"),
        params={"node_id":"demo-intro"}).status_code==401


def test_wide_code_tree_is_paged_without_bulk_structure_download(client,headers):
    """600 sibling nodes exercise cursor boundaries without returning all nodes."""
    from app.core import transaction
    from app.core import number_sort_key
    with transaction() as c:
        for number in range(1,601):
            key=str(number)
            c.execute("""INSERT INTO structure_nodes
                (id,code_id,type_id,parent_id,parent_key,number,number_key,
                 sort_key,title,status)
                 VALUES(?,?,?,?,?,?,?,?,?,'active')""",
                ("wide-chapter-"+key,"PEN","demo-chapter",None,"ROOT",
                 key,key,number_sort_key(key,"mixed"),"Глава "+key))
    reader=headers["user"]
    baseline=client.get(api("/codes/PEN/tree"),headers=reader,
                        params={"include_nodes":"false"})
    assert baseline.status_code==200
    assert baseline.json()["nodes"]==[]

    seen=set()
    cursor=None
    page_count=0
    while True:
        params={"limit":50}
        if cursor:
            params["cursor"]=cursor
        response=client.get(api("/codes/PEN/structure-index"),
                            headers=reader,params=params)
        assert response.status_code==200,response.text
        data=response.json()
        assert len(data["items"])<=50
        for item in data["items"]:
            assert item["id"] not in seen
            assert item["child_count"]==0
            seen.add(item["id"])
        page_count+=1
        if not data["has_more"]:
            assert data["next_cursor"] is None
            break
        cursor=data["next_cursor"]
        assert cursor and page_count<20
    assert len(seen)==601
    assert page_count==13
    assert "wide-chapter-600" in seen
