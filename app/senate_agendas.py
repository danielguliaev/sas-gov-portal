"""Public agenda catalog, discussion-only subjects, venues and schedule changes."""
from fastapi import APIRouter, Depends
from app.core import allrows, one, maybe, plain, fail, uid, ts, dumps, loads, transaction, read_db
from app.senate import optional_senate_actor, senate_actor, has_flag, senate_audit, profile, event
from app.senate_presentation import person_name

router = APIRouter(prefix='/senate', tags=['senate-agendas'])


def discussion_values(body):
    rows = body.get('discussions', [])
    if not isinstance(rows, list) or len(rows) > 200:
        fail(422, 'INVALID_DISCUSSIONS', 'Укажите список свободных предметов.')
    result = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('title'), str) or not row['title'].strip():
            fail(422, 'DISCUSSION_TITLE_REQUIRED', 'Укажите название свободного предмета.')
        result.append({'title': plain(row['title'], 500), 'description': plain(row.get('description', ''), 100000)})
    return result


def store_discussions(c, kind, agenda_id, rows):
    table, key = ('senate_session_discussions', 'session_id') if kind == 'plenary' else ('senate_committee_discussions', 'agenda_id')
    c.execute(f'DELETE FROM {table} WHERE {key}=?', (agenda_id,))
    for position, row in enumerate(rows):
        c.execute(f'INSERT INTO {table} VALUES(?,?,?,?,?)', (uid(), agenda_id, position, row['title'], row['description']))


def committee_agenda_view(c, agenda_id):
    row = one(c, '''SELECT a.*,co.name AS committee_name FROM senate_committee_agendas a
        JOIN senate_committees co ON co.id=a.committee_id WHERE a.id=?''', (agenda_id,))
    row.update(kind='committee', chair_name=person_name(c, row['created_by']))
    row['items'] = allrows(c, '''SELECT i.position,i.snapshot_json,r.bill_id,r.version,r.decision,b.title FROM senate_committee_agenda_items i
        JOIN senate_reviews r ON r.id=i.review_id JOIN bills b ON b.id=r.bill_id WHERE i.agenda_id=? ORDER BY i.position''', (agenda_id,))
    for item in row['items']:
        snapshot = loads(item.pop('snapshot_json'), {})
        item['title'] = snapshot.get('title', item['title'])
    row['discussions'] = allrows(c, 'SELECT * FROM senate_committee_discussions WHERE agenda_id=? ORDER BY position', (agenda_id,))
    return row


def catalog(c, current):
    from app.senate_sessions import can_manage_sessions
    rows = allrows(c, '''SELECT s.id,s.title,d.starts_at,d.status,d.venue,d.schedule_status,d.schedule_reason,d.creator_id,
        (SELECT COUNT(*) FROM senate_session_items i WHERE i.session_id=s.id) AS voting_items
        FROM legislative_sessions s JOIN senate_session_details d ON d.session_id=s.id ORDER BY d.starts_at DESC''')
    result = [dict(r, kind='plenary', committee_id='', committee_name='Пленарное заседание Сената', chair_name=person_name(c, r['creator_id']))
              for r in rows if r['status'] != 'draft' or can_manage_sessions(c, current)]
    for r in allrows(c, '''SELECT a.*,co.name AS committee_name FROM senate_committee_agendas a
        JOIN senate_committees co ON co.id=a.committee_id'''):
        r.update(kind='committee', chair_name=person_name(c, r['created_by']), status='published' if r['schedule_status'] != 'cancelled' else 'cancelled')
        result.append(r)
    return sorted(result, key=lambda r: r.get('starts_at') or r.get('published_at') or '', reverse=True)


@router.get('/agendas')
def agendas(current=Depends(optional_senate_actor)):
    from app.senate_sessions import finalize_due_sessions
    with transaction() as c:
        finalize_due_sessions(c)
        return catalog(c, current)


@router.get('/agendas/{kind}/{agenda_id}')
def agenda_detail(kind: str, agenda_id: str, current=Depends(optional_senate_actor)):
    from app.senate_sessions import _detail, finalize_due_sessions
    with transaction() as c:
        finalize_due_sessions(c)
        if kind == 'plenary':
            row = _detail(c, agenda_id, current)
            row['kind'] = kind
            return row
        if kind != 'committee':
            fail(404, 'NOT_FOUND', 'Повестка не найдена.')
        row = committee_agenda_view(c, agenda_id)
        row['can_manage'] = committee_manager(c, current, row['committee_id']) and row['schedule_status'] != 'cancelled' and not any(i['decision'] for i in row['items'])
        return row


def committee_manager(c, current, committee_id):
    return has_flag(c, current, 'superadmin') or all(has_flag(c, current, f) for f in ('senator', 'committee:' + committee_id, 'chair:' + committee_id))


@router.post('/agendas/{kind}/{agenda_id}/schedule')
def change_schedule(kind: str, agenda_id: str, body: dict, current=Depends(senate_actor)):
    from app.senate_sessions import _manage, _date, _detail, _session, _current_items, finalize_due_sessions
    with transaction() as c:
        finalize_due_sessions(c)
        if kind == 'plenary':
            _manage(c, current)
            row = _session(c, agenda_id)
            if row['status'] not in {'draft', 'published'}:
                fail(409, 'AGENDA_FINAL', 'Завершённое или отменённое заседание нельзя переносить или отменять повторно.')
        elif kind == 'committee':
            row = committee_agenda_view(c, agenda_id)
            if not committee_manager(c, current, row['committee_id']):
                fail(403, 'CHAIR_REQUIRED', 'Изменить заседание может председатель комитета.')
            if row['schedule_status'] == 'cancelled':
                fail(409, 'AGENDA_FINAL', 'Заседание уже отменено.')
            if any(i['decision'] for i in allrows(c, '''SELECT r.decision FROM senate_reviews r JOIN senate_committee_agenda_items i
                ON i.review_id=r.id WHERE i.agenda_id=?''', (agenda_id,))):
                fail(409, 'AGENDA_FINAL', 'По этой повестке уже зафиксированы решения.')
        else:
            fail(404, 'NOT_FOUND', 'Повестка не найдена.')
        action = body.get('action')
        reason = plain(body.get('reason', ''), 10000)
        if action not in {'reschedule', 'cancel'} or not reason:
            fail(422, 'SCHEDULE_REASON_REQUIRED', 'Выберите перенос или отмену и укажите основание.')
        if action == 'reschedule':
            starts = _date(body.get('starts_at'), 'starts_at').isoformat()
            venue = plain(body.get('venue', row.get('venue', '')), 1000)
            if kind == 'plenary':
                ends = _date(body.get('ends_at'), 'ends_at')
                from datetime import datetime, timezone
                if ends <= datetime.now(timezone.utc):
                    fail(422, 'INVALID_DATE', 'Новое ООС время закрытия должно быть в будущем.')
                if row['status'] == 'published':
                    choices = allrows(c, 'SELECT vc.* FROM vote_choices vc JOIN votes v ON v.id=vc.vote_id WHERE v.session_id=?', (agenda_id,))
                    snapshot = {'choices': choices, 'starts_at': row['starts_at'], 'ends_at': row['ends_at'], 'venue': row['venue']}
                    c.execute('INSERT INTO senate_voting_rounds VALUES(?,?,?,?,?)', (agenda_id, row['voting_round'], ts(), reason, dumps(snapshot)))
                    c.execute('DELETE FROM vote_choices WHERE vote_id IN (SELECT id FROM votes WHERE session_id=?)', (agenda_id,))
                    c.execute("UPDATE votes SET result='pending' WHERE session_id=?", (agenda_id,))
                    c.execute('UPDATE senate_session_details SET voting_round=voting_round+1 WHERE session_id=?', (agenda_id,))
                c.execute("UPDATE senate_session_details SET starts_at=?,ends_at=?,venue=?,schedule_status='postponed',schedule_reason=? WHERE session_id=?", (starts, ends.isoformat(), venue, reason, agenda_id))
                c.execute('UPDATE legislative_sessions SET held_at=? WHERE id=?', (starts, agenda_id))
            else:
                c.execute("UPDATE senate_committee_agendas SET starts_at=?,venue=?,schedule_status='postponed',schedule_reason=? WHERE id=?", (starts, venue, reason, agenda_id))
        elif kind == 'plenary':
            c.execute("UPDATE senate_session_details SET status='cancelled',schedule_status='cancelled',schedule_reason=? WHERE session_id=?", (reason, agenda_id))
            c.execute("UPDATE votes SET result='cancelled' WHERE session_id=?", (agenda_id,))
            for item in _current_items(c, agenda_id):
                election = maybe(c, 'SELECT id FROM senate_elections WHERE bill_id=?', (item['bill_id'],))
                c.execute('UPDATE bills SET status=? WHERE id=?', ('cancelled' if election else 'ready_plenary', item['bill_id']))
        else:
            c.execute("UPDATE senate_committee_agendas SET schedule_status='cancelled',schedule_reason=? WHERE id=?", (reason, agenda_id))
            c.execute("UPDATE senate_reviews SET status='report_ready' WHERE status='agenda' AND id IN (SELECT review_id FROM senate_committee_agenda_items WHERE agenda_id=?)", (agenda_id,))
        if kind == 'plenary':
            for item in _current_items(c, agenda_id):
                event(c, item['bill_id'], 'senate.agenda.rescheduled' if action == 'reschedule' else 'senate.agenda.cancelled', {'session_id':agenda_id, 'reason':reason}, current)
        senate_audit(c, current, 'agenda.' + action, 'legislative_session' if kind == 'plenary' else 'committee_agenda', agenda_id,
                     {k: row.get(k) for k in ('starts_at','venue','status')}, body)
        return {'id': agenda_id, 'kind': kind, 'action': action}
