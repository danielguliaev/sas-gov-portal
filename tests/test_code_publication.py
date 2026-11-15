"""REQ-022: code provenance, immutable identity and upgrades from older SQLite schemas."""
import sqlite3
from pathlib import Path

import app.core as core


def api(value):
    return "/api/v1"+value


def test_code_publication_metadata_is_editable_without_changing_code_id(client,headers):
    admin=headers["superadmin"]
    body={"name":"Commercial Code","abbreviation":"CC","jurisdiction_id":"state",
          "hierarchy_template":"empty","publication_issuer":"Уполномоченный орган штата",
          "publication_reference":"SC-2026-44","published_at":"2026-10-29"}
    created=client.post(api("/codes"),headers=admin,json=body)
    assert created.status_code==200,created.text
    code_id=created.json()["id"]
    code=next(x for x in client.get(api("/codes"),headers=headers["user"]).json()
              if x["id"]==code_id)
    assert code["publication_issuer"]==body["publication_issuer"]
    assert code["publication_reference"]==body["publication_reference"]
    assert code["published_at"]==body["published_at"]
    tree=client.get(api("/codes/"+code_id+"/tree"),headers=headers["user"])
    assert tree.status_code==200
    assert tree.json()["code"]["published_at"]=="2026-10-29"

    change={"name":"Commercial Code Revised","abbreviation":"CCR",
            "publication_issuer":"Секретарь штата",
            "publication_reference":"SC-2026-45","published_at":"2026-11-01"}
    denied=client.patch(api("/codes/"+code_id),headers=headers["user"],json=change)
    assert denied.status_code==403
    changed=client.patch(api("/codes/"+code_id),headers=admin,json=change)
    assert changed.status_code==200,changed.text
    assert changed.json()["id"]==code_id
    assert changed.json()["publication_reference"]=="SC-2026-45"
    latest=next(x for x in client.get(api("/codes"),headers=headers["lawyer"]).json()
                if x["id"]==code_id)
    assert latest["name"]=="Commercial Code Revised"
    assert latest["abbreviation"]=="CCR"
    assert latest["publication_issuer"]=="Секретарь штата"
    audit=client.get(api("/admin/audit"),headers=admin)
    assert any(x["action"]=="code.metadata.update" and x["object_id"]==code_id
               for x in audit.json())


def test_code_publication_validation_and_jurisdiction_are_safe(client,headers):
    admin=headers["superadmin"]
    body={"name":"Civil Code 2026","jurisdiction_id":"city","published_at":"2026-13-15"}
    invalid=client.post(api("/codes"),headers=admin,json=body)
    assert invalid.status_code==422
    assert invalid.json()["error"]["code"]=="INVALID_PUBLICATION_DATE"
    valid=client.post(api("/codes"),headers=admin,json={
        "name":"Civil Code 2026","jurisdiction_id":"city","published_at":""})
    assert valid.status_code==200,valid.text
    code_id=valid.json()["id"]
    for patch in (
        {"published_at":"invalid"},{"published_at":"2026-02-30"},
        {"jurisdiction_id":"state"},{"status":"archived"},{"name":" "}
    ):
        response=client.patch(api("/codes/"+code_id),headers=admin,json=patch)
        assert response.status_code==422,(patch,response.text)
    assert client.get(api("/codes/"+code_id+"/tree"),headers=admin).json()["code"]["status"]=="active"


def test_v4_migrates_prior_local_database_without_losing_codes(tmp_path):
    db=tmp_path/"existing-library.sqlite"
    base=Path(core.__file__).resolve().parent.parent/"db"
    with sqlite3.connect(db) as c:
        c.executescript((base/"001_initial.sql").read_text(encoding="utf-8"))
        c.execute("INSERT INTO schema_migrations(version) VALUES(1)")
        c.execute("ALTER TABLE article_revisions ADD COLUMN display_snapshot TEXT NOT NULL DEFAULT '{}'")
        c.execute("INSERT INTO schema_migrations(version) VALUES(2)")
        c.executescript((base/"003_library_acts.sql").read_text(encoding="utf-8"))
        c.execute("INSERT INTO schema_migrations(version) VALUES(3)")
        c.execute("INSERT INTO jurisdictions(id,name) VALUES('state','Штат')")
        c.execute("""INSERT INTO codes(id,jurisdiction_id,name,abbreviation)
                     VALUES('original-code','state','Historic Code','HC')""")
        c.commit()
    old=core.DB_PATH
    try:
        core.DB_PATH=str(db)
        core.init_database()
        core.init_database()  # repeated startup must not reapply ALTER TABLE
    finally:
        core.DB_PATH=old
    with sqlite3.connect(db) as c:
        version=c.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        row=c.execute("""SELECT id,name,abbreviation,publication_issuer,
                                publication_reference,published_at FROM codes
                         WHERE id='original-code'""").fetchone()
    assert version==6
    assert row==("original-code","Historic Code","HC",None,None,None)
