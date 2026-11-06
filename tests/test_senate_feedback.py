"""User feedback exercised through the real API, database, and document exports."""
from datetime import datetime, timedelta, timezone
from io import BytesIO

from pypdf import PdfReader
from app import core
from app.senate_document import ENACTING

API = '/api/v1/senate'
ADMIN = {'X-Demo-User': 'superadmin'}
SENATOR = {'X-Demo-User': 'legislator'}
LG = {'X-Demo-User': 'user'}


def create(client, **body):
    response = client.post(API + '/bills', headers=SENATOR, json={'title': 'О поправках к кодексу', **body})
    assert response.status_code == 200, response.text
    return response.json()['id']


def flag(client, user, name, active=True):
    response = client.post(API + '/admin/flags', headers=ADMIN, json={'user_id': user, 'flag': name, 'active': active})
    assert response.status_code == 200, response.text


def test_optional_preamble_and_three_effective_rules_in_every_document(client):
    for rule, effective, phrase in [('standard', None, 'спустя 14 дней после подписания губернатором'),
                                     ('immediate', None, 'является неотложным законом'),
                                     ('specified_date', '2027-02-12', 'вступают в силу 12.02.2027')]:
        bill_id = create(client, effective_rule=rule, effective_at=effective)
        bill = client.get(API + '/bills/' + bill_id, headers=SENATOR).json()
        assert bill['text_rich'] == ''
        assert bill['document_rich'].startswith('<p><strong>' + ENACTING)
        assert phrase in bill['document_rich']
        assert phrase in bill['revisions'][0]['document_rich']
        exported = client.get(API + '/bills/' + bill_id + '/export.json', headers=SENATOR).json()['law']
        assert exported['effective_rule'] == rule
        assert exported['final_text_rich'] == bill['document_rich']
        assert exported['preamble_rich'] == ''
        pdf = client.get(API + '/bills/' + bill_id + '/export.pdf', headers=SENATOR)
        assert phrase in ' '.join(p.extract_text() for p in PdfReader(BytesIO(pdf.content)).pages).replace('\n', ' ')
        assert client.post(API + '/bills/' + bill_id + '/submit', headers=SENATOR).status_code == 200
    for bad in ({'effective_rule': 'signature'}, {'effective_rule': 'specified_date'},
                {'effective_rule': 'specified_date', 'effective_at': '2027-02-30'}):
        assert client.post(API + '/bills', headers=SENATOR, json={'title': 'Неверная дата', **bad}).status_code == 422


def test_parameters_relations_and_numbered_document_do_not_mutate_library(client, admin):
    target = admin('189', cats=['accusation'], crime='felony')['id']
    source = admin('190', cats=['punishment_measure'], sanctions={'prison_min_months': 1, 'fine_max_usd': 100})['id']
    with core.transaction() as c:
        c.execute("INSERT INTO category_fields VALUES('period','punishment_measure','Срок применения','number',NULL,0,1)")
        before = core.allrows(c, 'SELECT * FROM articles ORDER BY id')
    changes = [
        {'operation': 'amend', 'target': {'article_id': source}, 'article': {
            'number': '190', 'title': 'Новые санкции', 'text_rich': '<p>Содержание изменённой статьи.</p>',
            'categories': ['punishment_measure'], 'custom_values': {'period': 14},
            'sanctions': {'prison_min_months': 2, 'prison_max_months': 24, 'fine_min_usd': 50, 'fine_max_usd': 200},
            'relations': [{'type_id': 'accusation_punishment', 'source_id': target, 'target_id': source}]}},
        {'operation': 'add', 'target': {'code_id': 'demo-penal', 'after_article_id': source}, 'article': {
            'number': '191', 'title': 'Новая норма', 'text_rich': '<p>Содержание новой статьи.</p>'}},
        {'operation': 'repeal', 'target': {'article_id': 'demo-article-1'}, 'article': {}}
    ]
    bill_id = create(client, text_rich='<p>Преамбула для проверки.</p>', changes=changes)
    bill = client.get(API + '/bills/' + bill_id, headers=SENATOR).json()
    doc = bill['document_rich']
    assert doc.index('Преамбула для проверки.') < doc.index(ENACTING) < doc.index('Статья 1.') < doc.index('Статья 2.') < doc.index('Статья 3.')
    for phrase in ['Содержание изменённой статьи.', 'Содержание новой статьи.', 'Штраф, максимум USD: 200', 'Срок применения: 14', '§189']:
        assert phrase in doc
    assert bill['changes'][0]['article']['sanctions']['prison_max_months'] == 24
    assert bill['changes'][1]['target']['parent_id'] == 'demo-intro'
    with core.read_db() as c:
        assert core.allrows(c, 'SELECT * FROM articles ORDER BY id') == before
        assert core.one(c, "SELECT used_ever FROM category_fields WHERE id='period'")['used_ever'] == 0
        assert not core.allrows(c, 'SELECT * FROM article_relations')
    changes[0]['article']['annotations'] = []
    response = client.post(API + '/bills', headers=SENATOR, json={'title': 'Сноски', 'changes': changes})
    assert response.status_code == 422 and response.json()['error']['code'] == 'ANNOTATIONS_READ_ONLY'


def test_bulk_coauthors_keep_confirmation_and_have_no_twenty_person_limit(client):
    with core.transaction() as c:
        for i in range(25):
            c.execute('INSERT INTO senate_profiles(user_id,name) VALUES(?,?)', (f'author-{i}', f'Соавтор {i}'))
    ids = [f'author-{i}' for i in range(25)]
    bill_id = create(client, coauthor_ids=ids + ['demo-user'])
    assert len(client.get(API + '/bills/' + bill_id, headers=SENATOR).json()['coauthors']) == 26
    assert client.post(API + '/bills/' + bill_id + '/coauthors/respond', headers=LG, json={'accept': True}).status_code == 200
    response = client.put(API + '/bills/' + bill_id, headers=SENATOR, json={'base_version': 1, 'coauthor_ids': ids + ['demo-user']})
    assert response.status_code == 200, response.text
    bill = client.get(API + '/bills/' + bill_id, headers=SENATOR).json()
    assert next(c for c in bill['coauthors'] if c['user_id'] == 'demo-user')['status'] == 'accepted'
    assert client.put(API + '/bills/' + bill_id, headers=LG, json={'base_version': 2, 'coauthor_ids': []}).status_code == 403


def mixed_agenda(client):
    flag(client, 'demo-user', 'lieutenant_governor')
    bill_id = create(client)
    with core.transaction() as c:
        c.execute("UPDATE bills SET status='ready_plenary' WHERE id=?", (bill_id,))
    question = client.post(API + '/elections', headers=LG, json={}).json()
    now = datetime.now(timezone.utc)
    # IC meeting may be later than the OOC vote closure.
    response = client.post(API + '/sessions', headers=LG, json={
        'title': 'Общая повестка', 'starts_at': (now + timedelta(days=7)).isoformat(),
        'ends_at': (now + timedelta(hours=1)).isoformat(), 'bill_ids': [bill_id, question['bill_id']]})
    assert response.status_code == 200, response.text
    session_id = response.json()['id']
    published = client.post(API + '/sessions/' + session_id + '/publish', headers=LG)
    assert published.status_code == 200, published.text
    return session_id, bill_id, question['id']


def test_mixed_agenda_hides_results_and_ooc_deadline_in_all_public_exports(client):
    session_id, bill_id, election_id = mixed_agenda(client)
    url = API + '/sessions/' + session_id
    assert client.post(url + '/votes', headers=SENATOR, json={'bill_id': bill_id, 'choice': 'yes'}).status_code == 200
    assert client.post(API + '/elections/' + election_id + '/votes', headers=SENATOR, json={'candidate_id': 'demo-legislator'}).status_code == 200
    for who in ({}, LG, SENATOR, ADMIN):
        data = client.get(url, headers=who).json()
        assert data['results_hidden'] is True
        assert data['items'][0]['choices'] == [] and data['items'][0]['tally'] is None
        assert data['items'][1]['election']['choices'] == [] and data['items'][1]['election']['tally'] is None
        assert 'present' not in data['attendance']
    mine = client.get(url, headers=SENATOR).json()
    assert mine['items'][0]['my_choice'] == 'yes'
    assert mine['items'][1]['election']['my_choice'] == 'demo-legislator'
    for endpoint in (url, API + '/sessions', API + '/elections', API + '/public', API + '/elections/' + election_id,
                     API + '/bills/' + bill_id + '/export.json'):
        assert 'ends_at' not in client.get(endpoint).text
    exported = client.get(API + '/bills/' + bill_id + '/export.json').json()['law']['history']['votes'][0]
    assert exported['choices'] == [] and exported['tally'] is None
    for entries in (client.get(API + '/admin', headers=ADMIN).json()['audit'],
                    client.get('/api/v1/admin/audit', headers=ADMIN).json()):
        assert not any(r['action'] == 'senate.vote.cast' for r in entries)
    pdf = client.get(url + '/export.pdf')
    text = ' '.join(p.extract_text() for p in PdfReader(BytesIO(pdf.content)).pages).replace('\n', ' ')
    assert 'Результаты скрыты' in text and 'ООС' not in text
    with core.transaction() as c:
        c.execute("UPDATE senate_session_details SET ends_at='2000-01-01T00:00:00+00:00' WHERE session_id=?", (session_id,))
    closed = client.get(url).json()
    assert closed['status'] == 'closed'
    assert closed['attendance']['present'] == 1
    assert closed['items'][0]['result'] == 'adopted'
    assert closed['items'][1]['election']['winner_id'] == 'demo-legislator'
    assert len(closed['items'][1]['election']['choices']) == 1
    assert 'pro_tempore' in client.get(API + '/me', headers=SENATOR).json()['flags']


def test_absence_cascades_between_election_and_bill_and_recast_restores_presence(client):
    session_id, bill_id, election_id = mixed_agenda(client)
    url = API + '/sessions/' + session_id
    vote = API + '/elections/' + election_id + '/votes'
    assert client.post(url + '/votes', headers=SENATOR, json={'bill_id': bill_id, 'choice': 'yes'}).status_code == 200
    assert client.post(vote, headers=SENATOR, json={'candidate_id': 'absent'}).status_code == 200
    data = client.get(url, headers=SENATOR).json()
    assert data['my_absent'] is True and data['items'][0]['my_choice'] == 'absent'
    assert client.post(vote, headers=SENATOR, json={'candidate_id': 'demo-legislator'}).status_code == 200
    data = client.get(url, headers=SENATOR).json()
    assert data['my_absent'] is False and data['items'][0]['my_choice'] is None
    with core.transaction() as c:
        c.execute("UPDATE senate_session_details SET ends_at='2000-01-01T00:00:00+00:00' WHERE session_id=?", (session_id,))
    assert client.get(url).json()['attendance']['present'] == 1


def test_lieutenant_fallback_and_quorum_configuration(client):
    flag(client, 'demo-user', 'lieutenant_governor')
    assert client.get(API + '/me', headers=LG).json()['capabilities']['manage_sessions'] is True
    flag(client, 'demo-legislator', 'pro_tempore')
    assert client.get(API + '/me', headers=LG).json()['capabilities']['manage_sessions'] is False
    assert client.post(API + '/elections', headers=LG, json={}).status_code == 200
    assert client.post(API + '/elections', headers=SENATOR, json={}).status_code == 403
    now = datetime.now(timezone.utc)
    body = {'title': 'Повестка', 'starts_at': (now + timedelta(days=1)).isoformat(),
            'ends_at': (now + timedelta(hours=1)).isoformat(), 'bill_ids': ['anything']}
    assert client.post(API + '/sessions', headers=LG, json=body).status_code == 403
    assert client.post(API + '/sessions', headers=ADMIN, json={**body, 'quorum': 2}).status_code == 403


def test_committee_agenda_date_is_required_and_appears_in_calendar_feed(client):
    from tests.test_senate_core import committee_setup
    bill_id = create(client)
    committee_setup(client, bill_id)
    agendas = client.get(API + '/public').json()['committee_agendas']
    assert len(agendas) == 1
    assert agendas[0]['starts_at'] == '2027-01-01T12:00:00+00:00'
    assert agendas[0]['committee_id'] == 'rules'
