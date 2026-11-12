from __future__ import annotations

import hashlib
import re
from typing import Any, Optional
import os
from urllib.parse import parse_qs, urlparse, urlsplit

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}



_SAFE_SCHEMES = ("http", "https", "mailto")

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_HEX16_RE = re.compile(r"^[0-9a-fA-F]{16}$")
_DODOSEND_SCHEME_RE = re.compile(
    r"^dodosend://thread/([0-9a-fA-F]{16})(?:\?account=([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}))?$"
)
_CANONICAL_DODOSEND_RE = re.compile(
    r"^https://dodosend\.com/t/([0-9a-fA-F]{16})(?:\?account=([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}))?$"
)
_GMAIL_THREAD_FRAG_RE = re.compile(
    r"^(all|inbox|sent|drafts|trash|spam|starred|imp|snoozed|scheduled|category/[^/]+|label/[^/]+)/([0-9a-fA-F]{16})$"
)
_DODOSEND_MD_RE = re.compile(
    r"dodosend://thread/[0-9a-fA-F]{16}(?:\?account=[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})?"
)


def _default_gmail_account() -> str | None:
    """Account for canonical Dodosend links; from BRIEFS_TODO_REVIEW_ACCOUNT (host TOML/env)."""
    acct = os.environ.get("BRIEFS_TODO_REVIEW_ACCOUNT", "user@example.com").strip()
    if acct and _EMAIL_RE.match(acct):
        return acct
    return None


def _account_from_query(query: str) -> str | None:
    vals = (parse_qs(query, keep_blank_values=True).get("account") or [])
    if vals and _EMAIL_RE.match(vals[0]):
        return vals[0]
    return None


def parse_gmail_thread_ref(url: Any) -> tuple[str, str | None] | None:
    """Extract (thread_id, account_or_None) from accepted Gmail/Dodosend URL forms.

    Inputs: https://dodosend.com/t/<16hex>?account=..., legacy
    https://dodo-link.pages.dev/t/<16hex>, dodosend://thread/<16hex>, and
    mail.google.com thread URLs. Search URLs are not thread refs.
    """
    if not isinstance(url, str):
        return None
    u = url.strip()
    if not u:
        return None
    m = _DODOSEND_SCHEME_RE.match(u)
    if m:
        return m.group(1).lower(), m.group(2)
    parsed = urlsplit(u)
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() == "https" and host in {"dodosend.com", "dodo-link.pages.dev"}:
        pm = re.fullmatch(r"/t/([0-9a-fA-F]{16})", parsed.path)
        if pm:
            return pm.group(1).lower(), _account_from_query(parsed.query)
        return None
    if "mail.google.com" in host:
        gm = _GMAIL_THREAD_FRAG_RE.match(parsed.fragment)
        if gm:
            return gm.group(2).lower(), None
        tf = re.search(r"thread-f:(\d+)", u)
        if tf:
            return format(int(tf.group(1)), "x"), None
    return None


def dodosend_thread_url(tid: str, account: str | None = None) -> str:
    """Canonical thread link: https://dodosend.com/t/<16hex> with optional ?account=<email>."""
    t = tid.strip().lower()
    if not _HEX16_RE.match(t):
        raise ValueError(f"invalid Gmail thread id: {tid!r}")
    acct = account if (account and _EMAIL_RE.match(account)) else _default_gmail_account()
    if acct:
        return f"https://dodosend.com/t/{t}?account={acct}"
    return f"https://dodosend.com/t/{t}"


def is_canonical_dodosend_url(url: Any) -> bool:
    """True only for https://dodosend.com/t/<exactly 16 hex> with optional account=email."""
    if not isinstance(url, str):
        return False
    return bool(_CANONICAL_DODOSEND_RE.match(url.strip()))


def rewrite_gmail_link_for_display(url: Any) -> str:
    """Rewrite a known thread URL to the canonical Dodosend https form; otherwise return as-is."""
    if not isinstance(url, str):
        return url
    u = url.strip()
    ref = parse_gmail_thread_ref(u)
    if not ref:
        return u
    tid, acct = ref
    return dodosend_thread_url(tid, acct)


def is_safe_url(url: Any) -> bool:
    """True for http(s)/mailto URLs and same-site relative paths; False for javascript:, data:, etc. (M-5)."""
    if not isinstance(url, str):
        return False
    u = url.strip()
    if not u:
        return False
    # Browsers ignore ASCII control chars/whitespace inside the scheme ("java\tscript:").
    compact = re.sub(r"[\x00-\x20\x7f]", "", u).lower()
    m = re.match(r"^([a-z][a-z0-9+.\-]*):", compact)
    if m:
        return m.group(1) in _SAFE_SCHEMES
    if compact.startswith("//") or compact.startswith("\\") or compact.startswith("/\\"):
        return False  # protocol-relative: treat as off-site, not allowed
    return True  # scheme-less = relative to the dashboard (e.g. "/i/3", or a bare label stored as url)


def safe_url(url: Any, fallback: str = "#") -> str:
    if not isinstance(url, str):
        return fallback
    rewritten = rewrite_gmail_link_for_display(url.strip())
    return rewritten if is_safe_url(rewritten) else fallback


def filter_safe_links(links: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for l in links or []:
        if not isinstance(l, dict) or not isinstance(l.get("url"), str):
            continue
        rewritten = rewrite_gmail_link_for_display(l["url"].strip())
        if is_safe_url(rewritten):
            item = dict(l)
            item["url"] = rewritten
            out.append(item)
    return out


def extract_links(text: str) -> list[dict[str, str]]:
    """Extract markdown [label](url) links and bare http(s) URLs."""
    links: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    # Markdown links: [label](url) — http(s) and dodosend://thread/...
    md_matches = re.findall(r"\[([^\]]+)\]\((https?://[^\)]+|dodosend://thread/[0-9a-fA-F]{16}(?:\?account=[^\)\s]+)?)\)", text)
    for label, url in md_matches:
        u = url.strip()
        if u not in seen_urls:
            links.append({"label": label.strip(), "url": u})
            seen_urls.add(u)

    # Bare URLs
    # Strip markdown link targets to avoid double-counting
    stripped = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+|dodosend://thread/[0-9a-fA-F]{16}(?:\?account=[^\)\s]+)?)\)", "", text)
    bare_matches = re.findall(r"https?://[^\s\)\"',;]+", stripped)
    for u in bare_matches:
        u_clean = u.rstrip(".,;:!?'\")")
        if u_clean and u_clean not in seen_urls:
            links.append({"label": u_clean, "url": u_clean})
            seen_urls.add(u_clean)
    for u in _DODOSEND_MD_RE.findall(stripped):
        if u not in seen_urls:
            links.append({"label": u, "url": u})
            seen_urls.add(u)

    return links


def normalize_url(url: str) -> str:
    """Normalize a URL to produce a canonical representation for item keys."""
    ref = parse_gmail_thread_ref(url)
    if ref:
        return f"gmail:{ref[0]}"

    parsed = urlparse(url)

    # mail.google.com -> extract 16-hex thread id if present (legacy / odd shapes)
    if "mail.google.com" in parsed.netloc:
        m = re.search(r"([0-9a-fA-F]{16})", url)
        if m:
            return f"gmail:{m.group(1).lower()}"

    # gerrit review urls -> keep change number (e.g. /#/c/17048/ -> /c/17048)
    if "gerrit" in parsed.netloc:
        m = re.search(r"/c/(\d+)", url)
        if m:
            return f"https://{parsed.netloc}/c/{m.group(1)}"

    # Standard URL: strip query, fragment, and trailing slash
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    return clean


def extract_flags(line: str) -> set[str]:
    """Extract NEW, updated, or resolved flags from an item line."""
    flags: set[str] = set()
    lower = line.lower()

    # NEW:
    # \bNEW\b (case-sensitive or whole word uppercase) or (new) or new: at start
    if re.search(r"\bNEW\b", line) or "(new)" in lower or re.match(r"^\s*new\s*:", lower):
        flags.add("NEW")

    # updated:
    if "(updated)" in lower or re.match(r"^\s*updated\s*:", lower):
        flags.add("updated")

    # resolved:
    # (resolved) or resolved / ✅ / done at line start or (done)
    if (
        "(resolved)" in lower
        or "(done)" in lower
        or "✅" in line
        or re.match(r"^\s*(?:resolved|done)\b", lower)
    ):
        flags.add("resolved")

    return flags


def clean_title(text: str) -> str:
    """Extract a clean, plain text title from an item's first sentence or clause (<=160 chars)."""
    # Replace markdown links [label](url) with label
    t = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    # Remove markdown styling
    t = t.replace("**", "").replace("*", "").replace("`", "")
    # Remove flag tags like (new), (updated), (resolved), NEW:
    t = re.sub(r"(?i)\((?:new|updated|resolved|done)\)", "", t)
    t = re.sub(r"(?i)^\s*(?:new|updated|resolved|done)\s*:\s*", "", t)
    t = re.sub(r"^\s*✅\s*", "", t)
    # Strip leading display numbers or bullets if present
    t = re.sub(r"^\s*(?:[-*]\s+)?(?:\d+(?:\.\d+[a-z]?)?\.?)\s+", "", t)
    # Collapse whitespace
    t = " ".join(t.split())

    # Get first sentence or clause
    # Split on period, question mark, or exclamation mark followed by space or end
    m = re.search(r"^(.*?)(?:[\.!\?](?:\s|$)|$)", t)
    first_sent = m.group(1).strip() if m else t
    if not first_sent:
        first_sent = t

    if len(first_sent) > 160:
        first_sent = first_sent[:157] + "..."
    return first_sent


def compute_item_key(item_text: str, title: str) -> str:
    """
    Generate stable item_key:
    1) Preferred source URL or first non-sheet URL
    2) Gmail thread id in text
    3) Else 'h:' + sha1(normalized title)[:12]
    """
    links = extract_links(item_text)
    source_links = [l["url"] for l in links if "docs.google.com/spreadsheets" not in l["url"]]

    # 1) Any recognized Gmail/Dodosend thread URL -> gmail:<id>
    for u in source_links:
        ref = parse_gmail_thread_ref(u)
        if ref:
            return f"gmail:{ref[0]}"

    # 1b) Preferred hosts
    preferred_hosts = [
        "mail.google.com", "dodosend.com", "dodo-link.pages.dev",
        "github.com", "forgejo", "atlassian.net", "gerrit",
    ]
    chosen_url = None
    for host in preferred_hosts:
        for u in source_links:
            if host in u:
                chosen_url = u
                break
        if chosen_url:
            break

    if not chosen_url and source_links:
        chosen_url = source_links[0]

    if chosen_url:
        return normalize_url(chosen_url)

    # 2) Gmail thread id in text
    gm = re.search(r"\b(?:thread|threadId)[ \t:=#]+([0-9a-fA-F]{16})\b", item_text, re.I)
    if gm:
        return f"gmail:{gm.group(1).lower()}"

    # 3) Hash of normalized title
    t = title.lower()
    # Strip flags words: new, updated, resolved, still, now
    t = re.sub(r"\b(new|updated|resolved|still|now|done)\b", " ", t)
    # Strip dates / times / numbers
    t = re.sub(r"\b\d+([:.]\d+)?\s*(am|pm|et)?\b", " ", t, flags=re.I)
    t = re.sub(
        r"\b(mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        " ",
        t,
    )
    t = re.sub(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)\b",
        " ",
        t,
    )
    t = re.sub(r"\b\d+\b", " ", t)
    # Strip punctuation
    t = re.sub(r"[^\w\s]", " ", t)
    # Collapse spaces and take first 12 words
    words = [w for w in t.split() if w][:12]
    norm_str = " ".join(words)
    if not norm_str:
        # Title was only dates/numbers/flag words (e.g. a "Sunday Oct 25" section header).
        # Don't let all of these collapse onto sha1(""); key on the raw title + text instead.
        raw = " ".join(f"{title} {item_text}".lower().split())
        norm_str = "raw:" + raw[:200]
    h = hashlib.sha1(norm_str.encode("utf-8")).hexdigest()[:12]
    return f"h:{h}"


def parse_date_hint(
    title: str,
    sheet_name: str = "",
    body: str = "",
    fallback: Optional[str] = None,
) -> Optional[str]:
    """Detect report date (YYYY-MM-DD) from sheet name, title, or body."""
    # 1. From sheet name (e.g. 'Radar weekly 2026-10-23')
    if sheet_name:
        m = re.search(r"\b(20\d\d-\d\d-\d\d)\b", sheet_name)
        if m:
            return m.group(1)

    # 2. Explicit YYYY-MM-DD in title or first lines of body
    for t in [title, body[:500]]:
        m = re.search(r"\b(20\d\d-\d\d-\d\d)\b", t)
        if m:
            return m.group(1)

    # 3. Month + Day in title (e.g. 'Fri Oct 23')
    m = re.search(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b",
        title,
        re.I,
    )
    if m:
        mon_str = m.group(1).lower()[:3]
        month = MONTHS.get(mon_str, 10)
        day = int(m.group(2))
        year = 2026  # default year
        return f"{year:04d}-{month:02d}-{day:02d}"

    return fallback


def parse_kind_hint(title: str, sheet_name: str = "") -> str:
    """Detect report kind ('weekly' or 'daily')."""
    text = f"{title} {sheet_name}".lower()
    if "weekly" in text or "review" in text and "weekly" in text:
        return "weekly"
    return "daily"


def parse_sheet_info(md: str) -> tuple[Optional[str], Optional[str]]:
    """Extract reply sheet URL and sheet label/name from markdown."""
    # Markdown link to docs.google.com/spreadsheets
    m = re.search(
        r"\[([^\]]+)\]\((https://docs\.google\.com/spreadsheets[^\)]+)\)",
        md,
    )
    if m:
        return m.group(2).strip(), m.group(1).strip()

    # Bare spreadsheet URL
    m_bare = re.search(r"(https://docs\.google\.com/spreadsheets[^\s\)\"',;]+)", md)
    if m_bare:
        return m_bare.group(1).strip(), None

    # Format like "Sheet: Radar replies 2026-10-22 1DfO6oO-..."
    m_id = re.search(
        r"Sheet:\s*([^\n\r]+?)\s+([a-zA-Z0-9_-]{30,})",
        md,
    )
    if m_id:
        label = m_id.group(1).strip()
        sheet_id = m_id.group(2).strip()
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit", label

    return None, None


def parse_brief(md: str, default_date: Optional[str] = None) -> dict[str, Any]:
    """
    Parse a markdown brief into structured report metadata and items.
    Returns:
    {
        title: str,
        sheet_url: str | None,
        sheet_name: str | None,
        date_hint: str | None,
        kind_hint: str,
        items: list[dict]
    }
    """
    lines = md.splitlines()

    # Title detection: first bold line, or heading (#), or first non-empty line
    title = ""
    title_explicit = False
    for line in lines:
        s = line.strip()
        if not s:
            continue
        # Bold line alone or starting line e.g. **Radar weekly review...**
        bold_m = re.match(r"^\*\*([^*]+)\*\*", s)
        if bold_m:
            title = s.replace("**", "").strip()
            title_explicit = True
            break
        if s.startswith("#"):
            title = s.lstrip("#").strip()
            title_explicit = True
            break
        if not title:
            title = s
            # A plain first line still counts as a title when it names the brief.
            if re.search(r"\b(daily|weekly|review|roundup|brief|update)\b", s[:80], re.I) and re.search(
                r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b",
                s[:80],
                re.I,
            ):
                title_explicit = True
            break

    sheet_url, sheet_name = parse_sheet_info(md)
    date_hint = parse_date_hint(title if title_explicit else "", sheet_name or "", "", fallback=default_date)
    kind_hint = parse_kind_hint(title, sheet_name or "")

    # Item parsing
    items: list[dict[str, Any]] = []
    current_section: Optional[str] = None
    current_item: Optional[dict[str, Any]] = None
    pos = 0

    for line in lines:
        stripped = line.strip()

        # Section heading: bold alone on a line, e.g. **Needs you (ranked)**
        sec_m = re.match(r"^\*\*([^*]+)\*\*\s*$", stripped)
        if sec_m:
            current_section = sec_m.group(1).strip()
            current_item = None
            continue

        # Sub-item line: e.g. "   - 1.1 text" or "   1.1. text" or "   1.2a text"
        sub_m = re.match(r"^\s{2,}(?:[-*]\s+)?(\d+\.\d+[a-z]?)\.?\s+(.*)", line)
        if sub_m:
            disp_id = sub_m.group(1)
            rest = sub_m.group(2)
            # Parent display ID is the part before the dot
            parent_disp_id = disp_id.rsplit(".", 1)[0]
            pos += 1
            current_item = {
                "display_id": disp_id,
                "parent_display_id": parent_disp_id,
                "position": pos,
                "section": current_section,
                "lines": [rest],
                "flags": extract_flags(rest),
            }
            items.append(current_item)
            continue

        # Top-level item line: e.g. "1. text" or " 1. text"
        top_m = re.match(r"^\s{0,1}(\d+)\.\s+(.*)", line)
        if top_m:
            disp_id = top_m.group(1)
            rest = top_m.group(2)
            pos += 1
            current_item = {
                "display_id": disp_id,
                "parent_display_id": None,
                "position": pos,
                "section": current_section,
                "lines": [rest],
                "flags": extract_flags(rest),
            }
            items.append(current_item)
            continue

        # Blank line does not end an item, but don't append to lines
        if not stripped:
            continue

        # Non-indented heading or paragraph ends the current item
        if line.startswith("#") or (not line.startswith(" ") and not line.startswith("\t")):
            current_item = None
            continue

        # Indented continuation line
        if current_item is not None:
            current_item["lines"].append(stripped)
            # Also check flags on continuation lines
            current_item["flags"].update(extract_flags(stripped))

    if not items:
        for line in lines:
            m_inl = re.match(r"^\s*(?:Open needing operator|Needs operator|Needs you)[^:]{0,40}:\s*(.+)$", line, re.I)
            if not m_inl:
                continue
            for part in re.split(r";\s*(?=\d+\s)", m_inl.group(1)):
                m_p = re.match(r"^(\d+)\s+(.*)", part.strip())
                if not m_p:
                    continue
                pos += 1
                items.append({
                    "display_id": m_p.group(1),
                    "parent_display_id": None,
                    "position": pos,
                    "section": "Needs you",
                    "lines": [m_p.group(2).strip().rstrip(".")],
                    "flags": extract_flags(m_p.group(2)),
                })
            break

    # Finalize items
    final_items: list[dict[str, Any]] = []
    for it in items:
        text_md = "\n".join(it["lines"]).strip()
        first_line = it["lines"][0] if it["lines"] else ""
        it_title = clean_title(first_line)
        links = extract_links(text_md)
        flags = it["flags"]
        item_key = compute_item_key(text_md, it_title)

        final_items.append({
            "display_id": it["display_id"],
            "parent_display_id": it["parent_display_id"],
            "position": it["position"],
            "section": it["section"],
            "text_md": text_md,
            "title": it_title,
            "links": links,
            "flags": flags,
            "item_key": item_key,
        })

    return {
        "title": title,
        "title_explicit": title_explicit,
        "sheet_url": sheet_url,
        "sheet_name": sheet_name,
        "date_hint": date_hint,
        "kind_hint": kind_hint,
        "items": final_items,
    }


def parse_cos_reqs(reqs_list: list[dict[str, Any]], default_date: str) -> dict[str, Any]:
    """
    Parse Chief of Staff batchUpdate requests list into a structured report.
    Returns:
    {
        title: "Chief of Staff",
        date: str,
        kind: "cos",
        sheet_url: str | None,
        items: list[dict],
        body_md: str
    }
    """
    rows: list[Any] = []
    for req in reqs_list:
        if "updateCells" in req:
            rows = req["updateCells"].get("rows", [])
            break

    items: list[dict[str, Any]] = []
    md_lines: list[str] = ["# Chief of Staff\n"]
    pos = 0

    for idx, row in enumerate(rows):
        if idx == 0:
            continue  # Skip header row

        vals = row.get("values", [])
        col_vals: list[str] = []
        for c in vals:
            u = c.get("userEnteredValue", {})
            if "stringValue" in u:
                col_vals.append(u["stringValue"])
            elif "formulaValue" in u:
                col_vals.append(u["formulaValue"])
            elif "numberValue" in u:
                col_vals.append(str(u["numberValue"]))
            else:
                col_vals.append("")

        while len(col_vals) < 5:
            col_vals.append("")

        disp_id = col_vals[0].strip()
        item_title = col_vals[1].strip()
        status_details = col_vals[2].strip()
        reply_cell = col_vals[3].strip()
        links_cell = col_vals[4].strip()

        if not disp_id and not item_title:
            continue

        parent_disp_id = disp_id.rsplit(".", 1)[0] if "." in disp_id else None

        # Extract links from links_cell
        links: list[dict[str, str]] = []
        # parse =HYPERLINK("url","label")
        for m in re.finditer(r'=HYPERLINK\("([^"]+)",\s*"([^"]+)"\)', links_cell):
            if is_safe_url(m.group(1)):
                links.append({"url": m.group(1).strip(), "label": m.group(2).strip()})
        # parse plain URLs
        for m in re.finditer(r"https?://[^\s\)\"',;]+", links_cell):
            u = m.group(0).rstrip(".,;:!?'\")")
            if not any(l["url"] == u for l in links):
                links.append({"url": u, "label": u})

        # Also extract links from status/details
        links.extend([l for l in extract_links(status_details) if not any(x["url"] == l["url"] for x in links)])

        # Construct markdown representation
        body_parts = []
        if status_details:
            body_parts.append(status_details)
        if links:
            links_md = " ".join([f"[{l['label']}]({l['url']})" for l in links])
            body_parts.append(links_md)
        text_md = "\n".join(body_parts)

        # Append to synthesized markdown
        indent = "   - " if parent_disp_id else ""
        if parent_disp_id:
            md_lines.append(f"{indent}{disp_id} **{item_title}**: {text_md}")
        else:
            md_lines.append(f"\n{disp_id}. **{item_title}**\n   {text_md}")

        pos += 1
        flags = extract_flags(f"{item_title} {status_details}")
        item_key = compute_item_key(text_md, clean_title(item_title))

        items.append({
            "display_id": disp_id,
            "parent_display_id": parent_disp_id,
            "position": pos,
            "section": None,
            "text_md": text_md,
            "title": clean_title(item_title),
            "links": links,
            "flags": flags,
            "item_key": item_key,
        })

    return {
        "title": "Chief of Staff",
        "date": default_date,
        "kind": "cos",
        "sheet_url": None,
        "items": items,
        "body_md": "\n".join(md_lines).strip(),
    }
