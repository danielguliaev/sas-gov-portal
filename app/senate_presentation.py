"""Human-readable document context and history, independent of stored event codes."""
from copy import deepcopy
from app.core import allrows, maybe, loads
from app.code_identity import canonical_code
from app.senate_document import document_rich


def person_name(c, user_id):
    row = maybe(c, 'SELECT name FROM senate_profiles WHERE user_id=?', (user_id,))
    if row and row['name']:
        return row['name']
    return {'demo-legislator': 'Тестовый сенатор', 'demo-user': 'Тестовый читатель',
            'demo-superadmin': 'Администратор', 'demo-lawyer': 'Тестовый юрист',
            'system': 'Система', 'system:senate': 'Система'}.get(user_id, 'Участник Сената')


def revision_document(c, bill_id, version):
    from app.core import one
    row = one(c, '''SELECT r.text_rich,r.created_at AS revised_at,m.metadata_json,b.author_id,b.created_at,s.number
        FROM bill_revisions r JOIN bills b ON b.id=r.bill_id LEFT JOIN senate_revision_meta m ON m.revision_id=r.id
        LEFT JOIN senate_bill_meta s ON s.bill_id=b.id WHERE r.bill_id=? AND r.version=?''', (bill_id,version))
    metadata = loads(row.pop('metadata_json'), {})
    return document_context(c, {**row, **metadata, 'text_rich': row['text_rich']}, row['author_id'])['document_rich']


def document_context(c, values, creator_id=None):
    result = deepcopy(values)
    result.setdefault('introduced_at', result.get('submitted_at') or result.get('created_at'))
    result.setdefault('revised_at', result.get('created_at'))
    result.setdefault('author_ids', [creator_id] if creator_id else [])
    result.setdefault('author_names', [person_name(c, x) for x in result['author_ids']])
    result.setdefault('coauthor_names', [person_name(c, x) for x in result.get('coauthor_ids', [])])
    for change in result.get('changes', []):
        target = change['target']
        code_id = canonical_code(c, target.get('code_id'))
        code = maybe(c, 'SELECT name,abbreviation FROM codes WHERE id=?', (code_id,))
        if code:
            name = target.get('code_name')
            if not name or name in {target.get('code_id'), code_id, code['abbreviation']}:
                name = code['name']
            target.update(code_id=code_id, code_name=name, code_abbreviation=code['abbreviation'])
    result['document_rich'] = document_rich(result)
    return result


DECISIONS = {'do_pass': 'Рекомендовать к принятию', 'amend_do_pass': 'Внести поправку и рекомендовать к принятию',
 'not_pass': 'Окончательно отклонить', 'held': 'Оставить в комитете', 'transfer': 'Передать в другой комитет',
 'amend_transfer': 'Внести поправку и передать в другой комитет', 'return_to_author': 'Вернуть на доработку'}
TITLES = {'draft_created': 'Создан черновик', 'revision_created': 'Сохранена новая редакция',
 'submitted': 'Проект подан', 'consultant_report': 'Предоставлен отчёт консультанта',
 'committee_agenda': 'Проект включён в повестку комитета', 'committee_decision': 'Принято решение комитета',
 'committee_deadline': 'Истёк срок комитета', 'coauthor_accepted': 'Соавторство подтверждено',
 'coauthor_declined': 'Приглашение отклонено', 'administrative_correction': 'Внесено административное исправление',
 'senate.agenda.published': 'Опубликована пленарная повестка', 'senate.vote.closed': 'Голосование завершено',
 'senate.vote.correction_pending': 'Результаты направлены на проверку', 'senate.vote.admin_resolved': 'Утверждены исправленные результаты',
 'senate.agenda.cancelled': 'Повестка отменена', 'senate.agenda.rescheduled': 'Заседание перенесено'}


def present_event(c, event):
    row = dict(event)
    row.update(actor_name=person_name(c, row['actor_id']), title=TITLES.get(row['event_type'], 'Обновлены сведения о проекте'), links=[])
    try:
        data = loads(row['body'])
    except (ValueError, TypeError):
        data = None
    if not isinstance(data, dict):
        row['description'] = row['body'] if data is None and not row['body'].startswith(('{', '[')) else 'Сведения сохранены.'
        return row
    sentences = []
    if data.get('committee_id'):
        committee = maybe(c, 'SELECT name FROM senate_committees WHERE id=?', (data['committee_id'],))
        if committee:
            sentences.append('Комитет: ' + committee['name'] + '.')
            row['links'].append({'label': committee['name'], 'href': '#committee/' + data['committee_id']})
    if data.get('session_id'):
        session = maybe(c, 'SELECT title FROM legislative_sessions WHERE id=?', (data['session_id'],))
        if session:
            row['links'].append({'label': session['title'], 'href': '#session/' + data['session_id']})
    if data.get('version'):
        sentences.append('Редакция № ' + str(data['version']) + '.')
    for key, prefix in [('recommendation', 'Рекомендация консультанта: '), ('decision', 'Решение председателя: ')]:
        if key in data:
            sentences.append(prefix + DECISIONS.get(data[key], 'Зафиксировано в материалах рассмотрения') + '.')
    states = {'adopted':'Проект принят', 'rejected':'Проект отклонён', 'ready_plenary':'Проект готов к пленарному рассмотрению',
              'committee_rejected':'Проект окончательно отклонён комитетом', 'committee_review':'Проект передан в комитет',
              'no_quorum':'Заседание не состоялось: кворум не достигнут', 'held':'Проект оставлен в комитете'}
    state = data.get('result') or data.get('status')
    if state in states:
        sentences.append(states[state] + '.')
    if isinstance(data.get('tally'), dict):
        t = data['tally']
        sentences.append(f'За: {t.get("yes",0)}, против: {t.get("no",0)}, воздержались: {t.get("abstain",0)}, отсутствовали: {t.get("absent",0)}.')
    if 'present' in data and 'quorum' in data:
        sentences.append(f'Присутствовали {data["present"]} сенатора. Требуемый кворум: {data["quorum"]}.')
    if data.get('reason'):
        sentences.append('Основание: ' + str(data['reason']))
    row['description'] = ' '.join(sentences) or 'Сведения о рассмотрении обновлены.'
    return row
