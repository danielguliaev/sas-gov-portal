"""Standalone normative acts need explicit classification, immutable IDs and safe archive."""
def api(value):
    return "/api/v1"+value


def test_external_published_document_is_not_law_until_explicit_classification(client,headers):
    publisher=headers["publisher"]
    admin=headers["superadmin"]
    reader=headers["user"]
    pub=client.post(api("/portal-publications"),headers=publisher,json={
        "portal_id":"government","jurisdiction_id":"state",
        "title":"Постановление о порядке ведомственной работы",
        "document_type":"Постановление","issuer":"Правительство штата",
        "text_rich":"<p>Официальный текст публикации.</p>"})
    assert pub.status_code==200,pub.text
    pid=pub.json()["id"]
    published=client.post(api("/portal-publications/"+pid+"/publish"),
                          headers={**publisher,"Idempotency-Key":"classify-published"},
                          json={"confirm":True})
    assert published.status_code==200,published.text
    before=client.get(api("/library/acts"),headers=reader)
    assert before.status_code==200
    assert pid not in {x["id"] for x in before.json()}
    assert client.get(api("/library/acts/"+pid),headers=reader).status_code==404
    assert client.post(api("/library/acts/"+pid+"/classify"),headers={
        **admin,"Idempotency-Key":"classify-denied"},json={"confirm":False}).status_code==422

    key={**admin,"Idempotency-Key":"classify-approved"}
    included=client.post(api("/library/acts/"+pid+"/classify"),headers=key,
                         json={"confirm":True})
    assert included.status_code==200,included.text
    repeated=client.post(api("/library/acts/"+pid+"/classify"),headers=key,
                         json={"confirm":True})
    assert repeated.status_code==200,repeated.text
    assert pid in {x["id"] for x in client.get(api("/library/acts"),headers=reader).json()}
    assert client.get(api("/library/acts/"+pid),headers=reader).json()["status"]=="published"

    preview=client.post(api("/library/acts/"+pid+"/archive/preview"),headers=admin)
    assert preview.status_code==200,preview.text
    archive=client.post(api("/library/acts/"+pid+"/archive"),headers={
        **admin,"Idempotency-Key":"classify-archive"},json={
        "confirm":True,"preview_hash":preview.json()["preview_hash"]})
    assert archive.status_code==200,archive.text
    assert pid not in {x["id"] for x in client.get(api("/library/acts"),headers=reader).json()}
    assert client.get(api("/library/acts/"+pid),headers=reader).status_code==403
    assert client.get(api("/library/acts/"+pid),headers=headers["lawyer"]).json()["status"]=="archived"
    restored=client.post(api("/library/acts/"+pid+"/restore"),headers={
        **admin,"Idempotency-Key":"classify-restore"},json={"confirm":True})
    assert restored.status_code==200,restored.text
    assert pid in {x["id"] for x in client.get(api("/library/acts"),headers=reader).json()}


def test_standalone_act_direct_publish_keeps_id_and_source_is_scoped(client,headers):
    admin=headers["superadmin"]
    reader=headers["user"]
    body={
        "jurisdiction_id":"city","title":"Городской правовой акт",
        "issuer":"Уполномоченный муниципальный орган",
        "document_type":"Постановление","text_rich":"<p>Городское регулирование.</p>",
        "confirm":True
    }
    failed=client.post(api("/library/acts"),headers=reader,json=body)
    assert failed.status_code==403
    publish=client.post(api("/library/acts"),headers={
        **admin,"Idempotency-Key":"city-publication"},json=body)
    assert publish.status_code==200,publish.text
    act_id=publish.json()["id"]
    repeat=client.post(api("/library/acts"),headers={
        **admin,"Idempotency-Key":"city-publication"},json=body)
    assert repeat.status_code==200 and repeat.json()["id"]==act_id
    city=client.get(api("/library/acts?jurisdiction=city"),headers=reader)
    assert act_id in {item["id"] for item in city.json()}
    state=client.get(api("/library/acts?jurisdiction=state"),headers=reader)
    assert act_id not in {item["id"] for item in state.json()}
    document=client.get(api("/library/acts/"+act_id),headers=reader)
    assert document.status_code==200
    assert document.json()["text_rich"]=="<p>Городское регулирование.</p>"
    assert document.json()["permalink"]=="/acts/"+act_id
