"""End-to-end API contracts for the previously missing library administration functions."""
import app.core as core


def url(path):
    return "/api/v1" + path


def test_revision_freezes_category_field_and_option_names(client, headers, admin):
    auth=headers["superadmin"]
    field=client.post(url("/categories/definition/fields"),headers=auth,json={
        "name":"Условия применения","field_type":"choice","selection_mode":"single"})
    assert field.status_code==200,field.text
    fid=field.json()["id"]
    option=client.post(url("/category-fields/"+fid+"/options"),headers=auth,
        json={"name":"Первоначальное значение"})
    oid=option.json()["id"]
    response=client.post(url("/articles"),headers=auth,json={
        "code_id":"demo-penal","parent_id":None,"number":"801",
        "title":"Статья с атрибутом","categories":["definition"],
        "custom_values":{fid:oid},"text_rich":"<p>Текст</p>"})
    assert response.status_code==200,response.text
    first=response.json()
    previous=client.get(url("/articles/"+first["id"]+"/revisions/"+first["revision_id"]),
                        headers=headers["lawyer"])
    snapshot=previous.json()["display_snapshot"]
    assert snapshot["categories"]["definition"]=="Определение"
    assert snapshot["fields"][fid]["name"]=="Условия применения"
    assert snapshot["fields"][fid]["value"]["name"]=="Первоначальное значение"
    updated=client.patch(url("/categories/definition"),headers=auth,
                         json={"name":"Толкование"})
    assert updated.status_code==200,updated.text
    updated=client.patch(url("/field-options/"+oid),headers=auth,
                         json={"name":"Переименовано"})
    assert updated.status_code==200,updated.text
    previous=client.get(url("/articles/"+first["id"]+"/revisions/"+first["revision_id"]),
                        headers=headers["lawyer"]).json()
    assert previous["display_snapshot"]["fields"][fid]["value"]["name"]=="Первоначальное значение"
    lock=client.post(url("/articles/"+first["id"]+"/edit-lock"),headers=auth,
                     json={"editor_session_id":"snapshot"})
    assert lock.status_code==200
    next_rev=client.post(url("/articles/"+first["id"]+"/revisions"),headers=auth,json={
        "editor_session_id":"snapshot","lock_token":lock.json()["token"],
        "base_revision_id":first["revision_id"],"text_rich":"<p>Новая редакция</p>"})
    assert next_rev.status_code==200,next_rev.text
    current=client.get(url("/articles/"+first["id"]+"/revisions/"+next_rev.json()["revision_id"]),
                       headers=headers["lawyer"]).json()
    assert current["display_snapshot"]["fields"][fid]["value"]["name"]=="Переименовано"
    assert current["display_snapshot"]["categories"]["definition"]=="Толкование"


def test_typed_field_search_and_history(client,headers,admin):
    auth=headers["superadmin"]
    multiple=client.post(url("/categories/definition/fields"),headers=auth,json={
        "name":"Признаки","field_type":"choice","selection_mode":"multiple"}).json()["id"]
    o1=client.post(url("/category-fields/"+multiple+"/options"),headers=auth,
                   json={"name":"Вариант А"}).json()["id"]
    o2=client.post(url("/category-fields/"+multiple+"/options"),headers=auth,
                   json={"name":"Вариант Б"}).json()["id"]
    new=client.post(url("/articles"),headers=auth,json={
        "code_id":"demo-penal","number":"805","title":"Особая редакция",
        "categories":["definition"],"custom_values":{multiple:[o1,o2]},
        "text_rich":"<p>Редкий исторический оборот</p>"})
    assert new.status_code==200,new.text
    aid=new.json()["id"]
    filtered=client.get(url("/search"),headers=headers["lawyer"],
                        params={"field_id":multiple,"field_value":o2})
    assert filtered.status_code==200,filtered.text
    assert aid in [row["id"] for row in filtered.json()["items"]]
    basic=client.get(url("/search"),headers=headers["user"],
                     params={"field_id":multiple,"field_value":o2})
    assert basic.status_code==403
    revisions=client.get(url("/search/revisions"),headers=headers["lawyer"],
                         params={"query":"Редкий исторический"})
    assert revisions.status_code==200,revisions.text
    assert aid in [r["article_id"] for r in revisions.json()["items"]]
    assert client.get(url("/search/revisions"),headers=headers["user"],
                      params={"query":"Редкий"}).status_code==403


def test_restore_can_choose_another_valid_node_location(client,headers,admin):
    auth=headers["superadmin"]
    type_id=client.post(url("/codes/demo-penal/hierarchy-types"),headers=auth,json={
        "name":"Дополнительный раздел","allowed_parents":["ROOT"],
        "level_order":1}).json()["id"]
    parent=client.post(url("/codes/demo-penal/nodes"),headers=auth,json={
        "type_id":type_id,"number":"3","title":"Новая группа"}).json()["id"]
    article=admin("806",parent="demo-intro")
    original=article["revision_id"]
    plan=client.post(url("/archive/preview"),headers=auth,
                     json={"article_id":article["id"]}).json()
    batch=client.post(url("/archive-batches"),
        headers={**auth,"Idempotency-Key":"location-archive"},
        json={"article_id":article["id"],"confirm":True,
              "preview_hash":plan["preview_hash"]})
    assert batch.status_code==200,batch.text
    inventory=client.get(url("/admin/archives"),headers=auth)
    assert inventory.status_code==200
    assert batch.json()["id"] in [b["id"] for b in inventory.json()]
    assert client.get(url("/admin/archives"),headers=headers["lawyer"]).status_code==403
    restore_plan=client.get(url("/archive-batches/"+batch.json()["id"]+"/preview"),
                            headers=auth).json()
    restored=client.post(url("/restore-batches"),
        headers={**auth,"Idempotency-Key":"location-restore"},json={
        "batch_id":batch.json()["id"],"confirm":True,
        "preview_hash":restore_plan["preview_hash"],"new_parent_id":parent})
    assert restored.status_code==200,restored.text
    actual=client.get(url("/articles/"+article["id"]),headers=headers["lawyer"]).json()
    assert actual["parent_id"]==parent
    assert actual["revision_id"]==original


def test_restore_nested_subtree_blocks_own_descendant(client,headers,admin):
    auth=headers["superadmin"]
    kind=client.post(url("/codes/demo-penal/hierarchy-types"),headers=auth,json={
        "name":"Вложенный тип","allowed_parents":["ROOT","demo-chapter"]}).json()["id"]
    child=client.post(url("/codes/demo-penal/nodes"),headers=auth,json={
        "type_id":kind,"parent_id":"demo-intro","number":"2",
        "title":"Вложенный элемент"}).json()["id"]
    # Archive a root that contains the nested element; self-placement must fail.
    plan=client.post(url("/archive/preview"),headers=auth,
                     json={"node_id":"demo-intro"}).json()
    batch=client.post(url("/archive-batches"),
        headers={**auth,"Idempotency-Key":"subtree-archive"},
        json={"node_id":"demo-intro","confirm":True,
              "preview_hash":plan["preview_hash"]}).json()["id"]
    restore_plan=client.get(url("/archive-batches/"+batch+"/preview"),headers=auth).json()
    error=client.post(url("/restore-batches"),
        headers={**auth,"Idempotency-Key":"subtree-restore"},
        json={"batch_id":batch,"confirm":True,"preview_hash":restore_plan["preview_hash"],
              "new_parent_id":child})
    assert error.status_code==422,error.text
    assert error.json()["error"]["code"]=="HIERARCHY_CYCLE"
    assert client.get(url("/nodes/"+child+"/article-index"),
                      headers=headers["user"]).status_code==404


def test_readonly_service_api_has_separate_auth_and_no_private_data(client,headers,admin,monkeypatch):
    import hashlib
    key="random-key-for-integration-test"
    monkeypatch.setenv("SERVICE_API_KEY_SHA256",hashlib.sha256(key.encode()).hexdigest())
    endpoint=url("/integration/articles/demo-article-1")
    assert client.get(endpoint,headers=headers["superadmin"]).status_code==401
    wrong=client.get(endpoint,headers={"Authorization":"Bearer wrong"})
    assert wrong.status_code==401
    response=client.get(endpoint,headers={"Authorization":"Bearer "+key})
    assert response.status_code==200,response.text
    item=response.json()
    assert item["id"]=="demo-article-1"
    assert "criminal_class" in item and "sanctions" in item and "relations" in item
    assert "annotations" not in item and "audit_events" not in item
    pages=client.get(url("/integration/articles"),params={"code_id":"demo-penal"},
                     headers={"Authorization":"Bearer "+key})
    assert pages.status_code==200
    assert pages.json()["items"]
    assert client.post(url("/articles"),json={"code_id":"demo-penal","number":"900"},
                       headers={"Authorization":"Bearer "+key}).status_code==401


def test_throttle_returns_429_and_security_headers(client,headers,monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE","2")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS","10")
    from app.main import _rate_windows
    _rate_windows.clear()
    urlpath=url("/codes")
    assert client.get(urlpath,headers=headers["user"]).status_code==200
    second=client.get(urlpath,headers=headers["user"])
    assert second.status_code==200
    assert second.headers["X-Content-Type-Options"]=="nosniff"
    assert second.headers["Cache-Control"]=="no-store"
    third=client.get(urlpath,headers=headers["user"])
    assert third.status_code==429
    assert third.json()["error"]["code"]=="RATE_LIMITED"
    monkeypatch.delenv("RATE_LIMIT_PER_MINUTE")
    _rate_windows.clear()


def test_csp_and_browser_permissions_restrict_untrusted_code(client,headers):
    for path in ("/","/codes/demo-penal",url("/codes")):
        response=client.get(path,headers=headers["user"])
        assert response.status_code==200,response.text
        csp=response.headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp
        assert "object-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "base-uri 'self'" in csp
        assert "'unsafe-eval'" not in csp
        assert response.headers["Permissions-Policy"].startswith("camera=()")
        assert response.headers["X-Frame-Options"]=="DENY"


def test_cross_chapter_navigation_uses_structure_before_root_articles(client,headers,admin):
    child=admin("9",parent="demo-intro",title="Поздняя глава")
    root=admin("0",parent=None,title="Ранняя нумерация без главы")
    # Even though root has a lower number, the chapter and all its articles come first.
    previous=client.get(url("/articles/"+child["id"]+"/adjacent"),
                        headers=headers["user"])
    assert previous.status_code==200,previous.text
    assert previous.json()["next"]["id"]==root["id"]
    first=client.get(url("/articles/demo-article-1/adjacent"),headers=headers["user"])
    assert first.status_code==200,first.text
    assert first.json()["next"]["id"]==child["id"]
    last=client.get(url("/articles/"+root["id"]+"/adjacent"),headers=headers["user"])
    assert last.json()["previous"]["id"]==child["id"]


def test_offline_backup_and_restore_are_verified(client,headers,tmp_path):
    from scripts.db_maintenance import backup,restore,verify
    path=core.DB_PATH
    saved=tmp_path/"library-snapshot.sqlite"
    backup(path,saved)
    assert saved.is_file()
    assert 2 in verify(saved)["schema_versions"]
    response=client.post(url("/articles"),headers=headers["superadmin"],json={
        "code_id":"demo-penal","number":"916","title":"Временная статья",
        "categories":["definition"]})
    assert response.status_code==200,response.text
    assert response.json()["id"]
    # The public server is normally stopped before restore. The test's isolated
    # SQLite connections are idle and exercising the same backup API is safe.
    restore(path,saved,confirm=True)
    assert client.get(url("/articles/"+response.json()["id"]),
                      headers=headers["lawyer"]).status_code==404
    assert 2 in verify(path)["schema_versions"]


def test_readiness_metrics_are_admin_only(client,headers):
    ready=client.get("/health/ready")
    assert ready.status_code==200
    assert ready.json()["status"]=="ready"
    client.get(url("/codes"),headers=headers["lawyer"])
    assert client.get(url("/admin/metrics"),headers=headers["user"]).status_code==403
    metrics=client.get(url("/admin/metrics"),headers=headers["superadmin"])
    assert metrics.status_code==200,metrics.text
    assert isinstance(metrics.json()["metrics"],dict)


def test_standalone_normative_act_publication_archive_and_stable_id(client,headers):
    h={**headers["superadmin"],"Idempotency-Key":"act-create-1"}
    body={"jurisdiction_id":"city","title":"Муниципальное постановление",
          "document_type":"Постановление","issuer":"Город Лос-Сантос",
          "text_rich":"<p>Текст акта</p><script>alert(1)</script>","confirm":True}
    missing=client.post(url("/library/acts"),headers=headers["superadmin"],json={
        **body,"confirm":False})
    assert missing.status_code==422
    created=client.post(url("/library/acts"),headers=h,json=body)
    assert created.status_code==200,created.text
    aid=created.json()["id"]
    repeated=client.post(url("/library/acts"),headers=h,json=body)
    assert repeated.json()==created.json()
    listing=client.get(url("/library/acts?jurisdiction=city"),headers=headers["user"])
    assert listing.status_code==200
    assert aid in [a["id"] for a in listing.json()]
    article=client.get(url("/library/acts/"+aid),headers=headers["user"])
    assert article.status_code==200
    assert "script" not in article.json()["text_rich"]
    preview=client.post(url("/library/acts/"+aid+"/archive/preview"),
                        headers=headers["superadmin"])
    assert preview.status_code==200
    denied=client.post(url("/library/acts/"+aid+"/archive"),
        headers={**headers["superadmin"],"Idempotency-Key":"act-archive-no"},
        json={"confirm":True,"preview_hash":"wrong"})
    assert denied.status_code==409
    archived=client.post(url("/library/acts/"+aid+"/archive"),
        headers={**headers["superadmin"],"Idempotency-Key":"act-archive-yes"},
        json={"confirm":True,"preview_hash":preview.json()["preview_hash"]})
    assert archived.status_code==200
    assert aid not in [a["id"] for a in client.get(url("/library/acts"),
                   headers=headers["lawyer"]).json()]
    assert client.get(url("/library/acts/"+aid),headers=headers["user"]).status_code==403
    assert client.get(url("/library/acts/"+aid),headers=headers["lawyer"]).status_code==200
    restored=client.post(url("/library/acts/"+aid+"/restore"),
        headers={**headers["superadmin"],"Idempotency-Key":"act-restore-yes"},
        json={"confirm":True})
    assert restored.status_code==200,restored.text
    assert restored.json()["id"]==aid
    assert client.get(url("/library/acts/"+aid),headers=headers["user"]).status_code==200


def test_generic_oidc_pkce_exchange_uses_fixed_configured_provider(client,monkeypatch):
    import json
    import app.auth as auth
    monkeypatch.setattr(auth,"AUTH_MODE","oidc")
    for key,value in (
        ("OIDC_AUTHORIZATION_URL","https://identity.example.org/oauth2/authorize"),
        ("OIDC_TOKEN_URL","https://identity.example.org/oauth2/token"),
        ("OIDC_CLIENT_ID","library-client"),
        ("OIDC_REDIRECT_URI","https://library.example.org/"),
    ):
        monkeypatch.setenv(key,value)
    cfg=client.get(url("/auth/config"))
    assert cfg.status_code==200,cfg.text
    assert cfg.json()["mode"]=="oidc"
    assert "token_url" not in cfg.json()
    invalid=client.post(url("/auth/token"),json={"code":"abc","code_verifier":"short"})
    assert invalid.status_code==422
    captured={}
    class FakeResponse:
        status=200
        def read(self,n=0):
            return json.dumps({"access_token":"jwt-access-token","expires_in":1800}).encode()
        def __enter__(self):return self
        def __exit__(self,*args):pass
    def fake_urlopen(req,timeout=0):
        captured["url"]=req.full_url
        captured["body"]=req.data.decode()
        return FakeResponse()
    monkeypatch.setattr(auth,"urlopen",fake_urlopen)
    ok=client.post(url("/auth/token"),json={
        "code":"sample-code","code_verifier":"a"*43,
        "redirect_uri":"https://evil.example/changed"})
    assert ok.status_code==200,ok.text
    assert ok.json()=={"access_token":"jwt-access-token",
                       "token_type":"Bearer","expires_in":1800}
    assert captured["url"]=="https://identity.example.org/oauth2/token"
    assert "https%3A%2F%2Flibrary.example.org%2F" in captured["body"]
    assert "evil.example" not in captured["body"]


def test_external_published_document_is_not_law_without_classification(client,headers):
    pub=client.post(url("/portal-publications"),headers=headers["publisher"],json={
        "portal_id":"government","jurisdiction_id":"state",
        "title":"Документ исполнительной власти",
        "document_type":"Постановление","issuer":"Правительство штата",
        "text_rich":"<p>Текст документа</p>"})
    assert pub.status_code==200,pub.text
    published=client.post(url("/portal-publications/"+pub.json()["id"]+"/publish"),
        headers={**headers["publisher"],"Idempotency-Key":"outside-law-publish"},
        json={"confirm":True})
    assert published.status_code==200,published.text
    aid=pub.json()["id"]
    assert aid not in [x["id"] for x in client.get(url("/library/acts"),
                     headers=headers["user"]).json()]
    assert client.get(url("/library/acts/"+aid),headers=headers["lawyer"]).status_code==404
    classified=client.post(url("/library/acts/"+aid+"/classify"),
        headers={**headers["superadmin"],"Idempotency-Key":"classify-official"},
        json={"confirm":True})
    assert classified.status_code==200,classified.text
    assert aid in [x["id"] for x in client.get(url("/library/acts"),
                    headers=headers["user"]).json()]


def test_disabled_relation_can_be_selectively_restored_only_if_compatible(client,headers,admin):
    h=headers["superadmin"]
    source=admin("831",cats=["accusation"],crime="felony")
    alternative=admin("832",cats=["accusation"],crime="misdemeanor")
    punishment=admin("833",sanctions={"fine_min_usd":0,"fine_max_usd":100})
    kind="accusation_punishment"
    relation=client.post(url("/relations"),headers=h,json={
        "type_id":kind,"source_id":source["id"],"target_id":punishment["id"]})
    assert relation.status_code==200,relation.text
    rid=relation.json()["id"]
    removed=client.post(url("/relations/"+rid+"/deactivate"),headers=h,
                        json={"reason":"Редакторское решение"})
    assert removed.status_code==200,removed.text
    items=client.get(url("/articles/"+source["id"]+"/relations/restore-candidates"),headers=h)
    assert items.status_code==200,items.text
    plan=next(x for x in items.json() if x["relation_id"]==rid)
    assert plan["restorable"] is True,plan
    assert client.get(url("/articles/"+source["id"]+"/relations/restore-candidates"),
                      headers=headers["lawyer"]).status_code==403
    body={"confirm":True,"preview_hash":plan["preview_hash"]}
    bad=client.post(url("/relations/"+rid+"/restore"),
                    headers={**h,"Idempotency-Key":"relation-stale"},json={
                    **body,"preview_hash":"bad"})
    assert bad.status_code==409
    created=client.post(url("/relations"),headers=h,json={
        "type_id":kind,"source_id":alternative["id"],"target_id":punishment["id"]})
    assert created.status_code==200,created.text
    stale=client.post(url("/relations/"+rid+"/restore"),
                    headers={**h,"Idempotency-Key":"relation-collision"},json=body)
    assert stale.status_code==409,stale.text
    plan=client.get(url("/articles/"+source["id"]+"/relations/restore-candidates"),
                    headers=h).json()[0]
    assert not plan["restorable"]
    assert plan["conflicts"]
    client.post(url("/relations/"+created.json()["id"]+"/deactivate"),headers=h,json={})
    plan=client.get(url("/articles/"+source["id"]+"/relations/restore-candidates"),
                    headers=h).json()[0]
    assert plan["restorable"]
    before=client.get(url("/articles/"+source["id"]),headers=h).json()["revision_id"]
    revived=client.post(url("/relations/"+rid+"/restore"),
        headers={**h,"Idempotency-Key":"relation-reactivate"},
        json={"confirm":True,"preview_hash":plan["preview_hash"]})
    assert revived.status_code==200,revived.text
    assert revived.json()["active"] is True
    after=client.get(url("/articles/"+source["id"]),headers=h).json()["revision_id"]
    assert before!=after
    assert len(client.get(url("/articles/"+source["id"]+"/relations"),
                          headers=headers["user"]).json()["items"])==1
