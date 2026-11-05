"""Officer elections and committee appointment proposals, using authoritative votes."""
from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from app.core import (allrows, dumps, fail, loads, maybe, one, plain,
                      read_db, transaction, ts, uid)
from app.senate import has_flag, known_users, optional_senate_actor, senate_actor, setting, senate_audit

def audit(c, current, action, object_type, object_id, before=None, after=None):
    senate_audit(c, current, action.removeprefix("senate."), object_type, object_id, before, after)

router = APIRouter(prefix="/senate", tags=["senate-officers"])


def _require_officer(c, current, flag):
    if not has_flag(c, current, flag) and not has_flag(c, current, "superadmin"):
        fail(403, "FORBIDDEN", "Недостаточно полномочий для этой процедуры.")


def _eligible(c):
    return [user for user in known_users(c)
            if has_flag(c, {"id": user["id"], "roles": set(user.get("roles", []))}, "senator")
            and not has_flag(c, {"id": user["id"], "roles": set(user.get("roles", []))}, "lieutenant_governor")]


def _assign_pro_tempore(c, winner):
    # An inactive direct flag overrides grants inherited from a custom group.
    for user in known_users(c):
        if has_flag(c, {"id": user["id"], "roles": set(user.get("roles", []))}, "pro_tempore"):
            c.execute("""INSERT INTO senate_user_flags VALUES(?,'pro_tempore',0)
                ON CONFLICT(user_id,flag) DO UPDATE SET active=0""", (user["id"],))
    c.execute("""INSERT INTO senate_user_flags VALUES(?,'pro_tempore',1)
        ON CONFLICT(user_id,flag) DO UPDATE SET active=1""", (winner,))


def _election(c, election_id):
    return one(c, """SELECT e.*,s.title,d.starts_at,d.ends_at,d.status,d.quorum,d.published_at
        FROM senate_elections e JOIN legislative_sessions s ON s.id=e.session_id
        JOIN senate_session_details d ON d.session_id=e.session_id WHERE e.id=?""", (election_id,))


def election_detail_in_db(c, election_id, current=None):
    from app.senate_sessions import _attendance
    row = _election(c, election_id)
    if row["status"] == "draft":
        if not current or current["id"] == "guest":
            fail(404, "NOT_FOUND", "Выборы не найдены.")
        _require_officer(c, current, "lieutenant_governor")
    candidates = loads(row.pop("candidates_json"), [])
    choices = allrows(c, "SELECT voter_id,choice AS candidate_id FROM vote_choices WHERE vote_id=? ORDER BY voter_id", (row["vote_id"],))
    count = Counter(choice["candidate_id"] for choice in choices)
    row.update({"kind": "election", "candidates": candidates, "choices": choices,
                "tally": [{"candidate_id": candidate["id"], "count": count[candidate["id"]]} for candidate in candidates],
                "attendance": {"present": len(choices), "required": row["quorum"]}, "items": []})
    return row


def finalize_election(c, session_id, current):
    from app.senate_sessions import _date
    row = one(c, "SELECT id FROM senate_elections WHERE session_id=?", (session_id,))
    election = _election(c, row["id"])
    if election["status"] != "published":
        return election
    if _date(election["ends_at"], "ends_at") > datetime.now(timezone.utc):
        fail(409, "SESSION_NOT_ENDED", "Голосование ещё не завершено.")
    candidates = {candidate["id"] for candidate in loads(election["candidates_json"], [])}
    counts = Counter(row["choice"] for row in allrows(c, "SELECT choice FROM vote_choices WHERE vote_id=?", (election["vote_id"],)) if row["choice"] in candidates)
    total = sum(counts.values())
    winner = None
    if total >= election["quorum"] and counts:
        top_count = max(counts.values())
        leaders = [candidate for candidate, count in counts.items() if count == top_count]
        threshold = total // 2 + 1 if election["rule"] == "simple_majority" else election["threshold"]
        if len(leaders) == 1 and top_count >= threshold:
            winner = leaders[0]
    status = "closed" if total >= election["quorum"] else "failed"
    result = "adopted" if winner else ("no_quorum" if status == "failed" else "no_winner")
    c.execute("UPDATE senate_elections SET winner_id=? WHERE id=?", (winner, election["id"]))
    c.execute("UPDATE senate_session_details SET status=? WHERE session_id=?", (status, session_id))
    c.execute("UPDATE votes SET result=? WHERE id=?", (result, election["vote_id"]))
    c.execute("UPDATE bills SET status=? WHERE id=?", ("adopted" if winner else "rejected", election["bill_id"]))
    previous = [{'user_id': user['id']} for user in known_users(c)
                if has_flag(c, {'id': user['id'], 'roles': set(user.get('roles', []))}, 'pro_tempore')]
    if winner:
        _assign_pro_tempore(c, winner)
    senate_audit(c, current, "election.close", "senate_election", election["id"],
          {"status": "published", "previous_officers": previous},
          {"status": status, "winner_id": winner, "result": result, "tally": dict(counts)})
    return _election(c, election["id"])


@router.get("/elections")
def elections(current=Depends(optional_senate_actor)):
    from app.senate_sessions import finalize_due_sessions
    with transaction() as c:
        finalize_due_sessions(c)
        can_view_drafts = current and (has_flag(c, current, "lieutenant_governor") or has_flag(c, current, "superadmin"))
        rows = allrows(c, "SELECT id FROM senate_elections ORDER BY created_at DESC")
        result = []
        for row in rows:
            election = _election(c, row["id"])
            if election["status"] != "draft" or can_view_drafts:
                result.append(election_detail_in_db(c, row["id"], current))
        return result


@router.post("/elections")
def create_election(body: dict, current=Depends(senate_actor)):
    from app.senate_sessions import _date
    title = plain(body.get("title", "Выборы Pro Tempore"), 250)
    if not title:
        fail(422, "TITLE_REQUIRED", "Укажите название выборов.")
    starts = _date(body.get("starts_at"), "starts_at")
    ends = _date(body.get("ends_at"), "ends_at")
    if ends <= starts or ends <= datetime.now(timezone.utc):
        fail(422, "INVALID_SESSION_DATES", "Окончание должно быть позже начала и текущего времени.")
    with transaction() as c:
        # This procedural power belongs to the Lieutenant-Governor, not all senators.
        if not has_flag(c, current, "lieutenant_governor"):
            fail(403, "LIEUTENANT_REQUIRED", "Выборы инициирует Лейтенант-Губернатор.")
        quorum = setting(c, "quorum", 1)
        if "quorum" in body and body["quorum"] != quorum:
            fail(403, "QUORUM_ADMIN_ONLY", "Используется кворум, установленный суперадминистратором.")
        parent = body.get("parent_election_id")
        if parent is not None:
            if not isinstance(parent, str):
                fail(422, "INVALID_PARENT", "Некорректный идентификатор предыдущих выборов.")
            previous = _election(c, parent)
            if previous["status"] not in {"closed", "failed"} or previous["winner_id"]:
                fail(409, "ELECTION_REPEAT_INVALID", "Повтор назначается только для завершённых выборов без победителя.")
        election_id, session_id, bill_id, vote_id = uid(), uid(), uid(), uid()
        c.execute("INSERT INTO bills VALUES(?,?,?,?,?,?)", (bill_id, title, current["id"], "election_draft", "", ts()))
        c.execute("INSERT INTO bill_revisions VALUES(?,?,?,?,?,?)", (uid(), bill_id, 1, "Выборы Pro Tempore", current["id"], ts()))
        c.execute("INSERT INTO legislative_sessions VALUES(?,?,?)", (session_id, title, starts.isoformat()))
        c.execute("INSERT INTO senate_session_details(session_id,starts_at,ends_at,quorum,creator_id) VALUES(?,?,?,?,?)",
                  (session_id, starts.isoformat(), ends.isoformat(), quorum, current["id"]))
        c.execute("INSERT INTO votes VALUES(?,?,?,?,?)", (vote_id, bill_id, session_id, "pending", ts()))
        c.execute("INSERT INTO senate_elections(id,session_id,bill_id,vote_id,parent_election_id,creator_id,created_at) VALUES(?,?,?,?,?,?,?)",
                  (election_id, session_id, bill_id, vote_id, parent, current["id"], ts()))
        senate_audit(c, current, "election.create", "senate_election", election_id, {},
              {"session_id": session_id, "parent_election_id": parent})
        return election_detail_in_db(c, election_id, current)


@router.post("/elections/{election_id}/publish")
def publish_election(election_id: str, current=Depends(senate_actor)):
    from app.senate_sessions import _date
    with transaction() as c:
        if not has_flag(c, current, "lieutenant_governor"):
            fail(403, "LIEUTENANT_REQUIRED", "Публикация выборов доступна Лейтенант-Губернатору.")
        election = _election(c, election_id)
        if election["status"] != "draft":
            fail(409, "AGENDA_FROZEN", "Выборы уже опубликованы.")
        rule = setting(c, "pro_tempore_rule", None)
        threshold = setting(c, "pro_tempore_threshold", None)
        if rule not in {"simple_majority", "absolute_majority", "two_thirds"}:
            fail(409, "ELECTION_RULE_REQUIRED", "Сначала настройте согласованное правило выборов Pro Tempore.")
        if rule != "simple_majority" and (type(threshold) is not int or threshold < 1):
            fail(422, "ELECTION_THRESHOLD_REQUIRED", "Укажите необходимое число голосов за кандидата.")
        now = datetime.now(timezone.utc)
        notice = setting(c, "min_notice_hours", 0)
        if _date(election["starts_at"], "starts_at") < now + timedelta(hours=float(notice)) or _date(election["ends_at"], "ends_at") <= now:
            fail(422, "MIN_NOTICE", "Не соблюдён срок публикации выборов.")
        senators = _eligible(c)
        if not senators:
            fail(409, "NO_CANDIDATES", "В системе нет действующих сенаторов.")
        candidates = [{"id": user["id"], "name": user.get("name") or user["id"]} for user in senators]
        for senator in senators:
            c.execute("INSERT INTO senate_session_electorate VALUES(?,?,0)", (election["session_id"], senator["id"]))
        c.execute("UPDATE senate_elections SET candidates_json=?,rule=?,threshold=? WHERE id=?", (dumps(candidates), rule, threshold, election_id))
        c.execute("UPDATE senate_session_details SET status='published',published_at=?,settings_json=? WHERE session_id=?",
                  (ts(), dumps({"rule": rule, "threshold": threshold}), election["session_id"]))
        c.execute("UPDATE bills SET status='election_published' WHERE id=?", (election["bill_id"],))
        senate_audit(c, current, "election.publish", "senate_election", election_id, {},
              {"candidates": candidates, "rule": rule, "threshold": threshold})
        return election_detail_in_db(c, election_id, current)


@router.get("/elections/{election_id}")
def election_detail(election_id: str, current=Depends(optional_senate_actor)):
    from app.senate_sessions import finalize_due_sessions
    with transaction() as c:
        finalize_due_sessions(c)
        return election_detail_in_db(c, election_id, current)


@router.post("/elections/{election_id}/votes")
def election_vote(election_id: str, body: dict, current=Depends(senate_actor)):
    from app.senate_sessions import _open_session
    candidate = body.get("candidate_id")
    if not isinstance(candidate, str):
        fail(422, "CANDIDATE_REQUIRED", "Выберите кандидата.")
    with transaction() as c:
        election = _election(c, election_id)
        _open_session(c, election["session_id"])
        if not maybe(c, "SELECT user_id FROM senate_session_electorate WHERE session_id=? AND user_id=?", (election["session_id"], current["id"])):
            fail(403, "NOT_IN_ELECTORATE", "Голосуют только сенаторы из опубликованного состава.")
        if candidate not in {row["id"] for row in loads(election["candidates_json"], [])}:
            fail(422, "INVALID_CANDIDATE", "Кандидат не входит в опубликованный список.")
        before = maybe(c, "SELECT choice FROM vote_choices WHERE vote_id=? AND voter_id=?", (election["vote_id"], current["id"]))
        c.execute("""INSERT INTO vote_choices VALUES(?,?,?) ON CONFLICT(vote_id,voter_id)
            DO UPDATE SET choice=excluded.choice""", (election["vote_id"], current["id"], candidate))
        senate_audit(c, current, "election.vote", "senate_election", election_id, before, {"candidate_id": candidate})
        return {"candidate_id": candidate, "saved_at": ts()}


@router.post("/elections/{election_id}/close")
def close_election(election_id: str, current=Depends(senate_actor)):
    with transaction() as c:
        _require_officer(c, current, "lieutenant_governor")
        election = _election(c, election_id)
        if election["status"] == "draft":
            fail(409, "NOT_PUBLISHED", "Выборы ещё не опубликованы.")
        finalize_election(c, election["session_id"], current)
        return election_detail_in_db(c, election_id, current)


def _guard_later_election(c, election):
    if maybe(c, """SELECT e.id FROM senate_elections e JOIN senate_session_details s ON s.session_id=e.session_id
        WHERE e.created_at>? AND s.status IN ('published','needs_review','closed') LIMIT 1""", (election['created_at'],)):
        fail(409, 'NEWER_ELECTION_ACTIVE', 'Есть более поздние выборы. Исправление старой попытки не может менять действующие полномочия.')


@router.post('/elections/{election_id}/admin-correction')
def correct_election_vote(election_id: str, body: dict, current=Depends(senate_actor)):
    from app.senate_sessions import _date
    reason = plain(body.get('reason', ''), 4000)
    voter, candidate = body.get('voter_id'), body.get('candidate_id')
    if not reason or not isinstance(voter, str) or (candidate is not None and not isinstance(candidate, str)):
        fail(422, 'INVALID_CORRECTION', 'Укажите участника, кандидата или снятие голоса и основание исправления.')
    with transaction() as c:
        if not has_flag(c, current, 'superadmin'):
            fail(403, 'FORBIDDEN', 'Исправление доступно суперадминистратору.')
        election = _election(c, election_id)
        if election['status'] == 'draft':
            fail(409, 'NOT_PUBLISHED', 'Выборы ещё не опубликованы.')
        _guard_later_election(c, election)
        one(c, 'SELECT user_id FROM senate_session_electorate WHERE session_id=? AND user_id=?', (election['session_id'], voter))
        if candidate is not None and candidate not in {row['id'] for row in loads(election['candidates_json'], [])}:
            fail(422, 'INVALID_CANDIDATE', 'Кандидат не входит в опубликованный список.')
        before = election_detail_in_db(c, election_id, current)
        if candidate is None:
            c.execute('DELETE FROM vote_choices WHERE vote_id=? AND voter_id=?', (election['vote_id'], voter))
        else:
            c.execute('''INSERT INTO vote_choices VALUES(?,?,?) ON CONFLICT(vote_id,voter_id)
                DO UPDATE SET choice=excluded.choice''', (election['vote_id'], voter, candidate))
        ended = _date(election['ends_at'], 'ends_at') <= datetime.now(timezone.utc)
        if ended or election['status'] != 'published':
            c.execute("UPDATE senate_session_details SET status='needs_review' WHERE session_id=?", (election['session_id'],))
            c.execute("UPDATE votes SET result='needs_review' WHERE id=?", (election['vote_id'],))
            c.execute("UPDATE bills SET status='election_review' WHERE id=?", (election['bill_id'],))
        senate_audit(c, current, 'election.admin_correction', 'senate_election', election_id, before,
                     {'voter_id': voter, 'candidate_id': candidate, 'reason': reason})
        return election_detail_in_db(c, election_id, current)


@router.post('/elections/{election_id}/admin-resolution')
def resolve_election(election_id: str, body: dict, current=Depends(senate_actor)):
    reason, winner = plain(body.get('reason', ''), 4000), body.get('winner_id')
    if not reason or body.get('officeholders_reviewed') is not True:
        fail(422, 'REVIEW_REQUIRED', 'Укажите основание и подтвердите проверку должностных полномочий.')
    with transaction() as c:
        if not has_flag(c, current, 'superadmin'):
            fail(403, 'FORBIDDEN', 'Утвердить исправление может суперадминистратор.')
        election = _election(c, election_id)
        _guard_later_election(c, election)
        if election['status'] != 'needs_review':
            fail(409, 'NO_PENDING_CORRECTION', 'Выборы не ожидают утверждения исправленного результата.')
        candidates = {row['id'] for row in loads(election['candidates_json'], [])}
        if winner is not None and (not isinstance(winner, str) or winner not in candidates):
            fail(422, 'INVALID_CANDIDATE', 'Укажите кандидата из опубликованного списка или отсутствие победителя.')
        before = election_detail_in_db(c, election_id, current)
        if winner:
            before['officeholders'] = [{'user_id': user['id']} for user in known_users(c)
                                      if has_flag(c, {'id': user['id'], 'roles': set(user.get('roles', []))}, 'pro_tempore')]
            _assign_pro_tempore(c, winner)
        elif election['winner_id']:
            c.execute("""INSERT INTO senate_user_flags VALUES(?,'pro_tempore',0)
                ON CONFLICT(user_id,flag) DO UPDATE SET active=0""", (election['winner_id'],))
        total = c.execute('SELECT COUNT(*) FROM vote_choices WHERE vote_id=?', (election['vote_id'],)).fetchone()[0]
        status = 'closed' if winner or total >= election['quorum'] else 'failed'
        result = 'adopted' if winner else ('no_quorum' if status == 'failed' else 'no_winner')
        c.execute('UPDATE senate_elections SET winner_id=? WHERE id=?', (winner, election_id))
        c.execute('UPDATE senate_session_details SET status=? WHERE session_id=?', (status, election['session_id']))
        c.execute('UPDATE votes SET result=? WHERE id=?', (result, election['vote_id']))
        c.execute('UPDATE bills SET status=? WHERE id=?', ('adopted' if winner else 'rejected', election['bill_id']))
        senate_audit(c, current, 'election.admin_resolution', 'senate_election', election_id, before,
                     {'winner_id': winner, 'reason': reason, 'officeholders_reviewed': True})
        return election_detail_in_db(c, election_id, current)


def apply_approved_proposal(c, bill_id, current):
    proposal = maybe(c, "SELECT * FROM senate_committee_proposals WHERE bill_id=?", (bill_id,))
    if not proposal or proposal["applied_at"]:
        return
    members = loads(proposal["members_json"], [])
    c.execute("""INSERT INTO senate_committees(id,name,description,active,created_at) VALUES(?,?,?,1,?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name,description=excluded.description,active=1""",
        (proposal["committee_id"], proposal["name"], proposal["description"], ts()))
    previous = allrows(c, "SELECT * FROM senate_user_flags WHERE flag IN (?,?)",
                      ("committee:" + proposal["committee_id"], "chair:" + proposal["committee_id"]))
    c.execute("UPDATE senate_user_flags SET active=0 WHERE flag IN (?,?)",
              ("committee:" + proposal["committee_id"], "chair:" + proposal["committee_id"]))
    for member in members:
        c.execute("""INSERT INTO senate_user_flags VALUES(?,?,1) ON CONFLICT(user_id,flag) DO UPDATE SET active=1""",
                  (member, "committee:" + proposal["committee_id"]))
    c.execute("INSERT INTO senate_user_flags VALUES(?,?,1) ON CONFLICT(user_id,flag) DO UPDATE SET active=1",
              (proposal["chair_id"], "chair:" + proposal["committee_id"]))
    c.execute("UPDATE senate_committee_proposals SET applied_at=? WHERE bill_id=?", (ts(), bill_id))
    senate_audit(c, current, "committee.proposal.apply", "senate_committee", proposal["committee_id"],
          {"flags": previous}, {"bill_id": bill_id, "members": members, "chair_id": proposal["chair_id"]})

@router.post("/committee-proposals")
def committee_proposal(body: dict, current=Depends(senate_actor)):
    from app.senate import create_bill_in_transaction
    name = plain(body.get("name"), 160)
    description = plain(body.get("description", ""), 4000)
    chair = body.get("chair_id")
    members = body.get("members")
    if not name or not isinstance(chair, str) or not isinstance(members, list) or not members or not all(isinstance(member, str) for member in members):
        fail(422, "PROPOSAL_REQUIRED_FIELDS", "Укажите название, председателя и учётные записи участников.")
    if chair not in members or len(set(members)) != len(members) or len(members) > 100:
        fail(422, "INVALID_COMMITTEE_MEMBERS", "Председатель входит в состав, участники не должны повторяться.")
    with transaction() as c:
        if not has_flag(c, current, "pro_tempore"):
            fail(403, "PRO_TEMPORE_REQUIRED", "Предложения о комитетах подаёт Pro Tempore.")
        if not has_flag(c, current, "senator"):
            fail(403, "SENATOR_REQUIRED", "Автор предложения должен иметь флаг сенатора.")
        users = {user["id"]: user for user in known_users(c)}
        if any(member not in users for member in members):
            fail(422, "UNKNOWN_MEMBER", "Все участники должны быть зарегистрированы.")
        chair_identity = {"id": chair, "roles": set(users[chair].get("roles", []))}
        if not has_flag(c, chair_identity, "senator"):
            fail(422, "CHAIR_SENATOR_REQUIRED", "Председателем назначается сенатор.")
        committee_id = body.get("committee_id")
        if committee_id is not None:
            if not isinstance(committee_id, str):
                fail(422, "INVALID_COMMITTEE", "Некорректный идентификатор комитета.")
            one(c, "SELECT id FROM senate_committees WHERE id=?", (committee_id,))
        else:
            committee_id = uid()
        if maybe(c, "SELECT id FROM senate_committees WHERE name=? AND id!=?", (name, committee_id)):
            fail(409, "COMMITTEE_NAME_EXISTS", "Комитет с таким названием уже существует.")
        if maybe(c, "SELECT bill_id FROM senate_committee_proposals WHERE name=? AND applied_at IS NULL", (name,)):
            fail(409, "PROPOSAL_EXISTS", "Предложение о комитете с таким названием уже находится на рассмотрении.")
        from html import escape
        title = plain(body.get("title", "О составе комитета «" + name + "»"), 250)
        text = "<p>Комитет: " + escape(name) + "</p><p>Полномочия: " + escape(description) + "</p><p>Председатель: " + escape(chair) + "</p><p>Участники: " + escape(", ".join(members)) + "</p>"
        created = create_bill_in_transaction(c, {"title": title, "text_rich": text,
            "document_type": "resolution_simple", "category_id": "general",
            "effective_at": body.get("effective_at", ts()), "changes": []}, current)
        c.execute("INSERT INTO senate_committee_proposals(bill_id,committee_id,name,description,chair_id,members_json) VALUES(?,?,?,?,?,?)",
                  (created["id"], committee_id, name, description, chair, dumps(members)))
        senate_audit(c, current, "committee.proposal.create", "bill", created["id"], {},
              {"committee_id": committee_id, "name": name, "chair_id": chair, "members": members})
        return {**created, "committee_id": committee_id}
