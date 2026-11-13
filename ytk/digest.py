"""Weekly digest: one LLM call over the week's summaries."""
import asyncio
import json
import os
import traceback
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import db, summarize

BERLIN = ZoneInfo("Europe/Berlin")
SETTLE_DAYS = 4   # give the fetcher the weekend before freezing a week


def week_of(day: date | None = None) -> tuple[date, date, str]:
    """Monday 00:00 to the next Monday, in Berlin time."""
    day = day or datetime.now(BERLIN).date()
    monday = day - timedelta(days=day.weekday())
    iso = monday.isocalendar()
    return monday, monday + timedelta(days=7), f"{iso.year}-W{iso.week:02d}"


def last_week() -> tuple[date, date, str]:
    """The ISO week that just ended: what a scheduled run should summarise."""
    return week_of(datetime.now(BERLIN).date() - timedelta(days=7))


def parse_week(text: str) -> tuple[date, date, str]:
    """'2025-W31' -> that ISO week's Monday..Monday, or raise ValueError."""
    year, _, num = text.partition("-W")
    monday = date.fromisocalendar(int(year), int(num), 1)
    iso = monday.isocalendar()
    return monday, monday + timedelta(days=7), f"{iso.year}-W{iso.week:02d}"


def should_wait(conn, start: date, end: date, today: date | None = None) -> int:
    """Unsettled videos from the week, unless the week is old enough to freeze.

    A digest generated while its week is still being fetched says "2 of 99" and
    then stays wrong for ever, because the row is never regenerated.
    """
    lo, hi = window(start, end)
    n = conn.execute("""
        SELECT COUNT(*) FROM watches w JOIN videos v ON v.id = w.video_id
        WHERE w.watched_at >= ? AND w.watched_at < ?
          AND v.state IN ('pending','fetched','running','failed')""",
        (lo, hi)).fetchone()[0]
    if n and (today or datetime.now(BERLIN).date()) < end + timedelta(days=SETTLE_DAYS):
        return n
    return 0


def window(start: date, end: date) -> tuple[str, str]:
    """Berlin calendar dates -> the UTC instants watches are stored in.

    The date is chosen in Berlin but `watches.watched_at` is UTC; comparing
    midnight as a `Z` literal shifted both edges by the CET/CEST offset.
    """
    lo = datetime.combine(start, time.min, BERLIN).astimezone(UTC)
    hi = datetime.combine(end, time.min, BERLIN).astimezone(UTC)
    return f"{lo:%Y-%m-%dT%H:%M:%SZ}", f"{hi:%Y-%m-%dT%H:%M:%SZ}"


def generate(conn, force: bool = False, model: str | None = None,
             week_arg: str | None = None) -> dict:
    start, end, week = parse_week(week_arg) if week_arg else week_of()
    existing = conn.execute("SELECT * FROM digests WHERE week=?", (week,)).fetchone()
    if existing and not force:
        return {"week": week, "generated": False, "reason": "already exists",
                "markdown": existing["markdown"], "video_count": existing["video_count"]}

    lo, hi = window(start, end)
    rows = conn.execute("""
        SELECT v.id, v.title, v.channel, MAX(w.watched_at) AS last,
               s.headline, s.bullets_json
        FROM watches w JOIN videos v ON v.id = w.video_id
        JOIN summaries s ON s.video_id = v.id
        WHERE w.watched_at >= ? AND w.watched_at < ?
        GROUP BY v.id ORDER BY last""", (lo, hi)).fetchall()
    # The prompt must state how much of the week it actually sees.
    watched = conn.execute(
        "SELECT COUNT(DISTINCT video_id) FROM watches WHERE watched_at >= ? "
        "AND watched_at < ?", (lo, hi)).fetchone()[0]
    if not rows:
        return {"week": week, "generated": False,
                "reason": "no summarised videos that week",
                "video_count": 0, "watched": watched}

    lines = []
    for r in rows:
        bullets = json.loads(r["bullets_json"] or "[]")[:2]
        claims = "".join(f" [{int(t) // 60}:{int(t) % 60:02d}] {s}" for t, s in bullets)
        lines.append(f"- {r['last'][:10]} · {r['title']} ({r['channel']}) — "
                     f"{r['headline']}{claims}")
    prompt = [{"role": "system", "content": "You write a short weekly digest in plain "
                                             "text. No markdown, no preamble."},
              {"role": "user", "content":
               f"This person watched {watched} videos in {week}; "
               f"{len(rows)} of them are summarised below.\n\n" + "\n".join(lines) +
               "\n\nWrite a digest in two sections:\n"
               "What you watched\nThree to six sentences naming the patterns and the "
               "channels that recurred. Say how many of the week's videos this covers.\n\n"
               "What you got out of it\nThree to eight lines, each one a claim or fact "
               "worth keeping, attributed to the video it came from. Use only the "
               "material above. No filler."}]
    text, answered = summarize.call(prompt, timeout=240)
    conn.execute("""INSERT INTO digests(week, generated_at, markdown, model, video_count)
                    VALUES (?,?,?,?,?)
                    ON CONFLICT(week) DO UPDATE SET generated_at=excluded.generated_at,
                      markdown=excluded.markdown, model=excluded.model,
                      video_count=excluded.video_count""",
                 (week, db.now_iso(), text, model or answered, len(rows)))
    return {"week": week, "generated": True, "video_count": len(rows),
            "watched": watched, "markdown": text}


async def loop(cfg, stop: asyncio.Event) -> None:
    """One digest per day for the week that ended.

    generate() returns "already exists" for a week it has written, so the
    check is free and a restart cannot duplicate a digest. In-process on
    purpose: the app already owns a lifetime task and the database.
    """
    while not stop.is_set():
        if os.environ.get("YTK_LLM_BASE"):
            try:
                await asyncio.to_thread(_run, cfg)
            except Exception:  # noqa: BLE001 - one bad week must not kill the loop
                traceback.print_exc()
        try:
            await asyncio.wait_for(stop.wait(), timeout=3600)
        except TimeoutError:
            pass


def _run(cfg) -> dict:
    """Own connection: the app's is opened check_same_thread=True."""
    conn = db.connect(cfg.db, same_thread=False)
    start, end, week = last_week()
    try:
        if should_wait(conn, start, end):
            return {"week": week, "generated": False, "reason": "week still settling"}
        return generate(conn, week_arg=week)
    finally:
        conn.close()
