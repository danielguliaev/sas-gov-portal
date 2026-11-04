"""Real-browser smoke test of the public-facing law reader (opt in with RUN_BROWSER_TESTS=1)."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest


@pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1",
                    reason="Browser tests require RUN_BROWSER_TESTS=1 and installed Chromium")
def test_legislation_portal_desktop_and_mobile(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base_url = "http://127.0.0.1:" + str(port)
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(tmp_path / "browser.db")
    env["APP_ENV"] = "development"
    env["AUTH_MODE"] = "demo"

    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "error"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    try:
        for attempt in range(80):
            try:
                with urlopen(base_url + "/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(.1)
        else:
            raise AssertionError("Web server failed to start for browser test")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width":1440, "height":900}, device_scale_factor=1)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base_url + "/", wait_until="domcontentloaded")
            expect(page.locator(".law-hero h1")).to_contain_text("Законодательство штата")
            expect(page.locator(".hero-guest")).to_be_visible()

            # The test role is explicitly chosen in the local environment.
            page.locator("#roleSelect").select_option("user")
            expect(page.locator(".portal-metric strong").first).to_have_text("1", timeout=15000)
            expect(page.locator(".jurisdiction-card")).to_have_count(3)
            captures = Path("test-results")
            captures.mkdir(exist_ok=True)
            page.screenshot(path=str(captures / "law-portal-desktop.png"), full_page=True)

            page.locator(".header-nav a[href='/laws']").click()
            expect(page.locator(".catalog-grid .law-code-card")).to_have_count(1)
            expect(page.locator(".catalog-summary")).to_contain_text("Найдено кодексов: 1")
            page.locator(".law-code-card").first.click()
            expect(page.locator(".section-tile")).to_have_count(1)
            page.locator(".section-tile").first.click()
            expect(page.locator(".article-card")).to_have_count(1)
            expect(page.locator(".article-card")).to_contain_text("Демонстрационная норма")
            page.screenshot(path=str(captures / "law-portal-reader.png"), full_page=True)

            # Search form navigates to an addressable search page.
            page.locator("a.brand").click()
            page.locator(".hero-search input").fill("Демонстрационная")
            page.locator(".hero-search button").click()
            expect(page).to_have_url(base_url + "/search?q=%D0%94%D0%B5%D0%BC%D0%BE%D0%BD%D1%81%D1%82%D1%80%D0%B0%D1%86%D0%B8%D0%BE%D0%BD%D0%BD%D0%B0%D1%8F")
            expect(page.locator(".search-results-head")).to_contain_text("Найдено: 1")
            expect(page.locator(".search-result")).to_have_count(1)
            page.locator(".search-result").first.click()
            expect(page.locator(".article-card")).to_be_visible()

            # Mobile menu is functional, not merely responsive artwork.
            page.set_viewport_size({"width":390, "height":820})
            page.locator("a.brand").click()
            expect(page.locator(".law-hero")).to_be_visible()
            page.locator("#menuToggle").click()
            expect(page.locator("#layout")).to_have_class("layout menu-open")
            expect(page.locator("#menuToggle")).to_have_attribute("aria-expanded", "true")
            page.locator("#navBackdrop").click(position={"x":340,"y":120})
            expect(page.locator("#layout")).to_have_class("layout")
            expect(page.locator("#menuToggle")).to_have_attribute("aria-expanded", "false")
            page.screenshot(path=str(captures / "law-portal-mobile.png"), full_page=True)

            assert not errors, "JavaScript errors: " + repr(errors)
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
