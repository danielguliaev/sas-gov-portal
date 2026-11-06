"""Reader sidebar and portal-scope regression tests."""
import app.core as core


def api(path):
    return "/api/v1" + path


def test_code_root_articles_have_a_separate_lightweight_index(client, headers, admin):
    first = admin("11", title="Основная статья", parent=None)
    second = admin("12", title="Следующая статья", parent=None)
    unauth = client.get(api("/codes/PEN/root/article-index"))
    assert unauth.status_code == 401

    r = client.get(api("/codes/PEN/root/article-index?limit=1"),
                   headers=headers["user"])
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["has_more"] is True
    assert data["items"][0]["id"] == first["id"]
    assert set(data["items"][0]) == {"id", "number", "title"}
    assert "text_rich" not in data["items"][0]

    follow = client.get(api("/codes/PEN/root/article-index"),
                        params={"limit": 1, "cursor": data["next_cursor"]},
                        headers=headers["user"])
    assert follow.status_code == 200
    assert [item["id"] for item in follow.json()["items"]] == [second["id"]]
    assert follow.json()["has_more"] is False

    child = client.get(api("/nodes/demo-intro/article-index"), headers=headers["user"])
    assert child.status_code == 200
    assert any(a["id"] == "demo-article-1" for a in child.json()["items"])
    assert all(a["id"] not in {first["id"], second["id"]} for a in child.json()["items"])


def test_library_notifications_are_isolated_from_other_sites(client, headers):
    with core.transaction() as c:
        core.notify(c, "demo-lawyer", "library", "test-library-change",
                    "Обновление статьи", "demo-article-1")
        core.notify(c, "demo-lawyer", "government", "test-government-pub",
                    "Публикация правительства", "demo-article-1")
        ids = dict(c.execute(
            "SELECT portal_id,id FROM notifications WHERE recipient_id='demo-lawyer' "
            "AND portal_id IN ('library','government')").fetchall())
    reader = headers["lawyer"]
    shown = client.get(api("/me/notifications"), headers=reader)
    assert shown.status_code == 200
    assert {item["portal_id"] for item in shown.json()} == {"library"}
    assert {item["title"] for item in shown.json()} == {"Обновление статьи"}

    external = client.get(api("/me/notifications?portal_id=government"), headers=reader)
    assert external.status_code == 403
    assert client.post(api("/me/notifications/" + ids["government"] + "/read"),
                       headers=reader).status_code == 404
    marked = client.post(api("/me/notifications/" + ids["library"] + "/read"),
                         headers=reader)
    assert marked.status_code == 200
    assert client.get(api("/me/notifications"), headers=reader).json()[0]["is_read"] == 1


def test_public_html_has_new_brand_and_three_roles(client):
    response = client.get("/codes/PEN")
    assert response.status_code == 200
    html = response.text
    assert "Правовая библиотека штата Сан Андреас" in html
    assert "Copyright © 2026 State of San Andreas" in html
    assert '<div class="footer-identity"><span>Правовая библиотека штата Сан Андреас</span>' in html
    assert 'class="seal"' not in html
    assert 'class="demo-banner"' not in html
    assert "Документация API" not in html
    assert "Демонстрационный портал" not in html
    assert 'value="user"' in html
    assert 'value="lawyer"' in html
    assert 'value="superadmin"' in html
    for other_role in ("editor", "publisher", "legislator"):
        assert 'value="' + other_role + '"' not in html
    for other_page in ("/laws", "/search"):
        assert client.get(other_page).status_code == 200
