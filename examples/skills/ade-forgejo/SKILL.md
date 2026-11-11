---
name: forgejo
description: >-
  Use a Forgejo instance (forgejo.mit.edu by default) from an agent: issues,
  pull requests, repos, any /api/v1 endpoint, with the token held in
  sudo-secretspec. Use when a task mentions Forgejo, forgejo.mit.edu, Codeberg,
  `fj`, `tea`, or needs a Forgejo/Gitea API token set up or rotated. Deploying
  images to SIPB Hyades (*.sipb.cloud) is the hyades-docker skill.
---

> **Managed copy, do not edit here.** Rendered by local-ade `bin/skill-everywhere` from
> [`skills/forgejo`](https://github.com/<USER>/local-ade/tree/master/skills/forgejo); the next sync
> overwrites local edits. Written for the agent TUIs on the operator's Mac: `~/src/<repo>` is
> `/home/box/src/<repo>` here when cloned, and Mac-only tools (launchd, herdr, Orca, Keychain,
> `hermes-ping`) are absent. Grok Bot's own rules win: sending, posting, pushing or merging still
> needs the operator's explicit approval.
>
> Source repo: forgejo.mit.edu/<USER>/sipb-agent-skills. On this box use $FORGEJO_SCRIPTS (forgejo-box) with FORGEJO_MIT_TOKEN from env; no sudo-secretspec, skip Token setup.

# Forgejo

Canonical source: `skills/forgejo/` in `~/src/sipb-agent-skills` (https://forgejo.mit.edu/<USER>/sipb-agent-skills) (written 2026-10-13).
Default host `forgejo.mit.edu` (Forgejo 16.0.5, Gitea-1.22-compatible API, probed
2026-10-13). API docs for any instance: `https://HOST/api/swagger`.

## Tools (installed globally, 2026-10-13)

The `scripts/` are **not on PATH**: call them by full path,
`~/src/ade/skills/forgejo/scripts/NAME` (2026-10-14: a bare `git-forgejo push`
failed with "command not found").

1. `scripts/forgejo-api` — stdlib Python, one call to `/api/v1`. Re-runs itself
   under `sudo-secretspec run`; mutations need `--yes`, `--dry-run` prints the plan.
   `forgejo-api whoami`, `forgejo-api GET /repos/OWNER/REPO/issues`. **Never run it (or `tea-forgejo`) with `--help`**:
   it is a live-service CLI. Usage: `forgejo-api [--host H] METHOD /PATH [--data '{json}'|@file.json] [--yes] [--dry-run]`;
   a comment is `POST /repos/O/R/issues/N/comments --data @body.json --yes`, with `body.json` made by
   `jq -Rs '{body: .}' < text.md > body.json` and checked with `[ -s body.json ]` first (2026-10-14: a `--help` on a POST
   was run by mistake; it posted nothing, this line is the fix). `--output FILE` saves the raw bytes (binary-safe), and PATH
   may be a full `https://HOST/...` URL on the same host, so `forgejo-api --output app.apk GET <browser_download_url>`
   downloads a private release asset; a redirect to another host never gets the token (2026-10-14).
   `--data @-` reads the body from stdin, for bodies holding a secret (an Actions secret:
   `printf %s "$V" | jq -Rs '{data: .}' | forgejo-api PUT /repos/O/R/actions/secrets/NAME --data @- --yes`,
   or `/user/actions/secrets/NAME` for every repo you own); `FORGEJO_TOKEN_VAR=OTHER_VAR` uses a
   different stored token (2026-10-14).
2. `scripts/tea-forgejo` — `tea` (brew, v0.16.0) with the secret mapped onto its
   env login (`GITEA_INSTANCE_URL` + `GITEA_TOKEN`), so nothing lands in
   `~/.config/tea`. Best for `issues`, `pulls`, `releases`, `api`.
   **The agent hosts differ (2026-10-18):** on `get` and `vps-primary` (the same user on both) there is no
   `sudo-secretspec`, so `tea` is logged in directly: this same PAT, piped from the Mac over ssh stdin, sits in
   `~/.config/forgejo/token` (0600) and in tea's own `~/.config/tea/config.yml` (0600), `tea login default
   forgejo.mit.edu` is set, and `tea login helper` is git's HTTPS credential helper for the host. `tea login add
   --oauth` is not used there: on `get` it fails (static Go binary, port-0 listener under libkrun TSI; `forgejo-ci`
   skill, section 3), and OAuth tokens expire. Run tea as the user from `cd ~`. Details: `docs/agents-vps-primary.md` in site-config.
2a. `scripts/git-forgejo` — git over HTTPS with the secret:
   `~/src/ade/skills/forgejo/scripts/git-forgejo -C DIR push REMOTE BRANCH`, where REMOTE is whichever
   remote points at the Forgejo host: check `git -C DIR remote -v` first (`forgejo` in some repos, `origin` in others,
   e.g. `~/src/mit`; 2026-10-17 a guessed `forgejo` failed)
   (full path every time: the bare name failed twice on 2026-10-14). The token reaches git only through a `GIT_ASKPASS`
   helper (`git-askpass`, holds no secret) and `credential.helper=` is cleared so
   the keychain never stores it. Keep remote URLs token-free
   (`https://HOST/OWNER/REPO.git`). Plain `git push` will prompt and fail by design.
   **A big first push is slow, not refused** (2026-10-18): from a home link the upload runs at about 0.5-1.5 MB/s,
   so a 500 MiB history takes 10-20 min (523 MiB took 17 min 54 s). Start it in the background with no `timeout`
   under about 45 min, and never raise `http.postBuffer`: git then packs into memory first, so "Writing objects"
   reaches ~90% before a byte is sent and the wait looks like a server stall. A `timeout 600` killed one such push
   and the agent reported that Forgejo could not take the full history. A shallow push is refused ("shallow update
   not allowed"); push the full history once, then every later push is incremental.
   git-forgejo skips an inherited `HTTPS_PROXY` for the Forgejo host (it cost ~40% of upload speed);
   `GIT_FORGEJO_KEEP_PROXY=1` keeps it, for a network that needs one.
3. `fj` (brew `forgejo-cli`, Forgejo-native) — fine **unauthenticated** (public
   repos). **Do not `fj auth add-token` / `fj auth login` for agents:** it keeps
   the token in its own plaintext key file, outside sudo-secretspec.
4. `pyforgejo` (PyPI, typed client) — a library, so install it on demand:
   `uv run --with pyforgejo python script.py` under `sudo-secretspec run`.
   Not installed globally: pip-style global installs into the brew Python would
   break its externally-managed marker, and uv caches it after the first use.

## Token setup

Run `scripts/setup-token [HOST]`. It declares `FORGEJO_MIT_EDU_TOKEN`
(optional, so `check` stays green on hosts without it), opens the token page in
the default browser, lets `sudo-secretspec set` take the pasted value, then
verifies it with `GET /user` and prints only the login. The name is
`FORGEJO_<HOST upper-cased, . and - as _>_TOKEN`. `setup-token HOST PURPOSE` makes a
second, narrower token `FORGEJO_<HOST>_<PURPOSE>_TOKEN`: `setup-token forgejo.mit.edu
PACKAGE` is the `write:package`-only token for container pushes and Hyades CI
(`hyades-docker` skill), verified with `GET /packages/<USER>`.

The declaration is a runtime `add`. On this machine `sudo-secretspec
template-check` says "no tracked declaration source configured" (2026-10-13), so
there is nothing to mirror it into and no drift is reported. If a tracked source
is ever configured, mirror the declaration there, or remove it with `delete` then
`undeclare`.

## Token or OAuth2?

**Personal access token (chosen).** Static, scoped, revocable, no expiry unless
set: exactly what `sudo-secretspec` stores. OAuth2 exists on the instance
(authorization code + PKCE, refresh tokens; `tea login add --oauth` and
`fj auth login` use it) but its access tokens expire and its refresh tokens
rotate, which a static secret store cannot follow, and both CLIs would keep the
result in plaintext config. Use OAuth2 only for a human at a keyboard, never for
agents.

## forgejo.mit.edu CI

**Scope (operator, 2026-10-18):** Hyades/Forgejo CI is for MIT/SIPB work. CI for a GitHub-sourced repo (Plektra, hermes-agent,
herdr, secretspec-sqlite, sudo-secretspec, the stayturgid agent) now runs on free GitHub runners via `ci/base` + `ci/run/**` +
a `scripts/github-*` wrapper (`forgejo-ci` skill, section 6 item 6); their private Forgejo copies are retired.

Moved to the **`forgejo-ci` skill** (2026-10-14): runner facts, job-image selection (our own `ghcr.io/<USER>/ci-image-plektra`),
the loopback `LD_PRELOAD` shim for libkrun TSI bugs (Gradle daemon, okhttp MockWebServer, WebSocket tests), probe tooling,
ticket etiquette and the SIPB/libkrun ticket map. The evidence behind it, unchanged, is in
`docs/runner-notes.md` in the new public repo **https://forgejo.mit.edu/<USER>/loopback-port-shim** (local `~/src/loopback-port-shim`), which
is also where the shim, probes, probe workflows and `ci/tools/` scripts now live (`skills/forgejo/examples/plektra/` is only a pointer). Compare runs with
`scripts/ci-step-times forgejo OWNER/REPO RUN` and `... github OWNER/REPO RUN`.

## Filing issues on SIPB projects: fork only; you file upstream (operator, 2026-10-22)

SIPB prefers short, human-readable issues. **AI output never goes on `SIPB/*`**
(no issue, comment, or PR from an agent). For every problem on a SIPB project on
forgejo.mit.edu:

1. Public fork `<USER>/<repo>` (created if missing, issues enabled): the **full
   AI-oriented issue** — environment, exact commands/output, commit-pinned
   file:line, hypotheses marked as such, patches, impact. Verbose is fine; only
   AIs need to read it. Title `[AI detail] <intended upstream title>`.
2. On that same fork issue, a **comment** holding the **short human draft**
   (what broke, minimal repro, one or two fix ideas; aim for under ~150 words).
   Follow-ups (e.g. a reply to someone on upstream) use the same two steps on
   the fork: AI-readable write-up, then a for-humans draft as another comment.
   **Never the clipboard** (hard to keep track of; operator, 2026-10-22) — that
   comment is what you rewrite when you post on `SIPB/*`. Agents never open,
   comment on, or PR to `SIPB/*`.
3. After you file upstream, you (or an agent updating **only the fork**) set the
   AI issue's first line to `**Upstream issue:** URL`. Upstream can link back to
   the AI twin in a footer when you write it — agents do not post that footer.
4. **Edit only within an hour, and only if nothing follows** (operator, 2026-10-17).
   A body or a comment of ours may be edited only while under an hour old and no
   comment has been posted after it; otherwise a new comment. Never delete
   comments. Applies on the fork (the only tier agents write).
5. **When done or handing off:** list the full URLs of every `<USER>/<repo>`
   issue and PR involved — that is the review surface.

`scripts/forgejo-two-tier-issue --upstream SIPB/REPO --title T --human human.md
--ai ai.md --fork-only --yes` is the **only** SIPB path: fork, AI issue, human
draft as its first comment. For `SIPB/*` the script forces fork-only and
**refuses every upstream write** (the old `--upstream-go-ahead` escape hatch is
gone). Non-SIPB upstreams may still use the legacy auto-file path. Hyades drafts:
`skills/hyades-docker/tickets/`; CI-runner drafts: `forgejo-ci` repo
`docs/tickets/two-tier/`. Fork: `<USER>/hyades` (2026-10-14).

## Container registry and Hyades deploys

forgejo.mit.edu is also SIPB's OCI registry (`forgejo.mit.edu/OWNER/NAME`, Packages
tab) and the image source for **Hyades**, SIPB's microVM hosting (`*.sipb.cloud`).
That side is the **`hyades-docker` skill** (`skills/hyades-docker/`): its
`hyades-registry push IMAGE` runs podman (preferred) or docker logged in with the
package token through a throwaway auth file (never a bare `podman login`, which saves
the token in `auth.json`), and `hyades-ci-secrets OWNER/REPO --yes` puts
`PACKAGE_TOKEN`/`UPDATE_TOKEN` into Actions secrets for the
[SIPB/hyades-action](https://forgejo.mit.edu/SIPB/hyades-action) build-push-update
step, using `forgejo-api --data @-` above. Package listing stays here:
`forgejo-api GET '/packages/OWNER?type=container'`.

## Agent loop (local agent <-> Forgejo CI, no agent on the server)

1. Branch and commit locally, `~/src/ade/skills/forgejo/scripts/git-forgejo push forgejo BRANCH`.
2. `forgejo-api POST /repos/O/R/pulls --yes --data '{"head":"BRANCH","base":"plk/main","title":"..."}'`.
3. Poll `GET /repos/O/R/actions/runs` for the run, then `.../runs/ID/jobs` and
   `.../actions/jobs/JOB/logs` for failures (Forgejo's shapes, not GitHub's: a run has `commit_sha`, no
   `head_sha`, and its outcome is in `status`; `.../jobs` is a bare array, not `{jobs: [...]}`; 2026-10-17: a
   `head_sha` filter matched nothing and a waiter polled an empty run id for 25 min); comment with
   `POST /repos/O/R/issues/N/comments --yes`.
4. Fix, push again, repeat. Cancel runs you no longer need
   (`POST .../actions/runs/ID/cancel --yes`): the runner is shared.

## Markdown rendering gotchas (verified 2026-10-30 against forgejo.mit.edu)

1. **A list marker is digits plus `.` or `)` only.** `10a.` is not a marker: after a list item it folds into that
   item's paragraph, and after a blank line it is a plain paragraph outside the list. Do not renumber to avoid it.
2. **Lettered sub-items (`6a`, `10a`): a blockquote inside the parent item.** Indent `> **10a.** text` to the
   parent's content column: 3 spaces under `6.`, 4 under `10.` (marker width plus one space). Fewer spaces land it
   outside the list, which is the common mistake. It adds no second bullet, wraps aligned and keeps the list tight.
3. **Little HTML survives.** `<ol type="a">`, `<ol start>`, `<li value>`, `<dl>`, `<details>` are kept; `style`
   attributes are stripped, and `<ol type="a">` shows `a.`, not `10a.`.
4. **Check a rendering before pushing it:** `forgejo-api POST /markdown --data '{"Text":"...","Mode":"markdown"}' --yes`
   returns the server's HTML (a render, not a mutation). It breaks on newlines, so it overstates `<br>`; judge
   nesting and stripped tags, not line wraps.

## Rules

1. Never print the token; `get`/`export` stream values (see `sudo-secretspec`).
2. Prefer least scope: `read:*` for browsing, add `write:*` only for the task.
3. Mutations (create issue/PR, merge, delete) are outward-facing on a shared MIT
   service: confirm with the operator first unless the task already asked for them.
4. Rotate by re-running `setup-token`; `set` overwrites.

## Existing skills checked (2026-10-13)

None adopted; ours is a thin layer so no third-party code receives the token.
`magnus919/agent-skills` `forgejo-cli` (MIT, Python, guarded mutations,
`FORGEJO_AGENT_TOKEN` env) is the best of them and a good reference for endpoint
coverage; an mcp.directory `forgejo` skill is a `tea` cheat sheet; printing-press
`pp-forgejo` wraps a Node CLI with a local SQLite mirror.
