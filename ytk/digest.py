"""Weekly digest: one LLM call over the week's summaries."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import db, summarize

BERLIN = ZoneInfo("Europe/Berlin")


def week_of(day: date | None = None) -> tuple[date, date, str]:
    """Monday 00:00 to the next Monday, in Berlin time."""
    day = day or datetime.now(BERLIN).date()
    monday = day - timedelta(days=day.weekday())
    iso = monday.isocalendar()
    return monday, monday + timedelta(days=7), f"{iso.year}-W{iso.week:02d}"


def parse_week(text: str) -> tuple[date, date, str]:
    """'2025-W31' -> that ISO week's Monday..Monday, or raise ValueError."""
    year, _, num = text.partition("-W")
    monday = date.fromisocalendar(int(year), int(num), 1)
    return monday, monday + timedelta(days=7), text.upper()


def generate(conn, force: bool = False, model: str | None = None,
             week_arg: str | None = None) -> dict:
    start, end, week = parse_week(week_arg) if week_arg else week_of()
    existing = conn.execute("SELECT * FROM digests WHERE week=?", (week,)).fetchone()
    if existing and not force:
        return {"week": week, "generated": False, "reason": "already exists",
                "markdown": existing["markdown"], "video_count": existing["video_count"]}

    rows = conn.execute("""
        SELECT v.id, v.title, v.channel, MAX(w.watched_at) AS last,
               s.headline, s.bullets_json
        FROM watches w JOIN videos v ON v.id = w.video_id
        JOIN summaries s ON s.video_id = v.id
        WHERE w.watched_at >= ? AND w.watched_at < ?
        GROUP BY v.id ORDER BY last""",
        (f"{start}T00:00:00Z", f"{end}T00:00:00Z")).fetchall()
    if not rows:
        return {"week": week, "generated": False, "reason": "no summarised videos that week",
                "video_count": 0}

    lines = []
    for r in rows:
        lines.append(f"- {r['last'][:10]} · {r['title']} ({r['channel']}) — {r['headline']}")
    prompt = [{"role": "system", "content": "You write a short weekly digest. Markdown only, no preamble."},
              {"role": "user", "content":
               f"This person watched {len(rows)} videos in {week}:\n\n" + "\n".join(lines) +
               "\n\nWrite a digest in two sections:\n"
               "## What you watched\nThree to six sentences naming the patterns and the "
               "channels that recurred.\n"
               "## What you got out of it\nThree to eight bullets, each one a claim or fact "
               "worth keeping, attributed to the video it came from. No filler."}]
    text = summarize.call(prompt, timeout=240)
    conn.execute("""INSERT INTO digests(week, generated_at, html, markdown, model, video_count)
                    VALUES (?,?,?,?,?,?)
                    ON CONFLICT(week) DO UPDATE SET generated_at=excluded.generated_at,
                      html=excluded.html, markdown=excluded.markdown, model=excluded.model,
                      video_count=excluded.video_count""",
                 (week, db.now_iso(), None, text, model or "auto", len(rows)))
    return {"week": week, "generated": True, "video_count": len(rows), "markdown": text}
