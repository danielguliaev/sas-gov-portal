"""Local-only account/character fixtures and administrator workbench."""
from fastapi import APIRouter, Depends
from app import core
from app.core import actor, allrows, one, plain, fail, dumps, loads, transaction, read_db, ts, uid
from app.senate import require_admin, senate_audit, PROFILE_FIELDS

router = APIRouter(prefix='/test-accounts', tags=['local-test-accounts'])
ACCOUNT_ROLES = {'user', 'lawyer', 'library_editor', 'portal_publisher', 'superadmin'}


def local_only():
    if core.APP_ENV == 'production' or core.AUTH_MODE != 'demo':
        fail(404, 'NOT_FOUND', 'Тестовая панель недоступна.')


def seed():
    local_only()
    with transaction() as c:
        for n in range(1, 21):
            account_id = f'test-{n:02d}'
            c.execute('INSERT OR IGNORE INTO portal_test_accounts VALUES(?,?,1,?,?)',
                      (account_id, f'Тестовый аккаунт {n:02d}', dumps(['user']), ts()))
            for number in (1, 2):
                character_id = f'{account_id}-character-{number}'
                c.execute('INSERT OR IGNORE INTO portal_test_characters VALUES(?,?,?)', (character_id, account_id, ts()))
                c.execute('INSERT OR IGNORE INTO senate_profiles(user_id,name) VALUES(?,?)',
                          (character_id, f'Персонаж {n:02d}.{number}'))


@router.get('/choices', dependencies=[Depends(local_only)])
def choices():
    # Choosing a fixture is intentionally public in local demonstration mode.
    with read_db() as c:
        return allrows(c, '''SELECT a.id AS account_id,a.name AS account_name,ch.id AS character_id,p.name
            FROM portal_test_accounts a JOIN portal_test_characters ch ON ch.account_id=a.id
            JOIN senate_profiles p ON p.user_id=ch.id WHERE a.active=1 ORDER BY a.id,ch.id''')


@router.get('', dependencies=[Depends(local_only)])
def overview(current=Depends(actor)):
    with read_db() as c:
        require_admin(c, current)
        accounts = allrows(c, 'SELECT * FROM portal_test_accounts ORDER BY id')
        for account in accounts:
            account['roles'] = loads(account.pop('roles_json'))
            account['characters'] = allrows(c, '''SELECT ch.id,p.* FROM portal_test_characters ch
                JOIN senate_profiles p ON p.user_id=ch.id WHERE ch.account_id=? ORDER BY ch.id''', (account['id'],))
            for character in account['characters']:
                character['flags'] = [r['flag'] for r in allrows(c,
                    'SELECT flag FROM senate_user_flags WHERE user_id=? AND active=1', (character['id'],))]
        flags = ['senator', 'pro_tempore', 'lieutenant_governor']
        labels = {}
        for committee in allrows(c, 'SELECT id,name FROM senate_committees WHERE active=1'):
            flags += ['committee:' + committee['id'], 'chair:' + committee['id']]
            labels['committee:' + committee['id']] = 'Участник ' + committee['name']
            labels['chair:' + committee['id']] = 'Председатель ' + committee['name']
        for group in allrows(c, 'SELECT id,name FROM senate_flag_definitions WHERE active=1'):
            flags.append(group['id'])
            labels[group['id']] = group['name']
        return {'accounts': accounts, 'account_roles': sorted(ACCOUNT_ROLES), 'character_flags': flags, 'flag_labels': labels,
                'audit': allrows(c, "SELECT * FROM senate_audit WHERE action LIKE 'senate.test_accounts.%' ORDER BY created_at DESC LIMIT 100")}


@router.put('/{account_id}', dependencies=[Depends(local_only)])
def update_account(account_id: str, body: dict, current=Depends(actor)):
    with transaction() as c:
        require_admin(c, current)
        before = one(c, 'SELECT * FROM portal_test_accounts WHERE id=?', (account_id,))
        name, active = plain(body.get('name'), 150), body.get('active')
        roles = body.get('roles')
        if not name or type(active) is not bool or not isinstance(roles, list) or any(not isinstance(r, str) or r not in ACCOUNT_ROLES for r in roles):
            fail(422, 'INVALID_ACCOUNT', 'Укажите название, состояние и допустимые права аккаунта.')
        c.execute('UPDATE portal_test_accounts SET name=?,active=?,roles_json=? WHERE id=?',
                  (name, int(active), dumps(sorted(set(roles) | {'user'})), account_id))
        senate_audit(c, current, 'test_accounts.account', 'test_account', account_id, before, body)
    return {'id': account_id}


@router.post('/{account_id}/characters', dependencies=[Depends(local_only)])
def add_character(account_id: str, body: dict, current=Depends(actor)):
    name = plain(body.get('name'), 150)
    if not name:
        fail(422, 'NAME_REQUIRED', 'Укажите имя персонажа.')
    with transaction() as c:
        require_admin(c, current)
        one(c, 'SELECT id FROM portal_test_accounts WHERE id=?', (account_id,))
        character_id = 'character-' + uid()
        c.execute('INSERT INTO portal_test_characters VALUES(?,?,?)', (character_id, account_id, ts()))
        c.execute('INSERT INTO senate_profiles(user_id,name) VALUES(?,?)', (character_id, name))
        senate_audit(c, current, 'test_accounts.character.create', 'character', character_id, {}, {'account_id': account_id, 'name': name})
    return {'id': character_id}


@router.put('/characters/{character_id}/profile', dependencies=[Depends(local_only)])
def update_character(character_id: str, body: dict, current=Depends(actor)):
    with transaction() as c:
        require_admin(c, current)
        one(c, 'SELECT id FROM portal_test_characters WHERE id=?', (character_id,))
        before = one(c, 'SELECT * FROM senate_profiles WHERE user_id=?', (character_id,))
        values = {f: plain(body.get(f, before[f]), 250) for f in PROFILE_FIELDS}
        if not values['name']:
            fail(422, 'NAME_REQUIRED', 'Укажите имя персонажа.')
        c.execute('UPDATE senate_profiles SET name=?,capitol_office=?,district_office=?,phone=?,email=? WHERE user_id=?',
                  (*(values[f] for f in PROFILE_FIELDS), character_id))
        senate_audit(c, current, 'test_accounts.character.profile', 'character', character_id, before, values)
    return values


@router.put('/characters/{character_id}/flags', dependencies=[Depends(local_only)])
def update_flags(character_id: str, body: dict, current=Depends(actor)):
    requested = body.get('flags')
    with transaction() as c:
        require_admin(c, current)
        one(c, 'SELECT id FROM portal_test_characters WHERE id=?', (character_id,))
        allowed = {'senator', 'pro_tempore', 'lieutenant_governor'}
        allowed.update(r['id'] for r in allrows(c, 'SELECT id FROM senate_flag_definitions WHERE active=1'))
        for r in allrows(c, 'SELECT id FROM senate_committees WHERE active=1'):
            allowed.update({'committee:' + r['id'], 'chair:' + r['id']})
        if not isinstance(requested, list) or any(not isinstance(f, str) or f not in allowed for f in requested):
            fail(422, 'INVALID_FLAGS', 'Выберите существующие группы и полномочия персонажа.')
        before = allrows(c, 'SELECT * FROM senate_user_flags WHERE user_id=?', (character_id,))
        # Replace direct grants, not inherited permissions. Inactive direct rows
        # would incorrectly veto the rights supplied by a selected group.
        c.execute('DELETE FROM senate_user_flags WHERE user_id=?', (character_id,))
        for flag in set(requested):
            c.execute('INSERT INTO senate_user_flags VALUES(?,?,1)', (character_id, flag))
        senate_audit(c, current, 'test_accounts.character.flags', 'character', character_id, before, {'flags': requested})
    return {'flags': requested}
