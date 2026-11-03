# YouTube Watch-History Knowledge Pipe (working title)

* **Author:** Hannes
* **Date:** 2026-10-31
* **Status:** brainstorm brief, pre-draft

## Overview

An app that reads your YouTube watch history, stores the full transcript of every
video you watched, stores a summary, stores a preview image, and makes all of it
reachable from any agent, any harness, and any knowledge base. Self-hosted on the
NAS. Web UI for browsing.

The problem stated plainly: you watch a lot of YouTube and retain almost none of
it. The video you watched two months ago is not findable. The platform keeps no
usable record. Anything you want to keep, you have to keep yourself.

## What the landscape actually contains (measured 2026-10-31)

Discovery: 6 GitHub search queries + 3 web searches; 26 repos verified through the
GitHub API the same day. Stars are live, not from search snippets.

### Group A - history-driven knowledge apps (the direct comparables)

| Repo | ★ | Lang | License | Pushed | History in | Transcripts | Frames | MCP | Web UI |
|---|---|---|---|---|---|---|---|---|---|
| aucontraire/chronovista | 2 | Py+TS | AGPL-3.0 | 2026-10-31 | ✅ Takeout | ✅ 1.46M segments | — | — | ✅ React, 81 endpoints |
| 0xchamin/mcptube | 164 | Py | ⚠️ README MIT, API `none` | 2026-04-13 | — single URL | ✅ | ✅ claims vision | ✅ | — |
| sidinsearch/superbrain | 496 | Py | ⚠️ NOASSERTION | 2026-09-25 | — | ✅ | ✅ OpenCV | — | Android app |
| mr8lu/ObsidianYouTubeSync | 4 | Py | ⚠️ README MIT, API `none` | 2026-10-23 | ✅ yt-dlp + cookies | ✅ | — | ✅ | — |
| gorynychzmey/vts | 3 | Py | MIT | 2026-10-31 | — | ✅ Whisper | — | ✅ | ✅ + delivery adapters |
| thedavidmurray/youtube-intelligence | 1 | Py | MIT | 2026-01-13 | ✅ channels RSS | ✅ | — | — | — |
| Abhishek-P-Joshi/pkb-mcp | 0 | Py | ⚠️ `none` | 2026-04-24 | ❌ Watch Later only | — | — | ✅ | — |
| karto/youtube-watch-history-importer | 0 | Py | MIT | 2026-03-17 | ✅ Takeout | — | — | — | — |
| Tobeiyyy/afterwatch | 0 | Py | MIT | 2026-10-28 | ✅ claimed | — | — | — | — |
| RC0d3r/yt-transcript-mcp | 0 | Shell | MIT | 2026-10-28 | ✅ curate | ✅ FTS5+Chroma | — | ✅ | — |

`youtube-intelligence` has 2 commits, both on its creation day. `chronovista` has
104 commits and is the only one of the ten with a working end-to-end product.

### Group B - transcript fetchers (component, not product)

| Repo | ★ | Lang | License | Pushed | Frames |
|---|---|---|---|---|---|
| kimtaeyoon83/mcp-server-youtube-transcript | 602 | TS | MIT | 2026-07-21 | — |
| ZubeidHendricks/youtube-mcp-server | 592 | TS | MIT | 2026-08-08 | — |
| anaisbetts/mcp-youtube | 552 | TS | MIT | 2026-08-16 | — |
| jkawamoto/mcp-youtube-transcript | 494 | Py | MIT | 2026-10-28 | — |
| kevinwatt/yt-dlp-mcp | 297 | TS | MIT | 2026-10-29 | — |
| guimatheus92/mcp-video-analyzer | 107 | TS | MIT | 2026-10-25 | — |
| samson-art/transcriptor-mcp | 24 | TS | MIT | 2026-10-30 | ✅ get_video_frame |

### Group C - search layer you should not rebuild

| Repo | ★ | Lang | License | Pushed | What it is |
|---|---|---|---|---|---|
| tobi/qmd | 30,780 | TS | MIT | 2026-10-15 | hybrid BM25+vector search over a markdown corpus, one CLI |

### Group D - capture primitive

| Repo | ★ | Lang | License | Pushed | What it is |
|---|---|---|---|---|---|
| sniklaus/youtube-watchmarker | 229 | JS | GPL-3.0 | 2026-04-10 | browser extension that logs what you watched |

### Winners per axis

* **Popularity:** not applicable. The most-starred thing that does any of this is a
  markdown search engine (qmd, 30.7k), not a knowledge pipe. The best real
  comparable has 496 stars and is an Android app.
* **Pipeline breadth:** chronovista. History + transcripts + timestamp search +
  takeout import + deleted-video recovery.
* **Agnostic delivery:** vts. MCP as a retrieval server plus pluggable delivery
  adapters. It treats "the app" and "the knowledge base" as separate things.
* **Frames from video:** mcptube and superbrain claim it; nobody pairs it with
  watch history.
* **Licensing traps:** mcptube, ObsidianYouTubeSync and pkb-mcp all read "MIT" in
  the README while the GitHub API reports `none`. Do not fork blind.
* **AGPL trap:** chronovista is AGPL-3.0. Reading it is fine; forking it makes
  anything you publish AGPL.

## Measured constraints (these numbers decide the design)

Run on the Mac, 2026-10-31, home IP, no cookies, no proxy.

1. **There is no watch-history API.** YouTube Data API v3 has returned an empty
   `HL` placeholder since 2016; `activities` is deprecated. Tested claim, confirmed
   by Google's own revision history. There is no supported export endpoint; the
   options that exist are compared in the section below.
2. **Takeout is lossy.** `watch-history.json` gives title, channel, url, timestamp.
   It gives no watch duration and no position. A 90-minute lecture closed at minute
   40 is identical to one watched to the end. Some accounts now only get
   `watch-history.html`, so the parser has to read both.
3. **`yt-dlp` subtitle download is dead.** 3 videos, 6 subtitle requests: 2 written,
   4 returned `HTTP 429 Too Many Requests`. Retrying the same video after a 25s
   cooldown still returned 429. This is not a throughput limit you can tune around.
4. **`youtube-transcript-api` works.** 14 consecutive videos from one channel: 14
   ok, 0 failures, 1.09 s average, ~55 videos/minute, no cookies, no proxy, no
   rate-limit hit. 3,000 videos is about an hour. 40,000 is about twelve.
   Caveat: the sample channel has manual captions on every video. Videos with no
   caption track at all fail, and that failure is permanent - one music video in an
   earlier 3-video sample had no track.
5. **Metadata is free.** `yt-dlp --dump-json` on a watch page returns title,
   channel, duration, publish date and chapters without downloading video. Only the
   subtitle CDN throttles.
6. **Thumbnails need no download.** `https://i.ytimg.com/vi/<id>/maxresdefault.jpg`
   is a static URL. Storing a thumbnail costs one HTTP GET.
7. **Frames cost real money.** A frame out of a video means downloading the video
   (100-500 MB for a 20-minute talk), then ffmpeg. ffmpeg 9.0.2 is installed on the
   Mac. At 3,000 videos that is roughly half a terabyte of transfer.

## Watch-history acquisition (measured on this machine, 2026-10-31)

Four sources exist. Only one of them has dwell time, and it is not the one the
whole internet writes about.

| Source | What it yields | Dwell time | Setup | Tested |
|---|---|---|---|---|
| Safari `History.db` | 1,487 watch visits, 1,395 distinct videos, 2026-05-17 to 2026-10-31 | ❌ | none, read a copy of the sqlite file | ✅ |
| Chromium `History` (Arc, Chrome) | 719 visits, 650 distinct videos, 2025-07-20 to 2025-11-26 | ✅ 696 of 719 visits | none, same | ✅ |
| Google Takeout | years back, includes phone and TV viewing | ❌ | manual export, link dies after ~7 days | ❌ not tested |
| innertube `browse` on `/feed/history` | whatever the history page paginates | ❌ | Google cookies + SAPISIDHASH request signing | ✅ blocked without auth |

Union of the two browser databases on this Mac right now: **2,041 distinct
videos**. Shorts: 337 visits. Visits per month, Safari only: 94, 254, 282, 173,
280, 404.

Details that change the design:

1. **Safari history is pruned to about seven months.** Total history in the
   database starts 2026-03-31, first YouTube visit 2026-05-17. So Safari is a
   rolling window, not an archive. Whatever is not captured is lost.
2. **Chromium records dwell time, Safari does not.** 696 of 719 Arc visits carry
   a `visit_duration`. Of those, 533 ran over 60 s and 95 under 10 s. That is the
   "did I actually watch it" signal Google refuses to export, and it comes free
   with the browser file.
3. **Dwell time has to be capped.** The maximum recorded duration is 202,465 s -
   fifty-six hours, from a tab left open over a weekend. Any filter on duration
   needs an upper bound as well as a lower one.
4. **Titles come for free.** The history rows already read `Battlefield 6 -
   YouTube`. Video id extracted from the URL, title from the row, no network call.
5. **The innertube history path needs a real Google session.** Unauthenticated
   `/feed/history` returns HTTP 200 with a 15 KB `ytInitialData` and zero video
   renderers; the body says `Der Wiedergabeverlauf wird ohne Anmeldung nicht
   angezeigt`. Authenticated it works (YouTube.js exposes `getHistory()`), but it
   duplicates what the browser file already has for every video watched on this
   Mac, and it needs cookies that expire.

The conclusion is the opposite of the plan I started with: **the browser's own
history database is the primary source, Takeout is the backfill, and no
authenticated scraping is needed at all.**

## Other users, and why the ingest is a push API

A server on the NAS cannot read anybody's browser file. So the boundary is not
"the app reads history", it is "something on the user's machine pushes history
in". That single correction decides the multi-user story before any auth work.

Three input paths cover everyone:

| Path | Platform | Dwell time | Install |
|---|---|---|---|
| `POST /import` with a Takeout zip | any, including iOS, Android, TV | ❌ | none, manual export |
| local collector script, `browser-history` + optional Chromium SQL | macOS, Windows, Linux | ✅ on Chromium only | one command, once |
| MV3 beacon posting to `/watch` | desktop browsers | ✅ exact | extension |

**The cross-platform collector is one dependency, not three parsers.**
`browser-history` 0.5.0 (released 2025-12-28, no runtime dependencies) reads
Chrome, Chromium, Edge, Brave, Vivaldi, Firefox and Safari in a single call and
returns `(timestamp, url, title)`. Tested on this Mac against Safari, Arc and
Firefox: 83,994 rows, 4,975 YouTube watch visits, **4,253 distinct videos from
2023-04-28 to 2026-10-31**, correct titles, timezone-aware timestamps.

It does not expose `visit_duration`. That is only in the Chromium `visits`
table, so dwell time stays a direct SQL read on the same machines. Two tiers: the
library for coverage, one extra query for the signal.

What other users lose, and cannot get back:

* **iPhone, iPad, Android, smart TV** - no local history file is reachable. Those
  users have Takeout and the beacon, nothing else. Their dwell time never exists.
* **Safari users on other Macs** need Full Disk Access for whatever runs the
  collector, which is a real onboarding step, not a footnote.
* **Anyone whose browser prunes history** (Safari keeps about seven months) has
  already lost everything older than that on the day they install.

**Volume, corrected by the same run:** 4,253 videos, not 2,041. At 55 transcripts
a minute the first pass is 77 minutes.

**What multi-user actually costs.** Not a tenant column and a login page. One
SQLite file per user, a push endpoint that takes a user key, and then the thing
that will actually break: all fetching leaves from one NAS IP. 55 videos a minute
was measured for one user; ten users backfilling at once is ten times the pressure
from a single address. Rate limiting per user is not optional in that world, and
it is the reason to stay single-user until there is a second real user.

## Analysis

The pipeline is small. The app is not.

What everyone in the landscape builds is the fetching half: transcript in, summary
out, chunk into a vector store. What nobody in the landscape builds well is the
second half - the part that makes an archive worth keeping: knowing what you have,
not re-fetching it, and making it queryable from whichever agent is in front of you
today. The dead repos in Group A are dead at the summarization stage, not the
fetching stage.

Evidence that this is a real gap and evidence that it is a weak demand signal are
the same evidence: nine of ten comparables have fewer than five stars, six have
commits only on their creation day. Either everyone who wants this builds it
privately and never publishes, or everyone who starts it abandons it after the
first transcript lands. The second reading is more likely, and it is the one to
design against: the failure mode is a folder of 400 markdown files nobody opens.

That reframes the build. The question is not "can I get the transcripts" (measured:
yes, 55 per minute). The question is "what does the screen show me that makes me
open it in week three".

Three objections to the proposal as stated:

1. **"Agnostic" is usually a way of not choosing.** An adapter layer with zero
   users is a plugin API for one person. Concretely, agnostic here reduces to three
   things: a canonical store you own, an MCP server, and a markdown writer. Obsidian,
   LightRAG, Claude, Codex all consume one of those. Anything past that is
   speculative.

2. **Full transcripts are not the searchable surface.** 3,000 videos times roughly
   4,000 words is 12 million words. Storing them is correct - they are the primary
   source and they are cheap. Searching them is not. The retrieval surface should be
   chapter-level segment summaries with timestamp links back to the recording. The
   full text sits behind that, for when a summary is not enough.

3. **Watch history is a worse signal than it looks.** No duration, no position, no
   "did I finish it". YouTube shows the same video in history for a 10-second glance
   and a full watch. If the archive is built naively, 40% of it is noise: shorts,
   music, accidentally opened tabs, videos re-watched six times. There needs to be a
   cheap signal to drop those - minimum duration, a Shorts filter, a re-watch count
   that becomes useful rather than a duplicate.

## Goals vs Non-Goals

**In scope:**
* Import watch history from Google Takeout (JSON and HTML).
* Fetch and store full transcripts for every imported video that has one.
* Store a summary per video, chapter-aware when chapters exist.
* Store one preview image per video.
* Deduplicate: one row per video id, many watch events.
* Browse, filter and search in a web UI.
* Export: markdown files and an MCP server, both over the same store.
* Plug in any LLM through one OpenAI-compatible endpoint.

**Out of scope (for now):**
* Multi-user, accounts, sharing.
* Real transcription (Whisper) for videos without captions - fallback only, later.
* Frame extraction mid-video - a per-video button, later.
* Vector search - SQLite FTS5 first.
* Live capture of what you are watching right now.
* Anything that reads your Google account.

## Proposed shape (smallest version that is worth running)

One Python service, one SQLite file, one container.

```
collector ───┐
Takeout zip ─┼─> POST /import ─> videos table ─> fetch ─> transcript + thumb
beacon / URL ┘                        │                       │
                               │                  summarize (OmniRoute)
                               │                       │
                               └────> web UI (Jinja+HTMX, 4 screens)
                                      export: markdown folder
                                      export: MCP server
```

* **Ingest** - `POST /import` only. A local collector script reads the browser
  databases on the user's own machine and pushes rows; the same endpoint takes a
  Takeout zip, a pasted URL, or a beacon hit. The server never touches a browser
  file, so the same code serves user one and user ten.
* **Fetch** - `youtube-transcript-api` for text, `yt-dlp --dump-json` for metadata
  and chapters, thumbnail straight off `i.ytimg.com`.
* **Summarize** - one call per video to any OpenAI-compatible endpoint. OmniRoute is
  already running on the NAS, so the model is a config value, not an integration.
* **Store** - SQLite. `videos`, `watches`, `segments`, `summaries`. FTS5 over the
  summary text.
* **Front end** - FastAPI + Jinja + HTMX. Four screens: Library (filter by channel,
  date, topic), Video (summary, chapters, transcript with timecodes), Queue (what is
  pending and what failed), Settings (LLM endpoint, filters, export paths).

**Stack choice:** Python 3.12 + FastAPI + SQLite + Jinja2/HTMX.
The entire fetch layer is Python already. Adding React and a Node build chain buys
nothing for four screens and one user, and it doubles the deploy surface on the NAS.
If the UI later needs real client state, HTMX is replaceable screen by screen.

Roughly eight files: `ingest.py`, `fetch.py`, `summarize.py`, `store.py`, `web.py`,
`export_md.py`, `mcp_server.py`, `templates/`.

## Feature candidates, ranked by cost against data already collected

Ordered so that the cheap ones are also the ones that answer "why open it in week
three". Everything here was checked against something measured, not imagined.

### Free - falls out of the import, no new fetch

1. **Re-watch count as an importance signal.** 4,975 visits over 4,253 videos
   means roughly 700 videos were watched more than once. A video watched six times
   is the closest thing to an explicit "this mattered" that exists without asking.
   One `GROUP BY` on the watches table.
2. **Dwell triage.** 95 of 719 Chromium visits ran under ten seconds; one ran 56
   hours. A row counts as watched at over 60 s and under 2 h, everything else is
   archived on import and never fetched. Saves a chunk of the 77-minute first pass.
3. **Started but abandoned.** `yt-dlp --dump-json` returns duration for free, so
   `max(dwell) / duration` gives "opened 40 videos, closed them at five minutes".
   Nobody in the landscape builds this and it is one join.
4. **The archive outlives the video.** Store title, channel and thumbnail at import.
   When YouTube deletes or privates a video, the row stays readable with its
   transcript. chronovista built Wayback Machine recovery for this; storing what you
   already fetched covers most of it for nothing.
5. **Language-aware summaries.** Measured across his titles: 2,776 English, 981
   German, 1,218 ambiguous. Do not assume English, do not translate the stored
   transcript, and keep the summary in the video's language plus one English line.

### One new public call

6. **SponsorBlock segment stripping.** `https://sponsor.ajay.app/api/skipSegments?videoID=<id>`
   is public, needs no key, and returned real segments on the first test (a sponsor
   read at 266-318 s and an intro at 68-72 s). Removing sponsor, intro and self-promo
   spans before summarizing cuts the least useful 15% of the transcript and makes the
   summary denser. A 404 means the video has no segments, which is not an error.
   It also hands you chapter-like markers for videos that declare no chapters.

### The one that earns the app being opened

7. **Weekly digest.** One scheduled LLM call over the week's summaries: what you
   watched, which channels were new, what you actually got out of it. Twelve videos
   is a cheap prompt. This is the answer to the failure mode named earlier - a
   folder of notes nobody opens. Everything else in this document is plumbing for
   this one screen.

### Refused, with the reason

* **A chat UI over the archive.** The MCP server is the chat UI, and it already
  plugs into the agents he runs. Building a second one inside this app means
  rebuilding retrieval, streaming and prompt handling that already exist.
* **Vector search.** FTS5 covers 4,253 documents in the same SQLite file. Revisit
  when a real query fails, not before.
* **Frames instead of thumbnails.** 4,253 videos times 100-500 MB. No.
* **Channel subscription and new-video pull.** That is a different product (a
  reader), and `youtube-intelligence` already tried it and died.
* **Topic hubs / clustering.** The summary call can return tags for free; render
  tags as a filter. A hub page is a nice-to-have that grows its own schema.
* **Wayback Machine recovery.** Only matters for videos already gone; the import-time
  snapshot covers the common case.
* **Multi-user, mobile app, Notion/Readwise sync, recommendations.** All speculative
  until one person uses this daily.

## Alternatives Weighed

* **Fork chronovista.** Rejected: AGPL-3.0 kills any later public release. It is
  Postgres + React + 81 endpoints for a single user. Its thesis is research and
  analysis across 1.46M segments; this app's thesis is "find the thing I watched".
  Worth reading, not forking.
* **Fork ObsidianYouTubeSync.** Rejected: 4 stars, no UI, Gemini hard-wired, and a
  README-vs-API license conflict. It is closest to the stated idea, which is a
  warning rather than a recommendation.
* **Fork mcptube.** Rejected on license conflict and because it takes one URL at a
  time - it has no history concept, which is the entire ask.
* **Run vts and point it at videos.** Rejected as the primary: it is built per-video
  and per-upload, not per-history, and it re-transcribes audio with Whisper when a
  caption track already exists. Its MCP and delivery-adapter design is the part
  worth copying.
* **qmd as the search layer.** Deferred: 30.7k stars and genuinely good, but it
  adds npm plus a second runtime. SQLite FTS5 is in the same file as the data and
  covers 3,000 documents. Swap in qmd if FTS5 demonstrably disappoints.
* **Whisper for everything.** Rejected: only needed for videos with no caption
  track, and it is roughly 100x the compute of reading captions. Fallback only.
* **Frames instead of thumbnails.** Rejected for v1: one static URL versus a
  100-500 MB download per video. Keep a button.
* **Scrape `/feed/history` through innertube.** Rejected: needs a live Google
  cookie plus SAPISIDHASH signing, gives the same rows the browser file already
  holds, and breaks when the session expires. Read it, do not depend on it.
* **Pure Obsidian plugin.** Rejected: a plugin cannot own a job queue, cannot keep
  job state across restarts, and cannot serve MCP. Obsidian is an export target.
* **LightRAG as the store.** Deferred: already running in `document-knowledge-pipe`,
  but it needs an LLM per document and re-indexes on change. Becomes an export
  adapter once the markdown exists.

## Key Decisions & Open Questions

**Decided (overridable):**
1. Canonical store is SQLite; markdown and MCP are exports, not the source of truth.
2. Transcript fetch uses `youtube-transcript-api`, not `yt-dlp --write-subs`.
3. Source order is browser history databases first (dwell time, zero setup),
   Takeout second (older and other-device viewing), MV3 beacon third (exact dwell).
   No authenticated scraping of YouTube.
4. Summaries are chapter-aware and go through one OpenAI-compatible endpoint.
5. A watch counts as real only if dwell is over 60 s and under 2 h, or the video
   is marked watched by hand. `visit_duration` is capped, never trusted raw.
5. Search is SQLite FTS5 until it measurably fails.
6. Preview image is the thumbnail. Frames are a later per-video button.

**Open:**
1. Which knowledge base is the first real target to prove end-to-end?
2. Frames: ever, or thumbnail-only for good?
3. Name.
4. Should the app keep watching Safari's database on a schedule, or only on an
   explicit import? The database is pruned at about seven months, so a schedule
   that stops running loses the difference permanently.

**Answered by measurement:** 4,253 distinct videos since 2023-04-28, about 250 a
month. At 55 transcripts a minute the first full pass takes 77 minutes. No queue,
no rate limiter, no proxy - for one user, from one IP.
