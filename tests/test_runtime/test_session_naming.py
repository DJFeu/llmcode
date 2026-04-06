"""Tests for Session name and tags fields (Task 5: Session Naming)."""
import pytest
from pathlib import Path
from llm_code.runtime.session import Session, SessionManager


class TestSessionNaming:
    def test_default_name_empty(self):
        s = Session.create(Path("/tmp"))
        assert s.name == ""
        assert s.tags == ()

    def test_rename(self):
        s = Session.create(Path("/tmp"))
        s2 = s.rename("my-session")
        assert s2.name == "my-session"
        assert s.name == ""  # immutable

    def test_add_tags(self):
        s = Session.create(Path("/tmp"))
        s2 = s.add_tags("auth", "refactor")
        assert s2.tags == ("auth", "refactor")
        assert s.tags == ()  # immutable

    def test_add_tags_dedup(self):
        s = Session.create(Path("/tmp")).add_tags("a", "b")
        s2 = s.add_tags("b", "c")
        assert s2.tags == ("a", "b", "c")

    def test_serialize_with_name_tags(self):
        s = Session.create(Path("/tmp")).rename("test").add_tags("t1")
        d = s.to_dict()
        assert d["name"] == "test"
        assert d["tags"] == ["t1"]

    def test_deserialize_with_name_tags(self):
        s = Session.create(Path("/tmp")).rename("test").add_tags("t1")
        d = s.to_dict()
        s2 = Session.from_dict(d)
        assert s2.name == "test"
        assert s2.tags == ("t1",)

    def test_deserialize_legacy_no_name(self):
        """Old session JSON without name/tags fields should still load."""
        s = Session.create(Path("/tmp"))
        d = s.to_dict()
        # Simulate old format by removing new fields
        d.pop("name", None)
        d.pop("tags", None)
        s2 = Session.from_dict(d)
        assert s2.name == ""
        assert s2.tags == ()



class TestSessionManagerExtensions:
    def test_rename(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s = Session.create(Path("/tmp"))
        mgr.save(s)
        renamed = mgr.rename(s.id, "my-session")
        assert renamed.name == "my-session"
        loaded = mgr.load(s.id)
        assert loaded.name == "my-session"

    def test_delete(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s = Session.create(Path("/tmp"))
        mgr.save(s)
        assert mgr.delete(s.id) is True
        with pytest.raises(FileNotFoundError):
            mgr.load(s.id)

    def test_delete_nonexistent(self, tmp_path):
        mgr = SessionManager(tmp_path)
        assert mgr.delete("nonexistent") is False

    def test_search_by_name(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s1 = Session.create(Path("/tmp")).rename("auth-refactor")
        s2 = Session.create(Path("/tmp")).rename("perf-tuning")
        mgr.save(s1)
        mgr.save(s2)
        results = mgr.search("auth")
        assert len(results) == 1
        assert results[0].name == "auth-refactor"

    def test_search_by_path(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s1 = Session.create(Path("/work/myapp"))
        s2 = Session.create(Path("/work/other"))
        mgr.save(s1)
        mgr.save(s2)
        results = mgr.search("myapp")
        assert len(results) == 1

    def test_search_by_tag(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s1 = Session.create(Path("/tmp")).add_tags("urgent")
        s2 = Session.create(Path("/tmp")).add_tags("low-priority")
        mgr.save(s1)
        mgr.save(s2)
        results = mgr.search("urgent")
        assert len(results) == 1

    def test_get_by_name(self, tmp_path):
        mgr = SessionManager(tmp_path)
        s = Session.create(Path("/tmp")).rename("my-session")
        mgr.save(s)
        found = mgr.get_by_name("my-session")
        assert found is not None
        assert found.id == s.id

    def test_get_by_name_not_found(self, tmp_path):
        mgr = SessionManager(tmp_path)
        assert mgr.get_by_name("nope") is None
