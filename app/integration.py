"""Read-only, separately authenticated integration feed for future game clients.

No punishment computation or game-specific decision rules live in this module.
Set SERVICE_API_KEY_SHA256 to the SHA-256 hex digest of a randomly generated
secret. Do not reuse user JWTs or the developer demo role header.
"""
import hashlib
import hmac
import os

from fastapi import APIRouter, Depends, Header, Query

from app.core import allrows, fail, loads, one, read_db, sanctions_of

router = APIRouter(prefix="/integration", tags=["integration"])


def service_actor(authorization: str | None = Header(None)):
    expected = os.getenv("SERVICE_API_KEY_SHA256", "").strip().lower()
    if len(expected) != 64 or not authorization or not authorization.startswith("Bearer "):
        fail(401, "SERVICE_AUTH_REQUIRED", "Требуется отдельный токен интеграционного чтения.")
    candidate = hashlib.sha256(authorization[7:].encode()).hexdigest()
    if not hmac.compare_digest(candidate, expected):
        fail(401, "SERVICE_AUTH_REQUIRED", "Неверный токен интеграционного чтения.")
    return "integration_reader"


@router.get("/codes")
def integration_codes(service=Depends(service_actor)):
    with read_db() as c:
        return allrows(c, """SELECT id,jurisdiction_id,name,abbreviation
                             FROM codes WHERE status='active' ORDER BY jurisdiction_id,name,id""")


@router.get("/articles")
def integration_articles(code_id: str, after_id: str | None = None,
                         limit: int = Query(100, ge=1, le=200),
                         service=Depends(service_actor)):
    """Stable-ID keyset paging independent from article legal numbers."""
    with read_db() as c:
        one(c, "SELECT id FROM codes WHERE id=? AND status='active'", (code_id,))
        rows = allrows(c, """SELECT id,code_id,parent_id,number,title,criminal_class,
                            categories_json,prison_min_months,prison_max_months,
                            fine_min_usd,fine_max_usd
                            FROM articles WHERE code_id=? AND status='active' AND id>?
                            ORDER BY id LIMIT ?""",
                       (code_id, after_id or "", limit+1))
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {"items": [{
            "id": r["id"], "code_id": r["code_id"], "parent_id": r["parent_id"],
            "number": r["number"], "title": r["title"],
            "categories": loads(r["categories_json"], []),
            "criminal_class": r["criminal_class"], "sanctions": sanctions_of(r)
        } for r in rows],
                "has_more": has_more,
                "next_after_id": rows[-1]["id"] if has_more and rows else None}


@router.get("/articles/{article_id}")
def integration_article(article_id: str, service=Depends(service_actor)):
    with read_db() as c:
        a = one(c, "SELECT * FROM articles WHERE id=? AND status='active'", (article_id,))
        links = allrows(c, """SELECT r.id,r.type_id,r.source_id,r.target_id
                              FROM article_relations r
                              JOIN articles source ON source.id=r.source_id
                              JOIN articles target ON target.id=r.target_id
                              WHERE r.active=1 AND source.status='active'
                              AND target.status='active'
                              AND (r.source_id=? OR r.target_id=?)
                              ORDER BY r.id""", (article_id, article_id))
        return {
            "id": a["id"], "code_id": a["code_id"], "parent_id": a["parent_id"],
            "number": a["number"], "title": a["title"], "text_rich": a["text_rich"],
            "revision_id": a["revision_id"], "categories": loads(a["categories_json"], []),
            "criminal_class": a["criminal_class"], "sanctions": sanctions_of(a),
            "custom_values": loads(a["custom_values"], {}), "relations": links,
        }
