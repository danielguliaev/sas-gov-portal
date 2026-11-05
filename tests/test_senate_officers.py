from datetime import datetime, timedelta, timezone

from app.core import conn

API = "/api/v1/senate"
ADMIN = {"X-Demo-User": "superadmin"}
LIEUTENANT = {"X-Demo-User": "user"}
SENATOR = {"X-Demo-User": "legislator"}
SECOND = {"X-Demo-User": "lawyer"}


def _flag(user_id, flag, active=1):
    c = conn()
    c.execute("INSERT INTO senate_user_flags VALUES(?,?,?) ON CONFLICT(user_id,flag) DO UPDATE SET active=excluded.active", (user_id, flag, active))
    c.close()


def _policy():
    c = conn()
    c.execute("UPDATE senate_settings SET setting_value='\"simple_majority\"' WHERE setting_key='pro_tempore_rule'")
    c.close()


def _election(client, parent=None):
    _flag("demo-user", "lieutenant_governor")
    now = datetime.now(timezone.utc)
    body = {"title": "Выборы председателя", "starts_at": (now+timedelta(hours=1)).isoformat(),
            "ends_at": (now+timedelta(hours=2)).isoformat()}
    if parent:
        body["parent_election_id"] = parent
    response = client.post(API+"/elections", json=body, headers=LIEUTENANT)
    assert response.status_code == 200, response.text
    return response.json()


def _end(session_id):
    c = conn()
    c.execute("UPDATE senate_session_details SET ends_at=? WHERE session_id=?",
              ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(), session_id))
    c.close()


def test_election_requires_lieutenant_and_explicit_policy(client):
    now = datetime.now(timezone.utc)
    body = {"starts_at": (now+timedelta(hours=1)).isoformat(), "ends_at": (now+timedelta(hours=2)).isoformat()}
    assert client.post(API+"/elections", headers=ADMIN, json=body).status_code == 403
    election = _election(client)
    url = API+"/elections/"+election["id"]
    assert client.get(url).status_code == 404
    blocked = client.post(url+"/publish", headers=LIEUTENANT)
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "ELECTION_RULE_REQUIRED"
    _policy()
    published = client.post(url+"/publish", headers=LIEUTENANT)
    assert published.status_code == 200, published.text
    assert {candidate["id"] for candidate in published.json()["candidates"]} == {"demo-legislator"}
    assert client.post(url+"/votes", headers=LIEUTENANT, json={"candidate_id":"demo-legislator"}).status_code == 403


def test_election_assigns_winner_only_once_and_keeps_named_ballot(client):
    _policy()
    election = _election(client)
    url = API+"/elections/"+election["id"]
    client.post(url+"/publish", headers=LIEUTENANT)
    response = client.post(url+"/votes", headers=SENATOR, json={"candidate_id":"demo-legislator"})
    assert response.status_code == 200, response.text
    _end(election["session_id"])
    result = client.get(url).json()
    assert result["winner_id"] == "demo-legislator"
    assert result["choices"] == [{"voter_id":"demo-legislator","candidate_id":"demo-legislator"}]
    assert client.post(url+"/close", headers=LIEUTENANT).status_code == 200
    assert client.post(url+"/votes", headers=SENATOR, json={"candidate_id":"demo-legislator"}).status_code == 409
    c = conn()
    assert c.execute("SELECT active FROM senate_user_flags WHERE user_id='demo-legislator' AND flag='pro_tempore'").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM audit_events WHERE action='senate.election.close'").fetchone()[0] == 1
    c.close()


def test_election_replaces_officer_inherited_from_custom_group(client):
    _policy()
    client.post(API + '/admin/flag-types', headers=ADMIN,
                json={'id': 'officers', 'name': 'Председатели', 'permissions': ['pro_tempore']})
    _flag('demo-lawyer', 'officers')
    election = _election(client)
    url = API + '/elections/' + election['id']
    client.post(url + '/publish', headers=LIEUTENANT)
    client.post(url + '/votes', headers=SENATOR, json={'candidate_id': 'demo-legislator'})
    _end(election['session_id'])
    assert client.get(url).json()['winner_id'] == 'demo-legislator'
    assert 'pro_tempore' not in client.get(API + '/me', headers=SECOND).json()['flags']
    c = conn()
    before = c.execute("SELECT before_json FROM senate_audit WHERE action='senate.election.close'").fetchone()[0]
    c.close()
    assert 'demo-lawyer' in before


def test_closed_election_ballot_correction_requires_explicit_office_review(client):
    _policy()
    _flag('demo-lawyer', 'senator')
    election = _election(client)
    url = API + '/elections/' + election['id']
    client.post(url + '/publish', headers=LIEUTENANT)
    client.post(url + '/votes', headers=SENATOR, json={'candidate_id': 'demo-legislator'})
    _end(election['session_id'])
    assert client.get(url).json()['winner_id'] == 'demo-legislator'
    payload = {'voter_id': 'demo-legislator', 'candidate_id': 'demo-lawyer', 'reason': 'Ошибка записи голоса'}
    assert client.post(url + '/admin-correction', headers=SENATOR, json=payload).status_code == 403
    corrected = client.post(url + '/admin-correction', headers=ADMIN, json=payload)
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()['status'] == 'needs_review'
    assert 'pro_tempore' in client.get(API + '/me', headers=SENATOR).json()['flags']
    resolution = {'winner_id': 'demo-lawyer', 'reason': 'Проверена запись открытого голосования'}
    assert client.post(url + '/admin-resolution', headers=ADMIN, json=resolution).status_code == 422
    result = client.post(url + '/admin-resolution', headers=ADMIN, json={**resolution, 'officeholders_reviewed': True})
    assert result.status_code == 200, result.text
    assert result.json()['winner_id'] == 'demo-lawyer'
    assert 'pro_tempore' not in client.get(API + '/me', headers=SENATOR).json()['flags']
    assert 'pro_tempore' in client.get(API + '/me', headers=SECOND).json()['flags']
    audit = client.get(API + '/admin', headers=ADMIN).json()['audit']
    assert any(row['action'] == 'senate.election.admin_resolution' for row in audit)


def test_old_election_correction_cannot_change_newer_election(client):
    _policy()
    election = _election(client)
    url = API + '/elections/' + election['id']
    client.post(url + '/publish', headers=LIEUTENANT)
    _end(election['session_id'])
    client.get(url)
    newer = _election(client)
    assert client.post(API + '/elections/' + newer['id'] + '/publish', headers=LIEUTENANT).status_code == 200
    response = client.post(url + '/admin-correction', headers=ADMIN,
                           json={'voter_id': 'demo-legislator', 'candidate_id': 'demo-legislator', 'reason': 'Поздняя проверка'})
    assert response.status_code == 409 and response.json()['error']['code'] == 'NEWER_ELECTION_ACTIVE'


def test_tie_repeat_preserves_first_votes_and_parent(client):
    _policy()
    _flag("demo-lawyer", "senator")
    election = _election(client)
    url = API+"/elections/"+election["id"]
    published = client.post(url+"/publish", headers=LIEUTENANT).json()
    assert len(published["candidates"]) == 2
    for headers, candidate in [(SENATOR,"demo-legislator"),(SECOND,"demo-lawyer")]:
        assert client.post(url+"/votes", headers=headers, json={"candidate_id":candidate}).status_code == 200
    _end(election["session_id"])
    result = client.get(url).json()
    assert result["status"] == "closed" and result["winner_id"] is None
    repeated = _election(client, election["id"])
    assert repeated["parent_election_id"] == election["id"]
    assert client.get(url).json()["choices"] == result["choices"]


def test_approved_committee_proposal_grants_flags_only_after_plenary(client):
    _flag("demo-legislator", "pro_tempore")
    response = client.post(API+"/committee-proposals", headers=SENATOR, json={
        "name":"Комитет по бюджету", "description":"Бюджетные вопросы", "chair_id":"demo-legislator",
        "members":["demo-legislator","demo-user"]})
    assert response.status_code == 200, response.text
    proposal = response.json()
    c = conn()
    assert c.execute("SELECT COUNT(*) FROM senate_user_flags WHERE flag=?", ("committee:"+proposal["committee_id"],)).fetchone()[0] == 0
    c.execute("UPDATE bills SET status='ready_plenary' WHERE id=?", (proposal["id"],))
    c.close()
    now = datetime.now(timezone.utc)
    agenda = client.post(API+"/sessions", headers=ADMIN, json={"title":"Утверждение комитета",
        "starts_at":(now+timedelta(hours=1)).isoformat(),"ends_at":(now+timedelta(hours=2)).isoformat(),
        "bill_ids":[proposal["id"]]}).json()
    url = API+"/sessions/"+agenda["id"]
    assert client.post(url+"/publish", headers=ADMIN).status_code == 200
    assert client.post(url+"/votes", headers=SENATOR, json={"bill_id":proposal["id"],"choice":"yes"}).status_code == 200
    _end(agenda["id"])
    assert client.get(url).json()["status"] == "closed"
    c = conn()
    flags = {row["flag"] for row in c.execute("SELECT flag FROM senate_user_flags WHERE user_id='demo-legislator' AND active=1")}
    assert "committee:"+proposal["committee_id"] in flags
    assert "chair:"+proposal["committee_id"] in flags
    assert c.execute("SELECT name FROM senate_committees WHERE id=?", (proposal["committee_id"],)).fetchone()[0] == "Комитет по бюджету"
    assert c.execute("SELECT applied_at FROM senate_committee_proposals WHERE bill_id=?", (proposal["id"],)).fetchone()[0]
    c.close()


def test_reversing_applied_proposal_requires_explicit_flag_review(client):
    _flag("demo-legislator", "pro_tempore")
    created = client.post(API+"/committee-proposals", headers=SENATOR, json={
        "name":"Комитет по транспорту", "description":"Транспорт", "chair_id":"demo-legislator",
        "members":["demo-legislator","demo-user"]}).json()
    c = conn()
    c.execute("UPDATE bills SET status='ready_plenary' WHERE id=?", (created["id"],))
    c.close()
    now = datetime.now(timezone.utc)
    agenda = client.post(API+"/sessions", headers=ADMIN, json={"title":"Утверждение комитета",
        "starts_at":(now+timedelta(hours=1)).isoformat(),"ends_at":(now+timedelta(hours=2)).isoformat(),
        "bill_ids":[created["id"]]}).json()
    url = API+"/sessions/"+agenda["id"]
    client.post(url+"/publish", headers=ADMIN)
    client.post(url+"/votes", headers=SENATOR, json={"bill_id":created["id"],"choice":"yes"})
    _end(agenda["id"])
    assert client.get(url).json()["status"] == "closed"
    corrected = client.post(url+"/admin-correction", headers=ADMIN, json={"bill_id":created["id"],
        "voter_id":"demo-legislator", "choice":"no", "reason":"Уточнение протокола"})
    assert corrected.status_code == 200, corrected.text
    resolution = {"reason":"Решение исправлено", "results":{created["id"]:"rejected"}}
    response = client.post(url+"/admin-resolution", headers=ADMIN, json=resolution)
    assert response.status_code == 409 and response.json()["error"]["code"] == "COMMITTEE_ACTIONS_REVIEW_REQUIRED"
    # The admin explicitly reviews already-issued flags; no automatic mass rollback.
    response = client.post(url+"/admin-resolution", headers=ADMIN, json={**resolution,"committee_actions_reviewed":True})
    assert response.status_code == 200, response.text
    c = conn()
    assert c.execute("SELECT active FROM senate_user_flags WHERE user_id='demo-user' AND flag=?",
                     ("committee:"+created["committee_id"],)).fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM senate_audit WHERE action='senate.vote.admin_resolution'").fetchone()[0] == 1
    c.close()


def test_election_pdf_export_has_actual_pdf_for_frozen_candidates(client):
    _policy()
    election = _election(client)
    url = API+"/elections/"+election["id"]
    assert client.post(url+"/publish", headers=LIEUTENANT).status_code == 200
    assert client.post(url+"/votes", headers=SENATOR, json={"candidate_id":"demo-legislator"}).status_code == 200
    _end(election["session_id"])
    exported = client.get(API+"/sessions/"+election["session_id"]+"/export.pdf")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "application/pdf"
    assert exported.content.startswith(b"%PDF-")
    assert len(exported.content) > 1000
    assert client.get(url).json()["winner_id"] == "demo-legislator"
