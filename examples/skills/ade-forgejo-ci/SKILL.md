---
name: forgejo-ci
description: >-
  Run CI jobs on SIPB's Hyades runner (forgejo.mit.edu, label `ubuntu`, libkrun
  microVMs): choose the job image, use the loopback LD_PRELOAD shim for libkrun
  TSI bugs (Gradle daemon, okhttp MockWebServer, WebSocket tests hang or flake),
  run probe workflows, read logs, and file SIPB/libkrun tickets properly. Use
  when a job on forgejo.mit.edu hangs, flakes, is slow, or needs Java/Android,
  or when a task mentions Hyades, `runs-on: ubuntu`, libkrun, TSI or the shim.
  Also the recipe for a GitHub-sourced repo's CI on free GitHub runners (section 6 item 6).
---

> **Managed copy, do not edit here.** Rendered by local-ade `bin/skill-everywhere` from
> [`skills/forgejo-ci`](https://github.com/<USER>/local-ade/tree/master/skills/forgejo-ci); the next sync
> overwrites local edits. Written for the agent TUIs on the operator's Mac: `~/src/<repo>` is
> `/home/box/src/<repo>` here when cloned, and Mac-only tools (launchd, herdr, Orca, Keychain,
> `hermes-ping`) are absent. Grok Bot's own rules win: sending, posting, pushing or merging still
> needs the operator's explicit approval.
>
> Source repo: forgejo.mit.edu/<USER>/loopback-port-shim (box clone /home/box/src/loopback-port-shim).

# Forgejo CI on SIPB Hyades

**Where CI runs (operator, 2026-10-18):** CI for a GitHub-sourced repo (Plektra, hermes-agent, herdr, secretspec-sqlite,
sudo-secretspec, the stayturgid agent) runs on **free GitHub runners** via an orphan `ci/base` branch (workflow + wrapper),
`ci/run/**` pushes and a `scripts/github-*` wrapper (item 6 below); the private Forgejo copies of those six repos were
deleted on 2026-10-18 (Forgejo-only branches kept on GitHub as `ci/archive/*`). Hyades/Forgejo CI stays for MIT/SIPB work. Everything below about libkrun, the shim and Hyades images is still true
for Hyades; it just no longer applies to the six repos above.

Written 2026-10-14 from runs 168-240 on the private test repo `<USER>/plektra-<USER>`.
**Canonical home: https://forgejo.mit.edu/<USER>/loopback-port-shim** (local `~/src/loopback-port-shim`; this skill is
`skills/forgejo-ci/` there, reached from `~/src/ade/skills/forgejo-ci` by symlink). Paths below are relative to that
repo. Token, API and git helpers are the `forgejo` skill in local-ade (`forgejo-api`, `git-forgejo`; **never `--help` on
them**; the scripts in `ci/tools/` find them through `FORGEJO_SCRIPTS`, default `~/src/ade/skills/forgejo/scripts`).
Long-form evidence with run numbers: `docs/runner-notes.md` and the other files in `docs/`.
Deploying images to Hyades itself (the SSH console, `hyades.json`, `*.sipb.cloud`, SIPB/hyades-action, update tokens) is
the `hyades-docker` skill in local-ade.

## 1. Runner facts

1. `runs-on: ubuntu` (also `alpine`, `rocky`), never `ubuntu-latest`; workflows in `.forgejo/workflows/`; actions need a full
   URL (`uses: https://github.com/actions/checkout@v4`); `upload-artifact@v4` is unsupported.
2. **One libkrun microVM per job** (rootless podman, krun; "2.0-sipb", guest kernel 6.12.91), `capacity = 4` jobs at once on a
   shared host that has **one loopback port space** for TSI sockets. Per job: 16 vCPU (EPYC 3.0 GHz), 62.8 GiB RAM, root,
   disk is virtiofs (slow for small files; `/dev/shm` tmpfs is 29-33x faster but `noexec`). DAX cannot work: sipbphus is
   btrfs RAID1 (@xy, SIPB/hyades#2). Do not pursue virtiofs writeback or more workers: @xy tried writeback and CI got
   slower, and libkrun's slp (libkrun#924) says writeback loses data unless every virtio-fs device is quiesced before
   shutdown, and extra workers barely helped. Use the loop image of item 4a.
   @xy (2026-10-16) pointed at libkrun#933, slp's experimental synchronous virtio-fs queues: it also needs a
   guest kernel half in libkrunfw, and slp measured about 1.3-1.6x per operation (M3 Pro, not KVM) against
   our 17x create gap, so the loop image stays; we offered to rerun the benchmark if it reaches a test runner
   (SIPB/hyades#2 comment 107).
   Download speed is fine (79-102 MB/s).
3. Label images are `forgejo.mit.edu/sipb/ci-images:<label>` (Dockerfiles in `SIPB/ci-images`: ubuntu 26.04, alpine 3.24,
   rocky 10): git, curl, python3, node, podman, go; **no gcc, JDK or Android SDK**. Images are never re-pulled, so pin a
   job image by digest. A job-level `container: image:` replaces the label's image (verified, run 239).
4. Node actions (`actions/checkout`) need `node` in the job image (node 18 is enough: run 240).
4a. **Disk: put the workspace and Gradle home on an ext4 loop image** (`truncate -s 40G`, `mkfs.ext4 -E
   lazy_itable_init=1,lazy_journal_init=1`, `mount -o loop,noatime`, copy the checkout, `mount --bind` it over
   `$GITHUB_WORKSPACE`, `GRADLE_USER_HOME` into `$GITHUB_ENV`). Fastest in the 2026-10-14 bake-off: 324 s loop vs 393 s
   guest tmpfs vs 458 s as-is (`docs/comparison.md`). 2026-10-16 bounded pair on our image (run 150, same commit,
   sequential jobs, real `mount -t tmpfs` not `/dev/shm`/`--tmpfs`): loop 270 s vs guest tmpfs 274 s (within noise);
   keep the loop so the job does not pin a multi-GiB tmpfs. Production (Plektra's `.forgejo/scripts/fast-disk.sh`):
   Gradle 7 m 20 s with the loop vs 9 m 18 s as-is (runs 246 and 243). Not `/dev/shm` (`noexec`: aapt2 fails) and not
   `podman --tmpfs` (host-side, the same virtiofs; crun#1952). **Keep the loop image for Android/Gradle.** A guest
   tmpfs (`mount -t tmpfs -o size=… tmpfs /mnt/fast`) is fine for smaller exec-capable working sets (hyades-action
   does this for container storage; CI RAM limit 64 GB). Do not switch Plektra to tmpfs because a maintainer named it.
   slp's CI layout (libkrun#924): virtiofs for sources and the final artifact, tmpfs or virtio-blk for transient data;
   crun has no scratch-disk annotation yet, and the loop image is the job-side stand-in. Do not file another disk
   ticket. **After the bind mount, every later run step must start with `cd "$PWD"`:** its shell starts in the
   directory hidden by the mount (run 246: cwd on device 26, mount on 1792). Java re-resolves the path by name and
   sees the mount, but a relative `open()` from bash or Python does not: in run 245 the release script could not find
   the APK Gradle had just written.
4b. **A step's `working-directory:` is ignored:** every run step starts in the job's initial cwd (probe run 249,
   `ci/probes/probe-cwd.yml`; Apple container on the Mac, an ordinary Linux runtime, honours both). `cd` to an absolute
   path in the script instead (`cd "$GITHUB_WORKSPACE/sub"`). Cause: SIPB's crun `libkrun_exec_ssh` (`krun.c`) builds an
   ssh argv from the args and env only, never `process->cwd`, and libkrun's guest `init/src/ssh.rs` spawns with no cwd.
   Plektra's dispatch workflow built `plk/main` instead of the requested ref until this was fixed. Reported: SIPB/hyades#4.
4c. **Maven Central rate-limits the shared Hyades address** (2026-10-16: HTTP 429 on every Plektra run for hours after
   a burst of a dozen-plus parallel jobs; Sonatype's consumption limit, https://central.sonatype.org/faq/429-error/). Fix: a Gradle init script
   that rewrites the Central URLs in `settings.buildscript`, `pluginManagement` and `dependencyResolutionManagement` to
   Google's mirror `https://maven-central.storage-download.googleapis.com/maven2/`, and for Robolectric, which downloads
   android-all jars outside Gradle, sets test system properties `robolectric.dependency.repo.url` (the mirror) and
   `robolectric.dependency.repo.id`; without them 1,071 of 1,434 tests failed in `MavenArtifactFetcher` (run 139).
   Plektra's copy: `.forgejo/central-mirror.gradle` on `plk/base` (retired with its Hyades CI; GitHub runners use `.github/ci/central-mirror.gradle` on `ci/base`). Keep to about 4 jobs at once (the runner's capacity).
4d. **The runner can fail to copy every `uses:` action into the job** (2026-10-20, <USER>/jev-like-hyades runs 1-3):
   `failed to copy directory ".../.cache/act/<hash>/" to container: ... statat .../.eslintignore (or .git): path escapes
   from parent`, then `Cannot find module .../dist/index.js`. It hit `actions/checkout` and SIPB/hyades-action alike, and a
   re-run did not help. Workaround, no `uses:` at all: clone with `git init . && git fetch --depth 1
   "$server_url/$repository.git" "$sha" && git checkout FETCH_HEAD` (public repo), and inline hyades-action's steps:
   tmpfs on `/var/lib/containers/storage`, `buildah build --format docker --network=host`, `buildah login
   --password-stdin` + `buildah push`, then the `curl -X POST -H "X-Token: …"` update (jev-like-hyades
   `.forgejo/workflows/main.yml`). `--format docker` matters: buildah's default OCI format ignores a Containerfile `SHELL`.
   Also <USER>/get runs 624-625 (fixed in get `e89f9a1`). Cause: a podman regression in `podman cp` (upstream
   [forgejo/runner#1770](https://code.forgejo.org/forgejo/runner/issues/1770) →
   [podman#29805](https://github.com/podman-container-tools/podman/issues/29805), fix in progress
   [buildah#7129](https://github.com/podman-container-tools/buildah/pull/7129); downgrading podman is the stopgap).
   Likely arrived with the Hyades host's Monday update. Drop the workaround once the runner's podman carries the fix.
4e. **Parse a workflow before pushing it**: `python3 -c "import yaml; yaml.safe_load(open(F))"`. A plain scalar holding
   `": "` (`run: curl -H "X-Token: $T"`) is a YAML mapping error, and Forgejo then shows a failed run with the commit
   message as the job name, empty `runs_on` and no log ("job has not been executed yet"); use `run: |`.
5. Job logs: only the first `⭐ Run` marker survives; print your own `BENCHSTEP` markers (`scripts/ci-step-times`).

## 2. Choosing the job image

| Image | Has | Lacks | Use |
|---|---|---|---|
| stock `ubuntu` label | git, node, python3, podman | gcc, JDK, SDK | scripts and non-Java jobs |
| **`ghcr.io/<USER>/ci-image-plektra@sha256:<digest>`** (ours, public, amd64; canonical source `forgejo.mit.edu/<USER>/ci-image-plektra`, mirrored to GitHub for builds) | android-sdk 36, JDK 17, node 18, gcc, git, python3, **`/usr/local/lib/portshim.so`** | nothing Plektra needs | Gradle/Robolectric/Android jobs: no install, no compile |
| `ghcr.io/cirruslabs/android-sdk:36` | SDK 36, JDK 21, gcc | node, JDK 17 | base of ours; 47 s first pull, cached after |
| `eclipse-temurin:17-jdk-noble` | JDK 17 | gcc, git, SDK, node | saves nothing |
| `mingc/android-build-box` | everything | (5.9 GB) | too big |

Our image's digest is printed by its GitHub Actions build-mirror run (`gh run list -R <USER>/ci-image-plektra`; the digest is in the
"Verify the pushed image" step). To change the image, edit its Dockerfile and push; update the digest in the workflow. The
shim source is pinned there by commit (`SHIM_SHA`): bump it when `loopback-port-shim.c` changes.

## 3. The shim (libkrun TSI workarounds)

Source `loopback-port-shim.c` at the repo root (267 lines, MIT; `make`, `make check`, probes in `tests/`; SIPB/hyades#3 points
at the repo). **Five problems (the fourth is not fixable in a shim), one `LD_PRELOAD` library:**

1. A listener bound to **port 0 is unreachable** (libkrun#818). The shim rewrites `bind(port 0)` to a random port in
   20000-32000 (nanosecond seed, mutex, ring of the last 4096 ports).
2. **A thread blocked in `recv()`/`read()`/`accept()` holds the socket lock**, so `send()`, `shutdown()` and `getpeername()`
   from other threads block (libkrun#819, #926). The shim waits in `poll()` (which holds no lock) before blocking reads and
   runs `shutdown()` in a helper thread with a 300 ms wait (no-op on listeners).
3. **A listener closed during a blocked `accept()` is not released**: a new listener on that port loses connections, stale
   ones drain in about 60 s (libkrun#929). Cure: never reuse a port soon (the ring).
4. TSI host sockets always have `SO_REUSEPORT`, so a duplicate `listen()` succeeds (libkrun#860); avoiding port reuse (3) hides it.
5. `getsockopt()` below `SOL_SOCKET` on a UDP socket that has talked to a remote host oopses the guest kernel (libkrunfw#140,
   SIPB/hyades#9); the shim guards it and returns `EOPNOTSUPP`.

Also: `SO_RCVTIMEO` is not honoured under TSI (use `poll`); `getsockopt(SO_ACCEPTCONN)` says 0 for a listener.

**Use it opt-in.** In a job on our image: `echo /usr/local/lib/portshim.so >> /etc/ld.so.preload` (root; reaches Gradle's
daemon and test workers) or `LD_PRELOAD=/usr/local/lib/portshim.so` for one process. **An `LD_PRELOAD` written to
`$GITHUB_ENV` does not reach Gradle's daemon.** On a stock label: `apt-get install gcc libc6-dev` (about 30 s), fetch the
source, `gcc -shared -fPIC -O2 -pthread -o /usr/local/lib/portshim.so shim.c -ldl` (works on glibc and musl).
**Not covered:** Go and other static binaries (no dynamic loader; confirmed 2026-10-18 on the `get` microVM, which preloads the shim globally: python port 0 works with it and is refused with `env -u LD_PRELOAD`; `tea login add --oauth` binds `127.0.0.1:0` and nothing connects, while `--redirect-url http://127.0.0.1:<fixed port>` works, so give a Go tool a fixed port); programs depending on `SA_RESTART`/`EINTR` (untested
prediction), so it is **not** enabled globally. Do not set it for jobs without loopback servers. **Every Gradle job is
one:** the client reaches its daemon over loopback, and even `--no-daemon` forks a single-use daemon (stayturgid run 1,
2026-10-17: "Could not connect to the Gradle daemon" after 6 s without the shim).

## 4. Probing and measuring

0. The workload repo is the public https://forgejo.mit.edu/<USER>/hyades-bench (a copy of the Plektra tree; default checkout
   `~/src/hyades-bench`; `BENCH_REPO`/`BENCH_SLUG`/`BENCH_URL` in `ci/tools/bench-env.sh` override). The scripts push and fetch by
   URL, never through a checkout's `forgejo` remote. Run numbers cited elsewhere in this skill are from the deleted private
   `<USER>/plektra-<USER>` Forgejo repo (its `bench/mit-ci` branch: GitHub `ci/archive/bench-mit-ci`).
1. `ci/tools/push-probe.sh WORKFLOW.yml probe/NAME [EXTRA_FILE...]` pushes a probe workflow as a throwaway branch on
   top of `bench/mit-ci` (extra files land at the repo root); `wait-run.sh SHA OUT` waits (`WAIT_MAX`, `RP`, default `<USER>/hyades-bench`) and saves the
   log; `push-bench.sh asis|tmpfs|loop BRANCH` derives and pushes the full workflow (`DERIVE_IMAGE=ghcr.io/...@sha256:...`
   uses our image); `run-matrix.sh` does unattended matrices. Run them with `run_in_background`; read `summary.md` only.
2. Probe sources: in `tests/`: `duplex-probe.c` (blocked recv vs send), `reuse-probe.c` (listener reuse, `REUSE_PORT`, modes
   jdk/close/kill/none), `dup-bind-probe.py`, `java-churn/` (okhttp loop). In `ci/tools/`: `accept-probe.c`,
   `keepalive-repro.py`, `listener-churn-repro.py`, `heartbeat.sh` (jstack of the stuck worker). `ci/probes/probe-*.yml` run them.
3. **Use a fresh port per probe run**: stale listeners from an earlier no-shim run on the same port drain over about a minute
   and look like shim failures (that mistake cost a day; libkrun#929 corrected).
4. Normal-Linux controls: Apple `container` CLI (Ubuntu 24.04, kernel 6.18) shows what a non-TSI kernel does.
5. Timings on a shared host: repeat; under 20% is noise.

## 5. Tickets (etiquette learned)

1. **Search the upstream tracker first** (`gh search issues --json`), with several phrasings: I filed #926 before finding
   #819 and #860 and had to say so.
2. **File only after testing finishes**; show the draft to the operator first; label it "prepared with an AI coding assistant,
   results reviewed by me"; correct your own errors in the ticket in the same breath.
3. Build the body with `jq -Rs`/`--rawfile`, check `[ -s file ]` before posting (an empty body once went out through a BSD `sed`
   failure in a pipeline); never read results through a pipe. Use GNU tools (`gsed`, `ggrep`).
3a. **SIPB tickets: fork only (operator, 2026-10-22):** AI output never goes on `SIPB/*`.
   Full `[AI detail]` report on `<USER>/hyades`; short human draft as that issue's
   first comment (`forgejo-two-tier-issue --fork-only`); the operator files upstream
   himself. Script refuses every `SIPB/*` write. Edit policy on the fork: under an
   hour and nothing after, else new comment; never delete (2026-10-17). History:
   2026-10-14 two-tier filings and later squash/repair live in
   `docs/tickets/two-tier/hyades-N/`. Details: the `forgejo` skill.
4. Ticket map: SIPB/hyades#2 (disk speed; AI twin <USER>/hyades#5), **#3 (TSI bugs + ask: ship the shim precompiled,
   opt-in, in the ci-images; @xy: only after a human review of the code, or passt for CI instead; twin #6)**, #4 (step cwd; twin #7);
   libkrun #818 (port 0), #819 (blocked recv), #860 (always `SO_REUSEPORT`), #924 (virtiofs speed; slp replied 2026-10-14, see item 1.4a), #926 (`shutdown`/`getpeername`
   hang), #929 (stale listener). **#3 is answered: do not re-ask, and do not ask the operator to review the C** (comment 69,
   2026-10-14: he does not understand it; SIPB may close the ticket). If they later enable passt for CI, re-run the suite
   without the shim before removing it. Replies are not watched: check with `gh issue view N -R libkrun/libkrun --comments` and
   `forgejo-api GET /repos/SIPB/hyades/issues/N/comments`.

## 6. Results to date

1. **Without the shim** (runs 189-205): the test step hangs forever on okhttp MockWebServer, or 7 tests fail per run, a different
   flaky set each time. Disk speed was not the cause (as-is, tmpfs, loop storage all hung identically).
2. **With the shim** (runs 229, 230, and 241 on our image): 1,417 tests, **1 failure, `CornerButtonThemeTest`**, which also fails
   on GitHub Actions and on a laptop (an upstream bug); test step about 3 minutes. An okhttp MockWebServer loop: 800 iterations in
   1 s with the shim vs 150 s per 150 and about 30% failures without.
3. **Our image** (run 241): setup step 0.2 s instead of about 100 s (JDK + SDK install 69 s, gcc, shim compile); compile 4 m 25 s
   unchanged (virtiofs-bound); job otherwise as before. Run 240: `actions/checkout`, toolchain and shim verified.
4. **SIPB/hyades#3 is answered (2026-10-14).** @xy: passt for the CI runner only (not Hyades) until upstream TSI fixes; the shim
   goes into ci-images only after a human has reviewed and understood the code. the operator will not be that reviewer. Plektra
   keeps the opt-in preload in our own image. Still open only if SIPB picks it up: global preload (needs `SA_RESTART`/`EINTR`
   tests, per-job port ranges, Go/static-binary answer), and a no-shim re-test if CI moves to passt.

5. **Plektra, 2026-10-14 to 2026-10-18 (retired):** it was built only on Hyades (fork-only `plk/base`:
   `.forgejo/workflows/djb-main.yml`, `djb-test-ref.yml`, the loop image of item 1.4a, `.forgejo/scripts/fast-disk.sh`,
   `scripts/djb-ci-run`, `scripts/djb-install-latest`). Now see item 6; `.forgejo/` stays in its tree as history and the
   `djb-*` scripts forward to the `github-*` ones.
6. **GitHub-sourced repo on free GitHub runners (2026-10-18), the recipe for any of them:** GitHub stays the source of
   truth and there is no Forgejo copy. An orphan branch `ci/base` holds only the CI overlay: `.github/workflows/<ci>.yml`
   (triggered by `push: branches: ['ci/run/**']`), `scripts/github-ci-run` (or `github-test-ref` for Plektra and hermes-agent),
   `docs/ci-github.md`. The wrapper builds a commit = target tree + overlay (deleting only the workflows whose unrestricted
   `push` trigger would fire on `ci/run/**`, so upstream 96-core jobs never queue), force-pushes
   `ci/run/<slug>-<sha8>` and watches the run (`--no-wait` prints the URL). It needs `origin/ci/base` fetched. Stock
   `ubuntu-latest` (4 vCPU, 16 GB) is fine: no loopback shim, no fast-disk loop, no Forgejo token (those are libkrun/Hyades
   workarounds). Per repo: Plektra `scripts/github-test-ref [--release] REF` and `scripts/github-install-latest` (APK
   re-signed with the Mac debug key from the secret `DJB_DEBUG_KEYSTORE_B64`; AGP alone mints a foreign cert on a runner);
   hermes-agent `scripts/github-test-ref REF [REF2]` (10-shard pytest, about 9 min against about 51 min on Hyades; upstream
   `CI`/`Tests`/`Docker`/... workflows are disabled on the fork because they ask for 32/96-core runners); herdr,
   secretspec-sqlite, sudo-secretspec `scripts/github-ci-run` (on their default branch too); stayturgid agent `just
   agent-ci-run`, which publishes the **unsigned** APK as the GitHub prerelease `agent-ci-<versionName>-<sha8>`, then `just
   agent-ci-fetch` (uses `gh`) downloads, checks, signs with apksigner on the Mac and verifies the signer. `bg` refuses
   local builds via `load-watch ci decide`. A new repo: copy a sibling's `ci/base` files. Free-plan limit: 20 concurrent
   jobs.

Not in this repo (see its README, "What is not in this repository"): `local-timings.md`, the generated `forgejo-ci.yml`, the general
`forgejo` skill and pre-2026-10-14 history, all in local-ade.

Reference files, all in `docs/`: `shim-by-default.md` (how to get the shim into jobs, six ways,
risks), `tickets/` (ticket texts, including the filed #3 as `sipb-hyades-shim-request-draft.md`), `comparison.md`,
`hyades-disk-research.md`; the deriver is `ci/tools/derive-forgejo-ci.py`.
