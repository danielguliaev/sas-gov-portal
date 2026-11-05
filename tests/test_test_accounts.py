import app.core as core

ADMIN = {'X-Demo-User': 'superadmin'}
BASE = '/api/v1/test-accounts'


def test_seed_twenty_accounts_is_idempotent_and_preserves_edits(client):
    from app.test_accounts import seed
    data = client.get(BASE, headers=ADMIN).json()
    assert len(data['accounts']) == 20
    assert all(len(a['characters']) == 2 for a in data['accounts'])
    client.put(BASE+'/test-01', headers=ADMIN, json={'name':'Изменён', 'active':False,'roles':['user']})
    seed()
    account = client.get(BASE, headers=ADMIN).json()['accounts'][0]
    assert account['name'] == 'Изменён' and account['active'] == 0


def test_account_panel_denies_reader_and_working_mode(client, monkeypatch):
    assert client.get(BASE, headers={'X-Demo-User':'user'}).status_code == 403
    assert client.put(BASE+'/test-01', headers={'X-Demo-User':'user'}, json={}).status_code == 403
    monkeypatch.setattr(core,'APP_ENV','production')
    assert client.get('/accounts').status_code == 404
    assert client.get(BASE+'/choices').status_code == 404
    monkeypatch.setattr(core,'APP_ENV','development')
    monkeypatch.setattr(core,'AUTH_MODE','oidc')
    assert client.get(BASE+'/choices').status_code == 404


def test_character_permissions_authorship_and_ownership(client):
    first='test-01-character-1'
    second='test-01-character-2'
    first_auth={'X-Demo-User':'test-01@'+first}
    second_auth={'X-Demo-User':'test-01@'+second}
    assert client.get('/api/v1/senate/me', headers={'X-Demo-User':'test-02@'+first}).status_code == 401
    assert client.post('/api/v1/senate/bills', headers=first_auth,json={}).status_code == 403
    assert client.put(BASE+'/characters/'+first+'/flags', headers=ADMIN,json={'flags':['senator']}).status_code == 200
    response=client.post('/api/v1/senate/bills', headers=first_auth,json={'title':'Первый персонаж','text_rich':'<p>Проект</p>'})
    assert response.status_code == 200, response.text
    bill_id=response.json()['id']
    assert client.get('/api/v1/senate/bills/'+bill_id,headers=second_auth).status_code == 404
    assert not client.get('/api/v1/senate/me',headers=second_auth).json()['capabilities']['create_bill']
    client.put(BASE+'/characters/'+first+'/profile',headers=ADMIN,json={'name':'Новое имя'})
    assert client.get('/api/v1/senate/me',headers=first_auth).json()['profile']['name']=='Новое имя'
    client.put(BASE+'/test-01',headers=ADMIN,json={'name':'Аккаунт','roles':['lawyer'],'active':True})
    assert 'lawyer' in client.get('/api/v1/senate/me',headers=second_auth).json()['roles']
    client.put(BASE+'/test-01',headers=ADMIN,json={'name':'Аккаунт','roles':['user'],'active':False})
    assert client.get('/api/v1/senate/me',headers=first_auth).status_code == 401
    with core.read_db() as c:
        assert core.one(c,'SELECT author_id FROM bills WHERE id=?',(bill_id,))['author_id']==first
        assert 'account=test-01' in core.one(c,"SELECT source FROM audit_events WHERE action='senate.bill.create' AND object_id=?",(bill_id,))['source']


def test_new_character_and_group_membership(client):
    group=client.post('/api/v1/senate/admin/flag-types',headers=ADMIN,json={'name':'Консультанты','permissions':['committee:rules']}).json()['id']
    character=client.post(BASE+'/test-02/characters',headers=ADMIN,json={'name':'Дополнительный персонаж'}).json()['id']
    assert client.put(BASE+'/characters/'+character+'/flags',headers=ADMIN,json={'flags':[group]}).status_code==200
    me=client.get('/api/v1/senate/me',headers={'X-Demo-User':'test-02@'+character}).json()
    assert 'committee:rules' in me['flags']
    assert client.put(BASE+'/characters/'+character+'/flags',headers=ADMIN,json={'flags':['not-existing']}).status_code==422
    assert client.get(BASE,headers=ADMIN).json()['audit']
