"""Browser-level regression tests for the Today filters (real Chromium via Playwright).

Regression (Oct 23 2026): a leftover Area (e.g. "family", from a filtered URL or Chrome's form-state
restore) stayed selected when the user picked a Bot, so every bot except Family showed an empty list.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from briefs.config import find_bot
from briefs.db import connect

sync_api = pytest.importorskip("playwright.sync_api")
CHROME = "/usr/bin/google-chrome"
if not os.path.exists(CHROME):
    pytest.skip("google-chrome not installed", allow_module_level=True)

TS_HOST = "grok-bot-box.example-tailnet.ts.net"
# Same headers Tailscale Serve adds in front of the app.
TS_HEADERS = {
    "Tailscale-User-Login": "user@example.com",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": TS_HOST,
    "X-Forwarded-For": "100.64.0.1",
}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server(imported_db: str, tmp_path: Path):
    conn = connect(imported_db)
    radar, family = find_bot("radar"), find_bot("family")
    conn.execute("UPDATE items SET area='code' WHERE bot_id=?", (radar.id,))
    conn.execute("UPDATE items SET area='family' WHERE bot_id=?", (family.id,))
    for i in range(3):
        conn.execute(
            "INSERT INTO items (bot_id, item_key, title, status, area) VALUES (?, ?, ?, 'done', 'code')",
            (radar.id, f"radar:closed{i}", f"Radar closed thing {i}"),
        )
    conn.execute(
        "INSERT INTO items (bot_id, item_key, title, status, area, due_at) VALUES (?, 'radar:due', 'Radar due thing', 'open', 'code', '2026-11-01')",
        (radar.id,),
    )
    conn.commit()
    conn.close()
    stub = tmp_path / "notify.sh"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    stub.chmod(0o755)
    port = _free_port()
    env = dict(os.environ, BRIEFS_DB=imported_db, BRIEFS_NOTIFY_CMD=str(stub))
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "briefs.web:create_app", "--factory", "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/healthz", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    yield base
    proc.terminate()
    proc.wait(timeout=10)


def _rows(page) -> int:
    return page.locator("#item-list .item-row").count()


def _settle(page) -> None:
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)


def test_filters_in_real_browser(server: str) -> None:
    with sync_api.sync_playwright() as p:
        br = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx = br.new_context(viewport={"width": 1280, "height": 900}, color_scheme="dark", extra_http_headers=TS_HEADERS)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        bad: list[str] = []
        page.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.status >= 400 and "favicon" not in r.url else None)

        # User case: page loaded with a leftover Area filter, then he picks a bot.
        page.goto(server + "/?area=family&status=all")
        _settle(page)
        assert page.locator("#filter-area").input_value() == "family"
        assert _rows(page) > 0
        page.select_option("#filter-bot", "radar")
        _settle(page)
        assert page.locator("#filter-area").input_value() == "", "picking a Bot must clear Area"
        assert "area=family" not in page.url and "bot=radar" in page.url
        radar_all = _rows(page)
        assert radar_all > 0
        assert page.locator("#section-radar").count() == 1

        page.select_option("#filter-status", "open")
        _settle(page)
        radar_open = _rows(page)
        assert 0 < radar_open < radar_all

        page.select_option("#filter-status", "closed")
        _settle(page)
        assert _rows(page) >= 3 and page.get_by_text("Radar closed thing 0").count() == 1
        page.select_option("#filter-status", "open")
        _settle(page)

        page.check("input[name=due]")
        _settle(page)
        assert _rows(page) >= 1 and page.get_by_text("Radar due thing").count() == 1
        assert _rows(page) < radar_open
        page.uncheck("input[name=due]")
        _settle(page)
        assert _rows(page) == radar_open

        # Another bot, then an Area (which clears Bot).
        page.select_option("#filter-bot", "family")
        _settle(page)
        family_rows = _rows(page)
        assert family_rows > 0 and page.locator("#section-family").count() == 1
        page.select_option("#filter-area", "code")
        _settle(page)
        assert page.locator("#filter-bot").input_value() == ""
        assert _rows(page) > 0 and page.locator("#section-radar").count() == 1

        # Back / forward keep list and controls in sync with the URL.
        page.go_back()
        _settle(page)
        assert "bot=family" in page.url
        assert page.locator("#filter-bot").input_value() == "family"
        assert _rows(page) == family_rows
        page.go_forward()
        _settle(page)
        assert page.locator("#filter-area").input_value() == "code"
        assert page.locator("#filter-bot").input_value() == ""

        # Reload of a filtered URL.
        page.goto(server + "/?bot=radar&status=open")
        _settle(page)
        page.reload()
        _settle(page)
        assert page.locator("#filter-bot").input_value() == "radar"
        assert _rows(page) == radar_open

        # A slug alias in the URL still selects the bot (Show all links used slugs).
        page.goto(server + "/?bot=project")
        _settle(page)
        assert page.locator("#filter-bot").input_value() == "project-bot"

        # An empty combination explains itself and offers Clear filters.
        page.goto(server + "/?bot=radar&area=family")
        _settle(page)
        assert _rows(page) == 0
        assert page.locator("#empty-state").count() == 1
        page.click("#clear-filters-btn")
        _settle(page)
        assert _rows(page) > 0

        assert not errors, errors
        assert not bad, bad
        br.close()


def test_row_details_accordion(server: str) -> None:
    """Oct 25 2026: several item details stayed open at once, overlapping rows. Now at most one is open."""
    with sync_api.sync_playwright() as p:
        br = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        page = br.new_context(viewport={"width": 1280, "height": 900}, extra_http_headers=TS_HEADERS).new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(server + "/?status=all")
        _settle(page)
        opened = "details.row-detail[open]"
        snips = page.locator("details.row-detail > summary")
        assert snips.count() >= 3
        snips.nth(0).click(); page.wait_for_timeout(100)
        assert page.locator(opened).count() == 1
        snips.nth(1).click(); page.wait_for_timeout(100)
        assert page.locator(opened).count() == 1
        assert page.locator("details.row-detail").nth(1).evaluate("d => d.open")
        snips.nth(1).click(); page.wait_for_timeout(100)
        assert page.locator(opened).count() == 0, "clicking the same item again closes it"
        snips.nth(2).click(); page.wait_for_timeout(100)
        page.keyboard.press("Escape"); page.wait_for_timeout(100)
        assert page.locator(opened).count() == 0, "Escape closes it"
        snips.nth(0).click(); page.wait_for_timeout(100)
        page.mouse.click(5, 5); page.wait_for_timeout(100)
        assert page.locator(opened).count() == 0, "clicking outside closes it"
        assert not errors, errors
        br.close()


def test_theme_toggle(server: str) -> None:
    """Oct 26 2026: The operator's Mac is dark by default; a top-bar control cycles Auto / Light / Dark."""
    bg = "() => getComputedStyle(document.body).backgroundColor"
    with sync_api.sync_playwright() as p:
        br = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        for scheme in ("dark", "light"):
            ctx = br.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme, extra_http_headers=TS_HEADERS)
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(server + "/")
            _settle(page)
            html = page.locator("html")
            btn = page.locator("#theme-toggle")
            assert html.get_attribute("data-theme") == "auto"
            assert "Auto" in btn.get_attribute("aria-label")
            auto_bg = page.evaluate(bg)
            # Header must not overflow on a 390px phone.
            assert page.evaluate("document.documentElement.scrollWidth") <= 390
            btn.click()
            assert html.get_attribute("data-theme") == "light"
            assert page.evaluate("localStorage.getItem('briefs-theme')") == "light"
            assert "Light" in btn.get_attribute("title")
            light_bg = page.evaluate(bg)
            btn.click()
            assert html.get_attribute("data-theme") == "dark"
            assert page.evaluate("localStorage.getItem('briefs-theme')") == "dark"
            dark_bg = page.evaluate(bg)
            assert light_bg == "rgb(246, 247, 249)" and dark_bg == "rgb(17, 18, 21)"
            assert auto_bg == (dark_bg if scheme == "dark" else light_bg)
            assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == "dark"
            # Persists across reloads, and still works after an htmx swap of the submit bar.
            page.reload(); _settle(page)
            assert html.get_attribute("data-theme") == "dark" and page.evaluate(bg) == dark_bg
            page.evaluate("htmx.trigger(document.body, 'queueChanged')"); _settle(page)
            btn.click()
            assert html.get_attribute("data-theme") == "auto"
            assert page.evaluate("localStorage.getItem('briefs-theme')") is None
            assert page.evaluate(bg) == auto_bg
            # Pre-paint script: forced theme under the opposite OS scheme.
            forced = "light" if scheme == "dark" else "dark"
            page.evaluate(f"localStorage.setItem('briefs-theme', '{forced}')")
            page.reload(); _settle(page)
            assert page.evaluate(bg) == (light_bg if forced == "light" else dark_bg)
            assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == forced
            assert not errors, errors
            ctx.close()
        br.close()


def test_topbar_phone_spacing(server: str) -> None:
    """Oct 26 2026: on phones the search box butted against Submit all; require >=8px gaps, no overflow."""
    js = """() => {
      const r = s => document.querySelector(s).getBoundingClientRect();
      const els = ['.logo-link', '.nav-links', '.search-input', '#submit-all-btn', '#theme-toggle'].map(r);
      const gaps = [];
      for (let i = 0; i < els.length; i++) for (let j = 0; j < els.length; j++) {
        const a = els[i], b = els[j];
        if (i === j || a.right > b.left) continue;
        if (a.bottom > b.top && b.bottom > a.top) gaps.push(b.left - a.right);  // same row, a left of b
      }
      return {sw: document.documentElement.scrollWidth, gaps, right: Math.max(...els.map(e => e.right))};
    }"""
    with sync_api.sync_playwright() as p:
        br = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        for w, h in ((390, 844), (360, 800)):
            ctx = br.new_context(viewport={"width": w, "height": h}, extra_http_headers=TS_HEADERS)
            page = ctx.new_page()
            page.goto(server + "/")
            _settle(page)
            m = page.evaluate(js)
            assert m["sw"] <= w and m["right"] <= w, m
            assert m["gaps"] and min(m["gaps"]) >= 7.5, m
            ctx.close()
        br.close()
