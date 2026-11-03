"""Read-only MCP surface. No write tool exists here, by design: digest generation
and imports stay on the web side so there is one owner for every write."""
import json
import os
import sqlite3

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from . import db

DB_PATH = os.environ.get("YTK_DB", "data/ytk.sqlite")
PORT = (os.environ.get("YTK_BIND", "0.0.0.0:8488")).rpartition(":")[2] or "8488"
mcp = MCPServer("yt-knowledge")

# Built at module level: mcp.session_manager only exists after this call, and the
# mounted sub-app's lifespan never runs on its own.
inner = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    transport_security=TransportSecuritySettings(
        allowed_hosts=[f"127.0.0.1:{PORT}", f"localhost:{PORT}",
                       f"192.168.178.40:{PORT}", f"nas:{PORT}", "ytk:8488"],
        allowed_origins=["*"]))


def _conn():
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _summary(c, vid):
    r = c.execute("SELECT * FROM summaries WHERE video_id=?", (vid,)).fetchone()
    if not r:
        return None
    return {"headline": r["headline"],
            "bullets": json.loads(r["bullets_json"] or "[]"),
            "tags": json.loads(r["tags_json"] or "[]"),
            "keywords": json.loads(r["keywords_json"] or "[]"),
            "short_en": r["short_en"], "lang": r["lang"]}


@mcp.tool()
def search_archive(query: str, limit: int = 15) -> str:
    """Search the archive. Transcript hits and summary hits both come back, with
    a timestamp for anything found in speech."""
    c = _conn()
    try:
        hits = db.search(c, query, limit)
    finally:
        c.close()
    if not hits:
        return "no hits"
    out = []
    for h in hits:
        at = ""
        if h["kind"] == "transcript":
            at = f" @{int(h['ts']) // 60}:{int(h['ts']) % 60:02d} " \
                 f"https://youtu.be/{h['id']}?t={int(h['ts'])}"
        text = h["headline"] if h["kind"] == "summary" else h["hit"]
        out.append(f"- [{h['id']}] {h['title'] or h['id']} ({h['channel'] or '?'})"
                   f"{at}\n  {text}")
    return "\n".join(out)


@mcp.tool()
def get_video(video_id: str) -> str:
    """Metadata, summary, keywords and chapters for one video."""
    c = _conn()
    try:
        v = c.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        if not v:
            return "no such video"
        s = _summary(c, video_id)
        chaps = c.execute("SELECT start_s, title FROM chapters WHERE video_id=? "
                          "ORDER BY start_s", (video_id,)).fetchall()
        watches = c.execute("SELECT watched_at, dwell_s FROM watches WHERE video_id=? "
                            "ORDER BY watched_at DESC", (video_id,)).fetchall()
    finally:
        c.close()
    parts = [f"# {v['title'] or video_id}",
             f"https://www.youtube.com/watch?v={video_id}",
             f"channel: {v['channel']} · {v['duration_s']}s · {v['published_at']}"]
    if s:
        parts += ["", s["headline"], "", "keywords: " + ", ".join(s["keywords"]),
                  "tags: " + ", ".join(s["tags"])]
        for t, text in s["bullets"]:
            parts.append(f"- [{int(t) // 60}:{int(t) % 60:02d}] {text}")
    if chaps:
        parts += ["", "chapters:"] + [f"- {int(ch[0]) // 60}:{int(ch[0]) % 60:02d} {ch[1]}"
                                      for ch in chaps]
    if watches:
        parts.append("")
        parts += [f"watched {w['watched_at']} ({w['dwell_s'] or 0}s)" for w in watches]
    return "\n".join(parts)


@mcp.tool()
def get_transcript(video_id: str, around: int = -1, limit: int = 400) -> str:
    """Transcript lines. `around` is a second offset; omit it for the start."""
    c = _conn()
    try:
        if around >= 0:
            rows = c.execute("SELECT start_s, text FROM segments WHERE video_id=? "
                             "AND start_s BETWEEN ? AND ? ORDER BY start_s",
                             (video_id, around - 120, around + 240)).fetchall()
        else:
            rows = c.execute("SELECT start_s, text FROM segments WHERE video_id=? "
                             "ORDER BY start_s LIMIT ?", (video_id, limit)).fetchall()
    finally:
        c.close()
    return "\n".join(f"[{int(s) // 60}:{int(s) % 60:02d}] {t}" for s, t in rows) or "no transcript"


@mcp.tool()
def list_channel(name: str, limit: int = 30) -> str:
    """Videos archived from a channel whose name contains `name`."""
    c = _conn()
    try:
        rows = c.execute("""SELECT v.id, v.title, v.published_at,
                                   (SELECT COUNT(*) FROM watches w WHERE w.video_id=v.id) n
                            FROM videos v WHERE v.channel LIKE ? AND v.title IS NOT NULL
                            ORDER BY v.published_at DESC LIMIT ?""",
                         (f"%{name}%", limit)).fetchall()
    finally:
        c.close()
    return "\n".join(f"- [{r['id']}] {r['title']} ({r['published_at']}) watched {r['n']}x"
                     for r in rows) or "no videos"


@mcp.tool()
def recent(days: int = 30) -> str:
    """What was watched in the last N days, with the video's headline."""
    c = _conn()
    try:
        rows = c.execute("""SELECT v.id, v.title, MAX(w.watched_at) last, s.headline
                            FROM watches w JOIN videos v ON v.id=w.video_id
                            LEFT JOIN summaries s ON s.video_id=v.id
                            WHERE w.watched_at >= datetime('now', ?)
                            GROUP BY v.id ORDER BY last DESC LIMIT 50""",
                         (f"-{int(days)} days",)).fetchall()
    finally:
        c.close()
    return "\n".join(f"- {r['last'][:10]} [{r['id']}] {r['title'] or r['id']}"
                     f"\n  {r['headline'] or ''}" for r in rows) or "nothing"


@mcp.tool()
def stats() -> str:
    """Corpus size and per-state counts."""
    c = _conn()
    try:
        counts = db.queue_counts(c)
        segs = c.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
        sums = c.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
    finally:
        c.close()
    return (f"{counts['total']} videos, {segs} transcript lines, {sums} summaries\n"
            + ", ".join(f"{k}={counts[k]}" for k in
                        ("pending", "fetched", "ready", "failed", "no_caption", "archived")))
