# Stage 7B — Critic Review: STRUCTURAL (lines 1–246)

**Verdict:** The architecture is small and mostly coherent, but the process boundary is drawn wrong (a write tool lives on a read-only connection) and the single `state` column cannot express the one failure the pipeline actually creates — a fetched video whose summary failed — so retry, the mechanism the design leans on, has no correct state to start from.

## Findings

1. **[BLOCKER]** The MCP process is declared read-only yet exposes a mutating tool: line 124 "`(separate process, read-only connection)`" against line 172 "`MCP   search_archive, get_video, get_transcript, list_channel, recent, run_digest`" — digest generation therefore has two entry points with different privileges and there is no described owner of the digest write.

2. **[BLOCKER]** `videos.state` cannot express "metadata and transcript fetched, summary failed", because "`pending|fetched|ready|failed|no_caption`" (line 133) collapses both failure stages into one terminal `failed` alongside a single `fail_reason` column (line 147), so a retry cannot resume at summarize and must re-run the metadata/transcript fetch — and with no attempt counter, `failed` is permanent while line 85 claims "Failures land in `failed` with a reason" as the handling.

3. **[MAJOR]** The search index does not cover the corpus the journey searches: "`videos_fts  FTS5 over title, channel, headline, bullets`" (line 153) versus CUJ 3's promise of "opening the video page scrolled to the matching transcript line" (line 94) — transcript text is stored in `segments` (line 149) but indexed by nothing, so the phrase-level goal in line 38 is unreachable for any phrase that appears only in speech.

4. **[MAJOR]** `summaries` has no column for the cross-language field the prompt requires: line 180 says "a single English sentence is stored alongside for cross-language search" but the table is "`video_id, lang, headline, bullets_json, tags_json, model, created_at`" (line 152), and it has no `UNIQUE(video_id, lang)`, so regeneration duplicates rows and the English text the FTS index depends on is never persisted.

5. **[MAJOR]** `UNIQUE(video_id, watched_at)` (line 148) cannot tell a genuine re-watch from one viewing reported by two sources — CUJ 5 merges browser and Takeout rows that carry different timestamp granularity, so "existing rows gain new watch timestamps instead of duplicating" (line 108) fails and the re-watch count inflates; `seen_count` (line 147) appears nowhere else in the range, so no component owns deriving it.

6. **[MAJOR]** The document uses a state its own enum forbids and never describes the transition into it: "`state='archived'` retires them so a re-import does not resurrect noise" (line 159) against the five-value list on line 133, so `archived` rows are invisible to the enumerated state set and to "counts by state" on `/queue` (line 167).

7. **[MAJOR]** None of `segments`, `skips` or `chapters` carries a primary key or uniqueness constraint (lines 149–151), so a retried fetch — which finding 2 says is the normal failure path — re-inserts the same ~200 transcript lines instead of replacing them.

8. **[NIT]** Export mutates the shared volume on a GET: "`GET  /export/md       write markdown tree into the export volume`" (line 170) reads the live SQLite file the worker is writing and rewrites an entire tree with no stated snapshot, so any prefetch or retry of that URL races the worker.

Strongest counter-argument: every one of these is a schema or wiring detail that a single migration and a `stage` column fix, and the design's real load-bearing decisions — push-only ingest, SQLite, no job queue, transcript API over yt-dlp — are independently justified, so a critic could reasonably say the document is sound at the level it is written and that findings 3–7 belong in the implementation plan this review was told to ignore.
