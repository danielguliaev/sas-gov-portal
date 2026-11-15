"""REQ-121: successful and rejected admin actions retain audit source and outcome."""
import app.core as core


def path(value):
    return "/api/v1" + value


def test_audit_includes_source_outcome_and_no_sensitive_payload(client, headers):
    admin=headers["superadmin"]
    title="Audit Proof Category"
    created=client.post(path("/categories"),headers=admin,json={"name":title})
    assert created.status_code==200,created.text
    rejected=client.post(path("/categories"),headers=admin,json={
        "name":title,"token":"secret-value-must-not-be-logged"})
    assert rejected.status_code==409,rejected.text

    unauthorized=client.post(path("/categories"),headers=headers["user"],json={"name":"Denied"})
    assert unauthorized.status_code==403,unauthorized.text
    history=client.get(path("/admin/audit?limit=200"),headers=admin)
    assert history.status_code==200,history.text
    logs=history.json()
    successes=[entry for entry in logs if entry["action"]=="category.create"
               and entry["object_id"]==created.json()["id"]]
    assert len(successes)==1
    assert successes[0]["result"]=="success"
    assert successes[0]["http_status"]==200
    assert successes[0]["source"]=="POST /api/v1/categories"
    failures=[entry for entry in logs if entry["action"]=="request.rejected"
              and entry["source"]=="POST /api/v1/categories"]
    assert len(failures)==1
    assert failures[0]["user_id"]=="demo-superadmin"
    assert failures[0]["result"]=="failed"
    assert failures[0]["http_status"]==409
    assert failures[0]["before_json"]=="{}"
    assert "secret-value-must-not-be-logged" not in str(logs)
    assert all(entry["user_id"]!="demo-user" for entry in failures)


def test_failed_transaction_does_not_create_success_audit(client,headers):
    admin=headers["superadmin"]
    before=client.get(path("/admin/audit"),headers=admin).json()
    result=client.post(path("/codes"),headers=admin,json={
        "name":"Penal Code","jurisdiction_id":"state"})
    assert result.status_code==409
    after=client.get(path("/admin/audit"),headers=admin).json()
    new=[entry for entry in after if entry["id"] not in {e["id"] for e in before}]
    assert len(new)==1
    assert new[0]["action"]=="request.rejected"
    assert new[0]["http_status"]==409
    assert not any(entry["action"]=="code.create" for entry in new)


def test_old_audit_records_remain_visible_after_upgrade(client,headers):
    with core.read_db() as c:
        columns={row["name"] for row in c.execute("PRAGMA table_info(audit_events)")}
    assert {"source","result","http_status"} <= columns
    assert client.get(path("/admin/audit"),headers=headers["lawyer"]).status_code==403
