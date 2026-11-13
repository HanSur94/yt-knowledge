"""Where one video sits in the pipeline. One source for page and poller."""


def stage(state: str, has_segments: bool, attempts: int = 0) -> dict:
    """The bar is a stage display, not a measured percentage: the LLM call is a
    single request, so there is no finer progress to report."""
    if state == "ready":
        return {"label": "ready", "pct": 100, "done": True}
    if state == "running":
        if has_segments:
            return {"label": "summarising", "pct": 85, "done": False}
        return {"label": "transcribing", "pct": 45, "done": False}
    if state == "fetched":
        return {"label": "queued for summary", "pct": 85, "done": False}
    if state == "failed":
        return {"label": f"failed after {attempts} attempts", "pct": 0, "done": True}
    if state in ("no_caption", "archived"):
        return {"label": state, "pct": 0, "done": True}
    return {"label": "queued", "pct": 8, "done": False}
