from __future__ import annotations

from markupsafe import Markup
import html
import json

import logging
import os
import re
import secrets
import sys
from collections import OrderedDict
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from markdown_it import MarkdownIt

from briefs.actions import (
    ack,
    create_action,
    in_progress,
    inbox,
    list_actions,
    list_actions_for_item,
    mark,
    queue_text,
    queued_count,
    queued_item_texts,
    submit_all,
    submit_text,
)
from briefs.config import (
    ALLOWED_HOSTS,
    ALLOWED_LOGINS,
    BOTS,
    BRIEFS_DB,
    PACKAGE_ROOT,
    TZ_ET,
    bot_cli_name,
    find_bot,
    logs_dir,
    now_et,
    today_et,
)
from briefs import syncapi
from briefs.db import connect, init_schema
from briefs.parse import rewrite_gmail_link_for_display, safe_url
from briefs.store import (
    distinct_areas,
    get_item,
    get_report,
    item_timeline,
    items_open,
    items_snoozed,
    report_history,
    search,
    today_view,
)
from briefs.wake import (
    dispatch_configured,
    trigger_dispatch,
)

# Set only on the VPS copy: enables the /sync/* replication API and the stale-mirror banner.
SYNC_TOKEN_FILE = os.environ.get("BRIEFS_SYNC_TOKEN_FILE", "")

# Logger setup
logger = logging.getLogger("briefs.web")
logger.setLevel(logging.INFO)

ACCESS_LOG_FILE = logs_dir() / "access.log"

# Markdown renderer with table support and target="_blank" on links
md_engine = MarkdownIt("commonmark", {"html": False}).enable("table")


def _render_link_open(self: Any, tokens: Any, idx: int, options: Any, env: Any) -> str:
    tokens[idx].attrs["target"] = "_blank"
    tokens[idx].attrs["rel"] = "noopener"
    href = tokens[idx].attrGet("href")
    if href:
        tokens[idx].attrs["href"] = rewrite_gmail_link_for_display(href)
    return self.renderToken(tokens, idx, options, env)


md_engine.add_render_rule("link_open", _render_link_open)


def render_markdown(text: str) -> str:
    """Render markdown safely: raw HTML escaped, tables supported, links open in new tab."""
    if not text:
        return ""
    # Auto-link bare URLs not inside existing markdown links [label](url)
    parts = re.split(r"(\[[^\]]+\]\([^\)]+\))", text)
    new_parts = []
    for p in parts:
        if p.startswith("[") and "](" in p:
            new_parts.append(p)
        else:
            def _repl_bare(m: re.Match[str]) -> str:
                raw_u = m.group(1)
                clean_u = raw_u.rstrip(".,;:!?'\")")
                suffix = raw_u[len(clean_u):]
                return f"[{clean_u}]({clean_u}){suffix}"

            new_parts.append(re.sub(r"(https?://[^\s\)<>\"']+)", _repl_bare, p))

    combined = "".join(new_parts)
    return md_engine.render(combined)


def create_app(db_path: Optional[str] = None) -> FastAPI:
    active_db = db_path or BRIEFS_DB
    # Ensure database schema is initialized unless importing in pytest with default db
    if not (db_path is None and "PYTEST_CURRENT_TEST" in os.environ):
        conn_init = connect(active_db)
        init_schema(conn_init)
        conn_init.close()

    app = FastAPI(title="Bot Briefs", docs_url=None, redoc_url=None)

    static_dir = PACKAGE_ROOT / "static"
    templates_dir = PACKAGE_ROOT / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))
    templates.env.filters["md"] = lambda t: Markup(render_markdown(t or ""))
    templates.env.filters["safe_url"] = safe_url

    def get_db() -> Any:
        return connect(active_db)

    # L-4: security headers on every response; a per-request nonce for the few inline <script> blocks.
    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next: Any) -> Response:
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    # Auth & access logging middleware
    @app.middleware("http")
    async def auth_and_access_middleware(request: Request, call_next: Any) -> Response:
        path = request.url.path

        # Unauthenticated health check endpoint
        if path == "/healthz":
            return PlainTextResponse("ok")

        # VPS copy only (BRIEFS_SYNC_TOKEN_FILE set): mirror-lag probe, and /sync/* which does its own bearer-token
        # check (the box is a tagged node, so it has no Tailscale user identity). Nothing here exists on the box copy.
        if SYNC_TOKEN_FILE and path == "/healthz/mirror":
            conn = get_db()
            try:
                return JSONResponse(syncapi.mirror_state(conn))
            finally:
                conn.close()
        if SYNC_TOKEN_FILE and path.startswith("/sync/"):
            return await call_next(request)

        client_host = request.client.host if request.client else "unknown"
        login = request.headers.get("Tailscale-User-Login")
        log_line = f"{now_et().isoformat()} login={login or '-'} path={path} host={client_host}\n"

        # Log to file and python logger
        try:
            with open(ACCESS_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception:
            pass
        logger.info(f"login={login or '-'} path={path} host={client_host}")

        # Check authorization
        if login:
            if login not in ALLOWED_LOGINS:
                return HTMLResponse(f"Not allowed for {html.escape(login)}", status_code=403)
        else:
            # Check for forwarding headers or tailscale headers
            headers_lower = {k.lower(): v for k, v in request.headers.items()}
            is_proxied = (
                "x-forwarded-for" in headers_lower
                or "x-forwarded-host" in headers_lower
                or any(k.startswith("tailscale-") for k in headers_lower)
            )

            is_local = client_host in ("127.0.0.1", "::1", "localhost", "testclient")

            if is_proxied:
                # Proxied without login is forbidden
                return HTMLResponse("Forbidden: proxied request without Tailscale identity", status_code=403)
            elif not is_local:
                return HTMLResponse("Forbidden: non-local client", status_code=403)

        return await call_next(request)

    # Mount static files
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    def mirror_context() -> dict[str, Any]:
        """VPS copy: lag of the box mirror for the stale-data banner (empty dict on the box)."""
        if not SYNC_TOKEN_FILE:
            return {}
        conn = get_db()
        try:
            ms = syncapi.mirror_state(conn)
        finally:
            conn.close()
        last = ms.get("last_push_at")
        mins = None if ms.get("lag_seconds") is None else ms["lag_seconds"] // 60
        return {"mirror_stale": ms["stale"], "mirror_last": last, "mirror_minutes": mins}

    def common_context(request: Request, current_nav: str = "", current_bot: str = "") -> dict[str, Any]:
        return {
            **mirror_context(),
            "request": request,
            "all_bots": BOTS,
            "current_nav": current_nav,
            "current_bot": current_bot,
            "user_name": request.headers.get("Tailscale-User-Name"),
        }

    def _verify_csrf(req: Request) -> None:
        # M-4: human UI writes need a real allowed Tailscale identity (set by Tailscale Serve). Local processes
        # without the header (bots, scripts) use the brief CLI or /api/* instead.
        ui_login = req.headers.get("Tailscale-User-Login")
        if not ui_login or ui_login not in ALLOWED_LOGINS:
            raise HTTPException(status_code=403, detail="Forbidden: UI changes need a Tailscale login")
        hx_req = req.headers.get("HX-Request")
        if not hx_req or hx_req.lower() != "true":
            raise HTTPException(status_code=403, detail="Forbidden: missing or invalid HX-Request header")

        origin_header = req.headers.get("Origin") or req.headers.get("Referer")
        if not origin_header:
            raise HTTPException(status_code=403, detail="Forbidden: missing Origin/Referer header")

        parsed = urllib.parse.urlsplit(origin_header)
        origin_scheme = parsed.scheme.lower()
        origin_host = parsed.netloc.lower()

        if origin_scheme not in ("http", "https"):
            raise HTTPException(status_code=403, detail="Forbidden: invalid origin scheme")

        expected_host = (
            req.headers.get("X-Forwarded-Host")
            or req.headers.get("Host")
            or req.url.netloc
        ).lower()

        if origin_host != expected_host:
            raise HTTPException(status_code=403, detail="Forbidden: cross-origin request")

    def _verify_local_only(req: Request) -> None:
        headers_lower = {k.lower(): v for k, v in req.headers.items()}
        if any(k.startswith("tailscale-") or k.startswith("x-forwarded-") for k in headers_lower):
            raise HTTPException(status_code=403, detail="Forbidden: proxied request to local-only endpoint")
        client_host = req.client.host if req.client else "unknown"
        if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            raise HTTPException(status_code=403, detail="Forbidden: non-local client to local-only endpoint")

    if SYNC_TOKEN_FILE:
        syncapi.register(app, get_db, SYNC_TOKEN_FILE)

    from briefs import cleanup as _cleanup
    _cleanup.register(app, templates, common_context, _verify_csrf, get_db)

    from briefs import todo_review as _todo_review
    _todo_review.register(app, templates, common_context, _verify_csrf, get_db)

    def _render_item_row(conn: Any, item_id: int, request: Request) -> Response:
        it = conn.execute(
            """
            SELECT
                i.*,
                b.slug AS bot_slug,
                b.name AS bot_name,
                ri.display_id,
                ri.parent_display_id,
                ri.flags,
                ri.body_md
            FROM items i
            JOIN bots b ON b.id = i.bot_id
            LEFT JOIN report_items ri ON ri.id = (
                SELECT ri2.id
                FROM report_items ri2
                JOIN reports r2 ON r2.id = ri2.report_id
                WHERE ri2.item_id = i.id
                ORDER BY r2.report_date DESC, ri2.position ASC
                LIMIT 1
            )
            WHERE i.id = ?
            """,
            (item_id,),
        ).fetchone()
        if not it:
            raise HTTPException(status_code=404, detail="Item not found")

        d = dict(it)
        try:
            d["meta"] = json.loads(d.get("meta_json") or "{}")
        except Exception:
            d["meta"] = {}
        try:
            d["links"] = json.loads(d.get("links_json") or "[]")
        except Exception:
            d["links"] = []

        flags_list = [f.strip() for f in (d.get("flags") or "").split(",") if f.strip()]
        d["flags_list"] = flags_list

        q_texts = queued_item_texts(conn)
        d["queued_text"] = q_texts.get(item_id)

        return templates.TemplateResponse(
            request=request,
            name="partials/item_row.html",
            context={"request": request, "item": d, "today": today_et()},
        )

    # Routes
    @app.get("/", response_class=HTMLResponse)
    def page_today(
        request: Request,
        bot: Optional[str] = None,
        area: Optional[str] = None,
        status: str = "open",
        new: Optional[str] = None,
        due: Optional[str] = None,
    ) -> Any:
        conn = get_db()
        filter_new = (new or "").lower() in ("true", "1", "on")
        filter_due = (due or "").lower() in ("true", "1", "on")

        tv = today_view(
            conn,
            filter_bot=bot,
            filter_area=area,
            filter_new=filter_new,
            filter_due=filter_due,
            filter_status=status,
        )
        in_prog = in_progress(conn)
        q_count = queued_count(conn)
        areas = distinct_areas(conn)
        conn.close()

        dispatch_conf = dispatch_configured()
        fb = find_bot(bot) if bot else None

        current_query = str(request.url.query)
        current_query_path = f"/?{current_query}" if current_query else "/"

        ctx = common_context(request, current_nav="today")
        ctx.update({
            "cos": tv["cos"],
            "bot_cards": tv["bot_cards"],
            "in_progress": in_prog,
            "queued_count": q_count,
            "distinct_areas": areas,
            "dispatch_configured": dispatch_conf,
            "filter_bot": (fb.cli_name if fb else (bot or "")),
            "filter_bot_name": (fb.name if fb else ""),
            "filter_area": area or "",
            "filter_status": status,
            "filter_new": filter_new,
            "filter_due": filter_due,
            "current_query_path": current_query_path,
        })

        if request.headers.get("HX-Request") == "true" and request.headers.get("HX-Target") == "item-list":
            return templates.TemplateResponse(request=request, name="partials/today_cards.html", context=ctx)

        return templates.TemplateResponse(request=request, name="today.html", context=ctx)

    @app.get("/submit-bar", response_class=HTMLResponse)
    def submit_bar_get(request: Request) -> Any:
        conn = get_db()
        try:
            q_count = queued_count(conn)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request=request,
            name="partials/submit_bar.html",
            context={"request": request, "queued_count": q_count},
        )

    @app.get("/in-progress", response_class=HTMLResponse)
    def in_progress_get(request: Request, ids: Optional[str] = None) -> Any:
        conn = get_db()
        try:
            in_prog = in_progress(conn)
        finally:
            conn.close()

        curr_ids = [str(x["item"]["id"]) for x in in_prog]
        headers: dict[str, str] = {}
        if ids is not None:
            client_ids = [s.strip() for s in ids.split(",") if s.strip()]
            if sorted(client_ids) != sorted(curr_ids):
                headers["HX-Trigger"] = "listChanged"

        return templates.TemplateResponse(
            request=request,
            name="partials/in_progress.html",
            context={"request": request, "in_progress": in_prog},
            headers=headers,
        )

    @app.post("/i/{item_id}/send")
    async def item_send_post(
        request: Request,
        item_id: int,
        background_tasks: BackgroundTasks,
    ) -> Response:
        _verify_csrf(request)
        form = await request.form()
        text_val = str(form.get("text", "")).strip()
        if not text_val:
            raise HTTPException(status_code=400, detail="Text cannot be empty")

        author = request.headers.get("Tailscale-User-Login") or "local"
        conn = get_db()
        try:
            act = submit_text(conn, item_id, text_val, author)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        finally:
            conn.close()

        background_tasks.add_task(trigger_dispatch, active_db, [act["id"]])

        headers = {
            "HX-Trigger": json.dumps({"inprogressChanged": True, "draftClear": {"item": item_id}})
        }
        return HTMLResponse("", status_code=200, headers=headers)

    @app.post("/i/{item_id}/queue")
    async def item_queue_post(
        request: Request,
        item_id: int,
    ) -> Response:
        _verify_csrf(request)
        form = await request.form()
        text_val = str(form.get("text", "")).strip()
        if not text_val:
            raise HTTPException(status_code=400, detail="Text cannot be empty")

        author = request.headers.get("Tailscale-User-Login") or "local"
        conn = get_db()
        try:
            act = queue_text(conn, item_id, text_val, author)
            resp = _render_item_row(conn, item_id, request)
            resp.headers["HX-Trigger"] = json.dumps({"queueChanged": True, "draftClear": {"item": item_id}})
            return resp
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        finally:
            conn.close()

    @app.post("/submit-all")
    async def submit_all_post(
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> Response:
        _verify_csrf(request)
        drafts_data: list[dict[str, Any]] = []

        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body = await request.json()
                if isinstance(body, list):
                    drafts_data = body
                elif isinstance(body, dict):
                    drafts_data = body.get("drafts", [])
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid JSON body")
        else:
            form = await request.form()
            drafts_raw = form.get("drafts")
            if drafts_raw:
                try:
                    parsed = json.loads(str(drafts_raw))
                    if isinstance(parsed, list):
                        drafts_data = parsed
                    elif isinstance(parsed, dict):
                        drafts_data = parsed.get("drafts", [])
                except Exception:
                    raise HTTPException(status_code=400, detail="Invalid drafts JSON")

        author = request.headers.get("Tailscale-User-Login") or "local"
        conn = get_db()
        try:
            submitted_acts = submit_all(conn, drafts_data, author)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        finally:
            conn.close()

        if not submitted_acts:
            raise HTTPException(status_code=400, detail="Nothing to submit")

        action_ids = [a["id"] for a in submitted_acts]
        background_tasks.add_task(trigger_dispatch, active_db, action_ids)

        item_ids = [a["item_id"] for a in submitted_acts if a.get("item_id")]
        headers = {
            "HX-Trigger": json.dumps({
                "inprogressChanged": True,
                "listChanged": True,
                "queueChanged": True,
                "draftsSubmitted": item_ids,
            })
        }
        return HTMLResponse(f"<span>Submitted {len(submitted_acts)}</span>", status_code=200, headers=headers)

    @app.get("/b/{slug}", response_class=HTMLResponse)
    def page_bot(request: Request, slug: str) -> Any:
        bot_obj = find_bot(slug)
        if not bot_obj:
            raise HTTPException(status_code=404, detail="Bot not found")

        conn = get_db()
        latest_rep = conn.execute(
            """
            SELECT * FROM reports
            WHERE bot_id = ?
            ORDER BY report_date DESC, id DESC
            LIMIT 1
            """,
            (bot_obj.id,),
        ).fetchone()

        latest_report_dict = dict(latest_rep) if latest_rep else None
        latest_report_html = render_markdown(latest_report_dict["body_md"]) if latest_report_dict else ""

        items = items_open(conn, bot=bot_obj.slug, include_all=False)
        snoozed = items_snoozed(conn, bot_obj.slug)
        history = report_history(conn, bot=bot_obj.slug)
        conn.close()

        ctx = common_context(request, current_bot=bot_obj.slug)
        ctx.update({
            "bot": bot_obj,
            "latest_report": latest_report_dict,
            "latest_report_html": latest_report_html,
            "items": items,
            "snoozed_items": snoozed,
            "history": history,
        })
        return templates.TemplateResponse(request=request, name="bot.html", context=ctx)

    @app.get("/r/{report_id}", response_class=HTMLResponse)
    def page_report(request: Request, report_id: int) -> Any:
        conn = get_db()
        rep = get_report(conn, report_id)
        conn.close()
        if not rep:
            raise HTTPException(status_code=404, detail="Report not found")

        body_html = render_markdown(rep["body_md"])

        ctx = common_context(request, current_bot=rep["bot_slug"])
        ctx.update({
            "report": rep,
            "body_html": body_html,
        })
        return templates.TemplateResponse(request=request, name="report.html", context=ctx)

    @app.get("/i/{item_id}", response_class=HTMLResponse)
    def page_item(request: Request, item_id: int) -> Any:
        conn = get_db()
        item = item_timeline(conn, item_id)
        if not item:
            conn.close()
            raise HTTPException(status_code=404, detail="Item not found")

        for entry in item["timeline"]:
            entry["body_html"] = render_markdown(entry.get("body_md") or "")

        actions_list = list_actions_for_item(conn, item_id)
        bot_cli = bot_cli_name(item["bot_slug"])
        wake_avail = dispatch_configured()

        q_texts = queued_item_texts(conn)
        item["queued_text"] = q_texts.get(item_id)
        conn.close()

        ctx = common_context(request, current_bot=item["bot_slug"])
        ctx.update({
            "item": item,
            "actions": actions_list,
            "wake_available": wake_avail,
            "today": today_et(),
            "from_row": False,
        })
        return templates.TemplateResponse(request=request, name="item.html", context=ctx)

    @app.get("/i/{item_id}/actions", response_class=HTMLResponse)
    def item_actions_get(request: Request, item_id: int, from_row: Optional[str] = None) -> Any:
        conn = get_db()
        item = item_timeline(conn, item_id)
        if not item:
            conn.close()
            raise HTTPException(status_code=404, detail="Item not found")

        actions_list = list_actions_for_item(conn, item_id)
        bot_cli = bot_cli_name(item["bot_slug"])
        wake_avail = dispatch_configured()
        conn.close()

        ctx = {
            "request": request,
            "item": item,
            "actions": actions_list,
            "wake_available": wake_avail,
            "today": today_et(),
            "from_row": (from_row == "1"),
        }
        return templates.TemplateResponse(request=request, name="partials/item_actions.html", context=ctx)

    @app.post("/i/{item_id}/action")
    async def item_action_post(
        request: Request,
        item_id: int,
        background_tasks: BackgroundTasks,
    ) -> Response:
        _verify_csrf(request)
        form = await request.form()
        act_type = str(form.get("type", "")).strip()
        until_val = form.get("until")
        delta_val = form.get("delta")
        text_val = form.get("text")
        from_row = str(form.get("from_row", "")).strip() == "1"

        today_d = datetime.now(TZ_ET).date()
        payload: dict[str, Any] = {}

        if act_type == "snooze":
            u_str = str(until_val or "").strip()
            if u_str == "1d":
                final_until = (today_d + timedelta(days=1)).strftime("%Y-%m-%d")
            elif u_str == "3d":
                final_until = (today_d + timedelta(days=3)).strftime("%Y-%m-%d")
            elif u_str == "1w":
                final_until = (today_d + timedelta(days=7)).strftime("%Y-%m-%d")
            else:
                final_until = u_str
            payload["until"] = final_until
        elif act_type == "priority":
            try:
                payload["delta"] = int(str(delta_val))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid priority delta")
        elif act_type == "reply":
            payload["text"] = str(text_val or "").strip()
        elif act_type in ("done", "dismiss", "reopen"):
            pass
        else:
            raise HTTPException(status_code=400, detail=f"Invalid action type: {act_type}")

        author = request.headers.get("Tailscale-User-Login") or "local"
        conn = get_db()
        try:
            if act_type == "reply":
                # Replies follow the M2.5 lifecycle: Queue -> queued; Send -> submitted + one dispatch.
                if str(form.get("mode") or "") == "queue":
                    act = queue_text(conn, item_id, payload["text"], author)
                else:
                    act = submit_text(conn, item_id, payload["text"], author)
                    background_tasks.add_task(trigger_dispatch, active_db, [act["id"]])
            else:
                act = create_action(conn, item_id, act_type, payload, author)
        except ValueError as e:
            conn.close()
            raise HTTPException(status_code=400, detail=str(e))

        headers = {"HX-Trigger": "itemUpdated"}

        if from_row and act_type in ("done", "dismiss", "snooze"):
            status_text = "Done ✓" if act_type == "done" else ("Dismissed" if act_type == "dismiss" else f"Snoozed until {payload.get('until')}")
            stub_html = f"""<div class="item-row item-done-stub" id="item-row-{item_id}" style="padding: 10px 14px; background: var(--surface-alt); border-radius: var(--radius); margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; width: 100%;">
  <span style="color: var(--text-muted); font-size: 0.9rem;">{status_text}</span>
  <form hx-post="/i/{item_id}/action" hx-target="#item-row-{item_id}" hx-swap="outerHTML" style="margin: 0;">
    <input type="hidden" name="type" value="reopen">
    <input type="hidden" name="from_row" value="1">
    <button type="submit" class="btn btn-sm" style="min-height: 32px; padding: 0 10px; font-size: 0.8rem;">Undo</button>
  </form>
</div>"""
            conn.close()
            return HTMLResponse(stub_html, headers=headers)

        if from_row:
            resp = _render_item_row(conn, item_id, request)
            resp.headers["HX-Trigger"] = "itemUpdated"
            conn.close()
            return resp

        item = item_timeline(conn, item_id)
        actions_list = list_actions_for_item(conn, item_id)
        bot_cli = bot_cli_name(item["bot_slug"]) if item else ""
        wake_avail = dispatch_configured()
        conn.close()

        ctx = {
            "request": request,
            "item": item,
            "actions": actions_list,
            "wake_available": wake_avail,
            "today": today_et(),
            "from_row": from_row,
        }
        return templates.TemplateResponse(
            request=request,
            name="partials/item_actions.html",
            context=ctx,
            headers=headers,
        )

    @app.get("/history", response_class=HTMLResponse)
    def page_history(request: Request, bot: Optional[str] = None) -> Any:
        conn = get_db()
        history = report_history(conn, bot=bot)
        conn.close()

        # Group reports by date, newest first
        grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        for rep in history:
            d = rep["report_date"]
            grouped.setdefault(d, []).append(rep)

        ctx = common_context(request, current_nav="history")
        ctx.update({
            "grouped_reports": grouped,
            "filter_bot": bot or "",
        })
        return templates.TemplateResponse(request=request, name="history.html", context=ctx)

    @app.get("/search", response_class=HTMLResponse)
    def page_search(request: Request, q: Optional[str] = None) -> Any:
        conn = get_db()
        query = (q or "").strip()
        res = search(conn, query) if query else {"reports": [], "items": []}
        conn.close()

        ctx = common_context(request)
        ctx.update({
            "query": query,
            "reports": res["reports"],
            "items": res["items"],
        })

        if request.headers.get("HX-Request") == "true":
            return templates.TemplateResponse(request=request, name="partials/search_results.html", context=ctx)

        return templates.TemplateResponse(request=request, name="search.html", context=ctx)

    # Local-only Bot APIs
    @app.get("/api/actions")
    def api_actions(
        request: Request,
        status: Optional[str] = None,
        bot: Optional[str] = None,
    ) -> JSONResponse:
        _verify_local_only(request)
        conn = get_db()
        try:
            res = list_actions(conn, status=status, bot=bot)
            return JSONResponse(res)
        finally:
            conn.close()

    def _check_action_bot(conn: Any, action_id: int, body: dict[str, Any], op: str, request: Request) -> None:
        """M-4: optional per-bot scoping for /api/actions/{id}/mark|ack. If the caller says which bot it is
        ("bot": "<slug>"), it may only touch that bot's actions (chief-of-staff may touch any). Every call is logged."""
        row = conn.execute(
            "SELECT a.id, b.slug AS bot_slug FROM actions a LEFT JOIN bots b ON b.id = a.bot_id WHERE a.id = ?",
            (action_id,),
        ).fetchone()
        owner = row["bot_slug"] if row else None
        caller = body.get("bot")
        caller_bot = find_bot(str(caller)) if caller else None
        logger.info(f"api {op} action={action_id} owner={owner or '-'} caller_bot={caller or '-'}")
        try:
            with open(ACCESS_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{now_et().isoformat()} api_{op} action={action_id} owner={owner or '-'} caller_bot={caller or '-'}\n")
        except Exception:
            pass
        if caller and row:
            if caller_bot is None:
                raise HTTPException(status_code=400, detail=f"Unknown bot: '{caller}'")
            if caller_bot.slug != "cos" and caller_bot.slug != owner:
                raise HTTPException(status_code=403, detail=f"Forbidden: action {action_id} belongs to {owner}")

    @app.post("/api/actions/{action_id}/mark")
    async def api_mark(
        request: Request,
        action_id: int,
    ) -> JSONResponse:
        _verify_local_only(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        status = body.get("status")
        if not status:
            raise HTTPException(status_code=400, detail="Missing status field")
        conn = get_db()
        try:
            _check_action_bot(conn, action_id, body, "mark", request)
            res = mark(conn, action_id, status=status)
            return JSONResponse(res)
        except ValueError as e:
            msg = str(e)
            code = 404 if "not found" in msg.lower() else 400
            raise HTTPException(status_code=code, detail=msg)
        finally:
            conn.close()

    @app.get("/api/inbox")
    def api_inbox(
        request: Request,
        bot: str,
        include_seen: str = "0",
        peek: str = "0",
    ) -> JSONResponse:
        _verify_local_only(request)
        bot_obj = find_bot(bot)
        if not bot_obj:
            raise HTTPException(status_code=404, detail=f"Unknown bot: '{bot}'")
        inc_seen = include_seen.lower() in ("1", "true", "yes")
        is_peek = peek.lower() in ("1", "true", "yes")
        conn = get_db()
        try:
            res = inbox(conn, bot_obj, include_seen=inc_seen, peek=is_peek)
            return JSONResponse(res)
        finally:
            conn.close()

    @app.post("/api/actions/{action_id}/ack")
    async def api_ack(
        request: Request,
        action_id: int,
    ) -> JSONResponse:
        _verify_local_only(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        status = body.get("status")
        note = body.get("note")
        keep_open = body.get("keep_open", False)
        if not status:
            raise HTTPException(status_code=400, detail="Missing status field")
        conn = get_db()
        try:
            _check_action_bot(conn, action_id, body, "ack", request)
            res = ack(conn, action_id, status=status, note=note, keep_open=keep_open)
            return JSONResponse(res)
        except ValueError as e:
            msg = str(e)
            code = 404 if "not found" in msg.lower() else 400
            raise HTTPException(status_code=code, detail=msg)
        finally:
            conn.close()

    @app.get("/api/items/{item_id}/actions")
    def api_item_actions(
        request: Request,
        item_id: int,
    ) -> JSONResponse:
        _verify_local_only(request)
        conn = get_db()
        try:
            it = conn.execute("SELECT id FROM items WHERE id = ?", (item_id,)).fetchone()
            if not it:
                raise HTTPException(status_code=404, detail=f"Item not found: {item_id}")
            res = list_actions_for_item(conn, item_id)
            return JSONResponse(res)
        finally:
            conn.close()

    # JSON APIs
    @app.get("/api/items")
    def api_items(bot: Optional[str] = None, all: bool = False) -> JSONResponse:
        conn = get_db()
        items = items_open(conn, bot=bot, include_all=all)
        conn.close()
        return JSONResponse(items)

    @app.get("/api/reports")
    def api_reports(bot: Optional[str] = None, date: Optional[str] = None) -> JSONResponse:
        conn = get_db()
        history = report_history(conn, bot=bot)
        conn.close()
        if date:
            history = [r for r in history if r["report_date"] == date]
        return JSONResponse(history)

    @app.get("/api/report/{report_id}")
    def api_report(report_id: int) -> JSONResponse:
        conn = get_db()
        rep = get_report(conn, report_id)
        conn.close()
        if not rep:
            raise HTTPException(status_code=404, detail="Report not found")
        return JSONResponse(rep)

    # L-4: reject unexpected Host headers (DNS-rebinding guard). Outermost middleware.
    allowed_hosts = list(ALLOWED_HOSTS)
    if "pytest" in sys.modules:
        allowed_hosts.append("testserver")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    """Lazy `app` for uvicorn (`briefs.web:app`) without side effects on import (pytest)."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
