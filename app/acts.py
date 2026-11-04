"""Standalone normative acts explicitly classified into the legal library.

A governmental publication is not legislation until a superadministrator
classifies it. Library-authored acts are directly published by that role;
both kinds share stable IDs and a reversible archive classification.
"""
import hashlib

from fastapi import APIRouter, Depends, Header, Query

from app.core import (
    actor, audit, clean, dumps, fail, idempotent, one, allrows, maybe,
    plain, read_db, require, transaction, ts, uid,
)

router = APIRouter(prefix="/library/acts", tags=["library-acts"])


@router.get("")
def list_acts(jurisdiction: str | None = None,
              limit: int = Query(200,ge=1,le=200), current=Depends(actor)):
    with read_db() as c:
        sql = """SELECT p.id,p.portal_id,p.jurisdiction_id,p.title,p.document_type,
                        p.issuer,p.issued_at,p.published_at,p.external_reference,
                        p.related_article_id
                 FROM portal_publications p JOIN library_acts la
                 ON la.publication_id=p.id
                 WHERE la.status='active' AND p.status='published'
                 AND p.portal_id<>'judiciary'"""
        args=[]
        if jurisdiction:
            one(c,"SELECT id FROM jurisdictions WHERE id=?", (jurisdiction,))
            sql += " AND p.jurisdiction_id=?"
            args.append(jurisdiction)
        return allrows(c,sql+" ORDER BY p.published_at DESC,p.id DESC LIMIT ?",
                       args+[limit])


@router.get("/{act_id}")
def get_act(act_id: str, current=Depends(actor)):
    with read_db() as c:
        pub=one(c,"""SELECT * FROM portal_publications WHERE id=? AND
                     status='published' AND portal_id<>'judiciary'""",(act_id,))
        mapped=one(c,"SELECT * FROM library_acts WHERE publication_id=?",(act_id,))
        if mapped["status"] != "active":
            require(current,"lawyer","superadmin")
        pub["status"]="published" if mapped["status"]=="active" else "archived"
        pub["permalink"]="/acts/"+act_id
        return pub


@router.post("")
def create_act(body: dict, idempotency_key: str | None = Header(None),
               current=Depends(actor)):
    require(current,"superadmin")
    if body.get("confirm") is not True:
        fail(422,"CONFIRMATION_REQUIRED","Подтвердите публикацию нормативного акта.")
    with transaction() as c:
        def apply():
            juris=body.get("jurisdiction_id")
            one(c,"SELECT id FROM jurisdictions WHERE id=?",(juris,))
            title=plain(body.get("title"),250)
            kind=plain(body.get("document_type","Нормативный акт"),100)
            issuer=plain(body.get("issuer","Штат Сан-Андреас"),200)
            rich=clean(body.get("text_rich",""))
            if not title or not kind or not issuer or not rich.strip():
                fail(422,"INVALID_ACT","Необходимо указать название, орган, тип и текст акта.")
            related=body.get("related_article_id")
            if related:
                item=one(c,"""SELECT c.jurisdiction_id FROM articles a
                         JOIN codes c ON c.id=a.code_id WHERE a.id=? AND a.status='active'""",
                         (related,))
                if item["jurisdiction_id"] != juris:
                    fail(422,"JURISDICTION_MISMATCH","Юрисдикции акта и статьи отличаются.")
            pid=uid(); now=ts()
            c.execute("""INSERT INTO portal_publications
                (id,portal_id,jurisdiction_id,title,document_type,issuer,text_rich,
                 status,issued_at,published_at,related_article_id,external_reference)
                VALUES(?,'library',?,?,?,?,?,'published',?,?,?,?)""",
                (pid,juris,title,kind,issuer,rich,now,now,related,
                 plain(body.get("external_reference",""),200)))
            c.execute("""INSERT INTO library_acts
                (publication_id,classified_by,classified_at) VALUES(?,?,?)""",
                (pid,current["id"],now))
            audit(c,current,"library_act.publish","portal_publication",pid,{},
                  {"title":title,"jurisdiction_id":juris})
            return {"id":pid,"status":"published","permalink":"/acts/"+pid}
        return idempotent(c,current,"library_act.create",idempotency_key,body,apply)


@router.post("/{act_id}/classify")
def classify_act(act_id: str, body: dict,
                 idempotency_key: str | None = Header(None), current=Depends(actor)):
    require(current,"superadmin")
    if body.get("confirm") is not True:
        fail(422,"CONFIRMATION_REQUIRED","Подтвердите включение публикации в библиотеку.")
    with transaction() as c:
        def apply():
            pub=one(c,"""SELECT * FROM portal_publications
                    WHERE id=? AND status='published' AND portal_id NOT IN ('judiciary','library')""",
                    (act_id,))
            if maybe(c,"SELECT publication_id FROM library_acts WHERE publication_id=?",(act_id,)):
                fail(409,"ALREADY_CLASSIFIED","Акт уже классифицирован в библиотеке.")
            c.execute("""INSERT INTO library_acts
                (publication_id,classified_by,classified_at) VALUES(?,?,?)""",
                (act_id,current["id"],ts()))
            audit(c,current,"library_act.classify","portal_publication",act_id,{},
                  {"source_portal":pub["portal_id"],"title":pub["title"]})
            return {"id":act_id,"status":"published","permalink":"/acts/"+act_id}
        return idempotent(c,current,"library_act.classify:"+act_id,
                          idempotency_key,body,apply)


def _archive_plan(c,act_id):
    pub=one(c,"""SELECT p.id,p.title,p.published_at,la.status,la.classified_at
                  FROM portal_publications p JOIN library_acts la
                  ON la.publication_id=p.id
                  WHERE p.id=? AND p.status='published' AND la.status='active'""",(act_id,))
    fingerprint=hashlib.sha256(dumps(pub).encode()).hexdigest()
    return {"id":pub["id"],"title":pub["title"],"preview_hash":fingerprint}


@router.post("/{act_id}/archive/preview")
def archive_preview(act_id: str, current=Depends(actor)):
    require(current,"superadmin")
    with read_db() as c:
        return _archive_plan(c,act_id)


@router.post("/{act_id}/archive")
def archive_act(act_id: str, body: dict,idempotency_key: str | None = Header(None),
                current=Depends(actor)):
    require(current,"superadmin")
    if body.get("confirm") is not True:
        fail(422,"CONFIRMATION_REQUIRED","Подтвердите архивирование акта.")
    with transaction() as c:
        def apply():
            plan=_archive_plan(c,act_id)
            if body.get("preview_hash") != plan["preview_hash"]:
                fail(409,"PREVIEW_STALE","Состав архивной операции изменился.")
            previous=one(c,"SELECT * FROM library_acts WHERE publication_id=?",(act_id,))
            c.execute("""UPDATE library_acts SET status='archived',retired_at=?
                         WHERE publication_id=?""",(ts(),act_id))
            audit(c,current,"library_act.archive","portal_publication",act_id,
                  previous,{"status":"archived"})
            return {"id":act_id,"status":"archived"}
        return idempotent(c,current,"library_act.archive:"+act_id,
                          idempotency_key,body,apply)


@router.post("/{act_id}/restore")
def restore_act(act_id: str, body: dict,idempotency_key: str | None = Header(None),
                current=Depends(actor)):
    require(current,"superadmin")
    if body.get("confirm") is not True:
        fail(422,"CONFIRMATION_REQUIRED","Подтвердите восстановление акта.")
    with transaction() as c:
        def apply():
            previous=one(c,"""SELECT * FROM library_acts WHERE publication_id=?
                            AND status='archived'""",(act_id,))
            one(c,"""SELECT id FROM portal_publications WHERE id=? AND status='published'""",
                (act_id,))
            c.execute("""UPDATE library_acts SET status='active',retired_at=NULL
                         WHERE publication_id=?""",(act_id,))
            audit(c,current,"library_act.restore","portal_publication",act_id,
                  previous,{"status":"active"})
            return {"id":act_id,"status":"published"}
        return idempotent(c,current,"library_act.restore:"+act_id,
                          idempotency_key,body,apply)
