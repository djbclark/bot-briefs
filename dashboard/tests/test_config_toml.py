"""Runtime TOML configuration (fail-closed auth, bots, timezone)."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap


def _dashboard_root() -> str:
    return os.path.dirname(os.path.dirname(__file__))


def _reload_config(env: dict[str, str]) -> str:
    code = textwrap.dedent(
        """
        import briefs.config as c
        print(sorted(c.ALLOWED_LOGINS))
        print(c.TZ_ET.key)
        print(",".join(b.slug for b in c.BOTS))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        cwd=_dashboard_root(),
    )
    return proc.stdout.strip()


def test_fail_closed_without_allowed_logins(tmp_path):
    env = os.environ.copy()
    env.pop("BRIEFS_ALLOWED_LOGINS", None)
    env.pop("BRIEFS_CONFIG_FILE", None)
    env.pop("BRIEFS_ENV_FILE", None)
    env["PYTHONPATH"] = _dashboard_root()
    out = _reload_config(env)
    assert out.splitlines()[0] == "[]"


def test_toml_sets_allowed_logins_and_bots(tmp_path):
    cfg = tmp_path / "briefs.toml"
    cfg.write_text(
        textwrap.dedent(
            """
            [auth]
            allowed_logins = ["operator@example.test"]
            [server]
            timezone = "Europe/Berlin"
            [[bots]]
            id = "00000000-0000-4000-8000-000000000099"
            slug = "demo"
            name = "Demo Bot"
            sort = 5
            area_default = "general"
            cli_name = "demo"
            """
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["BRIEFS_CONFIG_FILE"] = str(cfg)
    env.pop("BRIEFS_ALLOWED_LOGINS", None)
    env["PYTHONPATH"] = _dashboard_root()
    out = _reload_config(env).splitlines()
    assert "operator@example.test" in out[0]
    assert out[1] == "Europe/Berlin"
    assert "demo" in out[2]
