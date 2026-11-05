"""Independent government portals and the publication-to-library editorial chain."""
import json
import os
from fastapi import APIRouter, Depends, Header

from app.core import (
    actor, append_revision, audit, clean, dumps, fail, idempotent, loads, maybe,
    one, allrows, plain, read_db, require, transaction, ts, uid, validate_article,
    validate_relations, present_article
)
from app.editor import _check_lock, _notify_favorites

router = APIRouter()


@router.get("/senate/dashboard")
def senate_dashboard(current=Depends(actor)):
    with read_db() as c:
        setting = one(c, "SELECT setting_value FROM senate_settings WHERE setting_key='committee_review_days'")
        committees = allrows(c, "SELECT id,name,description,active FROM senate_committees ORDER BY name")
        bills = allrows(c, "SELECT id,title,author_id,status,created_at FROM bills ORDER BY created_at DESC LIMIT 100")
        return {"committee_review_days": int(setting["setting_value"]), "committees": committees, "bills": bills}


@router.put("/senate/settings/committee-review-days")
def update_committee_review_days(body: dict, current=Depends(actor)):
    require(current, "superadmin")
    days = body.get("days")
    if not isinstance(days, int) or not 1 <= days <= 365:
        fail(422, "INVALID_REVIEW_DAYS", "Укажите срок от 1 до 365 календарных дней.")
    with transaction() as c:
        before = one(c, "SELECT setting_value FROM senate_settings WHERE setting_key='committee_review_days'")
        c.execute("UPDATE senate_settings SET setting_value=?,updated_at=? WHERE setting_key='committee_review_days'", (str(days), ts()))
        audit(c, current, "senate.settings.committee_review_days", "senate_setting", "committee_review_days", before, {"days": days})
    return {"days": days}

# DEMONSTRATION ONLY. No legal quorum or voting threshold is inferred in application logic.
DEFAULT_BILL_TRANSITIONS = {
    "project": ["discussion"],
    "discussion": ["voting"],
    "voting": ["decision"],
    "decision": ["published"],
    "published": [],
}
try:
    BILL_TRANSITIONS = json.loads(os.getenv("BILL_TRANSITIONS_JSON", dumps(DEFAULT_BILL_TRANSITIONS)))
    if not isinstance(BILL_TRANSITIONS, dict):
        raise ValueError
except (ValueError, TypeError):
    raise RuntimeError("Некорректный BILL_TRANSITIONS_JSON")


@router.get("/portal-publications")
def publications(portal_id: str | None = None, current=Depends(actor)):
    with read_db() as c:
        sql = "SELECT * FROM portal_publications WHERE status='published'"
        args = []
        if portal_id:
            one(c, "SELECT id FROM portals WHERE id=?", (portal_id,))
            sql += " AND portal_id=?"; args.append(portal_id)
        return allrows(c, sql + " ORDER BY published_at DESC,id DESC LIMIT 100", args)


@router.get("/portal-publications/{publication_id}")
def publication(publication_id: str, current=Depends(actor)):
    with read_db() as c:
        pub = one(c, "SELECT * FROM portal_publications WHERE id=? AND status='published'",
                  (publication_id,))
        return pub


@router.post("/portal-publications")
def create_publication(body: dict, current=Depends(actor)):
    require(current, "portal_publisher")
    with transaction() as c:
        portal_id, jurisdiction_id = body.get("portal_id"), body.get("jurisdiction_id")
        one(c, "SELECT id FROM portals WHERE id=?", (portal_id,))
        one(c, "SELECT id FROM jurisdictions WHERE id=?", (jurisdiction_id,))
        aid = body.get("related_article_id")
        if aid:
            one(c, "SELECT id FROM articles WHERE id=? AND status='active'", (aid,))
        publication_id = uid()
        c.execute("""INSERT INTO portal_publications
           (id,portal_id,jurisdiction_id,title,document_type,issuer,text_rich,
            status,issued_at,related_article_id,external_reference)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
           (publication_id, portal_id, jurisdiction_id, plain(body.get("title"), 250),
            plain(body.get("document_type"), 100), plain(body.get("issuer"), 200),
            clean(body.get("text_rich", "")), "draft", ts(), aid,
            plain(body.get("external_reference", ""), 200)))
        audit(c, current, "publication.create", "portal_publication", publication_id, {}, body)
    return {"id": publication_id, "status": "draft"}


@router.post("/portal-publications/{publication_id}/publish")
def publish(publication_id: str, body: dict, idempotency_key: str | None = Header(None),
            current=Depends(actor)):
    require(current, "portal_publisher")
    if body.get("confirm") is not True:
        fail(422, "CONFIRMATION_REQUIRED", "Для официальной публикации требуется подтверждение.")
    with transaction() as c:
        def apply():
            pub = one(c, "SELECT * FROM portal_publications WHERE id=?", (publication_id,))
            if pub["status"] != "draft":
                fail(409, "ALREADY_PUBLISHED", "Документ уже опубликован.")
            c.execute("UPDATE portal_publications SET status='published',published_at=? WHERE id=?",
                      (ts(), publication_id))
            review_id = None
            if pub["related_article_id"]:
                a = one(c, "SELECT id,revision_id FROM articles WHERE id=? AND status='active'",
                        (pub["related_article_id"],))
                review_id = uid()
                c.execute("""INSERT INTO editorial_reviews
                  (id,publication_id,article_id,base_revision_id)
                  VALUES(?,?,?,?)""",
                  (review_id, publication_id, a["id"], a["revision_id"]))
            audit(c, current, "publication.publish", "portal_publication", publication_id,
                  {"status": "draft"}, {"status": "published", "review_id": review_id})
            return {"id": publication_id, "status": "published", "review_id": review_id}
        return idempotent(c, current, "publish:" + publication_id, idempotency_key, body, apply)


@router.get("/editorial-reviews")
def editorial_reviews(status: str = "pending", current=Depends(actor)):
    require(current, "library_editor", "superadmin")
    if status not in ("pending", "approved", "rejected"):
        fail(422, "INVALID_STATUS", "Неизвестный статус.")
    with read_db() as c:
        return allrows(c, """SELECT r.*,p.title AS publication_title,p.issuer,p.published_at
                             FROM editorial_reviews r JOIN portal_publications p
                             ON p.id=r.publication_id WHERE r.status=?
                             ORDER BY p.published_at DESC""", (status,))


@router.post("/editorial-reviews/{review_id}/confirm")
def confirm_review(review_id: str, body: dict, idempotency_key: str | None = Header(None),
                   current=Depends(actor)):
    require(current, "library_editor", "superadmin")
    if body.get("confirm") is not True:
        fail(422, "CONFIRMATION_REQUIRED", "Подтвердите включение официального изменения.")
    with transaction() as c:
        def apply():
            review = one(c, "SELECT * FROM editorial_reviews WHERE id=?", (review_id,))
            if review["status"] != "pending":
                fail(409, "REVIEW_CLOSED", "Заявка уже рассмотрена.")
            pub = one(c, "SELECT * FROM portal_publications WHERE id=? AND status='published'",
                      (review["publication_id"],))
            article = one(c, "SELECT * FROM articles WHERE id=? AND status='active'",
                          (review["article_id"],))
            _check_lock(c, article["id"], current, body.get("lock_token"),
                        body.get("editor_session_id"))
            if (article["revision_id"] != review["base_revision_id"] or
                body.get("base_revision_id") != article["revision_id"]):
                fail(409, "STALE_REVISION", "После публикации основная статья изменилась.")
            # An official act is not automatically interpreted as a consolidated article.
            if "consolidated_text_rich" not in body:
                fail(422, "CONSOLIDATED_TEXT_REQUIRED", "Редактор должен проверить итоговый текст статьи.")
            title = plain(body.get("title", article["title"]), 300)
            rich = clean(body["consolidated_text_rich"])
            values = {
                "categories": body.get("categories", loads(article["categories_json"], [])),
                "criminal_class": body.get("criminal_class", article["criminal_class"]),
                "custom_values": body.get("custom_values", loads(article["custom_values"], {})),
                "sanctions": body.get("sanctions", {k: article[k] for k in (
                    "prison_min_months", "prison_max_months", "fine_min_usd", "fine_max_usd")}),
            }
            categories, criminal_class, custom, sanctions = validate_article(
                c, values, article["code_id"], article)
            c.execute("""UPDATE articles SET title=?,text_rich=?,categories_json=?,criminal_class=?,
                custom_values=?,prison_min_months=?,prison_max_months=?,fine_min_usd=?,fine_max_usd=?,
                is_demo=0,updated_at=? WHERE id=?""",
                (title, rich, dumps(categories), criminal_class, dumps(custom),
                 sanctions["prison_min_months"], sanctions["prison_max_months"],
                 sanctions["fine_min_usd"], sanctions["fine_max_usd"], ts(), article["id"]))
            validate_relations(c, article["id"])
            provenance = {"publication_id": pub["id"], "portal_id": pub["portal_id"],
                          "issuer": pub["issuer"], "document_type": pub["document_type"],
                          "published_at": pub["published_at"],
                          "external_reference": pub["external_reference"]}
            revision_id = append_revision(c, article["id"], current["id"],
                body.get("reason", "Подтверждение официальной публикации"), provenance)
            c.execute("""UPDATE editorial_reviews SET status='approved',reviewed_by=?,reviewed_at=?
                         WHERE id=?""", (current["id"], ts(), review_id))
            _notify_favorites(c, article["id"], revision_id, official=True)
            audit(c, current, "review.approve", "editorial_review", review_id, review,
                  {"revision_id": revision_id, "provenance": provenance})
            return {"review_id": review_id, "status": "approved", "revision_id": revision_id}
        return idempotent(c, current, "review:" + review_id, idempotency_key, body, apply)


@router.post("/editorial-reviews/{review_id}/reject")
def reject_review(review_id: str, body: dict, current=Depends(actor)):
    require(current, "library_editor", "superadmin")
    with transaction() as c:
        review = one(c, "SELECT * FROM editorial_reviews WHERE id=? AND status='pending'", (review_id,))
        c.execute("UPDATE editorial_reviews SET status='rejected',reviewed_by=?,reviewed_at=? WHERE id=?",
                  (current["id"], ts(), review_id))
        audit(c, current, "review.reject", "editorial_review", review_id, review,
              {"reason": plain(body.get("reason", ""), 2000)})
    return {"status": "rejected"}


@router.post("/portal-publications/{publication_id}/precedent")
def mark_precedent(publication_id: str, body: dict, current=Depends(actor)):
    require(current, "library_editor", "superadmin")
    with transaction() as c:
        pub = one(c, """SELECT * FROM portal_publications WHERE id=? AND portal_id='judiciary'
                       AND status='published'""", (publication_id,))
        value = bool(body.get("is_precedent"))
        c.execute("UPDATE portal_publications SET is_precedent=? WHERE id=?",
                  (1 if value else 0, publication_id))
        audit(c, current, "precedent.classify", "portal_publication", publication_id,
              {"is_precedent": bool(pub["is_precedent"])}, {"is_precedent": value})
    return {"is_precedent": value}


@router.get("/precedents")
def precedents(current=Depends(actor)):
    with read_db() as c:
        return allrows(c, """SELECT * FROM portal_publications WHERE portal_id='judiciary'
                             AND status='published' AND is_precedent=1
                             ORDER BY published_at DESC,id DESC LIMIT 100""")


@router.get("/bills")
def bills(current=Depends(actor)):
    with read_db() as c:
        return allrows(c, "SELECT * FROM bills ORDER BY created_at DESC LIMIT 100")


@router.get("/bills/{bill_id}")
def bill(bill_id: str, current=Depends(actor)):
    with read_db() as c:
        result = one(c, "SELECT * FROM bills WHERE id=?", (bill_id,))
        result["revisions"] = allrows(c, """SELECT * FROM bill_revisions WHERE bill_id=?
                                          ORDER BY version DESC""", (bill_id,))
        result["events"] = allrows(c, """SELECT * FROM bill_events WHERE bill_id=?
                                       ORDER BY created_at,id""", (bill_id,))
        result["discussions"] = allrows(c, """SELECT * FROM discussions WHERE bill_id=?
                                            ORDER BY created_at,id""", (bill_id,))
        result["votes"] = allrows(c, """SELECT id,result,created_at FROM votes WHERE bill_id=?
                                      ORDER BY created_at,id""", (bill_id,))
        result["adopted_act"] = maybe(c, "SELECT * FROM adopted_acts WHERE bill_id=?", (bill_id,))
        return result


@router.post("/bills")
def new_bill(body: dict, current=Depends(actor)):
    require(current, "legislator")
    with transaction() as c:
        bill_id = uid()
        title = plain(body.get("title"), 250)
        rich = clean(body.get("text_rich", ""))
        c.execute("INSERT INTO bills VALUES(?,?,?,?,?,?)",
                  (bill_id, title, current["id"], "project", rich, ts()))
        c.execute("INSERT INTO bill_revisions VALUES(?,?,?,?,?,?)",
                  (uid(), bill_id, 1, rich, current["id"], ts()))
        c.execute("INSERT INTO bill_events VALUES(?,?,?,?,?,?)",
                  (uid(), bill_id, "project", "Создание демонстрационного законопроекта", current["id"], ts()))
        audit(c, current, "bill.create", "bill", bill_id)
    return {"id": bill_id, "status": "project"}


@router.post("/bills/{bill_id}/revisions")
def new_bill_revision(bill_id: str, body: dict, current=Depends(actor)):
    require(current, "legislator")
    with transaction() as c:
        b = one(c, "SELECT * FROM bills WHERE id=?", (bill_id,))
        if b["status"] in ("decision", "published"):
            fail(409, "BILL_FROZEN", "После решения нельзя переписывать проект.")
        rich = clean(body.get("text_rich", ""))
        version = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM bill_revisions WHERE bill_id=?",
                            (bill_id,)).fetchone()[0]
        c.execute("INSERT INTO bill_revisions VALUES(?,?,?,?,?,?)",
                  (uid(), bill_id, version, rich, current["id"], ts()))
        c.execute("UPDATE bills SET text_rich=? WHERE id=?", (rich, bill_id))
        c.execute("INSERT INTO bill_events VALUES(?,?,?,?,?,?)",
                  (uid(), bill_id, "revision", "Редакция " + str(version), current["id"], ts()))
    return {"version": version}


@router.post("/bills/{bill_id}/discussions")
def discuss_bill(bill_id: str, body: dict, current=Depends(actor)):
    require(current, "legislator")
    with transaction() as c:
        b = one(c, "SELECT * FROM bills WHERE id=?", (bill_id,))
        if b["status"] != "discussion":
            fail(409, "INVALID_BILL_STATE", "Обсуждение сейчас закрыто.")
        discussion_id = uid()
        c.execute("INSERT INTO discussions VALUES(?,?,?,?,?)",
                  (discussion_id, bill_id, current["id"], plain(body.get("body"), 10000), ts()))
    return {"id": discussion_id}


@router.post("/bills/{bill_id}/advance")
def advance_bill(bill_id: str, body: dict, current=Depends(actor)):
    require(current, "legislator")
    next_status = body.get("status")
    with transaction() as c:
        b = one(c, "SELECT * FROM bills WHERE id=?", (bill_id,))
        if next_status not in BILL_TRANSITIONS.get(b["status"], []):
            fail(409, "INVALID_BILL_TRANSITION", "Переход состояния не разрешён регламентом.")
        if next_status == "published":
            pub_id = body.get("publication_id")
            pub = one(c, """SELECT * FROM portal_publications
                            WHERE id=? AND portal_id='legislature' AND status='published'""", (pub_id,))
            act_id = uid()
            c.execute("INSERT INTO adopted_acts VALUES(?,?,?)", (act_id, bill_id, pub["id"]))
        c.execute("UPDATE bills SET status=? WHERE id=?", (next_status, bill_id))
        c.execute("INSERT INTO bill_events VALUES(?,?,?,?,?,?)",
                  (uid(), bill_id, next_status, plain(body.get("reason", ""), 2000),
                   current["id"], ts()))
        audit(c, current, "bill.advance", "bill", bill_id,
              {"status": b["status"]}, {"status": next_status})
    return {"status": next_status}


@router.post("/bills/{bill_id}/votes")
def create_vote(bill_id: str, body: dict, current=Depends(actor)):
    require(current, "legislator")
    with transaction() as c:
        one(c, "SELECT id FROM bills WHERE id=? AND status='voting'", (bill_id,))
        session_id = body.get("session_id")
        if session_id:
            one(c, "SELECT id FROM legislative_sessions WHERE id=?", (session_id,))
        vote_id = uid()
        c.execute("INSERT INTO votes VALUES(?,?,?,?,?)",
                  (vote_id, bill_id, session_id, "pending", ts()))
    return {"id": vote_id}


@router.post("/votes/{vote_id}/choice")
def vote_choice(vote_id: str, body: dict, current=Depends(actor)):
    require(current, "legislator")
    choice = body.get("choice")
    if choice not in ("yes", "no", "abstain"):
        fail(422, "INVALID_VOTE", "Укажите yes, no или abstain.")
    with transaction() as c:
        one(c, "SELECT id FROM votes WHERE id=? AND result='pending'", (vote_id,))
        c.execute("""INSERT INTO vote_choices(vote_id,voter_id,choice) VALUES(?,?,?)
                     ON CONFLICT(vote_id,voter_id) DO UPDATE SET choice=excluded.choice""",
                  (vote_id, current["id"], choice))
    return {"choice": choice}


@router.post("/votes/{vote_id}/close")
def close_vote(vote_id: str, body: dict, current=Depends(actor)):
    require(current, "legislator")
    result = body.get("result")
    if result not in ("adopted", "rejected"):
        fail(422, "INVALID_VOTE_RESULT", "Укажите итоговый результат.")
    with transaction() as c:
        vote = one(c, "SELECT * FROM votes WHERE id=? AND result='pending'", (vote_id,))
        c.execute("UPDATE votes SET result=? WHERE id=?", (result, vote_id))
        c.execute("INSERT INTO bill_events VALUES(?,?,?,?,?,?)",
                  (uid(), vote["bill_id"], "vote_result", result, current["id"], ts()))
        tally = allrows(c, """SELECT choice,COUNT(*) AS count FROM vote_choices
                              WHERE vote_id=? GROUP BY choice""", (vote_id,))
        audit(c, current, "vote.close", "vote", vote_id, {}, {"result": result, "tally": tally})
    return {"result": result, "tally": tally, "note": "Итог зафиксирован по тестовому регламенту."}
