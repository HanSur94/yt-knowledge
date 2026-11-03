"""SQLite layer. Schema guard is PRAGMA user_version; refuse on mismatch."""
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta

SCHEMA_VERSION = 4
STATE = ("pending", "running", "fetched", "ready", "failed", "no_caption", "archived")

SCHEMA = """
CREATE TABLE videos(
  id TEXT PRIMARY KEY,
  title TEXT, channel TEXT, channel_id TEXT,
  duration_s INTEGER, published_at TEXT, language TEXT, "desc" TEXT,
  thumb_path TEXT,
  state TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER,
  fail_stage TEXT, fail_reason TEXT,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
);
CREATE INDEX idx_videos_state ON videos(state);

CREATE TABLE watches(
  video_id TEXT NOT NULL REFERENCES videos(id),
  watched_at TEXT NOT NULL,
  dwell_s INTEGER,
  sources TEXT NOT NULL,
  UNIQUE(video_id, watched_at)
);
CREATE INDEX idx_watches_video ON watches(video_id, watched_at);

CREATE TABLE segments(
  id INTEGER PRIMARY KEY,
  video_id TEXT NOT NULL REFERENCES videos(id),
  start_s REAL NOT NULL,
  text TEXT NOT NULL,
  UNIQUE(video_id, start_s)
);
CREATE VIRTUAL TABLE segments_fts USING fts5(text, content='segments', content_rowid='id');
CREATE TRIGGER seg_ai AFTER INSERT ON segments BEGIN
  INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER seg_ad AFTER DELETE ON segments BEGIN
  INSERT INTO segments_fts(segments_fts, rowid, text) VALUES ('delete', old.id, old.text); END;
CREATE TRIGGER seg_au AFTER UPDATE ON segments BEGIN
  INSERT INTO segments_fts(segments_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text); END;

CREATE TABLE skips(video_id TEXT NOT NULL, start_s REAL NOT NULL,
                   end_s REAL NOT NULL, category TEXT, PRIMARY KEY(video_id, start_s));
CREATE TABLE chapters(video_id TEXT NOT NULL, start_s REAL NOT NULL,
                      title TEXT, PRIMARY KEY(video_id, start_s));

CREATE TABLE summaries(
  video_id TEXT NOT NULL REFERENCES videos(id),
  lang TEXT NOT NULL, headline TEXT, bullets_json TEXT, tags_json TEXT,
  keywords_json TEXT,
  short_en TEXT, model TEXT, created_at TEXT NOT NULL,
  UNIQUE(video_id, lang)
);
CREATE TABLE digests(week TEXT PRIMARY KEY, generated_at TEXT NOT NULL,
                     html TEXT, markdown TEXT, model TEXT, video_count INTEGER);

CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE search_docs(
  id INTEGER PRIMARY KEY,
  video_id TEXT NOT NULL UNIQUE,
  title TEXT, channel TEXT, headline TEXT, bullets TEXT, short_en TEXT
);
CREATE VIRTUAL TABLE search_docs_fts USING fts5(
  title, channel, headline, bullets, short_en,
  content='search_docs', content_rowid='id');
CREATE TRIGGER sd_ai AFTER INSERT ON search_docs BEGIN
  INSERT INTO search_docs_fts(rowid, title, channel, headline, bullets, short_en)
  VALUES (new.id, new.title, new.channel, new.headline, new.bullets, new.short_en); END;
CREATE TRIGGER sd_ad AFTER DELETE ON search_docs BEGIN
  INSERT INTO search_docs_fts(search_docs_fts, rowid, title, channel, headline, bullets, short_en)
  VALUES ('delete', old.id, old.title, old.channel, old.headline, old.bullets, old.short_en); END;
CREATE TRIGGER sd_au AFTER UPDATE ON search_docs BEGIN
  INSERT INTO search_docs_fts(search_docs_fts, rowid, title, channel, headline, bullets, short_en)
  VALUES ('delete', old.id, old.title, old.channel, old.headline, old.bullets, old.short_en);
  INSERT INTO search_docs_fts(rowid, title, channel, headline, bullets, short_en)
  VALUES (new.id, new.title, new.channel, new.headline, new.bullets, new.short_en); END;
"""


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str, same_thread: bool = True) -> sqlite3.Connection:
    """`same_thread=False` is for the worker, which runs its ticks in a thread
    pool. Ticks are awaited one at a time, so the connection never has two
    concurrent users; it only changes threads between ticks."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # ponytail: REPLACE-conflict deletes must fire the FTS delete triggers
    conn.execute("PRAGMA recursive_triggers=ON")
    return conn


MIGRATIONS = {
    2: ["ALTER TABLE summaries ADD COLUMN keywords_json TEXT"],
    3: ["CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)"],
}


def init(conn: sqlite3.Connection) -> None:
    found = conn.execute("PRAGMA user_version").fetchone()[0]
    if found == 0:
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        return
    if found < SCHEMA_VERSION:
        for version in range(found + 1, SCHEMA_VERSION + 1):
            for sql in MIGRATIONS.get(version - 1, MIGRATIONS.get(version, [])):
                conn.execute(sql)
            conn.execute(f"PRAGMA user_version={version}")
        return
    if found != SCHEMA_VERSION:
        raise SystemExit(
            f"database schema is v{found}, this build needs v{SCHEMA_VERSION}. "
            "Refusing to start: the file is the only copy of the archive.")


def upsert_video(conn, rec) -> None:
    """Insert or update metadata. Never touches state: a re-import must not
    resurrect an archived or no_caption row."""
    ts = now_iso()
    conn.execute(
        """INSERT INTO videos(id, title, channel, first_seen, last_seen)
           VALUES (?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             title=COALESCE(excluded.title, videos.title),
             channel=COALESCE(excluded.channel, videos.channel),
             last_seen=excluded.last_seen""",
        (rec["video_id"], rec.get("title"), rec.get("channel"), ts, ts))


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def add_watch(conn, video_id: str, watched_at: str, dwell_s, source: str) -> str:
    """Merge a watch into an existing one within 90 minutes. Returns
    'merged' or 'new'."""
    at = _parse(watched_at)
    lo = (at - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    hi = (at + timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute(
        "SELECT watched_at, dwell_s, sources FROM watches "
        "WHERE video_id=? AND watched_at BETWEEN ? AND ? "
        "ORDER BY watched_at LIMIT 1", (video_id, lo, hi)).fetchone()
    if row is None:
        conn.execute("INSERT INTO watches(video_id, watched_at, dwell_s, sources) VALUES (?,?,?,?)",
                     (video_id, watched_at, dwell_s, source))
        return "new"
    earlier = min(row["watched_at"], watched_at)
    dwells = [d for d in (row["dwell_s"], dwell_s) if d is not None]
    best = max(dwells) if dwells else None
    srcs = row["sources"].split(",") + [source]
    conn.execute("UPDATE watches SET watched_at=?, dwell_s=?, sources=? "
                 "WHERE video_id=? AND watched_at=?",
                 (earlier, best, ",".join(dict.fromkeys(srcs)), video_id, row["watched_at"]))
    return "merged"


def queue_counts(conn) -> dict:
    rows = conn.execute("SELECT state, COUNT(*) n FROM videos GROUP BY state").fetchall()
    counts = {r["state"]: r["n"] for r in rows}
    for s in STATE:
        counts.setdefault(s, 0)
    counts["total"] = sum(v for k, v in counts.items() if k in STATE)
    return counts


# ---------------------------------------------------------------- phase 2 ---

CLAIM_SQL = """
UPDATE videos SET state='running'
WHERE id = (SELECT id FROM videos
            -- 'fetched' is a resting state, not a transient one: a row parked
            -- there by a crash, or by /admin/resummarize, has segments and no
            -- summary, and nothing claimed it before this clause existed.
            WHERE state IN ('pending','failed','fetched')
              AND COALESCE(next_attempt_at,0) <= :now
            ORDER BY COALESCE(next_attempt_at,0),
                     (SELECT COUNT(*) FROM watches w WHERE w.video_id = videos.id) DESC,
                     (SELECT COALESCE(MAX(w.dwell_s),0) FROM watches w
                       WHERE w.video_id = videos.id) DESC,
                     first_seen LIMIT 1)
RETURNING *;
"""


def resume_stuck(conn) -> int:
    """One worker, so any 'running' row at startup was interrupted by a stop.
    Without this the row is invisible forever."""
    return conn.execute("UPDATE videos SET state='pending' WHERE state='running'").rowcount


def claim_next(conn, now: int | None = None):
    """One statement, one row. A restart cannot strand a failed row: the due
    time is in the table, not in memory."""
    now = now if now is not None else int(datetime.now(UTC).timestamp())
    return conn.execute(CLAIM_SQL, {"now": now}).fetchone()


def save_fetch(conn, video_id: str, meta: dict, segments, skips, chapters) -> None:
    conn.execute("""UPDATE videos SET title=COALESCE(?,title), channel=COALESCE(?,channel),
                    channel_id=COALESCE(?,channel_id), duration_s=?, published_at=?,
                    language=COALESCE(?,language), \"desc\"=COALESCE(?,\"desc\")
                    WHERE id=?""",
                 (meta.get("title"), meta.get("channel"), meta.get("channel_id"),
                  meta.get("duration_s"), meta.get("published_at"),
                  meta.get("language"), meta.get("desc"), video_id))
    # DELETE first: a shorter refetch must not leave a stale tail, and this is the
    # path that keeps the FTS index honest (see PRAGMA recursive_triggers).
    conn.execute("DELETE FROM segments WHERE video_id=?", (video_id,))
    conn.executemany("INSERT INTO segments(video_id,start_s,text) VALUES (?,?,?)",
                     [(video_id, s, t) for s, t in segments])
    conn.execute("DELETE FROM skips WHERE video_id=?", (video_id,))
    conn.executemany("INSERT OR REPLACE INTO skips(video_id,start_s,end_s,category) VALUES (?,?,?,?)",
                     [(video_id, a, b, c) for a, b, c in skips])
    conn.execute("DELETE FROM chapters WHERE video_id=?", (video_id,))
    conn.executemany("INSERT OR REPLACE INTO chapters(video_id,start_s,title) VALUES (?,?,?)",
                     [(video_id, a, t) for a, t in chapters])


def save_summary(conn, video_id: str, s: dict) -> None:
    conn.execute("""INSERT INTO summaries(video_id,lang,headline,bullets_json,tags_json,
                    keywords_json,short_en,model,created_at) VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(video_id,lang) DO UPDATE SET
                      headline=excluded.headline, bullets_json=excluded.bullets_json,
                      tags_json=excluded.tags_json, keywords_json=excluded.keywords_json,
                      short_en=excluded.short_en, model=excluded.model,
                      created_at=excluded.created_at""",
                 (video_id, s["lang"], s["headline"],
                  json.dumps(s["bullets"], ensure_ascii=False),
                  json.dumps(s["tags"], ensure_ascii=False),
                  json.dumps(s.get("keywords", []), ensure_ascii=False),
                  s["short_en"], s.get("model") or os.environ.get("YTK_LLM_MODEL") or "unknown",
                  now_iso()))
    conn.execute("""INSERT INTO search_docs(video_id,title,channel,headline,bullets,short_en)
                    VALUES (?,?,?,?,?,?)
                    ON CONFLICT(video_id) DO UPDATE SET
                      title=excluded.title, channel=excluded.channel,
                      headline=excluded.headline, bullets=excluded.bullets,
                      short_en=excluded.short_en""",
                 (video_id, s.get("title"), s.get("channel"), s["headline"],
                  " ".join(b[1] for b in s["bullets"]), s["short_en"]))
    conn.execute("UPDATE videos SET state='ready' WHERE id=?", (video_id,))


def fail(conn, video_id: str, stage: str, reason: str) -> int:
    attempts = conn.execute("SELECT attempts FROM videos WHERE id=?", (video_id,)).fetchone()[0] + 1
    # Cap it: uncapped, attempts=12 means 68 hours, and the only recovery is a
    # human clicking retry. An unattended service cannot depend on that.
    delay = min(60 * 2 ** attempts, 3600)
    conn.execute("""UPDATE videos SET state='failed', fail_stage=?, fail_reason=?,
                    attempts=?, next_attempt_at=? WHERE id=?""",
                 (stage, reason[:500], attempts,
                  int(datetime.now(UTC).timestamp()) + delay, video_id))
    return attempts


def finish(conn, video_id: str, state: str, reason: str | None = None) -> None:
    conn.execute("UPDATE videos SET state=?, fail_reason=? WHERE id=?",
                 (state, reason, video_id))


def search(conn, q: str, limit: int = 30) -> list:
    """Two indexes, bucketed. bm25 across indexes is not comparable, so summary
    hits come first and transcript hits after, each deduped per video."""
    term = '"' + q.replace('"', " ").strip() + '"'   # phrase; also neutralises syntax
    if not q.strip():
        return []
    rows = conn.execute("""
        SELECT v.id, v.title, v.channel, s.headline, 'summary' AS kind, 0.0 AS ts, '' AS hit
        FROM search_docs_fts f JOIN search_docs d ON d.id=f.rowid
        JOIN videos v ON v.id=d.video_id
        LEFT JOIN summaries s ON s.video_id=d.video_id
        WHERE search_docs_fts MATCH ? ORDER BY rank LIMIT ?""", (term, limit)).fetchall()
    seen = {r["id"] for r in rows}
    for r in conn.execute("""
        SELECT v.id, v.title, v.channel, se.headline, 'transcript' AS kind,
               g.start_s AS ts, g.text AS hit
        FROM segments_fts f JOIN segments g ON g.id=f.rowid
        JOIN videos v ON v.id=g.video_id
        LEFT JOIN summaries se ON se.video_id=g.video_id
        WHERE segments_fts MATCH ? ORDER BY rank LIMIT ?""", (term, limit * 3)).fetchall():
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        rows.append(r)
        if len(rows) >= limit:
            break
    return rows


# --------------------------------------------------------------- controls ---

# -1, not NULL: a retried row must sort ahead of the ~1900 pending rows, whose
# key is 0. A negative value still satisfies the claim's `<= :now` filter.
RETRY_SQL = """UPDATE videos SET state='pending', attempts=0, next_attempt_at=-1,
                fail_stage=NULL, fail_reason=NULL WHERE id=?"""


def retry(conn, video_id: str) -> int:
    """Put one row at the front of the queue. Returns 0 for an unknown id."""
    return conn.execute(RETRY_SQL, (video_id,)).rowcount


def retry_failed(conn) -> int:
    """Every failed row, to the front. Bounded by the failed set, not the queue."""
    return conn.execute(
        """UPDATE videos SET state='pending', attempts=0, next_attempt_at=-1,
                            fail_stage=NULL, fail_reason=NULL
           WHERE state='failed'""").rowcount


def blocked_count(conn, now: int | None = None) -> int:
    """The block path is the only writer of pending+future, so this is the
    crawl's 'resting after a YouTube block' signal."""
    now = now if now is not None else int(datetime.now(UTC).timestamp())
    return conn.execute(
        "SELECT COUNT(*) FROM videos WHERE state='pending' AND next_attempt_at > ?",
        (now,)).fetchone()[0]


def last_failure(conn):
    return conn.execute(
        "SELECT MAX(last_seen) FROM videos WHERE state='failed'").fetchone()[0]


# --------------------------------------------------------------- settings ---

DEFAULTS = {"min_duration_s": "120", "max_duration_s": "0"}


def get_setting(conn, key: str):
    """Returns a str, falling back to DEFAULTS, then to ''."""
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else DEFAULTS.get(key, "")


def set_setting(conn, key: str, value) -> None:
    conn.execute("""INSERT INTO settings(key,value) VALUES(?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                 (key, str(value)))
