# controls-design.md — implementation-readiness review

Lens: could a competent engineer implement this from the document alone, with no
decisions left open? Reviewed against the code at HEAD and the live instance at
`192.168.178.40:8499` on 2026-11-02 22:22–22:25 CET.

Severity: BLOCKER = goal undelivered or a schema/contract change the plan omits.
MAJOR = silent behaviour change, unwritable test, or a wrong number the plan is
sized on. NIT = wording or an easily-copied precedent.

## Verified

1. **TRUE — the four "already exist" routes are real, and only `/digest/run` is
   reachable from a browser.** `app.py:178,201,208,257`. `grep -rn 'action=' ytk/templates/`
   returns exactly one POST form (`digest.html:5`, `/digest/run`); `queue.html`
   has none. The document's opening claim is accurate.

2. **TRUE — production has 4 failed rows, not 40 and not 1890.**
   `GET /healthz` five times between 22:22:37 and 22:25:38, byte-identical:
   `{"archived":3,"failed":4,"pending":1890,"ready":57,"running":0,"fetched":0,"no_caption":0,"total":1954}`.
   So `RETRY-ALL` moves **4 rows**, and `1890` (doc:159) is the *pending* count.

3. **TRUE — the production worker claimed nothing for 3 minutes.** Same five
   samples, `running` stayed 0 and `pending` stayed 1890 across 181 s. With
   `YTK_SLEEP≥0.5` and 1890 pending rows, a live worker would have moved at
   least a few. **CANNOT VERIFY** whether this is `YTK_WORKER=0` or a stalled
   loop — both need a credential or an SSH shell I do not have.

4. **TRUE — the backoff the document diagnoses is uncapped in code.**
   `db.py:255-262`: `attempts = … + 1; delay = 60 * 2 ** attempts` with no
   `min()`. attempts=8 → 4.3 h, 10 → 17 h, 12 → 68 h. The document names this
   (doc:22-25) and does not fix it.

5. **TRUE — a retried row does not jump the queue, it ties with it.**
   `db.py:182-193` `CLAIM_SQL` orders by `COALESCE(next_attempt_at,0), watches DESC,
   dwell DESC, first_seen`. Setting `next_attempt_at=NULL` gives the retried row
   the same sort key (0) as all 1890 pending rows, so it is ordered among them by
   watch count and age — not first.

6. **TRUE — `tests/test_controls.py` cannot be written in the existing style
   without an unlisted file.** `ytk.app` calls `load()` at import
   (`app.py:19`); importing it with no env raises
   `SystemExit: missing required env: YTK_SECRET`. `TestClient(app)` without the
   context manager then gives `AttributeError: 'State' object has no attribute
   'conn'`, because the connection is created in `lifespan` (`app.py:26-28`) —
   so an HTTP-level test also starts the real worker against the real
   `YTK_DB`. Every existing test is db-level only (`tests/test_db.py`).

## Findings

### BLOCKER — Retry cannot deliver Goal 2 ("immediately"), and the fix needs a schema change the file list omits

**Claim.** doc:35 promises "Retry one failed video … immediately". The specified
mechanism (doc:108-113, four columns reset, `next_attempt_at=NULL`) delivers
"retry at the same priority as 1890 pending rows".

**Evidence.** `CLAIM_SQL` (`db.py:182-193`) sorts on
`COALESCE(next_attempt_at,0) ASC` first. `NULL` and a past timestamp both sort
to 0, and every pending row is also 0. The tie is then broken by
`watches DESC, dwell DESC, first_seen ASC`. A failed row with no watches — the
common case for a row that died at `summarize` — lands at the *back* of the
1890. At `YTK_SLEEP=8` (`.env.example`) that is ~4.2 h of queue ahead of it,
before any failure. The operator clicks "retry", nothing happens, and nothing
errors. CUJ 2 (doc:61) says the opposite — "worked off in queue order" — which
is also not what the SQL does.

**Fix.** The document must pick one, in the plan, not leave it to the
implementer:
(a) front-of-queue: `next_attempt_at = 0` is *not* enough; add a
`retried_at INTEGER` column and order by `retried_at DESC` — a
`SCHEMA_VERSION` 3→4 bump, a `MIGRATIONS[4]` entry, and `ytk/db.py` in the file
list; or
(b) back-of-queue: accept it and say so in Goal 2 and CUJ 2 ("retry does not
change queue position; it only clears the backoff"), which needs no schema
change and is one sentence.
Choosing (b) is legitimate. Choosing it by accident is what the doc does now.

### MAJOR — The form-post response contract is undefined; implemented literally it shows the operator raw JSON or a 405

**Claim.** doc:94-96 says every control accepts "both a browser form and JSON,
the way `/digest/run` already does". doc:131-133 says "A per-row `retry` link"
and, two lines later, "Every control is a plain `<form method="post">`".

**Evidence.** A link is a GET. `POST /videos/{id}/retry` answers 405 for a GET,
so the literal reading of "link" breaks the page. `digest_run` (`app.py:180-198`)
resolves this by sniffing `content-type`, branching `is_form`, and returning
`RedirectResponse("/digest", 303)`; `/export/md` (`app.py:201-205`) does not —
it returns `{"written": n}` to any caller, including a form, which would strand
the operator on a JSON blob. The document says neither which precedent the four
new routes follow nor what the response body is.

**Fix.** State it: for a form post (content-type starts with
`application/x-www-form-urlencoded` or `multipart/form-data`) return
`303 See Other` with `Location: /queue` (Post/Redirect/Get); for anything else
return JSON `{"ok": true, "state": "pending", "id": "..."}`. The per-row
control is a `<form method="post" action="/videos/{{ id }}/retry">` with a
`<button>`, never an `<a>`. Acceptance 2 depends on that JSON body existing —
see the acceptance finding.

### MAJOR — Retry-all is sized on the wrong number, and its stated block behaviour is wrong twice

**Claim.** doc:59 "Forty rows sit in `failed`"; doc:159 "returns 1890 rows to
pending and the worker re-blocks within a tick, having spent one request";
doc:160 "the block path already backs off 600s".

**Evidence.** Production `failed` = 4, `pending` = 1890 (Verified 2). The 1890
is the pending count, copied into the risk section as if it were the retry
volume. `BLOCK_PAUSE` is **300**, not 600 (`worker.py:10`); 600 s is the
`next_attempt_at` the `Blocked` handler writes for the *one* row
(`worker.py:57-58`) — two different constants, neither described correctly.
And "one request": after a `Blocked`, that row is postponed but the other
retried rows are still pending at priority 0, so the next tick claims another
and blocks again — 4 requests, not one, before the 300 s rest.

**Fix.** Delete both numbers and state the mechanism instead: "retry-all resets
the `failed` set (4 rows today); the worker takes them one at a time, and a
`Blocked` on any of them rests the loop `BLOCK_PAUSE`=300 s while that row
returns in 600 s." If a confirmation step is wanted, size it on the current
`failed` count, not a fixed one.

### MAJOR — The file list is missing `ytk/db.py`, and `cfg.home` does not exist

**Claim.** doc:169-171 assigns the retry to `app.py` and the pause path to
`config.py` ("add `home` (the data dir) … the marker needs a path already
derived there").

**Evidence.** Nothing in `config.py` derives a data directory. `Settings` has
`secret, db, thumbs, bind, sleep`; `_path()` (`config.py:8-13`) resolves a
single file path. "Already derived there" is false. `Path(cfg.db).parent` is
the only source, and the document never says so — nor whether `home` is a new
`YTK_HOME` env var, which would drag `.env.example`, `compose.yaml` and
`Dockerfile` into the diff (all three absent from the table). Separately, the
retry UPDATE has no assigned home; if it is inlined in `app.py` (as
`resummarize` is, `app.py:215`), the specified test "retry clears all four
columns" has no function to call, because the test style is db-level only
(Verified 6). `pyproject.toml` is also unlisted if the test goes HTTP:
`dev = ["pytest>=8", "ruff>=0.6"]` adds no test client dependency, and there is
no `conftest.py` to set `YTK_SECRET`/`YTK_DB` before `import ytk.app`.

**Fix.** Add to the table: `ytk/db.py` — `retry(conn, video_id)` and
`retry_failed(conn) -> int`, one statement each; `config.py` — `home:
str = os.path.dirname(self.db)`, no new env var; and if the tests are
HTTP-level, `tests/conftest.py` + `pyproject.toml` (httpx). Otherwise say
plainly that the tests are db-level and no HTTP client is added.

### MAJOR — The pause pseudocode leaves the one line that matters as prose, and the obvious wrong line freezes the UI

**Claim.** doc:72-78:

```
while not stop.is_set():
    if paused():            # os.path.exists(cfg.home / "paused")
        wait 5s or until stop
        continue
    done = _tick(...)
```

**Evidence.** The structure cannot busy-wait — the wait sits inside the branch,
so a spinning `continue` is not reachable. The risk is the opposite: "wait 5s"
is prose in a code block, and the two natural translations are both wrong.
`time.sleep(5)` blocks the event loop — and the web server shares that loop
(`app.py:35` creates the worker task in the same process), so the UI would hang
for 5 s out of every 5 s precisely while the operator is paused, destroying
CUJ 1's "serving the UI is unaffected throughout". `asyncio.sleep(5)` ignores
`stop`. `worker._loop` already contains the correct idiom twice
(`worker.py:98,103`): `await asyncio.wait_for(stop.wait(), timeout=…)` in a
`try/except TimeoutError`.

**Fix.** Write the block as code, not prose:

```python
while not stop.is_set():
    if paused(cfg):
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            pass
        continue
    try:
        done = await asyncio.to_thread(_tick, conn, cfg)
    ...
```

`paused(cfg)` is `os.path.exists(os.path.join(cfg.home, "paused"))`, sync — a
`stat` is sub-millisecond and must not go through `to_thread`.

### MAJOR — None of the four acceptance criteria is falsifiable as written

**Claim.** doc:176-182 claims four tests, of which criterion 1 is the only one
that touches the worker.

**Evidence and the single-command test:**

1. *"With the marker present, `queue_counts` is unchanged over 60 s."*
   **Testable in one line, but asserts the wrong thing, and currently vacuous.**
   `curl -s -XPOST -H "Authorization: Bearer $YTK_SECRET" http://192.168.178.40:8499/admin/pause && a=$(curl -s http://192.168.178.40:8499/healthz) && sleep 60 && [ "$a" = "$(curl -s http://192.168.178.40:8499/healthz)" ] && echo PASS`
   fails for a correct implementation whenever a row was already claimed: the
   marker is checked *before* a claim, so an in-flight `running` row still
   finishes and `running→ready` changes the counts. It also passes today with
   no implementation at all, because the live worker is idle (Verified 3).
   Fix: precondition on `counts["running"] == 0` and assert
   `pending + failed` unchanged, not the whole dict.
2. *"`POST /videos/{id}/retry` … `state='pending'` and `next_attempt_at IS NULL`
   in the same request."* **Untestable over :8499 as written.** No route reads a
   single row's `state`/`next_attempt_at`; `/healthz` returns aggregate counts
   only, and `/v/{id}` is gated HTML. Even the response readback races the
   worker: `isolation_level=None` (`db.py:96`) means the UPDATE is committed
   immediately on a connection the worker does not share, and a priority-0 row
   can be claimed between the UPDATE and the response. Only reachable via
   `ssh nas sqlite3 /volume1/docker/yt-knowledge/data/ytk.sqlite …`, i.e. not
   against :8499. Fix: assert on the JSON body the route returns
   (see the response-contract finding) and run the check with the marker
   present.
3. *"`/healthz` reports `paused: true` within one tick of the marker
   appearing."* **Testable, tautological.**
   `curl -s -XPOST -H "Authorization: Bearer $YTK_SECRET" http://192.168.178.40:8499/admin/resume; curl -s -XPOST -H "Authorization: Bearer $YTK_SECRET" http://192.168.178.40:8499/admin/pause; curl -s http://192.168.178.40:8499/healthz | grep -q '"paused":true' && echo PASS`
   passes because `/healthz` reads the file directly; it proves nothing about
   the loop. "Within one tick" has no meaning for a value read from disk.
4. *"Every control is reachable with JavaScript disabled."* **Untestable with a
   shell command.** It is a browser property. The nearest proxy,
   `curl -s -b "session=$COOKIE" http://192.168.178.40:8499/queue | grep -c '<form'`,
   needs a session cookie and counts tags, which does not prove the forms
   submit (a form with no `action` posts to `/queue` and 405s). Fix: state it as
   a manual check, or as "every control is a `<form method=post>` with an
   explicit `action`" and assert exactly that string per control.

**Fix.** Rewrite criteria 1-3 as above; move 4 to a manual checklist item.

### MAJOR — Explicit-only retry leaves the actual defect in place, for exactly the operator this service has

The strongest case against doc:150-152, for an unattended NAS:
`db.fail`'s delay is `60 * 2**attempts` with no cap, and `attempts` only ever
increases. That is the *bug*; the retry button is a workaround that makes a
human the scheduler. This service's premise (doc:8) is that nobody is watching
at 02:00 when the LLM endpoint restarts. Under explicit-only, a 20-minute
outage converts a handful of rows into a multi-day or permanent backlog, and
the only signal is a number on a page nobody has open — the document itself
ships no alert (non-goals, doc:41-49). The design also rejects the sweep on the
grounds that "the retry volume is unpredictable during an outage", but volume
is already bounded by the failed-set size, which is 4 today; what is actually
unbounded is the delay. Keep the button, and cap the backoff:
`delay = min(60 * 2 ** attempts, 3600)` — one line in `db.py`, one test, and
then explicit retry is an accelerator rather than the only recovery path. If
the doc keeps explicit-only, it must at least list "no failure alert; rows can
sit failed for days" as an accepted risk, which it does not.

### NIT — "A per-row `retry` link"

doc:131. A link is a GET and the route is POST-only, so the literal
implementation returns 405. Covered by the response-contract MAJOR; the word
should be "button".

### NIT — `POST /queue/retry-failed` accepts an optional `{"state": "failed"}`

doc:103. The only legal value is the route's own name, so the body is either
noise or a filter with unspecified semantics — what does `{"state":"pending"}`
do, 400 or ignore? A browser form posts no JSON at all, so the empty case must
also be legal. Fix: drop the body, or say "unknown values are ignored".

### NIT — The status line has four states and three labels, and "last failure timestamp" has no query

doc:115-119. `paused` and `worker` are independent booleans: marker present +
`YTK_WORKER=0` has no label and no stated precedence. "Plus the last failure
timestamp" names no column and no query, while doc:135-138 promises "No new
query" — that promise covers only the counts. `MAX(last_seen) WHERE state='failed'`
is the one-liner; name it.

### NIT — New routes must be registered above `app.mount("/", mcp_app)`

`app.py:274-276`. Any route appended after the mount is shadowed by the MCP
sub-app and 404s. The file already carries the comment; the plan's "four
routes" row should say "before the mount" so the diff does not land at the
bottom of the file.

### NIT — "Resummarize on a different model" describes something the endpoint cannot do

doc:128. `POST /admin/resummarize` takes no body and reads
`YTK_LLM_MODEL` from the env (`app.py:212`); the model is fixed at container
start (`summarize.py:83`), and Non-goals (doc:44-45) already rejects env changes
without a recreate. The button can only mean "reset rows whose stored model
differs from the current env" — label it that, and say the body is empty.

## Worst finding

**BLOCKER: the retry mechanism cannot produce "immediately" — `next_attempt_at=NULL`
puts a retried row at priority 0, tied with the 1890 pending rows, and
`CLAIM_SQL` then sorts it by watch count, so the operator's click is invisible
for hours.** An engineer will implement the four-column reset exactly as
specified, watch the row sit in `pending`, conclude the UPDATE is broken, spend
the day on the SQL that is not wrong, and then discover the fix needs a column
and a `SCHEMA_VERSION` bump the plan never lists.
