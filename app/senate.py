"""Senate documents, membership and committee workflow on the shared database."""
import json
import re
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from app import core
from app.core import actor, allrows, audit, clean, dumps, fail, loads, maybe, one, plain, read_db, transaction, ts, uid

router = APIRouter(prefix='/senate', tags=['senate'])
EDITABLE = {'draft', 'held', 'revision_requested'}
DECISIONS = {'do_pass', 'amend_do_pass', 'not_pass', 'held', 'transfer', 'amend_transfer', 'return_to_author'}
RULES = {'predominantly_for', 'simple_majority', 'absolute_majority', 'two_thirds'}
PROFILE_FIELDS = ('name', 'capitol_office', 'district_office', 'phone', 'email')


def senate_actor(request: Request, authorization: str | None = Header(None), x_demo_user: str | None = Header(None)):
    current = actor(request, authorization, x_demo_user)
    with read_db() as c:
        flags = allrows(c, 'SELECT flag,active FROM senate_user_flags WHERE user_id=?', (current['id'],))
    result = dict(current)
    result['roles'] = set(current['roles'])
    for f in flags:
        mapped = 'legislator' if f['flag'] == 'senator' else f['flag']
        if f['active']:
            result['roles'].add(mapped)
        else:
            result['roles'].discard(mapped)
    return result


def optional_senate_actor(request: Request, authorization: str | None = Header(None), x_demo_user: str | None = Header(None)):
    if authorization is None and x_demo_user is None:
        return {'id': 'guest', 'roles': set(), 'audit_source': request.method + ' ' + request.url.path}
    return senate_actor(request, authorization, x_demo_user)


def has_flag(c, current, flag):
    definition = maybe(c, 'SELECT active FROM senate_flag_definitions WHERE id=?', (flag,))
    if definition is not None and not definition['active']:
        return False
    assigned = maybe(c, 'SELECT active FROM senate_user_flags WHERE user_id=? AND flag=?', (current['id'], flag))
    if assigned is not None:
        return bool(assigned['active'])
    if ('legislator' if flag == 'senator' else flag) in current['roles']:
        return True
    custom = allrows(c, '''SELECT d.permissions_json FROM senate_user_flags u JOIN senate_flag_definitions d ON d.id=u.flag
                        WHERE u.user_id=? AND u.active=1 AND d.active=1''', (current['id'],))
    return any(flag in loads(row['permissions_json'], []) for row in custom)


def require_flag(c, current, *flags):
    if not has_flag(c, current, 'superadmin') and not any(has_flag(c, current, f) for f in flags):
        fail(403, 'FORBIDDEN', 'Недостаточно полномочий для этого действия.')


def setting(c, key, default=None):
    row = maybe(c, 'SELECT setting_value FROM senate_settings WHERE setting_key=?', (key,))
    if row is None:
        return default
    try:
        return json.loads(row['setting_value'])
    except (ValueError, TypeError):
        return row['setting_value']


def settings(c):
    return {r['setting_key']: setting(c, r['setting_key']) for r in allrows(c, 'SELECT setting_key FROM senate_settings')}


def known_users(c):
    users = {r['user_id']: dict(r, id=r['user_id'], roles=[]) for r in allrows(c, 'SELECT * FROM senate_profiles')}
    if core.AUTH_MODE == 'demo' and core.APP_ENV != 'production':
        for role, roles in core.DEMO_ROLES.items():
            user_id = 'demo-' + role
            users.setdefault(user_id, {'id': user_id, 'user_id': user_id, 'name': '', 'roles': []})
            users[user_id]['roles'] = list(roles)
    for row in allrows(c, 'SELECT DISTINCT user_id FROM senate_user_flags'):
        users.setdefault(row['user_id'], {'id': row['user_id'], 'user_id': row['user_id'], 'name': '', 'roles': []})
    return list(users.values())


def senate_audit(c, current, action, object_type, object_id, before=None, after=None):
    c.execute('INSERT INTO senate_audit VALUES(?,?,?,?,?,?,?,?)',
              (uid(), current['id'], 'senate.' + action, object_type, object_id, dumps(before or {}), dumps(after or {}), ts()))
    audit(c, current, 'senate.' + action, object_type, object_id, before or {}, after or {})


def event(c, bill_id, kind, body, current):
    c.execute('INSERT INTO bill_events VALUES(?,?,?,?,?,?)',
              (uid(), bill_id, kind, body if isinstance(body, str) else dumps(body), current['id'], ts()))


def flag_list(c, current):
    explicit = {r['flag']: bool(r['active']) for r in allrows(c, 'SELECT flag,active FROM senate_user_flags WHERE user_id=?', (current['id'],))}
    inferred = {'senator' if r == 'legislator' else r for r in current['roles']}
    custom = allrows(c, '''SELECT d.permissions_json FROM senate_user_flags u JOIN senate_flag_definitions d ON d.id=u.flag
                        WHERE u.user_id=? AND u.active=1 AND d.active=1''', (current['id'],))
    for row in custom:
        inferred.update(loads(row['permissions_json'], []))
    combined = (inferred | {f for f, a in explicit.items() if a}) - {f for f, a in explicit.items() if not a}
    return sorted(f for f in combined if has_flag(c, current, f))


def profile(c, user_id):
    return maybe(c, 'SELECT * FROM senate_profiles WHERE user_id=?', (user_id,)) or dict(user_id=user_id, **{f: '' for f in PROFILE_FIELDS})


def require_admin(c, current):
    if not has_flag(c, current, 'superadmin'):
        fail(403, 'FORBIDDEN', 'Требуются полномочия суперадминистратора.')


def bill_row(c, bill_id):
    row = one(c, '''SELECT b.*,m.number,m.document_type,m.category_id,m.effective_at,m.version,
                 m.changes_json,m.submitted_at,m.current_committee_id FROM bills b
                 JOIN senate_bill_meta m ON m.bill_id=b.id WHERE b.id=?''', (bill_id,))
    row['changes'] = loads(row.pop('changes_json'), [])
    return row


def can_read(c, bill, current):
    return (bill['status'] != 'draft' or bill['author_id'] == current['id'] or has_flag(c, current, 'superadmin') or
            maybe(c, "SELECT 1 FROM senate_coauthors WHERE bill_id=? AND user_id=? AND status='accepted'", (bill['id'], current['id'])) is not None)


def readable_bill(c, bill_id, current):
    bill = bill_row(c, bill_id)
    if not can_read(c, bill, current):
        fail(404, 'NOT_FOUND', 'Предмет не найден.')
    return bill


def require_editor(c, bill, current):
    if not (bill['author_id'] == current['id'] or has_flag(c, current, 'superadmin') or maybe(c,
            "SELECT 1 FROM senate_coauthors WHERE bill_id=? AND user_id=? AND status='accepted'", (bill['id'], current['id']))):
        fail(403, 'FORBIDDEN', 'Редактирование доступно автору и подтверждённым соавторам.')
    if bill['status'] not in EDITABLE:
        fail(409, 'BILL_FROZEN', 'Эта редакция уже находится на рассмотрении.')


def list_bills(c, current, mine=False, q='', status='', category='', type_='', committee='', author='', coauthor='', date_from='', date_to=''):
    rows = allrows(c, '''SELECT b.*,m.number,m.document_type,m.category_id,m.effective_at,m.version,
                     m.changes_json,m.submitted_at,m.current_committee_id FROM bills b
                     JOIN senate_bill_meta m ON m.bill_id=b.id ORDER BY b.created_at DESC,b.id''')
    result = []
    for row in rows:
        if not can_read(c, row, current):
            continue
        if mine and row['author_id'] != current['id'] and not maybe(c,
            "SELECT 1 FROM senate_coauthors WHERE bill_id=? AND user_id=? AND status='accepted'", (row['id'], current['id'])):
            continue
        if q.casefold() not in (row['title'] + ' ' + row['number'] + ' ' + row['author_id']).casefold():
            continue
        if ((status and row['status'] != status) or (category and row['category_id'] != category) or
            (type_ and row['document_type'] != type_) or (committee and row['current_committee_id'] != committee)):
            continue
        if author and author.casefold() not in (row['author_id'] + ' ' + profile(c, row['author_id'])['name']).casefold():
            continue
        if coauthor:
            coauthors = allrows(c, "SELECT user_id FROM senate_coauthors WHERE bill_id=? AND status='accepted'", (row['id'],))
            if not any(coauthor.casefold() in (co['user_id'] + ' ' + profile(c, co['user_id'])['name']).casefold() for co in coauthors):
                continue
        record_date = (row['submitted_at'] or row['created_at'])[:10]
        if (date_from and record_date < date_from) or (date_to and record_date > date_to):
            continue
        row['changes'] = loads(row.pop('changes_json'), [])
        result.append(row)
    return result


def process_deadlines(c):
    due = allrows(c, "SELECT * FROM senate_reviews WHERE (status='awaiting_report' AND due_at<=?) OR (status IN ('report_ready','agenda') AND consideration_due_at<=?)", (ts(), ts()))
    system = {'id': 'system', 'roles': set(), 'audit_source': 'senate.deadline'}
    for review in due:
        bill = bill_row(c, review['bill_id'])
        if bill['current_committee_id'] != review['committee_id'] or bill['status'] != 'committee_review':
            continue
        c.execute("UPDATE senate_reviews SET status='expired' WHERE id=?", (review['id'],))
        c.execute("UPDATE bills SET status='ready_plenary' WHERE id=?", (bill['id'],))
        c.execute('UPDATE senate_bill_meta SET current_committee_id=NULL WHERE bill_id=?', (bill['id'],))
        event(c, bill['id'], 'committee_deadline', 'Срок комитета истёк. Предмет готов к пленарному рассмотрению.', system)
        senate_audit(c, system, 'committee.deadline', 'bill', bill['id'], {'status': bill['status']}, {'status': 'ready_plenary'})


def assign_review(c, bill, committee_id):
    one(c, 'SELECT id FROM senate_committees WHERE id=? AND active=1', (committee_id,))
    cycle = c.execute('SELECT COALESCE(MAX(cycle),0)+1 FROM senate_reviews WHERE bill_id=? AND committee_id=?', (bill['id'], committee_id)).fetchone()[0]
    now = ts()
    due = (datetime.now(timezone.utc) + timedelta(days=int(setting(c, 'committee_review_days', 10)))).isoformat()
    c.execute('INSERT INTO senate_reviews(id,bill_id,committee_id,cycle,version,assigned_at,due_at) VALUES(?,?,?,?,?,?,?)',
              (uid(), bill['id'], committee_id, cycle, bill['version'], now, due))
    c.execute("UPDATE bills SET status='committee_review' WHERE id=?", (bill['id'],))
    c.execute('UPDATE senate_bill_meta SET current_committee_id=? WHERE bill_id=?', (committee_id, bill['id']))


@router.get('/config')
def config():
    with read_db() as c:
        return {'document_types': allrows(c, 'SELECT * FROM senate_document_types ORDER BY rowid'),
                'categories': allrows(c, 'SELECT * FROM senate_categories ORDER BY name'),
                'flag_types': allrows(c, 'SELECT * FROM senate_flag_definitions ORDER BY name'), 'settings': settings(c)}


@router.get('/me')
def me(current=Depends(senate_actor)):
    with transaction() as c:
        c.execute('INSERT OR IGNORE INTO senate_profiles(user_id) VALUES(?)', (current['id'],))
        flags = flag_list(c, current)
        invites = allrows(c, "SELECT ca.*,b.title FROM senate_coauthors ca JOIN bills b ON b.id=ca.bill_id WHERE ca.user_id=? AND ca.status='pending'", (current['id'],))
        return {'id': current['id'], 'roles': sorted(current['roles']), 'profile': profile(c, current['id']), 'flags': flags,
                'capabilities': {'create_bill': 'senator' in flags, 'admin': 'superadmin' in flags,
                'pro_tempore': 'pro_tempore' in flags, 'lieutenant_governor': 'lieutenant_governor' in flags}, 'invitations': invites}


def senator_rows(c):
    return [dict(profile(c, u['id']), id=u['id']) for u in known_users(c)
            if has_flag(c, {'id': u['id'], 'roles': set(u['roles'])}, 'senator')]


@router.get('/public')
def public():
    with transaction() as c:
        process_deadlines(c)
        sessions = []
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='senate_session_details'").fetchone():
            sessions = allrows(c, "SELECT s.*,d.* FROM legislative_sessions s JOIN senate_session_details d ON s.id=d.session_id WHERE d.status!='draft' ORDER BY d.starts_at DESC")
        return {'bills': list_bills(c, {'id': 'guest', 'roles': set()}),
                'committees': allrows(c, 'SELECT * FROM senate_committees WHERE active=1 ORDER BY name'), 'sessions': sessions,
                'news': allrows(c, 'SELECT * FROM senate_news ORDER BY published_at DESC'), 'senators': senator_rows(c), 'settings': settings(c)}


@router.get('/bills')
def bills(mine: bool = False, q: str = '', status: str = '', category: str = '', type: str = '', committee: str = '',
          author: str = '', coauthor: str = '', date_from: str = '', date_to: str = '', current=Depends(optional_senate_actor)):
    for value in (date_from, date_to):
        if value:
            try:
                if len(value) != 10:
                    raise ValueError
                datetime.strptime(value, '%Y-%m-%d')
            except ValueError:
                fail(422, 'INVALID_DATE_FILTER', 'Укажите дату в формате ГГГГ-ММ-ДД.')
    if date_from and date_to and date_from > date_to:
        fail(422, 'INVALID_DATE_RANGE', 'Начало периода не может быть позже окончания.')
    with transaction() as c:
        process_deadlines(c)
        return list_bills(c, current, mine, q, status, category, type, committee, author, coauthor, date_from, date_to)


def validate_changes(c, raw):
    if not isinstance(raw, list) or len(raw) > 200:
        fail(422, 'INVALID_CHANGES', 'Список изменений должен содержать не более 200 положений.')
    changes, targets = [], set()
    for change in raw:
        if not isinstance(change, dict) or change.get('operation') not in {'add', 'amend', 'repeal'}:
            fail(422, 'INVALID_CHANGE', 'Выберите добавление, изменение или отмену.')
        operation = change['operation']
        target = change.get('target') or {}
        if not isinstance(target, dict):
            fail(422, 'INVALID_TARGET', 'Некорректная ссылка на положение библиотеки.')
        article_id, node_id = target.get('article_id') or change.get('article_id'), target.get('node_id') or change.get('node_id')
        snapshot = None
        if operation != 'add':
            if bool(article_id) == bool(node_id):
                fail(422, 'TARGET_REQUIRED', 'Выберите одну статью или один раздел.')
            table, target_id = ('articles' if article_id else 'structure_nodes'), article_id or node_id
            if target_id in targets:
                fail(422, 'DUPLICATE_TARGET', 'Одно положение не может быть изменено дважды в одной редакции.')
            targets.add(target_id)
            snapshot = one(c, f"SELECT * FROM {table} WHERE id=? AND status='active'", (target_id,))
            target = {'code_id': snapshot['code_id'], 'article_id' if article_id else 'node_id': target_id,
                      'revision_id': snapshot.get('revision_id'), 'number': snapshot['number'], 'parent_id': snapshot['parent_id']}
        else:
            code_id = target.get('code_id') or change.get('code_id')
            one(c, "SELECT id FROM codes WHERE id=? AND status='active'", (code_id,))
            parent_id = target.get('parent_id') or change.get('parent_id')
            if parent_id:
                one(c, "SELECT id FROM structure_nodes WHERE id=? AND code_id=? AND status='active'", (parent_id, code_id))
            target = {'code_id': code_id, 'parent_id': parent_id}
        article = change.get('article', {})
        if not isinstance(article, dict):
            fail(422, 'INVALID_CHANGE', 'Некорректное содержание положения.')
        text = clean(article.get('text_rich', change.get('text_rich', '')))
        if operation != 'repeal' and not re.sub('<[^>]+>', '', text).strip():
            fail(422, 'TEXT_REQUIRED', 'Укажите новый текст положения.')
        content = {'id': snapshot['id'] if snapshot else article.get('id') or uid(),
                   'number': plain(article.get('number', change.get('number', snapshot['number'] if snapshot else '')), 100),
                   'title': plain(article.get('title', change.get('title', snapshot['title'] if snapshot else '')), 250), 'text_rich': text}
        if operation == 'add' and (not content['number'] or not content['title']):
            fail(422, 'NEW_ARTICLE_REQUIRED', 'Для нового положения укажите номер и заголовок.')
        changes.append({'operation': operation, 'target': target, 'article': content, 'source': snapshot})
    return changes


def validate_bill(c, body, old=None):
    title = plain(body.get('title', old['title'] if old else ''), 250)
    rich = clean(body.get('text_rich', old['text_rich'] if old else ''))
    if not title or not re.sub('<[^>]+>', '', rich).strip():
        fail(422, 'BILL_REQUIRED_FIELDS', 'Укажите название и текст проекта.')
    document_type = body.get('document_type', old['document_type'] if old else 'senate_bill')
    category = body.get('category_id', old['category_id'] if old else 'general')
    one(c, 'SELECT id FROM senate_document_types WHERE id=?' + ('' if old and old['document_type'] == document_type else ' AND active=1'), (document_type,))
    one(c, 'SELECT id FROM senate_categories WHERE id=?' + ('' if old and old['category_id'] == category else ' AND active=1'), (category,))
    effective = body.get('effective_at', old['effective_at'] if old else None)
    if effective:
        try:
            datetime.fromisoformat(effective.replace('Z', '+00:00'))
        except (ValueError, TypeError, AttributeError):
            fail(422, 'INVALID_DATE', 'Укажите корректную дату вступления в силу.')
    changes = validate_changes(c, body['changes']) if 'changes' in body else (old['changes'] if old else [])
    return dict(title=title, text_rich=rich, document_type=document_type, category_id=category, effective_at=effective, changes=changes)


def save_revision(c, bill_id, version, values, current):
    revision_id = uid()
    c.execute('INSERT INTO bill_revisions VALUES(?,?,?,?,?,?)', (revision_id, bill_id, version, values['text_rich'], current['id'], ts()))
    c.execute('INSERT INTO senate_revision_meta VALUES(?,?)', (revision_id, dumps({k: v for k, v in values.items() if k != 'text_rich'})))


@router.post('/bills')
def create_bill(body: dict, current=Depends(senate_actor)):
    with transaction() as c:
        return create_bill_in_transaction(c, body, current)


def create_bill_in_transaction(c, body, current):
    if not has_flag(c, current, 'senator'):
        fail(403, 'SENATOR_REQUIRED', 'Подавать проекты могут только сенаторы.')
    values, bill_id = validate_bill(c, body), uid()
    number = f"SB-{c.execute('SELECT COUNT(*)+1 FROM senate_bill_meta').fetchone()[0]:04d}"
    c.execute('INSERT INTO bills VALUES(?,?,?,?,?,?)', (bill_id, values['title'], current['id'], 'draft', values['text_rich'], ts()))
    c.execute('INSERT INTO senate_bill_meta(bill_id,number,document_type,category_id,effective_at,changes_json) VALUES(?,?,?,?,?,?)',
              (bill_id, number, values['document_type'], values['category_id'], values['effective_at'], dumps(values['changes'])))
    save_revision(c, bill_id, 1, values, current)
    event(c, bill_id, 'draft_created', 'Черновик создан.', current)
    senate_audit(c, current, 'bill.create', 'bill', bill_id, after={'version': 1})
    return {'id': bill_id, 'status': 'draft', 'version': 1, 'number': number}


@router.get('/bills/{bill_id}')
def bill_detail(bill_id: str, current=Depends(optional_senate_actor)):
    with transaction() as c:
        process_deadlines(c)
        bill = readable_bill(c, bill_id, current)
        bill['revisions'] = allrows(c, 'SELECT r.*,m.metadata_json FROM bill_revisions r LEFT JOIN senate_revision_meta m ON m.revision_id=r.id WHERE r.bill_id=? ORDER BY r.version DESC', (bill_id,))
        for revision in bill['revisions']:
            revision['metadata'] = loads(revision.pop('metadata_json'), {})
        bill['events'] = allrows(c, 'SELECT * FROM bill_events WHERE bill_id=? ORDER BY created_at,id', (bill_id,))
        private = bill['author_id'] == current['id'] or has_flag(c, current, 'superadmin')
        if not private:
            bill['events'] = [e for e in bill['events'] if e['event_type'] != 'coauthor_declined']
        bill['coauthors'] = allrows(c, 'SELECT * FROM senate_coauthors WHERE bill_id=?' + ('' if private else " AND status='accepted'"), (bill_id,))
        bill['reviews'] = allrows(c, 'SELECT * FROM senate_reviews WHERE bill_id=? ORDER BY assigned_at,id', (bill_id,))
        for review in bill['reviews']:
            item = maybe(c, 'SELECT snapshot_json FROM senate_committee_agenda_items WHERE review_id=?', (review['id'],))
            review['agenda_snapshot'] = loads(item['snapshot_json'], {}) if item else None
        manage_agendas = has_flag(c, current, 'superadmin') or has_flag(c, current, 'pro_tempore')
        bill['votes'] = allrows(c, '''SELECT v.* FROM votes v LEFT JOIN senate_session_details s ON s.session_id=v.session_id
            WHERE v.bill_id=? AND (s.status IS NULL OR s.status!='draft' OR ?) ORDER BY v.created_at''', (bill_id, manage_agendas))
        return bill


@router.put('/bills/{bill_id}')
def update_bill(bill_id: str, body: dict, current=Depends(senate_actor)):
    with transaction() as c:
        bill = readable_bill(c, bill_id, current)
        require_editor(c, bill, current)
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='senate_committee_proposals'").fetchone() and maybe(c,
                'SELECT 1 FROM senate_committee_proposals WHERE bill_id=?', (bill_id,)):
            fail(409, 'PROPOSAL_FROZEN', 'Состав и текст предложения о комитете зафиксированы. Создайте новое предложение.')
        if type(body.get('base_version')) is not int or body['base_version'] != bill['version']:
            fail(409, 'STALE_VERSION', 'Проект изменён другим автором. Обновите данные перед сохранением.')
        values, version = validate_bill(c, body, bill), bill['version'] + 1
        c.execute('UPDATE bills SET title=?,text_rich=? WHERE id=?', (values['title'], values['text_rich'], bill_id))
        c.execute('UPDATE senate_bill_meta SET document_type=?,category_id=?,effective_at=?,version=?,changes_json=? WHERE bill_id=?',
                  (values['document_type'], values['category_id'], values['effective_at'], version, dumps(values['changes']), bill_id))
        save_revision(c, bill_id, version, values, current)
        event(c, bill_id, 'revision_created', {'version': version}, current)
        senate_audit(c, current, 'bill.revise', 'bill', bill_id, {'version': bill['version']}, {'version': version})
    return {'id': bill_id, 'version': version, 'status': bill['status']}


@router.post('/bills/{bill_id}/submit')
def submit_bill(bill_id: str, current=Depends(senate_actor)):
    with transaction() as c:
        bill = readable_bill(c, bill_id, current)
        require_editor(c, bill, current)
        if not has_flag(c, current, 'senator'):
            fail(403, 'SENATOR_REQUIRED', 'Подача доступна только сенатору.')
        if not bill['effective_at']:
            fail(422, 'EFFECTIVE_DATE_REQUIRED', 'Укажите дату вступления изменений в силу.')
        document_type = one(c, 'SELECT * FROM senate_document_types WHERE id=?', (bill['document_type'],))
        for field in loads(document_type['required_fields_json'], []):
            if not bill.get(field):
                fail(422, 'DOCUMENT_REQUIRED_FIELD', 'Заполните обязательное поле шаблона: ' + field)
        c.execute('UPDATE senate_bill_meta SET submitted_at=COALESCE(submitted_at,?) WHERE bill_id=?', (ts(), bill_id))
        target = bill['current_committee_id'] or 'rules'
        assign_review(c, bill, target)
        event(c, bill_id, 'submitted', {'committee_id': target, 'version': bill['version']}, current)
        senate_audit(c, current, 'bill.submit', 'bill', bill_id, {'status': bill['status']}, {'status': 'committee_review'})
    return {'id': bill_id, 'status': 'committee_review'}


@router.post('/bills/{bill_id}/coauthors')
def invite_coauthor(bill_id: str, body: dict, current=Depends(senate_actor)):
    with transaction() as c:
        bill = readable_bill(c, bill_id, current)
        require_editor(c, bill, current)
        if bill['author_id'] != current['id']:
            fail(403, 'AUTHOR_REQUIRED', 'Соавторов приглашает автор проекта.')
        user_id = plain(body.get('user_id'), 200)
        if user_id == current['id'] or not any(u['id'] == user_id for u in known_users(c)):
            fail(422, 'INVALID_COAUTHOR', 'Выберите зарегистрированного пользователя, кроме автора.')
        c.execute('''INSERT INTO senate_coauthors VALUES(?,?,?,?,NULL) ON CONFLICT(bill_id,user_id)
                     DO UPDATE SET status='pending',invited_at=excluded.invited_at,responded_at=NULL''',
                  (bill_id, user_id, 'pending', ts()))
        senate_audit(c, current, 'coauthor.invite', 'bill', bill_id, after={'user_id': user_id})
    return {'status': 'pending'}


@router.post('/bills/{bill_id}/coauthors/respond')
def respond_coauthor(bill_id: str, body: dict, current=Depends(senate_actor)):
    if type(body.get('accept')) is not bool:
        fail(422, 'INVALID_RESPONSE', 'Укажите ответ на приглашение.')
    with transaction() as c:
        invite = one(c, "SELECT * FROM senate_coauthors WHERE bill_id=? AND user_id=? AND status='pending'", (bill_id, current['id']))
        status = 'accepted' if body['accept'] else 'declined'
        c.execute('UPDATE senate_coauthors SET status=?,responded_at=? WHERE bill_id=? AND user_id=?', (status, ts(), bill_id, current['id']))
        event(c, bill_id, 'coauthor_' + status, current['id'], current)
        senate_audit(c, current, 'coauthor.respond', 'bill', bill_id, invite, {'status': status})
    return {'status': status}


@router.get('/library')
def library(q: str = '', current=Depends(senate_actor)):
    with read_db() as c:
        require_flag(c, current, 'senator')
        articles = allrows(c, '''SELECT a.id,a.code_id,a.parent_id,a.number,a.title,a.text_rich,a.revision_id,c.name AS code_name
                             FROM articles a JOIN codes c ON c.id=a.code_id WHERE a.status='active' ORDER BY c.name,a.sort_key''')
        nodes = allrows(c, "SELECT * FROM structure_nodes WHERE status='active' ORDER BY sort_key")
        query = q.casefold()
        return {'articles': [a for a in articles if query in (a['title'] + ' ' + a['number'] + ' ' + a['code_name']).casefold()][:200],
                'nodes': [n for n in nodes if query in (n['title'] + ' ' + n['number']).casefold()][:200]}


@router.get('/senators')
def senators():
    with read_db() as c:
        return senator_rows(c)


@router.put('/profile')
def update_profile(body: dict, current=Depends(senate_actor)):
    with transaction() as c:
        if not has_flag(c, current, 'senator'):
            fail(403, 'SENATOR_REQUIRED', 'Публичный профиль доступен сенаторам.')
        before = profile(c, current['id'])
        values = {f: plain(body.get(f, before[f]), 250) for f in PROFILE_FIELDS}
        if values['email'] and ('@' not in values['email'] or ' ' in values['email']):
            fail(422, 'INVALID_EMAIL', 'Укажите корректную электронную почту.')
        c.execute('''INSERT INTO senate_profiles VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
          name=excluded.name,capitol_office=excluded.capitol_office,district_office=excluded.district_office,
          phone=excluded.phone,email=excluded.email''', (current['id'], *(values[f] for f in PROFILE_FIELDS)))
        senate_audit(c, current, 'profile.update', 'profile', current['id'], before, values)
    return values


@router.get('/committees')
def committees():
    with read_db() as c:
        return allrows(c, 'SELECT * FROM senate_committees WHERE active=1 ORDER BY name')


@router.get('/committees/{committee_id}')
def committee_detail(committee_id: str, current=Depends(optional_senate_actor)):
    with transaction() as c:
        process_deadlines(c)
        row = one(c, 'SELECT * FROM senate_committees WHERE id=?', (committee_id,))
        row['members'] = [dict(profile(c, u['id']), id=u['id'], chair=has_flag(c, {'id': u['id'], 'roles': set(u['roles'])}, 'chair:' + committee_id))
                          for u in known_users(c) if has_flag(c, {'id': u['id'], 'roles': set(u['roles'])}, 'committee:' + committee_id)]
        row['queue'] = list_bills(c, current, committee=committee_id)
        row['reviews'] = allrows(c, 'SELECT r.*,b.title FROM senate_reviews r JOIN bills b ON b.id=r.bill_id WHERE r.committee_id=? ORDER BY assigned_at DESC', (committee_id,))
        row['agendas'] = allrows(c, 'SELECT * FROM senate_committee_agendas WHERE committee_id=? ORDER BY published_at DESC', (committee_id,))
        for agenda in row['agendas']:
            agenda['items'] = allrows(c, 'SELECT i.position,i.snapshot_json,r.*,b.title FROM senate_committee_agenda_items i JOIN senate_reviews r ON r.id=i.review_id JOIN bills b ON b.id=r.bill_id WHERE i.agenda_id=? ORDER BY i.position', (agenda['id'],))
        return row


@router.post('/committees/{committee_id}/agenda')
def committee_agenda(committee_id: str, body: dict, current=Depends(senate_actor)):
    with transaction() as c:
        process_deadlines(c)
        if not all(has_flag(c, current, f) for f in ('senator', 'committee:' + committee_id, 'chair:' + committee_id)):
            fail(403, 'CHAIR_REQUIRED', 'Повестку формирует председатель комитета.')
        ids = body.get('review_ids')
        title = plain(body.get('title', 'Повестка комитета'), 250)
        if not isinstance(ids, list) or not ids or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
            fail(422, 'INVALID_AGENDA', 'Выберите рассмотрения для повестки без повторов.')
        agenda_id = uid()
        c.execute('INSERT INTO senate_committee_agendas VALUES(?,?,?,?,?)', (agenda_id, committee_id, title, current['id'], ts()))
        for position, review_id in enumerate(ids, 1):
            review = one(c, "SELECT * FROM senate_reviews WHERE id=? AND committee_id=? AND status='report_ready'", (review_id, committee_id))
            bill = bill_row(c, review['bill_id'])
            if bill['status'] != 'committee_review':
                fail(409, 'REVIEW_CLOSED', 'Рассмотрение этого предмета уже завершено.')
            category = one(c, 'SELECT * FROM senate_categories WHERE id=?', (bill['category_id'],))
            snapshot = {'category': category, 'rule': category['committee_rule'], 'threshold': category['committee_threshold'],
                        'version': review['version'], 'title': bill['title'], 'text_rich': bill['text_rich'], 'settings': settings(c)}
            c.execute('INSERT INTO senate_committee_agenda_items VALUES(?,?,?,?)', (agenda_id, review_id, position, dumps(snapshot)))
            c.execute("UPDATE senate_reviews SET status='agenda' WHERE id=?", (review_id,))
            event(c, review['bill_id'], 'committee_agenda', {'agenda_id': agenda_id, 'version': review['version']}, current)
        senate_audit(c, current, 'committee.agenda', 'committee_agenda', agenda_id, after={'title': title, 'review_ids': ids})
    return {'id': agenda_id}


@router.post('/committees/{committee_id}/reports')
def committee_report(committee_id: str, body: dict, current=Depends(senate_actor)):
    with transaction() as c:
        process_deadlines(c)
        if not has_flag(c, current, 'committee:' + committee_id) or has_flag(c, current, 'senator'):
            fail(403, 'CONSULTANT_REQUIRED', 'Отчёт подаёт консультант комитета без сенаторского флага.')
        review = one(c, "SELECT * FROM senate_reviews WHERE bill_id=? AND committee_id=? AND status='awaiting_report' ORDER BY cycle DESC LIMIT 1", (body.get('bill_id'), committee_id))
        text, recommendation = plain(body.get('text'), 100000), body.get('recommendation')
        if not text or recommendation not in DECISIONS:
            fail(422, 'REPORT_REQUIRED', 'Укажите анализ и рекомендацию.')
        now = ts()
        due = (datetime.now(timezone.utc) + timedelta(days=int(setting(c, 'consideration_days', 10)))).isoformat()
        c.execute("UPDATE senate_reviews SET status='report_ready',report_text=?,report_by=?,recommendation=?,report_at=?,consideration_due_at=? WHERE id=?",
                  (text, current['id'], recommendation, now, due, review['id']))
        event(c, review['bill_id'], 'consultant_report', {'review_id': review['id'], 'recommendation': recommendation}, current)
        senate_audit(c, current, 'committee.report', 'review', review['id'], after={'recommendation': recommendation})
    return {'status': 'report_ready', 'consideration_due_at': due}


@router.post('/committees/{committee_id}/decisions')
def committee_decision(committee_id: str, body: dict, current=Depends(senate_actor)):
    with transaction() as c:
        process_deadlines(c)
        if not all(has_flag(c, current, f) for f in ('senator', 'committee:' + committee_id, 'chair:' + committee_id)):
            fail(403, 'CHAIR_REQUIRED', 'Решение вносит назначенный председатель комитета с флагом сенатора.')
        review = one(c, "SELECT * FROM senate_reviews WHERE bill_id=? AND committee_id=? AND status='agenda' ORDER BY cycle DESC LIMIT 1", (body.get('bill_id'), committee_id))
        bill = bill_row(c, review['bill_id'])
        decision = body.get('decision')
        if decision not in DECISIONS:
            fail(422, 'INVALID_DECISION', 'Выберите допустимое решение комитета.')
        if decision == 'return_to_author' and not setting(c, 'disputed_policies', False):
            fail(409, 'POLICY_UNRESOLVED', 'Возврат на доработку ещё не утверждён владельцем.')
        transcript, comment, tally = plain(body.get('transcript', ''), 200000), plain(body.get('comment', ''), 10000), body.get('tally', {})
        if not transcript or not isinstance(tally, dict) or not tally:
            fail(422, 'MEETING_RECORD_REQUIRED', 'Укажите стенограмму и результаты голосования совещания.')
        for key, value in tally.items():
            if key not in {'yes', 'no', 'abstain', 'absent'} or type(value) is not int or value < 0:
                fail(422, 'INVALID_TALLY', 'Результаты голосования должны содержать целые неотрицательные числа.')
        if decision.startswith('amend_'):
            if body.get('base_version') != bill['version']:
                fail(409, 'STALE_VERSION', 'Редакция изменилась. Обновите предмет.')
            values, version = validate_bill(c, body, bill), bill['version'] + 1
            c.execute('UPDATE bills SET title=?,text_rich=? WHERE id=?', (values['title'], values['text_rich'], bill['id']))
            c.execute('UPDATE senate_bill_meta SET document_type=?,category_id=?,effective_at=?,version=?,changes_json=? WHERE bill_id=?',
                      (values['document_type'], values['category_id'], values['effective_at'], version, dumps(values['changes']), bill['id']))
            save_revision(c, bill['id'], version, values, current)
            bill['version'] = version
        c.execute("UPDATE senate_reviews SET status='decided',decision=?,chair_id=?,comment=?,transcript=?,tally_json=? WHERE id=?",
                  (decision, current['id'], comment, transcript, dumps(tally), review['id']))
        status = {'do_pass': 'ready_plenary', 'amend_do_pass': 'ready_plenary', 'not_pass': 'committee_rejected',
                  'held': 'held', 'return_to_author': 'revision_requested'}.get(decision)
        if decision in {'transfer', 'amend_transfer'}:
            target = body.get('target_committee_id')
            if target == committee_id:
                fail(422, 'INVALID_TARGET_COMMITTEE', 'Для передачи выберите другой комитет.')
            assign_review(c, bill, target)
            status = 'committee_review'
        else:
            c.execute('UPDATE bills SET status=? WHERE id=?', (status, bill['id']))
            if status == 'ready_plenary':
                c.execute('UPDATE senate_bill_meta SET current_committee_id=NULL WHERE bill_id=?', (bill['id'],))
        event(c, bill['id'], 'committee_decision', {'review_id': review['id'], 'decision': decision, 'status': status}, current)
        senate_audit(c, current, 'committee.decision', 'review', review['id'], review, {'decision': decision, 'status': status})
    return {'status': status}


@router.get('/news')
def news():
    with read_db() as c:
        return allrows(c, 'SELECT * FROM senate_news ORDER BY published_at DESC')


@router.post('/news')
def create_news(body: dict, current=Depends(senate_actor)):
    with transaction() as c:
        require_admin(c, current)
        title, text = plain(body.get('title'), 250), clean(body.get('body', ''))
        if not title or not text:
            fail(422, 'NEWS_REQUIRED', 'Укажите заголовок и текст новости.')
        news_id = uid()
        c.execute('INSERT INTO senate_news VALUES(?,?,?,?,?)', (news_id, title, text, current['id'], ts()))
        senate_audit(c, current, 'news.publish', 'news', news_id, after={'title': title})
    return {'id': news_id}


@router.get('/admin')
def admin(current=Depends(senate_actor)):
    with read_db() as c:
        require_admin(c, current)
        return {'users': known_users(c), 'flags': allrows(c, 'SELECT * FROM senate_user_flags ORDER BY user_id,flag'),
                'flag_types': allrows(c, 'SELECT * FROM senate_flag_definitions ORDER BY name'),
                'settings': settings(c), 'categories': allrows(c, 'SELECT * FROM senate_categories'),
                'document_types': allrows(c, 'SELECT * FROM senate_document_types'),
                'audit': allrows(c, 'SELECT * FROM senate_audit ORDER BY created_at DESC LIMIT 500')}


@router.put('/admin/settings')
def admin_settings(body: dict, current=Depends(senate_actor)):
    allowed = {'committee_review_days', 'consideration_days', 'quorum', 'min_notice_hours', 'disputed_policies', 'news_admin_only', 'pro_tempore_rule', 'pro_tempore_threshold'}
    if set(body) - allowed:
        fail(422, 'INVALID_SETTING', 'Неизвестная настройка Сената.')
    with transaction() as c:
        require_admin(c, current)
        before = settings(c)
        for key, value in body.items():
            if key == 'pro_tempore_rule':
                if value is not None and value not in RULES:
                    fail(422, 'INVALID_SETTING', 'Выберите правило выбора Pro Tempore.')
            elif key == 'pro_tempore_threshold':
                if value is not None and (type(value) is not int or value < 1):
                    fail(422, 'INVALID_SETTING', 'Укажите целый положительный порог.')
            elif key in {'disputed_policies', 'news_admin_only'}:
                if type(value) is not bool:
                    fail(422, 'INVALID_SETTING', 'Настройка должна быть логическим значением.')
            elif type(value) is not int or not (0 if key == 'min_notice_hours' else 1) <= value <= 10000:
                fail(422, 'INVALID_SETTING', 'Настройка должна быть целым числом в допустимом диапазоне.')
            c.execute('INSERT INTO senate_settings VALUES(?,?,?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,updated_at=excluded.updated_at', (key, dumps(value), ts()))
        senate_audit(c, current, 'settings.update', 'settings', 'senate', before, body)
    return body


@router.post('/admin/flags')
def admin_flag(body: dict, current=Depends(senate_actor)):
    with transaction() as c:
        require_admin(c, current)
        user_id, flag = plain(body.get('user_id'), 200), plain(body.get('flag'), 100)
        if not user_id or not flag or type(body.get('active')) is not bool:
            fail(422, 'INVALID_FLAG', 'Укажите пользователя, флаг и состояние.')
        if flag.startswith(('committee:', 'chair:')):
            one(c, 'SELECT id FROM senate_committees WHERE id=? AND active=1', (flag.split(':', 1)[1],))
        before = maybe(c, 'SELECT * FROM senate_user_flags WHERE user_id=? AND flag=?', (user_id, flag))
        c.execute('INSERT OR IGNORE INTO senate_profiles(user_id) VALUES(?)', (user_id,))
        c.execute('INSERT INTO senate_user_flags VALUES(?,?,?) ON CONFLICT(user_id,flag) DO UPDATE SET active=excluded.active', (user_id, flag, int(body['active'])))
        senate_audit(c, current, 'flags.assign', 'user', user_id, before, body)
    return body


@router.post('/admin/categories')
def admin_category(body: dict, current=Depends(senate_actor)):
    with transaction() as c:
        require_admin(c, current)
        category_id = plain(body.get('id') or uid(), 100)
        old = maybe(c, 'SELECT * FROM senate_categories WHERE id=?', (category_id,))
        name, active = plain(body.get('name', old['name'] if old else ''), 150), body.get('active', True)
        if not name or type(active) is not bool:
            fail(422, 'INVALID_CATEGORY', 'Укажите название и состояние категории.')
        values = []
        for stage in ('committee', 'plenary'):
            rule = body.get(stage + '_rule', old[stage + '_rule'] if old else 'simple_majority')
            threshold = body.get(stage + '_threshold', old[stage + '_threshold'] if old else None)
            if rule not in RULES or (rule in {'absolute_majority', 'two_thirds'} and (type(threshold) is not int or threshold < 1)):
                fail(422, 'INVALID_RULE', 'Выберите правило и положительный целый порог, когда он необходим.')
            values.extend((rule, threshold))
        c.execute('''INSERT INTO senate_categories VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
          active=excluded.active,committee_rule=excluded.committee_rule,committee_threshold=excluded.committee_threshold,
          plenary_rule=excluded.plenary_rule,plenary_threshold=excluded.plenary_threshold''', (category_id, name, int(active), *values))
        senate_audit(c, current, 'category.update', 'category', category_id, old, body)
    return {'id': category_id}


@router.post('/admin/flag-types')
def admin_flag_type(body: dict, current=Depends(senate_actor)):
    with transaction() as c:
        require_admin(c, current)
        flag_id = plain(body.get('id') or uid(), 100)
        old = maybe(c, 'SELECT * FROM senate_flag_definitions WHERE id=?', (flag_id,))
        name = plain(body.get('name', old['name'] if old else ''), 150)
        active = body.get('active', True)
        permissions = body.get('permissions', loads(old['permissions_json'], []) if old else [])
        canonical = {'senator', 'pro_tempore', 'lieutenant_governor', 'superadmin'}
        if not name or type(active) is not bool or not isinstance(permissions, list) or any(not isinstance(x, str) for x in permissions):
            fail(422, 'INVALID_FLAG_TYPE', 'Укажите название, состояние и список полномочий.')
        for permission in permissions:
            if permission not in canonical:
                if not permission.startswith(('committee:', 'chair:')):
                    fail(422, 'INVALID_PERMISSION', 'Допустимы только прямые полномочия Сената без вложенных флагов.')
                one(c, 'SELECT id FROM senate_committees WHERE id=? AND active=1', (permission.split(':', 1)[1],))
        c.execute('''INSERT INTO senate_flag_definitions VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE
          SET name=excluded.name,active=excluded.active,permissions_json=excluded.permissions_json''',
          (flag_id, name, int(active), dumps(sorted(set(permissions)))))
        senate_audit(c, current, 'flag_type.configure', 'flag_type', flag_id, old, body)
    return {'id': flag_id}


@router.post('/admin/document-types')
def admin_document_type(body: dict, current=Depends(senate_actor)):
    with transaction() as c:
        require_admin(c, current)
        type_id = plain(body.get('id') or uid(), 100)
        old = maybe(c, 'SELECT * FROM senate_document_types WHERE id=?', (type_id,))
        name, required = plain(body.get('name', old['name'] if old else ''), 150), body.get('required_fields', [])
        if not name or not isinstance(required, list) or any(x not in {'title', 'text_rich', 'effective_at', 'changes'} for x in required):
            fail(422, 'INVALID_DOCUMENT_TYPE', 'Укажите название и допустимые обязательные поля.')
        active = body.get('active', True)
        if type(active) is not bool:
            fail(422, 'INVALID_DOCUMENT_TYPE', 'Состояние типа должно быть логическим значением.')
        c.execute('''INSERT INTO senate_document_types VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
          active=excluded.active,template=excluded.template,required_fields_json=excluded.required_fields_json''',
          (type_id, name, int(active), clean(body.get('template', '')), dumps(required)))
        senate_audit(c, current, 'document_type.update', 'document_type', type_id, old, body)
    return {'id': type_id}


@router.post('/admin/committees')
def admin_committee(body: dict, current=Depends(senate_actor)):
    with transaction() as c:
        require_admin(c, current)
        committee_id, name = plain(body.get('id') or uid(), 100), plain(body.get('name'), 150)
        if not name:
            fail(422, 'NAME_REQUIRED', 'Укажите название комитета.')
        old = maybe(c, 'SELECT * FROM senate_committees WHERE id=?', (committee_id,))
        c.execute('INSERT INTO senate_committees(id,name,description,created_at) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,description=excluded.description',
                  (committee_id, name, plain(body.get('description', ''), 10000), ts()))
        senate_audit(c, current, 'committee.configure', 'committee', committee_id, old, body)
    return {'id': committee_id}


@router.get('/bills/{bill_id}/export.json')
def export_json(bill_id: str, current=Depends(optional_senate_actor)):
    bill = bill_detail(bill_id, current)
    versions, events, votes = bill.pop('revisions'), bill.pop('events'), bill.pop('votes')
    coauthors = [x['user_id'] for x in bill.pop('coauthors') if x['status'] == 'accepted']
    with read_db() as c:
        act = maybe(c, 'SELECT id FROM adopted_acts WHERE bill_id=?', (bill_id,))
        for vote in votes:
            vote['choices'] = allrows(c, 'SELECT voter_id,choice FROM vote_choices WHERE vote_id=? ORDER BY voter_id', (vote['id'],))
            if vote['session_id']:
                from app.senate_sessions import _detail
                session = _detail(c, vote['session_id'], current)
                vote['session'] = {key: value for key, value in session.items() if key not in {'items'}}
                item = next((item for item in session['items'] if item['vote_id'] == vote['id']), None)
                if item:
                    vote.update({key: item[key] for key in ('version', 'rule', 'threshold', 'category', 'choices', 'tally')})
                corrections = allrows(c, "SELECT user_id,action,before_json,after_json,created_at FROM senate_audit WHERE object_id=? AND action IN ('senate.vote.admin_correction','senate.vote.admin_resolution') ORDER BY created_at", (vote['session_id'],))
                vote['administrative_corrections'] = corrections
    export = {'format': 'san-andreas.senate-law', 'format_version': '1.0', 'export_id': uid(), 'generated_at': ts(),
              'law': {'bill_id': bill_id, 'act_id': act['id'] if act else None, 'title': bill['title'], 'document_type': bill['document_type'],
                      'category': bill['category_id'], 'effective_at': bill['effective_at'], 'authors': [bill['author_id']],
                      'coauthors': coauthors, 'final_text_rich': bill['text_rich'], 'changes': bill['changes'], 'status': bill['status'],
                      'history': {'versions': versions, 'events': events, 'votes': votes, 'reviews': bill['reviews']}}}
    with transaction() as c:
        if current['id'] != 'guest':
            senate_audit(c, current, 'bill.export', 'bill', bill_id, after={'format': 'json', 'export_id': export['export_id']})
    return JSONResponse(export, headers={'Content-Disposition': f'attachment; filename="{bill["number"]}.json"'})


@router.post('/admin/bills/{bill_id}/correct')
def admin_correct_bill(bill_id: str, body: dict, current=Depends(senate_actor)):
    with transaction() as c:
        require_admin(c, current)
        reason = plain(body.get('reason', ''), 10000)
        if not reason:
            fail(422, 'REASON_REQUIRED', 'Укажите основание административного исправления.')
        bill = bill_row(c, bill_id)
        if body.get('base_version') != bill['version']:
            fail(409, 'STALE_VERSION', 'Предмет изменён. Обновите данные.')
        before = dict(bill)
        changes_text = any(key in body for key in ('title', 'text_rich', 'document_type', 'category_id', 'effective_at', 'changes'))
        if changes_text:
            if bill['status'] == 'agenda_published':
                fail(409, 'AGENDA_FROZEN', 'Текст опубликованной повестки зафиксирован. Исправьте данные после завершения заседания.')
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='senate_committee_proposals'").fetchone() and maybe(c,
                    'SELECT 1 FROM senate_committee_proposals WHERE bill_id=?', (bill_id,)):
                fail(409, 'PROPOSAL_FROZEN', 'Состав предложения зафиксирован. Создайте новое предложение.')
            values = validate_bill(c, body, bill)
            bill['version'] += 1
            c.execute('UPDATE bills SET title=?,text_rich=? WHERE id=?', (values['title'], values['text_rich'], bill_id))
            c.execute('UPDATE senate_bill_meta SET document_type=?,category_id=?,effective_at=?,version=?,changes_json=? WHERE bill_id=?',
                      (values['document_type'], values['category_id'], values['effective_at'], bill['version'], dumps(values['changes']), bill_id))
            save_revision(c, bill_id, bill['version'], values, current)
        status = body.get('status', bill['status'])
        allowed = {'draft', 'committee_review', 'held', 'revision_requested', 'committee_rejected', 'ready_plenary', 'adopted', 'rejected'}
        if status not in allowed or bill['status'] == 'agenda_published':
            fail(409, 'STATUS_CORRECTION_BLOCKED', 'Нельзя исправить статус предмета опубликованной повестки.')
        if status == 'committee_review' and not bill['current_committee_id']:
            fail(422, 'COMMITTEE_REQUIRED', 'Для рассмотрения необходим действующий цикл комитета.')
        c.execute('UPDATE bills SET status=? WHERE id=?', (status, bill_id))
        event(c, bill_id, 'administrative_correction', {'reason': reason, 'before_status': before['status'], 'status': status,
              'before_version': before['version'], 'version': bill['version']}, current)
        senate_audit(c, current, 'bill.correct', 'bill', bill_id, before, {'reason': reason, 'status': status, 'version': bill['version']})
    return {'id': bill_id, 'status': status, 'version': bill['version']}


@router.put('/admin/profiles/{user_id}')
@router.post('/admin/profiles/{user_id}')
def admin_profile(user_id: str, body: dict, current=Depends(senate_actor)):
    with transaction() as c:
        require_admin(c, current)
        reason = plain(body.get('reason', ''), 10000)
        if not reason:
            fail(422, 'REASON_REQUIRED', 'Укажите основание исправления профиля.')
        before = profile(c, user_id)
        values = {f: plain(body.get(f, before[f]), 250) for f in PROFILE_FIELDS}
        if values['email'] and ('@' not in values['email'] or ' ' in values['email']):
            fail(422, 'INVALID_EMAIL', 'Укажите корректную электронную почту.')
        c.execute('''INSERT INTO senate_profiles VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
          name=excluded.name,capitol_office=excluded.capitol_office,district_office=excluded.district_office,
          phone=excluded.phone,email=excluded.email''', (user_id, *(values[f] for f in PROFILE_FIELDS)))
        senate_audit(c, current, 'profile.correct', 'profile', user_id, before, dict(values, reason=reason))
    return dict(values, user_id=user_id)


@router.get('/bills/{bill_id}/export.pdf')
def export_pdf(bill_id: str, current=Depends(optional_senate_actor), time_zone: str = 'UTC'):
    from app.senate_pdf import pdf_response, display, display_time, export_timezone, describe_event
    zone = export_timezone(time_zone)
    bill = bill_detail(bill_id, current)
    with read_db() as c:
        people = {u['id']: u.get('name') or u['id'] for u in known_users(c)}
        committee_names = {r['id']: r['name'] for r in allrows(c, 'SELECT id,name FROM senate_committees')}
    author = people.get(bill['author_id'], bill['author_id'])
    coauthors = ', '.join(people.get(co['user_id'], co['user_id']) for co in bill['coauthors'] if co['status'] == 'accepted') or 'Не указаны'
    sections = [('Реквизиты', f"{bill['number']}\nСтатус: {display(bill['status'])}\nАвтор: {author}\nСоавторы: {coauthors}\nДата вступления в силу: {display_time(bill['effective_at'], zone)}\nЧасовой пояс: {time_zone}")]
    sections.extend((f"Редакция {r['version']} · {display_time(r['created_at'], zone)}", r['text_rich']) for r in reversed(bill['revisions']))
    for review in bill['reviews']:
        sections.append((f"{committee_names.get(review['committee_id'], review['committee_id'])} · цикл {review['cycle']}",
                         f"Отчёт: {review['report_text'] or 'Не предоставлен'}\nРешение: {display(review['decision'])}\n{review['comment'] or ''}\n{review['transcript'] or ''}"))
    history_rows = [[display_time(e['created_at'], zone), people.get(e['actor_id'], e['actor_id']),
                     display(e['event_type']), describe_event(e)] for e in bill['events']]
    return pdf_response(bill['title'], sections, bill['number'] + '.pdf', history_rows=history_rows)
