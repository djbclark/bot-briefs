"""Regression tests for the 2026-11-01 hardening pass (H-1, M-4, M-5, L-3, L-4)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from briefs.config import ALLOWED_LOGINS
from briefs.mirror import validate_vps_action
from briefs.parse import filter_safe_links, is_safe_url, safe_url
from briefs.store import _safe_snippet
from briefs.web import create_app

LOGIN = sorted(ALLOWED_LOGINS)[0] if ALLOWED_LOGINS else "user@example.com"
XSS = '<img src=x onerror=alert(1)>'


def test_snippet_escapes_html_and_keeps_marks():
    out = str(_safe_snippet(f"before \x02{XSS}\x03 after <script>x</script>"))
    assert "<img" not in out and "<script>" not in out
    assert "&lt;img src=x onerror=alert(1)&gt;" in out
    assert out.count("<mark>") == 1 and out.count("</mark>") == 1


def test_search_page_escapes_payload(client):
    r = client.get("/search", params={"q": "onerror"})
    assert r.status_code == 200
    assert "<img src=x onerror" not in r.text


@pytest.mark.parametrize("url", ["javascript:alert(1)", " JaVaScRiPt:alert(1)", "java\tscript:alert(1)",
                                 "data:text/html,<b>", "vbscript:x", "//evil.example", "\\\\evil", "file:///etc/passwd"])
def test_unsafe_urls_rejected(url):
    assert not is_safe_url(url)
    assert safe_url(url) == "#"


@pytest.mark.parametrize("url", ["https://example.com/a", "http://x.y", "mailto:a@b.c", "/items/1", "items/2"])
def test_safe_urls_allowed(url):
    assert is_safe_url(url)
    assert safe_url(url) == url


def test_filter_safe_links():
    links = [{"label": "a", "url": "javascript:alert(1)"}, {"label": "b", "url": "https://ok.example"}]
    assert [l["label"] for l in filter_safe_links(links)] == ["b"]


def test_security_headers(client):
    r = client.get("/")
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert "frame-ancestors 'none'" in r.headers.get("content-security-policy", "")
    assert "nonce-" in r.headers["content-security-policy"]


def test_trusted_host_rejects_unknown(imported_db):
    c = TestClient(create_app(imported_db), base_url="http://evil.example")
    assert c.get("/").status_code == 400


def test_ui_post_requires_allowed_login(client):
    base = {"HX-Request": "true", "Origin": "http://testserver"}
    assert client.post("/cleanup/plan", content="{}", headers=base).status_code == 403
    bad = dict(base, **{"Tailscale-User-Login": "mallory@example.com"})
    assert client.post("/cleanup/plan", content="{}", headers=bad).status_code == 403


def _a(**kw):
    a = {"type": "done", "status": "queued", "payload_json": "{}"}
    a.update(kw)
    return a


def test_vps_action_validation():
    assert validate_vps_action(_a()) == "{}"
    assert validate_vps_action(_a(type="snooze", payload_json=json.dumps({"until": "2026-11-05"})))
    assert validate_vps_action(_a(type="priority", payload_json=json.dumps({"delta": -1})))
    for bad in [_a(type="exec"), _a(status="done"), _a(type="snooze", payload_json='{"until":"x"}'),
                _a(type="priority", payload_json='{"delta":99}'), _a(type="reply", payload_json='{"text":""}'),
                _a(payload_json="[1]"), _a(payload_json='{"x":{"y":1}}')]:
        with pytest.raises((ValueError, TypeError)):
            validate_vps_action(bad)


def test_templates_filter_external_hrefs():
    """Every href built from a data URL must pass through |safe_url."""
    import pathlib, re
    tdir = pathlib.Path(__file__).resolve().parent.parent / "briefs" / "templates"
    bad = []
    for p in tdir.rglob("*.html"):
        for m in re.finditer(r'href="\{\{\s*([^}]*?)\s*\}\}"', p.read_text()):
            if "safe_url" not in m.group(1):
                bad.append(f"{p.name}: {m.group(1)}")
    assert not bad, bad
