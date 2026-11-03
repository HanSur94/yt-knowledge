import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _path(value: str) -> str:
    """Relative defaults resolve against the project root, not the shell's
    working directory: started from ~, the old default created an empty second
    database and looked like the archive had vanished."""
    p = Path(value)
    return str(p if p.is_absolute() else ROOT / p)


@dataclass(frozen=True)
class Settings:
    secret: str
    db: str
    thumbs: str
    bind: str
    sleep: float


def load() -> Settings:
    missing = [k for k in ("YTK_SECRET",) if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"missing required env: {', '.join(missing)}")
    return Settings(
        secret=os.environ["YTK_SECRET"],
        db=_path(os.environ.get("YTK_DB") or "data/ytk.sqlite"),
        thumbs=_path(os.environ.get("YTK_THUMBS") or "data/thumbs"),
        bind=os.environ.get("YTK_BIND") or "127.0.0.1:8488",
        sleep=float(os.environ.get("YTK_SLEEP") or "0.5"),
    )
