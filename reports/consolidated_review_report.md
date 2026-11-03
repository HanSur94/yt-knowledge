# Consolidated EGM Review Report

**Target:** `docs/designs/yt-knowledge.md`
**Mode:** Consolidated, all five rubrics (stages 5, 6, 7, 8, 9)
**Date:** 2026-10-31 / 2026-11-01
**Reviewers:** six independent subagents in two waves, no shared context with the author

Round 1 ran stages 5 (Zoom Out), 6 (Comprehension), 7 (Critic: design, structural,
style) against the first draft, producing 50 findings. The document was rewritten in
response. Round 2 ran stage 8 (Intermediate Zoom Out, verifying every round-1 item)
plus stage 9 (Implementation Readiness and a technical reality check that executed
the named APIs). Stage 8 re-scored round 1 at **44 resolved, 6 partial, 0 still
open** and added 10 new findings; stage 9 added 36.

---

## Severity summary

| | Round 1 | Found in round 2 | Fixed in the final doc | Accepted, not fixed |
|---|---|---|---|---|
| 🔴 Blocker | 10 | 8 | 18 | 0 |
| 🟡 Major | 27 | 18 | 33 | 5 |
| 🟢 Nit | 13 | 10 | 13 | 4 |
| **Total** | **50** | **36** | **64** | **9** |

---

## 🔴 Blockers that were real

Five defect classes, each caught by more than one lens:

**1. The search index could not search the thing the app exists for.**
`videos_fts` covered only title, channel and summary, while the goal, CUJ 3 and the
acceptance test all promised finding a phrase that exists only in speech. Three
reviewers found it independently. Fixed: `segments_fts` indexes every transcript
line; `search_docs` gives it a parent key.

**2. The corpus filter needed data it did not have.** The ingest rule filtered on
`duration_s` (to drop Shorts), but duration only arrives with the metadata fetch,
and Safari rows have no dwell at all - so the filter would have passed the entire
noise population it was written to remove. Fixed: two stages, Shorts detected from
the URL.

**3. `failed` could not say what failed.** One terminal state with one reason column
meant a summary failure re-downloaded the transcript and a restart stranded the row
forever. Fixed: `fail_stage`, `attempts`, and `next_attempt_at`.

**4. The FTS index had no key.** `videos_fts` carries no video id and `videos` has a
text primary key, so a hit could not be traced back to a video - while the design
promised "best row per video". Fixed: `search_docs.video_id UNIQUE`.

**5. The document could not be started.** No statement anywhere of where the LLM
URL, model, secret, database path or data directory came from. Fixed: eleven
environment variables, three of them required, startup fails naming the missing one.

---

## 🔴 Blockers the technical check caught that no amount of reading would have

Stage 9b ran the named APIs on this machine. Three claims were wrong:

**`FastMCP` does not exist in mcp 2.3.0.** It is `MCPServer`; the old import path is
a `ModuleNotFoundError` on line one. Independently confirmed by the author:
`from mcp.server.fastmcp import FastMCP` fails, `mcp.server.MCPServer` exists.

**Mounting the MCP app at `/mcp` produces a dead endpoint.** Three separate faults:
the inner app's own route is already `/mcp`, so it serves `/mcp/mcp` and 307s the
bare path to a 404; a mounted sub-app's lifespan never runs, so the session manager
raises `Task group is not initialized`; and DNS-rebinding protection rejects the NAS
hostname with `421 Invalid Host header` unless `allowed_hosts` names it. All three
fixed in the plan, with the working recipe.

**`INSERT OR REPLACE` silently corrupts the transcript index.** SQLite fires
REPLACE-conflict delete triggers only when `recursive_triggers` is on. Measured by
the reviewer and reproduced by the author: one stored segment, `MATCH` returned two
hits. The plan's own test ("same row count") passed while it happened. Fixed:
explicit `DELETE` before insert, plus `PRAGMA recursive_triggers=ON`.

Also caught: `itsdangerous` missing from the dependency list, so the entire auth
story would not import; a cookie dependency that cannot protect a mounted sub-app
and an `HTTPException` in middleware that surfaces as 500 instead of 401; `uvicorn`
taking host and port separately so `YTK_BIND` as one string cannot work; and two
handlers on `POST /import` silently routing multipart uploads to the JSONL parser.

---

## 🟡 Accepted, not fixed - and why

| Finding | Why it stands |
|---|---|
| Dwell is not clamped to video duration, so a long backgrounded tab counts as a full viewing (stage 7a) | Real. The fix needs the ratio `max(dwell)/duration`, which is already stored; it belongs in the UI sort, not the ingest rule. Left visible rather than silently dropped. |
| With no dwell, a three-second click on a 90-minute video is kept and fetched (stage 8) | Accepted cost of the ingest rule. The alternative is treating Safari rows as unwatched, which loses the majority of the corpus. |
| SponsorBlock categories are not enumerated (stage 9a) | Three constants in one function. Listing them in a design doc adds no information. |
| The Takeout HTML fallback's trigger condition is unspecified (stage 9a) | Genuinely unknown until a real export exists; listed in Open Questions. |
| Test plan lacks cases for the archive rule, digest idempotency and the auth surface (stage 9a) | Fair. Added to the plan as work, not as a design change. |
| Template contexts, Jinja variable names, CSS, prompt wording, timeouts (stage 9a) | The Zero-Decision rule cannot mean every line is pre-written. Enumerated in the doc under "Deliberately left to implementation". |
| The list of ten compared repositories is not in the design doc (stage 7c, partial) | It is in `concepts_brief.md` with full tables. The design doc gives the method and the count. |
| HTMX and SAPSIDHASH still bare on first use (stage 7c, partial) | Real style defect, minor. |

---

## Where the reviews converged

Three independent lenses - Zoom Out, Design, and the implementation check - all
found the same missing thing: **the document described a search engine without
saying what it indexed.** That is the single highest-value catch of the run, and it
came from reviewers reading the goals against the schema rather than reading the
prose.

The second pattern: every one of the eight wrong technical claims was in a sentence
that read as precise. `FastMCP("yt-knowledge")`, "mounts at `/mcp`", "INSERT OR
REPLACE", "the five sync triggers". Precision in prose is not verification. The
check that worked was executing the API, not reading the claim.

## What the process cost and what it bought

Roughly two hours wall clock, nine reports, two rewrites, 663 lines of final
document. It removed one architecture error (unsearchable transcripts), one data
corruption bug (FTS double-indexing), one dead endpoint (the MCP mount), one
unstartable service (no config), and one permanent-failure trap (treating an IP
block as `no_caption`, which would have marked 4,000 videos dead on the first
night).

Strongest counter-argument to the whole exercise: seven of the nine remaining
findings are ordinary choices a competent engineer makes at the keyboard in an
afternoon, so a design that ships 663 lines and still hands over the prompt wording
may be doing paperwork rather than design. The counter to that counter: the four
defects worth fixing were invisible to the author across two full rewrites, and
three of them fail silently - which is the case where a second reader, or an
executed check, earns its cost.

---

*Produced with EGM. Individual stage reports: `stage5_zoom_out.md`,
`stage6_comprehension.md`, `stage7a_design.md`, `stage7b_structural.md`,
`stage7c_style.md`, `stage8_intermediate_zoom_out.md`,
`stage9a_implementation_readiness.md`, `stage9b_codebase_details.md`.*
