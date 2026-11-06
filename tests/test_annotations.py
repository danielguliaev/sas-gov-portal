"""Professional notes must use the same text offsets as the browser's DOM."""
from html import unescape

import bleach


def test_annotation_offsets_decode_entities_and_stay_on_old_revision(client, headers):
    root="/api/v1"
    admin=headers["superadmin"]
    lawyer=headers["lawyer"]
    article=client.post(root+"/articles",headers=admin,json={
        "code_id":"PEN","number":"988","title":"Изменение правовых определений",
        "text_rich":"<p>Норма A &amp; B</p><p> и <strong>условия</strong> применения.</p>",
        "categories":["definition"],"parent_id":None})
    assert article.status_code==200,article.text
    info=article.json()
    original=info["revision_id"]
    actual_text=unescape(bleach.clean(info["text_rich"],tags=[],strip=True))
    quote="A & B"
    start=actual_text.index(quote)
    created=client.post(root+"/articles/"+info["id"]+"/annotations",headers=lawyer,json={
        "revision_id":original,"start_offset":start,"end_offset":start+len(quote),
        "quote":quote,"note_text":"Разъяснение сочетания двух условий"})
    assert created.status_code==200,created.text
    invalid=client.post(root+"/articles/"+info["id"]+"/annotations",headers=lawyer,json={
        "revision_id":original,"start_offset":start,"end_offset":start+len(quote),
        "quote":"другой фрагмент","note_text":"Недопустимое смещение"})
    assert invalid.status_code==422

    lock=client.post(root+"/articles/"+info["id"]+"/edit-lock",headers=admin,json={
        "editor_session_id":"note-snapshot"})
    assert lock.status_code==200,lock.text
    updated=client.post(root+"/articles/"+info["id"]+"/revisions",headers=admin,json={
        "base_revision_id":original,"lock_token":lock.json()["token"],
        "editor_session_id":"note-snapshot","text_rich":"<p>Совершенно новая редакция.</p>"})
    assert updated.status_code==200,updated.text
    newer=updated.json()["revision_id"]
    before=client.get(root+"/articles/"+info["id"]+"/annotations",
                     headers=lawyer,params={"revision_id":original})
    after=client.get(root+"/articles/"+info["id"]+"/annotations",
                     headers=lawyer,params={"revision_id":newer})
    reader=client.get(root+"/articles/"+info["id"]+"/annotations",
                      headers=headers["user"],params={"revision_id":original})
    assert before.status_code==200
    assert len(before.json())==1
    assert before.json()[0]["quote"]==quote
    assert after.status_code==200 and after.json()==[]
    assert reader.status_code==403
