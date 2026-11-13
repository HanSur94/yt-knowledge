from ytk import progress


def test_stage_maps_the_pipeline():
    assert progress.stage("pending", False)["label"] == "queued"
    assert progress.stage("running", False)["label"] == "transcribing"
    assert progress.stage("running", True)["label"] == "summarising"
    assert progress.stage("fetched", True)["pct"] == 85
    assert progress.stage("ready", True) == {"label": "ready", "pct": 100, "done": True}


def test_terminal_states_stop_the_poller():
    for state in ("ready", "failed", "no_caption", "archived"):
        assert progress.stage(state, False)["done"] is True
    assert progress.stage("failed", False, 7)["label"] == "failed after 7 attempts"
