"""Gmail bulk-cleanup review pages (/cleanup/).

Read-only w.r.t. Gmail: this module only renders a pre-built dataset and stores
reviewed plans. Zero executes plans later (see triage-runs/v2/PLAN.md).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from briefs.config import now_et, state_home

PLAN_SCHEMA = "zero-cleanup-plan/v2"
ACTIONS = ["kill", "archive", "keep", "archive_unsub", "delete", "delete_unsub", "delete_hist"]
ACTION_LABELS = {
    "rule": "Leave to rule",
    "kill": "Kill",
    "archive": "Archive",
    "keep": "Keep",
    "archive_unsub": "Archive+Unsub",
    "delete": "Delete",
    "delete_unsub": "Delete+Unsub",
    "delete_hist": "Delete+history",
}
ACTION_HELP = {
    "rule": "Do nothing now; the daily rule archives it",
    "kill": "Trash these + all past mail from sender, unsubscribe",
    "archive": "Remove from inbox (stays unread, searchable)",
    "keep": "Leave in inbox",
    "archive_unsub": "Archive and unsubscribe from this source",
    "delete": "Move these to Trash (30-day recoverable)",
    "delete_unsub": "Trash these and unsubscribe from this source",
    "delete_hist": "Trash these + all past mail from this sender",
}
UNSUB_ACTIONS = {"kill", "archive_unsub", "delete_unsub"}
HISTORY_ACTIONS = {"kill", "delete_hist"}
MONEY_ACTIONS = {"rule", "archive", "keep", "archive_unsub"}  # "rule" = leave to daily rule (no action)
# Sensitive stacks (s["sensitive"] set) are shown, default Keep, and may take any action in s["allowed"]
# (Daniel, 2026-10-26). Teammate-owned mail is never in the dataset.
PLAN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,120}$")
MAX_PLAN_BYTES = 2_000_000
ZERO_SLUG = "zero"


def dataset_path() -> Path:
    return Path(os.environ.get("BRIEFS_CLEANUP_DATASET") or (state_home() / "data" / "cleanup" / "dataset.json"))


def plans_dir() -> Path:
    return Path(os.environ.get("BRIEFS_CLEANUP_PLANS_DIR") or (state_home() / "data" / "cleanup-plans"))


_cache: dict[str, Any] = {}


def load_dataset() -> dict[str, Any]:
    p = dataset_path()
    if not p.exists():
        raise HTTPException(status_code=404, detail="No cleanup dataset loaded")
    mtime = p.stat().st_mtime
    if _cache.get("key") != (str(p), mtime):
        _cache["data"] = json.loads(p.read_text(encoding="utf-8"))
        _cache["key"] = (str(p), mtime)
    return _cache["data"]


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def plan_checksum(plan: dict[str, Any]) -> str:
    body = {k: v for k, v in plan.items() if k != "checksum"}
    return "sha256:" + hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


RULE_HELP = {
    "github-bot-comments": "GitHub notification mail written by bots (Cursor, Codex, Linear, CodeRabbit, Codecov, Vercel, Paseo, GitHub Actions).",
    "github-ci-failed-cancelled": "GitHub Actions \"Run failed\" / \"Run cancelled\" notices.",
    "promotions-7d": "Anything in the Promotions tab older than 7 days, except Mochi, CVS, Chabad Sudbury, Beth Elohim, EZFacility, Pika, Maynard chess/daycare, and anything mentioning receipt/invoice/order.",
    "ebay-saved-search": "eBay saved-search alerts (\"NEW!\" / \"Latest price\").",
    "library-minlib-30d": "Minuteman Library mail older than 30 days.",
    "nextdoor": "Everything from Nextdoor.",
    "skej-2d": "Skej scheduling-assistant mail older than 2 days.",
    "chabad-sudbury-14d": "Chabad Sudbury mail older than 14 days.",
    "wordpress-maynard-wiki": "WordPress notifications from maynard.wiki.",
    "beehiiv": "Everything sent through beehiiv newsletters.",
    "beth-elohim-starlite-14d": "Beth Elohim \"Star-Lite\" bulletin older than 14 days.",
    "google-marketing-7d": "Google One / Maps / generic Google marketing older than 7 days.",
    "yelp-60d": "Yelp mail older than 60 days.",
    "exmuslims-org": "Everything from exmuslims.org.",
    "zulip-digest": "Zulip digest emails.",
    "discourse-summaries": "Discourse forum summary emails.",
    "mochi-promos-7d": "Mochi promotions older than 7 days (other Mochi mail stays).",
    "google-data-shares-7d": "\"You shared some Google Account data\" notices older than 7 days.",
}


def et_label(ts: str | None) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        return datetime.fromisoformat(str(ts)).astimezone(ZoneInfo("America/New_York")).strftime("%b %-d, %Y %-I:%M %p ET")
    except Exception:
        return str(ts or "")


def chunk_by_slug(ds: dict[str, Any], slug: str) -> dict[str, Any]:
    for c in ds.get("chunks", []):
        if c["slug"] == slug:
            return c
    raise HTTPException(status_code=404, detail="Unknown cleanup chunk")


def validate_plan(plan: dict[str, Any], ds: dict[str, Any]) -> list[str]:
    """Re-check the plan server-side; the page is never trusted. Returns errors."""
    errs: list[str] = []
    if plan.get("schema") != PLAN_SCHEMA or plan.get("version") != 2:
        errs.append("schema/version mismatch")
    pid = plan.get("plan_id")
    if not isinstance(pid, str) or not PLAN_ID_RE.match(pid):
        errs.append("bad plan_id")
    if plan.get("checksum") != plan_checksum(plan):
        errs.append("checksum mismatch")
    src = plan.get("source") or {}
    if src.get("dataset_id") != ds.get("dataset_id") or src.get("dataset_sha256") != ds.get("data_sha256"):
        errs.append("plan is for a different dataset")
        return errs
    try:
        chunk = chunk_by_slug(ds, src.get("chunk", ""))
    except HTTPException:
        errs.append("unknown chunk")
        return errs
    stacks = {s["id"]: s for s in chunk["stacks"]}
    keep_senders: set[str] = set()
    for st in plan.get("stacks", []):
        ds_st = stacks.get(st.get("id"))
        if not ds_st:
            errs.append(f"unknown stack {st.get('id')}")
            continue
        if st.get("action") == "keep" or "keep" in (st.get("overrides") or {}).values():
            keep_senders.add(ds_st["sender"])
    for st in plan.get("stacks", []):
        ds_st = stacks.get(st.get("id"))
        if not ds_st:
            continue
        act = st.get("action")
        if ds_st.get("money") and act not in MONEY_ACTIONS:
            errs.append(f"{ds_st['id']}: Money stacks allow only archive/keep/archive_unsub")
        if act not in ds_st["allowed"]:
            errs.append(f"{ds_st['id']}: action {act} not allowed")
        tids = {i["tid"] for i in ds_st["items"]}
        if not set(st.get("threads") or []) <= tids:
            errs.append(f"{ds_st['id']}: threads outside stack")
        ov = st.get("overrides") or {}
        allowed_ov = {"keep", "archive"} if ds_st["kind"] != "normal" else {"keep", "archive", "delete"}
        for tid, v in ov.items():
            if tid not in tids or v not in allowed_ov:
                errs.append(f"{ds_st['id']}: bad override {tid}={v}")
        if act in HISTORY_ACTIONS and ds_st["sender"] in keep_senders:
            errs.append(f"{ds_st['id']}: delete-history with a Keep for the same sender")
    for u in plan.get("unsubscribe", []):
        if u.get("auto_allowed") and u.get("method") != "one-click":
            errs.append(f"{u.get('source')}: auto unsubscribe without one-click")
    return errs


def register(app: FastAPI, templates: Any, common_context: Callable[..., dict[str, Any]],
             verify_csrf: Callable[[Request], None], get_db: Callable[[], Any]) -> None:

    @app.get("/cleanup", response_class=HTMLResponse)
    @app.get("/cleanup/", response_class=HTMLResponse)
    def cleanup_index(request: Request) -> Any:
        ds = load_dataset()
        ctx = common_context(request, current_nav="cleanup")
        owned: dict[str, list[dict[str, Any]]] = {}
        for o in ds.get("excluded_owned", []):
            owned.setdefault(o["owner"], []).append(o)
        ctx.update(ds=ds, owned=owned, generated_et=et_label(ds.get("generated_at")), rule_help=RULE_HELP, chunks=ds["chunks"],
                   total_minutes=round(sum(c["minutes"] for c in ds["chunks"])))
        return templates.TemplateResponse(request=request, name="cleanup_index.html", context=ctx)

    @app.get("/cleanup/{slug}", response_class=HTMLResponse)
    def cleanup_chunk(request: Request, slug: str) -> Any:
        ds = load_dataset()
        chunk = chunk_by_slug(ds, slug)
        idx = [c["slug"] for c in ds["chunks"]].index(slug)
        nxt = ds["chunks"][idx + 1]["slug"] if idx + 1 < len(ds["chunks"]) else None
        prv = ds["chunks"][idx - 1]["slug"] if idx > 0 else None
        meta = {
            "dataset_id": ds["dataset_id"], "dataset_sha256": ds["data_sha256"],
            "dataset_generated_at": ds.get("generated_at"), "chunk": slug,
            "title": chunk["title"], "labels": ACTION_LABELS,
        }
        ctx = common_context(request, current_nav="cleanup")
        ctx.update(ds=ds, chunk=chunk, meta=meta, labels=ACTION_LABELS, helps=ACTION_HELP,
                   order=["rule"] + ACTIONS, nxt=nxt, prv=prv, n_chunks=len(ds["chunks"]), pos=idx + 1)
        return templates.TemplateResponse(request=request, name="cleanup_chunk.html", context=ctx)

    @app.post("/cleanup/plan")
    async def cleanup_plan(request: Request) -> Any:
        verify_csrf(request)
        raw = await request.body()
        if len(raw) > MAX_PLAN_BYTES:
            raise HTTPException(status_code=413, detail="Plan too large")
        try:
            plan = json.loads(raw)
            assert isinstance(plan, dict)
        except Exception:
            raise HTTPException(status_code=400, detail="Body must be a JSON plan")
        ds = load_dataset()
        errs = validate_plan(plan, ds)
        if errs:
            return JSONResponse({"ok": False, "errors": errs[:20]}, status_code=422)
        d = plans_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{plan['plan_id']}.json"
        if path.exists():
            return JSONResponse({"ok": False, "errors": ["plan_id already submitted"]}, status_code=409)
        author = request.headers.get("Tailscale-User-Login") or "local"
        envelope = {"received_at": now_et().isoformat(), "reviewer": author, "plan": plan}
        path.write_text(json.dumps(envelope, indent=1, ensure_ascii=False), encoding="utf-8")
        action_id = None
        conn = get_db()
        try:
            bot = conn.execute("SELECT id FROM bots WHERE slug = ?", (ZERO_SLUG,)).fetchone()
            if bot:
                ts = now_et().isoformat()
                payload = {"plan_id": plan["plan_id"], "path": str(path), "chunk": plan["source"]["chunk"],
                           "totals": plan.get("totals", {}), "checksum": plan["checksum"],
                           "note": "Bulk cleanup plan. Archive first; Trash/Unsub only after Daniel confirms counts."}
                with conn:
                    cur = conn.execute(
                        """INSERT INTO actions (item_id, bot_id, type, payload_json, author, status,
                               wake_requested, wake_status, created_at, updated_at)
                           VALUES (NULL, ?, 'cleanup_plan', ?, ?, 'submitted', 0, '', ?, ?)""",
                        (bot["id"], json.dumps(payload), author, ts, ts))
                    action_id = cur.lastrowid
        finally:
            conn.close()
        return {"ok": True, "plan_id": plan["plan_id"], "action_id": action_id,
                "queued_for": ZERO_SLUG if action_id else None,
                "unsubscribe": plan.get("unsubscribe", [])}
