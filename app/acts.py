"""Explicit librarian classification of already published legal documents.

Independent governmental sites are deferred. Their publication records can be
classified by the library superadministrator without treating all publications
as normative law. Library-authored standalone acts use the existing editor API.
"""
from fastapi import APIRouter, Depends, Header

from app.core import (
    actor, audit, fail, idempotent, maybe, one, read_db, require, transaction,
    ts,
)

router = APIRouter(prefix="/library/acts", tags=["library-acts"])


@router.post("/{publication_id}/classify")
def classify_act(publication_id: str, body: dict,
                 idempotency_key: str | None = Header(None),
                 current=Depends(actor)):
    require(current, "superadmin")
    if body.get("confirm") is not True:
        fail(422, "CONFIRMATION_REQUIRED",
             "Подтвердите включение опубликованного акта в библиотеку.")
    with transaction() as c:
        def apply():
            pub = one(c, """SELECT * FROM portal_publications
                            WHERE id=? AND status='published'
                            AND portal_id NOT IN ('judiciary','library')""",
                      (publication_id,),
                      "Подходящая официальная публикация не найдена.")
            if maybe(c, "SELECT publication_id FROM library_acts WHERE publication_id=?",
                     (publication_id,)):
                fail(409, "ALREADY_CLASSIFIED",
                     "Публикация уже имеет статус классификации в библиотеке.")
            c.execute("""INSERT INTO library_acts
                       (publication_id,classified_by,classified_at) VALUES(?,?,?)""",
                      (publication_id,current["id"],ts()))
            audit(c,current,"library_act.classify","portal_publication",publication_id,
                  {},{"portal_id":pub["portal_id"],
                      "jurisdiction_id":pub["jurisdiction_id"]})
            return {"id":publication_id,"status":"active"}
        return idempotent(c,current,"library_act.classify:"+publication_id,
                          idempotency_key,body,apply)


@router.post("/{publication_id}/retire")
def retire_classified_act(publication_id: str, body: dict,
                          idempotency_key: str | None = Header(None),
                          current=Depends(actor)):
    require(current,"superadmin")
    if body.get("confirm") is not True:
        fail(422,"CONFIRMATION_REQUIRED",
             "Подтвердите исключение внешней публикации из нормативного каталога.")
    with transaction() as c:
        def apply():
            old=one(c, """SELECT * FROM library_acts
                          WHERE publication_id=? AND status='active'""",
                    (publication_id,))
            c.execute("""UPDATE library_acts SET status='archived',retired_at=?
                         WHERE publication_id=?""",(ts(),publication_id))
            audit(c,current,"library_act.retire","portal_publication",
                  publication_id,old,{"status":"archived"})
            return {"id":publication_id,"status":"archived"}
        return idempotent(c,current,"library_act.retire:"+publication_id,
                          idempotency_key,body,apply)
