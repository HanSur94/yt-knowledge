"""Speech-to-text fallback: when YouTube will not hand over captions, download
the audio and transcribe it here.

Measured 2026-11-25 from the home IP: the caption endpoint answered IpBlocked
for 5/5 videos while yt-dlp fetched the audio for 5/5 in 4-6 s each. The block
is on the caption endpoint, not on the video, so this path keeps the archive
moving when captions are throttled.

faster-whisper is an optional dependency (`pip install .[asr]`, included in the
Docker image). Without it, or with YTK_WHISPER=0, `enabled()` is False and the
worker behaves exactly as before.
"""
import glob
import os
import shutil
import subprocess
import tempfile
import threading

from . import fetch

MODEL = os.environ.get("YTK_WHISPER_MODEL") or "small"
THREADS = int(os.environ.get("YTK_WHISPER_THREADS") or "4")
# Where model weights are cached. Beside the database by default, so a container
# downloads them once onto the volume instead of on every rebuild.
MODELS_DIR = os.environ.get("YTK_WHISPER_DIR") or None
# Smallest audio stream first: speech recognition resamples to 16 kHz mono, so
# a 50 kbit/s opus track carries everything Whisper can use.
AUDIO_FORMAT = "249/250/worstaudio/bestaudio"
SAMPLE_RATE = 16000
CHUNK_S = int(os.environ.get("YTK_WHISPER_CHUNK_S") or "300")

_model = None
_lock = threading.Lock()


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def enabled() -> bool:
    return os.environ.get("YTK_WHISPER", "1") != "0" and available()


def download_audio(video_id: str, directory: str, timeout: int = 600) -> str:
    """yt-dlp the smallest audio stream into `directory`. Raises fetch errors."""
    try:
        out = subprocess.run(
            ["yt-dlp", "-q", "--no-warnings", "--no-playlist", "-f", AUDIO_FORMAT,
             "-o", os.path.join(directory, "%(id)s.%(ext)s"),
             "--", video_id],
            capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise fetch.Retryable(f"audio download timeout after {timeout}s") from exc
    if out.returncode != 0:
        msg = ((out.stderr or "").strip().splitlines() or ["yt-dlp failed"])[-1][:200]
        raise _classify(msg)
    files = glob.glob(os.path.join(directory, f"{glob.escape(video_id)}.*"))
    if not files:
        raise fetch.Retryable("audio download produced no file")
    return files[0]


def _classify(msg: str) -> Exception:
    """Map a yt-dlp error line to the worker's exception vocabulary."""
    low = msg.lower()
    if "429" in low or "too many requests" in low or ("not a bot" in low):
        return fetch.Blocked(f"audio: {msg}")
    if "private video" in low or "video unavailable" in low or "removed" in low:
        return fetch.Gone(msg)
    if "sign in to confirm your age" in low:
        # Age-gated audio needs cookies; without them it never succeeds, so do
        # not burn twenty attempts on it.
        return fetch.NoCaption(f"audio: {msg}")
    return fetch.Retryable(f"audio: {msg}")


def _load():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(MODEL, device="cpu", compute_type="int8",
                                  cpu_threads=THREADS, download_root=MODELS_DIR)
        return _model


def transcribe(path: str, language: str | None) -> tuple[list[tuple[float, str]], str]:
    """Audio file -> ([(start, text)], language).

    Pass the language when it is known. Left to guess from the first 30 s,
    Whisper heard a German video as English and returned an English
    translation instead of a transcript; detection over several windows is the
    fallback when metadata has no language.
    """
    model = _load()
    lang = (language or "").split("-")[0].lower() or None
    out = []
    for offset, audio in chunks(path, CHUNK_S):
        segments, info = model.transcribe(
            audio, language=lang, task="transcribe", beam_size=1, vad_filter=True,
            language_detection_segments=4, condition_on_previous_text=False)
        out += [(round(offset + float(s.start), 2), s.text.strip())
                for s in segments if s.text.strip()]
        # Detected once, then held: a chunk of music or silence must not flip
        # the rest of the video into another language.
        lang = lang or info.language
    if not out:
        raise fetch.NoCaption("no speech detected")
    return out, lang


def chunks(path: str, seconds: int):
    """Yield (offset_s, float32 mono 16 kHz array) windows of `seconds` each.

    faster-whisper's own decode_audio holds the whole file in memory several
    times over: a 51-minute video passed 1 GB on the N100 NAS and the kernel
    killed the process (exit 137) on every attempt. Streaming the decode keeps
    the peak at one window (5 min = 19 MB) whatever the video's length.
    """
    import av
    import numpy as np

    limit = seconds * SAMPLE_RATE
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono",
                                                  rate=SAMPLE_RATE)
    buf, have, offset = [], 0, 0.0

    def flush(n):
        nonlocal buf, have, offset
        joined = np.concatenate(buf)
        window, rest = joined[:n], joined[n:]
        buf, have = ([rest] if len(rest) else []), len(rest)
        start, offset = offset, offset + n / SAMPLE_RATE
        return start, window.astype(np.float32) / 32768.0

    with av.open(path, mode="r") as container:
        for frame in container.decode(audio=0):
            try:
                resampled = resampler.resample(frame)
            except av.error.InvalidDataError:
                continue                       # one corrupt frame is not the video
            for f in resampled:
                buf.append(f.to_ndarray().reshape(-1))
                have += buf[-1].size
                while have >= limit:
                    yield flush(limit)
        for f in resampler.resample(None):     # drain the resampler's tail
            buf.append(f.to_ndarray().reshape(-1))
            have += buf[-1].size
    if have:
        yield flush(have)


def transcript(video_id: str, language: str | None) -> tuple[list[tuple[float, str]], str]:
    """Download, transcribe, clean up. The temp dir never outlives the call."""
    directory = tempfile.mkdtemp(prefix=f"ytk-{video_id}-")
    try:
        return transcribe(download_audio(video_id, directory), language)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
