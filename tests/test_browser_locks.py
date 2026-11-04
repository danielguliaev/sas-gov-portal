"""Concurrent editor, dirty-exit and lock-expiration acceptance in Chromium."""
import os
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.request import urlopen
import pytest


@pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1",
                    reason="Requires Chromium in browser CI")
def test_two_browser_tabs_respect_editor_locks_and_dirty_exit(tmp_path):
    from playwright.sync_api import expect,sync_playwright

    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
    base="http://127.0.0.1:"+str(port)
    db=tmp_path/"editor-tabs.sqlite"
    env=os.environ.copy()
    env.update({"DATABASE_PATH":str(db),"APP_ENV":"development","AUTH_MODE":"demo"})
    server=subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app",
        "--host","127.0.0.1","--port",str(port),"--log-level","error"],
        env=env,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
    try:
        for i in range(100):
            try:
                with urlopen(base+"/health",timeout=1) as response:
                    if response.status==200:break
            except Exception:time.sleep(.1)
        else:raise AssertionError("Server not started")
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            context=browser.new_context(viewport={"width":1360,"height":800})
            first=context.new_page()
            created=first.request.post(base+"/api/v1/articles",
                headers={"X-Demo-User":"superadmin"},data={
                    "code_id":"demo-penal","number":"990","title":"Статья о блокировках",
                    "categories":["definition"],"text_rich":"<p>Первая редакция.</p>"})
            assert created.status==200,created.text()
            article_id=created.json()["id"]
            href=base+"/articles/"+article_id
            first.goto(base+"/")
            first.locator("#roleSelect").select_option("superadmin")
            first.goto(href)
            own=first.locator("#card-"+article_id)
            expect(own).to_be_visible()
            own.get_by_role("button",name="Редактировать").click()
            expect(first.locator("#editorDialog")).to_be_visible()
            first.locator("#articleForm textarea[name=text_rich]").fill("<p>Несохранённый текст.</p>")

            second=context.new_page()
            second.on("dialog",lambda d:d.dismiss())
            second.goto(base+"/")
            second.locator("#roleSelect").select_option("superadmin")
            second.goto(href)
            expect(second.locator("#card-"+article_id)).to_be_visible()
            second.locator("#card-"+article_id).get_by_role(
                "button",name="Редактировать").click()
            expect(second.locator("#editorDialog")).not_to_be_visible()
            expect(first.locator("#editorDialog")).to_be_visible()

            # AT-16: cancelling the exit confirmation retains all unsaved work.
            first.once("dialog",lambda d:d.dismiss())
            first.locator("#closeEditor").click()
            expect(first.locator("#editorDialog")).to_be_visible()
            expect(first.locator("#articleForm textarea[name=text_rich]")).to_have_value(
                "<p>Несохранённый текст.</p>")
            first.once("dialog",lambda d:d.accept())
            first.locator("#closeEditor").click()
            expect(first.locator("#editorDialog")).not_to_be_visible()
            response=first.request.get(base+"/api/v1/articles/"+article_id,
                headers={"X-Demo-User":"superadmin"})
            assert response.status==200 and response.json()["text_rich"]=="<p>Первая редакция.</p>"

            # After releasing the lock, the second tab obtains its own session.
            second.locator("#card-"+article_id).get_by_role(
                "button",name="Редактировать").click()
            expect(second.locator("#editorDialog")).to_be_visible()
            modified="<p>Новая редакция сохранена со второй вкладки.</p>"
            second.locator("#articleForm textarea[name=text_rich]").fill(modified)

            # AT-14: a lock that expired while the editor was open rejects save.
            with sqlite3.connect(db,timeout=5) as c:
                c.execute("UPDATE edit_locks SET expires_at=? WHERE article_id=?",
                    ("2000-01-01T00:00:00+00:00",article_id))
                c.commit()
            second.locator("#saveEditor").click()
            expect(second.locator("#reacquireLock")).to_be_visible()
            expect(second.locator("#articleForm textarea[name=text_rich]")).to_have_value(modified)
            second.locator("#reacquireLock").click()
            expect(second.locator("#reacquireLock")).not_to_be_visible()
            second.locator("#saveEditor").click()
            expect(second.locator("#editorStatus")).to_contain_text("Редакция сохранена")
            expect(second.locator("#card-"+article_id+" .article-text")).to_contain_text(
                "Новая редакция сохранена",timeout=10000)
            refreshed=second.request.get(base+"/api/v1/articles/"+article_id,
                headers={"X-Demo-User":"superadmin"})
            assert refreshed.status==200 and "Новая редакция сохранена" in refreshed.json()["text_rich"]
            browser.close()
    finally:
        server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill();server.wait(timeout=10)
