# Stage 8 — Intermediate Zoom Out (post-rewrite verification)

**Document:** `docs/designs/yt-knowledge.md` (535 lines, revised after review round 1)
**Mode:** REVIEW · **Standard:** Zoom Out · **Date:** 2026-10-31

Scope: every finding in stage5, stage6, stage7a, stage7b, stage7c (50 total),
checked against the current text, followed by what the rewrite introduced.

## (a) Status of every round-1 finding

| ID | Sev | Finding (short) | Status | Deciding line / note |
|----|-----|-----------------|--------|----------------------|
| S5-1 | BLOCKER | Only FTS index covers summaries, nothing indexes `segments` | RESOLVED | 158-163 `segments_fts` indexes every line; 193 `content='segments'` |
| S5-2 | MAJOR | 77-minute backfill applies transcript-only rate | RESOLVED | 96-99 "4.8 hours at the measured 4.08 s per video"; 370-372 same |
| S5-3 | MAJOR | Nowhere states where MCP runs or how stdio reaches a WAL file | RESOLVED | 146-149 one process, `/mcp` mounted ASGI, reason for rejecting stdio |
| S5-4 | MAJOR | `run_digest` on MCP contradicts read-only server | RESOLVED | 261-263 five read tools, "No write tool exists on the MCP surface" |
| S5-5 | MAJOR | Side-effecting `GET /export/md`, no token | RESOLVED | 251 `POST /export/md ... token`; 259 POST-not-GET stated |
| S5-6 | MAJOR | `archived` outside the enum; re-import behaviour undefined | PARTIAL | Enum fixed (173). But 87-88 says every video passing the ingest filter becomes `pending`, while 221 says "a re-import cannot resurrect" archived rows — an archived row passes the ingest filter, so the two rules collide and `upsert_video` has no stated state rule. |
| S5-7 | MAJOR | `short_en` has no column | RESOLVED | 187 summaries, 191 videos_fts |
| S5-8 | MAJOR | "serves one user and ten" with no user key | RESOLVED | 143-145 now "the only user and any later one"; 55-56 one household |
| S5-9 | NIT | Digest absent from Goals | RESOLVED | 48-49 |
| S5-10 | NIT | Diagram says one process and separate process | RESOLVED | 129-139 single container, `/mcp (mounted ASGI)` |
| S5-11 | NIT | Nothing says how FTS stays in sync | PARTIAL | 160 names triggers, 418 says "five sync triggers". The mechanism is named but not specified, and the count cannot be right — see new finding 1. |
| S6-1 | BLOCKER | No configuration anywhere | RESOLVED | 265-286, eleven env vars, one `.env` |
| S6-2 | BLOCKER | Rejected watch: no row? archived? enum mismatch | RESOLVED | 203-205 "never written: it is counted and dropped"; 173 enum contains archived |
| S6-3 | BLOCKER | `short_en` nowhere to live | RESOLVED | 187 |
| S6-4 | MAJOR | Search matches only summaries; ranking unspecified | RESOLVED | 157-163 bm25 per index, summary hits above transcript hits, dedupe per video |
| S6-5 | MAJOR | How `videos_fts` syncs with `videos`+`summaries` | PARTIAL | 160 "kept in sync by triggers"; 418 count wrong; 191 has no key to sync on — new finding 1 |
| S6-6 | MAJOR | No deployment artifact for "one container" | RESOLVED | 392-401 Dockerfile + compose, HEALTHCHECK, phase 0 |
| S6-7 | MAJOR | MCP write tool; digest persistence undefined | RESOLVED | 262-263; `digests` table 290 |
| S6-8 | MAJOR | Digest week boundary, record, trigger undefined | RESOLVED | 288-295 ISO week, Berlin, cron, staleness rule |
| S6-9 | MAJOR | `user_version` mismatch behaviour unstated | RESOLVED | 383-386 refuses to start, prints both versions, never migrates |
| S6-10 | NIT | "two ways only" vs three surfaces | RESOLVED | 14-16 "two live surfaces ... plus a file export" |
| S6-11 | NIT | "does not talk to Google" vs yt-dlp | RESOLVED | 16 and 353-356 "no authenticated Google API call", names the anonymous calls |
| S6-12 | NIT | Thumbnail "URL not a download job" contradiction | RESOLVED | 164-165 one static URL, "no `yt-dlp` call, no video download" |
| S6-13 | NIT | `videos.desc` never populated | RESOLVED | 450-452 fetch parses "... and description" |
| S6-14 | NIT | `bullets` vs `bullets_json` indexing | RESOLVED | 191-192 "bullets holds flattened bullet text, never raw JSON" |
| S6-15 | NIT | No timezone for stored times | RESOLVED | 380-381 UTC stored, Berlin rendered, Monday 00:00 Berlin |
| S7A-1 | BLOCKER | Ingest filter needs duration it does not have; Safari ungated | RESOLVED | 209-221 two stages, shorts from URL, duration rule after fetch |
| S7A-2 | BLOCKER | Search does not index the transcript | RESOLVED | 158-163, 193 |
| S7A-3 | MAJOR | State machine unclosed both ways; no re-summarize path | RESOLVED | 173 archived; 233 `ready -> fetched`; 253 `/admin/resummarize` |
| S7A-4 | MAJOR | CUJ 5 merge impossible under `UNIQUE(video_id, watched_at)` | RESOLVED | 121-124 90-minute rule; 419-420 `add_watch` implements it |
| S7A-5 | MAJOR | Throughput overclaim by construction | RESOLVED | 96-99 |
| S7A-6 | MAJOR | Privacy mitigated only on the write path | RESOLVED | 359-365 whole app behind one secret, every route including MCP |
| S7A-7 | MAJOR | 7200 s ceiling; dwell never checked against duration | PARTIAL | Ceiling gone (215-216). The second half survives: 217-218 only flags dwell above 4 h, so a 30-minute dwell on a 4-minute video is still counted as a full viewing and is not flagged or clamped. |
| S7A-8 | MAJOR | Alt B rejected on a premise CUJ 5 denies | RESOLVED | 318-330 rewritten as a real trade, phone gap named, revisit criterion given |
| S7B-1 | BLOCKER | Read-only MCP exposing a write tool | RESOLVED | 261-263 |
| S7B-2 | BLOCKER | State cannot express fetched + summary failed; no attempts | RESOLVED | 150-152, 175-176 `fail_stage`/`attempts`; 226-239 state machine |
| S7B-3 | MAJOR | Index does not cover the journey's corpus | RESOLVED | 158-163 |
| S7B-4 | MAJOR | No `short_en` column, no `UNIQUE(video_id, lang)` | RESOLVED | 187-189 |
| S7B-5 | MAJOR | Dedupe key cannot tell re-watch from duplicate; `seen_count` orphan | RESOLVED | 121-124; 196-197 explains its deliberate absence |
| S7B-6 | MAJOR | `archived` invisible to enum and `/queue` counts | RESOLVED | 173, 248 |
| S7B-7 | MAJOR | `segments`/`skips`/`chapters` have no key, retry duplicates | RESOLVED | 182-185 all three keyed |
| S7B-8 | NIT | Export mutates the volume on a GET | RESOLVED | 251 POST; prefetch/retry no longer possible |
| S7C-1 | BLOCKER | Unsourced "nine of ten" survey statistic | PARTIAL | 27-30 now gives date, six queries, 26 repos screened, ten projects. Still no list of the ten, so "nine under five stars" and "six creation-day commits" remain uncheckable; 26 screened vs 10 reported is also unexplained. |
| S7C-2 | MAJOR | Language mix sums to visits, unit unstated | RESOLVED | 197-198 "measured across 4,975 visits" |
| S7C-3 | MAJOR | MCP/FTS5/HTMX/WAL/SAPISIDHASH never expanded | PARTIAL | MCP (14), FTS5 (60), WAL (373) expanded; HTMX (136, 476) and SAPISIDHASH (322) still bare |
| S7C-4 | MAJOR | Thumbnail bold claim contradicted by its body | RESOLVED | 164-165 |
| S7C-5 | MAJOR | "good enough to replace watching it again" unfalsifiable | RESOLVED | 37-39 sample size and a named criterion (see new finding 7 for the tense problem it introduced) |
| S7C-6 | NIT | "≈200 segments" unsourced | RESOLVED | 377-379 measured 154-205 across 14 videos |
| S7C-7 | NIT | "room to grow" filler | RESOLVED | 344-345 now a concrete claim |
| S7C-8 | NIT | Constant-time compare asserted, no mechanism | RESOLVED | 363-364 `hmac.compare_digest` |

**Totals: 44 RESOLVED, 6 PARTIAL, 0 STILL OPEN.**

## (b) New findings introduced or left by the rewrite

1. **[BLOCKER]** "`videos_fts  FTS5(title, channel, headline, bullets, short_en)   -- one row per video,`" (191) against "`videos      id TEXT PK`" (170) — the index carries no video id and no rowid mapping to the 11-char text key, so a hit cannot be traced back to a video. Line 163 promises "duplicates collapsed to the best-scoring row per video" and summary hits ranked above transcript hits, both of which need that key. The "kept in sync by triggers" correction (160) also does not say how: `videos_fts` draws columns from two tables (`videos`, `summaries`) and `segments_fts` from a third, so insert/update/delete coverage needs at least seven triggers, not "the five sync triggers" (418). Fix is one column (`video_id UNINDEXED`) plus the real count.

2. **[BLOCKER]** "The MCP endpoint is registered in Claude or Codex by URL." (112) against "/mcp ... session required" (255) and "every route except `/healthz` and the login form requires it, including ... the mounted MCP endpoint" (361-362). There is no login form in an MCP client, and the only bearer token in the design is scoped to `/import` (366-367). CUJ 4 cannot be performed as written; the fix is to accept the same bearer on `/mcp`, which the doc never says.

3. **[MAJOR]** "`failed ──attempts < 3──> backed off, then retried at fail_stage`" (232) against "The worker claims rows with a single `UPDATE ... WHERE state='pending' RETURNING`" (151-152) and `claim_next` (421). Nothing stores a due time (`videos` has no `next_attempt_at`), nothing increments `attempts`, and the claim query as specified never selects a `failed` row. As drawn, "failed is not terminal" (236) holds only inside one process lifetime; a restart strands the row.

4. **[MAJOR]** "it is counted and dropped, and the response reports it" (203-204) against "`/queue` shows the pending count and the filtered count, and the two together equal what the collector reported" (519-520). A count that exists only in a POST response cannot be rendered on a page later, and the schema has no counter to hold it. Either the verification step is impossible or the "never written" rule has an exception.

5. **[MAJOR]** "`YTK_SECRET ... (required)`" (271) against "`YTK_LLM_BASE` and `YTK_LLM_MODEL` are the only two the service cannot start without" (284). The variable that guards every route is described as required on one line and optional on the next; the failure mode of a missing secret is an unauthenticated archive, not an unreachable queue.

6. **[MAJOR]** "`YTK_BIND ... default 192.168.178.40:8488`" (278) against "`compose.yaml` maps `8488:8488`" (401) and "The service binds to the LAN address, not `0.0.0.0`" (367). A bridge-networked container cannot bind the host's LAN address; with the stated port mapping the process must listen on 0.0.0.0 inside the container, which is exactly what the security sentence forbids. Either host networking or the bind default has to change, and neither is stated.

7. **[MAJOR]** "sampled against 20 videos, the summary names their central claim." (37-38) is written as a completed measurement, but nothing here has been built: the same sample is scheduled as future work in the Verification Plan ("run the worker on 20 videos first", 521) and Open Questions still calls summary quality and latency unknown (75-76). The goal is a good acceptance criterion; stating it in the past tense turns it into an unsourced result.

8. **[NIT]** "Module constants `SLEEP = 0.5` and `MAX_ATTEMPTS = 3` as the only tuning knobs" (456-457) against "`YTK_SLEEP ... default 0.5`" (280) and "Anything not listed has a constant in code" (267-268). Same knob in two places, and `MAX_ATTEMPTS = 3` duplicates the state machine's "attempts < 3" (232) — the second-source-of-truth problem the doc rejects for `seen_count`.

9. **[NIT]** Lines 206-208 are three consecutive blank lines where the old single-filter paragraph was cut, leaving "The alternative - inserting every visit as `archived` - would make the table the size of the browsing history" (204-205) as an orphan paragraph before the section says what the ingest rule actually is.

10. **[NIT]** "keep the watch if `dwell_s >= 60`, or if `dwell_s` is absent (Takeout and Safari never record one)" (212-214): for the dominant sources the only gate is the `/shorts/` path, so a three-second click on a 90-minute video is archived and counted as kept. That undercuts the paragraph's own justification for dropping rejected watches (204-205) — the table is still protected only where dwell exists.

**Zoom Out Standard.** The root problem (history is pruned and unsearchable) and the shape (one SQLite file, FTS5, one container, push-only ingest, transcript API over yt-dlp) are the simplest thing that works; nothing here is a speculative platform. Reuse is honest: the markdown layout comes from `document-knowledge-pipe` (479), the LLM is the existing local endpoint, the digest can use the existing notification path. The altitude is right everywhere except where precision is load-bearing: triggers, retry timing, and MCP credentials are the three places the doc is vague, and they are exactly the three an implementer cannot guess.

## (c) Verdict

**Fail, narrowly:** the architecture is now correct and every round-1 contradiction is closed, but search cannot return its own video id, the MCP surface has no credential path a URL-registered client can use, and the retry loop has no storage for its backoff — three small, local, and unmissable fixes.

Strongest counter-argument: all ten items are one column, one header, and one due-time field — a reviewer could fairly say the design is finished at the level it is written and these belong to the implementation plan, so failing it twice over three sentences risks rewarding paperwork over a design that a competent implementer would complete correctly on the first pass.
