"""VPS-side replication endpoints (dual-run with the Grok Bot box).

Only registered when BRIEFS_SYNC_TOKEN_FILE is set (the VPS copy). The box copy never loads this.

Box -> VPS:  POST /sync/push     idempotent row upserts/deletes of bots, reports, items, report_items, actions
VPS -> box:  GET  /sync/actions  actions Daniel created/changed on the VPS UI that the box has not applied yet
             POST /sync/actions/ack  the box reports "applied as box action N" (VPS renumbers to N) or "rejected"
Status:      GET  /sync/status (token) and GET /healthz/mirror (no token, timestamps only)

Every /sync/* call needs `Authorization: Bearer <token>` (token file is mode 600 on both machines).
Conflict rule: the box is authoritative. Rows pushed from the box overwrite the VPS copy, except item
status/snooze/priority while a VPS-made action for that item is still waiting to be pulled by the box.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# VPS-made actions get ids from this base so they can never collide with box action ids.
VPS_ID_BASE = 1_000_000_000
# Order matters for readability only (FK enforcement is off during ingestion).
TABLES = ("bots", "reports", "items", "report_items", "actions")
# Natural unique keys, used to clear a stale row that would block an upsert with a different id.
UNIQUE_KEYS = {
    "bots": ("slug",),
    "reports": ("bot_id", "report_date", "kind"),
    "items": ("bot_id", "item_key"),
    "report_items": ("report_id", "display_id"),
}
# Statuses the VPS UI controls. Once the box moves an action past these, the box owns it.
VPS_CONTROLLED = ("queued", "submitted", "pending")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def row_hash(row: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()


ACTION_VERSION_COLS = ("item_id", "bot_id", "type", "payload_json", "author", "status", "rank", "created_at")


def action_version(row: Any) -> str:
    """Hash of the fields the VPS UI controls (not id, wake_status, updated_at), so renumbering and
    webhook bookkeeping do not look like a new change."""
    d = dict(row)
    return row_hash({k: d.get(k) for k in ACTION_VERSION_COLS})


def init_sync_schema(conn: sqlite3.Connection) -> None:
    """VPS-only bookkeeping tables and triggers (idempotent). Never created on the box DB."""
    with conn:
        conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS sync_meta (k TEXT PRIMARY KEY, v TEXT);
        -- One row per action Daniel made on the VPS UI. cur_id is its id in `actions` right now
        -- (vps-range id until the box confirms, then the box's id). dirty=1: box has not seen the latest version.
        CREATE TABLE IF NOT EXISTS sync_origin (
            cur_id INTEGER PRIMARY KEY, orig_id INTEGER, mapped INTEGER DEFAULT 0,
            dirty INTEGER DEFAULT 1, delivered_hash TEXT, created_at TEXT);
        INSERT OR IGNORE INTO sync_meta(k, v) VALUES ('ingesting', '0');
        DROP TRIGGER IF EXISTS sync_actions_ai;
        CREATE TRIGGER sync_actions_ai AFTER INSERT ON actions
        WHEN NEW.id >= {VPS_ID_BASE} AND (SELECT v FROM sync_meta WHERE k='ingesting') != '1'
        BEGIN
            INSERT OR IGNORE INTO sync_origin(cur_id, orig_id, mapped, dirty, created_at)
            VALUES (NEW.id, NEW.id, 0, 1, strftime('%Y-%m-%dT%H:%M:%SZ','now'));
        END;
        DROP TRIGGER IF EXISTS sync_actions_au;
        CREATE TRIGGER sync_actions_au AFTER UPDATE ON actions
        WHEN (SELECT v FROM sync_meta WHERE k='ingesting') != '1'
        BEGIN
            UPDATE sync_origin SET dirty = 1 WHERE cur_id = NEW.id;
        END;
        """)
        # Make the VPS autoincrement start at VPS_ID_BASE so UI-made actions get vps-range ids.
        row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='actions'").fetchone()
        if row is None:
            conn.execute("INSERT INTO sqlite_sequence(name, seq) VALUES ('actions', ?)", (VPS_ID_BASE,))
        elif row[0] < VPS_ID_BASE:
            conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name='actions'", (VPS_ID_BASE,))


def meta_get(conn: sqlite3.Connection, k: str) -> str | None:
    r = conn.execute("SELECT v FROM sync_meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else None


def meta_set(conn: sqlite3.Connection, k: str, v: str) -> None:
    conn.execute("INSERT INTO sync_meta(k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def mirror_state(conn: sqlite3.Connection, stale_after_min: int = 30) -> dict[str, Any]:
    """Lag of the box mirror as seen by the VPS. last_push_at is stamped by every push, incl. heartbeats."""
    last = meta_get(conn, "last_push_at")
    out: dict[str, Any] = {"last_push_at": last, "lag_seconds": None, "stale": True,
                           "stale_after_minutes": stale_after_min, "last_change_at": meta_get(conn, "last_change_at")}
    if last:
        lag = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
        out["lag_seconds"] = int(lag)
        out["stale"] = lag > stale_after_min * 60
    return out


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _refresh_items_fts(conn: sqlite3.Connection, item_ids: set[int]) -> None:
    """items_fts is filled by the publish code (not triggers); rebuild the rows for items we touched.
    Body = joined report_items bodies for the item (same idea as the box's publish step)."""
    for iid in item_ids:
        it = conn.execute("SELECT title FROM items WHERE id=?", (iid,)).fetchone()
        conn.execute("DELETE FROM items_fts WHERE item_id = ?", (iid,))
        if not it:
            continue
        body = conn.execute(
            "SELECT body_md FROM report_items WHERE item_id=? ORDER BY id DESC LIMIT 1", (iid,)
        ).fetchone()
        conn.execute("INSERT INTO items_fts (title, body, item_id) VALUES (?, ?, ?)",
                     (it["title"], (body["body_md"] if body else "") or "", iid))


def apply_push(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply one batch in one transaction. Replays are harmless (upsert by primary key)."""
    upserts: dict[str, list[dict[str, Any]]] = payload.get("upserts") or {}
    deletes: dict[str, list[int]] = payload.get("deletes") or {}
    n_up = n_del = 0
    touched_items: set[int] = set()
    conn.execute("PRAGMA foreign_keys = OFF")  # replica: box guarantees consistency, order must not matter
    conn.execute("BEGIN IMMEDIATE")
    try:
        meta_set(conn, "ingesting", "1")  # keeps the origin-tracking triggers quiet for box-driven writes
        # Deletes first (children before parents), so a replaced row never trips a unique key.
        for t in ("actions", "report_items", "items", "reports", "bots"):
            ids = [int(i) for i in deletes.get(t, [])]
            for i in ids:
                if t == "items":
                    touched_items.add(i)
                conn.execute(f"DELETE FROM {t} WHERE id = ?", (i,))
                n_del += 1
        for t in TABLES:
            rows = upserts.get(t) or []
            if not rows:
                continue
            cols = _cols(conn, t)
            for row in rows:
                data = {k: v for k, v in row.items() if k in cols}
                rid = data["id"]
                if t in UNIQUE_KEYS and all(k in data for k in UNIQUE_KEYS[t]):
                    where = " AND ".join(f"{k} IS ?" for k in UNIQUE_KEYS[t])
                    conn.execute(f"DELETE FROM {t} WHERE {where} AND id != ?",
                                 [data[k] for k in UNIQUE_KEYS[t]] + [rid])
                keep: dict[str, Any] = {}
                if t == "items":
                    touched_items.add(rid)
                    # Pending VPS-made action for this item? Keep the local effect until the box pulls it.
                    pend = conn.execute(
                        "SELECT 1 FROM sync_origin so JOIN actions a ON a.id = so.cur_id "
                        "WHERE so.mapped = 0 AND a.item_id = ? LIMIT 1", (rid,)).fetchone()
                    if pend:
                        cur = conn.execute("SELECT status, snooze_until, priority FROM items WHERE id=?",
                                           (rid,)).fetchone()
                        if cur:
                            keep = dict(cur)
                if t == "report_items":
                    touched_items.add(data.get("item_id"))
                names = list(data)
                ph = ",".join("?" * len(names))
                upd = ",".join(f"{c}=excluded.{c}" for c in names if c != "id")
                if t == "actions" and "wake_status" in names:
                    # The VPS knows whether it fired the webhook; the box does not.
                    upd = upd.replace("wake_status=excluded.wake_status",
                                      "wake_status=CASE WHEN excluded.wake_status='' THEN actions.wake_status "
                                      "ELSE excluded.wake_status END")
                conn.execute(
                    f"INSERT INTO {t} ({','.join(names)}) VALUES ({ph}) "
                    f"ON CONFLICT(id) DO UPDATE SET {upd}", [data[c] for c in names])
                if keep:
                    conn.execute("UPDATE items SET status=?, snooze_until=?, priority=? WHERE id=?",
                                 (keep["status"], keep["snooze_until"], keep["priority"], rid))
                n_up += 1
        _refresh_items_fts(conn, {i for i in touched_items if i is not None})
        now = utcnow()
        meta_set(conn, "last_push_at", now)
        if n_up or n_del:
            meta_set(conn, "last_change_at", now)
        meta_set(conn, "ingesting", "0")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        meta_set_safe(conn)
        raise
    return {"upserts": n_up, "deletes": n_del}


def meta_set_safe(conn: sqlite3.Connection) -> None:
    try:
        with conn:
            meta_set(conn, "ingesting", "0")
    except Exception:
        pass


def pending_actions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """VPS-made actions whose latest version the box has not acknowledged."""
    out = []
    for so in conn.execute("SELECT * FROM sync_origin WHERE dirty = 1 ORDER BY cur_id").fetchall():
        a = conn.execute("SELECT * FROM actions WHERE id = ?", (so["cur_id"],)).fetchone()
        if not a:
            conn.execute("UPDATE sync_origin SET dirty = 0 WHERE cur_id = ?", (so["cur_id"],))
            continue
        d = dict(a)
        out.append({"vps_id": so["orig_id"], "cur_id": so["cur_id"], "mapped": bool(so["mapped"]),
                    "version": action_version(d), "action": d})
    return out


def apply_ack(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    """Box confirms it stored VPS action `vps_id` as its own action `box_id` (or rejected it)."""
    n = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        meta_set(conn, "ingesting", "1")
        for it in items:
            so = conn.execute("SELECT * FROM sync_origin WHERE orig_id = ?", (int(it["vps_id"]),)).fetchone()
            if not so:
                continue
            cur_id = so["cur_id"]
            if it.get("rejected"):
                now = utcnow()
                conn.execute("UPDATE actions SET status='failed', bot_note=?, updated_at=? WHERE id=?",
                             ("box rejected: " + str(it["rejected"])[:300], now, cur_id))
                conn.execute("UPDATE sync_origin SET dirty=0, mapped=1 WHERE cur_id=?", (cur_id,))
                n += 1
                continue
            box_id = int(it["box_id"])
            if not so["mapped"]:
                if conn.execute("SELECT 1 FROM actions WHERE id=?", (box_id,)).fetchone():
                    # The mirror already pushed the box's copy; keep that one, drop ours.
                    conn.execute("DELETE FROM actions WHERE id=?", (cur_id,))
                else:
                    conn.execute("UPDATE actions SET id=? WHERE id=?", (box_id, cur_id))
                conn.execute("UPDATE sync_origin SET cur_id=?, mapped=1 WHERE cur_id=?", (box_id, cur_id))
                cur_id = box_id
            cur = conn.execute("SELECT * FROM actions WHERE id=?", (cur_id,)).fetchone()
            # Only clear `dirty` if Daniel did not change it again after the box fetched it.
            if cur is not None and action_version(cur) == it.get("version"):
                conn.execute("UPDATE sync_origin SET dirty=0 WHERE cur_id=?", (cur_id,))
            n += 1
        meta_set(conn, "ingesting", "0")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        meta_set_safe(conn)
        raise
    return n


def table_digests(conn: sqlite3.Connection) -> dict[str, Any]:
    """Per-table row digest for `mirror verify` (box vs VPS). VPS-made, not-yet-pulled actions and the
    VPS-only wake_status column are left out."""
    out = {}
    for t in TABLES:
        rows = {}
        for r in conn.execute(f"SELECT * FROM {t}"):
            d = dict(r)
            if t == "actions" and d["id"] >= VPS_ID_BASE:
                continue
            d.pop("wake_status", None)
            rows[d["id"]] = row_hash(d)
        out[t] = {"count": len(rows), "digest": row_hash({str(i): h for i, h in sorted(rows.items())})}
    return out


def register(app: FastAPI, get_db: Callable[[], sqlite3.Connection], token_file: str) -> None:
    tok_path = Path(token_file)

    def token_ok(request: Request) -> bool:
        try:
            want = tok_path.read_text().strip()
        except OSError:
            return False
        got = request.headers.get("Authorization", "")
        if not want or not got.startswith("Bearer "):
            return False
        return hmac.compare_digest(got[7:].strip().encode(), want.encode())

    def deny() -> JSONResponse:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    conn0 = get_db()
    try:
        init_sync_schema(conn0)
    finally:
        conn0.close()

    @app.post("/sync/push")
    async def sync_push(request: Request) -> JSONResponse:
        if not token_ok(request):
            return deny()
        payload = await request.json()
        conn = get_db()
        try:
            res = apply_push(conn, payload)
        finally:
            conn.close()
        return JSONResponse({"ok": True, "server_time": utcnow(), **res})

    @app.get("/sync/actions")
    def sync_actions(request: Request) -> JSONResponse:
        if not token_ok(request):
            return deny()
        conn = get_db()
        try:
            return JSONResponse({"ok": True, "actions": pending_actions(conn)})
        finally:
            conn.close()

    @app.post("/sync/actions/ack")
    async def sync_ack(request: Request) -> JSONResponse:
        if not token_ok(request):
            return deny()
        body = await request.json()
        conn = get_db()
        try:
            n = apply_ack(conn, body.get("items") or [])
        finally:
            conn.close()
        return JSONResponse({"ok": True, "acked": n})

    @app.get("/sync/status")
    def sync_status(request: Request) -> JSONResponse:
        if not token_ok(request):
            return deny()
        conn = get_db()
        try:
            counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
            pend = conn.execute("SELECT COUNT(*) FROM sync_origin WHERE dirty=1").fetchone()[0]
            out: dict[str, Any] = {"ok": True, "server_time": utcnow(), "counts": counts,
                                   "pending_for_box": pend, **mirror_state(conn)}
            if request.query_params.get("digest"):
                out["digests"] = table_digests(conn)
            return JSONResponse(out)
        finally:
            conn.close()
