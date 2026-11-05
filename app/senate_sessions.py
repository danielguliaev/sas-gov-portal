"""Published agendas, attendance and voting. The existing vote tables own votes."""
from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from app.core import (allrows, clean, dumps, fail, loads, maybe, one,
                      plain, read_db, transaction, ts, uid)
from app.senate import (has_flag, known_users, optional_senate_actor, require_flag,
                        senate_actor, setting, senate_audit)

def audit(c, current, action, object_type, object_id, before=None, after=None):
    senate_audit(c, current, action.removeprefix("senate."), object_type, object_id, before, after)

router = APIRouter(prefix="/senate", tags=["senate-sessions"])
RULES = {"predominantly_for", "simple_majority", "absolute_majority", "two_thirds"}
CHOICES = {"yes", "no", "abstain", "absent"}
SYSTEM = {"id": "system:senate", "roles": set(), "audit_source": "senate.deadline"}


def _date(value, field):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        fail(422, "INVALID_DATE", "Укажите дату с часовым поясом: " + field)
    return result.astimezone(timezone.utc)


def _session(c, session_id):
    return one(c, """SELECT s.id,s.title,d.* FROM legislative_sessions s
        JOIN senate_session_details d ON d.session_id=s.id WHERE s.id=?""", (session_id,))


def _manage(c, current):
    require_flag(c, current, "pro_tempore", "superadmin")


def _event(c, bill_id, event, body, current):
    c.execute("INSERT INTO bill_events VALUES(?,?,?,?,?,?)",
              (uid(), bill_id, event, dumps(body), current["id"], ts()))


def _current_items(c, session_id):
    items = allrows(c, """SELECT i.*,b.title,v.result,m.metadata_json AS revision_metadata FROM senate_session_items i
        JOIN bills b ON b.id=i.bill_id JOIN votes v ON v.id=i.vote_id
        LEFT JOIN bill_revisions r ON r.bill_id=i.bill_id AND r.version=i.version
        LEFT JOIN senate_revision_meta m ON m.revision_id=r.id
        WHERE i.session_id=? ORDER BY i.position""", (session_id,))
    for item in items:
        snapshot = loads(item.pop('revision_metadata'), {})
        item['title'] = snapshot.get('title', item['title'])
    return items


def _choices(c, vote_id):
    return allrows(c, """SELECT v.voter_id,v.choice,e.is_lieutenant FROM vote_choices v
        JOIN votes vote ON vote.id=v.vote_id
        JOIN senate_session_electorate e ON e.session_id=vote.session_id AND e.user_id=v.voter_id
        WHERE v.vote_id=? ORDER BY v.voter_id""", (vote_id,))


def tally_votes(choices, rule, threshold):
    """Pure tally, with the Lieutenant-Governor added only for a senator tie."""
    counts = Counter(row["choice"] for row in choices if not row["is_lieutenant"])
    yes, no = counts["yes"], counts["no"]
    deciding = None
    if yes == no:
        lieutenant = next((row for row in choices if row["is_lieutenant"]), None)
        if lieutenant and lieutenant["choice"] in ("yes", "no"):
            deciding = lieutenant["choice"]
            yes += deciding == "yes"
            no += deciding == "no"
    if rule == "predominantly_for":
        adopted = yes > no
    elif rule == "simple_majority":
        adopted = yes > no + counts["abstain"]
    else:
        adopted = yes >= threshold
    return {"yes": yes, "no": no, "abstain": counts["abstain"],
            "absent": counts["absent"], "deciding_vote": deciding}, adopted


def _attendance(c, session_id):
    participants = allrows(c, """SELECT e.user_id,e.is_lieutenant,
        MAX(CASE WHEN vc.choice IN ('yes','no','abstain') THEN 1 ELSE 0 END) AS present,
        MAX(CASE WHEN vc.choice='absent' THEN 1 ELSE 0 END) AS absent
        FROM senate_session_electorate e
        LEFT JOIN votes v ON v.session_id=e.session_id
        LEFT JOIN vote_choices vc ON vc.vote_id=v.id AND vc.voter_id=e.user_id
        WHERE e.session_id=? GROUP BY e.user_id,e.is_lieutenant ORDER BY e.user_id""", (session_id,))
    present = sum(bool(row["present"]) and not row["absent"] for row in participants
                  if not row["is_lieutenant"])
    return {"present": present, "absent": sum(bool(row["absent"]) for row in participants
                                                if not row["is_lieutenant"]),
            "participants": participants}


def _record_adopted_act(c, item, current):
    if maybe(c, "SELECT id FROM adopted_acts WHERE bill_id=?", (item["bill_id"],)):
        return
    bill = one(c, "SELECT * FROM bills WHERE id=?", (item["bill_id"],))
    meta = one(c, "SELECT * FROM senate_bill_meta WHERE bill_id=?", (item["bill_id"],))
    text = one(c, "SELECT text_rich FROM bill_revisions WHERE bill_id=? AND version=?", (item["bill_id"], item["version"]))["text_rich"]
    publication_id, act_id = uid(), uid()
    c.execute("""INSERT INTO portal_publications
        (id,portal_id,jurisdiction_id,title,document_type,issuer,text_rich,status,issued_at,published_at,external_reference)
        VALUES(?,'legislature','state',?,?,?,?,'published',?,?,?)""",
        (publication_id,bill["title"],meta["document_type"],"Сенат штата Сан-Андреас",text,ts(),ts(),meta["number"]))
    c.execute("INSERT INTO adopted_acts VALUES(?,?,?)", (act_id,item["bill_id"],publication_id))
    senate_audit(c, current, "act.adopt","adopted_act",act_id,{}, {"bill_id":item["bill_id"],"version":item["version"],"publication_id":publication_id})

def _finalize(c, session_id, current):
    session = _session(c, session_id)
    if maybe(c, "SELECT id FROM senate_elections WHERE session_id=?", (session_id,)):
        from app.senate_officers import finalize_election
        return finalize_election(c, session_id, current)
    if session["status"] != "published":
        return session
    if _date(session["ends_at"], "ends_at") > datetime.now(timezone.utc):
        fail(409, "SESSION_NOT_ENDED", "Заседание ещё не завершилось.")
    present = _attendance(c, session_id)["present"]
    quorum_met = present >= session["quorum"]
    for item in _current_items(c, session_id):
        tally, adopted = tally_votes(_choices(c, item["vote_id"]), item["rule"], item["threshold"])
        result = ("adopted" if adopted else "rejected") if quorum_met else "no_quorum"
        status = ("adopted" if adopted else "rejected") if quorum_met else "ready_plenary"
        c.execute("UPDATE votes SET result=? WHERE id=?", (result, item["vote_id"]))
        c.execute("UPDATE bills SET status=? WHERE id=?", (status, item["bill_id"]))
        if status == "adopted":
            _record_adopted_act(c, item, current)
            from app.senate_officers import apply_approved_proposal
            apply_approved_proposal(c, item["bill_id"], current)
        _event(c, item["bill_id"], "senate.vote.closed", {"session_id": session_id,
               "result": result, "version": item["version"], "tally": tally,
               "quorum": session["quorum"], "present": present}, current)
    status = "closed" if quorum_met else "failed"
    c.execute("UPDATE senate_session_details SET status=? WHERE session_id=?", (status, session_id))
    senate_audit(c, current, "session.close", "legislative_session", session_id,
          {"status": "published"}, {"status": status, "present": present, "quorum": session["quorum"]})
    return _session(c, session_id)


def finalize_due_sessions(c):
    """Called from reads and periodic maintenance, safe to repeat after restarts."""
    due = allrows(c, "SELECT session_id FROM senate_session_details WHERE status='published' AND ends_at<=?", (ts(),))
    for row in due:
        _finalize(c, row["session_id"], SYSTEM)


def _detail(c, session_id, current=None):
    election = maybe(c, "SELECT id FROM senate_elections WHERE session_id=?", (session_id,))
    if election:
        from app.senate_officers import election_detail_in_db
        return election_detail_in_db(c, election["id"], current)
    session = _session(c, session_id)
    if session["status"] == "draft":
        if not current or current["id"] == "guest":
            fail(404, "NOT_FOUND", "Заседание не найдено.")
        _manage(c, current)
    result = {key: session[key] for key in ("id", "title", "starts_at", "ends_at", "status",
                                            "quorum", "published_at", "transcript", "creator_id")}
    result["items"] = _current_items(c, session_id)
    for item in result["items"]:
        item["choices"] = _choices(c, item["vote_id"])
        item["tally"], _ = tally_votes(item["choices"], item["rule"], item["threshold"])
        revision = one(c, "SELECT text_rich FROM bill_revisions WHERE bill_id=? AND version=?",
                       (item["bill_id"], item["version"]))
        item["text_rich"] = revision["text_rich"]
        item["category"] = loads(item.pop("category_json"), {})
    result["attendance"] = {**_attendance(c, session_id), "required": session["quorum"]}
    result["transcript_versions"] = allrows(c,
        "SELECT id,author_id,created_at FROM senate_session_transcripts WHERE session_id=? ORDER BY created_at",
        (session_id,))
    return result


@router.get("/sessions")
def sessions(current=Depends(optional_senate_actor)):
    with transaction() as c:
        finalize_due_sessions(c)
        can_manage = current and (has_flag(c, current, "pro_tempore") or has_flag(c, current, "superadmin"))
        sql = """SELECT s.id,s.title,d.starts_at,d.ends_at,d.status,d.quorum,d.published_at
            FROM legislative_sessions s JOIN senate_session_details d ON s.id=d.session_id"""
        if not can_manage:
            sql += " WHERE d.status!='draft'"
        return allrows(c, sql + " ORDER BY d.starts_at DESC")


@router.post("/sessions")
def create_session(body: dict, current=Depends(senate_actor)):
    title = plain(body.get("title"), 250)
    if not title:
        fail(422, "TITLE_REQUIRED", "Укажите название заседания.")
    starts = _date(body.get("starts_at"), "starts_at")
    ends = _date(body.get("ends_at"), "ends_at")
    if ends <= starts or ends <= datetime.now(timezone.utc):
        fail(422, "INVALID_SESSION_DATES", "Окончание должно быть позже начала и текущего времени.")
    bill_ids = body.get("bill_ids")
    if not isinstance(bill_ids, list) or not bill_ids or not all(isinstance(x, str) for x in bill_ids):
        fail(422, "BILLS_REQUIRED", "Выберите предметы повестки.")
    if len(set(bill_ids)) != len(bill_ids) or len(bill_ids) > 100:
        fail(422, "DUPLICATE_BILLS", "Повестка содержит повторения или более 100 предметов.")
    with transaction() as c:
        _manage(c, current)
        configured_quorum = setting(c, "quorum", 1)
        quorum = body.get("quorum", configured_quorum)
        if quorum != configured_quorum and not has_flag(c, current, "superadmin"):
            fail(403, "QUORUM_ADMIN_ONLY", "Кворум задаёт суперадминистратор.")
        if type(quorum) is not int or not 1 <= quorum <= 10000:
            fail(422, "INVALID_QUORUM", "Кворум должен быть положительным целым числом.")
        session_id = uid()
        c.execute("INSERT INTO legislative_sessions VALUES(?,?,?)", (session_id, title, starts.isoformat()))
        c.execute("""INSERT INTO senate_session_details(session_id,starts_at,ends_at,quorum,creator_id)
            VALUES(?,?,?,?,?)""", (session_id, starts.isoformat(), ends.isoformat(), quorum, current["id"]))
        for position, bill_id in enumerate(bill_ids):
            bill = one(c, "SELECT * FROM bills WHERE id=?", (bill_id,))
            if bill["status"] != "ready_plenary":
                fail(409, "BILL_NOT_READY", "В повестку можно включить только готовый к пленарному рассмотрению предмет.")
            if maybe(c, """SELECT i.bill_id FROM senate_session_items i JOIN senate_session_details d
                ON d.session_id=i.session_id WHERE i.bill_id=? AND d.status IN ('draft','published')""", (bill_id,)):
                fail(409, "BILL_ALREADY_SCHEDULED", "Предмет уже включён в действующую повестку.")
            meta = one(c, "SELECT * FROM senate_bill_meta WHERE bill_id=?", (bill_id,))
            category = one(c, "SELECT * FROM senate_categories WHERE id=?", (meta["category_id"],))
            vote_id = uid()
            c.execute("INSERT INTO votes VALUES(?,?,?,?,?)", (vote_id, bill_id, session_id, "pending", ts()))
            c.execute("""INSERT INTO senate_session_items
                (session_id,bill_id,vote_id,position,version,rule,threshold,category_json)
                VALUES(?,?,?,?,?,?,?,?)""", (session_id, bill_id, vote_id, position, meta["version"],
                category["plenary_rule"], category["plenary_threshold"], dumps(category)))
        senate_audit(c, current, "session.create", "legislative_session", session_id, {},
              {"bill_ids": bill_ids, "starts_at": starts.isoformat(), "ends_at": ends.isoformat(), "quorum": quorum})
        return _detail(c, session_id, current)


@router.post("/sessions/{session_id}/publish")
def publish_session(session_id: str, current=Depends(senate_actor)):
    with transaction() as c:
        _manage(c, current)
        session = _session(c, session_id)
        if maybe(c, "SELECT id FROM senate_elections WHERE session_id=?", (session_id,)):
            fail(409, "ELECTION_ENDPOINT", "Для выборов используйте отдельную процедуру публикации.")
        if session["status"] != "draft":
            fail(409, "AGENDA_FROZEN", "Повестка уже опубликована и не может быть изменена.")
        now = datetime.now(timezone.utc)
        starts, ends = _date(session["starts_at"], "starts_at"), _date(session["ends_at"], "ends_at")
        notice = setting(c, "min_notice_hours", 0)
        if starts < now + timedelta(hours=float(notice)) or ends <= now:
            fail(422, "MIN_NOTICE", "Не соблюдён минимальный срок публикации повестки до заседания.")
        users = known_users(c)
        senators = [user for user in users if has_flag(c, {"id": user["id"], "roles": set(user.get("roles", []))}, "senator")]
        lieutenants = [user for user in users if has_flag(c, {"id": user["id"], "roles": set(user.get("roles", []))}, "lieutenant_governor")]
        if len(lieutenants) > 1:
            fail(409, "MULTIPLE_LIEUTENANTS", "Назначено несколько Лейтенант-Губернаторов. Исправьте состав флагов.")
        if any(user["id"] in {s["id"] for s in senators} for user in lieutenants):
            fail(409, "INCOMPATIBLE_FLAGS", "Лейтенант-Губернатор не может одновременно входить в сенаторский кворум.")
        for user in senators + lieutenants:
            c.execute("INSERT INTO senate_session_electorate VALUES(?,?,?)", (session_id, user["id"], int(user in lieutenants)))
        for item in _current_items(c, session_id):
            bill = one(c, "SELECT * FROM bills WHERE id=?", (item["bill_id"],))
            if bill["status"] != "ready_plenary":
                fail(409, "BILL_NOT_READY", "Один из предметов больше не готов к рассмотрению.")
            meta = one(c, "SELECT * FROM senate_bill_meta WHERE bill_id=?", (item["bill_id"],))
            category = one(c, "SELECT * FROM senate_categories WHERE id=?", (meta["category_id"],))
            rule, threshold = category["plenary_rule"], category["plenary_threshold"]
            if rule not in RULES or (rule in {"absolute_majority", "two_thirds"} and
                                     (type(threshold) is not int or threshold < 1)):
                fail(422, "INVALID_VOTING_RULE", "Для категории не настроено корректное правило принятия.")
            c.execute("""UPDATE senate_session_items SET version=?,rule=?,threshold=?,category_json=?
                WHERE session_id=? AND bill_id=?""", (meta["version"], rule, threshold, dumps(category), session_id, item["bill_id"]))
            c.execute("UPDATE bills SET status='agenda_published' WHERE id=?", (item["bill_id"],))
            _event(c, item["bill_id"], "senate.agenda.published", {"session_id": session_id, "version": meta["version"]}, current)
        snapshot = {"min_notice_hours": notice, "electorate_policy": "snapshot_on_publish",
                    "published_electorate": [user["id"] for user in senators]}
        c.execute("""UPDATE senate_session_details SET status='published',published_at=?,settings_json=?
            WHERE session_id=?""", (ts(), dumps(snapshot), session_id))
        senate_audit(c, current, "session.publish", "legislative_session", session_id,
              {"status": "draft"}, {"status": "published", "settings": snapshot})
        return _detail(c, session_id, current)


@router.get("/sessions/{session_id}")
def session_detail(session_id: str, current=Depends(optional_senate_actor)):
    with transaction() as c:
        finalize_due_sessions(c)
        return _detail(c, session_id, current)


def _open_session(c, session_id):
    session = _session(c, session_id)
    if session["status"] != "published" or _date(session["ends_at"], "ends_at") <= datetime.now(timezone.utc):
        fail(409, "VOTING_CLOSED", "Голосование закрыто.")
    return session


@router.post("/sessions/{session_id}/votes")
def cast_vote(session_id: str, body: dict, current=Depends(senate_actor)):
    choice, bill_id = body.get("choice"), body.get("bill_id")
    if not isinstance(choice, str) or choice not in CHOICES:
        fail(422, "INVALID_VOTE", "Выберите за, против, воздержался или отсутствовал.")
    if not isinstance(bill_id, str):
        fail(422, "INVALID_BILL", "Укажите идентификатор предмета.")
    with transaction() as c:
        _open_session(c, session_id)
        voter = maybe(c, "SELECT * FROM senate_session_electorate WHERE session_id=? AND user_id=?", (session_id, current["id"]))
        if not voter:
            fail(403, "NOT_IN_ELECTORATE", "Вы не входите в состав голосующих этого заседания.")
        item = one(c, "SELECT * FROM senate_session_items WHERE session_id=? AND bill_id=?", (session_id, bill_id))
        absent = maybe(c, """SELECT 1 FROM vote_choices vc JOIN votes v ON v.id=vc.vote_id
            WHERE v.session_id=? AND vc.voter_id=? AND vc.choice='absent'""", (session_id, current["id"]))
        items = _current_items(c, session_id) if choice == "absent" else [item]
        before = allrows(c, """SELECT vc.* FROM vote_choices vc JOIN votes v ON v.id=vc.vote_id
            WHERE v.session_id=? AND vc.voter_id=?""", (session_id, current["id"]))
        if absent and choice != "absent":
            c.execute("""DELETE FROM vote_choices WHERE voter_id=? AND choice='absent' AND vote_id IN
                (SELECT id FROM votes WHERE session_id=?)""", (current["id"], session_id))
        for target in items:
            c.execute("""INSERT INTO vote_choices(vote_id,voter_id,choice) VALUES(?,?,?)
                ON CONFLICT(vote_id,voter_id) DO UPDATE SET choice=excluded.choice""", (target["vote_id"], current["id"], choice))
        senate_audit(c, current, "vote.cast", "legislative_session", session_id,
              {"choices": before}, {"bill_id": bill_id, "choice": choice})
        return {"choice": choice, "bill_id": bill_id, "session_id": session_id, "saved_at": ts(),
                "cascaded": choice == "absent"}


@router.post("/sessions/{session_id}/restore-presence")
def restore_presence(session_id: str, current=Depends(senate_actor)):
    with transaction() as c:
        _open_session(c, session_id)
        one(c, "SELECT * FROM senate_session_electorate WHERE session_id=? AND user_id=?", (session_id, current["id"]))
        previous = allrows(c, """SELECT vc.* FROM vote_choices vc JOIN votes v ON v.id=vc.vote_id
            WHERE v.session_id=? AND vc.voter_id=?""", (session_id, current["id"]))
        if not previous or any(row["choice"] != "absent" for row in previous):
            fail(409, "NOT_ABSENT", "Вы не отмечены отсутствующим на всём заседании.")
        c.execute("""DELETE FROM vote_choices WHERE voter_id=? AND vote_id IN
            (SELECT id FROM votes WHERE session_id=?)""", (current["id"], session_id))
        senate_audit(c, current, "vote.restore_presence", "legislative_session", session_id,
              {"choices": previous}, {"choices": [], "note": "Нужно проголосовать заново."})
        return {"restored": True, "message": "Присутствие восстановлено. Проголосуйте заново по каждому предмету."}


@router.post("/sessions/{session_id}/close")
def close_session(session_id: str, current=Depends(senate_actor)):
    with transaction() as c:
        _manage(c, current)
        session = _session(c, session_id)
        if session["status"] == "draft":
            fail(409, "NOT_PUBLISHED", "Повестка ещё не опубликована.")
        _finalize(c, session_id, current)
        return _detail(c, session_id, current)


@router.put("/sessions/{session_id}/transcript")
def save_transcript(session_id: str, body: dict, current=Depends(senate_actor)):
    text = plain(body.get("text"), 1000000)
    if not text:
        fail(422, "TRANSCRIPT_REQUIRED", "Введите текст стенограммы.")
    with transaction() as c:
        _manage(c, current)
        session = _session(c, session_id)
        if session["status"] == "draft" or _date(session["ends_at"], "ends_at") > datetime.now(timezone.utc):
            fail(409, "SESSION_NOT_ENDED", "Стенограмма загружается после завершения заседания.")
        _finalize(c, session_id, current)
        transcript_id = uid()
        c.execute("INSERT INTO senate_session_transcripts VALUES(?,?,?,?,?)", (transcript_id, session_id, text, current["id"], ts()))
        c.execute("UPDATE senate_session_details SET transcript=? WHERE session_id=?", (text, session_id))
        senate_audit(c, current, "session.transcript", "legislative_session", session_id,
              {"text": session["transcript"]}, {"text": text, "transcript_id": transcript_id})
        return {"id": transcript_id, "saved_at": ts()}


@router.get("/sessions/{session_id}/export.pdf")
def export_session_pdf(session_id: str, current=Depends(optional_senate_actor), time_zone: str = 'UTC'):
    from app.senate_pdf import pdf_response, display, display_time, export_timezone
    zone = export_timezone(time_zone)
    with transaction() as c:
        finalize_due_sessions(c)
        session = _detail(c, session_id, current)
        if session.get("kind") == "election":
            names = {candidate["id"]: candidate["name"] for candidate in session["candidates"]}
            sections = [("Выборы Pro Tempore", "Статус: " + display(session["status"]) +
                "\nНачало: " + display_time(session["starts_at"], zone) + "\nОкончание: " + display_time(session["ends_at"], zone) +
                "\nКворум: " + str(session["quorum"]) + "\nПравило: " + display(session["rule"]) +
                "\nПобедитель: " + names.get(session["winner_id"], session["winner_id"] or "Не определён"))]
            sections.append(("Кандидаты и итоги", "\n".join(
                names[row["candidate_id"]] + " (" + row["candidate_id"] + "): " + str(row["count"])
                for row in session["tally"])))
            sections.append(("Поимённые голоса", "\n".join(
                row["voter_id"] + ": " + names.get(row["candidate_id"], row["candidate_id"])
                for row in session["choices"]) or "Голоса не поданы."))
            return pdf_response(session["title"], sections, "senate-election-" + session_id + ".pdf")
        sections = [("Сведения о заседании", "Начало: " + display_time(session["starts_at"], zone) + "\nОкончание: " + display_time(session["ends_at"], zone) +
                     "\nСтатус: " + display(session["status"]) + "\nКворум: " + str(session["quorum"]) + "\nЧасовой пояс: " + time_zone)]
        for item in session["items"]:
            votes = "\n".join(row["voter_id"] + ": " + display(row["choice"]) for row in item["choices"])
            sections.append((item["title"], "Редакция: " + str(item["version"]) + "\nПравило: " + display(item["rule"]) +
                "\nРезультат: " + display(item["result"]) + "\n" + votes))
        sections.append(("Стенограмма", session["transcript"] or "Не загружена."))
        return pdf_response(session["title"], sections, "senate-session-" + session_id + ".pdf")





def _guard_newer_agenda(c, session_id):
    newer = maybe(c, """SELECT newer.session_id,newer.bill_id FROM senate_session_items original
        JOIN votes original_vote ON original_vote.id=original.vote_id
        JOIN senate_session_items newer ON newer.bill_id=original.bill_id AND newer.session_id!=original.session_id
        JOIN votes newer_vote ON newer_vote.id=newer.vote_id
        JOIN senate_session_details d ON d.session_id=newer.session_id
        WHERE original.session_id=? AND newer_vote.created_at>original_vote.created_at
        AND d.status IN ('draft','published','needs_review') LIMIT 1""", (session_id,))
    if newer:
        fail(409, "NEWER_SESSION_ACTIVE", "Предмет включён в более новую действующую повестку. Сначала завершите её проверку.", newer)


@router.post("/sessions/{session_id}/admin-correction")
def admin_correction(session_id: str, body: dict, current=Depends(senate_actor)):
    reason = plain(body.get("reason", ""), 4000)
    voter_id, bill_id, choice = body.get("voter_id"), body.get("bill_id"), body.get("choice")
    if not reason:
        fail(422, "REASON_REQUIRED", "Укажите основание административного исправления.")
    if not isinstance(voter_id, str) or not isinstance(bill_id, str) or not isinstance(choice, str) or choice not in CHOICES:
        fail(422, "INVALID_CORRECTION", "Укажите участника, предмет и допустимый голос.")
    with transaction() as c:
        if not has_flag(c, current, "superadmin"):
            fail(403, "FORBIDDEN", "Исправление доступно суперадминистратору.")
        session = _session(c, session_id)
        if maybe(c, "SELECT id FROM senate_elections WHERE session_id=?", (session_id,)):
            fail(409, "ELECTION_CORRECTION", "Голоса за кандидатов не исправляются как голоса по законопроектам.")
        if session["status"] == "draft":
            fail(409, "NOT_PUBLISHED", "Повестка ещё не опубликована.")
        _guard_newer_agenda(c, session_id)
        one(c, "SELECT user_id FROM senate_session_electorate WHERE session_id=? AND user_id=?", (session_id, voter_id))
        item = one(c, "SELECT * FROM senate_session_items WHERE session_id=? AND bill_id=?", (session_id, bill_id))
        before = _detail(c, session_id, current)
        prior_choices = allrows(c, """SELECT vc.* FROM vote_choices vc JOIN votes v ON v.id=vc.vote_id
            WHERE v.session_id=? AND vc.voter_id=?""", (session_id, voter_id))
        if choice != "absent" and any(row["choice"] == "absent" for row in prior_choices):
            c.execute("""DELETE FROM vote_choices WHERE voter_id=? AND choice='absent' AND vote_id IN
                (SELECT id FROM votes WHERE session_id=?)""", (voter_id, session_id))
        items = _current_items(c, session_id) if choice == "absent" else [item]
        for target in items:
            c.execute("""INSERT INTO vote_choices(vote_id,voter_id,choice) VALUES(?,?,?)
                ON CONFLICT(vote_id,voter_id) DO UPDATE SET choice=excluded.choice""", (target["vote_id"], voter_id, choice))
        ended = _date(session["ends_at"], "ends_at") <= datetime.now(timezone.utc)
        if ended or session["status"] in {"closed", "failed", "needs_review"}:
            c.execute("UPDATE senate_session_details SET status='needs_review' WHERE session_id=?", (session_id,))
            for agenda_item in _current_items(c, session_id):
                c.execute("UPDATE votes SET result='needs_review' WHERE id=?", (agenda_item["vote_id"],))
                c.execute("UPDATE bills SET status='review_required' WHERE id=?", (agenda_item["bill_id"],))
                c.execute("""UPDATE portal_publications SET status='under_review' WHERE id IN
                    (SELECT publication_id FROM adopted_acts WHERE bill_id=?)""", (agenda_item["bill_id"],))
                _event(c, agenda_item["bill_id"], "senate.vote.correction_pending", {
                    "session_id": session_id, "corrected_voter_id": voter_id, "reason": reason}, current)
        senate_audit(c, current, "vote.admin_correction", "legislative_session", session_id,
              {"session": before, "choices": prior_choices}, {"bill_id": bill_id, "voter_id": voter_id,
              "choice": choice, "reason": reason, "needs_review": ended or session["status"] != "published"})
        return _detail(c, session_id, current)


@router.post("/sessions/{session_id}/admin-resolution")
def admin_resolution(session_id: str, body: dict, current=Depends(senate_actor)):
    reason = plain(body.get("reason", ""), 4000)
    results = body.get("results")
    if not reason:
        fail(422, "REASON_REQUIRED", "Укажите основание итогового решения.")
    if not isinstance(results, dict) or not all(isinstance(key, str) and isinstance(value, str)
                                               and value in {"adopted", "rejected", "no_quorum"}
                                               for key, value in results.items()):
        fail(422, "INVALID_RESULTS", "Укажите решение по каждому предмету.")
    with transaction() as c:
        if not has_flag(c, current, "superadmin"):
            fail(403, "FORBIDDEN", "Итоговое исправление доступно суперадминистратору.")
        session = _session(c, session_id)
        if session["status"] != "needs_review":
            fail(409, "NO_PENDING_CORRECTION", "Заседание не ожидает решения по исправлению.")
        _guard_newer_agenda(c, session_id)
        items = _current_items(c, session_id)
        if set(results) != {item["bill_id"] for item in items}:
            fail(422, "ALL_RESULTS_REQUIRED", "Нужно явно решить судьбу всех предметов повестки.")
        if "no_quorum" in results.values() and set(results.values()) != {"no_quorum"}:
            fail(422, "SESSION_QUORUM_CONSISTENCY", "Недостаток кворума относится ко всему заседанию.")
        before = _detail(c, session_id, current)
        for item in items:
            result = results[item["bill_id"]]
            proposal = maybe(c, "SELECT * FROM senate_committee_proposals WHERE bill_id=?", (item["bill_id"],))
            if proposal and proposal["applied_at"] and result != "adopted" and body.get("committee_actions_reviewed") is not True:
                fail(409, "COMMITTEE_ACTIONS_REVIEW_REQUIRED", "Состав комитета уже применён. Проверьте и исправьте флаги вручную, затем подтвердите проверку.")
            status = "ready_plenary" if result == "no_quorum" else result
            c.execute("UPDATE votes SET result=? WHERE id=?", (result, item["vote_id"]))
            c.execute("UPDATE bills SET status=? WHERE id=?", (status, item["bill_id"]))
            if result == "adopted":
                _record_adopted_act(c, item, current)
                from app.senate_officers import apply_approved_proposal
                apply_approved_proposal(c, item["bill_id"], current)
            c.execute("""UPDATE portal_publications SET status=? WHERE id IN
                (SELECT publication_id FROM adopted_acts WHERE bill_id=?)""",
                ("published" if result == "adopted" else "superseded", item["bill_id"]))
            _event(c, item["bill_id"], "senate.vote.admin_resolved", {"session_id": session_id,
                   "result": result, "reason": reason, "version": item["version"]}, current)
        status = "failed" if set(results.values()) == {"no_quorum"} else "closed"
        c.execute("UPDATE senate_session_details SET status=? WHERE session_id=?", (status, session_id))
        senate_audit(c, current, "vote.admin_resolution", "legislative_session", session_id,
              before, {"results": results, "reason": reason,
                       "committee_actions_reviewed": body.get("committee_actions_reviewed", False)})
        return _detail(c, session_id, current)
