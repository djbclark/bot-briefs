#!/usr/bin/env python3
"""Unit tests for bin/aiuse-underpace-check."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "aiuse-underpace-check"

_loader = importlib.machinery.SourceFileLoader("aiuse_underpace_check", str(SCRIPT))
_spec = importlib.util.spec_from_loader("aiuse_underpace_check", _loader)
assert _spec is not None
auc = importlib.util.module_from_spec(_spec)
_loader.exec_module(auc)


def make_window(
    *,
    label: str = "Weekly quota",
    window_id: str = "weekly",
    used_percent: float | None = 20.0,
    resets_at: str | None = "2026-11-28T12:25:00+00:00",
    window_minutes: int | None = 10080,
    rolling: bool = True,
    state: str = "ok",
    pool_family: str = "default",
) -> dict:
    return {
        "label": label,
        "window_id": window_id,
        "used_percent": used_percent,
        "resets_at": resets_at,
        "window_minutes": window_minutes,
        "rolling": rolling,
        "state": state,
        "pool_family": pool_family,
    }


def make_account(
    *,
    provider: str = "codex",
    account: str | None = "user@example.com",
    billing_kind: str = "subscription_window",
    error: str | None = None,
    windows: list[dict] | None = None,
    source: str = "codexbar",
) -> dict:
    return {
        "source": source,
        "provider": provider,
        "account": account,
        "billing_kind": billing_kind,
        "error": error,
        "windows": windows if windows is not None else [],
    }


def make_envelope(accounts: list[dict], *, top_level: bool = False) -> dict:
    if top_level:
        return {"accounts": accounts}
    return {
        "schema_version": "1.8",
        "snapshot": {
            "collected_at": "2026-11-26T12:00:00+00:00",
            "accounts": accounts,
        },
    }


class TimeHelpersTest(unittest.TestCase):
    def test_now_from_default_utc_aware(self):
        dt = auc.now_from(None)
        self.assertIsNotNone(dt.tzinfo)
        self.assertEqual(dt.utcoffset(), timezone.utc.utcoffset(dt))

    def test_now_from_parses_iso_and_z(self):
        dt_z = auc.now_from("2026-11-26T12:25:00Z")
        dt_off = auc.now_from("2026-11-26T07:25:00-05:00")
        self.assertEqual(dt_z, dt_off)

    def test_fmt_et_format(self):
        dt = auc.now_from("2026-11-28T12:25:00+00:00")
        self.assertEqual(auc.fmt_et(dt), "Sat Nov 28, 7:25 AM ET")


class EvaluateTest(unittest.TestCase):
    def test_flagged_by_projected_only(self):
        # 7-day window (168h). Now is 48h before reset -> 120h elapsed -> 71.43% elapsed.
        # used=45% -> under_points=26.43 (<=30, NOT flagged by points),
        # projected=45/0.7143=63.0% (<70, FLAGGED by projected).
        now = auc.now_from("2026-11-26T12:25:00+00:00")
        data = make_envelope(
            [
                make_account(
                    provider="codex",
                    account="alice@example.com",
                    windows=[
                        make_window(
                            label="Codex weekly",
                            window_id="secondary",
                            used_percent=45.0,
                            resets_at="2026-11-28T12:25:00+00:00",
                            window_minutes=10080,
                        )
                    ],
                )
            ]
        )
        items = auc.evaluate(data, now, auc.DEFAULT_THRESHOLDS)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["key"], "aiuse-underpace:codex:alice@example.com:secondary")
        self.assertEqual(item["provider"], "codex")
        self.assertEqual(item["account"], "alice@example.com")
        self.assertEqual(item["label"], "Codex weekly")
        self.assertEqual(item["used_percent"], 45.0)
        self.assertEqual(item["elapsed_pct"], 71.4)
        self.assertEqual(item["projected_pct"], 63.0)
        self.assertEqual(item["under_points"], 26.4)
        self.assertLessEqual(item["under_points"], 30.0)
        self.assertLess(item["projected_pct"], 70.0)
        self.assertEqual(item["resets_et"], "Sat Nov 28, 7:25 AM ET")
        self.assertEqual(item["hours_left"], 48.0)
        self.assertIn("55% is still unused", item["suggestion"])
        self.assertIn("codex Codex weekly", item["suggestion"])

    def test_flagged_by_points_only(self):
        # 10-day window (14400 min = 240h). 24h left -> 90% elapsed.
        # used=55% -> under_points=35 (>30, FLAGGED by points),
        # projected=55/0.90=61.1% (>=50, NOT flagged by projected when projected_below=50).
        now = auc.now_from("2026-11-27T12:25:00+00:00")
        data = make_envelope(
            [
                make_account(
                    provider="antigravity",
                    account=None,
                    windows=[
                        make_window(
                            label="Gemini weekly",
                            window_id="gemini-weekly",
                            used_percent=55.0,
                            resets_at="2026-11-28T12:25:00+00:00",
                            window_minutes=14400,
                        )
                    ],
                )
            ]
        )
        thresholds = {**auc.DEFAULT_THRESHOLDS, "projected_below": 50, "under_pace_points": 30}
        items = auc.evaluate(data, now, thresholds)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["key"], "aiuse-underpace:antigravity:-:gemini-weekly")
        self.assertIsNone(item["account"])
        self.assertGreaterEqual(item["projected_pct"], 50.0)
        self.assertGreater(item["under_points"], 30.0)
        self.assertEqual(item["under_points"], 35.0)

    def test_not_flagged_when_resets_beyond_72h(self):
        now = auc.now_from("2026-11-26T12:00:00+00:00")
        data = make_envelope(
            [
                make_account(
                    windows=[
                        make_window(
                            used_percent=5.0,
                            resets_at="2026-11-29T12:01:00+00:00",  # 72h 1m out
                            window_minutes=10080,
                        )
                    ]
                )
            ]
        )
        items = auc.evaluate(data, now, auc.DEFAULT_THRESHOLDS)
        self.assertEqual(items, [])

    def test_not_flagged_for_5_hour_window(self):
        now = auc.now_from("2026-11-26T12:00:00+00:00")
        data = make_envelope(
            [
                make_account(
                    windows=[
                        make_window(
                            label="Gemini 5-hour",
                            window_id="gemini-5h",
                            used_percent=2.0,
                            resets_at="2026-11-26T13:00:00+00:00",  # 1h left, 80% elapsed
                            window_minutes=300,
                        )
                    ]
                )
            ]
        )
        items = auc.evaluate(data, now, auc.DEFAULT_THRESHOLDS)
        self.assertEqual(items, [])

    def test_not_flagged_at_100_percent_used(self):
        now = auc.now_from("2026-11-26T12:00:00+00:00")
        data = make_envelope(
            [
                make_account(
                    windows=[
                        make_window(
                            label="Claude/GPT weekly",
                            window_id="3p-weekly",
                            used_percent=100.0,
                            resets_at="2026-11-27T12:00:00+00:00",
                            window_minutes=10080,
                            state="exhausted",
                        )
                    ]
                )
            ]
        )
        items = auc.evaluate(data, now, auc.DEFAULT_THRESHOLDS)
        self.assertEqual(items, [])

    def test_skips_error_account(self):
        now = auc.now_from("2026-11-26T12:00:00+00:00")
        data = make_envelope(
            [
                make_account(
                    provider="claude",
                    error="cswap has no registered Claude Code accounts",
                    windows=[
                        make_window(
                            used_percent=5.0,
                            resets_at="2026-11-27T12:00:00+00:00",
                            window_minutes=10080,
                        )
                    ],
                )
            ]
        )
        items = auc.evaluate(data, now, auc.DEFAULT_THRESHOLDS)
        self.assertEqual(items, [])

    def test_skips_non_included_billing_kind_and_supports_top_level_accounts(self):
        now = auc.now_from("2026-11-26T12:00:00+00:00")
        data = make_envelope(
            [
                make_account(
                    provider="pi",
                    billing_kind="payg_api",
                    windows=[
                        make_window(
                            used_percent=5.0,
                            resets_at="2026-11-27T12:00:00+00:00",
                            window_minutes=10080,
                        )
                    ],
                )
            ],
            top_level=True,
        )
        self.assertEqual(auc.evaluate(data, now, auc.DEFAULT_THRESHOLDS), [])
        custom = {**auc.DEFAULT_THRESHOLDS, "include_billing_kinds": ["subscription_window", "payg_api"]}
        self.assertEqual(len(auc.evaluate(data, now, custom)), 1)

    def test_ignore_list_provider_and_provider_window_id(self):
        now = auc.now_from("2026-11-26T12:00:00+00:00")
        data = make_envelope(
            [
                make_account(
                    provider="codex",
                    windows=[
                        make_window(
                            label="Codex weekly",
                            window_id="secondary",
                            used_percent=10.0,
                            resets_at="2026-11-27T12:00:00+00:00",
                        )
                    ],
                ),
                make_account(
                    provider="antigravity",
                    account=None,
                    windows=[
                        make_window(
                            label="Gemini weekly",
                            window_id="gemini-weekly",
                            used_percent=10.0,
                            resets_at="2026-11-27T12:00:00+00:00",
                        ),
                        make_window(
                            label="Other weekly",
                            window_id="other-weekly",
                            used_percent=10.0,
                            resets_at="2026-11-27T18:00:00+00:00",
                        ),
                    ],
                ),
            ]
        )
        thresholds = {**auc.DEFAULT_THRESHOLDS, "ignore": ["codex", "antigravity/gemini-weekly"]}
        items = auc.evaluate(data, now, thresholds)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["key"], "aiuse-underpace:antigravity:-:other-weekly")

    def test_past_reset_and_zero_elapsed_not_flagged(self):
        now = auc.now_from("2026-11-26T12:00:00+00:00")
        data = make_envelope(
            [
                make_account(
                    windows=[
                        # Already reset at now
                        make_window(
                            window_id="at-now",
                            used_percent=0.0,
                            resets_at="2026-11-26T12:00:00+00:00",
                            window_minutes=10080,
                        ),
                        # 24h window resetting 48h from now -> elapsed_pct clamped to 0 -> projected None
                        make_window(
                            window_id="future-start",
                            used_percent=0.0,
                            resets_at="2026-11-28T12:00:00+00:00",
                            window_minutes=1440,
                        ),
                    ]
                )
            ]
        )
        self.assertEqual(auc.evaluate(data, now, auc.DEFAULT_THRESHOLDS), [])


class CliConfigAndOutputTest(unittest.TestCase):
    def _run_cli(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = auc.run(argv)
        return rc, buf.getvalue()

    def test_config_override_and_cli_override(self):
        now = "2026-11-26T12:00:00+00:00"
        # Window resets in 96h (beyond default 72h, within config 120h, beyond CLI 48h).
        data = make_envelope(
            [
                make_account(
                    provider="codex",
                    account="dev@example.com",
                    windows=[
                        make_window(
                            label="Codex weekly",
                            window_id="secondary",
                            used_percent=10.0,
                            resets_at="2026-11-30T12:00:00+00:00",
                            window_minutes=10080,
                        )
                    ],
                )
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            usage_path = Path(tmp) / "usage.json"
            usage_path.write_text(json.dumps(data), encoding="utf-8")
            missing_cfg = Path(tmp) / "missing.toml"
            cfg_path = Path(tmp) / "config.toml"
            cfg_path.write_text(
                "\n".join(
                    [
                        'include_billing_kinds = ["subscription_window"]',
                        'ignore = []',
                        "",
                        "[thresholds]",
                        "reset_within_hours = 120",
                        "projected_below = 75",
                        "under_pace_points = 25",
                        "min_window_hours = 24",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            # Missing config -> defaults (72h) -> 0 items
            rc, out = self._run_cli(
                ["--usage-json", str(usage_path), "--now", now, "--config", str(missing_cfg), "--json"]
            )
            self.assertEqual(rc, 0)
            payload_default = json.loads(out)
            self.assertEqual(payload_default["thresholds"]["reset_within_hours"], 72)
            self.assertEqual(payload_default["items"], [])

            # Config override (120h) -> 1 item
            rc, out = self._run_cli(
                ["--usage-json", str(usage_path), "--now", now, "--config", str(cfg_path), "--json"]
            )
            self.assertEqual(rc, 0)
            payload_cfg = json.loads(out)
            self.assertEqual(payload_cfg["thresholds"]["reset_within_hours"], 120)
            self.assertEqual(payload_cfg["thresholds"]["projected_below"], 75)
            self.assertEqual(len(payload_cfg["items"]), 1)

            # CLI override beats config (48h) -> 0 items
            rc, out = self._run_cli(
                [
                    "--usage-json",
                    str(usage_path),
                    "--now",
                    now,
                    "--config",
                    str(cfg_path),
                    "--reset-within-hours",
                    "48",
                    "--json",
                ]
            )
            self.assertEqual(rc, 0)
            payload_cli = json.loads(out)
            self.assertEqual(payload_cli["thresholds"]["reset_within_hours"], 48)
            self.assertEqual(payload_cli["thresholds"]["projected_below"], 75)
            self.assertEqual(payload_cli["items"], [])

    def test_markdown_none_case(self):
        now = auc.now_from("2026-11-26T12:25:00+00:00")
        md = auc.render_markdown([], now, auc.DEFAULT_THRESHOLDS)
        self.assertIn("# aiuse under-pace pools (Thu Nov 26, 7:25 AM ET)", md)
        self.assertIn("No paid pools are significantly under pace and resetting soon.", md)
        self.assertIn("Thresholds:", md)
        self.assertIn("72h", md)
        self.assertIn("70%", md)
        self.assertIn("30 pts", md)
        self.assertIn("24h", md)

    def test_json_shape_and_markdown_bullet(self):
        now = "2026-11-26T12:25:00+00:00"
        data = make_envelope(
            [
                make_account(
                    provider="codex",
                    account="user@example.com",
                    windows=[
                        make_window(
                            label="Codex weekly",
                            window_id="secondary",
                            used_percent=12.0,
                            resets_at="2026-11-28T12:25:00+00:00",
                            window_minutes=10080,
                        )
                    ],
                )
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            usage_path = Path(tmp) / "usage.json"
            usage_path.write_text(json.dumps(data), encoding="utf-8")
            missing_cfg = Path(tmp) / "missing.toml"
            rc, out = self._run_cli(
                ["--usage-json", str(usage_path), "--now", now, "--config", str(missing_cfg), "--json"]
            )
            self.assertEqual(rc, 0)
            payload = json.loads(out)
            self.assertEqual(set(payload.keys()), {"now", "now_et", "thresholds", "items", "markdown"})
            self.assertEqual(payload["now"], "2026-11-26T12:25:00Z")
            self.assertEqual(payload["now_et"], "Thu Nov 26, 7:25 AM ET")
            self.assertIsInstance(payload["thresholds"], dict)
            self.assertEqual(len(payload["items"]), 1)

            item = payload["items"][0]
            for required_key in (
                "key",
                "provider",
                "account",
                "label",
                "used_percent",
                "elapsed_pct",
                "projected_pct",
                "under_points",
                "resets_at",
                "resets_et",
                "hours_left",
                "suggestion",
            ):
                self.assertIn(required_key, item)

            md = payload["markdown"]
            self.assertIn("# aiuse under-pace pools (Thu Nov 26, 7:25 AM ET)", md)
            self.assertIn(
                "- **codex user@example.com — Codex weekly**: 12.0% used, 71.4% of window elapsed, "
                "resets Sat Nov 28, 7:25 AM ET (48.0h). "
                "88% is still unused; route bulk work to codex Codex weekly before it resets.",
                md,
            )


class GatedAndHintTests(unittest.TestCase):
    NOW = datetime(2026, 11, 26, 12, 25, tzinfo=timezone.utc)

    def _data(self, **acct_extra):
        win = make_window(label="Codex weekly", used_percent=10.0)
        win["models_hint"] = "codex models draw this pool"
        acct = make_account(windows=[win])
        acct.update(acct_extra)
        return {"snapshot": {"accounts": [acct]}}

    def test_operator_gated_account_skipped(self):
        self.assertEqual(auc.evaluate(self._data(requires_operator_approval=True), self.NOW, {}), [])

    def test_models_hint_in_suggestion(self):
        items = auc.evaluate(self._data(), self.NOW, {})
        self.assertEqual(len(items), 1)
        self.assertIn("(codex models draw this pool)", items[0]["suggestion"])
        self.assertEqual(items[0]["models_hint"], "codex models draw this pool")


class CodingGatherIntegrationTests(unittest.TestCase):
    def test_gather_renders_underpace_section(self):
        gpath = ROOT / "bin" / "coding-gather"
        ld = importlib.machinery.SourceFileLoader("coding_gather_t", str(gpath))
        sp = importlib.util.spec_from_loader("coding_gather_t", ld)
        cg = importlib.util.module_from_spec(sp)
        ld.exec_module(cg)
        now = datetime(2026, 11, 26, 12, 25, tzinfo=timezone.utc)
        data = {"snapshot": {"accounts": [make_account(windows=[make_window(label="Codex weekly", used_percent=12.0)])]}}
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "aiuse.json"
            f.write_text(json.dumps(data))
            up = cg.underpace_payload(now, str(f), live=False)
        self.assertIsNone(up["error"])
        self.assertEqual(len(up["items"]), 1)
        md = cg.render(now, {"markdown": ""}, None, [], None, up)
        self.assertIn("## Paid pools under pace, resetting soon (aiuse)", md)
        self.assertIn("12.0% used", md)
        skipped = cg.underpace_payload(now, None, live=False)
        self.assertIn("skipped", skipped["error"])


if __name__ == "__main__":
    unittest.main()
