# yt-knowledge

A self-hosted archive of the YouTube videos you actually watched.

It reads your browser history, pulls each video's transcript, has a language
model summarise it into a headline, timestamped key points, keywords and tags,
keeps the thumbnail, and makes the whole thing searchable — from a web UI, from
the command line, or from an MCP client.

No account, no cloud, no API key beyond the model endpoint you already run.

## How it works

```
Safari / Chrome / Firefox history
        │  collector (reads the browser SQLite directly)
        ▼
   POST /import  ──►  SQLite (videos, watches, segments, summaries)
                          │
                          ├─ worker: yt-dlp metadata → transcript API →
                          │          SponsorBlock → thumbnail → LLM summary
                          │
                          ├─ web UI   library, search, video, queue, settings
                          ├─ FTS5     phrase search over speech, not just titles
                          ├─ export   Obsidian-ready markdown, one note per video
                          └─ MCP      read-only tools at /mcp
```

Watch history is the ingestion source because YouTube's Data API has no
watch-history endpoint. Dwell time from the browser is used to triage: a
60-second bounce is rejected, a 40-minute stay is ranked first.

## Quickstart

```bash
cp .env.example .env          # set YTK_SECRET to something long
docker compose up -d --build  # http://localhost:8499
```

Then, on the machine with the browser history:

```bash
python -m ytk.collector --dry-run                 # what it found
python -m ytk.collector --push http://<host>:8499 --token "$YTK_SECRET"
```

The default port is 8499 because 8484–8491 are usually taken on a home NAS.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `YTK_SECRET` | required | shared secret; the login password and the API bearer token |
| `YTK_DB` | `data/ytk.sqlite` | SQLite file; FTS5 required |
| `YTK_THUMBS` | `data/thumbs` | thumbnail directory |
| `YTK_BIND` | `127.0.0.1:8499` | listen address |
| `YTK_SLEEP` | `0.5` | seconds between queue items |
| `YTK_WORKER` | `1` | `0` serves the UI without crawling |
| `YTK_LLM_BASE` | unset | OpenAI-compatible base URL; unset = transcripts only |
| `YTK_LLM_MODEL` | unset | model name for summaries |
| `YTK_EXPORT` | `<db dir>/export` | markdown export root |

Video length limits, pause, retry and export are also live in the UI at
`/settings` and `/queue` — no restart needed.

## Notes

- **One worker, one row in flight.** Claiming is a single SQL statement, so a
  restart cannot strand a row; `state='running'` rows are recovered at startup.
- **`fetched` is a resting state.** A row parked between transcript and summary
  is still claimable, so an interrupted run resumes instead of wedging.
- **Backoff is capped at one hour** (`min(60 * 2**attempts, 3600)`).
- **YouTube throttles transcripts per IP.** Expect ~150 videos/hour, and the
  queue to stall entirely after a few hundred. That is the API, not the crawler.
- **SQLite stays on the machine running the app.** Copy it with
  `sqlite3 .backup`, never with `cp` over a network share.

## Docs

- [`docs/designs/yt-knowledge.md`](docs/designs/yt-knowledge.md) — the design
- [`docs/designs/controls-design.md`](docs/designs/controls-design.md) — operator controls
- [`reports/`](reports) — EGM review reports, including the blockers they caught
- [`2026-11-01_DRIFT_REPORT.md`](2026-11-01_DRIFT_REPORT.md) — design vs. code

## Tests

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest -q
```
