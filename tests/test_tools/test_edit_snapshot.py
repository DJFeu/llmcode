"""C5: EditSnapshot + EditSnapshotStore — tool-level undo primitives."""
from __future__ import annotations

from llm_code.tools.edit_snapshot import EditSnapshot, EditSnapshotStore


class TestEditSnapshot:
    def test_frozen_fields(self) -> None:
        s = EditSnapshot(
            path="/tmp/x.py", before="old", after="new", ts=1.0,
        )
        try:
            s.path = "/other"
            raise AssertionError("EditSnapshot must be frozen")
        except Exception:
            pass

    def test_reversible_diff(self) -> None:
        """to_revert_content returns the content that undoes the edit."""
        s = EditSnapshot(path="/x", before="v1", after="v2", ts=0)
        assert s.to_revert_content() == "v1"


class TestEditSnapshotStore:
    def test_record_and_list(self) -> None:
        store = EditSnapshotStore()
        store.record(path="/a", before="a1", after="a2")
        store.record(path="/b", before="b1", after="b2")
        assert len(store.list()) == 2

    def test_recent_most_recent_first(self) -> None:
        store = EditSnapshotStore()
        store.record(path="/a", before="", after="x")
        store.record(path="/b", before="", after="y")
        store.record(path="/c", before="", after="z")
        recent = store.recent(2)
        assert [s.path for s in recent] == ["/c", "/b"]

    def test_restore_returns_snapshot_and_pops(self) -> None:
        store = EditSnapshotStore()
        store.record(path="/a", before="prev", after="curr")
        snap = store.pop_latest()
        assert snap.before == "prev"
        assert store.list() == ()

    def test_pop_empty_returns_none(self) -> None:
        assert EditSnapshotStore().pop_latest() is None

    def test_filter_by_path(self) -> None:
        store = EditSnapshotStore()
        store.record(path="/a", before="", after="x")
        store.record(path="/b", before="", after="y")
        store.record(path="/a", before="x", after="z")
        only_a = store.for_path("/a")
        assert len(only_a) == 2
        assert all(s.path == "/a" for s in only_a)

    def test_max_size_evicts_oldest(self) -> None:
        store = EditSnapshotStore(max_size=3)
        for i in range(5):
            store.record(path=f"/f{i}", before="", after=str(i))
        # Only the last 3 kept.
        kept = [s.path for s in store.list()]
        assert kept == ["/f2", "/f3", "/f4"]

    def test_clear(self) -> None:
        store = EditSnapshotStore()
        store.record(path="/a", before="", after="x")
        store.clear()
        assert store.list() == ()
