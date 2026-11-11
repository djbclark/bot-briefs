"""Tests for the /cleanup/ review pages and plan submission."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from briefs import cleanup
from briefs.db import connect
from briefs.web import create_app

CSRF = {"HX-Request": "true", "Origin": "http://testserver", "Tailscale-User-Login": "user@example.com"}


@pytest.fixture
def ds_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    s1 = {"id": "news@a.com", "sender": "news@a.com", "name": "A", "money": False, "kind": "normal", "count": 2,
          "threads": 2, "newsletter": True, "covered": [], "partly_covered": [], "flag": None, "default": "kill",
          "allowed": ["kill", "archive", "keep", "archive_unsub", "delete", "delete_unsub", "delete_hist"], "note": "",
          "unsub": {"one_click": True, "mailto": None, "url": "https://a.com/u", "checked": True},
          "items": [{"tid": "t1", "mids": ["m1"], "n": 1, "date": "2026-10-01", "subject": "s", "snippet": "", "rule": None, "cat": ""},
                    {"tid": "t2", "mids": ["m2"], "n": 1, "date": "2026-10-02", "subject": "s2", "snippet": "", "rule": None, "cat": ""}]}
    s2 = dict(s1, id="bank@b.com", sender="bank@b.com", kind="money", money=True, default="archive", allowed=["archive", "keep"],
              items=[dict(s1["items"][0], tid="t3")])
    ds = {"schema": "x", "dataset_id": "test-ds", "generated_at": "2026-10-26T00:00:00", "data_sha256": "abc",
          "counts": {"candidates": 3, "stacks": 2, "excluded": 0}, "excluded": {}, "rules": [
              {"id": "nextdoor", "query": "from:nextdoor.com", "description": "", "emails_now": 0}],
          "chunks": [{"slug": "01-test", "group": "kill", "title": "Test", "emails": 3, "minutes": 1.0, "stacks": [s1, s2]}]}
    p = tmp_path / "ds.json"
    p.write_text(json.dumps(ds))
    monkeypatch.setenv("BRIEFS_CLEANUP_DATASET", str(p))
    monkeypatch.setenv("BRIEFS_CLEANUP_PLANS_DIR", str(tmp_path / "plans"))
    return ds


def make_plan(action: str = "kill", overrides: dict | None = None, pid: str = "test-ds-01-test-abcd") -> dict:
    plan = {"schema": cleanup.PLAN_SCHEMA, "version": 2, "plan_id": pid, "generated_at": "2026-10-26T12:00:00Z",
            "source": {"chunk": "01-test", "dataset_id": "test-ds", "dataset_sha256": "abc"},
            "stacks": [{"id": "news@a.com", "sender": "news@a.com", "action": action, "threads": ["t1", "t2"],
                        "overrides": overrides or {}},
                       {"id": "bank@b.com", "sender": "bank@b.com", "action": "archive", "threads": ["t3"], "overrides": {}}],
            "unsubscribe": [{"source": "news@a.com", "method": "one-click", "auto_allowed": True}], "totals": {"trash": 2}}
    plan["checksum"] = cleanup.plan_checksum(plan)
    return plan


def test_pages_render(temp_db: str, ds_env: dict) -> None:
    c = TestClient(create_app(temp_db))
    r = c.get("/cleanup/")
    assert r.status_code == 200 and "01-test" in r.text and "nextdoor" in r.text
    r = c.get("/cleanup/01-test")
    assert r.status_code == 200 and 'class="cl-title" aria-expanded="false"' in r.text
    assert c.get("/cleanup/nope").status_code == 404


def test_plan_requires_csrf(temp_db: str, ds_env: dict) -> None:
    c = TestClient(create_app(temp_db))
    assert c.post("/cleanup/plan", json=make_plan()).status_code == 403


def test_plan_saved_and_enqueued(imported_db: str, ds_env: dict, tmp_path: Path) -> None:
    c = TestClient(create_app(imported_db))
    r = c.post("/cleanup/plan", content=json.dumps(make_plan()), headers=CSRF)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] and (tmp_path / "plans" / "test-ds-01-test-abcd.json").exists()
    conn = connect(imported_db)
    row = conn.execute("SELECT a.type, b.slug FROM actions a JOIN bots b ON b.id=a.bot_id WHERE a.id=?", (j["action_id"],)).fetchone()
    conn.close()
    if row:  # zero bot exists in the sample import
        assert row["type"] == "cleanup_plan" and row["slug"] == "zero"
    assert c.post("/cleanup/plan", content=json.dumps(make_plan()), headers=CSRF).status_code == 409


@pytest.mark.parametrize("plan,err", [
    (dict(make_plan(), checksum="sha256:00"), "checksum"),
    (make_plan(action="kill", overrides={"t1": "keep"}), "Keep"),
    (make_plan(action="delete_hist", overrides={"t2": "keep"}), "Keep"),
    (make_plan(overrides={"tX": "keep"}), "bad override"),
])
def test_plan_rejected(temp_db: str, ds_env: dict, plan: dict, err: str) -> None:
    c = TestClient(create_app(temp_db))
    r = c.post("/cleanup/plan", content=json.dumps(plan), headers=CSRF)
    assert r.status_code == 422 and err in " ".join(r.json()["errors"])


def test_money_stack_cannot_be_deleted(temp_db: str, ds_env: dict) -> None:
    p = make_plan()
    p["stacks"][1]["action"] = "delete"
    p["checksum"] = cleanup.plan_checksum(p)
    r = TestClient(create_app(temp_db)).post("/cleanup/plan", content=json.dumps(p), headers=CSRF)
    assert r.status_code == 422


def test_sensitive_shown_any_action_and_owned_listed(temp_db: str, ds_env: dict, tmp_path: Path) -> None:
    ds = json.loads(Path(cleanup.dataset_path()).read_text())
    s3 = dict(ds["chunks"][0]["stacks"][0], id="bank@c.com#sensitive", sender="bank@c.com", default="keep",
              sensitive="bank/tax/legal/e-sign/2FA/medical/family", items=[dict(ds["chunks"][0]["stacks"][0]["items"][0], tid="t9")])
    ds["chunks"][0]["stacks"].append(s3)
    ds["counts"].update(sensitive=1, teammate_owned=2)
    ds["excluded_owned"] = [{"owner": "Pika", "sender": "pika@x.org", "emails": 2}]
    Path(cleanup.dataset_path()).write_text(json.dumps(ds))
    c = TestClient(create_app(temp_db))
    r = c.get("/cleanup/")
    assert "another bot owns it" in r.text and "pika@x.org" in r.text
    assert "cl-badge sensitive" in c.get("/cleanup/01-test").text
    p = make_plan(pid="test-ds-01-test-sens")
    p["stacks"].append({"id": s3["id"], "sender": "bank@c.com", "action": "delete_hist", "threads": ["t9"], "overrides": {}})
    p["checksum"] = cleanup.plan_checksum(p)
    r = c.post("/cleanup/plan", content=json.dumps(p), headers=CSRF)
    assert r.status_code == 200, r.text


def test_money_limited_even_if_dataset_allows(temp_db: str, ds_env: dict) -> None:
    ds = json.loads(Path(cleanup.dataset_path()).read_text())
    ds["chunks"][0]["stacks"][1]["allowed"] = ["archive", "keep", "delete"]
    Path(cleanup.dataset_path()).write_text(json.dumps(ds))
    p = make_plan()
    p["stacks"][1]["action"] = "delete"
    p["checksum"] = cleanup.plan_checksum(p)
    r = TestClient(create_app(temp_db)).post("/cleanup/plan", content=json.dumps(p), headers=CSRF)
    assert r.status_code == 422 and "Money" in " ".join(r.json()["errors"])
