# Stage 7 — Critic Review, Lens A: Design Coherence and Risk

Scope: lines 1–246 (everything before `## Detailed Implementation Plan`).

**Verdict:** A clean skeleton whose two load-bearing choices — a dwell filter applied at ingest and an FTS index over summaries — do not survive contact with the data sources they must serve; both must be settled before any implementation starts, because they fix the corpus definition and the search contract respectively.

## Findings

1. [BLOCKER] The corpus filter is declared to run where it cannot: "Filters applied at ingest, not at display: a watch counts if `dwell_s` is between 60 and 7200, or if `dwell_s` is absent (Takeout) and the video is not a Short" (156–157) — but the "not a Short" test needs `duration_s`, which arrives only with the metadata fetch that happens after a row reaches `fetched` (83–84), and Safari rows, the entire reason the project exists (23), have no `dwell_s` and therefore take the branch written for Takeout, passing with no dwell gate at all.

2. [BLOCKER] The search design does not index the thing the goals ask for: the goal is "Answer a phrase-level question in under a second over 4,000 videos" (38) and CUJ 3 returns results "each opening the video page scrolled to the matching transcript line" (93–94), but `videos_fts` is "FTS5 over title, channel, headline, bullets" (153) — a phrase that appears in the transcript and not in a summary bullet is unfindable, and the sub-50 ms target (240) is measured against the wrong index entirely.

3. [MAJOR] The state machine is unclosed in both directions: `videos.state` is declared as "`pending|fetched|ready|failed|no_caption`" (133) yet `state='archived'` is relied on ninety lines later (158–159), and `ready` is terminal with no re-entry path even though `summaries.model` records which model produced each row (152), so a prompt or model upgrade cannot be applied to 4,253 existing rows without hand-written SQL.

4. [MAJOR] CUJ 5 promises a merge its schema cannot perform: "Those watch events are merged by video id, existing rows gain new watch timestamps instead of duplicating" (108–109) against `watches ... UNIQUE(video_id, watched_at)` (148) — the only dedupe key is exact-timestamp equality, so a Takeout row for a video already captured from a browser lands as a second watch and the system cannot distinguish "same viewing, different clock" from "genuine rewatch".

5. [MAJOR] The throughput figure is an overclaim by construction: "the run is expected to take about 77 minutes" (87) is exactly 4,253 / 55 (238), i.e. the fetch half only, while the same sentence describes rows reaching `ready` via "summary and tags" (84) — summarisation latency is never measured anywhere in the document, so the first backfill's real duration is unknown.

6. [MAJOR] The privacy risk is named at full strength and mitigated only on the write path: "Browser history is the most sensitive file on the machine" (229–230) is answered by "`/import` requires a bearer token ... The service binds to the LAN address, not `0.0.0.0`" (234–236), leaving the library, video pages, transcript export and the MCP server readable by anything on the LAN with no auth, including the side-effecting `GET /export/md` (170).

7. [MAJOR] The filter thresholds are asserted rather than derived: the 7200 s ceiling (156) silently discards a two-hour podcast watched to the end — long-form is the material the summary goal is for — while `dwell_s` is a page-visit duration that is never clamped to `duration_s`, so one backgrounded tab on a four-minute video counts as a full viewing.

8. [MAJOR] Alternative B is rejected on a premise the document's own CUJ denies: "It duplicates rows the browser file already holds, for the one platform where reading the file is legal and free" (206–207) contradicts "Videos watched on the phone never appear in any browser database" (106) — phone viewing is the exact case B would cover without a manual export, and its real cons (cookie lifetime, silent expiry, no dwell) are listed but never weighed against the manual-upload path that CUJ 5 depends on.

Strongest counter-argument: none of these is an architectural dead end — `segments` already stores line-level text so `segments_fts` is one table (finding 2), and the filter could move to the worker unchanged (findings 1, 7) — which means the fault is that the spec never states the search and corpus trade-offs it has actually made, not that it chose wrongly.
