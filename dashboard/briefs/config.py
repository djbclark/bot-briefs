from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_local_env() -> None:
    """Load KEY=VALUE lines from config.local.env (or $BRIEFS_ENV_FILE) into os.environ without overriding
    variables that are already set. Keeps deployment-specific values (allowed logins, bot ids) out of the code."""
    path = Path(os.environ.get("BRIEFS_ENV_FILE", str(REPO_ROOT / "config.local.env")))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip().strip('"').strip("'")
        if k:
            os.environ.setdefault(k, v)


_load_local_env()

# Database configuration
DEFAULT_DB_PATH = REPO_ROOT / "data" / "briefs.db"
BRIEFS_DB = os.environ.get("BRIEFS_DB", str(DEFAULT_DB_PATH))

# Server configuration
BRIEFS_PORT = int(os.environ.get("BRIEFS_PORT", "8642"))
BRIEFS_BASE_URL = os.environ.get("BRIEFS_BASE_URL", f"http://127.0.0.1:{BRIEFS_PORT}")
# Tailnet URL the operator opens (used for item links handed to bots).
BRIEFS_PUBLIC_URL = os.environ.get("BRIEFS_PUBLIC_URL", "https://grok-bot-box.example-tailnet.ts.net")

# Auth configuration
# No default: unless BRIEFS_ALLOWED_LOGINS is set (env or config.local.env) no Tailscale login is allowed,
# so a fresh copy fails closed. Comma-separated, e.g. BRIEFS_ALLOWED_LOGINS=you@example.com
_allowed_env = os.environ.get("BRIEFS_ALLOWED_LOGINS", "")
ALLOWED_LOGINS: set[str] = {s.strip() for s in _allowed_env.split(",") if s.strip()}
if not ALLOWED_LOGINS:
    import logging as _logging

    _logging.getLogger("briefs.config").warning(
        "BRIEFS_ALLOWED_LOGINS is not set: every Tailscale-proxied request will be refused (fail closed)"
    )

# Host headers the web app accepts (L-4: DNS-rebinding guard). Comma-separated; ports are ignored.
_default_hosts = "localhost,127.0.0.1,::1,<box-host>.example-tailnet.ts.net,<vps-host>.example-tailnet.ts.net"
ALLOWED_HOSTS: list[str] = [
    h.strip() for h in os.environ.get("BRIEFS_ALLOWED_HOSTS", _default_hosts).split(",") if h.strip()
]

# Sources directory override (e.g. dev/samples)
BRIEFS_SOURCES_DIR = os.environ.get("BRIEFS_SOURCES_DIR")

# Timezone
TZ_ET = ZoneInfo("America/New_York")


def today_et() -> str:
    """Return today's date formatted as YYYY-MM-DD in America/New_York."""
    return datetime.now(TZ_ET).strftime("%Y-%m-%d")


def now_et() -> datetime:
    """Return current datetime in America/New_York."""
    return datetime.now(TZ_ET)


@dataclass(frozen=True)
class Bot:
    id: str
    slug: str
    name: str
    sort: int
    area_default: str
    cli_name: str


BOTS: list[Bot] = [
    Bot("00000000-0000-4000-8000-000000000001", "cos", "Chief of Staff", 0, "admin", "chief-of-staff"),
    Bot("00000000-0000-4000-8000-000000000002", "radar", "Radar", 10, "code", "radar"),
    Bot("00000000-0000-4000-8000-000000000003", "zero", "Zero", 20, "email", "zero"),
    Bot("00000000-0000-4000-8000-000000000004", "family", "Family", 30, "family", "family"),
    Bot("00000000-0000-4000-8000-000000000005", "money", "Money", 40, "finance", "money"),
    Bot("00000000-0000-4000-8000-000000000006", "business", "Business", 50, "business", "business"),
    Bot("00000000-0000-4000-8000-000000000007", "chess", "Chess", 60, "chess", "chess"),
    Bot("00000000-0000-4000-8000-000000000008", "pika", "Pika", 70, "pika", "pika"),
    Bot("00000000-0000-4000-8000-000000000009", "friends", "Friends", 80, "social", "friends"),
    Bot("00000000-0000-4000-8000-000000000010", "project", "Project", 90, "project", "project-bot"),
    Bot("00000000-0000-4000-8000-000000000011", "oneoffs", "One-offs", 100, "misc", "one-offs"),
    Bot("00000000-0000-4000-8000-000000000012", "coding", "Coding", 110, "code", "coding"),
    Bot("00000000-0000-4000-8000-000000000013", "other", "Other", 120, "misc", "other"),
]

# Optional override of the Grok Bot agent ids: BRIEFS_BOT_IDS="cos=<uuid>,radar=<uuid>,..." (slug=id).
_id_over = dict(
    p.split("=", 1) for p in (x.strip() for x in os.environ.get("BRIEFS_BOT_IDS", "").split(",")) if "=" in p
)
if _id_over:
    from dataclasses import replace as _dc_replace

    BOTS = [_dc_replace(b, id=_id_over.get(b.slug, b.id).strip()) for b in BOTS]

BOTS_BY_ID: dict[str, Bot] = {b.id.lower(): b for b in BOTS}
BOTS_BY_SLUG: dict[str, Bot] = {b.slug.lower(): b for b in BOTS}
BOTS_BY_NAME: dict[str, Bot] = {b.name.lower(): b for b in BOTS}
BOTS_BY_CLI_NAME: dict[str, Bot] = {b.cli_name.lower(): b for b in BOTS}


def bot_cli_name(bot: Bot | str) -> str:
    """Return the canonical CLI/notify name for a bot or bot identifier."""
    if isinstance(bot, Bot):
        return bot.cli_name
    b = find_bot(bot)
    if b:
        return b.cli_name
    return str(bot)


def find_bot(identifier: str) -> Bot | None:
    """Find a bot by UUID, slug, name, or CLI name (case-insensitive, normalized)."""
    if not identifier:
        return None
    val = identifier.strip().lower()
    if val in BOTS_BY_ID:
        return BOTS_BY_ID[val]
    if val in BOTS_BY_SLUG:
        return BOTS_BY_SLUG[val]
    if val in BOTS_BY_NAME:
        return BOTS_BY_NAME[val]
    if val in BOTS_BY_CLI_NAME:
        return BOTS_BY_CLI_NAME[val]
    # Try normalized name/slug (e.g. "project-bot" -> "project bot")
    normalized = val.replace("-", " ").replace("_", " ")
    for b in BOTS:
        if (
            b.name.lower() == normalized
            or b.slug.lower() == normalized
            or b.cli_name.lower() == normalized
            or b.cli_name.lower().replace("-", " ") == normalized
        ):
            return b
    return None
