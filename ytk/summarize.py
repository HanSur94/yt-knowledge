"""LLM adapter. Any OpenAI-compatible endpoint; no SDK, no vendor."""
import json
import os
import time
import urllib.error
import urllib.request

MAX_CHARS = 48_000          # ~12k tokens of transcript text
SYSTEM = ("You summarise a video transcript for a personal archive. Return JSON "
          "only, no prose, no code fence.")


def _in_sponsor(ts: float, skips) -> bool:
    return any(a <= ts <= b for a, b, _c in skips)


def clip(segments, max_chars: int = MAX_CHARS):
    """Opening and closing thirds, with a marker where the middle was cut.
    ponytail: middle dropped, not sampled - a better rule needs a tokenizer."""
    text = [(s, t) for s, t in segments]
    total = sum(len(t) for _, t in text)
    if total <= max_chars:
        return text, False
    third = max_chars // 3
    head, tail, used = [], [], 0
    for s, t in text:
        if used + len(t) > third:
            break
        head.append((s, t))
        used += len(t)
    used = 0
    for s, t in reversed(text):
        if used + len(t) > third:
            break
        tail.append((s, t))
        used += len(t)
    return head + [(head[-1][0], "[... middle of the transcript omitted ...]")] + list(reversed(tail)), True


def build_prompt(video, segments, chapters, skips, lang_hint=None):
    body, cut = clip([(s, t) for s, t in segments if not _in_sponsor(s, skips)])
    lines = [f"[{int(s // 60):02d}:{int(s % 60):02d}] {t}" for s, t in body]
    chaps = "\n".join(f"[{int(c // 60)}:{int(c % 60):02d}] {t}" for c, t in chapters) or "none"
    user = f"""TITLE: {video.get('title')}
CHANNEL: {video.get('channel')}
DURATION: {video.get('duration_s')} seconds
CHAPTERS:
{chaps}

TRANSCRIPT{' (middle omitted)' if cut else ''}:
{chr(10).join(lines)}

Return JSON with exactly these keys:
  "headline": one sentence, the video's central claim, in the transcript's language
  "bullets": array of 5-9 objects {{"t": <int seconds>, "s": "what it claims"}}
  "tags": array of 3-6 short lowercase topical tags
  "keywords": array of 4-10 key terms a person would search for, exact phrases
              from the video (names, products, numbers, tools)
  "lang": ISO 639-1 code of the transcript
  "short_en": one English sentence, same claim"""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def parse(reply: str) -> dict:
    text = reply.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.lower().startswith("json") else text
    d = json.loads(text)
    bullets = [(int(b["t"]), str(b["s"]).strip()) for b in d["bullets"]]
    if not d.get("headline") or not bullets:
        raise ValueError("summary missing headline or bullets")
    return {"headline": d["headline"].strip(),
            "keywords": [str(k) for k in d.get("keywords", [])][:12],
            "bullets": bullets[:12],
            "tags": [str(t).lower() for t in d.get("tags", [])][:8],
            "lang": (d.get("lang") or "en")[:5],
            "short_en": (d.get("short_en") or d["headline"]).strip()}


def call(messages, timeout=180) -> str:
    base = os.environ.get("YTK_LLM_BASE", "").rstrip("/")
    model = os.environ.get("YTK_LLM_MODEL", "")
    if not base or not model:
        raise RuntimeError("YTK_LLM_BASE and YTK_LLM_MODEL are not set")
    payload = json.dumps({"model": model, "messages": messages,
                          "temperature": 0.2, "stream": False}).encode()
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("YTK_LLM_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(f"{base}/chat/completions", data=payload,
                                 headers=headers, method="POST")
    # A blip here costs a whole digest, and the endpoint is on the other side of a
    # home network; one retry is cheaper than a re-run.
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read())
            return d["choices"][0]["message"]["content"]
        except urllib.error.HTTPError:
            raise                       # 4xx/5xx: retrying the same body will not help
        except OSError as exc:
            if attempt:
                raise
            last = exc
            time.sleep(2)
    raise last


def summarize(video, segments, chapters, skips) -> dict:
    reply = call(build_prompt(video, segments, chapters, skips))
    try:
        return parse(reply)
    except ValueError:
        return parse(call(build_prompt(video, segments, chapters, skips) +
                          [{"role": "user", "content": "Return valid JSON only."}]))
