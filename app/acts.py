"""Officially classified standalone legal acts in the library.

Published governmental documents enter this catalog only after explicit
superadministrator classification. Unclassified news and judicial decisions
are never presented as binding normative acts.
"""
import base64
import json

from fastapi import APIRouter, Depends, Header, Query

from app.core import (
    actor, audit, clean, dumps, fail, idempotent, loads, one, allrows, maybe,
    plain, read_db, require, transaction, ts, uid,
)

router = APIRouter(prefix="/library/acts", tags=["library-acts"])


def _published(c, publication_id):
    return one(c, """SELECT * FROM portal_publications WHERE id=?
                     AND status='published' AND portal_id<>'judiciary'""",
               (publication_id,), "Опубликованный нормативный документ не найден.")


def _list_cursor(value, jurisdiction):
    if not value:
        return None
    try:
        decoded = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if decoded["jurisdiction"] != (jurisdiction or "*"):
            raise ValueError("scope")
        return decoded["date"], decoded["id"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, base64.binascii.Error):
        fail(422, "INVALID_CURSOR", "Неверный курсор каталога нормативных актов.")


@router.get("")
def list_acts(jurisdiction: str | None = None,
              cursor: str | None = None,
              limit: int = Query(30, ge=1, le=100),
              current=Depends(actor)):
    with read_db() as c:
        if jurisdiction:
            one(c, "SELECT id FROM jurisdictions WHERE id=?", (jurisdiction,))
        sql = """SELECT p.id,p.jurisdiction_id,p.title,p.document_type,p.issuer,
                        p.issued_at,p.published_at,p.external_reference,
                        p.related_article_id,p.portal_id
                 FROM library_acts a JOIN portal_publications p ON p.id=a.publication_id
                 WHERE a.status='active' AND p.status='published' AND p.portal_id<>'judiciary'"""
        args = []
        if jurisdiction:
            sql += " AND p.jurisdiction_id=?"
            args.append(jurisdiction)
        position = _list_cursor(cursor, jurisdiction)
        if position:
            sql += " AND (p.published_at<? OR (p.published_at=? AND p.id<?))"
            args.extend((position[0],position[0],position[1]))
        rows = allrows(c, sql + " ORDER BY p.published_at DESC,p.id DESC LIMIT ?", args+[limit+1])
        more = len(rows)>limit
        items = rows[:limit]
        next_cursor = None
        if more and items:
            last = items[-1]
            next_cursor = base64.urlsafe_b64encode(dumps({
                "jurisdiction":jurisdiction or "*","date":last["published_at"],"id":last["id"]
            }).encode()).decode().rstrip("=")
        return {"items":items,"has_more":more,"next_cursor":next_cursor}


@router.get("/{publication_id}")
def get_act(publication_id: str, current=Depends(actor)):
    with read_db() as c:
        pub = _published(c, publication_id)
        mapping = one(c, "SELECT * FROM library_acts WHERE publication_id=?",
                      (publication_id,), "Документ не включён в правовую библиотеку.")
        if mapping["status"] != "active":
            require(current, "superadmin")
        pub["library_status"] = mapping["status"]
        pub["permalink"] = "/acts/" + publication_id
        return pub


@router.post("")
def create_act(body: dict, idempotency_key: str | None = Header(None),
               current=Depends(actor)):
    require(current, "superadmin")
    if body.get("confirm") is not True:
        fail(422, "CONFIRMATION_REQUIRED", "Подтвердите публикацию отдельного нормативного акта.")
    jurisdiction = body.get("jurisdiction_id")
    with transaction() as c:
        def apply():
            one(c, "SELECT id FROM jurisdictions WHERE id=?", (jurisdiction,))
            title = plain(body.get("title"), 250)
            kind = plain(body.get("document_type"), 100)
            issuer = plain(body.get("issuer"), 200)
            if not title or not kind or not issuer:
                fail(422, "INVALID_ACT", "Нужны название, вид документа и издавший орган.")
            related = body.get("related_article_id")
            if related:
                article = one(c, """SELECT a.id,c.jurisdiction_id FROM articles a
                                    JOIN codes c ON c.id=a.code_id
                                    WHERE a.id=? AND a.status='active'""",(related,))
                if article["jurisdiction_id"]!=jurisdiction:
                    fail(422, "JURISDICTION_MISMATCH", "Юрисдикции документа и статьи не совпадают.")
            pid = uid()
            issued = ts()
            c.execute("""INSERT INTO portal_publications
                (id,portal_id,jurisdiction_id,title,document_type,issuer,text_rich,
                 status,issued_at,published_at,related_article_id,external_reference)
                 VALUES(?,?,?,?,?,?,?,'published',?,?,?,?)""",
                (pid,"library",jurisdiction,title,kind,issuer,
                 clean(body.get("text_rich","")),issued,issued,related,
                 plain(body.get("external_reference",""),200)))
            c.execute("""INSERT INTO library_acts(publication_id,classified_by,classified_at)
                         VALUES(?,?,?)""",(pid,current["id"],ts()))
            audit(c,current,"library_act.publish","portal_publication",pid,{},{
                "jurisdiction_id":jurisdiction,"title":title,"issuer":issuer})
            return {"id":pid,"status":"published","permalink":"/acts/"+pid}
        return idempotent(c,current,"library_act.create",idempotency_key,body,apply)


@router.post("/{publication_id}/classify")
def classify_act(publication_id: str, body: dict, idempotency_key: str | None = Header(None),
                 current=Depends(actor)):
    require(current, "superadmin")
    if body.get("confirm") is not True:
        fail(422, "CONFIRMATION_REQUIRED", "Подтвердите включение опубликованного акта в библиотеку.")
    with transaction() as c:
        def apply():
            p = _published(c,publication_id)
            if maybe(c,"SELECT publication_id FROM library_acts WHERE publication_id=?",(publication_id,)):
                fail(409, "ALREADY_CLASSIFIED", "Акт уже классифицирован в библиотеке.")
            c.execute("INSERT INTO library_acts(publication_id,classified_by,classified_at) VALUES(?,?,?)",
                      (publication_id,current["id"],ts()))
            audit(c,current,"library_act.classify","portal_publication",publication_id,
                  {},{"portal_id":p["portal_id"],"jurisdiction_id":p["jurisdiction_id"]})
            return {"id":publication_id,"permalink":"/acts/"+publication_id}
        return idempotent(c,current,"library_act.classify:"+publication_id,idempotency_key,body,apply)


@router.post("/{publication_id}/retire")
def retire_act(publication_id: str, body: dict, idempotency_key: str | None = Header(None),
               current=Depends(actor)):
    require(current, "superadmin")
    if body.get("confirm") is not True:
        fail(422, "CONFIRMATION_REQUIRED", "Подтвердите исключение акта из действующего каталога.")
    with transaction() as c:
        def apply():
            old=one(c,"SELECT * FROM library_acts WHERE publication_id=? AND status='active'",
                    (publication_id,))
            c.execute("""UPDATE library_acts SET status='archived',retired_at=?
                         WHERE publication_id=?""",(ts(),publication_id))
            audit(c,current,"library_act.retire","portal_publication",publication_id,old,
                  {"status":"archived"})
            return {"id":publication_id,"status":"archived"}
        return idempotent(c,current,"library_act.retire:"+publication_id,idempotency_key,body,apply)
