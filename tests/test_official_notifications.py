"""Official legal amendments appear in each lawyer's own notification center."""
def api(path):
    return "/api/v1"+path


def test_lawyer_without_favorites_receives_official_change_once(client,headers):
    admin=headers["superadmin"]
    publisher=headers["publisher"]
    editor=headers["editor"]
    lawyer=headers["lawyer"]
    reader=headers["user"]
    article=client.post(api("/articles"),headers=admin,json={
        "code_id":"PEN","number":"873","title":"Официальная редакция",
        "text_rich":"<p>Старый текст.</p>","categories":["definition"]})
    assert article.status_code==200,article.text
    a=article.json()
    assert client.get(api("/me/favorites"),headers=lawyer).json()==[]
    assert client.get(api("/me/notifications"),headers=lawyer).json()==[]
    pub=client.post(api("/portal-publications"),headers=publisher,json={
        "portal_id":"government","jurisdiction_id":"state",
        "title":"Изменение положения","issuer":"Правительство штата",
        "document_type":"Постановление","text_rich":"<p>Официальное постановление.</p>",
        "related_article_id":a["id"]})
    assert pub.status_code==200,pub.text
    published=client.post(api("/portal-publications/"+pub.json()["id"]+"/publish"),
        headers={**publisher,"Idempotency-Key":"off-n1"},json={"confirm":True})
    assert published.status_code==200,published.text
    lock=client.post(api("/articles/"+a["id"]+"/edit-lock"),headers=editor,json={
        "editor_session_id":"official-notification"})
    assert lock.status_code==200,lock.text
    approved=client.post(api("/editorial-reviews/"+published.json()["review_id"]+"/confirm"),
        headers={**editor,"Idempotency-Key":"off-n2"},json={
        "confirm":True,"base_revision_id":a["revision_id"],"lock_token":lock.json()["token"],
        "editor_session_id":"official-notification",
        "consolidated_text_rich":"<p>Новый текст статьи.</p>"})
    assert approved.status_code==200,approved.text
    first=client.get(api("/me/notifications"),headers=lawyer)
    assert first.status_code==200,first.text
    official=[n for n in first.json() if n["event_key"]=="official:"+approved.json()["revision_id"]]
    assert len(official)==1
    assert official[0]["portal_id"]=="library"
    assert official[0]["is_read"]==0
    second=client.get(api("/me/notifications"),headers=lawyer)
    assert [x["id"] for x in second.json() if x["event_key"]==official[0]["event_key"]]==[official[0]["id"]]
    marked=client.post(api("/me/notifications/"+official[0]["id"]+"/read"),headers=lawyer)
    assert marked.status_code==200
    third=client.get(api("/me/notifications"),headers=lawyer)
    matching=[n for n in third.json() if n["id"]==official[0]["id"]]
    assert len(matching)==1 and matching[0]["is_read"]==1
    reader_events=client.get(api("/me/notifications"),headers=reader).json()
    assert all(n["event_key"]!=official[0]["event_key"] for n in reader_events)
