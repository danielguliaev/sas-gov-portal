"""Acceptance checks for document identity, authorship, resolutions and meeting lifecycle."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import pytest
from pypdf import PdfReader
from app import core
from app.code_identity import migrate_code_ids
from app.senate_document import ENACTING, RESOLUTIONS
from tests.test_senate_core import draft, flag, committee_setup

API = '/api/v1/senate'
SENATOR = {'X-Demo-User':'legislator'}
ADMIN = {'X-Demo-User':'superadmin'}
READER = {'X-Demo-User':'user'}


def dates():
    now = datetime.now(timezone.utc)
    return {'starts_at': (now + timedelta(days=2)).isoformat(), 'ends_at': (now + timedelta(days=1)).isoformat()}


def post(client, path, body, headers=ADMIN):
    response = client.post(API + path, json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def finish(session_id):
    with core.transaction() as c:
        c.execute("UPDATE senate_session_details SET ends_at='2000-01-01T00:00:00+00:00' WHERE session_id=?", (session_id,))


def test_abbreviation_identity_migration_preserves_foreign_keys_and_old_snapshots(client, admin):
    with core.transaction() as c:
        c.execute("INSERT INTO codes(id,jurisdiction_id,name,abbreviation) VALUES('legacy-civil','state','Civil Code','CIV')")
        c.execute("INSERT INTO articles(id,code_id,number,number_key,sort_key,title,created_at,updated_at) VALUES('civil-1','legacy-civil','1','1','1','Гражданское право',?,?)", (core.ts(),core.ts()))
        c.execute("INSERT INTO sort_modes VALUES('legacy-civil:ROOT:x','manual')")
        migrate_code_ids(c)
        migrate_code_ids(c)
        assert core.one(c, "SELECT code_id FROM articles WHERE id='civil-1'")['code_id'] == 'CIV'
        assert core.one(c, "SELECT code_id FROM code_outline WHERE item_id='civil-1'")['code_id'] == 'CIV'
        assert core.one(c, "SELECT code_id FROM code_id_aliases WHERE old_id='legacy-civil'")['code_id'] == 'CIV'
        assert core.maybe(c, "SELECT 1 FROM sort_modes WHERE parent_key='CIV:ROOT:x'")
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    bill_id = draft(client, changes=[{'operation':'add','target':{'code_id':'legacy-civil'},'article':{'number':'2','title':'Новая норма','text_rich':'<p>Текст</p>'}}])
    doc = client.get(API+'/bills/'+bill_id, headers=SENATOR).json()
    assert 'Civil Code' in doc['document_rich'] and 'legacy-civil' not in doc['document_rich']
    assert doc['changes'][0]['target']['code_id'] == 'CIV'


def test_new_code_id_comes_from_abbreviation_and_collision_is_rejected(client):
    body = {'name':'Commercial Code','abbreviation':'com','jurisdiction_id':'state'}
    response = client.post('/api/v1/codes',headers=ADMIN,json=body)
    assert response.status_code == 200, response.text
    assert response.json()['id'] == 'COM'
    assert client.post('/api/v1/codes',headers=ADMIN,json={**body,'name':'Another Code'}).status_code == 409


def test_only_senators_are_selectable_and_document_authors_are_separate_from_creator(client):
    assert [r['id'] for r in client.get(API+'/coauthor-candidates',headers=SENATOR).json()] == ['demo-legislator']
    assert client.post(API+'/bills',headers=SENATOR,json={'title':'Проверка','coauthor_ids':['demo-user']}).status_code == 422
    flag(client,'demo-user','senator')
    flag(client,'demo-lawyer','senator')
    client.put(API+'/profile',headers=SENATOR,json={'name':'Мария Сенатор'})
    client.put(API+'/profile',headers=READER,json={'name':'Иван Автор'})
    post(client,'/admin/profiles/demo-lawyer',{'name':'Анна Соавтор','reason':'Настройка профиля для теста'})
    bill_id = draft(client,author_ids=['demo-user','demo-legislator'],coauthor_ids=['demo-lawyer'])
    bill = client.get(API+'/bills/'+bill_id,headers=SENATOR).json()
    assert bill['author_id'] == 'demo-legislator'
    assert bill['author_ids'] == ['demo-user','demo-legislator']
    assert 'Представлено Иван Автор, Мария Сенатор' in bill['document_rich']
    assert 'Соавторы: Анна Соавтор' in bill['document_rich']
    assert bill['events'][0]['actor_name'] == 'Мария Сенатор'
    assert client.get(API+'/bills/'+bill_id, headers=READER).status_code == 200
    assert client.get(API+'/bills?mine=true',headers=READER).json()[0]['id'] == bill_id
    assert client.get(API+'/bills?author=Иван',headers=READER).json()[0]['id'] == bill_id
    exported = client.get(API+'/bills/'+bill_id+'/export.json',headers=SENATOR).json()['law']
    assert exported['authors'] == bill['author_ids'] and exported['created_by'] == 'demo-legislator'
    assert client.put(API+'/bills/'+bill_id,headers=SENATOR,json={'base_version':1,'author_ids':['demo-lawyer'],'coauthor_ids':['demo-lawyer']}).status_code == 422


@pytest.mark.parametrize('kind', list(RESOLUTIONS))
def test_resolutions_have_two_blocks_and_no_statutory_amendments(client,kind):
    preamble = '<p>ПРИНИМАЯ ВО ВНИМАНИЕ, что первая причина;</p><p><br></p><p>вторая причина.</p>'
    operative = '<p>'+RESOLUTIONS[kind][1]+'</p><p>РЕШЕНО, поддержать инициативу.</p>'
    bill_id = draft(client,document_type=kind,text_rich=preamble,resolution_text_rich=operative)
    bill = client.get(API+'/bills/'+bill_id,headers=SENATOR).json()
    html = bill['document_rich']
    assert RESOLUTIONS[kind][0] in html
    assert html.index('первая причина') < html.index('вторая причина') < html.index('поддержать инициативу')
    assert '<p><br></p>' in html
    assert ENACTING not in html and 'неотложным законом' not in html
    response = client.put(API+'/bills/'+bill_id,headers=SENATOR,json={'base_version':1,'changes':[{'operation':'repeal','target':{'article_id':'demo-article-1'}}]})
    assert response.status_code == 422
    text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(client.get(API+'/bills/'+bill_id+'/export.pdf',headers=SENATOR).content)).pages)
    assert RESOLUTIONS[kind][0] in text and 'поддержать инициативу' in text


def test_header_attributes_determine_actual_vote_rule_and_preserve_empty_lines(client):
    post(client,'/admin/categories',{'id':'general','name':'Общие вопросы','plenary_rule':'simple_majority','plenary_threshold':2})
    preamble = '<p>Первый абзац.</p><p><br></p><p><br></p><p>Последний абзац.</p>'
    bill_id = draft(client,text_rich=preamble,vote_rule='absolute_majority',appropriation=True,fiscal_committee=True,local_project=False)
    bill = client.get(API+'/bills/'+bill_id,headers=SENATOR).json()
    assert 'Голосование: Абсолютное большинство' in bill['document_rich']
    assert 'Ассигнование: ДА' in bill['document_rich'] and 'Фискальный комитет: ДА' in bill['document_rich']
    assert '<p><br></p><p><br></p>' in bill['document_rich']
    with core.transaction() as c:
        c.execute("UPDATE bills SET status='ready_plenary' WHERE id=?",(bill_id,))
    agenda = post(client,'/sessions',{'title':'Голосование по выбранному правилу','bill_ids':[bill_id],**dates()})
    result = post(client,'/sessions/'+agenda['id']+'/publish',{})
    assert result['items'][0]['rule'] == 'absolute_majority' and result['items'][0]['threshold'] == 2


def test_history_describes_decisions_and_names_without_raw_json(client):
    client.put(API+'/profile',headers=SENATOR,json={'name':'Мария Сенатор'})
    bill_id = draft(client)
    committee_setup(client,bill_id)
    bill = client.get(API+'/bills/'+bill_id).json()
    assert bill['revisions'][0]['author_name'] == 'Мария Сенатор'
    for event in bill['events']:
        assert event['title'] and event['description']
        assert 'review_id' not in event['description'] and 'committee_id' not in event['description']
        assert not event['description'].startswith('{')
    report = next(e for e in bill['events'] if e['event_type']=='consultant_report')
    assert 'Рекомендовать к принятию' in report['description']
    submitted = next(e for e in bill['events'] if e['event_type']=='submitted')
    assert 'Rules Committee' in submitted['description'] and submitted['actor_name']=='Мария Сенатор'


def test_first_unwrapped_editor_line_survives_on_first_pdf_page(client):
    bill_id=draft(client,text_rich='Первая строка без контейнера.<div><br></div><div>Последняя строка.</div>')
    bill=client.get(API+'/bills/'+bill_id,headers=SENATOR).json()
    assert '<p>Первая строка без контейнера.</p>' in bill['text_rich']
    response=client.get(API+'/bills/'+bill_id+'/export.pdf',headers=SENATOR)
    first=PdfReader(BytesIO(response.content)).pages[0].extract_text()
    assert 'Первая строка без контейнера.' in first and 'Последняя строка.' in first


def test_discussion_only_plenary_has_venue_and_no_ballots(client):
    agenda=post(client,'/sessions',{'title':'Обсуждение бюджета','venue':'Капитолий\nЗал 113','discussions':[{'title':'Бюджетные предложения','description':'Открытое обсуждение.'}],**dates()})
    session_id=agenda['id']
    published=post(client,'/sessions/'+session_id+'/publish',{})
    assert published['items']==[] and len(published['discussions'])==1
    assert published['venue']=='Капитолий\nЗал 113'
    catalog=client.get(API+'/agendas').json()
    assert catalog[0]['venue']==published['venue'] and 'ends_at' not in catalog[0]
    assert client.post(API+'/sessions/'+session_id+'/votes',headers=SENATOR,json={'bill_id':published['discussions'][0]['id'],'choice':'yes'}).status_code==404
    finish(session_id)
    assert client.get(API+'/sessions/'+session_id).json()['status']=='closed'


def test_reschedule_clears_ballots_preserves_round_and_cancel_never_adopts(client):
    bill_id=draft(client)
    with core.transaction() as c:
        c.execute("UPDATE bills SET status='ready_plenary' WHERE id=?",(bill_id,))
    agenda=post(client,'/sessions',{'title':'Повестка с переносом','venue':'Зал 1','bill_ids':[bill_id],**dates()})
    session_id=agenda['id']
    post(client,'/sessions/'+session_id+'/publish',{})
    post(client,'/sessions/'+session_id+'/votes',{'bill_id':bill_id,'choice':'yes'},SENATOR)
    payload={'action':'reschedule','reason':'Изменено расписание','venue':'Зал 2',**dates()}
    assert client.post(API+'/agendas/plenary/'+session_id+'/schedule',headers=READER,json=payload).status_code==403
    post(client,'/agendas/plenary/'+session_id+'/schedule',payload)
    row=client.get(API+'/sessions/'+session_id,headers=SENATOR).json()
    assert row['voting_round']==2 and row['items'][0]['my_choice'] is None
    assert row['schedule_status']=='postponed' and row['venue']=='Зал 2'
    assert 'choices' not in row['previous_rounds'][0]
    with core.read_db() as c:
        snapshot=core.loads(core.one(c,'SELECT snapshot_json FROM senate_voting_rounds WHERE session_id=?',(session_id,))['snapshot_json'])
        assert snapshot['choices'][0]['choice']=='yes'
        assert not core.allrows(c,'SELECT * FROM vote_choices')
    post(client,'/agendas/plenary/'+session_id+'/schedule',{'action':'cancel','reason':'Отмена заседания'})
    assert client.get(API+'/sessions/'+session_id).json()['status']=='cancelled'
    assert client.get(API+'/bills/'+bill_id).json()['status']=='ready_plenary'
    assert client.post(API+'/sessions/'+session_id+'/votes',headers=SENATOR,json={'bill_id':bill_id,'choice':'yes'}).status_code==409
    with core.read_db() as c:
        assert not core.allrows(c,'SELECT * FROM adopted_acts')


def test_committee_free_subject_cancellation_and_reuse_of_report(client):
    bill_id=draft(client)
    committee_setup(client,bill_id)
    agenda=client.get(API+'/committees/rules').json()['agendas'][0]
    post(client,'/agendas/committee/'+agenda['id']+'/schedule',{'action':'cancel','reason':'Новое расписание'},SENATOR)
    review=client.get(API+'/bills/'+bill_id).json()['reviews'][0]
    new=post(client,'/committees/rules/agenda',{'title':'Новое совещание','starts_at':dates()['starts_at'],'venue':'Капитолий, зал 2','review_ids':[review['id']],
        'discussions':[{'title':'Свободный вопрос','description':'Обсудить порядок работы.'}]},SENATOR)
    row=client.get(API+'/agendas/committee/'+new['id']).json()
    assert row['items'][0]['bill_id']==bill_id and row['discussions'][0]['title']=='Свободный вопрос'
    assert 'ends_at' not in row
    assert len(client.get(API+'/agendas').json())==2
