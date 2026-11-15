"""Code creation: clear validation, atomic hierarchy presets, and jurisdiction uniqueness."""
import json


def test_create_code_requires_superadmin(client, headers):
    payload={"name":"Civil Code","abbreviation":"CC",
             "jurisdiction_id":"state","hierarchy_template":"standard"}
    for role in ("user","lawyer"):
        result=client.post("/api/v1/codes",json=payload,headers=headers[role])
        assert result.status_code==403,result.text
    assert not any(code["name"]=="Civil Code"
                   for code in client.get("/api/v1/codes",headers=headers["user"]).json())


def test_new_code_standard_template_creates_valid_tree(client, headers):
    admin=headers["superadmin"]
    body={"name":"Civil Code","abbreviation":"CC","jurisdiction_id":"state",
          "hierarchy_template":"standard"}
    result=client.post("/api/v1/codes",json=body,headers=admin)
    assert result.status_code==200,result.text
    data=result.json()
    assert data["name"]=="Civil Code"
    assert data["type_count"]==4
    codes=client.get("/api/v1/codes",headers=headers["lawyer"]).json()
    code=next(c for c in codes if c["id"]==data["id"])
    assert code["abbreviation"]=="CC"
    assert code["jurisdiction_id"]=="state"
    assert code["status"]=="active"
    assert code["is_demo"]==0
    assert code["article_count"]==0
    tree=client.get("/api/v1/codes/"+data["id"]+"/tree",
                    headers=headers["user"])
    assert tree.status_code==200,tree.text
    types=tree.json()["types"]
    assert [type_["name"] for type_ in types]==["Часть","Раздел","Глава","Подраздел"]
    assert [type_["level_order"] for type_ in types]==[1,2,3,4]
    ids={t["name"]:t["id"] for t in types}
    parents={t["name"]:json.loads(t["allowed_parents"]) for t in types}
    assert parents["Часть"]==["ROOT"]
    assert parents["Раздел"]==["ROOT",ids["Часть"]]
    assert parents["Глава"]==["ROOT",ids["Часть"],ids["Раздел"]]
    assert parents["Подраздел"]==["ROOT",ids["Часть"],ids["Раздел"],ids["Глава"]]
    assert tree.json()["nodes"]==[]

    part=client.post("/api/v1/codes/"+data["id"]+"/nodes",headers=admin,
        json={"type_id":ids["Часть"],"parent_id":None,
              "number":"I","title":"Общие положения"})
    assert part.status_code==200,part.text
    section=client.post("/api/v1/codes/"+data["id"]+"/nodes",headers=admin,
        json={"type_id":ids["Раздел"],"parent_id":part.json()["id"],
              "number":"1","title":"Первый раздел"})
    assert section.status_code==200,section.text


def test_code_creation_rejects_blank_duplicate_and_invalid_template(client, headers):
    admin=headers["superadmin"]
    initial=client.get("/api/v1/codes",headers=admin).json()
    base={"jurisdiction_id":"state","abbreviation":"ABC","hierarchy_template":"standard"}
    for name,expected in [("    ",422),("Penal Code",409),("penal code",409)]:
        result=client.post("/api/v1/codes",headers=admin,json={**base,"name":name})
        assert result.status_code==expected,result.text
    bad=client.post("/api/v1/codes",headers=admin,json={
        **base,"name":"Invalid Skeleton","hierarchy_template":"unknown"})
    assert bad.status_code==422
    unknown=client.post("/api/v1/codes",headers=admin,json={
        **base,"name":"Unknown Jurisdiction","jurisdiction_id":"unknown"})
    assert unknown.status_code==404
    after=client.get("/api/v1/codes",headers=admin).json()
    assert len(after)==len(initial)

    clean=client.post("/api/v1/codes",headers=admin,json={
        **base,"name":"Penal Code","jurisdiction_id":"city",
        "hierarchy_template":"empty"})
    assert clean.status_code==200,clean.text
    assert clean.json()["type_count"]==0
    types=client.get("/api/v1/codes/"+clean.json()["id"]+"/tree",headers=admin)
    assert types.json()["types"]==[]
