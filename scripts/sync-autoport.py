#!/usr/bin/env python3
"""sync-autoport.py: port a source tree into a dest tree when the change is clean.

Two modes:

  Live-box -> this repo (default, dest is the repo that contains this script):
    For each manifest row whose live file changed since the last ack (sha256 differs from
    the baseline) it takes the change itself, old-live -> new-live (old content comes from a
    content-addressed snapshot taken at --snapshot / stage2-success time), and:
      * if any added or removed line matches a secret pattern, a public IPv4, or a personal
        deny-list term (~/.config/secret-scan/denylist.txt), it is NOT ported: needs_ai
        (reason names the rule, never the value);
      * otherwise the same change is 3-way merged into the dest copy
        (`git merge-file repo old new`); clean -> ported, conflict -> needs_ai.
    NEW / MISSING rows and drift with no snapshot are always needs_ai.

  Repo -> repo (`--source-repo` + `--dest-repo`), used by `just stage2` to port the private
  exact-copy repo into the public scrubbed repo. Baseline keys are repo-relative paths
  (default ~/.local/state/bot-briefs-sync/public-baseline.tsv). Only paths that already
  exist in dest are auto-ported. A brand-new source path (not in the previous baseline,
  not in dest) is needs_ai; already-seen source-only paths stay private and are not
  re-flagged every run.

  scripts/sync-autoport.py               dry run: JSON report, writes nothing
  scripts/sync-autoport.py --apply       write ported files into dest (never commits)
  scripts/sync-autoport.py --snapshot    store snapshots of the current source files
  scripts/sync-autoport.py --source-repo PRIV --dest-repo PUB [--apply]
Exit: 0 nothing to do, 2 something ported and nothing needs AI, 3 needs_ai items present.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "config/manifest.tsv"
BASE = Path(os.environ.get("SYNC_BASELINE", Path.home() / ".local/state/bot-briefs-sync/baseline.tsv"))
PUBLIC_BASE = Path(os.environ.get("SYNC_PUBLIC_BASELINE", Path.home() / ".local/state/bot-briefs-sync/public-baseline.tsv"))
SNAP = Path(os.environ.get("SYNC_SNAPSHOTS", Path.home() / ".local/state/bot-briefs-sync/snapshots"))
DENY = Path(os.environ.get("SECRET_SCAN_DENYLIST", Path.home() / ".config/secret-scan/denylist.txt"))
SKIP_DIR = {".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"}

SECRET_RES = {
    "private-key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "aws-key": r"AKIA[0-9A-Z]{16}",
    "github-token": r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}",
    "slack-token": r"xox[abprs]-[A-Za-z0-9-]{10,}",
    "webhook": r"hooks\.slack\.com/services/|discord(app)?\.com/api/webhooks/",
    "api-key": r"sk-(ant-|proj-)?[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35}|ya29\.[0-9A-Za-z_-]{30,}",
    "telegram-bot-token": r"[0-9]{8,10}:AA[0-9A-Za-z_-]{30,}",
    "tailscale-key": r"tskey-[A-Za-z0-9-]{10,}",
    "bearer-literal": r"[Bb]earer\s+[A-Za-z0-9._~+/=-]{24,}",
    "jwt": r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    "tailnet-name": r"\b[a-z0-9-]+\.ts\.net\b",
    "email": r"\b[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com|example\.(com|org))[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
}
IPV4 = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")


def public_ipv4(line: str) -> bool:
    for m in IPV4.finditer(line):
        o = [int(x) for x in m.groups()]
        if any(x > 255 for x in o):
            continue
        a, b = o[0], o[1]
        if a in (0, 10, 127) or a >= 224 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31) \
                or (a == 100 and 64 <= b <= 127) or (a == 169 and b == 254) or (a == 198 and b in (18, 19)):
            continue
        return True
    return False


def load_deny(path: Path = DENY) -> list[re.Pattern]:
    out = []
    if path.exists():
        for ln in path.read_text().splitlines():
            t = ln.split("#", 1)[0].strip()
            if t:
                try:
                    out.append(re.compile(t, re.I))
                except re.error:
                    out.append(re.compile(re.escape(t), re.I))
    return out


def changed_lines(old: str, new: str) -> list[str]:
    import difflib
    return [l[1:] for l in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
            if l[:1] in "+-" and not l.startswith(("+++", "---"))]


def classify(old: str, new: str, deny: list[re.Pattern]) -> list[str]:
    """Rule names (never values) that the change old->new trips."""
    hits = set()
    for ln in changed_lines(old, new):
        for name, rx in SECRET_RES.items():
            if re.search(rx, ln):
                hits.add(name)
        if public_ipv4(ln):
            hits.add("public-ipv4")
        for i, d in enumerate(deny, 1):
            if d.search(ln):
                hits.add(f"denylist-term-{i}")
    return sorted(hits)


def merge3(repo_text: str, old: str, new: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as td:
        a, o, b = (Path(td) / n for n in ("repo", "old", "new"))
        a.write_text(repo_text)
        o.write_text(old)
        b.write_text(new)
        r = subprocess.run(["git", "merge-file", "-p", "-q", str(a), str(o), str(b)], capture_output=True, text=True)
        return r.returncode == 0, r.stdout


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rows(manifest: Path | None = None) -> list[tuple[str, str]]:
    man = manifest or MANIFEST
    out = []
    if not man.exists():
        return out
    for ln in man.read_text().splitlines():
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split("\t")
        if len(parts) >= 3 and not parts[1].startswith("/") and ".." not in parts[1] and parts[2].startswith("/"):
            out.append((parts[1], parts[2]))
    return out


def redact_text(s: str, deny: list[re.Pattern]) -> str:
    """Replace secret / deny-list matches with a rule name, never leave the value."""
    out = s
    for name, rx in SECRET_RES.items():
        out = re.sub(rx, f"<REDACTED:{name}>", out)
    def _ip(m):
        return "<REDACTED:public-ipv4>" if public_ipv4(m.group(0)) else m.group(0)
    out = IPV4.sub(_ip, out)
    for i, d in enumerate(deny, 1):
        out = d.sub(f"<REDACTED:denylist-term-{i}>", out)
    return out


def write_redacted_diff(diff_dir: Path, rel: str, old: str, new: str, deny: list[re.Pattern]) -> str:
    import difflib
    diff_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(diff_dir, 0o700)
    safe = rel.replace("/", "__")
    dest = diff_dir / f"{safe}.diff"
    raw = "".join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True),
        fromfile=f"a/{rel}", tofile=f"b/{rel}"))
    dest.write_text(redact_text(raw, deny))
    os.chmod(dest, 0o600)
    return str(dest)


def load_baseline(path: Path) -> dict[str, str]:
    base = {}
    if path.exists():
        for ln in path.read_text().splitlines():
            p, _, h = ln.partition("\t")
            if p:
                base[p] = h
    return base


def write_baseline(path: Path, mapping: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(f"{p}\t{h}\n" for p, h in sorted(mapping.items())))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def snapshot_file(p: Path, snap: Path = SNAP) -> str:
    snap.mkdir(parents=True, exist_ok=True)
    os.chmod(snap, 0o700)
    h = sha(p)
    t = snap / h
    if not t.exists():
        t.write_bytes(p.read_bytes())
        os.chmod(t, 0o600)
    return h


def rel_files(root: Path) -> set[str]:
    out = set()
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(root)
        if any(part in SKIP_DIR for part in rel.parts):
            continue
        out.add(rel.as_posix())
    return out


def snapshot_sources(items: list[Path], snap: Path = SNAP) -> int:
    n = 0
    snap.mkdir(parents=True, exist_ok=True)
    os.chmod(snap, 0o700)
    for p in items:
        if p.is_file() and not p.is_symlink():
            t = snap / sha(p)
            if not t.exists():
                t.write_bytes(p.read_bytes())
                os.chmod(t, 0o600)
                n += 1
    return n


def _consider(rp: str, src: Path, dest: Path, base: dict[str, str], deny: list[re.Pattern],
              snap: Path, apply: bool, diff_dir: Path | None, rep: dict) -> None:
    if not src.exists():
        if "example" in rp:
            rep["unchanged_drift"].append(rp)
        else:
            rep["needs_ai"].append({"path": rp, "reason": "MISSING-SOURCE"})
        return
    if not dest.exists():
        # Brand-new path not previously acked -> needs a human decision to add to dest.
        # Already-seen source-only paths stay private and are not re-flagged.
        if rp not in base:
            item = {"path": rp, "reason": "NEW (not in dest; decide include or keep private)"}
            if diff_dir is not None:
                try:
                    item["redacted_diff"] = write_redacted_diff(diff_dir, rp, "", src.read_text(), deny)
                except UnicodeDecodeError:
                    item["reason"] = "NEW (binary, not in dest)"
            rep["needs_ai"].append(item)
        return
    h = sha(src)
    if h == base.get(rp) or h == base.get(str(src)):
        if src.read_bytes() != dest.read_bytes():
            rep["unchanged_drift"].append(rp)  # acknowledged scrub (or exact) difference
        return
    if src.read_bytes() == dest.read_bytes():
        return
    old_p = snap / base.get(rp, base.get(str(src), "-"))
    if not old_p.exists():
        item = {"path": rp, "reason": "no snapshot of the acked source version; port by hand once, then ack"}
        if diff_dir is not None:
            try:
                item["redacted_diff"] = write_redacted_diff(diff_dir, rp, "", src.read_text(), deny)
            except UnicodeDecodeError:
                pass
        rep["needs_ai"].append(item)
        return
    try:
        old, new, cur = old_p.read_text(), src.read_text(), dest.read_text()
    except UnicodeDecodeError:
        rep["needs_ai"].append({"path": rp, "reason": "binary"})
        return
    hits = classify(old, new, deny)
    if hits:
        item = {"path": rp, "reason": "change touches: " + ", ".join(hits)}
        if diff_dir is not None:
            item["redacted_diff"] = write_redacted_diff(diff_dir, rp, old, new, deny)
        rep["needs_ai"].append(item)
        return
    ok, merged = merge3(cur, old, new)
    if not ok:
        item = {"path": rp, "reason": "3-way merge conflict with the scrubbed copy"}
        if diff_dir is not None:
            item["redacted_diff"] = write_redacted_diff(diff_dir, rp, old, new, deny)
        rep["needs_ai"].append(item)
        return
    rec = {"path": rp, "lines": len(changed_lines(old, new))}
    rep["ported"].append(rec)
    if apply:
        dest.write_text(merged)


def port_live_to_repo(apply: bool, deny: list[re.Pattern], base: dict[str, str],
                      snap: Path, diff_dir: Path | None, root: Path, manifest: Path) -> dict:
    rep = {"ported": [], "needs_ai": [], "unchanged_drift": []}
    for rp, dest in rows(manifest):
        _consider(rp, Path(dest), root / rp, base, deny, snap, apply, diff_dir, rep)
    rep["applied"] = apply
    return rep


def port_repo_to_repo(source: Path, dest: Path, apply: bool, deny: list[re.Pattern],
                      base: dict[str, str], snap: Path, diff_dir: Path | None) -> dict:
    """Port source repo files into dest for paths that already exist in dest.

    Source-only paths already recorded in the baseline are treated as private-only.
    Source-only paths not in the baseline are needs_ai (NEW).
    """
    rep = {"ported": [], "needs_ai": [], "unchanged_drift": []}
    dest_files = rel_files(dest)
    src_files = rel_files(source)
    src_manifest = {rp for rp, _ in rows(source / "config/manifest.tsv")}
    # Auto-port intersection; flag brand-new source/manifest paths not in dest.
    for rp in sorted(dest_files & src_files):
        _consider(rp, source / rp, dest / rp, base, deny, snap, apply, diff_dir, rep)
    for rp in sorted((src_manifest | src_files) - dest_files):
        _consider(rp, source / rp, dest / rp, base, deny, snap, apply, diff_dir, rep)
    rep["applied"] = apply
    return rep


def exit_for(rep: dict) -> int:
    if rep["needs_ai"]:
        return 3
    return 2 if rep["ported"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--source-repo", type=Path)
    ap.add_argument("--dest-repo", type=Path)
    ap.add_argument("--redacted-diff-dir", type=Path)
    ap.add_argument("--baseline", type=Path, help="override baseline.tsv path")
    a = ap.parse_args(argv)
    source = a.source_repo.resolve() if a.source_repo else None
    dest = a.dest_repo.resolve() if a.dest_repo else None
    if (source is None) ^ (dest is None):
        print("both --source-repo and --dest-repo are required together", file=sys.stderr)
        return 2
    snap = SNAP
    if a.snapshot:
        if source:
            n = snapshot_sources([source / rel for rel in rel_files(source)], snap)
        else:
            n = snapshot_sources([Path(d) for _rp, d in rows()], snap)
        print(f"snapshots: {n} new in {snap}")
        return 0
    deny = load_deny()
    if source:
        base_path = a.baseline or PUBLIC_BASE
        base = load_baseline(base_path)
        rep = port_repo_to_repo(source, dest, a.apply, deny, base, snap, a.redacted_diff_dir)
    else:
        base_path = a.baseline or BASE
        base = load_baseline(base_path)
        rep = port_live_to_repo(a.apply, deny, base, snap, a.redacted_diff_dir, ROOT, MANIFEST)
    print(json.dumps(rep, indent=2))
    return exit_for(rep)


if __name__ == "__main__":
    sys.exit(main())
