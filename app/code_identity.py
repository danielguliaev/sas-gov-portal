"""Rename code identities transactionally while keeping article identities intact."""
import re


def code_identifier(abbreviation):
    value = abbreviation.strip().upper()
    return value if re.fullmatch(r'[\w-]{1,40}', value, re.UNICODE) else None


def canonical_code(c, value):
    row = c.execute('SELECT code_id FROM code_id_aliases WHERE old_id=?', (value,)).fetchone()
    return row['code_id'] if row else value


def migrate_code_ids(c):
    rows = [dict(r) for r in c.execute('SELECT id,abbreviation,name FROM codes')]
    proposed = {r['id']: ('PEN' if r['id'] in {'demo-penal', 'penal-demo'} and r['name'] == 'Penal Code'
                         else code_identifier(r['abbreviation'])) for r in rows}
    occupied = {r['id'] for r in rows}
    c.execute('PRAGMA defer_foreign_keys=ON')
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for row in rows:
        old, new = row['id'], proposed[row['id']]
        # Ambiguous abbreviations must never merge two libraries.
        if not new or old == new or new in occupied or list(proposed.values()).count(new) != 1:
            continue
        for table in tables:
            for fk in c.execute(f'PRAGMA foreign_key_list("{table}")').fetchall():
                if fk[2] == 'codes' and fk[4] == 'id':
                    c.execute(f'UPDATE "{table}" SET "{fk[3]}"=? WHERE "{fk[3]}"=?', (new, old))
        modes = c.execute('SELECT parent_key FROM sort_modes WHERE substr(parent_key,1,?)=?', (len(old)+1, old+':')).fetchall()
        for mode in modes:
            c.execute('UPDATE sort_modes SET parent_key=? WHERE parent_key=?', (new+mode[0][len(old):], mode[0]))
        c.execute('UPDATE codes SET id=?,abbreviation=? WHERE id=?', (new, new, old))
        c.execute('INSERT INTO code_id_aliases VALUES(?,?)', (old, new))
        occupied.add(new)
    if c.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('Смена идентификаторов кодексов нарушает ссылки.')
