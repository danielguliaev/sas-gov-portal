"""Browser regression for cursor pagination, a transient network failure and deep links."""
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest


@pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1",
                    reason="Chromium test is enabled by RUN_BROWSER_TESTS=1")
def test_reader_retry_and_deep_article_link(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port=sock.getsockname()[1]
    base="http://127.0.0.1:"+str(port)
    env=os.environ.copy()
    env["DATABASE_PATH"]=str(tmp_path/"reader-large.db")
    env["APP_ENV"]="development"
    env["AUTH_MODE"]="demo"
    server=subprocess.Popen([
        sys.executable,"-m","uvicorn","app.main:app","--host","127.0.0.1",
        "--port",str(port),"--log-level","error"
    ],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
    try:
        for attempt in range(90):
            try:
                with urlopen(base+"/health",timeout=1) as reply:
                    if reply.status==200:break
            except Exception:
                time.sleep(.1)
        else:
            raise AssertionError("Reader server did not start")
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            context=browser.new_context(viewport={"width":1280,"height":800})
            page=context.new_page()
            page_errors=[]
            page.on("pageerror",lambda err:page_errors.append(str(err)))
            root="/api/v1/articles"
            created=[]
            for i in range(14):
                result=page.request.post(base+root,headers={"X-Demo-User":"superadmin"},
                    data={
                        "code_id":"demo-penal","parent_id":"demo-intro",
                        "number":str(500+i),"title":"Норма для пагинации "+str(i),
                        "categories":["definition"],
                        "text_rich":"<p>"+("Развёрнутое положение закона. "*25)+"</p>"
                    })
                assert result.status==200,result.text()
                created.append(result.json()["id"])
            failed=[]
            def temporary_failure(route):
                if "cursor=" in route.request.url and not failed:
                    failed.append(route.request.url)
                    route.abort("failed")
                else:
                    route.continue_()
            page.route("**/api/v1/nodes/demo-intro/articles?*",temporary_failure)
            code_requests=[]
            page.on("request",lambda req: code_requests.append(req.url)
                    if "/api/v1/codes/demo-penal/tree" in req.url else None)
            page.goto(base+"/")
            page.locator("#roleSelect").select_option("user")
            page.goto(base+"/codes/demo-penal/nodes/demo-intro")
            expect(page.locator(".article-card")).to_have_count(12,timeout=15000)
            assert code_requests and all("include_nodes=false" in url
                                         for url in code_requests),code_requests
            expect(page.locator("#codeNav .code-tree-node")).not_to_have_count(0)
            original_ids=page.locator(".article-card").evaluate_all(
                "(nodes) => nodes.map(n => n.dataset.articleId)")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            expect(page.locator(".load-status")).to_contain_text(
                "Не удалось загрузить статьи",timeout=10000)
            assert len(failed)==1
            # Failure does not delete or replace the 12 already rendered articles.
            assert page.locator(".article-card").evaluate_all(
                "(nodes) => nodes.map(n => n.dataset.articleId)") == original_ids
            page.locator(".load-status button").click()
            expect(page.locator(".article-card")).to_have_count(15,timeout=10000)
            actual_ids=page.locator(".article-card").evaluate_all(
                "(nodes) => nodes.map(n => n.dataset.articleId)")
            assert len(actual_ids)==len(set(actual_ids))
            assert created[-1] in actual_ids

            # Opening a deep permalink should seek directly to the article.
            page.goto(base+"/articles/"+created[-1])
            expect(page.locator("#card-"+created[-1])).to_be_visible(timeout=10000)
            expect(page.locator("#card-"+created[-1])).to_contain_text(
                "Норма для пагинации 13")
            assert page.locator(".tree-code-name").count()==1

            # REQ-068: collapse remains reversible and persists in the tab session.
            toggle=page.locator("#sidebarToggle")
            expect(toggle).to_be_visible()
            expect(toggle).to_have_attribute("aria-expanded","true")
            toggle.click()
            expect(page.locator("#layout")).to_have_class("layout sidebar-collapsed")
            expect(toggle).to_have_attribute("aria-expanded","false")
            page.reload()
            expect(page.locator("#layout")).to_have_class("layout sidebar-collapsed")
            page.locator("#sidebarToggle").click()
            expect(page.locator("#layout")).to_have_class("layout")

            # REQ-075/076: a return to a large chapter must load enough cards
            # to restore its own history position without changing the URL.
            page.goto(base+"/codes/demo-penal/nodes/demo-intro")
            expect(page.locator(".article-card")).to_have_count(12,timeout=15000)
            page.evaluate("window.scrollTo(0, 1800)")
            page.wait_for_function("window.scrollY >= 1300")
            saved=page.evaluate("window.scrollY")
            # Avoid Playwright's implicit scroll-to-element: a real click on a
            # visible card must preserve the reader's last viewport position.
            page.locator(".article-card").nth(4).locator("a[href^='/articles/']").first.evaluate(
                "(link) => link.click()")
            expect(page).to_have_url(base+"/articles/"+created[3])
            expect(page.locator("#card-"+created[3])).to_be_visible()
            page.go_back(wait_until="domcontentloaded")
            expect(page).to_have_url(base+"/codes/demo-penal/nodes/demo-intro")
            page.wait_for_function("""(saved) =>
                Math.abs(window.scrollY - saved) < 70""",arg=saved,timeout=15000)
            page.go_forward(wait_until="domcontentloaded")
            expect(page).to_have_url(base+"/articles/"+created[3])
            expect(page.locator("#card-"+created[3])).to_be_visible()

            # AT-12: publishing a new revision from a second browser tab must
            # never replace the open reader's text until an explicit reload.
            article_id=created[3]
            existing=page.locator("#card-"+article_id+" .article-text")
            expect(existing).to_contain_text("Развёрнутое положение закона")
            writer=page.context.new_page()
            current=writer.request.get(base+root+"/"+article_id,
                                       headers={"X-Demo-User":"superadmin"})
            assert current.status==200,current.text()
            current_revision=current.json()["revision_id"]
            editor_session="other-browser-tab"
            acquired=writer.request.post(base+root+"/"+article_id+"/edit-lock",
                headers={"X-Demo-User":"superadmin"},
                data={"editor_session_id":editor_session})
            assert acquired.status==200,acquired.text()
            published=writer.request.post(base+root+"/"+article_id+"/revisions",
                headers={"X-Demo-User":"superadmin"},data={
                    "base_revision_id":current_revision,
                    "lock_token":acquired.json()["token"],
                    "editor_session_id":editor_session,
                    "text_rich":"<p>Новая редакция из второй вкладки.</p>",
                    "reason":"Новая публикация"})
            assert published.status==200,published.text()
            # The old DOM remains untouched; no background freshness poll.
            expect(existing).to_contain_text("Развёрнутое положение закона")
            assert "Новая редакция из второй вкладки" not in existing.inner_text()
            page.reload()
            expect(page.locator("#card-"+article_id+" .article-text")).to_contain_text(
                "Новая редакция из второй вкладки",timeout=15000)
            writer.close()
            assert not page_errors,page_errors
            browser.close()
    finally:
        server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
