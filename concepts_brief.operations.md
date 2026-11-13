# yt-knowledge — operations pass

* **Author:** Hannes
* **Date:** 2026-11-10
* **Status:** brainstorm brief, pre-draft
* **Mode:** shipped-system audit (EGM). Reliability first, coverage second.

## Overview

The archive, the worker and the digest generator all exist and run. What does not exist is an operator: nothing triggers collection or digest generation, the retry loop cannot tell a blocked IP from a bad video, and the digest reports numbers it cannot back. This pass makes the system truthful and self-driving before it makes it larger.

Measured on 2026-11-10 against the live database: 1,954 videos — 141 ready (7.2%), 1,191 failed (60.9%), 564 archived (28.9%, of which 361 by the 30-minute ceiling), 53 no_caption. Last watch ingested 2026-11-01. One digest row ever: 2025-W31, html NULL. Re-measured at 22:55 after the metadata wall cleared: failed 325, pending 816, ready 193. No launchd job, no crontab, no Hermes cron job and no compose service schedules collection or digest generation.

## Analysis

**A. The retry loop is the failure, not the fix.** `fetch.metadata` raises plain `Retryable` for every yt-dlp error that is not private/removed (`ytk/fetch.py:43-48`); only `fetch.transcript` ever raises `Blocked` (`ytk/fetch.py:82-83`). Metadata failures therefore fall into the catch-all `db.fail` (`ytk/worker.py:96-99`), which increments `attempts` forever (`ytk/db.py:267-276`) and re-arms the row hourly, while `CLAIM_SQL` keeps `state='failed'` claimable with no attempt limit (`ytk/db.py:193`). That is the 1,191-row population at attempts 23-25 with 870 due right now. The YAGNI lens is right that the pause machinery already exists; the gap is classification plus a terminal count, not a component.

**B. Nothing runs it.** The only caller of `digest.generate` is `POST /digest/run` (`ytk/app.py:212`). `week_of()` returns the week containing today (`ytk/digest.py:12`), so the obvious Monday-morning run digests a week that is hours old and returns "no summarised videos that week". The collector is documented as one manual command (`README.md:40-45`) and the design records "no timer" as a decision (`docs/designs/yt-knowledge.md:340-341`). A daily in-process check is smaller than any external scheduler and matches the precedence already in the repo (`scripts/backup_loop.py`).

**C. The digest says things that are not true.** The prompt asserts "This person watched {len(rows)} videos" where `rows` are only videos that already have a summary (`ytk/digest.py:33-50`) — 23 of 165 in 2026-W44. `bullets_json` is selected and never used (`ytk/digest.py:35` vs `45-55`), so the second section, "what you got out of it", can only be invented. `html` is written NULL and never read (`ytk/digest.py:62`). The page prints the current week beside a stored row's own week (`ytk/app.py:198`, `ytk/templates/digest.html:12`) and renders markdown inside `white-space:pre-wrap` (`ytk/templates/digest.html:16`), so `##` ships literally. The window is computed in Berlin (`ytk/digest.py:7,12`) but compared as UTC strings (`ytk/digest.py:40`), shifting both edges by one to two hours.

**D. The LLM route blocks the service.** `/digest/run` is `async def` and calls `digest.generate` inline (`ytk/app.py:201-213`), which makes a 240-second blocking `urllib` call (`ytk/digest.py:56`, `ytk/summarize.py:96-108`). The event loop that serves `/healthz` and hosts the worker task freezes for the duration, past the image's healthcheck timeout. Note the trap: offloading all of `generate()` would use the app's connection, which is opened with `db.connect(cfg.db)` at `ytk/app.py:27`; the worker passes `same_thread=False` (`ytk/worker.py:125`) for exactly this reason. Offload the call, or open a thread-local connection.

**E. Coverage is 7% and the ceiling removes the content he watches.** 361 of 564 archived rows were dropped for exceeding a 30-minute ceiling that the design explicitly refused ("a two-hour podcast watched to the end is exactly the material this archive is for", `docs/designs/yt-knowledge.md:233-234`). Raising it is a settings row, not code (`ytk/db.py:353`; `ytk/worker.py:107-111`), but archived rows are not claimable (`ytk/db.py:193`) and `retry_failed` selects only `state='failed'` (`ytk/db.py:334`), so the exclusion is effectively permanent and needs a one-off requeue.

**F. Operative debt that costs more than it looks.** `scripts/push_data.sh:9-11` still copies the laptop's stale database over the live one — exactly the direction the deploy plan retired. The deployed NAS tree is not a repository and the Mac tree carries the whole implementation as uncommitted changes over a single initial commit, so nothing identifies what is serving port 8499. `running_since` is cleared only at startup (`ytk/db.py:207`), leaving 1,704 stale timestamps. `db.search` wraps the whole query in one FTS phrase (`ytk/db.py:290`), so "distributed system" returns nothing while "postgres" returns hits. MCP's host allowlist omits the Tailscale address (`ytk/mcp_server.py:21-23`), so `/mcp` answers 421 off-LAN.

## Goals vs Non-Goals

* **In scope:** unattended weekly digest for the week that ended; a worker that stops instead of burning attempts; a digest whose numbers and rendering match reality; recovery of the long-form backlog; identifiable deployment; a runbook that states the manual steps.
* **Out of scope:** multiple users, multi-account secrets, new services or sidecars, a markdown renderer dependency, frames/vision, a web UI redesign, cloud anything.

## Alternatives Weighed

* **Scheduler:** in-process daily check for the previous ISO week (chosen: no new actor, idempotent via the existing "already exists" branch) vs launchd job on the NAS (cannot see the DB owner) vs Hermes cron (outside the process, another thing to keep alive) vs compose sidecar (a service for ten lines).
* **Collector:** launchd job on the Mac (only place browser history exists) vs manual command plus a staleness signal on `/healthz`. The plist is the honest fix but puts the NAS secret on the laptop and reverses a recorded decision; it needs an explicit owner decision.
* **Fetch wall:** classify metadata failures as `Blocked` (rejected: a false positive pauses ~1,900 rows for 300 s on every failure) vs terminal give-up after N attempts plus one manual probe (chosen) vs a separate health-probe process (a new moving part for a one-line diagnosis).
* **Digest rendering:** plain-text prompt + existing `pre-wrap` (chosen) vs adding a markdown renderer (new dependency, XSS surface for LLM output) vs storing rendered HTML (the dead column).
* **Deployment identity:** commit the tree and deploy `git archive HEAD` with the SHA recorded (chosen) vs `git init` on the NAS (a second history nobody can diff) vs status quo (cannot answer what is running).
* **Coverage timing:** lift the ceiling after the fetch path works (chosen) vs now (adds 361 heavy transcript jobs to a queue that has not drained).

## Key Decisions & Staging

| ID | Severity | Finding (one line) | Evidence | Disposition |
|---|---|---|---|---|
| F1 | BLOCKER | Diagnose the yt-dlp wall with one in-container run before changing the classifier; the outcome decides cookies, image update, or block-handling. | `ytk/fetch.py:43-48`, `ytk/worker.py:96-99` | FIX NOW |
| F2 | BLOCKER | Stop the infinite retry: claim only `failed` rows below N attempts and show the retired count on `/queue`. | `ytk/db.py:193,271` | FIX NOW |
| F3 | BLOCKER | Make the digest default to the previous ISO week and compare an aware Berlin window converted to UTC. | `ytk/digest.py:12,27,40` | FIX NOW |
| F4 | BLOCKER | Stop the digest lying: pass "N of M watched", feed the bullets, write plain text, stop writing `html`, show the stored row's week. | `ytk/digest.py:35,50,62`; `templates/digest.html:12,16`; `app.py:198` | FIX NOW |
| F5 | BLOCKER | Run one digest per day inside the app for the previous week; the existing "already exists" branch makes it idempotent. | `ytk/digest.py:29-31`; `scripts/backup_loop.py` | FIX NOW |
| F6 | MAJOR | Offload the 240 s LLM call without handing the app's connection to a thread. | `ytk/app.py:212`; `ytk/db.py:93-99`; `ytk/worker.py:125` | FIX NOW |
| F7 | MAJOR | Delete `scripts/push_data.sh`: it overwrites the live database with a stale copy. | `scripts/push_data.sh:9-11` | FIX NOW |
| F8 | MAJOR | Commit the 11 dirty files; deploy `git archive HEAD` and record the SHA in the tree. | `git status`; `docs/plans/nas-deploy.md:40-42` | FIX NOW |
| F9 | MAJOR | Cap `/import` body size so one push cannot exhaust the process that owns the SQLite writer. | `ytk/app.py:301-311` | FIX NOW |
| F10 | MINOR | Clear `running_since` in `fail`/`finish`/`save_summary` so the column means what `/queue` prints. | `ytk/db.py:267-284` | FIX NOW |
| F11 | MINOR | Add the Tailscale host to MCP's allowlist. | `ytk/mcp_server.py:21-23` | FIX NOW |
| N1 | MAJOR | Decide the collector trigger; either a launchd plist with its own secret or a manual step plus a "newest watch is old" line on `/healthz`. | `README.md:40-45`; `docs/designs/yt-knowledge.md:340-341` | FIX NEXT (decision) |
| N2 | MAJOR | After the fetch path works: raise the ceiling via `/settings` and requeue the length-archived rows in one statement. | `ytk/db.py:353,334`; `ytk/worker.py:48-56` | FIX NEXT |
| N3 | MAJOR | Search by token AND instead of one phrase, so multi-word queries match. | `ytk/db.py:290` | FIX NEXT |
| N4 | MAJOR | Record the answering model from the response and pass it into digests; pin the model in `.env`. | `ytk/summarize.py:100`; `ytk/db.py:251`; `ytk/digest.py:62` | FIX NEXT |
| N5 | MAJOR | Give `/import` its own write-scoped token and a session lifetime. | `ytk/app.py:64-68,85`; `ytk/config.py` | FIX NEXT |
| N6 | MINOR | Write the restore runbook and assert `user_version` at startup; verify the reported double-migration claim before trusting an old snapshot. | `ytk/db.py:104-127` | FIX NEXT |
| A1 | — | One secret for login, session and push. | `ytk/config.py`, `ytk/app.py` | ACCEPT while single-operator and LAN/Tailscale-only; breaks the day a second operator or remote access exists. |
| A2 | — | No CSRF token; `SameSite=strict` is the defence. | `ytk/app.py:85` | ACCEPT until a second user exists. |
| A3 | — | Backups are never opened or integrity-checked. | `scripts/backup_loop.py` | ACCEPT only until N6; treat snapshots as unproven. |
| A4 | — | 30-minute ceiling as load protection. | `ytk/worker.py:107-111` | ACCEPT until F1/F2 land; it is a symptom, not the policy. |
| A5 | — | `YTK_LLM_MODEL=auto`. | `.env` | ACCEPT until N4; summaries are not reproducible meanwhile. |
| R1 | — | Add a markdown renderer for digest output. | `templates/digest.html:16` | REJECT: make the prompt emit plain text; deletes the dependency and the XSS surface. |
| R2 | — | New scheduler service or sidecar for the digest. | `ytk/app.py:25-52` | REJECT: the app already owns a lifetime task. |
| R3 | — | `git init` on the NAS. | `docs/plans/nas-deploy.md:40-42` | REJECT: second history; deploy a commit instead. |
| R4 | — | Reclassify metadata failures as `Blocked` before diagnosis. | `ytk/worker.py:83-89` | REJECT: a false positive pauses ~1,900 rows per failure. |
| R5 | — | New abstraction for the circuit breaker. | `ytk/worker.py:14,147-156` | REJECT: `STATE` and the pause marker already exist. |
| R6 | — | Lift the duration ceiling this week. | `ytk/db.py:353` | REJECT: 361 heavy jobs onto a queue that has not drained. |

## Open decisions for the owner

1. **Collector trigger.** A launchd job keeps the archive current but puts `YTK_SECRET` on the Mac and reverses the "no timer" decision; the alternative is a manual step with a staleness warning on `/healthz`.
2. **Pinned model.** Which model the gateway actually serves, and whether summarising through the gateway's agent prompt is acceptable per summary.
3. **Long-form budget.** Once the fetch path works, do multi-hour streams get transcribed (cost, and `summarize.clip` drops the middle third at 48k chars), or does a ceiling stay?

## Verification this pass leaves behind

* `week_of(date(2026,11,9))[2] == "2026-W46"` and the 2026-11-02 window formats as `2026-11-01T23:00:00Z` in CET.
* A claim test: a `failed` row at the attempt limit is not claimed; one below it is.
* A digest test: "N of M" uses the watched count, not the summarised count.
* `/healthz` reports the newest watch date, so staleness is visible without reading the database.

## Outcome (2026-11-10, 22:55)

F1 answered before any code changed: `yt-dlp 2026.10.26` inside the container
returns metadata for a row that had failed 24 times, so the wall was a window,
not a permanent block. The caption endpoint still answers `IpBlocked`
intermittently. The queue is draining on its own: failed 1,191 -> 325,
pending 816, ready 193, and `ready` keeps climbing.

Landed in one commit (525c395), 35 tests pass, deployed and healthy:

* F2 — 20 consecutive failures pause the worker 900 s and print the reason on
  `/queue`, instead of re-arming the same rows forever.
* F3 — `last_week()` and `window()`: the digest takes the week that ended and
  compares UTC instants converted from Berlin midnight. Four tests cover it.
* F4 — the prompt states "N of M" (23 of 165 for 2026-W44), feeds two timestamped
  bullets per video, asks for plain text, no longer writes `html`, and the page
  shows the stored row's week.
* F5 — `digest.loop()`: one check every six hours inside the app, previous week,
  own connection, idempotent through the unique `week`.
* F6 — `/digest/run` offloads to a thread with a thread-local connection, so the
  event loop no longer blocks for the LLM.
* F7 — `scripts/push_data.sh` deleted; it copied the laptop database over the
  live one.
* F9 — `/import` rejects bodies over 32 MB by Content-Length (chunked bodies
  remain unbounded; noted in the code).
* F10 — `running_since` cleared in `fail`, `finish` and `save_summary`.
* F11 — MCP accepts the Tailscale host.

Verified against the live service: `POST /digest/run {"week":"2026-W44"}` wrote
2,638 characters, `video_count` 23, and the text opens with "This covers 23 of
the 165 videos". The 2025-W31 row is untouched.

F8 is half done: the baseline and the operations commit exist on the Mac; the
NAS tree is still a tar copy with no commit stamp. N1-N6 are unchanged.

## Next-pass outcome (2026-11-10, 23:20)

* **N1 collector.** `YTK_IMPORT_TOKEN` is a separate, write-scoped token accepted
  only under `/import*`, so the Mac never holds the login secret. The script
  reads it from the Keychain when available, otherwise from
  `~/.config/ytk-import-token` (0600) — adding a Keychain item from a non-GUI
  session fails with "User interaction is not allowed". A LaunchAgent cannot read
  `~/Documents` (TCC: "Operation not permitted"); the collector runs as a Hermes
  cron job at 21:10 over the ssh session instead. First run: 2,353 visits,
  142 new, 2,056 merged, newest watch 2026-11-11 (was 2026-11-01).
* **N2 ceiling.** `max_duration_s` set to 0 (no ceiling); the 361
  length-archived rows requeued. `archived` 567 -> 206, `pending` 1,318.
* **N3 search.** `db.search` now requires every word, not adjacency:
  `"acquisition confusing"` matches "a confusing acquisition". Test added.
* **N4 provenance.** `summarize.call()` returns the model that answered and the
  summariser/digest store it. The gateway exposes one id (`hermes-agent`), so the
  env pin stays `auto`; new rows now record what actually answered.
* **N5 import scope.** Import token as above; session lifetime 7 days instead of
  Starlette's 14.
* **N6 restore.** The schema guard already existed
  (`test_user_version_mismatch_refuses_to_start`); the missing half was the
  written procedure, now in `README.md` under "Restore a backup".
* **N7 deploy stamp.** Deploy copies `git archive HEAD` and writes the commit to
  `DEPLOYED`, which the image bakes in; `/healthz` reports
  `"version": "b06cd9a"`. `/healthz` also reports `newest_watch`, so staleness is
  visible without opening the database.

36 tests pass. Commits: 1b591e0, b06cd9a, 244fe98.

## Digest cadence (follow-up)

The loop now checks hourly and refuses to freeze a week while it is still being
fetched: `should_wait()` counts watched videos in that week that are
pending/fetched/running/failed, and holds off until the week is four days old,
then writes it with whatever exists. Without that gate a thin digest was written
once and never corrected. `/digest` states how many videos are still being
fetched.

Proof: after the deploy (f2fa0b1) the loop ran on startup and created 2026-W45
on its own — 2 videos, 1,844 characters. Two of ninety-nine, because only 193
videos in the archive have summaries; the digest is not the bottleneck.
