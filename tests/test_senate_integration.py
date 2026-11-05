"""Cross-portal privacy, migration and real export checks."""
from io import BytesIO

import app.core as core


def new_draft(client, headers):
    response = client.post("/api/v1/senate/bills", headers=headers["legislator"], json={
        "title": "О порядке открытого рассмотрения проектов",
        "text_rich": "<p>Статья 1. Материалы рассмотрения доступны читателям.</p>",
        "document_type": "senate_bill", "category_id": "general",
        "effective_at": "2026-12-01T00:00:00Z", "changes": [],
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_legacy_routes_cannot_leak_or_modify_senate_draft(client, headers):
    bill_id = new_draft(client, headers)
    assert client.get(f"/api/v1/senate/bills/{bill_id}").status_code == 404
    for url in ("/api/v1/bills", "/api/v1/senate/dashboard"):
        response = client.get(url, headers=headers["user"])
        assert response.status_code == 200, response.text
        assert bill_id not in response.text
    assert client.get(f"/api/v1/bills/{bill_id}", headers=headers["legislator"]).status_code == 409
    response = client.post(f"/api/v1/bills/{bill_id}/revisions", headers=headers["legislator"],
                           json={"text_rich": "<p>Обход проверки версии</p>"})
    assert response.status_code == 409
    response = client.post(f"/api/v1/bills/{bill_id}/advance", headers=headers["legislator"],
                           json={"status": "discussion"})
    assert response.status_code == 409


def test_migrations_repeat_without_touching_library_and_senate_audit_retained(client):
    with core.read_db() as c:
        before = core.allrows(c, "SELECT * FROM articles ORDER BY id")
    core.init_database()
    core.init_database()
    with core.transaction() as c:
        after = core.allrows(c, "SELECT * FROM articles ORDER BY id")
        assert before == after
        c.execute("""INSERT INTO audit_events
            (id,user_id,action,object_type,object_id,before_json,after_json,created_at)
            VALUES('senate-retained','system','senate.test','bill','x','{}','{}','2000-01-01T00:00:00Z')""")
    core.purge_old_audit_events()
    with core.read_db() as c:
        assert core.one(c, "SELECT action FROM audit_events WHERE id='senate-retained'")["action"] == "senate.test"


def test_pdf_embeds_cyrillic_font_and_paginates_long_history():
    from app.senate_pdf import pdf_response
    from pypdf import PdfReader
    response = pdf_response("История законопроекта", [
        ("Редакция 1", "Статья 1. Открытое рассмотрение.\n" * 150),
        ("Журнал рассмотрения", "Проект передан в комитет.")], "senate-test.pdf")
    assert response.media_type == "application/pdf"
    reader = PdfReader(BytesIO(response.body))
    assert len(reader.pages) >= 3
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "История законопроекта" in text
    assert "Проект передан в комитет." in text
