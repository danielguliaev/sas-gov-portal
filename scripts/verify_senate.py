"""Re-run the Senate acceptance path on an isolated database, saving real exports."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from fastapi.testclient import TestClient

import app.core as core
from app.main import app

API = '/api/v1/senate'
OUTPUT = Path(__file__).resolve().parents[1] / '.senate-preview/acceptance'


def run():
    if core.AUTH_MODE != 'demo' or core.APP_ENV == 'production':
        raise RuntimeError('Acceptance uses local demonstration identities. Run with AUTH_MODE=demo and APP_ENV=development.')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    previous_path = core.DB_PATH
    try:
        with TemporaryDirectory(prefix='senate-acceptance-') as tmp:
            core.DB_PATH = str(Path(tmp) / 'acceptance.db')
            with TestClient(app) as client:
                def request(method, path, role=None, body=None):
                    headers = {'X-Demo-User': role} if role else {}
                    response = client.request(method, API + path, headers=headers, json=body)
                    if response.status_code != 200:
                        raise AssertionError(f'{method} {path}: {response.status_code} {response.text}')
                    if response.headers.get('content-type', '').startswith('application/json'):
                        return response.json()
                    return response.content

                for user, flag in [('demo-legislator', 'committee:rules'), ('demo-legislator', 'chair:rules'),
                                   ('demo-legislator', 'pro_tempore'), ('demo-user', 'committee:rules')]:
                    request('POST', '/admin/flags', 'superadmin', {'user_id': user, 'flag': flag, 'active': True})
                with core.read_db() as c:
                    library_before = core.allrows(c, 'SELECT * FROM articles ORDER BY id')
                bills = []
                for number in (1, 2):
                    text = ''.join(f'<p>Статья {i}. Проверочный текст о публикации материалов комитетов и сохранении истории рассмотрения. '
                                   f'Это материал автоматической проверки, а не действующий акт.</p>' for i in range(1, 15))
                    bill = request('POST', '/bills', 'legislator', {
                        'title': f'Проверочный проект {number}. О публикации материалов комитетов',
                        'text_rich': text, 'document_type': 'senate_bill', 'category_id': 'general',
                        'effective_at': '2026-12-01T00:00:00Z', 'changes': [{
                            'operation': 'amend', 'target': {'article_id': 'demo-article-1'},
                            'article': {'text_rich': '<p>Предлагаемая поправка для проверки экспорта.</p>'}}],
                    })
                    bill_id = bill['id']
                    request('PUT', f'/bills/{bill_id}', 'legislator', {'base_version': 1, 'text_rich': text + '<p>Редакция уточнена.</p>'})
                    assert client.get(API + f'/bills/{bill_id}').status_code == 404
                    request('POST', f'/bills/{bill_id}/coauthors', 'legislator', {'user_id': 'demo-lawyer'})
                    request('POST', f'/bills/{bill_id}/coauthors/respond', 'lawyer', {'accept': True})
                    request('POST', f'/bills/{bill_id}/submit', 'legislator')
                    request('POST', '/committees/rules/reports', 'user', {
                        'bill_id': bill_id, 'text': 'Проверочный отчёт консультанта. Замечаний нет.', 'recommendation': 'do_pass'})
                    review = request('GET', f'/bills/{bill_id}')['reviews'][0]
                    request('POST', '/committees/rules/agenda', 'legislator', {'title': 'Проверочная повестка', 'review_ids': [review['id']]})
                    request('POST', '/committees/rules/decisions', 'legislator', {
                        'bill_id': bill_id, 'decision': 'do_pass', 'comment': 'Рекомендовано к принятию.',
                        'transcript': 'Проверочная запись открытого совещания.', 'tally': {'yes': 1, 'no': 0, 'abstain': 0, 'absent': 0}})
                    bills.append(bill_id)
                now = datetime.now(timezone.utc)
                ends = now + timedelta(seconds=5)
                session = request('POST', '/sessions', 'legislator', {
                    'title': 'Проверочное пленарное заседание', 'starts_at': (now + timedelta(seconds=1)).isoformat(),
                    'ends_at': ends.isoformat(), 'quorum': 1, 'bill_ids': bills})
                session_id = session['id']
                request('POST', f'/sessions/{session_id}/publish', 'legislator')
                vote_path = f'/sessions/{session_id}/votes'
                request('POST', vote_path, 'legislator', {'bill_id': bills[0], 'choice': 'yes'})
                request('POST', vote_path, 'legislator', {'bill_id': bills[1], 'choice': 'absent'})
                absent = request('GET', f'/sessions/{session_id}')
                assert absent['attendance']['present'] == 0
                assert all(item['choices'][0]['choice'] == 'absent' for item in absent['items'])
                for bill_id in bills:
                    request('POST', vote_path, 'legislator', {'bill_id': bill_id, 'choice': 'yes'})
                timeout = monotonic() + 10
                while datetime.now(timezone.utc) <= ends:
                    if monotonic() >= timeout:
                        raise AssertionError('The test session did not reach its deadline.')
                    sleep(.1)
                closed = request('GET', f'/sessions/{session_id}')
                assert closed['status'] == 'closed'
                assert all(item['result'] == 'adopted' for item in closed['items'])
                request('PUT', f'/sessions/{session_id}/transcript', 'legislator', {'text': 'Проверочная стенограмма. Оба проекта приняты.'})
                exported = request('GET', f'/bills/{bills[0]}/export.json')
                assert len(exported['law']['history']['versions']) == 2
                assert exported['law']['act_id']
                (OUTPUT / 'bill.json').write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding='utf-8')
                (OUTPUT / 'bill.pdf').write_bytes(request('GET', f'/bills/{bills[0]}/export.pdf?time_zone=Europe/Berlin'))
                (OUTPUT / 'session.pdf').write_bytes(request('GET', f'/sessions/{session_id}/export.pdf?time_zone=Europe/Berlin'))
                with core.read_db() as c:
                    assert library_before == core.allrows(c, 'SELECT * FROM articles ORDER BY id')
                summary = {'result': 'passed', 'bills': len(bills), 'versions_per_bill': 2,
                           'attendance': closed['attendance']['present'], 'results': [i['result'] for i in closed['items']],
                           'library_unchanged': True, 'exports': ['bill.pdf', 'bill.json', 'session.pdf']}
                (OUTPUT / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps(summary, ensure_ascii=False))
                print('Exports:', OUTPUT)
    finally:
        core.DB_PATH = previous_path


if __name__ == '__main__':
    run()
