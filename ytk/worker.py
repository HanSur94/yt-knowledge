"""One worker, one row in flight. The tick order is fixed on purpose; see the
design doc's app.py section."""
import asyncio
import os
import time
import traceback

from . import db, fetch, summarize

BLOCK_PAUSE = 300   # seconds; a block is global, so rest the whole worker


def paused(cfg) -> bool:
    """Marker beside the database: on the volume in the image (`/data`), in
    `data/` on a laptop. Derived, never configured."""
    return os.path.exists(os.path.join(os.path.dirname(os.path.abspath(cfg.db)), "paused"))


def _tick(conn, cfg) -> str | None:
    """One row start to finish. Blocking; the caller runs it in a thread.

    The pause guard lives here, not in the loop: _tick has two callers, and one
    of them is scripts/backfill.py, the documented way to run a long crawl.
    """
    if paused(cfg):
        return None
    row = db.claim_next(conn)
    if row is None:
        return None
    vid = row["id"]
    try:
        if not conn.execute("SELECT 1 FROM segments WHERE video_id=? LIMIT 1",
                            (vid,)).fetchone():
            meta = fetch.metadata(vid)
            skip = _duration_skip(
                meta.get("duration_s"),
                int(db.get_setting(conn, "min_duration_s") or 120),
                int(db.get_setting(conn, "max_duration_s") or 0))
            if skip:
                # Metadata and a thumbnail still land, so the row is readable in
                # the library; only the transcript call is skipped.
                db.save_fetch(conn, vid, meta, [], [], meta["chapters"])
                path = _safe(fetch.thumbnail, vid, cfg.thumbs, default=None)
                if path:
                    conn.execute("UPDATE videos SET thumb_path=? WHERE id=?", (path, vid))
                db.finish(conn, vid, "archived", skip)
                return vid
            try:
                segments = fetch.transcript(vid, ("de", "en"))
            except fetch.NoCaption:
                db.save_fetch(conn, vid, meta, [], [], meta["chapters"])
                db.finish(conn, vid, "no_caption", None)
                return vid
            skips = _safe(fetch.sponsors, vid, default=[])
            path = _safe(fetch.thumbnail, vid, cfg.thumbs, default=None)
            db.save_fetch(conn, vid, meta, segments, skips, meta["chapters"])
            if path:
                conn.execute("UPDATE videos SET thumb_path=? WHERE id=?", (path, vid))
        db.finish(conn, vid, "fetched")

        if not os.environ.get("YTK_LLM_BASE"):
            return vid                     # transcripts only until an endpoint exists
        video = dict(conn.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone())
        segs = [(r[0], r[1]) for r in conn.execute(
            "SELECT start_s, text FROM segments WHERE video_id=? ORDER BY start_s", (vid,))]
        chaps = [(r[0], r[1]) for r in conn.execute(
            "SELECT start_s, title FROM chapters WHERE video_id=? ORDER BY start_s", (vid,))]
        skips = [(r[0], r[1], r[2]) for r in conn.execute(
            "SELECT start_s, end_s, category FROM skips WHERE video_id=?", (vid,))]
        s = summarize.summarize(video, segs, chaps, skips)
        s["title"], s["channel"] = video["title"], video["channel"]
        db.save_summary(conn, vid, s)
        return vid
    except fetch.Blocked as exc:
        # Send the row to the back of the queue, not the front: leaving
        # next_attempt_at NULL makes a blocked crawl re-claim the same rows and
        # starve every retry that would have succeeded.
        conn.execute("UPDATE videos SET state='pending', next_attempt_at=? WHERE id=?",
                     (int(time.time()) + 600, vid))
        return f"blocked:{exc}"
    except fetch.Gone as exc:
        db.finish(conn, vid, "archived", f"gone: {exc}"[:200])
        return vid
    except fetch.NoCaption as exc:
        db.finish(conn, vid, "no_caption", str(exc)[:200])
        return vid
    except Exception as exc:  # noqa: BLE001 - one bad row must not stop the worker
        stage = "fetch" if not conn.execute(
            "SELECT 1 FROM segments WHERE video_id=? LIMIT 1", (vid,)).fetchone() else "summarize"
        db.fail(conn, vid, stage, f"{type(exc).__name__}: {exc}")
        return vid


def _duration_skip(duration_s, floor: int, ceiling: int) -> str | None:
    """The length window. Long videos are archived before the transcript call,
    because that call is the expensive one and a 3-hour stream is not what this
    archive is for. duration_s is None until metadata lands."""
    if duration_s and floor and duration_s < floor:
        return f"shorter than {floor // 60} min"
    if ceiling and duration_s and duration_s > ceiling:
        return f"longer than {ceiling // 60} min"
    return None


def _safe(fn, *args, default=None):
    """Extra args are forwarded. Without that, fetch.thumbnail(vid, dir) was
    called as thumbnail(vid), raised TypeError, and stored the *directory* as
    the thumbnail path: every image 404'd and no file was ever written."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 - sponsors and thumbnails are optional
        return default


async def run(cfg, stop: asyncio.Event):
    conn = db.connect(cfg.db, same_thread=False)   # own connection, own threads
    try:
        await _loop(conn, cfg, stop)
    finally:
        conn.close()


async def _loop(conn, cfg, stop: asyncio.Event):
    while not stop.is_set():
        if paused(cfg):
            # asyncio, not time.sleep: the web server shares this event loop.
            # wait_for, not bare sleep: a stop must not wait out the timeout.
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass
            continue
        try:
            done = await asyncio.to_thread(_tick, conn, cfg)
        except Exception:  # noqa: BLE001 - the loop outlives its failures
            traceback.print_exc()
            done = None
        if isinstance(done, str) and done.startswith("blocked"):
            print(f"  {done} - pausing {BLOCK_PAUSE}s", flush=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=BLOCK_PAUSE)
            except TimeoutError:
                pass
        elif done is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass
        else:
            await asyncio.sleep(cfg.sleep)
