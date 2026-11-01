#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.17,<2"]
# ///
"""Saner.AI MCP server (stdio).

Exposes the <PRIVATE_REPO> plugin as MCP tools. Every call shells out to the
Saner command (default: ~/.hermes/bin/saner, which holds the shared OpenCLI
physical-profile lease and forces background-window mode; falls back to
`opencli saner`). Argument lists only, never a shell.

Each call gets its own throwaway conversation-pointer file, so concurrent
clients can't continue each other's Skai conversations: `ask` starts a new
conversation unless `conversation_id` is given, and always returns the id.

Environment:
  SANER_CMD             command prefix (whitespace-split), e.g. "opencli saner"
  SANER_BRIDGE_OWNER    lease owner label (default saner-mcp)
  SANER_LOCK_TIMEOUT    seconds to wait for the profile lease (default 90)
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

INSTRUCTIONS = """\
Tools for the operator's Saner.AI account: tasks, notes (most are synced Gmail
messages and calendar events), conversations, and Skai, Saner's assistant that
can see all of it.
Prefer the read tools (saner_tasks, saner_search, saner_note, saner_notes,
saner_conversations, saner_read) for facts: they are fast, free, and leave no
trace. Use saner_ask only for synthesis across many notes. Each ask uses one
of about 200 daily Skai messages and creates a conversation the operator sees
in Saner. To follow up, pass the conversation_id that saner_ask returned.
IDs are UUIDs; any unique prefix of 4+ characters also works."""

mcp = FastMCP("saner", instructions=INSTRUCTIONS)


def _saner_cmd() -> list[str]:
    configured = os.environ.get("SANER_CMD", "").strip()
    if configured:
        return shlex.split(configured)
    wrapper = Path.home() / ".hermes" / "bin" / "saner"
    if wrapper.is_file() and os.access(wrapper, os.X_OK):
        return [str(wrapper)]
    if shutil.which("opencli"):
        return ["opencli", "saner"]
    raise ToolError("Neither ~/.hermes/bin/saner nor opencli is available on this host")


def _error_text(stdout: str, stderr: str, code: int) -> str:
    for blob in (stdout, stderr):
        for line in [blob.strip()] + blob.strip().splitlines():
            try:
                data = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict):
                err = data.get("error") if isinstance(data.get("error"), dict) else data
                message = err.get("message") or err.get("error_message") or err.get("error_type")
                if message:
                    hint = err.get("help") or err.get("hint")
                    kind = err.get("code") or err.get("error_type") or f"exit {code}"
                    return f"{kind}: {message}" + (f" (hint: {hint})" if hint else "")
    text = "\n".join(l for l in (stdout + "\n" + stderr).splitlines() if l.strip() and "update available" not in l.lower() and not l.strip().startswith(("Run:", "Download:")))
    return (text.strip() or f"saner command failed with exit {code}")[:1500]


def _run(args: list[str], *, timeout: float = 120, json_output: bool = True) -> tuple[Any, str]:
    """Run one saner command with a private conversation-pointer file; return (parsed stdout, conversation id)."""
    with tempfile.TemporaryDirectory(prefix="saner-mcp-") as tmp:
        state = Path(tmp) / "state.json"
        env = {
            **os.environ,
            "OPENCLI_SANER_STATE": str(state),
            "SANER_BRIDGE_OWNER": os.environ.get("SANER_BRIDGE_OWNER", "saner-mcp"),
            "SANER_LOCK_TIMEOUT": os.environ.get("SANER_LOCK_TIMEOUT", "90"),
        }
        argv = _saner_cmd() + args + (["-f", "json"] if json_output else [])
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"saner command timed out after {timeout:g}s") from exc
        conversation_id = ""
        if state.exists():
            try:
                conversation_id = json.loads(state.read_text()).get("conversation_id", "")
            except ValueError:
                pass
        if proc.returncode == 16:
            raise ToolError("lock_busy: another caller is using the operator's browser profile; retry in a minute")
        if proc.returncode != 0:
            raise ToolError(_error_text(proc.stdout, proc.stderr, proc.returncode))
        if not json_output:
            return proc.stdout.strip(), conversation_id
        try:
            return json.loads(proc.stdout), conversation_id
        except ValueError as exc:
            raise ToolError(f"unexpected output from saner: {proc.stdout[:300]!r}") from exc


def _one(rows: Any) -> Any:
    return rows[0] if isinstance(rows, list) and len(rows) == 1 else rows


@mcp.tool()
def saner_status() -> dict:
    """Login state, plan, remaining daily Skai messages and AI requests, and minutes until the session token expires."""
    rows, _ = _run(["status"])
    row = dict(_one(rows))
    row.pop("current_conversation", None)
    return row


@mcp.tool()
def saner_tasks(status: Literal["open", "done", "all"] = "open", due_only: bool = False, limit: int = 50) -> list[dict]:
    """List tasks (id, content, priority, due date, done, recurring). due_only=True keeps tasks with a due date."""
    args = ["tasks", "--status", status, "--limit", str(limit)]
    if due_only:
        args += ["--due", "true"]
    rows, _ = _run(args)
    return rows


@mcp.tool()
def saner_search(query: str, mode: Literal["smart", "semantic"] = "smart", limit: int = 10) -> list[dict]:
    """Search notes, synced emails and calendar events. smart = keyword/fuzzy; semantic = by meaning. Query must be 3+ characters. Returns ids for saner_note."""
    rows, _ = _run(["search", query, "--mode", mode, "--limit", str(limit)])
    return rows


@mcp.tool()
def saner_note(note_id: str) -> dict:
    """Full text of one note, email or calendar event (id or unique 4+ char prefix from saner_search/saner_notes)."""
    rows, _ = _run(["note", note_id])
    return _one(rows)


@mcp.tool()
def saner_notes(limit: int = 20) -> list[dict]:
    """Most recently updated notes (id, title, type, source, updated_at, preview)."""
    rows, _ = _run(["notes", "--limit", str(limit)])
    return rows


@mcp.tool()
def saner_conversations(query: str | None = None, kind: Literal["all", "chat", "proactive"] = "all", limit: int = 20) -> list[dict]:
    """List or search Skai conversations. kind=proactive returns Skai's twice-daily briefings about the operator's day."""
    args = ["conversations"] + ([query] if query else []) + ["--kind", kind, "--limit", str(limit)]
    rows, _ = _run(args)
    for row in rows:
        row.pop("current", None)
    return rows


@mcp.tool()
def saner_read(conversation_id: str, last: int | None = None) -> list[dict]:
    """Messages (role, sender, created_at, content) of one conversation; last=N keeps only the last N."""
    args = ["read", conversation_id] + (["--last", str(last)] if last else [])
    rows, _ = _run(args)
    return rows


@mcp.tool()
def saner_ask(prompt: str, conversation_id: str | None = None, bot: str | None = None, reasoning: bool | None = None, timeout: int = 150) -> dict:
    """Ask Skai (sees the operator's notes, tasks, email and calendar) and return its answer.

    Starts a new conversation unless conversation_id is given; the returned conversation_id continues it.
    WRITE action: uses one daily Skai message and is visible to the operator in Saner.
    bot defaults to 'skai' (reasoning mode on). External bots such as 'grok-4' cannot see Saner data.
    """
    args = ["ask", prompt, "--timeout", str(timeout)]
    args += ["--conversation", conversation_id] if conversation_id else ["--new", "true"]
    if bot:
        args += ["--bot", bot]
    if reasoning is not None:
        args += ["--reasoning", "true" if reasoning else "false"]
    text, conv = _run(args, timeout=timeout + 90, json_output=False)
    return {"response": text, "conversation_id": conv or conversation_id or ""}


if __name__ == "__main__":
    mcp.run()
