from ytk import fetch


def test_no_cookie_file_means_anonymous(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "COOKIES", str(tmp_path / "missing.txt"))
    assert fetch._session() is None


def test_netscape_cookie_file_becomes_a_session(tmp_path, monkeypatch):
    p = tmp_path / "cookies.txt"
    p.write_text("# Netscape HTTP Cookie File\n"
                 ".youtube.com\tTRUE\t/\tTRUE\t0\tTEST\t1\n")
    monkeypatch.setattr(fetch, "COOKIES", str(p))
    s = fetch._session()
    assert s is not None
    assert any(c.name == "TEST" and c.value == "1" for c in s.cookies)


def test_a_broken_cookie_file_is_ignored(tmp_path, monkeypatch):
    p = tmp_path / "cookies.txt"
    p.write_text("not a cookie file\n")
    monkeypatch.setattr(fetch, "COOKIES", str(p))
    assert fetch._session() is None
