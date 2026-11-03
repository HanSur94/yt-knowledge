# Structural review — controls-design.md

Lens: does the document match the code that actually exists. Every claim below is
checked against the working tree (`ytk/app.py`, `ytk/db.py`, `ytk/worker.py`,
`ytk/config.py`, the templates, `Dockerfile`, `compose.yaml`). Items marked
`[measured]` were executed against a live instance on port 8611 with
`YTK_WORKER=0` and a scratch database.

## Verified

1. **TRUE** — "Only `/digest/run` is reachable from the UI."
   (controls-design.md:12)
   `grep -rn "action=" ytk/templates/` returns four forms (library search,
   login, digest search, digest run); exactly one targets a control endpoint:
   `<form method="post" action="/digest/run" ...>` (ytk/templates/digest.html:5).
   No template references `/export/md`, `/admin/resummarize` or `/import/url`;
   `ytk/templates/base.html:32-34` carries nav links only. No `<script>` tag
   exists in any template.

2. **TRUE** — "the web process and the worker process are the same process here"
   (controls-design.md:80)
   `task = asyncio.create_task(worker.run(cfg, stop))` (ytk/app.py:35), started
   from `lifespan`.

3. **TRUE** — "`/queue` already computes `db.queue_counts(conn)` ... `n` in
   'Retry failed (n)' is `counts["failed"]`, already present. No new query."
   (controls-design.md:137-138)
   `counts = db.queue_counts(conn)` (ytk/app.py:224); `db.queue_counts` defaults
   every key in `STATE` including `failed` (ytk/db.py:174-176).

4. **TRUE** — "the pause state must survive a container restart"
   (controls-design.md:81-82)
   The marker lands on the bind mount: `ENV ... YTK_DB=/data/ytk.sqlite`
   (Dockerfile:10) and `/volume1/docker/yt-knowledge/data:/data`
   (compose.yaml:8). Note the doc never states which expression produces the
   path — see N1.

5. **FALSE** — "`videos.state='failed'` has a six-hour backoff
   (`60 * 2**attempts` capped by nothing)" (controls-design.md:22-23)
   The formula matches `delay = 60 * 2 ** attempts` (ytk/db.py:257), but no
   attempt count produces six hours, and the first failure waits two minutes,
   not six hours. Numbers in F3.

6. **FALSE** — "`resume_stuck` already repairs exactly this at startup"
   (controls-design.md:156-158)
   It is called at every start — `stuck = db.resume_stuck(conn)` (ytk/app.py:29),
   before the `YTK_WORKER` gate at ytk/app.py:34 — but the risk it is cited
   against is a pause, and a pause never restarts the process, so the repair
   never runs. It cannot be needed either: with a pre-claim check the in-flight
   row completes. See F4.

7. **FALSE** — "All controls are `POST` and all accept both a browser form and
   JSON, the way `/digest/run` already does" (controls-design.md:94-96)
   Only `/digest/run` branches on content type (ytk/app.py:181-197).
   `/export/md` (ytk/app.py:201-205) and `/admin/resummarize`
   (ytk/app.py:208-218) ignore the body and always return JSON; `/import/url`
   does `payload = await request.json()` unconditionally (ytk/app.py:259) and
   returns 500 on a urlencoded form body `[measured]`. See F1.

8. **FALSE** — "`ytk/config.py` | add `home` (the data dir) | the marker needs a
   path already derived there" (controls-design.md:170)
   `Settings` has five fields and no `home` (ytk/config.py:16-23). `_path()` is
   applied per field to `db` and `thumbs` only (ytk/config.py:31-32); nothing
   derives a data directory. See N1.

Also contradicted: "the only place a row is taken" (controls-design.md:169) is
false — `worker._tick` has two callers. See F5.

## Findings

### F1 — BLOCKER: the "form and JSON" pattern does not generalise, and one control 500s

**Claim** — controls-design.md:94-96: "All controls are `POST` and all accept
both a browser form and JSON, the way `/digest/run` already does". The route
table (98-106) lists `/export/md`, `/admin/resummarize` and `/import/url` as
"exists", i.e. no change; §UI (131-133) requires "Every control is a plain
`<form method="post">`".

**Evidence**
- `/digest/run` is the only handler that sniffs the content type
  (ytk/app.py:181-182). On a form post it throws the body away — `body = {}`
  (ytk/app.py:183-188) — and forces `force=True` (ytk/app.py:190). It never
  reads a form *field*, so it is not a template for a control that carries a
  value. It also branches the response: 303 to `/digest` for a form, dict for
  JSON (ytk/app.py:196-198).
- `/import/url` needs a `url` value. Measured form post:
  `curl -X POST .../import/url -H 'Content-Type: application/x-www-form-urlencoded' --data 'url=...'`
  → **500**, traceback `File "ytk/app.py", line 259, in import_url /
  payload = await request.json() / json.decoder.JSONDecodeError: Expecting
  value: line 1 column 1`. The same call as JSON returns
  `{"received":1,"new":1,...}` 200.
- `/export/md` as a form post returns `{"written":0}` with **200** `[measured]`
  (ytk/app.py:205). The operator's page is replaced by raw JSON, with no nav.
- `/admin/resummarize` as a form post returns `{"error":"YTK_LLM_MODEL is not
  set"}` **503** `[measured]` (ytk/app.py:214).

**Impact** — three existing handlers must change (content-type branch plus a 303
back to `/queue`), and `/import/url` needs a form-field read that `/digest/run`
cannot demonstrate. The implementation table budgets "four routes"
(controls-design.md:171), so the scope is understated by three handlers, one of
which is broken for the intended use.

**Fix** — one helper, used by all six mutating handlers:

```python
def _is_form(r): return (r.headers.get("content-type") or "").startswith(
    ("application/x-www-form-urlencoded", "multipart/form-data"))
```

Then `if _is_form(request): return RedirectResponse("/queue", 303)` at the end of
each, and read `url` from `await request.form()` in `import_url`. Add a row to
the implementation table for the three existing handlers.

### F2 — MAJOR: AC1 contradicts CUJ 1 and is flaky in both directions

**Claim** — controls-design.md:178: "With the marker present, `queue_counts` is
unchanged over 60 seconds."

**Evidence**
- CUJ 1 says "The current row finishes, no new row is claimed"
  (controls-design.md:54-55). `_tick` is one row start to finish
  (ytk/worker.py:13-70) and the loop only re-checks before the next claim. So a
  marker that appears while a row is in flight *does* change counts when that
  row lands in `ready`/`failed` — a correct implementation fails AC1.
- If the in-flight tick is slow (a transcript fetch plus a summarize call can
  exceed 60 s), the counts do not change inside the window and a **broken**
  pause passes.
- If the queue is empty the check is vacuous.
- An unauthenticated form post to any new route gets `303 -> /login` and
  `curl` exits 0 `[measured]`, so a test that forgets the bearer header also
  "passes".

**Fix** — assert the invariant where it lives, not on a wall clock:

```python
# tests/test_controls.py
touch(cfg_db.parent / "paused")
assert worker._tick(conn, cfg) is None          # no claim while paused
```

and for the shell check, start from a quiescent queue
(`counts["running"] == 0`), snapshot, wait 30 s, compare. The 60-second version
is the only acceptance item that will burn real time (see Worst finding).

### F3 — MAJOR: the backoff is not six hours, and the first retry is two minutes

**Claim** — controls-design.md:22-23: "has a six-hour backoff
(`60 * 2**attempts` capped by nothing)".

**Evidence** — `attempts` is incremented before the formula runs
(ytk/db.py:256-257), so the delay for a failure count `n` is `60 * 2**n`:

| attempts | delay | human |
|---|---|---|
| 1 | 120 s | 2 min |
| 2 | 240 s | 4 min |
| 3 | 480 s | 8 min |
| 6 | 3 840 s | 64 min |
| 8 | 15 360 s | 4 h 16 min |
| 9 | 30 720 s | 8 h 32 min |
| 12 | 245 760 s | 2.84 days |

Six hours is 21 600 s → `2**n = 360` → `n = 8.49`, so the value crosses six
hours *between* attempts 8 and 9. Six hours is never produced. The doc's own
argument ("waits forever once `attempts` is large") survives; the headline
number is wrong by 180x at the first failure.

Sub-note (`NIT`, "capped by nothing"): the only real cap is int64. At
`attempts=58`, `60 * 2**58 = 17293822569102704640 > 2**63-1`; the bind raises
`OverflowError: Python int too large to convert to SQLite INTEGER` `[measured]`.
Because `db.fail` is called *from* the except block (ytk/worker.py:69), the
raise escapes `_tick` and the row stays in `running` — `state` was already set
by the claim. Unreachable in practice (the delays sum to eons), so this is a
curiosity, not a live bug; but it means "the row waits a long time" is not the
worst case.

**Fix** — say "2 minutes, doubling per failure, uncapped: 4 h 16 min at eight
failures". If a ceiling is wanted it is one line: `min(60 * 2**attempts, 21600)`.

### F4 — MAJOR: `resume_stuck` does not cover pause, and the risk it is cited against cannot occur

**Claim** — controls-design.md:156-158: "**A pause between claim and finish
leaves one row in `running`.** Accepted: `resume_stuck` already repairs exactly
this at startup".

**Evidence**
- `db.resume_stuck` is one statement — `UPDATE videos SET state='pending' WHERE
  state='running'` (ytk/db.py:199) — and it is called only at startup
  (ytk/app.py:29) and by `scripts/backfill.py:30`. A pause is a file check
  inside the loop; it does not restart the process, so this repair cannot fire
  for a pause-caused row.
- The scenario cannot arise as described: the marker is checked before the
  claim, so a paused worker never abandons the row it already holds; that row
  runs to completion.
- The only way to strand a `running` row is a kill/recreate mid-tick — which is
  the alternative the doc rejects (controls-design.md:142-144), not a pause.

**Fix** — delete the bullet, or state the real residual: "a hard kill mid-tick
leaves one row in `running` until the next start, where `resume_stuck` clears
it." If pause should also repair on resume, add one `UPDATE` in
`POST /admin/resume`; there is nothing for it to repair otherwise.

### F5 — MAJOR: the pause check in `_loop` misses `scripts/backfill.py`

**Claim** — controls-design.md:169: "check the marker before each claim ...
**the only place a row is taken**".

**Evidence**
- `db.claim_next` is called at ytk/worker.py:15, inside `_tick` — not in
  `_loop`. `_tick` has two callers: `asyncio.to_thread(_tick, conn, cfg)`
  (ytk/worker.py:91) and `got = worker._tick(conn, cfg)`
  (scripts/backfill.py:38). The backfill script is the documented way to run a
  long crawl; it never looks at the marker.
- Consequence: pause in the UI, then run `scripts/backfill.py --hours 3`, and
  the crawl continues — the exact quota-burning scenario in the problem
  statement (controls-design.md:18-21).

**Fix** — put the guard at the top of `_tick`, before `db.claim_next`:

```python
def _tick(conn, cfg):
    if paused(cfg):
        return None
    row = db.claim_next(conn)
```

One guard, both callers. `_loop` then only needs the wait, and the "only place"
justification becomes true.

### F6 — MAJOR: the status line has no data behind it

**Claim** — controls-design.md:117-119: "`/healthz` gains `paused` and `worker`
(a boolean read from the same marker, and the container's `YTK_WORKER`
setting). `/queue` renders a status line: `running` / `paused` / `worker
disabled`, plus the last failure timestamp." Goal 4 (38-39) promises "whether
the worker is running, paused, or **blocked**, and why".

**Evidence**
- `worker` from `YTK_WORKER` is a config echo, not liveness. The worker is an
  `asyncio.Task` (ytk/app.py:35) whose exception is only observed at shutdown
  (`await task`, ytk/app.py:44-47). A task that dies at startup — e.g.
  `db.connect` raising on a locked file — is never noticed, and `/healthz` will
  report `worker: true` forever.
- `blocked` has no persisted source. The signal is a local return string,
  `return f"blocked:{exc}"` (ytk/worker.py:59), consumed at ytk/worker.py:95
  and dropped. The only durable trace is the row-level
  `state='pending', next_attempt_at=now+600` (ytk/worker.py:57-58), which the
  doc never mentions.
- "last failure timestamp" has no column. `videos` has `first_seen`,
  `last_seen`, `next_attempt_at` and no `failed_at` (ytk/db.py:11-21), and the
  failures query selects `id, title, fail_stage, fail_reason, attempts` only
  (ytk/app.py:226). There is nothing to render.

**Fix** — three one-liners: store the task (`app.state.task = task`) and report
`worker = task is not None and not task.done()`; report blocked as
`SELECT COUNT(*) FROM videos WHERE state='pending' AND next_attempt_at > :now`
(the only writer of pending+future is the block path); replace "last failure
timestamp" with "next retry at" from `next_attempt_at`, or drop it. A real
`failed_at` needs a v4 migration, which the doc's own pause rationale argues
against (controls-design.md:146-148).

### F7 — MAJOR: CUJ 3 cannot succeed on the NAS; export writes outside the volume

**Claim** — controls-design.md:63-64: "Operator clicks Export markdown on the
Queue page, sees `written: 21`, and finds the notes under the export path."
Risk section (162-163): "measured on the laptop at 21 notes."

**Evidence**
- `export.write_tree(conn, os.environ.get("YTK_EXPORT") or "data/export")`
  (ytk/app.py:204) — a raw relative path, not passed through `config._path()`,
  so it resolves against the process cwd.
- In the image the cwd is `/app` (`WORKDIR /app`, Dockerfile:3), and the only
  volume is `/data` (Dockerfile:9, compose.yaml:8). `write_tree` does
  `os.makedirs(folder, exist_ok=True)` (ytk/export.py:87), so the notes land in
  `/app/data/export` — the container's writable layer, lost on the next
  recreate and invisible to Obsidian.
- `.env.example` sets no `YTK_EXPORT` (lines 1-5), and `compose.yaml:5` runs the
  container as uid 1000 while `/app` is created root-owned by `WORKDIR`
  (Dockerfile:3). Creating `/app/data` as uid 1000 will most likely raise
  `PermissionError` → 500. (Ownership is inferred from Dockerfile:3-10 +
  compose.yaml:5; I could not run the container here — no Docker on this host.)
- The laptop measurement in the risk section is exactly the case where the
  default works, because the repo root is writable and `data/export` exists.

**Fix** — `ENV YTK_EXPORT=/data/export` next to Dockerfile:10 and in
`.env.example`; or route the default through `_path("data/export")` so laptop
and NAS agree. Name the export root in the doc.

### F8 — MAJOR: the acceptance section does not gate the increment

**Claims** — controls-design.md:178-182.

**Evidence**
- **AC1** — unsound and contradictory; see F2.
- **AC2** (179-180) checks `state='pending'` and `next_attempt_at IS NULL` only.
  The doc's own headline invariant is four columns (108-113). A retry that
  forgets `attempts=0` passes AC2 and re-fails into an 8 h 32 min penalty
  (attempts 8→9, F3); one that forgets `fail_reason=NULL` passes and leaves a
  stale error on a pending row.
- **AC3** (181) "/healthz reports `paused: true` within one tick of the marker
  appearing." `/healthz` would read the marker per request (117), so there is
  no tick to be within. A worker that ignores the marker entirely passes AC3,
  because `/healthz` and the worker share only `os.path.exists`.
- **AC4** (182) "Every control is reachable with JavaScript disabled" is not
  checkable from a shell — it needs a browser with JS off. The grep proxy
  (`<form method=post` present in the rendered HTML) passes today for
  `/import/url`, which 500s `[measured]`. **This is the one AC that is not
  objectively checkable from a shell.**
- Coverage: seven controls in the table, acceptance for two. No AC for resume,
  retry-all, export, resummarize, or import URL — the controls with the newest
  code. AC2 also races a live worker: the claim happens within `cfg.sleep`
  (0.5 s, ytk/worker.py:107), so the assert must run with `YTK_WORKER=0`.

**Fix** — AC1 → unit test on `_tick` (F2). AC2 → assert all four columns:
`SELECT state, attempts, next_attempt_at, fail_reason`. AC3 → drop "within one
tick" and assert the worker, not the endpoint. AC4 → "each control appears as
`<form method=post action=...>` in the rendered `/queue` HTML, and returns 200/303
with a session". Add one AC per control.

## NITs

**N1 — `config.home` does not exist and is not needed.** controls-design.md:74
writes `os.path.exists(cfg.home / "paused")`; `Settings` has no `home`
(ytk/config.py:16-23) and `cfg.home` would be a `str`, so `/` would `TypeError`.
Nothing "already derived" a data dir (`_path` is applied per field,
ytk/config.py:31-32). Delete the config.py row and use
`Path(cfg.db).parent / "paused"` — one expression, correct on laptop and NAS.

**N2 — CUJ 1 says the operator "sees `running`" (line 54), but `/queue` never
renders that count.** Queue stats iterate a fixed list that omits `running`:
`{% for k in ["total","pending","fetched","ready","failed","no_caption","archived"] %}`
(ytk/templates/queue.html:3), even though `queue_counts` supplies it
(ytk/db.py:174-175). Add one word, or reword the CUJ.

**N3 — route registration order.** New routes must be declared above
`app.mount("/", mcp_app)` (ytk/app.py:276); a route added after the mount is
unreachable — measured 404 on a minimal Starlette app with the same shape. The
file comments on this (ytk/app.py:274-275), but the design's "four routes" row
should say "above the `/` mount".

**N4 — CSRF surface is unstated.** There is no token and no Origin check: a POST
with a session cookie and `Origin: http://evil.example` returns 200 `[measured]`.
The only defence is `same_site="strict"` (ytk/app.py:77). That holds for true
cross-site posts, but same-site sibling subdomains on the NAS count as
same-site, and `https_only=False` (ytk/app.py:78) means no `Secure` flag. Worth
one sentence in the doc: "form POSTs are CSRF-safe only because the cookie is
`SameSite=strict`; do not relax it." No token needed for a one-operator LAN app;
this is a note, not a redesign.

**N5 — `/healthz` is the Docker healthcheck target** (`HEALTHCHECK ... /healthz`,
Dockerfile:12-13). Adding `paused` is fine; making the *status code* depend on
it would mark a deliberately paused container unhealthy. State that the endpoint
stays 200.

**N6 — "A per-row `retry` link" (line 131) contradicts "Every control is a plain
`<form method="post">`" (132).** A link is GET. Make it a form with a button,
and say so, or an implementer will add `GET /videos/{id}/retry` and violate the
doc's own POST rule.

## Worst finding

**AC1, "`queue_counts` is unchanged over 60 seconds" (line 178), against CUJ 1,
"The current row finishes" (line 54).** The two cannot both be true: touch the
marker while a row is in flight and a correct pause still changes the counts
when that row lands. The test is a coin flip — it fails on a correct
implementation whose tick completes inside the window, and passes on a broken
one whose tick is slow, on an empty queue, or when the pause POST silently
303'd to `/login`. An implementer will spend the day re-checking the marker
path, the check placement and whether `os.path.exists` sees the file, at 60
seconds per iteration, and the obvious "fix" — make pause interrupt the
in-flight row — contradicts CUJ 1 and creates the stranded-`running` risk that
F4 then has to excuse. Replace it with a sub-second pytest on `_tick`.
