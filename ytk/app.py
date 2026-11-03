"""Service: web UI, one worker, ingest. Phase 2 adds fetch + summaries."""
import asyncio
import contextlib
import hmac
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import db, digest, export, ingest, worker
from .config import load
from .mcp_server import inner as mcp_app
from .mcp_server import mcp

cfg = load()
PKG = Path(__file__).parent
templates = Jinja2Templates(directory=str(PKG / "templates"))


@asynccontextmanager
async def lifespan(app):
    conn = db.connect(cfg.db)
    db.init(conn)
    app.state.conn = conn
    stuck = db.resume_stuck(conn)
    if stuck:
        print(f"resumed {stuck} interrupted row(s)")
    stop = asyncio.Event()
    task = None
    if os.environ.get("YTK_WORKER", "1") != "0":
        task = asyncio.create_task(worker.run(cfg, stop))
    app.state.task = task
    try:
        # The mounted sub-app's own lifespan never runs, so its session manager
        # must be started here or every MCP request raises "Task group is not
        # initialized".
        async with mcp.session_manager.run():
            yield
    finally:
        stop.set()
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, RuntimeError):
                pass
        conn.close()


app = FastAPI(title="yt-knowledge", lifespan=lifespan)


def _is_form(request: Request) -> bool:
    """Browsers post forms; scripts post JSON. One predicate, or five copies."""
    return (request.headers.get("content-type") or "").startswith(
        ("application/x-www-form-urlencoded", "multipart/form-data"))


def _bearer_ok(request: Request) -> bool:
    got = request.headers.get("authorization", "")
    if not got.startswith("Bearer "):
        return False
    return hmac.compare_digest(got[7:], cfg.secret)


@app.middleware("http")
async def gate(request: Request, call_next):
    path = request.url.path
    if path in ("/healthz", "/login"):
        return await call_next(request)
    if request.session.get("ok") or _bearer_ok(request):
        return await call_next(request)
    # Return, never raise: HTTPException inside BaseHTTPMiddleware surfaces as 500.
    if path.startswith(("/import", "/mcp")):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


# Added after the gate so it wraps it: the gate can then read request.session.
app.add_middleware(SessionMiddleware, secret_key=cfg.secret, same_site="strict",
                   https_only=False)      # LAN is plain HTTP; Secure would never stick


@app.get("/healthz")
async def healthz(request: Request):
    """Unauthenticated on purpose: it is the Docker healthcheck. Stays 200 when
    paused, or a deliberate pause would mark the container unhealthy."""
    task = getattr(request.app.state, "task", None)
    return {"ok": True,
            "paused": worker.paused(cfg),
            "worker": task is not None and not task.done(),
            "blocked": db.blocked_count(request.app.state.conn),
            "counts": db.queue_counts(request.app.state.conn)}


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    given = str(form.get("password", ""))
    if hmac.compare_digest(given, cfg.secret):
        request.session["ok"] = True
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html",
                                      {"error": "wrong secret"}, status_code=401)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def library(request: Request, q: str = "", page: int = 1, per: int = 50):
    conn = request.app.state.conn
    where, args = "", []
    hits = db.search(conn, q) if q.strip() else []
    if hits:
        return templates.TemplateResponse(request, "search.html", {"hits": hits, "q": q})
    if q.strip():
        where = "WHERE v.title LIKE ? OR v.channel LIKE ?"
        args = [f"%{q.strip()}%"] * 2
    total = conn.execute(f"SELECT COUNT(*) FROM videos v {where}", args).fetchone()[0]
    rows = conn.execute(
        f"""SELECT v.id, v.title, v.channel, v.duration_s, v.state,
                   COUNT(w.watched_at) AS watches, MAX(w.watched_at) AS last_watched
            FROM videos v LEFT JOIN watches w ON w.video_id = v.id
            {where} GROUP BY v.id
            ORDER BY last_watched DESC NULLS LAST LIMIT ? OFFSET ?""",
        [*args, per, (page - 1) * per]).fetchall()
    # Only the fetched rows have an image; asking for the rest renders a broken
    # icon 1,900 times. One stat per visible row is cheaper than a 404 storm.
    rows = [{**dict(r), "has_thumb": os.path.exists(
        os.path.join(cfg.thumbs, f"{r['id']}.jpg"))} for r in rows]
    return templates.TemplateResponse(request, "library.html", {
        "rows": rows, "q": q, "page": page, "per": per, "total": total,
        "pages": max(1, -(-total // per))})


@app.get("/thumb/{video_id}.jpg")
async def thumb(request: Request, video_id: str):
    """The file name is the video id and the directory is config, so a stored
    path that was written on a different machine cannot break the image."""
    from fastapi.responses import FileResponse
    if not ingest.VIDEO_ID.match(video_id):
        return JSONResponse({"error": "bad id"}, status_code=400)
    path = os.path.join(cfg.thumbs, f"{video_id}.jpg")
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/v/{video_id}", response_class=HTMLResponse)
async def video_page(request: Request, video_id: str):
    conn = request.app.state.conn
    v = conn.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
    if not v:
        return HTMLResponse("no such video", status_code=404)
    s = conn.execute("SELECT * FROM summaries WHERE video_id=? ORDER BY created_at DESC LIMIT 1",
                     (video_id,)).fetchone()
    bullets = json.loads(s["bullets_json"]) if s and s["bullets_json"] else []
    chaps = conn.execute("SELECT start_s, title FROM chapters WHERE video_id=? ORDER BY start_s",
                         (video_id,)).fetchall()
    segs = conn.execute("SELECT start_s, text FROM segments WHERE video_id=? ORDER BY start_s",
                        (video_id,)).fetchall()
    watches = conn.execute("SELECT watched_at, dwell_s, sources FROM watches WHERE video_id=? "
                           "ORDER BY watched_at DESC", (video_id,)).fetchall()
    return templates.TemplateResponse(request, "video.html", {
        "v": v, "s": s, "bullets": bullets, "chapters": chaps, "segments": segs,
        "watches": watches})


@app.get("/digest", response_class=HTMLResponse)
async def digest_page(request: Request, week: str = ""):
    conn = request.app.state.conn
    if week:
        try:
            week = digest.parse_week(week)[2]
        except ValueError:
            return HTMLResponse("bad week, expected YYYY-Www", status_code=400)
        row = conn.execute("SELECT * FROM digests WHERE week=?", (week,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM digests ORDER BY week DESC LIMIT 1").fetchone()
    return templates.TemplateResponse(request, "digest.html",
                                      {"d": row, "week": week or digest.week_of()[2]})


@app.post("/digest/run")
async def digest_run(request: Request):
    conn = request.app.state.conn
    is_form = _is_form(request)
    body = {}
    if not is_form:
        try:
            body = await request.json()
        except ValueError:      # no body: treat as a plain run
            body = {}
    try:
        out = digest.generate(conn, force=bool(body.get("force")) or is_form,
                              week_arg=body.get("week") or None)
    except RuntimeError as exc:                     # no endpoint configured
        return JSONResponse({"error": str(exc)}, status_code=503)
    except (TimeoutError, OSError) as exc:          # the LLM did not answer in time
        return JSONResponse({"error": f"llm timeout: {exc}"}, status_code=504)
    if is_form:
        return RedirectResponse("/digest", status_code=303)
    return {k: v for k, v in out.items() if k != "markdown"}


@app.post("/export/md")
async def export_md(request: Request):
    # Never cwd-relative: in the image cwd is /app, root-owned and outside the
    # volume, so the notes would be unwritable and lost on recreate.
    dest = os.environ.get("YTK_EXPORT") or os.path.join(
        os.path.dirname(os.path.abspath(cfg.db)), "export")
    n = export.write_tree(request.app.state.conn, dest)
    if _is_form(request):
        return RedirectResponse("/queue", status_code=303)
    return {"written": n, "path": dest}


@app.post("/admin/resummarize")
async def resummarize(request: Request):
    """Hand every summarised row back to the worker when its model is stale."""
    conn = request.app.state.conn
    model = os.environ.get("YTK_LLM_MODEL", "")
    if not model:
        return JSONResponse({"error": "YTK_LLM_MODEL is not set"}, status_code=503)
    # 'fetched' is claimable (see db.CLAIM_SQL): the row has segments and needs
    # only a summary, so the worker picks it up instead of stranding it.
    cur = conn.execute("""UPDATE videos SET state='fetched', next_attempt_at=NULL
                          WHERE state='ready' AND id IN
                          (SELECT video_id FROM summaries WHERE model IS NOT ?)""", (model,))
    if _is_form(request):
        return RedirectResponse("/queue", status_code=303)
    return {"reset": cur.rowcount, "model": model}


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    conn = request.app.state.conn
    return templates.TemplateResponse(request, "settings.html", {
        "min_min": int(db.get_setting(conn, "min_duration_s") or 120) // 60,
        "max_min": int(db.get_setting(conn, "max_duration_s") or 0) // 60,
        "export": os.environ.get("YTK_EXPORT") or os.path.join(
            os.path.dirname(os.path.abspath(cfg.db)), "export")})


@app.post("/settings")
async def settings_save(request: Request):
    conn = request.app.state.conn
    form = await request.form() if _is_form(request) else await request.json()
    clamp = lambda v, hi: max(0, min(int(v), hi))

    min_s = clamp(form.get("min_min") or 0, 600) * 60
    max_s = clamp(form.get("max_min") or 0, 600) * 60
    # A ceiling below the floor would archive every video and fetch nothing.
    if max_s and max_s <= min_s:
        min_s = max(0, max_s - 60)
    with conn:
        db.set_setting(conn, "min_duration_s", min_s)
        db.set_setting(conn, "max_duration_s", max_s)
    if _is_form(request):
        return RedirectResponse("/settings", status_code=303)
    return {"min_duration_s": min_s, "max_duration_s": max_s}


@app.get("/queue", response_class=HTMLResponse)
async def queue(request: Request):
    conn = request.app.state.conn
    counts = db.queue_counts(conn)
    failures = conn.execute(
        "SELECT id, title, fail_stage, fail_reason, attempts FROM videos "
        "WHERE state='failed' ORDER BY last_seen DESC LIMIT 50").fetchall()
    task = getattr(request.app.state, "task", None)
    return templates.TemplateResponse(request, "queue.html", {
        "counts": counts, "failures": failures,
        "paused": worker.paused(cfg),
        "blocked": db.blocked_count(conn),
        "last_fail": db.last_failure(conn),
        "disabled": task is None})


@app.post("/import")
async def import_(request: Request):
    conn = request.app.state.conn
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        blob = await upload.read() if upload is not None else b""
        records = ingest.parse_takeout(blob)
    else:
        body = await request.body()
        records = ingest.parse_jsonl(body)
    out = {"received": len(records), "new": 0, "merged": 0, "rejected": 0}
    for rec in records:
        keep, _reason = ingest.ingest_filter(rec)
        if not keep:
            out["rejected"] += 1
            continue
        with conn:
            db.upsert_video(conn, rec)
            out[db.add_watch(conn, rec["video_id"], rec["watched_at"],
                             rec["dwell_s"], rec["source"])] += 1
    return out


@app.post("/import/url")
async def import_url(request: Request):
    is_form = _is_form(request)
    if is_form:
        url = str((await request.form()).get("url", ""))
    else:
        url = (await request.json()).get("url", "")
    out = {"received": 0, "new": 0, "merged": 0, "rejected": 0}
    for rec in ingest.parse_url(url):
        out["received"] += 1
        keep, _ = ingest.ingest_filter(rec)
        if not keep:
            out["rejected"] += 1
            continue
        with request.app.state.conn as conn:
            db.upsert_video(conn, rec)
            out[db.add_watch(conn, rec["video_id"], rec["watched_at"],
                             rec["dwell_s"], rec["source"])] += 1
    if is_form:
        return RedirectResponse("/queue", status_code=303)
    return out


def _marker(state: bool) -> None:
    """Sync on purpose: one stat/create beside the database, sub-millisecond.
    A thread hop here would cost more than the call."""
    path = os.path.join(os.path.dirname(os.path.abspath(cfg.db)), "paused")
    if state:
        open(path, "a").close()
    else:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)


@app.post("/admin/pause")
async def pause(request: Request):
    _marker(True)
    return await _control_done(request, {"paused": True})


@app.post("/admin/resume")
async def resume(request: Request):
    _marker(False)
    return await _control_done(request, {"paused": False})


@app.post("/videos/{video_id}/retry")
async def retry_one(request: Request, video_id: str):
    if not ingest.VIDEO_ID.match(video_id):
        return JSONResponse({"error": "bad id"}, status_code=400)
    n = db.retry(request.app.state.conn, video_id)
    if not n:
        return JSONResponse({"error": "no such video"}, status_code=404)
    return await _control_done(request, {"retried": n, "id": video_id})


@app.post("/queue/retry-failed")
async def retry_failed(request: Request):
    n = db.retry_failed(request.app.state.conn)
    return await _control_done(request, {"retried": n})


async def _control_done(request: Request, payload: dict):
    """Form posts redirect back to the queue; scripts get the numbers."""
    if _is_form(request):
        return RedirectResponse("/queue", status_code=303)
    return {"ok": True, **payload}


# Mounted last: a Mount at a path prefix competes with the routes, so every
# FastAPI route must be registered before it.
app.mount("/", mcp_app)   # inner serves exactly /mcp


def _cli_query(argv: list[str]) -> int:
    """Offline query, no server: what an agent or a shell actually needs."""
    import argparse
    ap = argparse.ArgumentParser(prog="ytk q")
    ap.add_argument("query", nargs="?", default=None)
    ap.add_argument("--id", dest="video_id")
    ap.add_argument("--recent", type=int, metavar="DAYS")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--transcript", action="store_true", help="with --id: print lines")
    ap.add_argument("--limit", type=int, default=8)
    a = ap.parse_args(argv)

    conn = db.connect(cfg.db)
    db.init(conn)
    if a.stats:
        c = db.queue_counts(conn)
        segs = conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
        sums = conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
        print(f"{c['total']} videos, {segs} transcript lines, {sums} summaries")
        print(", ".join(f"{k}={c[k]}" for k in db.STATE))
        return 0
    if a.video_id:
        v = conn.execute("SELECT * FROM videos WHERE id=?", (a.video_id,)).fetchone()
        if not v:
            print("no such video")
            return 1
        s = conn.execute("SELECT * FROM summaries WHERE video_id=?", (a.video_id,)).fetchone()
        print(f"{v['title']}  ({v['channel']}, {v['duration_s']}s, {v['published_at']})")
        print(f"https://youtu.be/{a.video_id}")
        if s:
            print(f"\n{s['headline']}")
            print("keywords: " + ", ".join(json.loads(s["keywords_json"] or "[]")))
            for t, text in json.loads(s["bullets_json"] or "[]"):
                print(f"  [{int(t) // 60}:{int(t) % 60:02d}] {text}")
        if a.transcript:
            print()
            for start, text in conn.execute(
                    "SELECT start_s, text FROM segments WHERE video_id=? ORDER BY start_s",
                    (a.video_id,)):
                print(f"[{int(start) // 60}:{int(start) % 60:02d}] {text}")
        return 0
    if a.recent:
        for r in conn.execute(
                "SELECT v.id, v.title, MAX(w.watched_at) last FROM watches w "
                "JOIN videos v ON v.id=w.video_id "
                "WHERE w.watched_at >= datetime('now', ?) "
                "GROUP BY v.id ORDER BY last DESC", (f"-{a.recent} days",)):
            print(f"{r['last'][:10]}  {r['id']}  {r['title'] or ''}")
        return 0
    if not a.query:
        ap.print_help()
        return 2
    hits = db.search(conn, a.query, a.limit)
    if not hits:
        print("no hits")
        return 0
    for h in hits:
        if h["kind"] == "transcript":
            print(f"[{h['id']}] {h['title']}  @{int(h['ts']) // 60}:{int(h['ts']) % 60:02d} "
                  f"https://youtu.be/{h['id']}?t={int(h['ts'])}")
            print(f"    {h['hit']}")
        else:
            print(f"[{h['id']}] {h['title']}")
            print(f"    {h['headline']}")
    return 0


def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in ("q", "query"):
        raise SystemExit(_cli_query(sys.argv[2:]))
    import uvicorn
    host, _, port = cfg.bind.rpartition(":")
    uvicorn.run(app, host=host or "127.0.0.1", port=int(port or 8488))


if __name__ == "__main__":
    main()
