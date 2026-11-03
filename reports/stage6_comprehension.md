# Stage 6 — Comprehension Test

* **Target:** `docs/designs/yt-knowledge.md` (366 lines, Draft)
* **Mode:** REVIEW — fresh session, document only, no outside knowledge added

## What this system does

A self-hosted service on the home NAS that turns YouTube watch history into a
local, searchable archive. It keeps one row per video: title, channel, the full
transcript, a chapter-aware summary with timestamp links, a thumbnail, and a
record of every watch with its time and dwell. It is reachable through a small
server-rendered web UI and through an MCP server that any agent can call, and it
can write a markdown folder for a knowledge base. The motivation is that YouTube
offers no usable history and the browser silently prunes what it does keep
(Safari about seven months), so roughly a thousand videos a year become
unreachable. The service recovers them from the browser databases on the user's
own machine, from Google Takeout zips that cover phone-only viewing, and from a
pasted URL. It never authenticates to Google and stores no credentials. Videos
without a caption track are not transcribed; they are recorded as `no_caption`
and skipped. Search is SQLite FTS5, not embeddings, and there is no chat UI
inside the app — the MCP server plays that role. One background worker backfills
metadata, transcript, thumbnail and sponsor segments, then summarizes each video
through an OpenAI-compatible endpoint. The whole thing is deliberately one
container, one SQLite file, no broker and no ORM.

## How it works

`collector.py` runs on the user's machine, not the server. It reads Safari, Arc
and Firefox history through `browser_history`, re-reads the Chromium database
from a temporary copy to attach `visit_duration` as dwell, keeps only YouTube
watch URLs, and POSTs JSONL in batches of 500 to `/import` with a bearer token.
`/import` also takes a multipart Takeout zip and a pasted URL; `ingest.py`
normalizes all three into `{video_id, title, channel, watched_at, dwell_s,
source}` and applies the filters (dwell 60–7200 s, or no dwell and not a Short)
at ingest time, writing rows into SQLite. A single asyncio worker started in the
app's `lifespan` claims `pending` rows with one `UPDATE ... RETURNING` and walks
each one through `fetch` — metadata via `yt-dlp --dump-json`, transcript via
`youtube-transcript-api`, SponsorBlock segments, thumbnail download — to
`fetched`, then through `summarize` (headline, timestamped bullets, tags) to
`ready`; failures carry a reason. Library, video, queue and digest pages are
Jinja templates with HTMX over the same file, and `/export/md` writes a
`<channel>/<date>-<slug>.md` tree. `mcp_server.py` opens a read-only connection
per request for agent access.

## Findings

1. [BLOCKER] **Detailed Implementation Plan (`pyproject.toml`, `app.py`, `summarize.py`)** — nothing anywhere states where the LLM base URL, model name, bearer token, data directory or SQLite path come from (config file, environment variables, or CLI flags), so the service cannot be started, configured or deployed from this document.
2. [BLOCKER] **Data model (filters paragraph) vs. Technical Plan decision 2** — `state='archived'` is introduced there but is absent from the enumerated state set `pending|fetched|ready|failed|no_caption`, and no section says whether a watch rejected by the 60 s / 7200 s rule creates a row at all or creates it already archived, so the worker's claim query and any CHECK constraint cannot be written.
3. [BLOCKER] **Summary prompt shape vs. Data model** — the prompt requires "a single English sentence stored alongside" each summary for cross-language search, but `summaries` has no column to hold it.
4. [MAJOR] **Data model / CUJ 3** — `videos_fts` covers only `title`, `channel`, `headline` and `bullets`, while CUJ 3 and the goal of answering a phrase-level question require matching text that exists only in `segments`, and the ranking of those results is never specified.
5. [MAJOR] **`db.py`** — the document never says how `videos_fts` is kept in sync with `videos` and `summaries` (triggers, or explicit writes inside `set_state`), which is a design decision left to the implementer.
6. [MAJOR] **Goals / Target Files** — "run as one container on the NAS" is a stated goal, but none of the ten target files is a Dockerfile, compose file or other deployment artifact, and the verification plan never builds an image.
7. [MAJOR] **API surface / `mcp_server.py`** — `run_digest` is listed as an MCP tool while the same file is specified as a read-only connection with "no write tool", and where the resulting digest is persisted is not defined.
8. [MAJOR] **API surface / `summarize.py`** — `/digest` promises "the latest weekly digest", but no section defines the week boundary, the digest record, or what triggers a weekly run.
9. [MAJOR] **Non-Functional Requirements (Rollback) / `db.py`** — `PRAGMA user_version` is named as the migration guard, but what happens on a version mismatch (refuse to start, rebuild, migrate in place) is never stated.
10. [NIT] **Overview vs. Goals** — the Overview says the archive is exposed "two ways only", while Goals and the API surface add the markdown export as a third surface.
11. [NIT] **Overview / Non-Functional Requirements (Privacy) vs. Technical Plan decision 4 and `fetch.py`** — "It does not talk to Google" and "calls no Google endpoint" sit against `yt-dlp` metadata calls and `i.ytimg.com` thumbnail GETs; the sentence needs to say "no authenticated Google API".
12. [NIT] **Technical Plan decision 4** — the heading claims the thumbnail is "a URL, not a download job" and the body immediately describes downloading it into `/data/thumbs` at fetch time.
13. [NIT] **Data model vs. `fetch.py`** — `videos.desc` exists in the schema but the metadata fields `fetch.py` parses omit the description.
14. [NIT] **Data model** — `videos_fts` indexes `bullets` while the column is `bullets_json`, so whether the raw JSON or the extracted bullet text is indexed is unspecified.
15. [NIT] **Non-Functional Requirements (Performance)** — no timezone is given for `watched_at`, `first_seen`, the filtered window or the weekly digest boundary.
