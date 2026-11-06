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
    return one(c, """SELECT e.*,b.title,d.starts_at,d.ends_at,
        CASE WHEN v.result='needs_review' THEN 'needs_review'
        WHEN v.result!='pending' AND d.status='needs_review' THEN 'closed' ELSE COALESCE(d.status,b.status) END AS status,
        d.quorum,d.published_at FROM senate_elections e JOIN bills b ON b.id=e.bill_id
        LEFT JOIN senate_session_details d ON d.session_id=e.session_id
        LEFT JOIN votes v ON v.id=e.vote_id WHERE e.id=?""", (election_id,))


def election_detail_in_db(c, election_id, current=None):
    from app.senate_sessions import visible_session
    row = _election(c, election_id)
    if row['status'] == 'draft':
        from app.senate_sessions import can_manage_sessions
        if not current or not can_manage_sessions(c, current):
            fail(404, 'NOT_FOUND', 'Повестка ещё не опубликована.')
    candidates = loads(row.pop('candidates_json'), [])
    choices = allrows(c, 'SELECT voter_id,choice AS candidate_id FROM vote_choices WHERE vote_id=? ORDER BY voter_id', (row['vote_id'],))
    hidden = row['status'] not in {'closed', 'failed'}
    count = Counter(choice['candidate_id'] for choice in choices)
    mine = next((r['candidate_id'] for r in choices if current and r['voter_id'] == current['id']), None)
    row.update(kind='election', candidates=candidates, my_choice=mine, results_hidden=hidden,
        choices=[] if hidden else choices,
        tally=None if hidden else [{'candidate_id': candidate['id'], 'count': count[candidate['id']]} for candidate in candidates])
    row['can_vote'] = bool(current and maybe(c, 'SELECT 1 FROM senate_session_electorate WHERE session_id=? AND user_id=? AND is_lieutenant=0', (row['session_id'], current['id'])))
    if hidden:
        row['winner_id'] = None
    return visible_session(c, row, current)


def publish_election_item(c, election_id, senators, current):
    election = _election(c, election_id)
    if not senators:
        fail(409, 'NO_CANDIDATES', 'В системе нет действующих сенаторов.')
    candidates = [{'id': user['id'], 'name': user.get('name') or user['id']} for user in senators]
    rule = setting(c, 'pro_tempore_rule', 'simple_majority') or 'simple_majority'
    threshold = setting(c, 'pro_tempore_threshold', None)
    if rule not in {'simple_majority', 'absolute_majority', 'two_thirds'}:
        fail(422, 'INVALID_ELECTION_RULE', 'Неверное правило выборов про темпоре.')
    if rule != 'simple_majority' and (type(threshold) is not int or threshold < 1):
        fail(422, 'ELECTION_THRESHOLD_REQUIRED', 'Укажите число голосов за кандидата в настройках.')
    c.execute('UPDATE senate_elections SET candidates_json=?,rule=?,threshold=? WHERE id=?', (dumps(candidates), rule, threshold, election_id))
    c.execute('UPDATE senate_session_items SET rule=?,threshold=? WHERE vote_id=?', (rule, threshold, election['vote_id']))
    senate_audit(c, current, 'election.publish', 'senate_election', election_id, {}, {'candidates': candidates, 'rule': rule, 'threshold': threshold})


def finalize_election_item(c, election_id, current, quorum_met):
    election = _election(c, election_id)
    if one(c, 'SELECT result FROM votes WHERE id=?', (election['vote_id'],))['result'] != 'pending':
        return
    candidates = {candidate['id'] for candidate in loads(election['candidates_json'], [])}
    choices = allrows(c, 'SELECT vc.choice FROM vote_choices vc JOIN senate_session_electorate e ON e.session_id=? AND e.user_id=vc.voter_id WHERE vc.vote_id=? AND e.is_lieutenant=0', (election['session_id'], election['vote_id']))
    counts = Counter(r['choice'] for r in choices if r['choice'] in candidates)
    present = sum(r['choice'] != 'absent' for r in choices)
    winner = None
    if quorum_met and counts:
        leaders = [candidate for candidate, count in counts.items() if count == max(counts.values())]
        threshold = present // 2 + 1 if election['rule'] == 'simple_majority' else election['threshold']
        if len(leaders) == 1 and counts[leaders[0]] >= threshold:
            winner = leaders[0]
    result = 'adopted' if winner else ('no_winner' if quorum_met else 'no_quorum')
    c.execute('UPDATE senate_elections SET winner_id=? WHERE id=?', (winner, election_id))
    c.execute('UPDATE votes SET result=? WHERE id=?', (result, election['vote_id']))
    c.execute('UPDATE bills SET status=? WHERE id=?', ('adopted' if winner else 'rejected', election['bill_id']))
    previous = [{'user_id': user['id']} for user in known_users(c)
                if has_flag(c, {'id': user['id'], 'roles': set(user.get('roles', []))}, 'pro_tempore')]
    if winner:
        _assign_pro_tempore(c, winner)
    senate_audit(c, current, 'election.close', 'senate_election', election_id,
        {'status': 'published', 'previous_officers': previous}, {'winner_id': winner, 'result': result, 'tally': dict(counts)})


@router.get('/elections')
def elections(current=Depends(optional_senate_actor)):
    from app.senate_sessions import finalize_due_sessions
    with transaction() as c:
        finalize_due_sessions(c)
        from app.senate_sessions import can_manage_sessions
        return [election_detail_in_db(c, row['id'], current) for row in allrows(c, 'SELECT id FROM senate_elections ORDER BY created_at DESC')
                if _election(c, row['id'])['status'] != 'draft' or (current and can_manage_sessions(c, current))]


@router.post('/elections')
def create_election(body: dict, current=Depends(senate_actor)):
    title = plain(body.get('title', 'Выборы про темпоре'), 250)
    if not title:
        fail(422, 'TITLE_REQUIRED', 'Укажите название вопроса.')
    with transaction() as c:
        if not has_flag(c, current, 'lieutenant_governor'):
            fail(403, 'LIEUTENANT_REQUIRED', 'Предмет о выборах инициирует только лейтенант-губернатор.')
        parent = body.get('parent_election_id') or None
        if parent:
            previous = _election(c, parent)
            if previous['status'] not in {'closed', 'failed', 'cancelled'} or previous['winner_id']:
                fail(409, 'ELECTION_REPEAT_INVALID', 'Повторное голосование связывается с завершённой попыткой без победителя.')
        election_id, bill_id = uid(), uid()
        c.execute('INSERT INTO bills VALUES(?,?,?,?,?,?)', (bill_id, title, current['id'], 'ready_plenary', '', ts()))
        c.execute('INSERT INTO bill_revisions VALUES(?,?,?,?,?,?)', (uid(), bill_id, 1, 'Выборы про темпоре', current['id'], ts()))
        c.execute('INSERT INTO senate_elections(id,bill_id,parent_election_id,creator_id,created_at) VALUES(?,?,?,?,?)', (election_id, bill_id, parent, current['id'], ts()))
        senate_audit(c, current, 'election.create', 'senate_election', election_id, {}, {'parent_election_id': parent, 'bill_id': bill_id})
        return election_detail_in_db(c, election_id, current)


@router.get('/elections/{election_id}')
def election_detail(election_id: str, current=Depends(optional_senate_actor)):
    from app.senate_sessions import finalize_due_sessions
    with transaction() as c:
        finalize_due_sessions(c)
        return election_detail_in_db(c, election_id, current)


@router.post('/elections/{election_id}/votes')
def election_vote(election_id: str, body: dict, current=Depends(senate_actor)):
    from app.senate_sessions import _open_session, _cast_choice
    candidate = body.get('candidate_id')
    with transaction() as c:
        election = _election(c, election_id)
        if not election['session_id']:
            fail(409, 'NOT_PUBLISHED', 'Вопрос ещё не включён в опубликованную повестку.')
        _open_session(c, election['session_id'])
        voter = maybe(c, 'SELECT * FROM senate_session_electorate WHERE session_id=? AND user_id=? AND is_lieutenant=0', (election['session_id'], current['id']))
        if not voter:
            fail(403, 'NOT_IN_ELECTORATE', 'Голосуют сенаторы из опубликованного состава.')
        if not isinstance(candidate, str) or candidate not in {r['id'] for r in loads(election['candidates_json'], [])} | {'abstain', 'absent'}:
            fail(422, 'INVALID_CANDIDATE', 'Выберите кандидата, воздержание или отсутствие.')
        item = one(c, 'SELECT * FROM senate_session_items WHERE vote_id=?', (election['vote_id'],))
        _cast_choice(c, election['session_id'], item, candidate, current)
        return {'candidate_id': candidate, 'saved_at': ts()}


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
        if election['status'] in {'draft', 'ready_plenary'}:
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
            # Candidate corrections affect this item; the rest of the agenda retains its results.
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
        # The shared session was already closed; only this election result changes.
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
