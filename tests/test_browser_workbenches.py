"""Chromium acceptance for administrator-friendly hierarchy and relations pages."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen
import pytest


@pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1",
                    reason="Chromium is enabled only during browser CI")
def test_admin_task_workbenches_and_russian_offense_classes(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        port=sock.getsockname()[1]
    base="http://127.0.0.1:"+str(port)
    env=os.environ.copy()
    env.update({"DATABASE_PATH":str(tmp_path/"workbench.sqlite"),
                "APP_ENV":"development","AUTH_MODE":"demo"})
    server=subprocess.Popen([sys.executable,"-m","uvicorn","app.main:app",
        "--host","127.0.0.1","--port",str(port),"--log-level","error"],
        env=env,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
    try:
        for attempt in range(100):
            try:
                with urlopen(base+"/health",timeout=1) as response:
                    if response.status==200:break
            except Exception:
                time.sleep(.1)
        else:raise AssertionError("Server did not start")

        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(viewport={"width":1440,"height":900})
            errors=[]
            page.on("pageerror",lambda error: errors.append(str(error)))
            page.goto(base+"/")
            page.locator("#roleSelect").select_option("superadmin")
            page.goto(base+"/admin/hierarchy?code=demo-penal")
            expect(page.locator(".admin-hierarchy-workbench")).to_be_visible()
            expect(page.locator(".wb-steps .wb-step")).to_have_count(3)
            expect(page.locator(".wb-picker-item")).not_to_have_count(0)
            expect(page.locator(".wb-editor h4")).to_be_visible()
            page.screenshot(path="test-results/law-library-hierarchy-workbench.png",full_page=True)

            page.locator("#hierarchyAddLevel").click()
            expect(page.locator("#hierarchyLevelCreator")).to_be_visible()
            page.locator("#hierarchyLevelCreator input").first.fill("Подглава")
            page.locator("#hierarchyLevelCreator button[type=submit]").click()
            expect(page.locator(".wb-picker")).to_contain_text("Подглава",timeout=10000)
            page.locator(".wb-steps [data-step=move]").click()
            expect(page.locator("#hierarchyMovingNode")).to_be_visible()
            expect(page.locator("#hierarchyMoveDestination")).to_be_visible()
            page.locator(".wb-steps [data-step=order]").click()
            expect(page.locator("#hierarchySortGroup")).to_be_visible()
            page.set_viewport_size({"width":900,"height":800})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")
            page.screenshot(path="test-results/law-library-hierarchy-workbench-narrow.png",full_page=True)

            page.set_viewport_size({"width":1440,"height":900})
            page.goto(base+"/admin/relations")
            expect(page.locator(".admin-relations-workbench")).to_be_visible()
            expect(page.locator(".wb-steps .wb-step")).to_have_count(3)
            expect(page.locator(".wb-picker-item")).not_to_have_count(0)
            page.screenshot(path="test-results/law-library-relations-workbench.png",full_page=True)
            page.locator("#relationsAddType").click()
            expect(page.locator("#relationTypeCreator")).to_be_visible()
            page.locator("#relationTypeCreator input[type=text]").first.fill("Уточняет")
            page.locator("#relationTypeCreator button[type=submit]").click()
            expect(page.locator(".wb-picker")).to_contain_text("Уточняет",timeout=10000)
            page.locator(".wb-steps [data-step=assign]").click()
            expect(page.locator("#relationAssignType")).to_be_visible()
            expect(page.locator(".wb-finder")).to_have_count(2)
            page.locator(".wb-steps [data-step=review]").click()
            expect(page.locator("#relationReviewArticleId")).to_be_visible()
            page.locator("#relationReviewArticleId").fill("demo-article-1")
            page.get_by_role("button",name="Показать связи").click()
            expect(page.locator(".wb-result-section")).to_have_count(2)
            page.set_viewport_size({"width":900,"height":800})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")

            page.locator("#roleSelect").select_option("lawyer")
            page.goto(base+"/search")
            expect(page.locator("select[name=criminal_class]")).to_be_visible()
            assert page.locator("select[name=criminal_class] option").all_text_contents()[-3:] == [
                "Тяжкое преступление","Проступок","Нарушение"]
            page.locator("#roleSelect").select_option("superadmin")
            page.goto(base+"/codes/demo-penal")
            page.get_by_role("button",name="Создать статью").click()
            labels=page.locator("#editorDialog select[name=criminal_class] option").all_text_contents()
            assert labels[-3:]==["Тяжкое преступление","Проступок","Нарушение"]
            assert not errors,errors
            browser.close()
    finally:
        server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
