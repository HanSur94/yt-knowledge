# YouTube Watch-History Knowledge Pipe

* **Author:** Hannes
* **Status:** Draft, revised after EGM review round 1
* **Date:** 2026-10-31

--------------------------------------------------------------------------------

## Overview

A self-hosted service that turns YouTube watch history into a searchable local
archive: one row per video, the full transcript, a chapter-aware summary, a
thumbnail, and a record of when and how long it was watched. It exposes that
archive through two live surfaces - a small web UI for browsing and a Model
Context Protocol (MCP) server for any agent that speaks it - plus a file export
into a markdown folder. It makes no authenticated Google API call.

## The Problem

YouTube keeps no usable record of what you watched and gives no way to get one.
The Data API has had no watch-history endpoint since 2016. The result is 2,042 distinct
videos on this machine that exist only as URLs in three browser databases, 1,934
of them past the noise filter, about 150 new ones a month, and all of them
unreachable the moment the browser prunes them (Safari keeps about seven months).

Existing tools solve the fetching half and die at the archive half. A GitHub
sweep on 2026-10-31 (six search queries, 26 repositories verified through the
API) found ten projects that ingest watch history or channel content: nine have
under five stars, and six have commits on their creation day and none after. The
failure mode is a folder of 400 markdown files nobody opens.

## Goals

* Capture every watch event that can still be recovered, from every source listed
  below, without reading a Google account.
* Store the full transcript of every captured video that has a caption track.
* Store a summary per video that answers "what did this video claim" without
  reopening it: sampled against 20 videos, the summary names their central claim.
  Every bullet carries the timestamp of the line that supports it.
* Answer a phrase that occurs only in speech in under a second across 4,000
  videos, and open the video page at that line.
* Survive video deletion: title, channel, thumbnail and transcript stay readable
  after the source video is gone.
* Expose the archive to any agent through MCP, and to any knowledge base through
  a markdown folder.
* Run as one container on the NAS with one SQLite file and no external services
  except an OpenAI-compatible LLM endpoint.
* Produce a weekly digest: what was watched, which channels were new, what the
  week's summaries add up to.

## Non-Goals

* Reading or storing Google credentials. No OAuth, no cookie handling, no
  authenticated scraping.
* Multi-user accounts, sharing, or a hosted service. One household, one secret,
  one container.
* Transcribing audio. Videos without a caption track are recorded as
  `no_caption` and skipped.
* Frame extraction from video files. One thumbnail per video only.
* Vector or embedding search. SQLite FTS5 (full-text search) until it measurably
  fails.
* A chat interface inside the app. The MCP server is the chat interface.
* Reading a user's browser file from the server. Impossible by construction; the
  collector pushes, the server receives.

## Open Questions

* **Name**: `yt-knowledge` is a working title.
* **First knowledge-base target**: which export proves the end-to-end path -
  Obsidian vault, LightRAG, or the markdown folder alone?
* **Digest delivery**: web page only, or also a push through the existing
  notification path?
* **Takeout format**: JSON or HTML for this account. Parser must handle both; the
  JSON path is unverified because it needs a real export.
* **Summary latency**: the dominant cost of the first backfill is unmeasured. The
  first 20 videos will produce the number.

## Critical User Journeys

### CUJ 1: First import on the Mac

* **Given**: The service runs on the NAS. The user has Safari, Arc and Firefox
  history containing 2,207 YouTube visits over 2,042 distinct videos.
* **When**: The user runs `collector.py --push http://nas:8488 --token ...` once.
* **Then**: The server holds one row per distinct video with first-watch and
  last-watch timestamps, dwell where the browser recorded it, and a `state` of
  `pending` for every video that passes the ingest filter. The response reports
  counts for received, merged-into-existing, filtered-short and new.

### CUJ 2: Backfill

* **Given**: 1,934 pending videos.
* **When**: The worker runs unattended overnight.
* **Then**: Each video moves through `fetched` (metadata, transcript, thumbnail,
  sponsor segments) to `ready` (summary and tags). A failure records which stage
  failed and why, and retries resume at that stage rather than re-fetching
  everything. `no_caption` videos never retry. The fetch stage alone takes about 2.2
  hours at the measured 4.08 s per video; summarisation time is added on top.

### CUJ 3: Find the thing from two months ago

* **Given**: A complete archive.
* **When**: The user types `postgres index bloat` into the library search.
* **Then**: The phrase search hits the transcript index, returns the videos whose
  speech contains it, and the matching video page opens scrolled to that line with
  a link to that second on YouTube. Summary-level results appear above
  transcript-level ones.

### CUJ 4: An agent uses the archive

* **Given**: The MCP endpoint is registered in Claude or Codex by URL.
* **When**: The agent calls `search_archive` and then `get_transcript`.
* **Then**: It receives timestamps and source URLs, and never needs the web UI.
  No MCP tool writes.

### CUJ 5: Phone-only viewing survives

* **Given**: Videos watched on the phone never appear in any browser database.
* **When**: The user uploads a Takeout zip to `/import`.
* **Then**: A watch within 90 minutes of an existing watch of the same video is
  merged into it - the earlier timestamp is kept, the larger dwell wins, and the
  source is appended. A watch outside that window is a genuine re-watch and
  becomes its own row. Dwell is simply absent for Takeout rows.

## Technical Plan (Architecture)

```text
   user's machine                     NAS container (one process)
   ──────────────                     ───────────────────────────
   collector.py ──POST /import──┐
   Takeout zip ──POST /import───┼──> ingest.py ──> SQLite ──> worker ──> fetch.py
   pasted URL  ──POST /import───┘                    │                     │
                                                     │              summarize.py
                                                     │                     │
                     web UI (Jinja+HTMX) <───────────┤                     │
                     POST /export/md     <───────────┤<────────────────────┘
                     /mcp (mounted ASGI) <───────────┘
```

Six decisions carry the design:

1. **Ingest is a push API.** The server never reads a browser file, so the same
   endpoint serves the only user and any later one, and the collector is the only
   platform-specific code.
2. **One process.** The web app, the worker loop and the MCP endpoint share one
   ASGI app and one database connection pool. The MCP endpoint is served at `/mcp` over
   streamable HTTP: the inner app is mounted at `/` with
   `streamable_http_path="/mcp"`, because mounting it at `/mcp` with a `/` path
   answers 307 and then 404. A stdio process is rejected outright - it cannot
   safely open a WAL SQLite file over a network share.
3. **One state column instead of a job queue.** `videos.state` plus
   `videos.attempts` and `videos.fail_stage`. The worker claims rows with a single
   `UPDATE ... WHERE state='pending' RETURNING`. No Celery, no Redis, no broker.
4. **Transcripts come from `youtube-transcript-api`, not `yt-dlp`.** Measured:
   `yt-dlp --write-subs` returned HTTP 429 on four of six requests and still failed
   after a 25-second cooldown. `youtube-transcript-api` returned 14 of 14 at 1.09 s
   each. Metadata still comes from `yt-dlp --dump-json`, which is not throttled.
5. **Search is two indexes, because the goals need two different ones.** A phrase
   that exists only in speech has to hit the transcript, so `segments_fts` indexes
   every transcript line; `search_docs_fts` indexes title, channel, headline and
   bullets for browsing. Both are FTS5 external-content tables kept in sync by three triggers each, so
   regenerating a summary cannot leave a stale row. A search runs both, ranks
   each with FTS5 `bm25()`, and returns summary-level hits above transcript-level
   ones with duplicates collapsed to the best-scoring row per video.
6. **The thumbnail is one static URL.** `https://i.ytimg.com/vi/<id>/maxresdefault.jpg`
   is fetched directly; no `yt-dlp` call, no video download.

### Search indexes

`search_docs` exists for one reason: an FTS row needs a stable parent key, and one
row per video cannot be assembled by triggers spanning `videos` and `summaries`.
The worker writes `search_docs` in the same transaction that sets `state='ready'`,
so one table feeds one index with three triggers. `video_id` is `UNIQUE`, so the
key a hit returns is the video it belongs to and the "best row per video" rule in
a search is a `GROUP BY`, not a guess. Because these are external-content tables,
the index must be primed with `INSERT INTO <fts>(<fts>) VALUES('rebuild')` once at
`user_version` creation and again after any migration that recreates a table.

### Data model

```text
videos      id TEXT PK            -- 11-char video id, validated
            title, channel, channel_id, duration_s, published_at, language,
            desc, thumb_path
            state TEXT            -- pending|running|fetched|ready|failed
                                  -- |no_caption|archived
            attempts INTEGER DEFAULT 0
            next_attempt_at INTEGER   -- epoch seconds; claim gate for retries
            fail_stage TEXT       -- fetch|summarize
            fail_reason TEXT
            first_seen, last_seen

watches     video_id, watched_at, dwell_s, sources
            UNIQUE(video_id, watched_at)

segments    id INTEGER PK, video_id, start_s, text
            UNIQUE(video_id, start_s)          -- retry replaces, never duplicates
skips       video_id, start_s, end_s, category, PK(video_id, start_s)
chapters    video_id, start_s, title,          PK(video_id, start_s)

summaries   video_id, lang, headline, bullets_json, tags_json, keywords_json,
            short_en, model, created_at
            UNIQUE(video_id, lang)

search_docs id INTEGER PK, video_id TEXT UNIQUE            -- exactly one row per video
            title, channel, headline, bullets, short_en
search_docs_fts FTS5(title, channel, headline, bullets, short_en)
            content='search_docs', content_rowid='id'
segments_fts FTS5(text) content='segments', content_rowid='id'
```

`seen_count` is deliberately absent: it is `COUNT(*)` over `watches` and a stored
copy would be a second source of truth that drifts. The corpus is mixed-language: the three
longest watches are German, the rest mostly English. An earlier 2,776 English /
981 German split came from a library that double-counted rows and is withdrawn;
`short_en` exists so a German video stays findable from an English query whatever
the split turns out to be.

### Filters, in two stages

A watch rejected by the ingest rule is never written: it is counted and dropped,
and the response reports it. The alternative - inserting every visit as `archived`
- would make the table the size of the browsing history rather than the archive.



A single ingest-time filter cannot work, because the "is this a Short" and "is
this long enough to be worth keeping" questions need `duration_s`, which only
arrives with the metadata fetch. Two stages:

* **Ingest**: keep the watch if `dwell_s >= 60`, or if `dwell_s` is absent (Takeout
  and Safari never record one) and the URL is not a `/shorts/` path. Shorts are
  detected from the URL, not from duration. There is no upper bound on keeping: a
  two-hour podcast watched to the end is exactly the material this archive is for.
  A dwell longer than 4 hours is stored but flagged, because the measured maximum
  was 56 hours from a tab left open over a weekend.
* **After fetch**: if `duration_s < 120`, set `state='archived'` and skip the
  summary. Archived rows are never deleted and never re-summarised, so a re-import
  cannot resurrect them, but they stay searchable.

### State machine

```text
pending ──fetch ok──> fetched ──summary ok──> ready
   │                     │
   │                     └──summary fail──> failed (fail_stage=summarize)
   └──fetch fail────────────────────────> failed (fail_stage=fetch)
   └──no caption track──────────────────> no_caption
   └──duration < 120s───────────────────> archived
failed ──attempts < 3──> backed off, then retried at fail_stage
ready  ──POST /admin/resummarize──> fetched
```

`failed` is not terminal and not one bucket. `next_attempt_at` is what makes the
retry real across restarts: the claim query is
`UPDATE videos SET state='pending' WHERE id = (SELECT id FROM videos WHERE
state='pending' OR (state='failed' AND next_attempt_at <= ?) ORDER BY
COALESCE(next_attempt_at,0), first_seen LIMIT 1) RETURNING *`, with the backoff
set to `now + 60 * 2^attempts` on failure. `fail_stage` decides where the retry
resumes, so a summary failure never re-downloads a transcript. `attempts` caps the
loop at three, after which the row stays `failed` and appears on `/queue` with its
reason. One worker, one row in flight; there is no concurrency setting and no
lease, because `UPDATE ... RETURNING` on one connection is already exclusive.

**Terminal versus retryable failures.** `no_caption` is terminal and set only for
`TranscriptsDisabled`, `NoTranscriptFound` and `VideoUnavailable`. `IpBlocked`,
`RequestBlocked`, `PoTokenRequired`, HTTP 429, timeouts and 5xx are retryable and
land in `failed(fail_stage='fetch')`. Getting this backwards marks 4,000 videos
permanently `no_caption` on the first blocked night, so it is the one rule the
worker may not improvise.

### API surface

```text
POST /login                 set session cookie from the shared secret
POST /logout
GET  /                      library: search, channel and date filters, pagination
GET  /v/{id}                video: summary, chapters, transcript, thumbnail, watch log
GET  /thumb/{id}.jpg        thumbnail, served from YTK_THUMBS behind the same gate
GET  /queue                 counts by state, last 50 failures with stage and reason
GET  /digest                latest weekly digest
GET  /digest?week=YYYY-Www  one ISO week, defaults to the newest stored
POST /digest/run            generate now; body {"week","force"}  token
POST /export/md             write markdown tree to the volume    token
POST /import                JSONL or multipart Takeout zip      token
POST /import/url            one pasted URL                       token
POST /admin/resummarize     reset ready rows whose model != current
POST /logout
GET  /healthz               no auth, returns {"ok":true} and row counts
/mcp                        streamable HTTP MCP, read-only tools, bearer OR session
```

Everything except `/healthz` and `/login` requires either the session cookie or,
for `/import` and `/mcp`, an `Authorization: Bearer <YTK_SECRET>` header. An MCP
client registered by URL cannot fill in an HTML login form, so without the bearer
path CUJ 4 is unperformable. The check is one app-level middleware that returns
`RedirectResponse("/login", 303)` for browser paths and
`JSONResponse({"error":"unauthorized"}, 401)` for `/import` and `/mcp` - it returns
a response rather than raising, because `HTTPException` raised inside
`BaseHTTPMiddleware` is not converted and surfaces as a 500, and because router
`dependencies=[...]` do not run for a mounted sub-app at all. The side-effecting
export and import routes are POST, not GET.

MCP tools: `search_archive(query, channel, since)`, `get_video(id)`,
`get_transcript(id, around_ts)`, `list_channel(name)`, `recent(days)`. No write
tool exists on the MCP surface; digest generation is a web action.

### Configuration

Ten environment variables, one `.env` file, no config framework. `Settings`
holds five; the LLM trio is read at the call site because a service that cannot
summarise can still serve, import and export.

```text
YTK_SECRET          session signing key and the /import bearer token (required)
YTK_LLM_BASE        OpenAI-compatible base URL, e.g. http://192.168.178.40:8080/v1
YTK_LLM_MODEL       model name sent in the request body
YTK_LLM_KEY         optional bearer for that endpoint
YTK_DB              SQLite path        default data/ytk.sqlite, root-anchored
YTK_THUMBS          thumbnail directory              default data/thumbs
YTK_EXPORT          markdown export root             default data/export
YTK_BIND            host:port, split on the last ':'  default 127.0.0.1:8488
YTK_SLEEP           seconds between upstream calls   default 0.5
YTK_WORKER          "0" disables the background worker, for tests and exports
```

Only `YTK_SECRET` is enforced at startup (`ytk/config.py`); it guards every
route. `YTK_LLM_BASE` and `YTK_LLM_MODEL` are required to summarise or generate a
digest, and their absence is a 503 from those routes rather than a refusal to
boot. Relative paths resolve against the project root, not the shell's working
directory: anchored defaults stop a service started from `~` from creating an
empty second database. `uvicorn.run` takes `host` and `port` as separate arguments, so
`serve` splits `YTK_BIND` on the last colon. The default is `0.0.0.0` because a
bridge-networked container does not own the NAS LAN address - the LAN-only rule is
enforced by the compose port mapping, not by the bind address.

### Digest

One row per ISO week in a `digests` table: `week TEXT PK, generated_at, html,
markdown, model, video_count`. A digest is generated on demand only - `POST
/digest/run`, or the button on the page - never on a timer, so nothing calls the
LLM while the archive is idle. Generation is idempotent per ISO week: an existing
row is returned untouched unless `force` is set, so a restart cannot produce two
pages for the same seven days. The week boundary is Monday 00:00 Berlin time, and
`?week=YYYY-Www` reads any past week that was generated.

The digest covers videos **watched** that week that already have a summary. It
reports how many that was, because a week whose videos are still `pending` for
transcripts produces no digest at all rather than an empty one.

### Summary prompt shape

Input: title, channel, duration, chapters if any, and the transcript with sponsor,
intro and self-promo spans removed. Output: JSON with `headline` (one sentence),
`bullets` (5-9 items, each carrying the timestamp of the supporting line), `tags`
(3-6), `lang`, `short_en` (one English sentence). Summaries are written in the
video's language; `short_en` is the cross-language findability field.

## Alternatives Considered

### Alternative A: Fork chronovista

* **Pros**: Complete and working. Takeout import, 1.46M transcript segments,
  timestamp search, deleted-video recovery via the Wayback Machine, React dashboard.
* **Cons**: AGPL-3.0. PostgreSQL 15 plus React plus 81 REST endpoints for one user.
  Its data model is built for textual research and tag canonicalisation, not for
  "what did I watch and what did it say".
* **Reason for Rejection**: The licence forecloses any later publication, and the
  operational weight is several times the workload it serves. Read it, do not fork
  it.

### Alternative B: Authenticated innertube scraping of `/feed/history`

* **Pros**: The only path that sees phone and TV viewing without a manual export.
  YouTube.js exposes `getHistory()`. No local script, no Full Disk Access.
* **Cons**: Needs a live Google session cookie and SAPISIDHASH request signing.
  Unauthenticated, the page returns HTTP 200 with zero video renderers and
  `Der Wiedergabeverlauf wird ohne Anmeldung nicht angezeigt`. Sessions expire
  silently, and the failure looks like "no new videos" rather than "logged out".
* **Reason for Rejection**: It is a real trade, not a settled win. Takeout covers
  the same phone gap with no credential stored and no silent expiry; B is faster
  and more complete but puts a Google session inside a NAS container. If Phone
  viewing turns out to dominate the archive, this decision is the one to revisit,
  and the ingest API does not change if it is.

### Alternative C: Takeout only

* **Pros**: One code path, works for every platform and every user, no Full Disk
  Access, no local script.
* **Cons**: Manual export, links expire in about seven days, no dwell time ever, and
  the export has no duration or position - a lecture abandoned at minute 40 is
  indistinguishable from one watched to the end.
* **Reason for Rejection**: Loses the only "did I actually watch this" signal that
  exists, and captures nothing between exports.

### Alternative D: Next.js + Postgres

* **Pros**: Familiar UI tooling, a real client-side state model, and a path to more
  than one user without a rewrite.
* **Cons**: A Node build chain and a database server on the NAS for four screens and
  one user. The whole fetch layer is already Python.
* **Reason for Rejection**: Doubles the deploy surface to buy nothing at this size.
  HTMX is replaceable screen by screen if client state ever matters.

## Non-Functional Requirements

* **Privacy**: Browser history is the most sensitive file on the machine. The
  collector reads it locally, pushes only YouTube watch rows, and stores no other
  browsing data. The server holds no Google credentials and makes no authenticated Google
  API call. It does call `yt-dlp`, `i.ytimg.com` and the public SponsorBlock
  endpoint, all anonymously. Transcripts are public data. Summaries go to the local OmniRoute
  endpoint, not to a hosted model, unless that endpoint is reconfigured.
* **Access control**: The whole app sits behind one shared secret, not just
  `/import`. A single password field sets an `HttpOnly`, `SameSite=Strict` session
  cookie; every route except `/healthz` and the login form requires it, including
  the library, the video pages, the markdown export and the mounted MCP endpoint.
  Secrets are compared with `hmac.compare_digest`. This is one household, so one
  secret and no user table; a second user means a second container, not a role
  system.
* **Security**: `/import` additionally requires a bearer token for the collector
  path. The service binds to the LAN address, not `0.0.0.0`. Thumbnails are stored
  by video id, never by a caller-supplied path, and video ids are validated against
  `^[A-Za-z0-9_-]{11}$` before any outbound request or filesystem write.
* **Performance**: The fetch stage is measured at 4.08 s per video (metadata
  2.17 s, transcript 1.09 s, thumbnail 0.20 s, SponsorBlock 0.12 s, 0.5 s grace),
  so 1,934 videos take about 2.2 hours. Summarisation latency is unmeasured and
  dominates the total; the first backfill is an overnight run. SQLite in WAL
  (write-ahead log) mode; FTS5 phrase query target under 50 ms over ~800,000
  segment rows.
* **Storage**: Transcript text about 30 KB per video, 1,934 videos ≈ 58 MB.
  Thumbnails ≈ 25 KB each ≈ 48 MB. Segment counts measured at 154-205 per video
  across 14 sampled videos, so roughly 360,000 rows ≈ 40 MB indexed. The volume
  stays under 1 GB for the current corpus.
* **Time**: Every timestamp is stored as UTC ISO-8601 in SQLite and rendered in
  Europe/Berlin in the UI. The weekly digest boundary is Monday 00:00 Berlin time.
* **Rollback**: One volume and one container. Stop, restore the previous SQLite
  file, start. No migration runs without a `PRAGMA user_version` check. An older file is
  migrated in place step by step through the `MIGRATIONS` map; a newer file
  (written by a later build) is refused with both versions printed, because the
  only copy of the archive must not be written by code that does not know its
  schema.

## Detailed Implementation Plan

Phases are ordered so something is runnable after each one, and every phase ends
with a check that can fail. Phase 1 already contains the route its own check
calls; nothing is verified against a route a later phase owns.

**Layout as built.** The plan below was written as ten flat modules; the code that
exists is a package plus three scripts. Read the entries that follow as intent,
and this as the tree:

```text
ytk/app.py          routes, auth middleware, lifespan, worker start, CLI
ytk/config.py       Settings, root-anchored paths
ytk/db.py           schema, migrations, claim, add_watch, search
ytk/ingest.py       JSONL, Takeout JSON+HTML, URL, ingress filter
ytk/collector.py    browser history readers (runs on the user's machine)
ytk/fetch.py        metadata, transcript, SponsorBlock, thumbnail
ytk/summarize.py    prompt, JSON parse, OpenAI-compatible call
ytk/worker.py       the tick
ytk/mcp_server.py   MCPServer + streamable HTTP app
ytk/export.py       markdown tree writer, called by the route and the script
ytk/digest.py       weekly digest
ytk/templates/      base, login, library, video, queue, search, digest
scripts/            backfill.py, export.py, summarize_all.py
tests/              test_parse.py, test_db.py
```

Two files the plan never names carry working functionality: `config.py` and
`worker.py`. Three scripts do too: `backfill.py` (the resumable crawl),
`export.py` (the CLI wrapper over `ytk/export.py`) and `summarize_all.py` (the
one-shot pass over already-fetched rows).

### Phase 0: Container

#### 0. `Dockerfile` and `compose.yaml` (Create)

* **Type / Scope**: Deployment artifacts. The Goal says one container on the NAS,
  so these ship in phase 0 rather than at the end.
* **Proposed Changes**: `Dockerfile` on `python:3.12-slim`, non-root user, pinned through the lock file,
  `pip install --no-cache-dir .` so the `ytk` console script exists, one volume at
  `/data`, `CMD ["ytk","serve"]`. No `ffmpeg` (no frame extraction) and no
  `browser-history` (no browser in the container). `HEALTHCHECK` runs
  `python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8488/healthz')"`
  because the slim image has no `curl`. `compose.yaml` maps
  `8488:8488`, mounts `./data:/data`, and reads `.env`. No other services.

### Phase 1: Service skeleton, collector and ingest (no network beyond the LAN)

Phase 1 ships `db.py`, `ingest.py`, `collector.py` and the `app.py` routes
`POST /login`, `GET /`, `POST /import`, `GET /queue`, `GET /healthz`, with no
worker running. Check: `docker compose up` serves the login page, `collector.py
--dry-run` prints its counts, `--push` moves them into SQLite, and `/queue` shows
the same numbers. Phase 2 adds fetch, summarize and the worker to an app that
already runs.

#### 1. `pyproject.toml` (Create)

* **Type / Scope**: Python packaging.
* **Proposed Changes**: Python 3.12. Runtime dependencies: `fastapi`, `uvicorn`,
  `jinja2`, `httpx`, `itsdangerous` (required by Starlette's `SessionMiddleware`
  and not pulled by `fastapi`), `python-multipart`, `youtube-transcript-api`,
  `yt-dlp`, `mcp>=2,<3`. `browser-history` is a separate `[collector]` extra and is
  never installed in the container, which has no browser. Dev extra: `pytest`,
  `ruff`. No ORM, no migration framework. Console script `ytk`: `ytk q …` for
  offline queries, anything else starts the server. The collector runs as
  `python -m ytk.collector` on the user's machine, never in the container.

#### 2. `db.py` (Create)

* **Type / Scope**: Data layer.
* **Proposed Changes**: `connect(path)` sets `journal_mode=WAL`, `synchronous=NORMAL`,
  `foreign_keys=ON`, row factory. `init(conn)` creates the seven tables, the two
  FTS5 virtual tables and six sync triggers (insert, update, delete on each of
  `search_docs` and `segments`), guarded by `PRAGMA user_version`
  starting at 1. Helpers: `upsert_video(rec)`, `add_watch(video_id, at, dwell, src)`
  implementing the 90-minute merge, `set_state(video_id, state, **fields)`,
  `claim_next(limit)` using `UPDATE ... RETURNING`, `search(q, filters)`.

#### 3. `ingest.py` (Create)

* **Type / Scope**: Input adapters and filtering.
* **Proposed Changes**: `parse_jsonl(body)`, `parse_takeout(zip_bytes)` reading
  `Takeout/YouTube and YouTube Music/history/watch-history.json` with an HTML
  fallback for `watch-history.html`, and `parse_url(s)`. All return
  `[{video_id, title, channel, watched_at, dwell_s, source}]`. Video id
  extraction for `watch?v=`, `youtu.be/`, `shorts/`, `embed/`, with the ID regex
  from the security section applied before anything else. `ingest_filter(row)`
  returns `(keep, reason)` implementing the ingest-stage rule only; the duration
  rule lives in the worker because it needs metadata.

#### 4. `collector.py` (Create)

* **Type / Scope**: Client-side script, runs on the user's machine, never in the
  container.
* **Proposed Changes**: `browser_history.get_history()` for coverage across Chrome,
  Edge, Brave, Arc, Firefox and Safari, then a direct read of Chromium
  `visits.visit_duration` from a temporary copy of the locked database to attach
  dwell. Filters to YouTube watch URLs before sending. Posts JSONL in batches of
  500. `--dry-run` prints counts without sending and is the first thing to run.

  **Wire contract**, implemented on both sides: `POST /import`,
  `Content-Type: application/x-ndjson`, `Authorization: Bearer <YTK_SECRET>`, one
  object per line:
  `{"video_id": "<11 chars>", "title": str|null, "channel": str|null,
  "watched_at": "<UTC ISO-8601 with Z>", "dwell_s": int|null,
  "source": "safari"|"arc"|"chrome"|"firefox"|"takeout"|"url"}`.
  The collector converts each browser's own epoch (Chromium microseconds since
  1601, Safari seconds since 2001, Firefox microseconds since 1970) to that single
  form; `dwell_s` is `visit_duration / 1_000_000`, rounded. One handler serves the
  route: it branches on `request.headers["content-type"].startswith(
  "multipart/form-data")`, using `await request.form()` for a Takeout zip and
  `await request.body()` otherwise. Two handlers on one path is not possible in
  FastAPI - measured, both register and a multipart upload reaches whichever was
  declared first. Response: `{"received":n,"new":n,"merged":n,"rejected":n}` where
  `received` counts lines and `rejected` counts ingest-filter drops.

### Phase 2: Fetch and summarize

#### 5. `fetch.py` (Create)

* **Type / Scope**: Network adapters.
* **Proposed Changes**: `metadata(video_id)` shelling `yt-dlp --dump-json
  --skip-download`, parsing duration, channel, channel_id, publish date, chapters,
  language and description. `transcript(video_id)` via `YouTubeTranscriptApi().fetch()`, after
  `DELETE FROM segments WHERE video_id=?` so a shorter refetch cannot leave a
  stale tail, then plain `INSERT`. Never `INSERT OR REPLACE`: measured on SQLite
  3.41, a REPLACE conflict fires the delete trigger only when
  `recursive_triggers` is on, and without it `MATCH` returned two hits for one
  stored segment. `connect()` also sets `PRAGMA recursive_triggers=ON` as belt and
  braces. Raises `NoCaption` per the taxonomy above.
  `sponsors(video_id)` calling the public SponsorBlock endpoint and treating 404 as
  an empty list. `thumbnail(video_id, dir)` fetching `maxresdefault.jpg` with
  `hqdefault` fallback. Module constants `SLEEP = 0.5` and `MAX_ATTEMPTS = 3` as
  the only tuning knobs.

#### 6. `summarize.py` (Create)

* **Type / Scope**: LLM adapter and prompt.
* **Proposed Changes**: `build_prompt(video, segments, chapters, skips)` dropping
  sponsor spans, capping at roughly 12k tokens by sampling the opening and closing
  thirds of long transcripts, and stating the JSON contract. `summarize()` posts to
  the configured OpenAI-compatible base URL. `digest(rows)` builds the weekly page
  from `ready` summaries. One retry on malformed JSON, then
  `set_state(state='failed', fail_stage='summarize')` with the raw response stored.

#### 7. `app.py` (Create)

* **Type / Scope**: HTTP service, worker loop, auth.
* **Proposed Changes**: FastAPI app with the routes above. Auth is one app-level
  middleware, not router dependencies: a dependency never runs for a mounted
  sub-app, and `HTTPException` raised inside `BaseHTTPMiddleware` surfaces as 500
  rather than 401 (both measured). The middleware reads the session cookie or an
  `Authorization: Bearer` header, and returns a response for every unauthorised
  path except `/healthz` and `/login`.
  `lifespan` does two things: `async with mcp.session_manager.run():` around the
  yield, and one worker task. One tick, in this order and nothing else:
  (1) `claim_next()`; `None` means `await asyncio.sleep(5)` and tick again;
  (2) if `segments` already exist for the row, skip to step 6;
  (3) `metadata()` in `asyncio.to_thread` with a 60 s timeout, writing title,
  channel, channel_id, duration, published_at, language, desc, chapters;
  `yt-dlp` returns `upload_date` as `YYYYMMDD`, so it is parsed, not stored raw,
  and `chapters` is `None` for a video without markers;
  (4) if `duration_s < 120`, set `state='archived'` and end the tick;
  (5) `transcript()`, `sponsors()`, `thumbnail()`, each in `asyncio.to_thread`,
  each sleeping `YTK_SLEEP`; `NoCaption` sets `state='no_caption'` and ends the
  tick;
  (6) `state='fetched'`, commit;
  (7) `summarize()`, then write `summaries` and `search_docs` and set
  `state='ready'` in one transaction;
  (8) any other exception sets `state='failed'`, `fail_stage`, `fail_reason`,
  `attempts+1`, `next_attempt_at = now + 60 * 2**attempts`.
  A thumbnail failure is logged and does not fail the video. Every blocking call
  goes through `to_thread`: `yt-dlp` shells out and `sqlite3` is synchronous, so a
  direct call would stall every request. Jinja templates render server-side with
  HTMX attributes for search and pagination.

  `POST /export/md` writes a tree and returns a count. Layout
  `<channel-slug>/<published-date>-<title-slug>.md`: both slugs lowercased, German
  umlauts folded to ASCII, non-alphanumerics collapsed to single hyphens, capped at
  60 characters; an empty slug becomes the video id and a collision appends
  `-<video_id[0:6]>`. The date is `published_at`. Fixed frontmatter keys: `title`,
  `channel`, `url`, `published`, `duration_s`, `tags`, `watched_last`,
  `watched_count`, `summary`. Body: `## Summary`, `## Key points` as `- ` lines
  whose timestamps link to `https://youtu.be/<id>?t=<sec>`, then `## Transcript`
  with `[mm:ss]` prefixes. `registry.md` is one table row per exported video,
  regenerated in full. The export runs synchronously; a few seconds for 1,934
  files is fine, and anything slower moves to the worker rather than to a
  different design.

#### 8. `mcp_server.py` (Create)

* **Type / Scope**: MCP surface. Verified against `mcp` 2.3.0 on this machine.
* **Proposed Changes**: `from mcp.server import MCPServer` - the class was renamed
  from `FastMCP` in the 2.x line and `from mcp.server.fastmcp import FastMCP` is a
  `ModuleNotFoundError` on an unpinned install. Build the Starlette app at module
  level:
  `inner = mcp.streamable_http_app(streamable_http_path="/",
  transport_security=TransportSecuritySettings(allowed_hosts=["<host>:8488"],
  allowed_origins=["*"]))`, mounted by `app.py` at `"/"` after every FastAPI route
  is registered. Three traps, all measured: mounting at `/mcp` serves `/mcp/mcp`
  and 307-redirects the bare path to a 404; a mounted sub-app's lifespan never
  runs, so `app.py`'s lifespan must wrap its yield in
  `async with mcp.session_manager.run():`; and DNS-rebinding protection defaults
  to rejecting the NAS hostname with `421 Invalid Host header` unless
  `allowed_hosts` names it. No write tool, asserted by a test over the registered
  tool names against a fixed allowlist.

#### 9. `templates/` (Create)

* **Type / Scope**: Jinja2 templates, six files plus one stylesheet.
* **Proposed Changes**: `base.html`, `login.html`, `library.html`, `video.html`,
  `queue.html`, `search.html`, `digest.html`. The stylesheet is inline in
  `base.html`; there is no `static/` mount, so nothing bypasses the auth gate.
  No build step, no client framework.

### Phase 3: Verification

#### 10. `tests/test_parsers.py` and `tests/test_db.py` (Create)

* **Type / Scope**: The only tests. Network-free.
* **Proposed Changes**: `test_parsers.py` asserts video id extraction for all four
  URL forms and rejection of a malformed id; Takeout JSON rows mapping; the HTML
  fallback on a two-row fixture; the ingest filter accepting exactly 60 s and
  rejecting 59 s and a `/shorts/` URL; sponsor-span removal cutting exactly the
  overlapping caption lines. `test_db.py` asserts the 90-minute merge keeps the
  earlier timestamp and the larger dwell and does not create a second row, that a
  watch outside the window does create one, that a rejected refetch of segments
  leaves the same row count, that `claim_next` cannot hand the same row to two
  callers, and that regenerating a summary updates `search_docs_fts` rather than
  duplicating it. Runs against a temp file database.

### Deliberately left to implementation

Zero-Decision cannot mean every line is pre-written, or the plan would be the
code. These are handed to the coder on purpose, and none of them changes a
boundary, a schema or an external interface: the Jinja variable names and the six
template layouts; the CSS; the prompt wording and how a partial caption is
trimmed at the token cap; HTTP timeout and retry constants inside `summarize()`;
the login field name and cookie `max_age`; log format and level; the `pytest`
fixtures. Everything that would cost a rewrite - the state machine, the retry
column, the index keys, the MCP mount recipe, the auth mechanism, the wire format
and the export layout - is fixed above.

### Verification Plan

* **Build**: `pip install -e .` then `ytk serve --help` returns usage.
* **Unit tests**: `pytest tests/ -q`. Network-free.
* **Lint**: `ruff check .` and `ruff format --check .`.
* **Phase 1 check**: `collector.py --dry-run` prints 2,183 visits and 2,042
  distinct videos. Then `--push`; the response reports `received` = 2,183 and
  `new + merged + rejected` = 2,183, and the `/queue` total equals `new`. The
  rejection count exists only in that response, so the check compares the response
  to the dry run, never the page to the dry run.
* **Phase 2 check**: run the worker on 20 videos first. Confirm segment counts are
  in the measured 154-205 range, that the thumbnail file exists, that a summary
  bullet's timestamp lands on the sentence it claims, and record the summary
  latency that Open Questions still lists as unknown.
* **Search check**: pick a phrase that appears only in speech in a known video,
  search it, confirm it is found and that the link opens that second.
* **MCP check**: register `/mcp` by URL in one agent, ask a question whose answer
  is a timestamp, confirm the tool list matches the allowlist and that no write
  tool is exposed.
* **Acceptance**: a phrase known to be in a specific video is found by searching
  the library in under one second.

--------------------------------------------------------------------------------

*Drafted with EGM*


--------------------------------------------------------------------------------

## Phase 1 result (implemented 2026-11-01)

Built and run: `pyproject.toml`, `ytk/{config,db,ingest,collector,app}.py`, four
templates, `Dockerfile`, `compose.yaml`, two test files. 17 tests pass, `ruff
check` clean, service serves and accepts a real push.

```text
collector --dry-run   visits 2183   distinct 2042   with_dwell 688
                      span 2025-08-02 -> 2026-10-31
push response         received 2183 = new 1968 + merged 60 + rejected 155
database              videos 1934   watches 1968   titled 1934   with_dwell 528
```

Three corrections this phase forced on the document above:

1. **`browser-history` was dropped.** It reported every timestamp one hour late
   (delta exactly 3600 s, reproduced on three rows) and inflated the corpus to
   4,975 visits / 4,253 videos, roughly double the truth. The collector now
   queries the three browser schemas directly - Chromium microseconds since 1601,
   Firefox microseconds since 1970, Safari seconds since 2001 - in about 60 lines
   with no dependency. `with_dwell` proves the direct path works: 688 Chromium
   visits carry a duration where the library surfaced 62.
2. **Safari's database is not under `Application Support`.** It is
   `~/Library/Safari/History.db`. The adapter's existence guard swallowed the
   wrong path and silently contributed zero rows, which is why the first dry run
   printed 709 visits instead of 2,183.
3. **The noise filter removes 155 of 2,183 visits** and 108 of 2,042 distinct
   videos - the under-60-second clicks. The largest stored dwell is 56 hours, from
   a backgrounded tab: the outlier the over-4-hours flag exists for.

Left for Phase 2: the worker, transcript fetch, summaries, MCP surface.

--------------------------------------------------------------------------------

## Phase 2 result (implemented 2026-11-01)

Added `ytk/fetch.py`, `ytk/summarize.py`, `ytk/worker.py`, a video page, a search
page, a gated thumbnail route, and `running` as a schema state (v2) with a
startup sweep that returns interrupted rows to `pending`. 17 tests pass, `ruff`
clean.

Verified against real data:

```text
fetch, 4 videos     metadata 2.2 s, chapters 0-17, transcripts 329-2595 lines,
                    SponsorBlock 0-2 spans, thumbnails 4/4
worker, ~200 s      21 fetched, 3 failed, ~7 rows/min
search              "confusing acquisition" -> 1 transcript hit at 00:01,
                    "Omaha Beach" -> 1 hit
video page          2,595 transcript lines rendered, watch log, no summary yet
```

Four things this phase proved:

1. **`sqlite3` connections are thread-bound.** The worker runs its tick through
   `asyncio.to_thread`, so the connection created in `lifespan` raised
   `ProgrammingError: SQLite objects created in a thread can only be used in that
   same thread` on every tick, and the loop swallowed it into a 5-second sleep.
   The queue looked idle rather than broken. The worker now opens its own
   connection with `check_same_thread=False`; ticks are awaited one at a time, so
   it never has two concurrent users.
2. **`youtube-transcript-api` blocks the IP after about 20 videos.** Three rows
   failed with `IpBlocked` and were retried per the taxonomy rather than marked
   `no_caption` - the distinction the review insisted on. At ~150 rows an hour
   this is the real throughput limit, not the 4.08 s per video; either the sleep
   goes up or the backfill runs in bursts.
3. **A claim state is not optional.** Claiming with `UPDATE ... SET state='fetched'`
   would have stranded every interrupted row; `running` plus `resume_stuck()` at
   startup is three lines and makes the state machine honest.
4. **`i.ytimg.com` returns transient 404s** under light load - the first
   thumbnail fetch loop asked for `.../vi/{}/maxresdefault.jpg` without
   formatting the id, then the CDN 404'd on a correct URL too. Both are fixed;
   the fallback and one delayed retry cover the second.

Still open: summaries need an LLM endpoint (`YTK_LLM_BASE`). OmniRoute is not
running on the NAS, so the worker currently stops at `fetched` by design and the
summary path is covered only by `summarize.parse()` unit behaviour.

--------------------------------------------------------------------------------

## Phase 3 result (implemented 2026-11-01)

Summaries, keywords, the markdown export and the MCP surface are in. 17 tests
pass, `ruff` clean.

**LLM endpoint.** The gateway at `192.168.178.40:8642` works as an
OpenAI-compatible endpoint but carries the whole agent system prompt: a
"reply ok" request reported `prompt_tokens: 20886`. It still summarises correctly
and in 5.8-25 s, so it is usable at one call per video; the cost is input tokens,
not correctness. OmniRoute at `100.76.52.49:20128` refuses connections, so the
compose-era assumption that it always runs does not hold.

```text
summary pass     21 of 21 videos, 0 failures, 8-25 s each
                 German and English both handled
schema           v3 adds summaries.keywords_json, migrated in place
export           21 notes + registry.md, 1.4 MB, deterministic filenames
mcp              initialize 200 · 6 tools · search returns a timestamped hit
```

**The MCP mount, measured.** `FastMCP` is `MCPServer` in mcp 2.x;
`streamable_http_path="/"` mounted at `/mcp` answers 307 and then 404, so the
inner app is mounted at `/` with path `/mcp` to match exactly. The mounted
sub-app's lifespan never runs, so `mcp.session_manager.run()` is started inside
the FastAPI lifespan. DNS-rebinding protection needed `allowed_hosts` naming the
host and port. Authentication applies because the gate is app-level middleware,
which sits outside routing; a router dependency would not have covered it.

Tools, all read-only: `search_archive`, `get_video`, `get_transcript`,
`list_channel`, `recent`, `stats`. Digest generation and imports stay on the web
side so every write has one owner.

