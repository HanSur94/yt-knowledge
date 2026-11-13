"""Week math and the UTC window: the two errors the digest shipped with."""
from datetime import datetime, timedelta

from ytk import digest


def test_week_of_is_monday_to_monday():
    start, end, label = digest.week_of(digest.date(2026, 11, 11))
    assert (start, end) == (digest.date(2026, 11, 9), digest.date(2026, 11, 16))
    assert label == "2026-W46"


def test_last_week_is_the_week_that_ended():
    now = datetime.now(digest.BERLIN).date()
    assert digest.last_week()[2] == digest.week_of(now - timedelta(days=7))[2]


def test_window_converts_berlin_midnight_to_utc():
    # CET (UTC+1) and CEST (UTC+2): the old code stamped both as midnight Z.
    assert digest.window(digest.date(2026, 11, 2), digest.date(2026, 11, 9)) == (
        "2026-11-01T23:00:00Z", "2026-11-08T23:00:00Z")
    assert digest.window(digest.date(2026, 7, 6), digest.date(2026, 7, 13)) == (
        "2026-07-05T22:00:00Z", "2026-07-12T22:00:00Z")


def test_parse_week_normalises_the_label():
    assert digest.parse_week("2025-W3")[2] == "2025-W03"
    assert digest.parse_week("2025-W31")[0] == digest.date(2025, 7, 28)
