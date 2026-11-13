"""Regressions for current voting rights and immutable amendment sources."""
import pytest

from app import core
from tests.test_senate_core import draft, flag
from tests.test_senate_sessions import _ready_bill, _agenda, _publish
from tests.test_senate_officers import _election, _publish_question

API = '/api/v1/senate'
ADMIN = {'X-Demo-User': 'superadmin'}
SENATOR = {'X-Demo-User': 'legislator'}


@pytest.mark.parametrize(('role', 'permission'), [
    ('legislator', 'senator'), ('user', 'lieutenant_governor'),
])
def test_revoked_voter_cannot_change_ballots_or_restore_presence(client, role, permission):
    bill_id = _ready_bill(client)
    flag(client, 'demo-' + role, permission)
    session_id = _agenda(client, [bill_id])
    _publish(client, session_id)
    url = API + '/sessions/' + session_id
    headers = {'X-Demo-User': role}
    assert client.post(url + '/votes', headers=headers,
                       json={'bill_id': bill_id, 'choice': 'absent'}).status_code == 200
    flag(client, 'demo-' + role, permission, False)

    assert client.get(url, headers=headers).json()['can_vote'] is False
    for choice in ('yes', 'absent'):
        assert client.post(url + '/votes', headers=headers,
                           json={'bill_id': bill_id, 'choice': choice}).status_code == 403
    assert client.post(url + '/restore-presence', headers=headers).status_code == 403
    with core.read_db() as c:
        votes = core.allrows(c, '''SELECT vc.choice FROM vote_choices vc JOIN votes v ON v.id=vc.vote_id
                                  WHERE v.session_id=? AND vc.voter_id=?''', (session_id, 'demo-' + role))
    assert votes == [{'choice': 'absent'}]

    flag(client, 'demo-' + role, permission)
    assert client.get(url, headers=headers).json()['can_vote'] is True
    assert client.post(url + '/restore-presence', headers=headers).status_code == 200


def test_senator_granted_after_publication_does_not_join_frozen_electorate(client):
    bill_id = _ready_bill(client)
    session_id = _agenda(client, [bill_id])
    _publish(client, session_id)
    flag(client, 'demo-lawyer', 'senator')
    url = API + '/sessions/' + session_id
    headers = {'X-Demo-User': 'lawyer'}
    assert client.get(url, headers=headers).json()['can_vote'] is False
    assert client.post(url + '/votes', headers=headers,
                       json={'bill_id': bill_id, 'choice': 'yes'}).status_code == 403


def test_revoked_election_voter_cannot_use_either_ballot_endpoint(client):
    election = _election(client)
    _publish_question(client, election)
    url = API + '/elections/' + election['id']
    flag(client, 'demo-legislator', 'senator', False)
    assert client.get(url, headers=SENATOR).json()['can_vote'] is False
    assert client.post(url + '/votes', headers=SENATOR,
                       json={'candidate_id': 'demo-legislator'}).status_code == 403
    assert client.post(API + '/sessions/' + election['session_id'] + '/votes', headers=SENATOR,
                       json={'bill_id': election['bill_id'], 'choice': 'abstain'}).status_code == 403


def test_disabled_group_revokes_inherited_voting_permission(client):
    response = client.post(API + '/admin/flag-types', headers=ADMIN, json={
        'id': 'senator-group', 'name': 'Сенаторы', 'permissions': ['senator'],
    })
    assert response.status_code == 200, response.text
    flag(client, 'demo-lawyer', 'senator-group')
    bill_id = _ready_bill(client)
    session_id = _agenda(client, [bill_id])
    _publish(client, session_id)
    url = API + '/sessions/' + session_id
    headers = {'X-Demo-User': 'lawyer'}
    assert client.get(url, headers=headers).json()['can_vote'] is True
    response = client.post(API + '/admin/flag-types', headers=ADMIN, json={
        'id': 'senator-group', 'active': False,
    })
    assert response.status_code == 200, response.text
    assert client.get(url, headers=headers).json()['can_vote'] is False
    assert client.post(url + '/votes', headers=headers,
                       json={'bill_id': bill_id, 'choice': 'yes'}).status_code == 403


def revise_library_article(client, article_id='demo-article-1', **updates):
    url = '/api/v1/articles/' + article_id
    article = client.get(url, headers=ADMIN).json()
    lock = client.post(url + '/edit-lock', headers=ADMIN,
                       json={'editor_session_id': 'source-regression'}).json()
    response = client.post(url + '/revisions', headers=ADMIN, json={
        'editor_session_id': 'source-regression', 'lock_token': lock['token'],
        'base_revision_id': article['revision_id'], 'number': '1-B',
        'title': 'Обновлённая норма', 'text_rich': '<p>Новая действующая редакция.</p>', **updates,
    })
    assert response.status_code == 200, response.text
    return response.json()


def amendment():
    return {'operation': 'amend', 'target': {'article_id': 'demo-article-1'},
            'article': {'text_rich': '<p>Предложение автора проекта.</p>'}}


@pytest.mark.parametrize('legacy_payload', [False, True])
def test_saving_amendment_preserves_trusted_source_after_library_update(client, legacy_payload):
    bill_id = draft(client, changes=[amendment()])
    url = API + '/bills/' + bill_id
    before = client.get(url, headers=SENATOR).json()
    updated_article = revise_library_article(client)
    changes = client.get(url, headers=SENATOR).json()['changes']
    if legacy_payload:
        changes[0]['target'].pop('revision_id')
    changes[0]['source'] = {'text_rich': 'Untrusted client snapshot'}
    response = client.put(url, headers=SENATOR, json={
        'base_version': before['version'], 'title': 'Уточнённый заголовок', 'changes': changes,
    })
    assert response.status_code == 200, response.text
    after = client.get(url, headers=SENATOR).json()
    assert after['changes'] == before['changes']
    assert after['changes'][0]['target']['revision_id'] != updated_article['revision_id']
    assert after['revisions'][1]['version'] == before['version']
    exported = client.get(url + '/export.json', headers=SENATOR).json()
    assert exported['law']['changes'] == before['changes']


def test_new_amendment_uses_the_revision_selected_before_library_update(client):
    selected = client.get(API + '/library', headers=SENATOR).json()['articles'][0]
    revise_library_article(client)
    change = amendment()
    change['target']['revision_id'] = selected['revision_id']
    bill_id = draft(client, changes=[change])
    saved = client.get(API + '/bills/' + bill_id, headers=SENATOR).json()['changes'][0]
    assert saved['target']['revision_id'] == selected['revision_id']
    assert saved['source']['number'] == selected['number']
    assert saved['source']['title'] == selected['title']
    assert saved['source']['text_rich'] == selected['text_rich']
    assert saved['source']['sanctions'] == selected['sanctions']


def test_explicit_selection_of_new_revision_updates_source_from_server(client):
    bill_id = draft(client, changes=[amendment()])
    url = API + '/bills/' + bill_id
    before = client.get(url, headers=SENATOR).json()
    updated_article = revise_library_article(client)
    changes = before['changes']
    changes[0]['target']['revision_id'] = updated_article['revision_id']
    changes[0]['source'] = {'text_rich': 'Untrusted client snapshot'}
    response = client.put(url, headers=SENATOR,
                          json={'base_version': before['version'], 'changes': changes})
    assert response.status_code == 200, response.text
    saved = client.get(url, headers=SENATOR).json()['changes'][0]
    assert saved['target']['revision_id'] == updated_article['revision_id']
    assert saved['source']['text_rich'] == updated_article['text_rich']


def test_historical_source_preserves_selected_sanctions(client, admin):
    selected = admin('208', cats=['punishment_measure'],
                     sanctions={'prison_min_months': 1, 'prison_max_months': 2})
    revised = revise_library_article(client, selected['id'],
                                    sanctions={'prison_min_months': 10, 'prison_max_months': 20})
    change = {'operation': 'amend',
              'target': {'article_id': selected['id'], 'revision_id': selected['revision_id']},
              'article': {'text_rich': '<p>Поправка к выбранной мере наказания.</p>'}}
    bill_id = draft(client, changes=[change])
    saved = client.get(API + '/bills/' + bill_id, headers=SENATOR).json()['changes'][0]
    assert saved['source']['sanctions'] == selected['sanctions']
    assert saved['source']['prison_min_months'] == 1
    assert saved['article']['sanctions'] == selected['sanctions']
    assert revised['sanctions']['prison_min_months'] == 10


def test_source_revision_cannot_belong_to_another_article(client, admin):
    other = admin('209')
    change = amendment()
    change['target']['revision_id'] = other['revision_id']
    response = client.post(API + '/bills', headers=SENATOR,
                           json={'title': 'Некорректная исходная редакция', 'changes': [change]})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'INVALID_SOURCE_REVISION'
    assert client.get(API + '/bills?mine=true', headers=SENATOR).json() == []


def test_preserved_source_keeps_historical_code_alias_while_target_uses_current_code(client):
    bill_id = draft(client, changes=[amendment()])
    url = API + '/bills/' + bill_id
    before = client.get(url, headers=SENATOR).json()
    changes = before['changes']
    changes[0]['source']['code_id'] = 'legacy-penal'
    changes[0]['target']['code_id'] = 'legacy-penal'
    with core.transaction() as c:
        c.execute('INSERT INTO code_id_aliases VALUES(?,?)', ('legacy-penal', 'PEN'))
        c.execute('UPDATE senate_bill_meta SET changes_json=? WHERE bill_id=?',
                  (core.dumps(changes), bill_id))
    response = client.put(url, headers=SENATOR,
                          json={'base_version': before['version'], 'changes': changes})
    assert response.status_code == 200, response.text
    saved = client.get(url, headers=SENATOR).json()['changes'][0]
    assert saved['source'] == changes[0]['source']
    assert saved['target']['code_id'] == 'PEN'


@pytest.mark.parametrize('invalid_revision', ['missing-revision', ['invalid-type']])
def test_invalid_source_revision_rejected_without_creating_bill_version(client, invalid_revision):
    bill_id = draft(client, changes=[amendment()])
    url = API + '/bills/' + bill_id
    before = client.get(url, headers=SENATOR).json()
    changes = before['changes']
    changes[0]['target']['revision_id'] = invalid_revision
    response = client.put(url, headers=SENATOR,
                          json={'base_version': before['version'], 'changes': changes})
    assert response.status_code == 422, response.text
    assert client.get(url, headers=SENATOR).json()['version'] == before['version']
