# yt-knowledge: Operational Controls

Status: revision 2, post-review · 2026-11-02
Review: `reports/controls_design.md`, `reports/controls_structural.md`,
`reports/controls_readiness.md` (3 BLOCKER, 21 MAJOR, 19 NIT across three lenses)

## Revision 2 - what the review changed

Five mechanisms were wrong as drafted. Each is a decision now, not prose:

1. **`fetched` is not a queue state.** `CLAIM_SQL` selects only `pending` and due
   `failed` (`db.py:185-186`), yet `worker._tick` parks a row in `fetched`
   (`worker.py:38`) before it summarises, and `/admin/resummarize` writes
   `state='fetched'` for every stale-model row (`app.py:215`). One click
   therefore strands the whole `ready` set in a state nothing claims, and a
   crash between fetch and summarize strands a row the same way. Rev 1 shipped
   a button for the bug. Fix: `fetched` becomes claimable.
2. **Retry could not be immediate.** `next_attempt_at=NULL` ties the row with
   every pending row at sort key 0 and `CLAIM_SQL` then orders by watch count,
   so "retry now" lands behind ~1,890 rows. Rev 1 asserted the opposite. Fix:
   retry writes `next_attempt_at = -1`, which is claimable *and* sorts first.
3. **The pause marker had no path.** `Settings` has no `home`, `_path()` returns
   `str`, so `cfg.home / "paused"` was a `TypeError` (`config.py:17-23`). Fix:
   `Path(cfg.db).parent` — `/data` in the image, `data/` on the laptop. No new
   env var, no config change.
4. **`_tick` has two callers**, not one: the loop (`worker.py:91`) and
   `scripts/backfill.py:38`. A guard in `_loop` would leave a pause ineffective
   for the documented way to run a long crawl. Fix: guard inside `_tick`.
5. **Backoff was uncapped**, `60 * 2**attempts` (`db.py:257`) — 4 h 16 min at
   eight failures, 68 h at twelve, with no recovery except a human clicking.
   Fix: cap at one hour.

Facts corrected: production `failed` is **4** (not 40); `pending` is **1,890**
(rev 1 copied it into the risk section as a retry volume); `BLOCK_PAUSE` is
**300 s** (not 600); the block path costs **four** requests, not one.

## The problem

The service runs unattended on the NAS and its operator cannot steer it.

Four control endpoints exist: `POST /digest/run`, `POST /export/md`,
`POST /admin/resummarize`, `POST /import/url`. Only `/digest/run` is reachable
from the UI. Two controls do not exist at all:

- **No pause.** `YTK_WORKER=0` is read once at container start; stopping a crawl
  means recreating the container, which also restarts the web server.
- **No retry.** `db.fail` applies `60 * 2**attempts` with no ceiling and
  `attempts` only increases, so a row that failed during a short LLM outage
  waits hours or days. The only recovery is hand-written SQL.

## Goals

1. Start and stop the crawl from the UI without restarting the container.
2. Retry one failed video, or every failed video, and have it acted on promptly.
3. Reach the four existing endpoints from the UI, so no control needs curl.
4. Show whether the worker is running, paused, or blocked.

## Non-goals

- Accounts or roles. One shared secret; one operator.
- Scheduling UI. `YTK_SLEEP` stays an env var.
- Per-video delete. Data loss with no undo, and the 04:00 backup would copy it.
- A log viewer. `docker logs` is better than anything built here.
- A failure alert. Accepted risk: rows can sit `failed` for up to the capped
  hour, and nothing pages. Revisit if the archive becomes load-bearing.

## Critical user journeys

**CUJ 1 - stop a runaway crawl.** Operator opens Queue, clicks Pause. The row in
flight finishes, no new row is claimed, the badge reads `paused`. Serving the UI
is unaffected throughout. Resume continues from the same queue position.

**CUJ 2 - retry after an LLM outage.** Rows sit `failed` with
`fail_stage='summarize'`. Operator fixes the endpoint, clicks Retry all. The
rows become `pending` with `attempts=0` and `next_attempt_at=-1`, so the next
claim takes them **before** the backlog, not after it.

**CUJ 3 - pull the archive into Obsidian.** Operator clicks Export on the Queue
page and is redirected back to Queue with a count; the notes are under
`/data/export` on the NAS, which is inside the mounted volume.

## Technical plan

### Pause is a marker file, derived not configured

```python
def paused(cfg) -> bool:
    return os.path.exists(os.path.join(os.path.dirname(os.path.abspath(cfg.db)), "paused"))
```

Checked at the **top of `_tick`**, before `db.claim_next`, so both callers
(worker loop and `scripts/backfill.py`) honour it:

```python
def _tick(conn, cfg):
    if paused(cfg):
        return None
    row = db.claim_next(conn)
```

The loop keeps its existing wait idiom — never `time.sleep`, which would block
the web server's event loop, and never bare `asyncio.sleep`, which ignores
`stop`:

```python
while not stop.is_set():
    if paused(cfg):
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            pass
        continue
    done = await asyncio.to_thread(_tick, conn, cfg)
```

Why a file: it survives a recreate (the marker lives on the bind mount), needs
no migration, and is visible over SSH when the UI is down.
Ceiling, stated deliberately: no history, no author. When either is needed it
becomes a `settings` table with `changed_at`/`changed_by`.

### Claimability: one clause for all backoff

`CLAIM_SQL` becomes:

```sql
WHERE state IN ('pending','failed','fetched')
  AND COALESCE(next_attempt_at,0) <= :now
ORDER BY COALESCE(next_attempt_at,0),
         (SELECT COUNT(*) FROM watches w WHERE w.video_id=videos.id) DESC,
         (SELECT COALESCE(MAX(w.dwell_s),0) FROM watches w WHERE w.video_id=videos.id) DESC,
         first_seen
LIMIT 1
```

Three consequences, all intended:

- `fetched` is claimable, so a row parked between fetch and summarize is picked
  up and summarised instead of stranded — this is what makes Resummarize work.
- The time filter now applies to `pending` too, so the block handler's
  `next_attempt_at = now + 600` actually delays the row instead of merely
  reordering it (`worker.py:57-58`).
- Retry can jump the queue with a negative key: `-1 < 0`, so it sorts ahead of
  every `NULL` row while still satisfying `<= :now`.

### Routes

All mutating routes are `POST`, declared **before** `app.mount("/", mcp_app)`
(`app.py:276`) — a route added after the mount is shadowed by the MCP sub-app
and 404s. All share one helper:

```python
def _is_form(r) -> bool:
    return (r.headers.get("content-type") or "").startswith(
        ("application/x-www-form-urlencoded", "multipart/form-data"))
```

A form post gets Post/Redirect/Get: `303` to `/queue`. Anything else gets JSON.

| Route | Effect | Form field |
|---|---|---|
| `POST /admin/pause` | create the marker | — |
| `POST /admin/resume` | remove it | — |
| `POST /videos/{id}/retry` | one row, front of queue | — |
| `POST /queue/retry-failed` | every `failed` row, front | — |
| `POST /export/md` | exists; path fixed | — |
| `POST /admin/resummarize` | exists; now claimable | — |
| `POST /import/url` | exists; gains a form branch | `url` |

Retry is one statement, and it resets five columns — `state`, `attempts`,
`next_attempt_at`, `fail_stage`, `fail_reason`. Resetting `attempts` without
`next_attempt_at` leaves a six-hour wait on a row that was `failed`; resetting
`next_attempt_at` without `attempts` re-fails into the capped hour. Both halves
are load-bearing for different reasons.

### Export path

`/export/md` currently writes to a cwd-relative `data/export` (`app.py:204`),
which in the image is `/app/data/export` — root-owned, outside the volume, and
lost on recreate. Fixed to `Path(cfg.db).parent / "export"`, i.e. `/data/export`
on the NAS. `.env.example` gains `YTK_EXPORT` as the override.

### Status, with a source for every value

`/healthz` gains:

| Field | Source | Note |
|---|---|---|
| `paused` | `os.path.exists(marker)` | stays HTTP 200 — it is the Docker healthcheck |
| `worker` | `app.state.task is not None and not task.done()` | real liveness, not an env echo |
| `blocked` | `COUNT(*) WHERE state='pending' AND next_attempt_at > now` | the block path is the only writer of pending+future |

`/queue` renders `running / paused / blocked / worker disabled`, plus
`MAX(last_seen) WHERE state='failed'` as the last-failure time. The stats row
gains `running`, which `queue_counts` already returns (`db.py:174-175`) but the
template omits.

### Backoff cap

`db.fail`: `delay = min(60 * 2 ** attempts, 3600)`. One line. The retry button
becomes an accelerator rather than the only recovery path, which is the
difference between a defensible design and a human scheduler for an unattended
service.

### CSRF

Form posts rely on `SameSite=strict` (`app.py:77`) and no token. For one
operator on a LAN-bound port that is proportionate; recorded so nobody relaxes
the cookie without knowing it is the only defence.

## Alternatives considered

**Pause by recreating the container.** Rejected: takes the UI down with the
worker, which is the page the operator is clicking.

**Pause as a column.** Rejected: schema bump and migration for one boolean whose
only consumer is one loop. Revisit when it needs an author.

**Retry as an automatic sweep.** Rejected as the *primary* mechanism because it
hides the failure; kept as a bounded safety net via the one-hour cap, which
addresses the reviewer's strongest objection without an unpredictable retry
volume.

**`retried_at` column for queue priority.** Rejected: a schema bump for an
ordering that `next_attempt_at = -1` already gives.

## Risks

- A hard kill mid-tick leaves one row in `running` until the next start, where
  `resume_stuck` clears it (`db.py:199`). A pause cannot cause this: the marker
  is checked before the claim, so an in-flight row always completes.
- Retry-all during a block costs four requests before the loop rests 300 s
  (`BLOCK_PAUSE`, `worker.py:10`). Bounded by the `failed` set, which is 4 today.
- An expired session turns a control POST into a silent `303` to `/login`. The
  badge re-reads the marker, so the page self-corrects.

## Implementation

| File | Change | Why |
|---|---|---|
| `ytk/db.py` | `CLAIM_SQL` states + time clause; `retry()`, `retry_failed()`; `fail()` cap | every claim and every reset lands here |
| `ytk/worker.py` | `paused(cfg)`, guard at top of `_tick`, loop wait | two callers, one guard |
| `ytk/app.py` | `_is_form`, four routes before the mount, `/healthz` fields, export path, `import_url` form branch, queue context | control surface |
| `ytk/templates/queue.html` | control bar, per-row retry as a form button, `running` stat, status line | reachability |
| `ytk/templates/base.html` | one selector so the URL input is not unstyled | `input[type=search]` is the only styled input |
| `tests/test_controls.py` | paused tick, retry resets + claims first, `fetched` claimable, backoff cap | the four silent-failure behaviours |
| `.env.example` | `YTK_EXPORT` | the export override |

`config.py` is **not** touched: the marker path derives from `cfg.db`.

## Acceptance

1. `touch <data>/paused` then `worker._tick(conn, cfg) is None`; remove it and
   the next tick claims a row. (pytest, sub-second — not a 60-second wall clock)
2. `db.retry(conn, id)` then `claim_next(conn)` returns **that id** first, and
   the row reads `state='pending', attempts=0, next_attempt_at=-1,
   fail_stage IS NULL, fail_reason IS NULL`.
3. A `fetched` row with segments and no summary is returned by `claim_next`.
4. `db.fail` at `attempts=10` writes a delay of 3,600 s, not 61,440.
5. `POST /admin/pause` with a session cookie returns 303 and `/healthz` shows
   `paused: true`; the endpoint stays 200.
6. Every control appears in `/queue` as `<form method="post" action="...">`,
   and `POST /import/url` with `url=...` form-encoded returns 303, not 500.
