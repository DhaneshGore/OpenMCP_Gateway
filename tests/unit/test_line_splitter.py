from __future__ import annotations

from openmcp_gateway.upstream.stdio import LineSplitter


def test_splits_across_chunks() -> None:
    s = LineSplitter(100)
    assert s.feed(b'{"a":') == []
    assert s.feed(b'1}\n{"b":2}\n{"c"') == [(b'{"a":1}', False), (b'{"b":2}', False)]
    assert s.feed(b":3}\n") == [(b'{"c":3}', False)]
    assert s.flush() is None


def test_oversize_line_is_reported_once_and_skipped() -> None:
    s = LineSplitter(10)
    out = s.feed(b"0123456789ABCDEF")
    assert out == [(b"0123456789", True)]
    assert s.feed(b"GHIJ") == []  # still discarding
    assert s.feed(b"KL\nok\n") == [(b"ok", False)]


def test_exact_limit_is_allowed() -> None:
    s = LineSplitter(5)
    assert s.feed(b"12345\n") == [(b"12345", False)]


def test_flush_returns_unterminated_tail() -> None:
    s = LineSplitter(10)
    s.feed(b"abc")
    assert s.flush() == b"abc"
