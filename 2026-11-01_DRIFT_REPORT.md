# Drift report - yt-knowledge

Updated 2026-11-02 after the controls increment. Supersedes the 2026-11-01 report,
which was written against a stale read of `app.py` and listed four endpoints as
missing that had already been implemented.

## Resolved by the controls increment

| Was | Now |
|---|---|
| `/digest`, `/digest/run`, `/export/md`, `/admin/resummarize` unreachable from the UI | all wired; only `digest` has its own page, the rest are on `/queue` |
| `POST /import/url` 500 on a form post (`await request.json()` unconditional) | content-type branch; form field `url`; 303 back to queue |
| `/export/md` wrote to cwd-relative `data/export` = `/app/data/export`, outside the volume | `Path(cfg.db).parent/export` = `/data/export`, 48 entries verified on the NAS |
| `state='fetched'` unclaimable, so Resummarize stranded every ready row | `CLAIM_SQL` accepts `fetched`; a crash between fetch and summarize no longer strands a row |
| retry would tie with 1890 pending rows | `next_attempt_at = -1` sorts ahead of the backlog |
| backoff `60 * 2**attempts` uncapped (68 h at 12 failures) | capped at 3600 s |
| no pause, no retry, no status | `POST /admin/pause|resume`, `/videos/{id}/retry`, `/queue/retry-failed`, `/healthz` gains paused/worker/blocked |
| block path's `next_attempt_at=now+600` was reordered but never enforced | the claim now applies the time filter to `pending` too |

## Still drifting

| Document says | Code does |
|---|---|
| `digests` table feeds a weekly view | the table is read by `/digest` and written by `digest.generate`; `POST /digest/run` is unauthenticated-by-bearer only, no per-week cron |
| MCP exposes `run_digest` | MCP is read-only; the tool is absent from `mcp_server.py` |
| `YTK_EXPORT` documented | now in `.env.example`, absent from the NAS `.env` (the default is correct) |
| transcript throughput ~150 videos/hour | unmeasured since the IP block; `pending` has not drained in 24 h |

## Accepted, not drift

- No failure alert (non-goal): a row can sit `failed` for up to one hour.
- No CSRF token: `SameSite=strict` is the only defence, recorded in the design.
- Pause is a marker file with no author or history; it becomes a `settings`
  table when a second operator exists.
