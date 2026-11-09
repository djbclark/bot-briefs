"""Box-side mirror to the VPS copy of the dashboard (dual-run). Never changes how the box dashboard behaves.

Design (state replication, not an event hook, so no `brief` code path can be missed):
  * Reads the box DB read-only, hashes every row of bots/reports/items/report_items/actions and compares with
    `rowstate` in a private sidecar DB (state.db). Rows whose hash differs are pushed to the VPS, rows that
    vanished are pushed as deletes. A row is marked "sent" only after the VPS answered 2xx, so a frozen box,
    a dead VPS or a crash simply means the same diff is sent again later. The VPS upserts by id, so replays are safe.
  * Pull side: actions the user made on the VPS UI are fetched, stored once in the box DB (idempotent through the
    additive table `mirror_vps_actions`, written in the same transaction as the action), then acknowledged so
    the VPS renumbers them to the box id. `brief inbox` / `brief actions` therefore work unchanged.
  * Box is authoritative: after a VPS-made action is taken past queued/submitted/pending on the box,
    the VPS can no longer change it.
Transport: curl through the box tailscaled SOCKS proxy (userspace tailscale) to the VPS's tailnet-only HTTPS
name; bearer token read from a mode-600 file and passed to curl on stdin (never on the command line).

Usage: python -m briefs.mirror {run|once|pull|backfill|status|lag-check|verify}
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from briefs.config import ALLOWED_LOGINS, BRIEFS_DB, now_et
from briefs.syncapi import TABLES, VPS_CONTROLLED, row_hash

HOME = Path.home()
STATE_DIR = Path(os.environ.get("BRIEFS_MIRROR_STATE_DIR", HOME / ".local/share/briefs-mirror"))
TOKEN_FILE = Path(os.environ.get("BRIEFS_MIRROR_TOKEN_FILE", HOME / ".config/briefs-mirror/token"))
STATUS_DIR = Path(os.environ.get("BRIEFS_MIRROR_STATUS_DIR", "/home/box/agent-data/shared/briefs-mirror"))
URL = os.environ.get("BRIEFS_MIRROR_URL", "https://briefs.example-tailnet.ts.net").rstrip("/")
SOCKS = os.environ.get("BRIEFS_MIRROR_SOCKS", "localhost:1055")  # "" = direct (tests)
INTERVAL = float(os.environ.get("BRIEFS_MIRROR_INTERVAL", "10"))
HEARTBEAT_S = 60
BATCH_BYTES = 700_000
BATCH_ROWS = 400


def log(msg: str) -> None:
    sys.stderr.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} mirror: {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------- sidecar state
def state_db() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(STATE_DIR / "state.db"), timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS rowstate (tbl TEXT, id INTEGER, h TEXT, PRIMARY KEY (tbl, id))")
    c.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    return c


def meta_get(c: sqlite3.Connection, k: str, default: Optional[str] = None) -> Optional[str]:
    r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def meta_set(c: sqlite3.Connection, k: str, v: str) -> None:
    c.execute("INSERT INTO meta(k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
    c.commit()


# ---------------------------------------------------------------- transport
class MirrorHTTPError(RuntimeError):
    pass


def http(method: str, path: str, body: Optional[dict] = None, timeout: int = 25) -> dict[str, Any]:
    """One authenticated request to the VPS. The token goes to curl via stdin config, never argv."""
    token = TOKEN_FILE.read_text().strip()
    cmd = ["curl", "-sS", "--connect-timeout", "8", "--max-time", str(timeout), "-K", "-",
           "-X", method, "-w", "\n%{http_code}", URL + path]
    if SOCKS:
        cmd[1:1] = ["--socks5-hostname", SOCKS]
    tmp = None
    try:
        if body is not None:
            fd, tmp = tempfile.mkstemp(prefix="mirror-", dir=str(STATE_DIR))
            with os.fdopen(fd, "w") as f:
                json.dump(body, f)
            cmd += ["-H", "Content-Type: application/json", "--data-binary", f"@{tmp}"]
        proc = subprocess.run(cmd, input=f'header = "Authorization: Bearer {token}"\n', capture_output=True,
                              text=True, timeout=timeout + 5)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    if proc.returncode != 0:
        raise MirrorHTTPError(f"curl exit {proc.returncode}: {proc.stderr.strip()[:200]}")
    text, _, code = proc.stdout.rpartition("\n")
    if not code.startswith("2"):
        raise MirrorHTTPError(f"HTTP {code}: {text[:200]}")
    return json.loads(text) if text.strip() else {}


# ---------------------------------------------------------------- box DB access
def open_box(db_path: Optional[str] = None) -> sqlite3.Connection:
    c = sqlite3.connect(db_path or BRIEFS_DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=10000")
    return c


def scan(box: sqlite3.Connection) -> dict[str, dict[int, dict[str, Any]]]:
    out: dict[str, dict[int, dict[str, Any]]] = {}
    box.execute("BEGIN")  # one consistent read snapshot across the five tables
    try:
        for t in TABLES:
            out[t] = {r["id"]: dict(r) for r in box.execute(f"SELECT * FROM {t}")}
    finally:
        box.execute("COMMIT")
    return out


def diff(cur: dict[str, dict[int, dict[str, Any]]], st: sqlite3.Connection):
    """Returns (ops, hashes): ops = list of (table, 'up'|'del', id, row|None, hash|None)."""
    ops: list[tuple] = []
    for t in TABLES:
        known = dict(st.execute("SELECT id, h FROM rowstate WHERE tbl=?", (t,)).fetchall())
        dels = [i for i in known if i not in cur[t]]
        if len(dels) > max(20, len(known) // 2) and os.environ.get("BRIEFS_MIRROR_ALLOW_MASS_DELETE") != "1":
            raise RuntimeError(f"refusing to mirror {len(dels)} deletes from {t} (of {len(known)}); "
                               "set BRIEFS_MIRROR_ALLOW_MASS_DELETE=1 if intended")
        for i in dels:
            ops.append((t, "del", i, None, None))
        for i, row in cur[t].items():
            h = row_hash(row)
            if known.get(i) != h:
                ops.append((t, "up", i, row, h))
    return ops


def batches(ops: list[tuple]):
    cur: list[tuple] = []
    size = 0
    for op in ops:
        s = len(json.dumps(op[3], default=str)) if op[3] else 16
        if cur and (size + s > BATCH_BYTES or len(cur) >= BATCH_ROWS):
            yield cur
            cur, size = [], 0
        cur.append(op)
        size += s
    if cur:
        yield cur


def send_batch(st: sqlite3.Connection, batch: list[tuple]) -> dict[str, Any]:
    up: dict[str, list] = {}
    dl: dict[str, list] = {}
    for t, kind, i, row, _h in batch:
        (up.setdefault(t, []).append(row)) if kind == "up" else (dl.setdefault(t, []).append(i))
    resp = http("POST", "/sync/push", {"sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                       "upserts": up, "deletes": dl})
    with st:  # acked: remember what the VPS now has
        for t, kind, i, _row, h in batch:
            if kind == "up":
                st.execute("INSERT INTO rowstate(tbl,id,h) VALUES (?,?,?) ON CONFLICT(tbl,id) DO UPDATE SET h=excluded.h",
                           (t, i, h))
            else:
                st.execute("DELETE FROM rowstate WHERE tbl=? AND id=?", (t, i))
    return resp


def push_cycle(box: sqlite3.Connection, st: sqlite3.Connection, force_heartbeat: bool = False) -> int:
    """Send everything that differs from what the VPS last acknowledged. Returns number of rows sent."""
    cur = scan(box)
    ops = diff(cur, st)
    sent = 0
    for b in batches(ops):
        send_batch(st, b)
        sent += len(b)
    last_ok = float(meta_get(st, "last_ok_epoch", "0") or 0)
    if sent == 0 and (force_heartbeat or time.time() - last_ok >= HEARTBEAT_S):
        http("POST", "/sync/push", {"heartbeat": True})
    meta_set(st, "last_ok_epoch", str(time.time()))
    meta_set(st, "last_ok_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    meta_set(st, "last_error", "")
    if sent:
        meta_set(st, "last_change_sent_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return sent


# ---------------------------------------------------------------- VPS -> box actions
MAP_DDL = ("CREATE TABLE IF NOT EXISTS mirror_vps_actions ("
           "vps_id INTEGER PRIMARY KEY, box_id INTEGER, created_at TEXT)")


def _apply_effect(conn: sqlite3.Connection, item: sqlite3.Row, typ: str, payload: dict) -> None:
    """Same local effects as briefs.actions.create_action (kept in the caller's transaction)."""
    iid = item["id"]
    if typ == "done":
        conn.execute("UPDATE items SET status='done' WHERE id=?", (iid,))
    elif typ == "dismiss":
        conn.execute("UPDATE items SET status='dismissed' WHERE id=?", (iid,))
    elif typ == "snooze" and payload.get("until"):
        conn.execute("UPDATE items SET snooze_until=? WHERE id=?", (payload["until"], iid))
    elif typ == "priority" and payload.get("delta") in (-1, 1):
        p = item["priority"] if item["priority"] is not None else 3
        conn.execute("UPDATE items SET priority=? WHERE id=?", (max(1, min(5, p + payload["delta"])), iid))
    elif typ == "reopen":
        conn.execute("UPDATE items SET status='open', snooze_until=NULL WHERE id=?", (iid,))


VPS_ACTION_TYPES = ("done", "dismiss", "snooze", "priority", "reply", "reopen")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _vps_author() -> str:
    """L-3: VPS-made actions are recorded as the (first) allowed login, never as whatever the VPS claims."""
    logins = sorted(ALLOWED_LOGINS)
    return logins[0] if logins else "vps"


def validate_vps_action(a: dict[str, Any]) -> str:
    """L-3: allow-list type/status and check the payload shape of an action pulled from the VPS.
    Returns a normalised payload_json; raises ValueError on anything unexpected."""
    typ = a.get("type")
    if typ not in VPS_ACTION_TYPES:
        raise ValueError(f"action type {typ!r} not allowed")
    if a.get("status") not in VPS_CONTROLLED:
        raise ValueError(f"status {a.get('status')!r} not allowed")
    raw = a.get("payload_json") or "{}"
    if not isinstance(raw, str) or len(raw) > 20000:
        raise ValueError("bad payload")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if typ == "snooze":
        if not isinstance(payload.get("until"), str) or not _DATE_RE.match(payload["until"]):
            raise ValueError("snooze needs until=YYYY-MM-DD")
    elif typ == "priority":
        if payload.get("delta") not in (-1, 1) or isinstance(payload.get("delta"), bool):
            raise ValueError("priority needs delta -1 or 1")
    elif typ == "reply":
        if not isinstance(payload.get("text"), str) or not payload["text"].strip():
            raise ValueError("reply needs text")
    for k, v in payload.items():
        if not isinstance(k, str) or not isinstance(v, (str, int, float, bool, type(None))):
            raise ValueError("payload values must be scalars")
    return raw  # unchanged text, so box/VPS payload comparisons stay stable


def apply_vps_action(conn: sqlite3.Connection, entry: dict[str, Any]) -> dict[str, Any]:
    """Store one VPS-made action on the box exactly once. Returns the ack item for the VPS."""
    a = entry["action"]
    vps_id = int(entry["vps_id"])
    ack: dict[str, Any] = {"vps_id": vps_id, "version": entry.get("version")}
    try:
        a = dict(a, payload_json=validate_vps_action(a))
    except (ValueError, TypeError) as ex:
        ack["rejected"] = f"invalid action: {ex}"[:200]
        return ack
    conn.execute(MAP_DDL)
    conn.execute("BEGIN IMMEDIATE")
    try:
        m = conn.execute("SELECT box_id FROM mirror_vps_actions WHERE vps_id=?", (vps_id,)).fetchone()
        now = now_et().isoformat()
        if m is None and entry.get("mapped"):
            raise RuntimeError("VPS says mapped but box has no mapping; refusing to guess")
        if m is None:
            item = conn.execute("SELECT * FROM items WHERE id=?", (a["item_id"],)).fetchone() if a.get("item_id") else None
            if item is None:
                conn.execute("ROLLBACK")
                ack["rejected"] = f"item {a.get('item_id')} not on box"
                return ack
            cur = conn.execute(
                "INSERT INTO actions (item_id, bot_id, type, payload_json, author, status, wake_requested, wake_status,"
                " created_at, updated_at, rank) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (a["item_id"], item["bot_id"], a["type"], a["payload_json"], _vps_author(),
                 a["status"], a.get("wake_requested") or 0, "", a.get("created_at") or now, now, None))
            box_id = cur.lastrowid
            _apply_effect(conn, item, a["type"], json.loads(a["payload_json"] or "{}"))
            conn.execute("INSERT INTO mirror_vps_actions(vps_id, box_id, created_at) VALUES (?,?,?)",
                         (vps_id, box_id, now))
        else:
            box_id = m[0]
            row = conn.execute("SELECT status, payload_json FROM actions WHERE id=?", (box_id,)).fetchone()
            # Box is authoritative once it moved the action past the states the VPS UI controls.
            if row and row["status"] in VPS_CONTROLLED and (row["status"], row["payload_json"]) != (a["status"], a["payload_json"]):
                conn.execute("UPDATE actions SET status=?, payload_json=?, updated_at=? WHERE id=?",
                             (a["status"], a["payload_json"], now, box_id))
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    ack["box_id"] = box_id
    return ack


def pull_cycle(db_path: Optional[str] = None, timeout: int = 25) -> int:
    resp = http("GET", "/sync/actions", timeout=timeout)
    entries = resp.get("actions") or []
    if not entries:
        return 0
    conn = open_box(db_path)
    try:
        acks = []
        for e in entries:  # one bad entry must not block the others
            try:
                acks.append(apply_vps_action(conn, e))
            except Exception as ex:
                log(f"could not apply VPS action {e.get('vps_id')}: {ex}")
        try:  # same ordering rule the dashboard uses (rank 1..N over submitted actions)
            from briefs.actions import rerank_submitted
            rerank_submitted(conn)
        except Exception as e:  # ranking is cosmetic; never fail the pull for it
            log(f"rerank skipped: {e}")
    finally:
        conn.close()
    if acks:
        http("POST", "/sync/actions/ack", {"items": acks}, timeout=timeout)
    return len(acks)


def best_effort_pull() -> None:
    """Called from `brief inbox|actions` so a wake-up never races the poll. Silent, short, never raises."""
    try:
        if not TOKEN_FILE.exists() or os.environ.get("BRIEFS_MIRROR_HOOK", "1") == "0":
            return
        if os.path.abspath(os.environ.get("BRIEFS_DB", BRIEFS_DB)) != os.path.abspath(
                str(Path(__file__).resolve().parent.parent / "data" / "briefs.db")):
            return  # scratch/test databases never talk to the real VPS
        st = state_db()
        if time.time() - float(meta_get(st, "pull_fail_epoch", "0") or 0) < 30:
            return  # VPS recently unreachable: do not slow the bot down
        try:
            pull_cycle(timeout=5)
        except Exception as e:
            meta_set(st, "pull_fail_epoch", str(time.time()))
            log(f"pre-read pull skipped: {e}")
    except Exception:
        pass


# ---------------------------------------------------------------- status / lag record
def write_status(st: sqlite3.Connection, extra: Optional[dict] = None) -> dict[str, Any]:
    last_ok = float(meta_get(st, "last_ok_epoch", "0") or 0)
    s = {"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "last_push_ok_at": meta_get(st, "last_ok_at"),
         "box_side_lag_seconds": int(time.time() - last_ok) if last_ok else None,
         "last_change_sent_at": meta_get(st, "last_change_sent_at"),
         "last_error": meta_get(st, "last_error") or "", "pid": os.getpid()}
    s.update(extra or {})
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_DIR / "status.json.tmp"
    tmp.write_text(json.dumps(s, indent=2))
    tmp.replace(STATUS_DIR / "status.json")
    return s


def lag_check(st: sqlite3.Connection) -> dict[str, Any]:
    """Record mirror lag in the shared dir. Writes files only; nothing is sent to anyone."""
    extra: dict[str, Any] = {}
    try:
        v = http("GET", "/sync/status", timeout=15)
        extra.update(vps_last_push_at=v.get("last_push_at"), vps_lag_seconds=v.get("lag_seconds"),
                     vps_stale=v.get("stale"), vps_pending_for_box=v.get("pending_for_box"), vps_counts=v.get("counts"))
    except Exception as e:
        extra["vps_unreachable"] = str(e)[:200]
    s = write_status(st, extra)
    lag = s.get("box_side_lag_seconds")
    state = "ok" if (lag is not None and lag < 300 and "vps_unreachable" not in extra) else "LAGGING"
    prev = meta_get(st, "lag_state")
    last_logged = float(meta_get(st, "lag_logged_epoch", "0") or 0)
    if state != prev or time.time() - last_logged > 1800:
        with open(STATUS_DIR / "lag.log", "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {state} box_lag_s={lag} "
                    f"vps_lag_s={extra.get('vps_lag_seconds')} err={s['last_error'][:120]!r}\n")
        meta_set(st, "lag_state", state)
        meta_set(st, "lag_logged_epoch", str(time.time()))
    return s


# ---------------------------------------------------------------- commands
_LAST_ERR: list = ["", 0.0]


def cycle(box: sqlite3.Connection, st: sqlite3.Connection) -> None:
    errs = []
    try:
        pull_cycle()
    except Exception as e:
        errs.append(f"pull: {e}")
    try:
        push_cycle(box, st)
    except Exception as e:
        errs.append(f"push: {e}")
    if errs:
        msg = "; ".join(errs)[:500]
        meta_set(st, "last_error", msg)
        # During an outage do not repeat the same line every cycle: log on change, else every 5 minutes.
        if msg != _LAST_ERR[0] or time.time() - _LAST_ERR[1] > 300:
            log(msg[:300])
            _LAST_ERR[:] = [msg, time.time()]
    else:
        if _LAST_ERR[0]:
            log("recovered; mirror is syncing again")
        _LAST_ERR[:] = ["", 0.0]


def cmd_run(_a: argparse.Namespace) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(STATE_DIR / "daemon.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another mirror daemon is running; exiting")
        return
    st = state_db()
    box = open_box()
    log(f"daemon started pid={os.getpid()} url={URL} interval={INTERVAL}s")
    last_dv, last_wall, last_lag = None, time.time(), 0.0
    while True:
        now = time.time()
        if now - last_wall > 45:  # the loop sleeps ~10 s; a bigger jump means the box was frozen/paused
            msg = f"time gap of {int(now - last_wall)}s since the last cycle (box paused/frozen?)"
            log(msg)
            STATUS_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATUS_DIR / "lag.log", "a") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} GAP {msg}\n")
        last_wall = now
        try:
            dv = box.execute("PRAGMA data_version").fetchone()[0]
            # Pull every cycle (cheap GET); scan+push only when the DB changed, an error is pending, or heartbeat is due.
            errs_pending = bool(meta_get(st, "last_error"))
            if dv != last_dv or errs_pending or time.time() - float(meta_get(st, "last_ok_epoch", "0") or 0) >= HEARTBEAT_S:
                cycle(box, st)
                last_dv = dv
            else:
                try:
                    pull_cycle()
                except Exception as e:
                    meta_set(st, "last_error", f"pull: {e}"[:500])
            if time.time() - last_lag > 300:
                lag_check(st)
                last_lag = time.time()
        except Exception as e:
            log(f"cycle error: {e}")
            try:
                box = open_box()
            except Exception:
                pass
        time.sleep(max(1.0, INTERVAL - (time.time() - now)))


def cmd_once(_a: argparse.Namespace) -> None:
    st = state_db()
    box = open_box()
    n_pull = pull_cycle()
    n_push = push_cycle(box, st, force_heartbeat=True)
    s = write_status(st)
    print(json.dumps({"pulled_actions": n_pull, "pushed_rows": n_push, "status": s}, indent=2))


def cmd_pull(_a: argparse.Namespace) -> None:
    print(json.dumps({"pulled_actions": pull_cycle()}))


def cmd_backfill(a: argparse.Namespace) -> None:
    """Consistent snapshot of the box DB via the SQLite backup API, and rowstate seeded from that snapshot.
    Ship `--out` to the VPS as its initial DB; the first normal cycle then sends only what changed since."""
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    src = open_box()
    dst = sqlite3.connect(str(out))
    src.backup(dst)
    src.close()
    dst.execute("DROP TABLE IF EXISTS mirror_vps_actions")  # box-private bookkeeping, not for the replica
    dst.commit()
    dst.execute("PRAGMA journal_mode=DELETE")
    dst.row_factory = sqlite3.Row
    cur = scan(dst)
    dst.close()
    st = state_db()
    with st:
        st.execute("DELETE FROM rowstate")
        for t in TABLES:
            st.executemany("INSERT INTO rowstate(tbl,id,h) VALUES (?,?,?)",
                           [(t, i, row_hash(r)) for i, r in cur[t].items()])
    print(json.dumps({"snapshot": str(out), "rows": {t: len(cur[t]) for t in TABLES}}))


def cmd_status(_a: argparse.Namespace) -> None:
    st = state_db()
    print(json.dumps(write_status(st), indent=2))


def cmd_lag(_a: argparse.Namespace) -> None:
    print(json.dumps(lag_check(state_db()), indent=2))


def cmd_verify(_a: argparse.Namespace) -> None:
    """Compare per-table row digests of the box DB and the VPS DB."""
    box = open_box()
    cur = scan(box)
    mine = {}
    for t in TABLES:
        rows = {i: {k: v for k, v in r.items() if k != "wake_status"} for i, r in cur[t].items()}
        mine[t] = {"count": len(rows), "digest": row_hash({str(i): row_hash(r) for i, r in sorted(rows.items())})}
    theirs = http("GET", "/sync/status?digest=1")["digests"]
    bad = {t: (mine[t], theirs.get(t)) for t in TABLES if mine[t] != theirs.get(t)}
    print(json.dumps({"match": not bad, "differences": bad}, indent=2))
    sys.exit(0 if not bad else 1)


def main() -> None:
    p = argparse.ArgumentParser(prog="briefs.mirror")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("run", cmd_run), ("once", cmd_once), ("pull", cmd_pull), ("status", cmd_status),
                     ("lag-check", cmd_lag), ("verify", cmd_verify)):
        sub.add_parser(name).set_defaults(fn=fn)
    b = sub.add_parser("backfill")
    b.add_argument("--out", required=True)
    b.set_defaults(fn=cmd_backfill)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
