"""Session workflow contract, exercising the real API and database."""
from datetime import datetime, timedelta, timezone

import pytest

from app.core import conn, ts
from app.senate_sessions import tally_votes

API = "/api/v1/senate"
ADMIN = {"X-Demo-User": "superadmin"}
SENATOR = {"X-Demo-User": "legislator"}
READER = {"X-Demo-User": "user"}


def _ready_bill(client, title="Поправка к закону"):
    response = client.post(API + "/bills", headers=SENATOR, json={
        "title": title, "text_rich": "<p>Текст законопроекта</p>",
        "document_type": "senate_bill", "category_id": "general",
        "effective_at": "2027-01-01T00:00:00+00:00", "changes": []})
    assert response.status_code == 200, response.text
    bill_id = response.json()["id"]
    c = conn()
    c.execute("UPDATE bills SET status='ready_plenary' WHERE id=?", (bill_id,))
    c.close()
    return bill_id


def _agenda(client, bill_ids, quorum=1):
    now = datetime.now(timezone.utc)
    response = client.post(API + "/sessions", headers=ADMIN, json={
        "title": "Пленарное заседание", "starts_at": (now + timedelta(hours=1)).isoformat(),
        "ends_at": (now + timedelta(hours=2)).isoformat(), "bill_ids": bill_ids, "quorum": quorum})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _publish(client, session_id):
    response = client.post(API + "/sessions/" + session_id + "/publish", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def _end(session_id):
    c = conn()
    c.execute("UPDATE senate_session_details SET ends_at=? WHERE session_id=?",
              ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), session_id))
    c.close()


def test_vote_rules_are_not_conflated():
    choices = [{"choice": choice, "is_lieutenant": 0} for choice in ["yes", "yes", "no", "abstain"]]
    assert tally_votes(choices, "predominantly_for", None)[1] is True
    assert tally_votes(choices, "simple_majority", None)[1] is False
    assert tally_votes(choices, "absolute_majority", 2)[1] is True
    assert tally_votes(choices, "two_thirds", 3)[1] is False


def test_lieutenant_vote_only_breaks_yes_no_tie():
    votes = [{"choice": "yes", "is_lieutenant": 0}, {"choice": "no", "is_lieutenant": 0},
             {"choice": "yes", "is_lieutenant": 1}]
    tally, passed = tally_votes(votes, "predominantly_for", None)
    assert passed and tally["yes"] == 2 and tally["deciding_vote"] == "yes"
    votes.append({"choice": "yes", "is_lieutenant": 0})
    tally, passed = tally_votes(votes, "predominantly_for", None)
    assert passed and tally["yes"] == 2 and tally["deciding_vote"] is None


def test_draft_privacy_publish_and_frozen_electorate(client):
    bill_id = _ready_bill(client)
    session_id = _agenda(client, [bill_id])
    assert client.get(API + "/sessions/" + session_id).status_code == 404
    assert client.get(API + "/sessions").json() == []
    assert client.get(API + "/bills/" + bill_id).json()["votes"] == []
    data = _publish(client, session_id)
    assert data["status"] == "published"
    assert data["items"][0]["version"] == 1
    assert client.post(API + "/sessions/" + session_id + "/publish", headers=ADMIN).status_code == 409
    assert client.post(API + "/sessions/" + session_id + "/votes", headers=ADMIN,
                       json={"bill_id": bill_id, "choice": "yes"}).status_code == 403
    assert client.post(API + "/sessions/" + session_id + "/votes", headers=READER,
                       json={"bill_id": bill_id, "choice": "yes"}).status_code == 403
    # Vote is available immediately after publication, before the game meeting starts.
    assert client.post(API + "/sessions/" + session_id + "/votes", headers=SENATOR,
                       json={"bill_id": bill_id, "choice": "yes"}).status_code == 200
    # Changing category configuration does not change the published snapshot.
    c = conn()
    c.execute("UPDATE senate_categories SET plenary_rule='absolute_majority',plenary_threshold=99 WHERE id='general'")
    c.close()
    data = client.get(API + "/sessions/" + session_id).json()
    assert data["items"][0]["rule"] == "simple_majority"


def test_published_item_title_uses_frozen_revision_after_later_correction(client):
    bill_id = _ready_bill(client, 'Исходное название')
    session_id = _agenda(client, [bill_id])
    _publish(client, session_id)
    client.post(API + '/sessions/' + session_id + '/votes', headers=SENATOR,
                json={'bill_id': bill_id, 'choice': 'yes'})
    _end(session_id)
    client.get(API + '/sessions/' + session_id)
    response = client.post(API + '/admin/bills/' + bill_id + '/correct', headers=ADMIN,
                           json={'title': 'Позднее исправленное название', 'base_version': 1, 'reason': 'Исправление после заседания'})
    assert response.status_code == 200, response.text
    assert client.get(API + '/sessions/' + session_id).json()['items'][0]['title'] == 'Исходное название'


def test_absence_cascades_then_new_vote_clears_other_marks(client):
    first, second = _ready_bill(client, "Первый"), _ready_bill(client, "Второй")
    session_id = _agenda(client, [first, second])
    _publish(client, session_id)
    url = API + "/sessions/" + session_id
    for bill, choice in [(first, "yes"), (second, "absent")]:
        response = client.post(url + "/votes", headers=SENATOR, json={"bill_id": bill, "choice": choice})
        assert response.status_code == 200, response.text
    data = client.get(url).json()
    assert data["attendance"]["present"] == 0
    assert all(item["choices"][0]["choice"] == "absent" for item in data["items"])
    response = client.post(url + "/votes", headers=SENATOR, json={"bill_id": first, "choice": "no"})
    assert response.status_code == 200, response.text
    data = client.get(url).json()
    assert data["attendance"]["present"] == 1
    assert data["items"][0]["choices"][0]["choice"] == "no"
    assert data["items"][1]["choices"] == []
    c = conn()
    assert c.execute("SELECT COUNT(*) FROM audit_events WHERE action='senate.vote.cast'").fetchone()[0] == 3
    c.close()


def test_deadline_closes_idempotently_and_rejects_late_vote(client):
    bill = _ready_bill(client)
    session_id = _agenda(client, [bill])
    _publish(client, session_id)
    url = API + "/sessions/" + session_id
    assert client.post(url + "/close", headers=ADMIN).status_code == 409
    assert client.post(url + "/votes", headers=SENATOR, json={"bill_id": bill, "choice": "yes"}).status_code == 200
    _end(session_id)
    assert client.post(url + "/votes", headers=SENATOR, json={"bill_id": bill, "choice": "no"}).status_code == 409
    data = client.get(url).json()
    assert data["status"] == "closed" and data["items"][0]["result"] == "adopted"
    assert client.post(url + "/close", headers=ADMIN).status_code == 200
    c = conn()
    assert c.execute("SELECT status FROM bills WHERE id=?", (bill,)).fetchone()[0] == "adopted"
    assert c.execute("SELECT COUNT(*) FROM bill_events WHERE bill_id=? AND event_type='senate.vote.closed'", (bill,)).fetchone()[0] == 1
    c.close()


def test_insufficient_quorum_returns_all_items_and_preserves_transcripts(client):
    bill = _ready_bill(client)
    session_id = _agenda(client, [bill], quorum=2)
    _publish(client, session_id)
    url = API + "/sessions/" + session_id
    client.post(url + "/votes", headers=SENATOR, json={"bill_id": bill, "choice": "yes"})
    assert client.put(url + "/transcript", headers=ADMIN, json={"text": "Рано"}).status_code == 409
    _end(session_id)
    data = client.get(url).json()
    assert data["status"] == "failed" and data["items"][0]["result"] == "no_quorum"
    for text in ["Первая стенограмма", "Исправленная стенограмма"]:
        response = client.put(url + "/transcript", headers=ADMIN, json={"text": text})
        assert response.status_code == 200, response.text
    data = client.get(url).json()
    assert data["transcript"] == "Исправленная стенограмма" and len(data["transcript_versions"]) == 2
    c = conn()
    assert c.execute("SELECT status FROM bills WHERE id=?", (bill,)).fetchone()[0] == "ready_plenary"
    c.close()
    exported = client.get(url + "/export.pdf")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "application/pdf"
    assert exported.content.startswith(b"%PDF-")


def test_session_mutations_require_officer_role(client):
    bill = _ready_bill(client)
    now = datetime.now(timezone.utc)
    response = client.post(API + "/sessions", headers=SENATOR, json={"title": "Нет полномочий",
        "starts_at": (now+timedelta(hours=1)).isoformat(), "ends_at": (now+timedelta(hours=2)).isoformat(),
        "bill_ids": [bill]})
    assert response.status_code == 403



def test_admin_correction_requires_permission_reason_and_cascades_absence(client):
    first, second = _ready_bill(client, "Первый"), _ready_bill(client, "Второй")
    session_id = _agenda(client, [first, second])
    _publish(client, session_id)
    url = API + "/sessions/" + session_id
    body = {"bill_id": first, "voter_id": "demo-legislator", "choice": "absent", "reason": "Исправление записи присутствия"}
    assert client.post(url+"/admin-correction", headers=SENATOR, json=body).status_code == 403
    assert client.post(url+"/admin-correction", headers=ADMIN, json={**body, "reason":""}).status_code == 422
    result = client.post(url+"/admin-correction", headers=ADMIN, json=body)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["status"] == "published" and data["attendance"]["present"] == 0
    assert all(item["choices"][0]["choice"] == "absent" for item in data["items"])
    result = client.post(url+"/admin-correction", headers=ADMIN, json={**body, "choice":"yes"})
    assert result.status_code == 200
    assert result.json()["items"][1]["choices"] == []
    c = conn()
    audit_rows = c.execute("SELECT before_json,after_json FROM audit_events WHERE action='senate.vote.admin_correction'").fetchall()
    assert len(audit_rows) == 2 and "Исправление записи присутствия" in audit_rows[0]["after_json"]
    c.close()


def test_post_close_correction_requires_explicit_consistent_resolution(client):
    bill = _ready_bill(client)
    session_id = _agenda(client, [bill])
    _publish(client, session_id)
    url = API + "/sessions/" + session_id
    client.post(url+"/votes", headers=SENATOR, json={"bill_id":bill,"choice":"yes"})
    _end(session_id)
    assert client.get(url).json()["items"][0]["result"] == "adopted"
    corrected = client.post(url+"/admin-correction", headers=ADMIN, json={"bill_id":bill,
        "voter_id":"demo-legislator", "choice":"no", "reason":"Ошибка стенограммы"})
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["status"] == "needs_review"
    assert corrected.json()["items"][0]["result"] == "needs_review"
    assert corrected.json()["items"][0]["tally"]["no"] == 1
    assert client.post(url+"/admin-resolution", headers=ADMIN, json={"reason":"Проверено","results":{}}).status_code == 422
    resolved = client.post(url+"/admin-resolution", headers=ADMIN,
        json={"reason":"Проверено по стенограмме", "results":{bill:"rejected"}})
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "closed" and resolved.json()["items"][0]["result"] == "rejected"
    c = conn()
    assert c.execute("SELECT COUNT(*) FROM adopted_acts WHERE bill_id=?", (bill,)).fetchone()[0] == 1
    assert c.execute("SELECT p.status FROM portal_publications p JOIN adopted_acts a ON a.publication_id=p.id WHERE a.bill_id=?", (bill,)).fetchone()[0] == "superseded"
    assert c.execute("SELECT status FROM bills WHERE id=?", (bill,)).fetchone()[0] == "rejected"
    assert c.execute("SELECT COUNT(*) FROM audit_events WHERE action='senate.vote.admin_resolution'").fetchone()[0] == 1
    c.close()


def test_old_session_correction_cannot_overwrite_newer_active_agenda(client):
    bill = _ready_bill(client)
    original = _agenda(client, [bill])
    _publish(client, original)
    original_url = API + "/sessions/" + original
    _end(original)
    assert client.get(original_url).json()["status"] == "failed"
    newer = _agenda(client, [bill])
    correction = client.post(original_url+"/admin-correction", headers=ADMIN, json={
        "bill_id":bill,"voter_id":"demo-legislator","choice":"yes","reason":"Проверка старого протокола"})
    assert correction.status_code == 409, correction.text
    assert correction.json()["error"]["code"] == "NEWER_SESSION_ACTIVE"
    assert correction.json()["error"]["details"]["session_id"] == newer
    # The same conflict must block final resolution of a manually reopened review.
    c = conn()
    c.execute("UPDATE senate_session_details SET status='needs_review' WHERE session_id=?", (original,))
    c.close()
    resolution = client.post(original_url+"/admin-resolution", headers=ADMIN, json={
        "reason":"Старый итог уточнён","results":{bill:"adopted"}})
    assert resolution.status_code == 409, resolution.text
    assert resolution.json()["error"]["code"] == "NEWER_SESSION_ACTIVE"
    c = conn()
    assert c.execute("SELECT status FROM bills WHERE id=?", (bill,)).fetchone()[0] == "ready_plenary"
    assert c.execute("SELECT result FROM votes WHERE session_id=?", (original,)).fetchone()[0] == "no_quorum"
    assert c.execute("SELECT COUNT(*) FROM vote_choices WHERE vote_id IN (SELECT id FROM votes WHERE session_id=?)", (original,)).fetchone()[0] == 0
    c.close()
