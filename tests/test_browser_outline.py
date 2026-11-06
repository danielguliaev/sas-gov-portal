"""Browser acceptance for editable code descriptions and mixed root ordering."""
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest


@pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1",
                    reason="Browser tests require RUN_BROWSER_TESTS=1")
def test_mixed_outline_and_admin_reorder(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = "http://127.0.0.1:" + str(port)
    env = os.environ.copy()
    env.update({"DATABASE_PATH": str(tmp_path / "mixed-outline.sqlite"),
                "APP_ENV": "development", "AUTH_MODE": "demo"})
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try:
                with urlopen(base + "/health", timeout=1) as reply:
                    if reply.status == 200:
                        break
            except Exception:
                time.sleep(.1)
        else:
            raise AssertionError("Mixed outline test server did not start")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1360, "height": 850})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/")
            page.locator("#roleSelect").select_option("user")
            page.goto(base + "/codes/PEN", wait_until="domcontentloaded")
            expect(page.locator(".section-tile")).to_have_count(1)
            expect(page.locator(".section-heading")).not_to_be_visible()
            expect(page.locator(".reader-empty")).to_have_count(0)
            expect(page.locator(".eyebrow").first).to_have_text("PEN")
            assert page.get_by_text("Структура документа").count() == 0

            page.locator("#roleSelect").select_option("lawyer")
            page.goto(base + "/codes/PEN", wait_until="domcontentloaded")
            expect(page.locator(".section-heading")).not_to_be_visible()

            # The existing code root contains a part. Insert an introduction
            # before the part and a concluding article after it.
            first = page.request.post(base + "/api/v1/articles",
                headers={"X-Demo-User": "superadmin"}, data={
                    "code_id": "PEN", "number": "850",
                    "title": "Вводная статья", "categories": ["definition"],
                    "insert_at": 0, "text_rich": "<p>Введение.</p>"})
            assert first.ok, first.text()
            last = page.request.post(base + "/api/v1/articles",
                headers={"X-Demo-User": "superadmin"}, data={
                    "code_id": "PEN", "number": "851",
                    "title": "Заключительная статья", "categories": ["definition"],
                    "text_rich": "<p>Заключение.</p>"})
            assert last.ok, last.text()
            first_id, last_id = first.json()["id"], last.json()["id"]
            root = page.request.get(base + "/api/v1/codes/PEN/outline",
                                    headers={"X-Demo-User": "lawyer"}).json()
            assert [x["kind"] for x in root["items"]] == [
                "article", "node", "article"]

            page.goto(base + "/codes/PEN", wait_until="domcontentloaded")
            expect(page.locator(".reader-articles > .article-card")).to_have_count(2)
            expect(page.locator(".reader-articles > .outline-section-grid")).to_have_count(1)
            rendered = page.locator(".reader-articles > *").evaluate_all(
                """elements => elements.map(el =>
                    el.classList.contains('article-card') ?
                        el.dataset.articleId : 'section')""")
            assert rendered == [first_id, "section", last_id], rendered
            sidebar_order = page.locator(
                ".code-tree-root > .article-index, .code-tree-root > .code-tree-node"
            ).evaluate_all("""items => items.map(el =>
                el.classList.contains('article-index') ?
                    el.querySelector('a').dataset.articleId : 'section')""")
            assert sidebar_order == [first_id, "section", last_id], sidebar_order

            page.locator("#roleSelect").select_option("superadmin")
            page.goto(base + "/admin/codes", wait_until="domcontentloaded")
            card = page.locator(".admin-code-card").filter(has_text="Penal Code")
            card.locator(".code-metadata-editor summary").click()
            card.locator("textarea[name='description']").fill(
                "Положения об ответственности, преступлениях и наказаниях.")
            card.get_by_role("button", name="Сохранить сведения").click()
            page.goto(base + "/codes/PEN", wait_until="domcontentloaded")
            expect(page.locator(".lede")).to_have_text(
                "Положения об ответственности, преступлениях и наказаниях.")

            page.goto(base + "/admin/hierarchy?code=PEN&view=order",
                      wait_until="domcontentloaded")
            expect(page.locator("#hierarchySortGroup")).to_be_visible()
            expect(page.locator(".wb-sort-row")).to_have_count(3)
            page.locator(".wb-sort-row").nth(2).get_by_role(
                "button", name="Поднять", exact=False).click()
            page.get_by_role("button", name="Сохранить общий порядок").click()
            expect(page.locator(".toast.visible")).to_contain_text(
                "Общий порядок", timeout=10000)
            page.goto(base + "/codes/PEN", wait_until="domcontentloaded")
            expect(page.locator(".reader-articles > .article-card")).to_have_count(2)
            rendered = page.locator(".reader-articles > *").evaluate_all(
                """elements => elements.map(el =>
                    el.classList.contains('article-card') ?
                        el.dataset.articleId : 'section')""")
            assert rendered == [first_id, last_id, "section"], rendered
            assert not errors, errors
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
