# Design review — `docs/designs/controls-design.md` (DESIGN lens)

Reviewed 2026-11-02 against `ytk/app.py`, `ytk/db.py`, `ytk/worker.py`, `ytk/config.py`,
`ytk/templates/{queue,base}.html`, `Dockerfile`, `compose.yaml`, `.env.example`, `pyproject.toml`.
Two claims were proved by running code rather than by reading it; those runs are quoted inline.

## Verified

1. **"four control endpoints already exist and work" (doc:10-11) — FALSE.**
   All four routes exist: `/digest/run` (app.py:178), `/export/md` (app.py:201),
   `/admin/resummarize` (app.py:208), `/import/url` (app.py:257). Two of them do not work for the
   purpose this document gives them. `/admin/resummarize` writes
   `UPDATE videos SET state='fetched'` (app.py:215) and no query ever claims `fetched`:
   `WHERE state='pending' OR (state='failed' AND ...)` (db.py:185-186). `/import/url` does
   `payload = await request.json()` unconditionally (app.py:259), so a form post 500s.

2. **"`os.path.exists(cfg.home / "paused")`" (doc:74) — FALSE on two counts.**
   `Settings` has no `home` and no directory field at all: `secret, db, thumbs, bind, sleep`
   (config.py:17-23). And every path field is a `str` from `_path()` (config.py:8-13, `return str(...)`),
   so `cfg.home / "paused"` is a `TypeError`, not a path.

3. **"`worker._loop` checks for `<data dir>/paused` before each claim" (doc:70) — TRUE, the loop allows it.**
   The only claim in the process is `db.claim_next(conn)` at worker.py:15, inside `_tick`, and
   `_tick` is called from exactly one place: `done = await asyncio.to_thread(_tick, conn, cfg)`
   (worker.py:91). A check at the top of `while not stop.is_set():` (worker.py:89) is strictly
   before every claim. The only other `claim_next` callers are tests (tests/test_db.py:79,81); no
   production path claims outside `_tick`.

4. **Retry's four-column reset is sufficient — TRUE, but the document's reason for it is wrong.**
   Only `state='pending'` is load-bearing. CLAIM_SQL filters time **only on failed rows**:
   `WHERE state='pending' OR (state='failed' AND COALESCE(next_attempt_at,0) <= :now)` (db.py:185-186).
   Proved on a scratch DB: a `pending` row with `next_attempt_at = now+99999` **is** claimed on the
   next `claim_next`; a `failed` row with `attempts=0` and a future `next_attempt_at` is not.
   So "the claim query filters on it, so the retry would ... do nothing for up to six hours"
   (doc:110-112) is true only while the row stays `failed`. Clear the column anyway — it drives
   `ORDER BY COALESCE(next_attempt_at,0)` (db.py:187) — but the stated mechanism is the wrong clause.

5. **"`POST /queue/retry-failed`" is reachable (doc:103) — TRUE, the gate is not an obstacle.**
   `if request.session.get("ok") or _bearer_ok(request)` (app.py:68) passes it through; the prefix list
   `path.startswith(("/import", "/mcp"))` (app.py:71) only decides 401-JSON vs 303-redirect for
   *unauthenticated* requests, and `/queue/...` falls to `RedirectResponse("/login", status_code=303)`
   (app.py:73). A logged-in form post and a bearer-token script both reach it.

6. **"plus the last failure timestamp" (doc:119) — FALSE, there is no such value to read.**
   `videos` has `first_seen`, `last_seen`, `next_attempt_at` and no failure timestamp (db.py:11-21);
   `/queue` selects `id, title, fail_stage, fail_reason, attempts` (app.py:225-227). Satisfying that
   sentence needs a schema change the document says it does not need.

## Findings

### BLOCKER — `/admin/resummarize` is a silent no-op that strands every ready row in a state nothing claims
Doc:10-12 lists it as existing and working; doc:128 puts a "Resummarize on a different model" button on
`/queue`. Evidence: `UPDATE videos SET state='fetched', next_attempt_at=NULL WHERE state='ready' AND id IN
(SELECT video_id FROM summaries WHERE model IS NOT ?)` (app.py:215-217). CLAIM_SQL claims only
`state='pending'` or due `state='failed'` (db.py:185-186); `resume_stuck` repairs only `running`
(db.py:199). Proved: after two `claim_next` calls a `fetched` row was still `fetched` and nothing else
was claimable. One click after changing `YTK_LLM_MODEL` moves **all** ready rows to `fetched`
permanently; the route reports `{"reset": N}`, the queue's `fetched` count never drains, and nothing in
the shipped UI can move them back (the new retry routes only touch `failed`). Only
`scripts/summarize_all.py` (`WHERE v.state IN ('fetched','failed')`) can recover them.
Note the drift report "verified" this endpoint with `{"reset":0}` — the update never ran.
**Fix:** use the same reset the retry design prescribes: `state='pending', attempts=0,
next_attempt_at=NULL, fail_reason=NULL`; add `assert db.claim_next(conn)` as the test.

### BLOCKER — the Import URL control 500s: `/import/url` parses JSON only
Doc:94-96 ("all accept both a browser form and JSON") and doc:129 ("Import URL (a one-line form)"),
doc:133 ("no JavaScript"). Evidence: `payload = await request.json()` (app.py:259) with no
content-type branch — unlike `/digest/run`, which explicitly tests
`content-type` and reads `request.form()` (app.py:181-182). A `application/x-www-form-urlencoded`
body raises `JSONDecodeError` → 500. So the control cannot be used from the UI it is being added to,
and acceptance #4 ("Every control is reachable with JavaScript disabled", doc:182) cannot pass.
**Fix:** mirror `/digest/run`: `is_form = content-type.startswith(...)`, `url = form.get("url") if is_form
else (await request.json()).get("url")`, `RedirectResponse("/queue", 303)` on the form path.

### BLOCKER — the pause marker has no defined directory, and every default in this codebase resolves outside the mounted volume
Doc:70/84 say `<data dir>/paused` and `ls /volume1/docker/yt-knowledge/data/paused`; doc:170 says only
"add `home` (the data dir)". Nothing defines `YTK_HOME`, its default, or `.env`/compose wiring — and
`.env.example` has no such variable. The codebase's only path resolver is
`ROOT = Path(__file__).resolve().parent.parent` + `_path()` (config.py:5-13), i.e. the package's parent:
the repo on a dev checkout, **site-packages in the image** (`RUN pip install --no-cache-dir .`,
Dockerfile:6). The only persistent path in the deployment is `/data`
(`ENV YTK_DB=/data/ytk.sqlite`, Dockerfile:10; `VOLUME /data`, Dockerfile:9;
`/volume1/docker/yt-knowledge/data:/data`, compose.yaml). So the obvious implementation of `home`
(`_path(os.environ.get("YTK_HOME") or "data")`) writes the marker outside the volume: Pause 500s on a
permission error (container runs `user: "${PUID:-1000}:${PGID:-10}"`) or, if it succeeds, the marker is
lost on the next recreate — destroying the single justification the document gives for choosing a file
("the pause state must survive a container restart", doc:81) — and the documented SSH check points at a
path that will not exist.
**Fix:** derive it, do not configure it: `home = os.path.dirname(os.path.abspath(db))` inside
`Settings.load` (or skip the field entirely and use `Path(cfg.db).parent`), which is `/data` in the
image and `data/` on the laptop. No new env var, no `.env.example` row, no new failure mode.

### MAJOR — the retry rationale names the wrong filter clause, so the test it implies proves nothing
Doc:108-113 is the document's self-declared centrepiece. Because `state='pending'` is the first disjunct
(db.py:185), the time filter never applies to a retried row; clearing `next_attempt_at` matters for
ordering/backoff hygiene, not for claimability. Doc:174 then specifies
"retry clears all four columns" as the test — a test that passes even if the row is never claimable.
**Fix:** keep the four-column reset, rewrite the sentence to "the time filter applies only to
`state='failed'`; a pending row is claimable regardless", and assert `claim_next()` returns the row.

### MAJOR — the mitigation quoted for "Retry-all during a block" does not hold
Doc:159-161: "the worker re-blocks within a tick, having spent one request. Acceptable; the block path
already backs off 600s." Evidence: the blocked path sets `state='pending', next_attempt_at=now+600`
(worker.py:57-58), but a `pending` row is claimed regardless of `next_attempt_at` (db.py:185, proved
above). With an empty queue the same blocked row is re-claimed on the very next tick; the only real
throttle is `BLOCK_PAUSE = 300` in the loop (worker.py:10, 98), i.e. half the documented backoff.
**Fix:** make the filter honest — `AND (state<>'pending' OR COALESCE(next_attempt_at,0) <= :now)` in
CLAIM_SQL — and say that pause/retry inherit the corrected semantics.

### MAJOR — new routes must be declared above `app.mount("/", mcp_app)`; the document never says so
`# Mounted last: a Mount at a path prefix competes with the routes, so every FastAPI route must be
registered before it.` / `app.mount("/", mcp_app)` (app.py:274-276). Proved with a minimal Starlette app:
a route added *after* `Mount("/")` is never matched — the mount swallows it and the sub-app answers 404.
An implementer appending "four routes" next to the mount (the natural place, since they are the last
thing in the file) ships four 404s.
**Fix:** one line in the plan — "declare the four routes before app.py:276" — plus a route test that
asserts 303/200 rather than 404.

### MAJOR — "the last failure timestamp" needs a schema migration the document does not mention
Doc:119 requires it; there is no `failed_at` column (db.py:11-21) and `db.fail()` writes only
`attempts`/`next_attempt_at` (db.py:258-261). Deriving it (`next_attempt_at - 60*2**attempts`) is
guesswork. This is the only schema gap in the plan, and it contradicts the document's implicit
"no migration" stance (doc:82-83, 146-148).
**Fix:** drop the requirement, or display `next_attempt_at` as "next retry at" (already selected nowhere
— add it to app.py:226), or add `failed_at` with `SCHEMA_VERSION = 4` plus a `MIGRATIONS[3]` entry and
say so in the file table.

### MAJOR — four of the five controls return bare JSON with no post/redirect/get
Doc:131-133 makes every control a plain form; only `/digest/run` has the form branch that redirects
(`if is_form: return RedirectResponse("/digest", status_code=303)`, app.py:196-197).
`/export/md` (app.py:201-205) and `/admin/resummarize` (app.py:208-218) have no branch at all, so clicking
Export navigates the operator to `{"written": 21}` with no way back, and refresh re-POSTs. CUJ 3
(doc:63-64) describes this as a normal UI flow. `/admin/resummarize` returning
`{"error": "YTK_LLM_MODEL is not set"}` (app.py:214) as a redirect target is worse: a bare JSON error page.
**Fix:** one shared `_is_form(request)` helper (the check is already duplicated at app.py:181-182), and
`RedirectResponse("/queue", 303)` on the form path of all four.

### MAJOR — `/export/md` writes to a CWD-relative path the container cannot create; the document never names the export path
`export.write_tree(conn, os.environ.get("YTK_EXPORT") or "data/export")` (app.py:203-204). In the image
CWD is `/app` (Dockerfile:5), `/app` is root-owned and the process runs as uid 1000 (compose.yaml
`user:`), and `data/export` does not exist, so `os.makedirs` (export.py:87) raises `PermissionError` →
500. `YTK_EXPORT` appears in no file list here and in no `.env.example`. I cannot verify the NAS `.env`,
so whether this 500s in production is CANNOT VERIFY — but CUJ 3 ("finds the notes under the export
path", doc:64) is unfalsifiable as written because no path is stated, and the only writable mount is
`/data`.
**Fix:** default to `Path(cfg.db).parent / "export"` (i.e. `/data/export`), state the path in the doc and
in the UI ("written to /data/export"), and add `YTK_EXPORT` to `.env.example` if it stays configurable.

### MAJOR — `tests/test_controls.py` cannot run as specified
Doc:174 names one new test file for "pause marker honoured; retry clears all four columns". Evidence:
`fastapi.testclient` needs `httpx`, which is not installed and not in the dev extra
(`dev = ["pytest>=8", "ruff>=0.6"]`, pyproject.toml); `.venv/bin/python -c "import httpx"` →
`ModuleNotFoundError`. Worse, `from ytk.app import app` executes `cfg = load()` at import (app.py:19),
which raises `SystemExit("missing required env: YTK_SECRET")` and aborts collection for the whole suite,
and entering the lifespan starts the real worker (`worker.run(cfg, stop)`, app.py:34-35) against
`cfg.db` — with `YTK_DB` unset that is the repo's `data/ytk.sqlite`, so the test run would claim and
mutate archive rows.
**Fix:** put the retry/reset SQL in `db.py` (`db.retry_failed(conn)`, `db.retry_one(conn, vid)`) and test
it with the existing `tmp_path` fixture (tests/test_db.py:5-10); only add `httpx` to the dev extra if a
route-level test is actually wanted, and then set `YTK_SECRET`/`YTK_DB`/`YTK_WORKER=0` in a `conftest.py`
before import.

### MAJOR — acceptance #1 measures the wrong thing and will fail spuriously
Doc:178: "With the marker present, `queue_counts` is unchanged over 60 seconds." If a row is in flight
when the marker appears — the normal case, and the one CUJ 1 describes ("The current row finishes",
doc:54) — that row moves `running → ready/failed` and the counts change while pause is working correctly.
**Fix:** assert `db.claim_next(conn) is None` while the marker exists, and that removing it lets the next
tick claim a row. That is the property the design actually wants, and it is a two-line test.

### NIT — `cfg.home / "paused"` is a `TypeError`
Doc:74. `_path()` returns `str` (config.py:13) and `Settings.db`/`.thumbs` are annotated `str`
(config.py:19-20). Use `os.path.join` or declare the field `Path`.

### NIT — the optional `{"state": "failed"}` body on `/queue/retry-failed` is dead configuration
Doc:103. The route name fixes the value and no other value is specified or legal. Rung 1: delete the body.

### NIT — "within one tick" is wrong for `/healthz`
Doc:181. `/healthz` would stat the marker directly (as doc:117 says), so the answer is immediate, not
tick-paced. Say "immediately".

### NIT — the acceptance criterion and the test row disagree on how many columns retry resets
Doc:179-180 asserts `state='pending'` and `next_attempt_at IS NULL`; doc:174 says "all four columns".
Also `fail_stage` is left stale for a retried row while `fail_reason` is cleared (doc:108) — either clear
both or say why one survives.

### NIT — `base.html` "nothing" is not quite nothing
Doc:173. `input[type=search],input[type=password]` are the only styled inputs (base.html:21), so the
Import URL field added at doc:129 renders as an unstyled browser default. One selector.

### NIT — `/videos/{id}/retry` has no not-found behaviour
Doc:102. `ingest.VIDEO_ID` already exists for id validation (used at app.py:134); an unknown id should
be 404 rather than a 200 that resets zero rows.

### NIT — `/healthz` is unauthenticated and gains worker state
Doc:117. `if path in ("/healthz", "/login"): return await call_next(request)` (app.py:66). Publishing
`paused`/`worker` there is public by design (LAN-bound in compose.yaml), just say it is intentional.

### NIT — an expired session turns every control into a silent 303
Doc:73 sends unauthenticated non-`/import`, non-`/mcp` requests to `/login`. A stale cookie means Pause
appears to work and does nothing. Acceptable for one operator; worth one line, and the Pause/Resume
state is re-read from the marker anyway, so the badge will correct itself.

## Worst finding

`/admin/resummarize` (app.py:215) sets `state='fetched'`, a state no claim query accepts (db.py:185-186),
so shipping the "Resummarize on a different model" button silently strands every ready row in a
permanently unclaimable state while returning `{"reset": N}` — recoverable only by
`scripts/summarize_all.py` or hand-written SQL, never from the UI.
