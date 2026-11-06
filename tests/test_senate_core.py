"""Executable acceptance checks for Senate privacy, revisions and committee routing."""
import app.core as core

BASE = '/api/v1/senate'
SENATOR = {'X-Demo-User': 'legislator'}
ADMIN = {'X-Demo-User': 'superadmin'}
READER = {'X-Demo-User': 'user'}


def draft(client, **extra):
    body = {'title': 'О порядке опубликования актов', 'text_rich': '<p>Статья 1. Порядок опубликования.</p>',
            'effective_at': '2026-12-01', **extra}
    response = client.post(BASE + '/bills', json=body, headers=SENATOR)
    assert response.status_code == 200, response.text
    return response.json()['id']


def flag(client, user_id, name, active=True):
    response = client.post(BASE + '/admin/flags', json={'user_id': user_id, 'flag': name, 'active': active}, headers=ADMIN)
    assert response.status_code == 200, response.text


def committee_setup(client, bill_id):
    flag(client, 'demo-user', 'committee:rules')
    flag(client, 'demo-legislator', 'committee:rules')
    flag(client, 'demo-legislator', 'chair:rules')
    response = client.post(BASE + f'/bills/{bill_id}/submit', headers=SENATOR)
    assert response.status_code == 200, response.text
    response = client.post(BASE + '/committees/rules/reports', headers=READER,
                           json={'bill_id': bill_id, 'text': 'Соответствует требованиям.', 'recommendation': 'do_pass'})
    assert response.status_code == 200, response.text
    review = client.get(BASE + f'/bills/{bill_id}').json()['reviews'][-1]
    response = client.post(BASE + '/committees/rules/agenda', headers=SENATOR,
                           json={'title': 'Заседание Rules Committee', 'starts_at': '2027-01-01T12:00:00Z', 'review_ids': [review['id']]})
    assert response.status_code == 200, response.text


def decision(client, bill_id, kind='do_pass'):
    return client.post(BASE + '/committees/rules/decisions', headers=SENATOR,
                       json={'bill_id': bill_id, 'decision': kind, 'transcript': 'Совещание комитета состоялось.',
                             'tally': {'yes': 1, 'no': 0, 'abstain': 0}})


def test_guest_and_reader_cannot_see_draft_or_create(client):
    bill_id = draft(client)
    for headers in ({}, READER):
        assert client.get(BASE + f'/bills/{bill_id}', headers=headers).status_code == 404
        assert client.get(BASE + f'/bills/{bill_id}/export.json', headers=headers).status_code == 404
        assert client.get(BASE + '/bills', headers=headers).json() == []
        assert client.get(BASE + '/public', headers=headers).json()['bills'] == []
    assert client.post(BASE + '/bills', headers=READER, json={'title': 'Чужой проект'}).status_code == 403
    assert client.post(BASE + '/bills', headers=ADMIN, json={'title': 'Проект администратора'}).status_code == 403


def test_revisions_are_immutable_and_optimistic(client):
    bill_id = draft(client)
    response = client.put(BASE + f'/bills/{bill_id}', headers=SENATOR,
                          json={'base_version': 1, 'title': 'Новое название', 'text_rich': '<p>Новая редакция.</p>'})
    assert response.json()['version'] == 2
    assert client.put(BASE + f'/bills/{bill_id}', headers=SENATOR,
                      json={'base_version': 1, 'text_rich': '<p>Потерянная правка.</p>'}).status_code == 409
    bill = client.get(BASE + f'/bills/{bill_id}', headers=SENATOR).json()
    assert [r['version'] for r in bill['revisions']] == [2, 1]
    assert 'Порядок опубликования' in bill['revisions'][1]['text_rich']
    assert bill['title'] == 'Новое название'


def test_coauthors_receive_access_only_after_confirmation(client):
    bill_id = draft(client)
    assert client.post(BASE + f'/bills/{bill_id}/coauthors', json={'user_id': 'demo-user'}, headers=SENATOR).status_code == 200
    assert client.get(BASE + f'/bills/{bill_id}', headers=READER).status_code == 404
    assert client.get(BASE + '/me', headers=READER).json()['invitations'][0]['bill_id'] == bill_id
    assert client.post(BASE + f'/bills/{bill_id}/coauthors/respond', json={'accept': True}, headers=READER).status_code == 200
    assert client.get(BASE + f'/bills/{bill_id}', headers=READER).status_code == 200
    assert client.put(BASE + f'/bills/{bill_id}', json={'base_version': 1, 'title': 'Правка соавтора'}, headers=READER).status_code == 200
    assert client.post(BASE + f'/bills/{bill_id}/submit', headers=READER).status_code == 403


def test_submission_routes_to_rules_and_freezes_revision(client):
    bill_id = draft(client)
    assert client.post(BASE + f'/bills/{bill_id}/submit', headers=SENATOR).status_code == 200
    bill = client.get(BASE + f'/bills/{bill_id}').json()
    assert bill['status'] == 'committee_review'
    assert bill['current_committee_id'] == 'rules'
    assert bill['reviews'][0]['version'] == 1
    assert client.put(BASE + f'/bills/{bill_id}', headers=SENATOR, json={'base_version': 1}).status_code == 409
    assert client.post(BASE + f'/bills/{bill_id}/submit', headers=SENATOR).status_code == 409


def test_committee_report_and_chair_decision_are_distinct(client):
    bill_id = draft(client)
    committee_setup(client, bill_id)
    bill = client.get(BASE + f'/bills/{bill_id}').json()
    assert bill['reviews'][0]['status'] == 'agenda'
    assert bill['reviews'][0]['consideration_due_at']
    assert client.post(BASE + '/committees/rules/decisions', headers=READER,
                       json={'bill_id': bill_id, 'decision': 'do_pass'}).status_code == 403
    response = decision(client, bill_id)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'ready_plenary'
    assert client.get(BASE + f'/bills/{bill_id}').json()['reviews'][0]['report_by'] == 'demo-user'


def test_not_pass_is_final_but_held_supports_new_cycle(client):
    rejected = draft(client)
    committee_setup(client, rejected)
    assert decision(client, rejected, 'not_pass').json()['status'] == 'committee_rejected'
    assert client.put(BASE + f'/bills/{rejected}', headers=SENATOR, json={'base_version': 1}).status_code == 409
    assert client.post(BASE + f'/bills/{rejected}/submit', headers=SENATOR).status_code == 409
    held = draft(client)
    committee_setup(client, held)
    assert decision(client, held, 'held').json()['status'] == 'held'
    assert client.put(BASE + f'/bills/{held}', headers=SENATOR, json={'base_version': 1, 'title': 'Доработанный проект'}).status_code == 200
    assert client.post(BASE + f'/bills/{held}/submit', headers=SENATOR).status_code == 200
    reviews = client.get(BASE + f'/bills/{held}').json()['reviews']
    assert [r['cycle'] for r in reviews] == [1, 2]
    assert reviews[1]['version'] == 2


def test_both_committee_deadlines_advance_without_agenda(client):
    no_report = draft(client)
    client.post(BASE + f'/bills/{no_report}/submit', headers=SENATOR)
    with core.transaction() as c:
        c.execute("UPDATE senate_reviews SET due_at='2000-01-01T00:00:00+00:00' WHERE bill_id=?", (no_report,))
    assert client.get(BASE + f'/bills/{no_report}').json()['status'] == 'ready_plenary'
    no_decision = draft(client)
    committee_setup(client, no_decision)
    with core.transaction() as c:
        c.execute("UPDATE senate_reviews SET consideration_due_at='2000-01-01T00:00:00+00:00' WHERE bill_id=?", (no_decision,))
    bill = client.get(BASE + f'/bills/{no_decision}').json()
    assert bill['status'] == 'ready_plenary'
    assert bill['votes'] == []


def test_library_changes_snapshot_do_not_modify_library(client):
    with core.read_db() as c:
        before = core.one(c, "SELECT text_rich,revision_id FROM articles WHERE id='demo-article-1'")
    bill_id = draft(client, changes=[{'operation': 'amend', 'target': {'article_id': 'demo-article-1'},
                                     'article': {'text_rich': '<p>Предлагаемый новый текст.</p>'}}])
    bill = client.get(BASE + f'/bills/{bill_id}', headers=SENATOR).json()
    assert bill['changes'][0]['target']['revision_id'] == before['revision_id']
    assert bill['changes'][0]['source']['text_rich'] == before['text_rich']
    with core.read_db() as c:
        assert core.one(c, "SELECT text_rich,revision_id FROM articles WHERE id='demo-article-1'") == before


def test_export_has_all_versions_and_unicode_pdf(client):
    bill_id = draft(client)
    client.put(BASE + f'/bills/{bill_id}', headers=SENATOR, json={'base_version': 1, 'text_rich': '<p>Вторая редакция.</p>'})
    export = client.get(BASE + f'/bills/{bill_id}/export.json', headers=SENATOR)
    assert export.status_code == 200
    assert export.json()['format_version'] == '1.0'
    assert len(export.json()['law']['history']['versions']) == 2
    pdf = client.get(BASE + f'/bills/{bill_id}/export.pdf', headers=SENATOR)
    assert pdf.status_code == 200, pdf.text
    assert pdf.content.startswith(b'%PDF')
    assert client.get(BASE + f'/bills/{bill_id}', headers=SENATOR).json()['status'] == 'draft'


def test_admin_settings_and_flags_persist_and_audit(client):
    assert client.put(BASE + '/admin/settings', headers=READER, json={'quorum': 3}).status_code == 403
    response = client.put(BASE + '/admin/settings', headers=ADMIN, json={'committee_review_days': 5, 'consideration_days': 8, 'quorum': 2})
    assert response.status_code == 200
    flag(client, 'demo-user', 'senator')
    assert client.get(BASE + '/me', headers=READER).json()['capabilities']['create_bill'] is True
    flag(client, 'demo-legislator', 'senator', False)
    assert client.post(BASE + '/bills', headers=SENATOR, json={'title': 'Проект'}).status_code == 403
    admin = client.get(BASE + '/admin', headers=ADMIN).json()
    assert admin['settings']['consideration_days'] == 8
    assert any(r['action'] == 'senate.flags.assign' for r in admin['audit'])


def test_disabled_category_is_retained_in_history(client):
    bill_id = draft(client)
    assert client.post(BASE + '/admin/categories', headers=ADMIN, json={'id': 'general', 'active': False}).status_code == 200
    assert client.get(BASE + f'/bills/{bill_id}', headers=SENATOR).json()['category_id'] == 'general'
    assert client.post(BASE + '/bills', headers=SENATOR, json={'title': 'Новый', 'text_rich': '<p>Текст</p>'}).status_code == 404


def test_committee_agenda_requires_report_and_chair(client):
    bill_id = draft(client)
    flag(client, 'demo-legislator', 'committee:rules')
    flag(client, 'demo-legislator', 'chair:rules')
    client.post(BASE + f'/bills/{bill_id}/submit', headers=SENATOR)
    review = client.get(BASE + f'/bills/{bill_id}').json()['reviews'][0]
    assert client.post(BASE + '/committees/rules/agenda', headers=SENATOR,
                       json={'starts_at': '2027-01-01T12:00:00Z', 'review_ids': [review['id']]}).status_code == 404
    assert client.post(BASE + '/committees/rules/agenda', headers=ADMIN,
                       json={'starts_at': '2027-01-01T12:00:00Z', 'review_ids': [review['id']]}).status_code == 403
    assert decision(client, bill_id).status_code == 404


def test_admin_correction_preserves_original_version_and_reason(client):
    bill_id = draft(client)
    client.post(BASE + f'/bills/{bill_id}/submit', headers=SENATOR)
    assert client.post(BASE + f'/admin/bills/{bill_id}/correct', headers=ADMIN,
                       json={'base_version': 1, 'title': 'Исправлено'}).status_code == 422
    response = client.post(BASE + f'/admin/bills/{bill_id}/correct', headers=ADMIN,
                           json={'base_version': 1, 'title': 'Исправлено', 'reason': 'Опечатка в названии'})
    assert response.status_code == 200, response.text
    bill = client.get(BASE + f'/bills/{bill_id}').json()
    assert bill['version'] == 2
    assert bill['revisions'][1]['metadata']['title'] == 'О порядке опубликования актов'
    assert 'Опечатка в названии' in bill['events'][-1]['body']
    admin = client.get(BASE + '/admin', headers=ADMIN).json()
    assert any(r['action'] == 'senate.bill.correct' for r in admin['audit'])


def test_search_filters_author_confirmed_coauthor_and_dates(client):
    from datetime import datetime, timedelta, timezone
    bill_id = draft(client)
    client.put(BASE + '/profile', headers=SENATOR, json={'name': 'Мария Сенатор'})
    client.post(BASE + f'/bills/{bill_id}/coauthors', headers=SENATOR, json={'user_id': 'demo-user'})
    assert client.get(BASE + '/bills?coauthor=demo-user', headers=SENATOR).json() == []
    client.post(BASE + f'/bills/{bill_id}/coauthors/respond', headers=READER, json={'accept': True})
    client.post(BASE + f'/bills/{bill_id}/submit', headers=SENATOR)
    for filters in ({'author': 'мария'}, {'coauthor': 'demo-user'},
                    {'date_from': datetime.now(timezone.utc).date().isoformat()}):
        rows = client.get(BASE + '/bills', params=filters).json()
        assert [row['id'] for row in rows] == [bill_id]
    assert client.get(BASE + '/bills?author=другой').json() == []
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    assert client.get(BASE + '/bills', params={'date_from': tomorrow}).json() == []
    assert client.get(BASE + '/bills?date_from=2026-02-30').status_code == 422
    assert client.get(BASE + '/bills?date_from=2026-12-01&date_to=2026-01-01').status_code == 422


def test_bill_pdf_has_readable_history_and_user_timezone(client):
    from io import BytesIO
    from pypdf import PdfReader
    bill_id = draft(client)
    client.post(BASE + f'/bills/{bill_id}/submit', headers=SENATOR)
    with core.transaction() as c:
        c.execute("UPDATE bill_revisions SET created_at='2026-07-01T12:00:00+00:00' WHERE bill_id=?", (bill_id,))
    response = client.get(BASE + f'/bills/{bill_id}/export.pdf?time_zone=Europe/Berlin')
    assert response.status_code == 200, response.text
    text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
    assert 'На рассмотрении в комитете' in text
    assert '01.07.2026 14:00 CEST' in text
    assert 'Журнал рассмотрения' in text and 'Подача проекта' in text
    assert 'committee_review' not in text
    assert client.get(BASE + f'/bills/{bill_id}/export.pdf?time_zone=Invalid/Zone').status_code == 422


def test_admin_profile_correction_requires_reason_and_is_public(client):
    assert client.put(BASE + '/admin/profiles/demo-legislator', headers=ADMIN,
                      json={'name': 'Сенатор'}).status_code == 422
    response = client.put(BASE + '/admin/profiles/demo-legislator', headers=ADMIN,
                          json={'name': 'Тестовый сенатор', 'reason': 'Исправление имени'})
    assert response.status_code == 200
    assert client.get(BASE + '/senators').json()[0]['name'] == 'Тестовый сенатор'


def test_custom_flag_permissions_disable_and_explicit_revocation(client):
    response = client.post(BASE + '/admin/flag-types', headers=ADMIN,
                           json={'id': 'elected_member', 'name': 'Избранный сенатор', 'permissions': ['senator']})
    assert response.status_code == 200
    flag(client, 'demo-user', 'elected_member')
    assert client.get(BASE + '/me', headers=READER).json()['capabilities']['create_bill'] is True
    assert any(u['id'] == 'demo-user' for u in client.get(BASE + '/senators').json())
    flag(client, 'demo-user', 'senator', False)
    assert client.get(BASE + '/me', headers=READER).json()['capabilities']['create_bill'] is False
    flag(client, 'demo-user', 'senator', True)
    # A group's own grant disappears when the definition is disabled.
    flag(client, 'demo-lawyer', 'elected_member')
    assert client.get(BASE + '/me', headers={'X-Demo-User': 'lawyer'}).json()['capabilities']['create_bill'] is True
    assert client.post(BASE + '/admin/flag-types', headers=ADMIN,
                       json={'id': 'elected_member', 'active': False}).status_code == 200
    assert client.get(BASE + '/me', headers={'X-Demo-User': 'lawyer'}).json()['capabilities']['create_bill'] is False
    assert client.post(BASE + '/admin/flag-types', headers=ADMIN,
                       json={'name': 'Неверный флаг', 'permissions': ['elected_member']}).status_code == 422


def test_document_type_required_fields_enforced_on_submit(client):
    response = client.post(BASE + '/admin/document-types', headers=ADMIN,
                           json={'id': 'custom', 'name': 'Специальный проект', 'required_fields': ['changes']})
    assert response.status_code == 200
    bill_id = draft(client, document_type='custom')
    response = client.post(BASE + f'/bills/{bill_id}/submit', headers=SENATOR)
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'DOCUMENT_REQUIRED_FIELD'


def test_committee_agenda_snapshots_rules_and_text(client):
    bill_id = draft(client)
    committee_setup(client, bill_id)
    snapshot = client.get(BASE + f'/bills/{bill_id}').json()['reviews'][0]['agenda_snapshot']
    assert snapshot['rule'] == 'predominantly_for'
    assert client.post(BASE + '/admin/categories', headers=ADMIN,
                       json={'id': 'general', 'committee_rule': 'two_thirds', 'committee_threshold': 9}).status_code == 200
    assert decision(client, bill_id).status_code == 200
    export = client.get(BASE + f'/bills/{bill_id}/export.json').json()
    assert export['law']['history']['reviews'][0]['agenda_snapshot'] == snapshot
