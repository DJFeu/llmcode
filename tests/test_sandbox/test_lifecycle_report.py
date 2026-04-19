"""G3: SandboxLifecycleManager.report() diagnostic dict."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from llm_code.sandbox.lifecycle import SandboxLifecycleManager


class TestReportShape:
    def test_empty_manager(self) -> None:
        mgr = SandboxLifecycleManager()
        report = mgr.report()
        assert report["registered"] == 0
        assert report["closed"] == 0
        assert report["open"] == 0
        assert report["backends"] == []

    def test_registered_counts_and_names(self) -> None:
        mgr = SandboxLifecycleManager()
        b1 = MagicMock(spec=["name", "execute", "close"])
        b1.name = "pty"
        b2 = MagicMock(spec=["name", "execute", "close"])
        b2.name = "docker"
        mgr.register(b1)
        mgr.register(b2)
        report = mgr.report()
        assert report["registered"] == 2
        assert report["open"] == 2
        assert report["closed"] == 0
        names = [entry["name"] for entry in report["backends"]]
        assert sorted(names) == ["docker", "pty"]

    def test_closed_after_close_all(self) -> None:
        mgr = SandboxLifecycleManager()
        b = MagicMock(spec=["name", "execute", "close"])
        b.name = "bwrap"
        mgr.register(b)
        mgr.close_all()
        report = mgr.report()
        assert report["registered"] == 1
        assert report["closed"] == 1
        assert report["open"] == 0
        assert report["backends"][0]["closed"] is True

    def test_unnamed_backend_labeled(self) -> None:
        mgr = SandboxLifecycleManager()
        plain = object()  # no name attribute
        mgr.register(plain)
        report = mgr.report()
        # Falls back to a type label rather than failing.
        assert report["backends"][0]["name"] == "object"

    def test_report_is_json_serializable(self) -> None:
        mgr = SandboxLifecycleManager()
        b = MagicMock(spec=["name", "execute", "close"])
        b.name = "seatbelt"
        mgr.register(b)
        mgr.close_all()
        # Must roundtrip through JSON — diagnostics / logs ingest this.
        payload = json.dumps(mgr.report())
        again = json.loads(payload)
        assert again["registered"] == 1
        assert again["backends"][0]["name"] == "seatbelt"

    def test_report_backend_class_included(self) -> None:
        """Knowing the Python class (``DockerSandboxBackend`` etc.)
        helps triage when multiple backends of the same name coexist."""
        mgr = SandboxLifecycleManager()

        class MyAdapter:
            name = "custom"
            def execute(self, cmd, policy): ...
            def close(self): ...

        mgr.register(MyAdapter())
        report = mgr.report()
        assert report["backends"][0]["class"] == "MyAdapter"
