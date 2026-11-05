"""Contract tests for the legislative reader interface and catalog overview."""


def path(value):
    return "/api/v1" + value


def test_portal_page_and_assets_are_served(client):
    for route in ("/", "/laws", "/codes/demo-penal", "/articles/demo-article-1", "/search"):
        response = client.get(route)
        assert response.status_code == 200, (route, response.text)
        assert 'lang="ru"' in response.text
        assert 'href="/static/laws.css"' in response.text
        assert 'id="navBackdrop"' in response.text
        assert 'id="legalResourcesMenu"' in response.text
        assert 'id="otherResourcesMenu"' in response.text
        assert 'id="globalNav"' in response.text
        assert 'id="otherResourcesNav"' in response.text
        assert 'СОДЕРЖАНИЕ КОДЕКСА' in response.text
        assert 'href="/laws"' in response.text
    style = client.get("/static/laws.css")
    assert style.status_code == 200
    assert ".law-hero" in style.text
    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert "async function lawCatalog(" in script.text
    assert "async function searchPage(" in script.text
    assert '"article-number", "§ " + a.number' in script.text
    assert 'otherResourcesNav.replaceChildren()' in script.text


def test_codes_counts_and_overview_show_only_active_materials(client, headers, admin):
    auth = headers["lawyer"]
    assert client.get(path("/library/overview")).status_code == 401
    before = client.get(path("/library/overview"), headers=auth)
    assert before.status_code == 200, before.text
    metrics = before.json()["totals"]
    assert metrics["code_count"] >= 1
    assert metrics["article_count"] >= 1
    assert metrics["section_count"] >= 1
    code = next(item for item in client.get(path("/codes"),headers=auth).json()
                if item["id"] == "demo-penal")
    assert code["article_count"] >= 1
    assert code["section_count"] >= 1

    added = admin("1150",title="Новая демонстрационная статья")
    after = client.get(path("/library/overview"), headers=auth).json()
    assert after["totals"]["article_count"] == metrics["article_count"] + 1
    assert any(item["id"] == added["id"] for item in after["recent_revisions"])
    code_after = next(item for item in client.get(path("/codes"), headers=auth).json()
                      if item["id"] == "demo-penal")
    assert code_after["article_count"] == code["article_count"] + 1

    preview = client.post(path("/archive/preview"),
                          headers=headers["superadmin"],json={"article_id":added["id"]})
    assert preview.status_code == 200, preview.text
    archived = client.post(path("/archive-batches"),
             headers=dict(headers["superadmin"], **{"Idempotency-Key":"overview-archive"}),
             json={"article_id":added["id"],"preview_hash":preview.json()["preview_hash"],
                   "confirm":True})
    assert archived.status_code == 200, archived.text
    without = client.get(path("/library/overview"),headers=auth).json()
    assert without["totals"]["article_count"] == metrics["article_count"]
    assert not any(item["id"] == added["id"] for item in without["recent_revisions"])


def test_filter_catalog_jurisdiction_and_normalized_article_number(client, headers, admin):
    state = client.get(path("/codes?jurisdiction=state"),headers=headers["user"])
    assert state.status_code == 200, state.text
    assert all(code["jurisdiction_id"] == "state" for code in state.json())
    assert client.get(path("/codes?jurisdiction=city"),headers=headers["user"]).json() == []

    a = admin("120.2")
    query = client.get(path("/search"),headers=headers["lawyer"],
                       params={"number":"120-2"})
    assert query.status_code == 200, query.text
    assert a["id"] in {x["id"] for x in query.json()["items"]}
