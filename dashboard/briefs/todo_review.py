"""Local-only TODO review page (/todo-review).

Displays exported Saner + Shortwave candidate TODOs and stores human review
decisions (Keep, Done, Drop, Changed + note, owner reassignment) strictly in
dedicated local SQLite tables (todo_review_items, todo_review_decisions,
todo_review_audit).

SAFETY INVARIANTS:
- Candidate titles, notes, links, and provenance are treated as untrusted DATA.
- Never modifies Saner or source export files.
- Never inserts into `actions` or triggers any bot wake/dispatch or external HTTP/CLI call.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from briefs.config import TZ_ET, now_et, state_home
from briefs.parse import (
    dodosend_thread_url,
    is_canonical_dodosend_url,
    parse_gmail_thread_ref,
    rewrite_gmail_link_for_display,
)

# Owner choices for reassignment. Override with BRIEFS_TODO_REVIEW_OWNERS (comma-separated);
# "Other" is always appended as the fallback bucket.
_DEFAULT_OWNERS: tuple[str, ...] = ("Chess", "Coding", "Money", "Family", "Friends", "Radar", "Zero")


def _load_owners() -> tuple[str, ...]:
    raw = os.environ.get("BRIEFS_TODO_REVIEW_OWNERS", "")
    names = [n.strip() for n in raw.split(",") if n.strip()] or list(_DEFAULT_OWNERS)
    out: list[str] = []
    for n in names:
        if n.lower() != "other" and n not in out:
            out.append(n)
    return tuple(out) + ("Other",)


ALLOWED_OWNERS: tuple[str, ...] = _load_owners()
_OWNER_LOOKUP: dict[str, str] = {o.lower(): o for o in ALLOWED_OWNERS}

DECISION_VALUES: tuple[str, ...] = ("keep", "done", "drop", "changed")
DECISION_LABELS: dict[str, str] = {
    "keep": "Keep",
    "done": "Done",
    "drop": "Drop",
    "changed": "Changed",
}

SORT_OPTIONS: tuple[tuple[str, str], ...] = (
    ("age_desc", "Age: oldest known first (unknown last)"),
    ("age_asc", "Age: newest known first (unknown last)"),
    ("owner", "Owner (A-Z, then oldest first)"),
    ("source", "Source (A-Z, then oldest first)"),
    ("title", "Title (A-Z)"),
)
_VALID_SORTS = {k for k, _ in SORT_OPTIONS}

# Mail account used for https://dodosend.com/t/ links when a row carries none (host TOML/env).
def _default_account() -> str:
    acct = os.environ.get("BRIEFS_TODO_REVIEW_ACCOUNT", "user@example.com").strip()
    return acct if acct and re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", acct) else ""

DEFAULT_ACCOUNT = _default_account()  # import-time snapshot; prefer _default_account() at call time
TASK_KEY_RE = re.compile(r"^[A-Za-z0-9:._/-]{1,200}$")
_HEX16_RE = re.compile(r"^[0-9a-fA-F]{16}$")
_MAILTO_SAFE_RE = re.compile(r"^mailto:[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_UNSAFE_URL_CHARS_RE = re.compile(r"[\x00-\x20\x7f<>\"'`]")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_NOTE_LEN = 4000
MAX_AUDIT_LABEL_LEN = 120

# Export location: BRIEFS_TODO_REVIEW_EXPORT_DIR, else <state home>/todo-review.
# Expected files: todo-review-candidates.json and (optional) shortwave-thread-map.json.


def export_dir() -> Path:
    env_d = os.environ.get("BRIEFS_TODO_REVIEW_EXPORT_DIR")
    return Path(env_d) if env_d else state_home() / "todo-review"

_sync_cache: dict[str, Any] = {}


def ensure_schema(conn: Any) -> None:
    """Ensure dedicated TODO review and audit tables exist."""
    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS todo_review_items (
                task_key TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                source_status TEXT NOT NULL,
                created_at TEXT NULL,
                due_at TEXT NULL,
                proposed_owner TEXT NOT NULL,
                links_json TEXT NOT NULL DEFAULT '[]',
                provenance_json TEXT NOT NULL DEFAULT '[]',
                imported_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS todo_review_decisions (
                task_key TEXT PRIMARY KEY,
                decision TEXT NULL CHECK (decision IS NULL OR decision IN ('keep', 'done', 'drop', 'changed')),
                note TEXT NOT NULL DEFAULT '',
                assigned_owner TEXT NOT NULL,
                reviewer TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS todo_review_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_key TEXT NOT NULL,
                prev_decision TEXT NULL,
                new_decision TEXT NULL,
                prev_owner TEXT NULL,
                new_owner TEXT NULL,
                prev_note TEXT NULL,
                new_note TEXT NULL,
                reviewer TEXT NOT NULL,
                audit_label TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_todo_review_audit_key ON todo_review_audit(task_key, id DESC);
            """
        )


def dataset_path() -> Path:
    env_p = os.environ.get("BRIEFS_TODO_REVIEW_DATASET")
    if env_p:
        return Path(env_p)
    return export_dir() / "todo-review-candidates.json"


def threads_path() -> Optional[Path]:
    env_t = os.environ.get("BRIEFS_TODO_REVIEW_THREADS")
    if env_t is not None:
        return Path(env_t) if env_t.strip() else None
    # Only use the export dir's Shortwave thread mapping automatically when using the default dataset
    if os.environ.get("BRIEFS_TODO_REVIEW_DATASET"):
        return None
    default = export_dir() / "shortwave-thread-map.json"
    return default if default.exists() else None


def normalize_owner(val: Any) -> str:
    """Normalize a proposed owner to one of ALLOWED_OWNERS; Unknown or unrecognized -> 'Other'."""
    if not isinstance(val, str):
        return "Other"
    cleaned = val.strip()
    if not cleaned:
        return "Other"
    return _OWNER_LOOKUP.get(cleaned.lower(), "Other")


def validate_owner_strict(val: Any) -> str:
    """Strictly validate user-submitted owner reassignment against ALLOWED_OWNERS."""
    if not isinstance(val, str):
        raise ValueError("Invalid owner")
    cleaned = val.strip()
    if cleaned not in ALLOWED_OWNERS:
        raise ValueError(f"Invalid owner: {cleaned!r}")
    return cleaned


def clean_text_field(val: Any, max_len: int = MAX_NOTE_LEN) -> str:
    if val is None:
        return ""
    if not isinstance(val, str):
        val = str(val)
    cleaned = _CTRL_CHARS_RE.sub("", val).strip()
    if len(cleaned) > max_len:
        raise ValueError(f"Text exceeds maximum length ({max_len} chars)")
    return cleaned


def is_safe_review_url(url: Any) -> bool:
    """Strict URL validator for review links: allows https/http/mailto and
    https://dodosend.com/t/<exactly 16 hex> with optional account=valid email only.
    Rejects javascript:, data:, vbscript:, file:, dodosend://, protocol-relative //,
    control chars, and malformed URLs. Other https hosts (including Gmail search
    links) remain allowed.
    """
    if not isinstance(url, str):
        return False
    u = url.strip()
    if not u or len(u) > 2048:
        return False
    if _UNSAFE_URL_CHARS_RE.search(u):
        return False

    compact = re.sub(r"[\x00-\x20\x7f]", "", u).lower()
    if compact.startswith(("//", "\\\\", "/\\")):
        return False

    if compact.startswith("dodosend:"):
        return False

    if compact.startswith("mailto:"):
        return bool(_MAILTO_SAFE_RE.match(u))

    m = re.match(r"^([a-z][a-z0-9+.\-]*):", compact)
    if not m:
        return False
    scheme = m.group(1)
    if scheme not in ("https", "http"):
        return False

    parsed = urllib.parse.urlsplit(u)
    if parsed.scheme.lower() not in ("https", "http") or not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    if host == "dodosend.com":
        return is_canonical_dodosend_url(u)
    return True


def _decode_gmail_search_hint(url: str) -> str:
    try:
        frag = urllib.parse.urlsplit(url).fragment
        if frag.startswith("search/"):
            q = urllib.parse.unquote_plus(frag[len("search/"):])
            return q[:140]
    except Exception:
        pass
    return "Open Gmail search"


def build_email_links(
    raw_links: Any,
    provenance: list[dict[str, Any]],
    row_extra: Optional[dict[str, Any]] = None,
    thread_lookup: Optional[dict[str, dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Build safe email/thread links for a row:
    - Known 16-hex Gmail thread IDs -> one https://dodosend.com/t/<tid>?account=<email> link.
    - Preserves Gmail #search/... links only when no thread ID is known (no Dodosend search URL).
    - Preserves safe Shortwave / https links.
    - Accepts legacy dodosend://, dodo-link.pages.dev, and mail.google.com thread URLs as input.
    - Strips any unsafe URL (javascript:, data:, dodosend:// output, malformed URLs, etc.).
    """
    extra = row_extra or {}
    t_lookup = thread_lookup or {}

    known_threads: list[tuple[str, str]] = []  # (tid_lower, account)
    seen_tids: set[str] = set()

    def _add_tid(tid_candidate: Any, acct_candidate: Optional[str] = None) -> None:
        if not isinstance(tid_candidate, str):
            return
        t = tid_candidate.strip().lower()
        if not _HEX16_RE.match(t) or t in seen_tids:
            return
        acct = _default_account()
        if acct_candidate and re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", acct_candidate):
            acct = acct_candidate
        seen_tids.add(t)
        known_threads.append((t, acct))

    # 1. Explicit thread_id on row
    if extra.get("thread_id"):
        _add_tid(extra.get("thread_id"))

    # 2. Thread lookup from Shortwave migration mapping by shortwave_id
    for prov in provenance:
        sw_id = prov.get("shortwave_id")
        if isinstance(sw_id, str) and sw_id in t_lookup:
            entry = t_lookup[sw_id]
            _add_tid(entry.get("thread_id"))

    # Normalize raw_links list (strings or dicts), plus optional dodosend_link / gmail_link fields
    candidate_urls: list[tuple[str, Optional[str]]] = []
    for field_name in ("dodosend_link", "gmail_link"):
        fv = extra.get(field_name)
        if isinstance(fv, str) and fv.strip():
            candidate_urls.append((fv.strip(), None))

    for item in raw_links or []:
        if isinstance(item, str):
            candidate_urls.append((item.strip(), None))
        elif isinstance(item, dict) and isinstance(item.get("url"), str):
            lbl = item.get("label") if isinstance(item.get("label"), str) else None
            candidate_urls.append((item["url"].strip(), lbl))

    # 3. Extract any 16-hex thread IDs present in accepted input URL forms
    for raw_u, _ in candidate_urls:
        ref = parse_gmail_thread_ref(raw_u)
        if ref:
            _add_tid(ref[0], ref[1])

    out: list[dict[str, Any]] = []
    emitted_urls: set[str] = set()

    # Emit one canonical Dodosend https link for every known 16-hex thread ID
    multi_tid = len(known_threads) > 1
    for idx, (tid, acct) in enumerate(known_threads, start=1):
        suffix = f" {idx}" if multi_tid else ""
        dodo_url = dodosend_thread_url(tid, acct)
        out.append({
            "kind": "dodosend",
            "label": f"Dodosend{suffix}",
            "url": dodo_url,
            "external": True,
            "tooltip": f"Open thread {tid} in Dodosend",
        })
        emitted_urls.add(dodo_url)

    # Preserve Gmail search links (only if no thread id is known), Shortwave, other safe links
    search_links: list[tuple[str, Optional[str]]] = []
    shortwave_links: list[tuple[str, Optional[str]]] = []
    other_links: list[tuple[str, Optional[str]]] = []

    for raw_u, custom_lbl in candidate_urls:
        ref = parse_gmail_thread_ref(raw_u)
        if ref and ref[0] in seen_tids:
            continue
        if not is_safe_review_url(raw_u):
            continue
        if raw_u in emitted_urls:
            continue
        parsed = urllib.parse.urlsplit(raw_u)
        host = parsed.netloc.lower()
        if "mail.google.com" in host and parsed.fragment.startswith("search/"):
            search_links.append((raw_u, custom_lbl))
            emitted_urls.add(raw_u)
            continue
        if "shortwave.com" in host:
            shortwave_links.append((raw_u, custom_lbl))
            emitted_urls.add(raw_u)
            continue
        other_links.append((raw_u, custom_lbl))
        emitted_urls.add(raw_u)

    # No Dodosend search URL form is known; keep Gmail search only when no thread id exists.
    if not known_threads:
        multi_search = len(search_links) > 1
        for idx, (u, lbl) in enumerate(search_links, start=1):
            out.append({
                "kind": "gmail-search",
                "label": lbl or (f"Gmail search {idx}" if multi_search else "Gmail search"),
                "url": u,
                "external": True,
                "tooltip": _decode_gmail_search_hint(u),
            })

    multi_sw = len(shortwave_links) > 1
    for idx, (u, lbl) in enumerate(shortwave_links, start=1):
        out.append({
            "kind": "shortwave",
            "label": lbl or (f"Shortwave {idx}" if multi_sw else "Shortwave"),
            "url": u,
            "external": True,
            "tooltip": "Archived Shortwave thread link",
        })

    for idx, (u, lbl) in enumerate(other_links, start=1):
        out.append({
            "kind": "link",
            "label": lbl or (f"Link {idx}" if len(other_links) > 1 else "Link"),
            "url": u,
            "external": True,
            "tooltip": u,
        })

    return out


def _norm_title_for_match(title: str) -> str:
    return re.sub(r"\W+", " ", (title or "").lower()).strip()


def _dedup_provenance(prov_list: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in prov_list or []:
        if not isinstance(p, dict):
            continue
        sig = json.dumps(p, sort_keys=True, separators=(",", ":"))
        if sig not in seen:
            seen.add(sig)
            out.append(dict(p))
    return out


def canonical_task_key(row: dict[str, Any], provenance: list[dict[str, Any]]) -> str:
    """Compute deterministic stable task key from provenance or validated row key."""
    for p in provenance:
        sw_id = p.get("shortwave_id")
        if isinstance(sw_id, str) and sw_id.strip():
            cand = f"shortwave:{sw_id.strip()}"
            if TASK_KEY_RE.match(cand):
                return cand
    for p in provenance:
        st_id = p.get("saner_task_id")
        if isinstance(st_id, str) and st_id.strip():
            cand = f"saner-task:{st_id.strip()}"
            if TASK_KEY_RE.match(cand):
                return cand

    raw_key = row.get("key")
    if isinstance(raw_key, str) and TASK_KEY_RE.match(raw_key.strip()):
        return raw_key.strip()

    for p in provenance:
        sn_id = p.get("saner_note_id")
        heading = p.get("todo_heading")
        if isinstance(sn_id, str) and sn_id.strip():
            if heading is not None and str(heading).strip():
                cand = f"saner-note:{sn_id.strip()}:todo:{str(heading).strip()}"
            else:
                cand = f"saner-note:{sn_id.strip()}"
            if TASK_KEY_RE.match(cand):
                return cand

    digest = hashlib.sha256(_norm_title_for_match(str(row.get("title") or "")).encode("utf-8")).hexdigest()[:16]
    return f"cand:{digest}"


def _is_todos2import_note_row(row: dict[str, Any], prov: list[dict[str, Any]]) -> bool:
    for p in prov:
        if p.get("todo_heading") is not None:
            return True
        nt = str(p.get("note_title") or "").lower()
        if "todos2import" in nt:
            return True
    return False


def _merge_source_label(existing_source: str, added_source: str, prov: list[dict[str, Any]]) -> str:
    has_sw = any(p.get("shortwave_id") for p in prov) or "shortwave" in existing_source.lower() or "shortwave" in added_source.lower()
    has_sn = (
        any(p.get("saner_task_id") or p.get("saner_note_id") for p in prov)
        or "saner" in existing_source.lower()
        or "saner" in added_source.lower()
    )
    if has_sw and has_sn:
        return "Saner + Shortwave"
    if has_sw:
        return "Shortwave"
    if has_sn:
        return "Saner"
    return existing_source or added_source or "Saner"


def deduplicate_candidates(
    raw_rows: list[dict[str, Any]],
    thread_lookup: Optional[dict[str, dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Normalize and merge duplicate/imported Shortwave+TODOs2import rows deterministically
    while preserving all distinct candidate rows and all provenance.
    """
    merged_by_key: dict[str, dict[str, Any]] = {}
    key_order: list[str] = []

    # First pass: index Shortwave rows by normalized title and Gmail links so unmerged TODOs2import
    # rows merge into the stable shortwave:<id> key without duplicating rows.
    sw_by_title: dict[str, str] = {}
    sw_by_gmail_link: dict[str, str] = {}

    prepared: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
    for r in raw_rows or []:
        if not isinstance(r, dict):
            continue
        prov = _dedup_provenance(r.get("provenance") or [])
        key = canonical_task_key(r, prov)
        prepared.append((key, r, prov))
        if key.startswith("shortwave:"):
            nt = _norm_title_for_match(str(r.get("title") or ""))
            if nt and nt not in sw_by_title:
                sw_by_title[nt] = key
            for lk in r.get("links") or []:
                lk_s = lk if isinstance(lk, str) else (lk.get("url") if isinstance(lk, dict) else "")
                if not isinstance(lk_s, str) or not lk_s:
                    continue
                ref = parse_gmail_thread_ref(lk_s)
                dedup_k = f"gmail:{ref[0]}" if ref else (lk_s if "mail.google.com" in lk_s else None)
                if dedup_k and dedup_k not in sw_by_gmail_link:
                    sw_by_gmail_link[dedup_k] = key

    for key, r, prov in prepared:
        target_key = key
        if not key.startswith("shortwave:") and _is_todos2import_note_row(r, prov):
            nt = _norm_title_for_match(str(r.get("title") or ""))
            if nt and nt in sw_by_title:
                target_key = sw_by_title[nt]
            else:
                for lk in r.get("links") or []:
                    lk_s = lk if isinstance(lk, str) else (lk.get("url") if isinstance(lk, dict) else "")
                    if isinstance(lk_s, str) and lk_s:
                        ref = parse_gmail_thread_ref(lk_s)
                        dedup_k = f"gmail:{ref[0]}" if ref else lk_s
                        if dedup_k in sw_by_gmail_link:
                            target_key = sw_by_gmail_link[dedup_k]
                        break

        title = _CTRL_CHARS_RE.sub("", str(r.get("title") or "Untitled")).strip() or "Untitled"
        source = _CTRL_CHARS_RE.sub("", str(r.get("source") or "Saner")).strip() or "Saner"
        status = _CTRL_CHARS_RE.sub("", str(r.get("status") or "open")).strip() or "open"
        created_at = str(r["created_at"]).strip() if r.get("created_at") else None
        due_at = str(r["due_at"]).strip() if r.get("due_at") else None
        proposed_owner = normalize_owner(r.get("proposed_owner"))
        raw_links = list(r.get("links") or [])

        if target_key not in merged_by_key:
            merged_by_key[target_key] = {
                "task_key": target_key,
                "title": title,
                "source": _merge_source_label(source, source, prov),
                "source_status": status,
                "created_at": created_at,
                "due_at": due_at,
                "proposed_owner": proposed_owner,
                "_raw_links": raw_links,
                "_extra": {
                    "thread_id": r.get("thread_id"),
                    "dodosend_link": r.get("dodosend_link"),
                    "gmail_link": r.get("gmail_link"),
                },
                "provenance": prov,
            }
            key_order.append(target_key)
        else:
            existing = merged_by_key[target_key]
            combined_prov = _dedup_provenance(existing["provenance"] + prov)
            existing["provenance"] = combined_prov
            existing["source"] = _merge_source_label(existing["source"], source, combined_prov)
            existing["_raw_links"] = existing["_raw_links"] + raw_links
            if not existing["created_at"] and created_at:
                existing["created_at"] = created_at
            if not existing["due_at"] and due_at:
                existing["due_at"] = due_at
            if existing["proposed_owner"] == "Other" and proposed_owner != "Other":
                existing["proposed_owner"] = proposed_owner
            for ek in ("thread_id", "dodosend_link", "gmail_link"):
                if not existing["_extra"].get(ek) and r.get(ek):
                    existing["_extra"][ek] = r.get(ek)

    out: list[dict[str, Any]] = []
    for k in key_order:
        item = merged_by_key[k]
        links = build_email_links(
            item.pop("_raw_links"),
            item["provenance"],
            row_extra=item.pop("_extra"),
            thread_lookup=thread_lookup,
        )
        item["links"] = links
        out.append(item)
    return out


def _load_thread_lookup() -> dict[str, dict[str, Any]]:
    tp = threads_path()
    if not tp or not tp.exists():
        return {}
    try:
        raw = json.loads(tp.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for entry in raw:
        if isinstance(entry, dict) and isinstance(entry.get("sw_id"), str):
            out[entry["sw_id"]] = entry
    return out


def load_raw_export(path: Optional[Path] = None) -> dict[str, Any]:
    p = path or dataset_path()
    if not p.exists():
        raise HTTPException(status_code=404, detail="TODO review candidate export not found")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        raise ValueError("Invalid TODO review dataset format")
    return data


def sync_candidates(
    conn: Any,
    dataset: dict[str, Any],
    thread_lookup: Optional[dict[str, dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Idempotently upsert candidate metadata into todo_review_items.
    NEVER modifies todo_review_decisions or todo_review_audit, so user review
    decisions and owner reassignments always survive reimports.
    """
    ensure_schema(conn)
    t_lookup = thread_lookup if thread_lookup is not None else _load_thread_lookup()
    normalized_rows = deduplicate_candidates(dataset.get("rows") or [], thread_lookup=t_lookup)
    ts = now_et().isoformat()
    with conn:
        for r in normalized_rows:
            conn.execute(
                """
                INSERT INTO todo_review_items (
                    task_key, title, source, source_status, created_at, due_at,
                    proposed_owner, links_json, provenance_json, imported_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_key) DO UPDATE SET
                    title = excluded.title,
                    source = excluded.source,
                    source_status = excluded.source_status,
                    created_at = excluded.created_at,
                    due_at = excluded.due_at,
                    proposed_owner = excluded.proposed_owner,
                    links_json = excluded.links_json,
                    provenance_json = excluded.provenance_json,
                    updated_at = excluded.updated_at
                """,
                (
                    r["task_key"],
                    r["title"],
                    r["source"],
                    r["source_status"],
                    r["created_at"],
                    r["due_at"],
                    r["proposed_owner"],
                    json.dumps(r["links"], ensure_ascii=False),
                    json.dumps(r["provenance"], ensure_ascii=False),
                    ts,
                    ts,
                ),
            )
    return normalized_rows


def ensure_synced(conn: Any) -> tuple[dict[str, Any], list[str]]:
    """Ensure the active dataset file is synced into todo_review_items for this DB."""
    ensure_schema(conn)
    p = dataset_path()
    if not p.exists():
        raise HTTPException(status_code=404, detail="TODO review candidate export not found")
    tp = threads_path()
    t_mtime = tp.stat().st_mtime_ns if (tp and tp.exists()) else None
    stat_sig = (str(p), p.stat().st_mtime_ns, str(tp) if tp else "", t_mtime)

    db_row = conn.execute("PRAGMA database_list").fetchone()
    db_id = db_row[2] if db_row else id(conn)
    cache_entry = _sync_cache.get(db_id)

    if cache_entry and cache_entry.get("sig") == stat_sig:
        # Verify table still has rows (e.g. if DB was recreated at same temp path)
        cnt = conn.execute("SELECT COUNT(*) FROM todo_review_items").fetchone()[0]
        if cnt >= len(cache_entry["keys"]):
            return cache_entry["manifest"], cache_entry["keys"]

    raw_ds = load_raw_export(p)
    norm_rows = sync_candidates(conn, raw_ds)
    keys = [r["task_key"] for r in norm_rows]
    manifest = raw_ds.get("manifest") if isinstance(raw_ds.get("manifest"), dict) else {}
    _sync_cache[db_id] = {"sig": stat_sig, "manifest": manifest, "keys": keys}
    return manifest, keys


def parse_iso_et(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp into America/New_York; return None if absent/invalid. Never invent dates."""
    if not ts_str or not isinstance(ts_str, str):
        return None
    s = ts_str.strip()
    if not s or s.lower() in ("none", "null", "unknown"):
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_ET)
    else:
        dt = dt.astimezone(TZ_ET)
    return dt


def format_et(dt: Optional[datetime]) -> str:
    if dt is None:
        return "Unknown"
    return dt.strftime("%Y-%m-%d %I:%M %p ET")


def format_age(created_dt: Optional[datetime], ref_now: Optional[datetime] = None) -> tuple[str, Optional[int]]:
    if created_dt is None:
        return "Unknown", None
    now = ref_now or now_et()
    delta_secs = (now - created_dt).total_seconds()
    days = max(0, int(delta_secs // 86400))
    if days == 1:
        return "1 day", 1
    return f"{days} days", days


def format_provenance_badges(prov_list: list[dict[str, Any]]) -> list[str]:
    badges: list[str] = []
    for p in prov_list or []:
        if not isinstance(p, dict):
            continue
        if p.get("saner_task_id"):
            tid = str(p["saner_task_id"])
            badges.append(f"Saner task {tid[:8]}")
        if p.get("shortwave_id"):
            sw = str(p["shortwave_id"])
            short_sw = sw.split(":", 1)[0][:10]
            badges.append(f"Shortwave {short_sw}")
        if p.get("saner_note_id"):
            nid = str(p["saner_note_id"])[:8]
            nt = str(p.get("note_title") or "").strip()
            hd = p.get("todo_heading")
            if p.get("candidate_only"):
                badges.append(f"Candidate note {nid}" + (f" ({nt})" if nt else ""))
            elif hd is not None:
                label_nt = nt or f"note {nid}"
                badges.append(f"{label_nt} #TODO {hd}")
            else:
                badges.append(f"Saner note {nid}" + (f" ({nt})" if nt else ""))
    return badges or ["Unknown provenance"]


def _dom_id_for_key(task_key: str) -> str:
    return hashlib.sha256(task_key.encode("utf-8")).hexdigest()[:12]


def _refresh_stored_review_links(raw_links: Any) -> list[dict[str, Any]]:
    """Rewrite legacy thread URLs, drop Gmail fallbacks, and keep search only if no thread id."""
    if not isinstance(raw_links, list):
        return []
    known: list[tuple[str, str, Optional[str]]] = []  # tid, account, label
    seen: set[str] = set()
    rest: list[dict[str, Any]] = []
    search: list[dict[str, Any]] = []
    for lk in raw_links:
        if not isinstance(lk, dict) or not isinstance(lk.get("url"), str):
            continue
        url = lk["url"].strip()
        ref = parse_gmail_thread_ref(url)
        if ref:
            tid, acct = ref
            if tid in seen:
                continue
            seen.add(tid)
            lbl = lk.get("label") if isinstance(lk.get("label"), str) else None
            known.append((tid, acct or _default_account(), lbl))
            continue
        if not is_safe_review_url(url):
            continue
        parsed = urllib.parse.urlsplit(url)
        item = dict(lk)
        item["url"] = url
        if "mail.google.com" in parsed.netloc.lower() and parsed.fragment.startswith("search/"):
            search.append(item)
        else:
            rest.append(item)
    out: list[dict[str, Any]] = []
    multi = len(known) > 1
    for idx, (tid, acct, lbl) in enumerate(known, start=1):
        suffix = f" {idx}" if multi else ""
        out.append({
            "kind": "dodosend",
            "label": lbl or f"Dodosend{suffix}",
            "url": dodosend_thread_url(tid, acct),
            "external": True,
            "tooltip": f"Open thread {tid} in Dodosend",
        })
    if not known:
        out.extend(search)
    out.extend(rest)
    return out


def _hydrate_row(db_row: Any, ref_now: datetime, audit_count: int = 0) -> dict[str, Any]:
    d = dict(db_row)
    task_key = d["task_key"]
    created_dt = parse_iso_et(d.get("item_created_at"))
    due_dt = parse_iso_et(d.get("due_at"))
    age_str, age_days = format_age(created_dt, ref_now=ref_now)

    try:
        raw_links = json.loads(d.get("links_json") or "[]")
    except Exception:
        raw_links = []
    # Re-verify and rewrite legacy thread URLs on read as defense-in-depth
    safe_links = _refresh_stored_review_links(raw_links)

    try:
        prov = json.loads(d.get("provenance_json") or "[]")
    except Exception:
        prov = []

    proposed_owner = normalize_owner(d.get("proposed_owner"))
    assigned_owner_raw = d.get("assigned_owner")
    effective_owner = (
        assigned_owner_raw if (isinstance(assigned_owner_raw, str) and assigned_owner_raw in ALLOWED_OWNERS)
        else proposed_owner
    )
    decision = d.get("decision")
    if decision not in DECISION_VALUES:
        decision = None
    note = d.get("note") or ""
    decided_updated_dt = parse_iso_et(d.get("decision_updated_at"))

    return {
        "task_key": task_key,
        "dom_id": _dom_id_for_key(task_key),
        "title": d.get("title") or "Untitled",
        "source": d.get("source") or "Saner",
        "source_status": d.get("source_status") or "open",
        "created_at": d.get("item_created_at"),
        "created_et": format_et(created_dt),
        "created_ts": created_dt.timestamp() if created_dt is not None else None,
        "due_at": d.get("due_at"),
        "due_et": format_et(due_dt),
        "age_display": age_str,
        "age_days": age_days,
        "proposed_owner": proposed_owner,
        "effective_owner": effective_owner,
        "owner_reassigned": effective_owner != proposed_owner,
        "decision": decision,
        "decision_label": DECISION_LABELS.get(decision, "Undecided") if decision else "Undecided",
        "note": note,
        "reviewer": d.get("reviewer") or "",
        "decision_updated_et": format_et(decided_updated_dt) if decided_updated_dt else "",
        "links": safe_links,
        "provenance": prov,
        "provenance_badges": format_provenance_badges(prov),
        "audit_count": audit_count,
    }


def load_rows_from_db(conn: Any, active_keys: Optional[list[str]] = None) -> list[dict[str, Any]]:
    ensure_schema(conn)
    audit_counts = {
        r["task_key"]: r["cnt"]
        for r in conn.execute(
            "SELECT task_key, COUNT(*) AS cnt FROM todo_review_audit GROUP BY task_key"
        ).fetchall()
    }
    sql = """
        SELECT
            i.task_key,
            i.title,
            i.source,
            i.source_status,
            i.created_at AS item_created_at,
            i.due_at,
            i.proposed_owner,
            i.links_json,
            i.provenance_json,
            d.decision,
            d.note,
            d.assigned_owner,
            d.reviewer,
            d.updated_at AS decision_updated_at
        FROM todo_review_items i
        LEFT JOIN todo_review_decisions d ON d.task_key = i.task_key
    """
    params: list[Any] = []
    if active_keys is not None:
        if not active_keys:
            return []
        placeholders = ",".join("?" for _ in active_keys)
        sql += f" WHERE i.task_key IN ({placeholders})"
        params.extend(active_keys)

    ref_now = now_et()
    db_rows = conn.execute(sql, params).fetchall()
    return [_hydrate_row(r, ref_now, audit_counts.get(r["task_key"], 0)) for r in db_rows]


def get_single_row(conn: Any, task_key: str) -> Optional[dict[str, Any]]:
    rows = load_rows_from_db(conn, active_keys=[task_key])
    return rows[0] if rows else None


def compute_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "total": len(rows),
        "decided": 0,
        "undecided": 0,
        "keep": 0,
        "done": 0,
        "drop": 0,
        "changed": 0,
        "reassigned": 0,
    }
    for r in rows:
        dec = r.get("decision")
        if dec in DECISION_VALUES:
            counts["decided"] += 1
            counts[dec] += 1
        else:
            counts["undecided"] += 1
        if r.get("owner_reassigned"):
            counts["reassigned"] += 1
    return counts


def filter_and_sort_rows(
    rows: list[dict[str, Any]],
    *,
    source: str = "",
    owner: str = "",
    decision: str = "",
    status: str = "",
    q: str = "",
    sort: str = "age_desc",
) -> list[dict[str, Any]]:
    src_f = (source or "").strip().lower()
    own_f = (owner or "").strip().lower()
    dec_f = (decision or "").strip().lower()
    st_f = (status or "").strip().lower()
    q_f = (q or "").strip().lower()

    filtered: list[dict[str, Any]] = []
    for r in rows:
        if src_f and src_f != "all":
            row_src = r["source"].lower()
            if src_f in ("shortwave", "has:shortwave"):
                has_sw = "shortwave" in row_src or any(p.get("shortwave_id") for p in r["provenance"])
                if not has_sw:
                    continue
            elif src_f == "has:saner":
                has_sn = "saner" in row_src or any(
                    p.get("saner_task_id") or p.get("saner_note_id") for p in r["provenance"]
                )
                if not has_sn:
                    continue
            elif row_src != src_f:
                continue

        if own_f and own_f != "all":
            if r["effective_owner"].lower() != own_f:
                continue

        if dec_f and dec_f != "all":
            row_dec = r["decision"]
            if dec_f == "undecided":
                if row_dec is not None:
                    continue
            elif dec_f == "decided":
                if row_dec is None:
                    continue
            elif (row_dec or "") != dec_f:
                continue

        if st_f and st_f != "all":
            if r["source_status"].lower() != st_f:
                continue

        if q_f:
            haystack = " ".join([
                r["title"],
                r["task_key"],
                r["note"],
                r["effective_owner"],
                " ".join(r["provenance_badges"]),
            ]).lower()
            if q_f not in haystack:
                continue

        filtered.append(r)

    sort_key = sort if sort in _VALID_SORTS else "age_desc"

    def _age_oldest_first(item: dict[str, Any]) -> tuple[int, float, str]:
        cts = item["created_ts"]
        if cts is None:
            return (1, 0.0, item["task_key"])
        return (0, cts, item["task_key"])

    if sort_key == "age_desc":
        filtered.sort(key=_age_oldest_first)
    elif sort_key == "age_asc":
        filtered.sort(
            key=lambda item: (
                (1, 0.0, item["task_key"])
                if item["created_ts"] is None
                else (0, -item["created_ts"], item["task_key"])
            )
        )
    elif sort_key == "owner":
        filtered.sort(key=lambda item: (item["effective_owner"].lower(), *_age_oldest_first(item)))
    elif sort_key == "source":
        filtered.sort(key=lambda item: (item["source"].lower(), *_age_oldest_first(item)))
    elif sort_key == "title":
        filtered.sort(key=lambda item: (item["title"].lower(), item["task_key"]))

    return filtered


def apply_decision(
    conn: Any,
    *,
    task_key: Any,
    decision_input: Any,
    owner_input: Any = None,
    note_input: Any = None,
    reviewer: str,
    audit_label: str = "",
) -> dict[str, Any]:
    """Validate and persist a local review decision or owner reassignment.
    - Strictly idempotent: identical (decision, owner, note) is a no-op with zero DB/audit writes.
    - Writes only to todo_review_decisions and todo_review_audit.
    """
    ensure_schema(conn)
    if not isinstance(task_key, str) or not TASK_KEY_RE.match(task_key.strip()):
        raise ValueError("Invalid task_key")
    clean_key = task_key.strip()

    item_row = conn.execute(
        "SELECT task_key, proposed_owner FROM todo_review_items WHERE task_key = ?",
        (clean_key,),
    ).fetchone()
    if not item_row:
        raise LookupError(f"Unknown task_key: {clean_key}")

    proposed_owner = normalize_owner(item_row["proposed_owner"])
    dec_row = conn.execute(
        "SELECT decision, note, assigned_owner, created_at, updated_at FROM todo_review_decisions WHERE task_key = ?",
        (clean_key,),
    ).fetchone()

    prev_decision: Optional[str] = dec_row["decision"] if dec_row else None
    prev_owner: str = (
        dec_row["assigned_owner"]
        if (dec_row and dec_row["assigned_owner"] in ALLOWED_OWNERS)
        else proposed_owner
    )
    prev_note: str = dec_row["note"] if dec_row else ""

    # Validate decision_input
    if decision_input is None or decision_input == "__keep_current__":
        new_decision = prev_decision
    elif isinstance(decision_input, str):
        d_clean = decision_input.strip().lower()
        if d_clean in ("", "clear", "undecided"):
            new_decision = None
        elif d_clean in DECISION_VALUES:
            new_decision = d_clean
        else:
            raise ValueError(f"Invalid decision: {decision_input!r}")
    else:
        raise ValueError("Invalid decision type")

    # Validate owner_input
    if owner_input is None or (isinstance(owner_input, str) and owner_input.strip() == ""):
        new_owner = (
            proposed_owner
            if (isinstance(decision_input, str) and decision_input.strip().lower() == "clear" and owner_input is None)
            else prev_owner
        )
    else:
        new_owner = validate_owner_strict(owner_input)

    # Validate note_input
    if note_input is None:
        if isinstance(decision_input, str) and decision_input.strip().lower() == "clear":
            new_note = ""
        else:
            new_note = prev_note
    else:
        new_note = clean_text_field(note_input, max_len=MAX_NOTE_LEN)

    clean_audit_label = clean_text_field(audit_label, max_len=MAX_AUDIT_LABEL_LEN)
    clean_reviewer = clean_text_field(reviewer or "local", max_len=200) or "local"

    # Idempotency: exact same effective state -> strict no-op
    if (new_decision, new_owner, new_note) == (prev_decision, prev_owner, prev_note):
        return {
            "changed": False,
            "task_key": clean_key,
            "decision": new_decision,
            "owner": new_owner,
            "note": new_note,
        }

    ts = now_et().isoformat()
    with conn:
        if new_decision is None and new_owner == proposed_owner and new_note == "":
            conn.execute("DELETE FROM todo_review_decisions WHERE task_key = ?", (clean_key,))
        else:
            created_ts = dec_row["created_at"] if dec_row else ts
            conn.execute(
                """
                INSERT INTO todo_review_decisions (
                    task_key, decision, note, assigned_owner, reviewer, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_key) DO UPDATE SET
                    decision = excluded.decision,
                    note = excluded.note,
                    assigned_owner = excluded.assigned_owner,
                    reviewer = excluded.reviewer,
                    updated_at = excluded.updated_at
                """,
                (clean_key, new_decision, new_note, new_owner, clean_reviewer, created_ts, ts),
            )

        conn.execute(
            """
            INSERT INTO todo_review_audit (
                task_key, prev_decision, new_decision, prev_owner, new_owner,
                prev_note, new_note, reviewer, audit_label, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_key,
                prev_decision,
                new_decision,
                prev_owner,
                new_owner,
                prev_note,
                new_note,
                clean_reviewer,
                clean_audit_label,
                ts,
            ),
        )

    return {
        "changed": True,
        "task_key": clean_key,
        "decision": new_decision,
        "owner": new_owner,
        "note": new_note,
        "updated_at": ts,
    }


def todo_safe_url(url: Any) -> str:
    """Render-time href guard: rewrite known thread URLs, then is_safe_review_url or "#"."""
    if not isinstance(url, str):
        return "#"
    rewritten = rewrite_gmail_link_for_display(url.strip())
    return rewritten if is_safe_review_url(rewritten) else "#"


def register(
    app: FastAPI,
    templates: Any,
    common_context: Callable[..., dict[str, Any]],
    verify_csrf: Callable[[Request], None],
    get_db: Callable[[], Any],
) -> None:
    templates.env.filters["todo_safe_url"] = todo_safe_url

    @app.get("/todo-review", response_class=HTMLResponse)
    @app.get("/todo-review/", response_class=HTMLResponse)
    def page_todo_review(
        request: Request,
        source: Optional[str] = None,
        owner: Optional[str] = None,
        decision: Optional[str] = None,
        status: Optional[str] = None,
        sort: str = "age_desc",
        q: Optional[str] = None,
    ) -> Response:
        conn = get_db()
        try:
            manifest, active_keys = ensure_synced(conn)
            all_rows = load_rows_from_db(conn, active_keys=active_keys)
        finally:
            conn.close()

        summary = compute_summary(all_rows)
        distinct_sources = sorted({r["source"] for r in all_rows})
        distinct_statuses = sorted({r["source_status"] for r in all_rows})

        active_sort = sort if sort in _VALID_SORTS else "age_desc"
        filtered_rows = filter_and_sort_rows(
            all_rows,
            source=source or "",
            owner=owner or "",
            decision=decision or "",
            status=status or "",
            q=q or "",
            sort=active_sort,
        )

        exported_dt = parse_iso_et(manifest.get("exported_at"))
        missing_notes = manifest.get("missing_note_ids")
        missing_notes_count = len(missing_notes) if isinstance(missing_notes, list) else 0
        raw_warnings = manifest.get("warnings") if isinstance(manifest.get("warnings"), list) else []
        warnings_list = [str(w) for w in raw_warnings if str(w).strip()]
        export_incomplete = (not bool(manifest.get("complete", True))) or missing_notes_count > 0

        ctx = common_context(request, current_nav="todo-review")
        ctx.update({
            "manifest": manifest,
            "exported_et": format_et(exported_dt),
            "export_incomplete": export_incomplete,
            "missing_notes_count": missing_notes_count,
            "warnings_list": warnings_list,
            "rows": filtered_rows,
            "summary": summary,
            "allowed_owners": ALLOWED_OWNERS,
            "decision_labels": DECISION_LABELS,
            "sort_options": SORT_OPTIONS,
            "distinct_sources": distinct_sources,
            "distinct_statuses": distinct_statuses,
            "filter_source": (source or "").strip(),
            "filter_owner": (owner or "").strip(),
            "filter_decision": (decision or "").strip(),
            "filter_status": (status or "").strip(),
            "filter_sort": active_sort,
            "filter_q": (q or "").strip(),
        })

        if request.headers.get("HX-Request") == "true" and request.headers.get("HX-Target") == "todo-review-list":
            return templates.TemplateResponse(
                request=request,
                name="partials/todo_review_list.html",
                context=ctx,
            )

        return templates.TemplateResponse(
            request=request,
            name="todo_review.html",
            context=ctx,
        )

    @app.post("/todo-review/decide", response_class=HTMLResponse)
    async def post_todo_review_decide(request: Request) -> Response:
        verify_csrf(request)

        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                payload = await request.json()
                if not isinstance(payload, dict):
                    raise ValueError("Expected JSON object")
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid JSON body")
            task_key = payload.get("task_key")
            decision_val = payload.get("decision", "__keep_current__")
            owner_val = payload.get("owner")
            note_val = payload.get("note")
            audit_label = payload.get("audit_label", "")
        else:
            form = await request.form()
            task_key = form.get("task_key")
            decision_val = form.get("decision", "__keep_current__")
            owner_val = form.get("owner")
            note_val = form.get("note")
            audit_label = form.get("audit_label", "")

        reviewer = request.headers.get("Tailscale-User-Login") or "local"

        conn = get_db()
        try:
            _, active_keys = ensure_synced(conn)
            result = apply_decision(
                conn,
                task_key=task_key,
                decision_input=decision_val,
                owner_input=owner_val,
                note_input=note_val,
                reviewer=reviewer,
                audit_label=str(audit_label or ""),
            )
            updated_row = get_single_row(conn, result["task_key"])
            all_rows = load_rows_from_db(conn, active_keys=active_keys)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        finally:
            conn.close()

        if not updated_row:
            raise HTTPException(status_code=404, detail="Task row not found")

        summary = compute_summary(all_rows)
        resp = templates.TemplateResponse(
            request=request,
            name="partials/todo_review_row.html",
            context={
                "request": request,
                "row": updated_row,
                "allowed_owners": ALLOWED_OWNERS,
                "decision_labels": DECISION_LABELS,
                "summary": summary,
                "include_oob_summary": True,
                "just_saved": result["changed"],
                "was_noop": not result["changed"],
            },
        )
        resp.headers["X-Todo-Review-Noop"] = "0" if result["changed"] else "1"
        return resp
