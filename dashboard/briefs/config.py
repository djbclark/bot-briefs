from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

logger = logging.getLogger("briefs.config")

# Python package root (briefs/); store assets live here in the Nix store / editable install.
PACKAGE_ROOT = Path(__file__).resolve().parent
# Dashboard repo root (parent of briefs/); dev data, samples, and local config.
REPO_ROOT = PACKAGE_ROOT.parent

_TOML_BOTS: list[dict[str, Any]] | None = None
_TOML_TIMEZONE: str | None = None


def state_home() -> Path:
    """Writable state root (VPS /var/lib/briefs or dashboard repo in dev)."""
    if vps := os.environ.get("BRIEFS_VPS_HOME"):
        return Path(vps)
    return REPO_ROOT


def _state_home() -> Path:
    return state_home()


def _load_toml_config() -> None:
    """Load BRIEFS_CONFIG_FILE into os.environ (setdefault) and module-level TOML tables."""
    global _TOML_BOTS, _TOML_TIMEZONE
    cfg_path = os.environ.get("BRIEFS_CONFIG_FILE", "").strip()
    if not cfg_path:
        return
    path = Path(cfg_path)
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        logger.warning("BRIEFS_CONFIG_FILE %s not readable: %s", cfg_path, e)
        return
    except tomllib.TOMLDecodeError as e:
        logger.error("Invalid TOML in %s: %s", cfg_path, e)
        return

    server = data.get("server") or {}
    auth = data.get("auth") or {}
    paths = data.get("paths") or {}

    if tz := server.get("timezone"):
        _TOML_TIMEZONE = str(tz)
    def _apply_env(key: str, value: str) -> None:
        if key not in os.environ:
            os.environ[key] = value

    if port := server.get("port"):
        _apply_env("BRIEFS_PORT", str(port))
    if public_url := server.get("public_url"):
        _apply_env("BRIEFS_PUBLIC_URL", str(public_url))
    if base_url := server.get("base_url"):
        _apply_env("BRIEFS_BASE_URL", str(base_url))

    logins = auth.get("allowed_logins")
    if logins is not None and "BRIEFS_ALLOWED_LOGINS" not in os.environ:
        if isinstance(logins, list):
            _apply_env("BRIEFS_ALLOWED_LOGINS", ",".join(str(x) for x in logins if str(x).strip()))
        elif isinstance(logins, str):
            _apply_env("BRIEFS_ALLOWED_LOGINS", logins)

    hosts = auth.get("allowed_hosts")
    if hosts is not None and "BRIEFS_ALLOWED_HOSTS" not in os.environ:
        if isinstance(hosts, list):
            _apply_env("BRIEFS_ALLOWED_HOSTS", ",".join(str(h) for h in hosts if str(h).strip()))
        elif isinstance(hosts, str):
            _apply_env("BRIEFS_ALLOWED_HOSTS", hosts)

    if db := paths.get("db"):
        _apply_env("BRIEFS_DB", str(db))
    if sources := paths.get("sources_dir"):
        _apply_env("BRIEFS_SOURCES_DIR", str(sources))

    todo_review = data.get("todo_review") or {}
    if isinstance(todo_review, dict):
        owners = todo_review.get("owners")
        if isinstance(owners, list) and owners:
            _apply_env("BRIEFS_TODO_REVIEW_OWNERS", ",".join(str(o) for o in owners if str(o).strip()))
        for tkey, env_key in (
            ("account", "BRIEFS_TODO_REVIEW_ACCOUNT"),
            ("export_dir", "BRIEFS_TODO_REVIEW_EXPORT_DIR"),
            ("dataset", "BRIEFS_TODO_REVIEW_DATASET"),
            ("threads", "BRIEFS_TODO_REVIEW_THREADS"),
        ):
            if val := todo_review.get(tkey):
                _apply_env(env_key, str(val))

    bots = data.get("bots")
    if isinstance(bots, list) and bots:
        _TOML_BOTS = [b for b in bots if isinstance(b, dict)]

    bot_ids = auth.get("bot_ids") or server.get("bot_ids")
    if "BRIEFS_BOT_IDS" not in os.environ:
        if isinstance(bot_ids, dict) and bot_ids:
            _apply_env("BRIEFS_BOT_IDS", ",".join(f"{k}={v}" for k, v in bot_ids.items()))
        elif isinstance(bot_ids, str) and bot_ids:
            _apply_env("BRIEFS_BOT_IDS", bot_ids)


def _load_local_env() -> None:
    """Load KEY=VALUE lines from config.local.env (or $BRIEFS_ENV_FILE) without overriding existing env."""
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


_load_toml_config()
_load_local_env()

DEFAULT_DB_PATH = _state_home() / "data" / "briefs.db"
BRIEFS_DB = os.environ.get("BRIEFS_DB", str(DEFAULT_DB_PATH))

BRIEFS_PORT = int(os.environ.get("BRIEFS_PORT", "8642"))
BRIEFS_BASE_URL = os.environ.get("BRIEFS_BASE_URL", f"http://127.0.0.1:{BRIEFS_PORT}")
BRIEFS_PUBLIC_URL = os.environ.get("BRIEFS_PUBLIC_URL", "https://briefs.example-tailnet.ts.net")

_allowed_env = os.environ.get("BRIEFS_ALLOWED_LOGINS", "")
ALLOWED_LOGINS: set[str] = {s.strip() for s in _allowed_env.split(",") if s.strip()}
if not ALLOWED_LOGINS:
    logger.warning(
        "BRIEFS_ALLOWED_LOGINS is not set: every Tailscale-proxied request will be refused (fail closed)"
    )

_default_hosts = "localhost,127.0.0.1,::1,briefs.example-tailnet.ts.net"
ALLOWED_HOSTS: list[str] = [
    h.strip() for h in os.environ.get("BRIEFS_ALLOWED_HOSTS", _default_hosts).split(",") if h.strip()
]

BRIEFS_SOURCES_DIR = os.environ.get("BRIEFS_SOURCES_DIR")

_tz_name = _TOML_TIMEZONE or os.environ.get("BRIEFS_TIMEZONE", "America/New_York")
TZ_ET = ZoneInfo(_tz_name)


def today_et() -> str:
    return datetime.now(TZ_ET).strftime("%Y-%m-%d")


def now_et() -> datetime:
    return datetime.now(TZ_ET)


@dataclass(frozen=True)
class Bot:
    id: str
    slug: str
    name: str
    sort: int
    area_default: str
    cli_name: str


def _default_bots() -> list[Bot]:
    return [
        Bot("00000000-0000-4000-8000-000000000001", "bot-1", "Example Bot 1", 10, "general", "bot-1"),
        Bot("00000000-0000-4000-8000-000000000002", "bot-2", "Example Bot 2", 20, "general", "bot-2"),
        Bot("00000000-0000-4000-8000-000000000003", "bot-3", "Example Bot 3", 30, "general", "bot-3"),
        Bot("00000000-0000-4000-8000-000000000004", "bot-4", "Example Bot 4", 40, "general", "bot-4"),
        Bot("00000000-0000-4000-8000-000000000005", "bot-5", "Example Bot 5", 50, "general", "bot-5"),
        Bot("00000000-0000-4000-8000-000000000006", "bot-6", "Example Bot 6", 60, "general", "bot-6"),
        Bot("00000000-0000-4000-8000-000000000007", "bot-7", "Example Bot 7", 70, "general", "bot-7"),
        Bot("00000000-0000-4000-8000-000000000008", "bot-8", "Example Bot 8", 80, "general", "bot-8"),
        Bot("00000000-0000-4000-8000-000000000009", "bot-9", "Example Bot 9", 90, "general", "bot-9"),
        Bot("00000000-0000-4000-8000-000000000010", "bot-10", "Example Bot 10", 100, "general", "bot-10"),
        Bot("00000000-0000-4000-8000-000000000011", "bot-11", "Example Bot 11", 110, "general", "bot-11"),
        Bot("00000000-0000-4000-8000-000000000012", "bot-12", "Example Bot 12", 120, "general", "bot-12"),
        Bot("00000000-0000-4000-8000-000000000013", "bot-13", "Example Bot 13", 130, "general", "bot-13"),
    ]


def _bots_from_toml(rows: list[dict[str, Any]]) -> list[Bot]:
    out: list[Bot] = []
    for row in rows:
        try:
            out.append(
                Bot(
                    id=str(row["id"]),
                    slug=str(row["slug"]),
                    name=str(row["name"]),
                    sort=int(row["sort"]),
                    area_default=str(row.get("area_default", "general")),
                    cli_name=str(row.get("cli_name", row["slug"])),
                )
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Skipping invalid bot row in config: %s", e)
    return out or _default_bots()


BOTS: list[Bot] = _bots_from_toml(_TOML_BOTS) if _TOML_BOTS else _default_bots()

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
    if isinstance(bot, Bot):
        return bot.cli_name
    b = find_bot(bot)
    if b:
        return b.cli_name
    return str(bot)


def find_bot(identifier: str) -> Bot | None:
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


def logs_dir() -> Path:
    if explicit := os.environ.get("BRIEFS_LOGS_DIR"):
        return Path(explicit)
    return _state_home() / "logs"
