"""Research must outlive the turn, the process, and repetition.

Only messages were durable, so every chat turn rebuilt its context from visible
text alone and re-ran research it had already done. These tests cover the store
that fixes that: it must dedupe, stay bounded, survive a reopen, and never leak
across sessions.
"""
import pytest

from server.state import SQLiteStore


@pytest.fixture()
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "state.sqlite3"))


def _session(store, owner="tenant-a"):
    created = store.create_session(owner, "New benchmark")
    return created if isinstance(created, str) else created["id"]


def test_findings_round_trip(store):
    sid = _session(store)
    store.add_findings(sid, [
        {"title": "Azure AI Search", "url": "https://learn.microsoft.com/azure/search/"},
        {"title": "Bedrock", "url": "https://docs.aws.amazon.com/bedrock/"},
    ])

    saved = store.list_findings(sid)
    assert [item["title"] for item in saved] == ["Azure AI Search", "Bedrock"]


def test_recording_the_same_url_twice_is_a_no_op(store):
    """Turns overlap; the digest must not fill with duplicates of one page."""
    sid = _session(store)
    url = "https://learn.microsoft.com/azure/search/"
    store.add_findings(sid, [{"title": "Azure AI Search", "url": url}])
    store.add_findings(sid, [{"title": "Azure AI Search (again)", "url": url}])

    assert len(store.list_findings(sid)) == 1


def test_findings_are_bounded_so_a_long_session_cannot_grow_forever(store):
    sid = _session(store)
    store.max_findings = 5
    store.add_findings(sid, [{"title": f"t{i}", "url": f"https://example.com/{i}"} for i in range(20)])

    saved = store.list_findings(sid)
    assert len(saved) == 5
    # The newest survive: the most recent research is the most relevant.
    assert saved[-1]["url"] == "https://example.com/19"


def test_findings_do_not_leak_between_sessions(store):
    first = _session(store)
    second = _session(store)
    store.add_findings(first, [{"title": "A", "url": "https://a.example.com/"}])

    assert store.list_findings(second) == []


def test_findings_survive_a_reopen(tmp_path):
    """The point is durability: a restart must not lose the research."""
    path = str(tmp_path / "state.sqlite3")
    store = SQLiteStore(path)
    sid = _session(store)
    store.add_findings(sid, [{"title": "Azure", "url": "https://learn.microsoft.com/azure/search/"}])

    reopened = SQLiteStore(path)
    assert [item["url"] for item in reopened.list_findings(sid)] == [
        "https://learn.microsoft.com/azure/search/"
    ]


def test_a_finding_without_a_url_is_ignored(store):
    sid = _session(store)
    store.add_findings(sid, [{"title": "no link"}, {"url": "  "}, None])

    assert store.list_findings(sid) == []
