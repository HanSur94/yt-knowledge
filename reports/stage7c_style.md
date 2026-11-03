# Stage 7 — Critic Review, Lens C (Style & Plain English)

**Scope:** lines 1–246 of `docs/designs/yt-knowledge.md`
**Verdict:** Mostly plain and unusually well-grounded for a draft, but it carries one wholly unsourced survey statistic, one number that silently counts visits while calling them the video corpus, and a jargon layer (MCP, FTS5, HTMX, WAL, SAPISIDHASH) that is never introduced.

## Findings

1. **[BLOCKER]** "Nine of ten comparable projects have under five stars and six have commits only on their creation day." (lines 26–27) — no sample, no list, no date, no method; the reader cannot check any of the three counts, and this uncheckable survey is what licenses "Existing tools solve the fetching half and die at the archive half".

2. **[MAJOR]** "Measured language mix of the corpus: 2,776 English, 981 German, 1,218 ambiguous." (line 182) — the three figures sum to 4,975, which is the *visit* count from CUJ 1 (line 72), not the 4,253-video corpus; the unit being counted is never stated.

3. **[MAJOR]** "It exposes that archive two ways only - a small web UI for browsing and an MCP server for any agent." (line 14) — MCP is never expanded anywhere in the range, and the same unnamed-terms problem recurs with FTS5 (line 52), HTMX (line 121), WAL (line 239) and SAPISIDHASH (line 202).

4. **[MAJOR]** "**The thumbnail is a URL, not a download job.**" (line 139) — contradicted two lines later by "one GET at fetch time, stored under `/data/thumbs`", which is a download; the bolded claim and its own body describe different mechanisms.

5. **[MAJOR]** "Store a summary per video that is good enough to replace watching it again" (lines 34–35) — an unfalsifiable goal with no acceptance test, so the Goals list cannot be checked as met or unmet.

6. **[NIT]** "Segment rows ≈ 200 per video." (line 242) — no source and no relation to the 30 KB transcript estimate it sits beside, so the storage total rests on an unexplained figure.

7. **[NIT]** "**Pros**: Familiar UI tooling, room to grow, easier if a second user appears." (line 221) — "room to grow" is filler with no checkable content, restated as "room to grow" again at line 222.

8. **[NIT]** "`/import` requires a bearer token compared in constant time." (line 234) — a security property asserted with no named mechanism, so a reader cannot tell whether it means `hmac.compare_digest` or something weaker.

**Strongest counter-argument:** The jargon is the audience's own vocabulary — a document read by the author plus one agent gains nothing from defining HTMX or WAL, and expanding every acronym would lengthen a spec that is otherwise commendably terse.
