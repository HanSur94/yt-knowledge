"""Markdown export. One implementation, two callers: the CLI script and
POST /export/md."""
import json
import os
import re

FOLD = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
        "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}


def slug(text: str, limit: int = 60) -> str:
    for a, b in FOLD.items():
        text = text.replace(a, b)
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:limit].strip("-") or "untitled"


def stamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def render(row, segs, chaps, bullets, tags, keywords, watches) -> str:
    vid = row["id"]
    url = f"https://www.youtube.com/watch?v={vid}"
    fm = [
        "---",
        f'title: "{row["title"].replace(chr(34), chr(39)) if row["title"] else vid}"',
        f'channel: "{row["channel"] or ""}"',
        f"url: {url}",
        f"video_id: {vid}",
        f'published: {row["published_at"] or ""}',
        f"duration_s: {row['duration_s'] or 0}",
        f"tags: [{', '.join(tags)}]",
        f"keywords: [{', '.join(keywords)}]",
        f'watched_last: {watches[0]["watched_at"] if watches else ""}',
        f"watched_count: {len(watches)}",
        "summary: " + (json.dumps(row["headline"]) if row["headline"] else "null"),
        "---",
        "",
    ]
    meta_line = (f"[{url}]({url}) · {row['channel'] or 'unknown'} · "
                 f"{(row['duration_s'] or 0) // 60} min · watched {len(watches)}×")
    body = [f"# {row['title'] or vid}", "", meta_line, ""]
    if row["headline"]:
        body += ["## Summary", "", row["headline"], ""]
    if keywords:
        body += ["**Keywords:** " + " · ".join(f"`{k}`" for k in keywords), ""]
    if bullets:
        body += ["## Key points", ""]
        for ts, text in bullets:
            body.append(f"- [{stamp(ts)}]({url}?t={int(ts)}) {text}")
        body.append("")
    if chaps:
        body += ["## Chapters", ""]
        body += [f"- [{stamp(c[0])}]({url}?t={int(c[0])}) {c[1]}" for c in chaps]
        body.append("")
    if segs:
        body += ["## Transcript", ""]
        body += [f"**[{stamp(s)}]** {t}" for s, t in segs]
        body.append("")
    return "\n".join(fm + body)


def write_tree(conn, out: str) -> int:
    rows = conn.execute("""
        SELECT v.*, s.headline, s.bullets_json, s.tags_json, s.keywords_json
        FROM videos v LEFT JOIN summaries s ON s.video_id = v.id
        WHERE v.state IN ('ready','fetched') AND v.title IS NOT NULL
        ORDER BY v.channel, v.published_at DESC""").fetchall()

    written = 0
    index = ["# YouTube archive", "", f"{len(rows)} videos", "",
             "| date | title | channel | watched |", "|---|---|---|---|"]
    for row in rows:
        vid = row["id"]
        segs = conn.execute("SELECT start_s, text FROM segments WHERE video_id=? "
                            "ORDER BY start_s", (vid,)).fetchall()
        chaps = conn.execute("SELECT start_s, title FROM chapters WHERE video_id=? "
                             "ORDER BY start_s", (vid,)).fetchall()
        watches = conn.execute("SELECT watched_at FROM watches WHERE video_id=? "
                               "ORDER BY watched_at DESC", (vid,)).fetchall()
        bullets = json.loads(row["bullets_json"]) if row["bullets_json"] else []
        tags = json.loads(row["tags_json"]) if row["tags_json"] else []
        keywords = json.loads(row["keywords_json"]) if row["keywords_json"] else []
        folder = os.path.join(out, slug(row["channel"] or "unknown"))
        os.makedirs(folder, exist_ok=True)
        name = f'{row["published_at"] or "0000"}-{slug(row["title"] or vid)}.md'
        target = os.path.join(folder, name)
        if os.path.exists(target):
            # Only disambiguate when a *different* video claimed the name, so a
            # re-run overwrites itself instead of growing -abcdef copies.
            with open(target) as fh:
                head = fh.read(200)
            if f"video_id: {vid}" not in head:
                name = name[:-3] + f"-{vid[:6]}.md"
                target = os.path.join(folder, name)
        with open(target, "w") as fh:
            fh.write(render(row, segs, chaps, bullets, tags, keywords, watches))
        written += 1
        index.append(f'| {row["published_at"] or "—"} | [[{name[:-3]}]] | '
                     f'{row["channel"] or "—"} | {len(watches)} |')

    with open(os.path.join(out, "registry.md"), "w") as fh:
        fh.write("\n".join(index) + "\n")
    return written
