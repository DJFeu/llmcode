"""Tests for Session name and tags fields (Task 5: Session Naming)."""
import pytest
from pathlib import Path
from llm_code.runtime.session import Session


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
