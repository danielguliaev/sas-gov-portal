from app.core import conn, ts


def api(path):
    return "/api/v1" + path


def test_auth_and_permission_filter(client, headers):
    path = api("/articles/demo-article-1")
    assert client.get(path).status_code == 401
    user = client.get(path, headers=headers["user"])
    assert user.status_code == 200
    assert "categories" not in user.json()
    lawyer = client.get(path, headers=headers["lawyer"])
    assert lawyer.status_code == 200
    assert lawyer.json()["categories"] == ["definition"]
    assert client.get(api("/search?category=accusation"),
                      headers=headers["user"]).status_code == 403
    assert client.get(api("/admin/audit"), headers=headers["user"]).status_code == 403


def test_sanctions_classification_and_unique_number(client, headers, admin):
    bad = client.post(api("/articles"), headers=headers["superadmin"],
        json={"code_id":"PEN","number":"10","title":"Тест обвинения",
              "text_rich":"<p>Текст</p>","categories":["accusation"]})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "CRIMINAL_CLASS_REQUIRED"
    bad_fine = client.post(api("/articles"), headers=headers["superadmin"],
        json={"code_id":"PEN","number":"11","title":"Санкция",
              "categories":["accusation"],"criminal_class":"felony",
              "sanctions":{"fine_min_usd":100,"fine_max_usd":50}})
    assert bad_fine.status_code == 422
    a = admin("10", cats=["accusation"], crime="felony",
              sanctions={"prison_min_months":0,"prison_max_months":12})
    assert "accusation" in a["categories"]
    assert "punishment_measure" in a["categories"]
    assert a["sanctions"]["prison_min_months"] == 0
    assert a["sanctions"]["fine_min_usd"] is None
    duplicate = client.post(api("/articles"), headers=headers["superadmin"],
        json={"code_id":"PEN","number":" 1-0 ","title":"Дублирование","categories":["definition"]})
    assert duplicate.status_code == 409
    assert client.get(api("/articles/" + a["id"]), headers=headers["user"]).json()["id"] == a["id"]


def test_edit_lock_revision_and_expiry(client, headers, admin):
    a = admin("21")
    url = api("/articles/" + a["id"])
    session_a = {"editor_session_id": "window-1"}
    lock = client.post(url+"/edit-lock", headers=headers["superadmin"], json=session_a)
    assert lock.status_code == 200, lock.text
    token = lock.json()["token"]
    second = client.post(url+"/edit-lock", headers=headers["superadmin"],
                         json={"editor_session_id":"window-2"})
    assert second.status_code == 409
    stale = client.post(url+"/revisions", headers=headers["superadmin"],
        json={"base_revision_id":"old","lock_token":token,"editor_session_id":"window-1",
              "title":"Подмена"})
    assert stale.status_code == 409
    good = client.post(url+"/revisions", headers=headers["superadmin"],
        json={"base_revision_id":a["revision_id"],"lock_token":token,
              "editor_session_id":"window-1","number":"21-A",
              "text_rich":'<p>Текст</p><script>alert(1)</script>'})
    assert good.status_code == 200, good.text
    assert good.json()["id"] == a["id"]
    assert "script" not in good.json()["text_rich"]
    assert good.json()["revision_id"] != a["revision_id"]
    versions = client.get(url+"/revisions", headers=headers["lawyer"])
    assert len(versions.json()) == 2
    conn_obj = conn()
    conn_obj.execute("UPDATE edit_locks SET expires_at=? WHERE article_id=?",
                     ("2000-01-01T00:00:00+00:00", a["id"]))
    conn_obj.close()
    expired = client.post(url+"/revisions", headers=headers["superadmin"],
        json={"base_revision_id":good.json()["revision_id"],"lock_token":token,
              "editor_session_id":"window-1","title":"Не разрешено"})
    assert expired.status_code == 409
    replacement = client.post(url+"/edit-lock",headers=headers["superadmin"],
                              json={"editor_session_id":"window-2"})
    assert replacement.status_code == 200
    assert replacement.json()["token"] != token


def test_lock_revoke_only_own(client, headers, admin):
    a = admin("22")
    path = api("/articles/" + a["id"] + "/edit-lock")
    lock = client.post(path, json={"editor_session_id":"tab-1"}, headers=headers["superadmin"])
    assert lock.status_code == 200
    assert client.post(path+"/revoke",json={"confirm":False},
                       headers=headers["superadmin"]).status_code == 422
    revoke = client.post(path+"/revoke",json={"confirm":True},
                         headers=headers["superadmin"])
    assert revoke.status_code == 200
    assert client.post(path+"/heartbeat",headers=headers["superadmin"],
                       json={"token":lock.json()["token"],"editor_session_id":"tab-1"}).status_code == 409


def test_relations_and_archive_no_auto_reactivation(client, headers, admin):
    accusation = admin("30", cats=["accusation"], crime="misdemeanor")
    second = admin("31", cats=["accusation"], crime="felony")
    punishment1 = admin("32", sanctions={"fine_min_usd":0,"fine_max_usd":100})
    punishment2 = admin("33", sanctions={"prison_min_months":1,"prison_max_months":12})
    def rel(source, target):
        return client.post(api("/relations"), headers=headers["superadmin"],
            json={"type_id":"accusation_punishment","source_id":source["id"],"target_id":target["id"]})
    assert rel(accusation,punishment1).status_code == 200
    assert rel(accusation,punishment2).status_code == 200
    assert rel(second,punishment1).status_code == 409
    preview = client.post(api("/archive/preview"),headers=headers["superadmin"],
                          json={"article_id":punishment1["id"]}).json()
    archive = client.post(api("/archive-batches"),headers=dict(headers["superadmin"],**{"Idempotency-Key":"archive-punish"}),
        json={"article_id":punishment1["id"],"preview_hash":preview["preview_hash"],"confirm":True})
    assert archive.status_code == 200, archive.text
    relations = client.get(api("/articles/"+accusation["id"]+"/relations"),
                           headers=headers["user"]).json()
    assert len(relations["items"]) == 1
    batch_id = archive.json()["id"]
    restore_preview = client.get(api("/archive-batches/"+batch_id+"/preview"),
                                 headers=headers["superadmin"]).json()
    restore = client.post(api("/restore-batches"),
        headers=dict(headers["superadmin"],**{"Idempotency-Key":"restore-punish"}),
        json={"batch_id":batch_id,"preview_hash":restore_preview["preview_hash"],"confirm":True})
    assert restore.status_code == 200, restore.text
    assert restore.json()["relations_reactivated"] == 0
    assert len(client.get(api("/articles/"+accusation["id"]+"/relations"),
                          headers=headers["user"]).json()["items"]) == 1


def test_atomic_conflict_swap_and_idempotency(client, headers, admin):
    a = admin("40")
    preview = client.post(api("/archive/preview"), headers=headers["superadmin"],
        json={"article_id":a["id"]}).json()
    body = {"article_id":a["id"],"preview_hash":preview["preview_hash"],"confirm":True}
    h = dict(headers["superadmin"],**{"Idempotency-Key":"archive40"})
    archived = client.post(api("/archive-batches"), json=body, headers=h)
    assert archived.status_code == 200
    repeated = client.post(api("/archive-batches"), json=body, headers=h)
    assert repeated.json()["id"] == archived.json()["id"]
    replacement = admin("40")
    preview = client.get(api("/archive-batches/"+archived.json()["id"]+"/preview"),
                         headers=headers["superadmin"]).json()
    assert len(preview["conflicts"]) == 1
    restore_body = {"batch_id":archived.json()["id"],"preview_hash":preview["preview_hash"],"confirm":True}
    denied = client.post(api("/restore-batches"), json=restore_body,
        headers=dict(headers["superadmin"],**{"Idempotency-Key":"swap40"}))
    assert denied.status_code == 409
    restore_body["confirm_swap"] = True
    restored = client.post(api("/restore-batches"), json=restore_body,
        headers=dict(headers["superadmin"],**{"Idempotency-Key":"swap41"}))
    assert restored.status_code == 200, restored.text
    assert client.get(api("/articles/"+a["id"]),headers=headers["user"]).status_code == 200
    assert client.get(api("/articles/"+replacement["id"]),headers=headers["user"]).status_code == 403


def test_pagination_and_deep_link_position(client, headers, admin):
    for n in range(50, 65):
        admin(str(n))
    first = client.get(api("/nodes/demo-intro/articles?limit=5"),headers=headers["user"]).json()
    assert first["has_more"] is True
    assert len(first["items"]) == 5
    second = client.get(api("/nodes/demo-intro/articles?limit=5&cursor="+first["next_cursor"]),
                        headers=headers["user"]).json()
    assert len(second["items"]) == 5
    assert not {a["id"] for a in first["items"]}.intersection(a["id"] for a in second["items"])
    pos = client.get(api("/articles/"+second["items"][0]["id"]+"/position"),
                     headers=headers["user"])
    assert pos.status_code == 200
    assert pos.json()["index"] >= 5


def test_portal_publication_editorial_confirmation(client, headers, admin):
    a = admin("90")
    pub = client.post(api("/portal-publications"),headers=headers["publisher"],
        json={"portal_id":"government","jurisdiction_id":"state","title":"Демо постановление",
              "document_type":"Постановление","issuer":"Правительство штата",
              "text_rich":"<p>Демонстрационный акт</p>","related_article_id":a["id"]})
    assert pub.status_code == 200,pub.text
    published = client.post(api("/portal-publications/"+pub.json()["id"]+"/publish"),
        headers=dict(headers["publisher"],**{"Idempotency-Key":"publish-90"}),
        json={"confirm":True})
    assert published.status_code == 200,published.text
    assert published.json()["review_id"]
    assert client.post(api("/portal-publications/"+pub.json()["id"]+"/publish"),
        headers=dict(headers["publisher"],**{"Idempotency-Key":"publish-90"}),
        json={"confirm":True}).json() == published.json()
    # Publication does not immediately change the consolidated article.
    assert client.get(api("/articles/"+a["id"]),headers=headers["user"]).json()["revision_id"] == a["revision_id"]
    lock = client.post(api("/articles/"+a["id"]+"/edit-lock"), headers=headers["editor"],
        json={"editor_session_id":"editor-window"})
    assert lock.status_code == 200, lock.text
    applied = client.post(api("/editorial-reviews/"+published.json()["review_id"]+"/confirm"),
        headers=dict(headers["editor"],**{"Idempotency-Key":"confirm-90"}),
        json={"confirm":True,"base_revision_id":a["revision_id"],
              "lock_token":lock.json()["token"],"editor_session_id":"editor-window",
              "consolidated_text_rich":"<p>Проверенный текст</p>"})
    assert applied.status_code == 200, applied.text
    after = client.get(api("/articles/"+a["id"]),headers=headers["user"]).json()
    assert "Проверенный текст" in after["text_rich"]
    assert after["provenance"]["publication_id"] == pub.json()["id"]


def test_field_optional_and_server_side_field_privacy(client, headers, admin):
    created = client.post(api("/categories/accusation/fields"), headers=headers["superadmin"],
        json={"name":"Условное поле","field_type":"choice","selection_mode":"single"})
    assert created.status_code == 200
    a = admin("95", cats=["accusation"], crime="infraction")
    lawyer = client.get(api("/articles/"+a["id"]),headers=headers["lawyer"]).json()
    assert lawyer["custom_values"] == {}
    assert "custom_values" not in client.get(api("/articles/"+a["id"]),
                                             headers=headers["user"]).json()
