"""Tests for harness config types."""
from __future__ import annotations


def test_harness_control_defaults():
    from llm_code.harness.config import HarnessControl

    ctrl = HarnessControl(name="lsp_diagnose", category="sensor", kind="computational")
    assert ctrl.name == "lsp_diagnose"
    assert ctrl.category == "sensor"
    assert ctrl.kind == "computational"
    assert ctrl.enabled is True
    assert ctrl.trigger == "post_tool"


def test_harness_control_custom_trigger():
    from llm_code.harness.config import HarnessControl

    ctrl = HarnessControl(
        name="repo_map", category="guide", kind="computational", trigger="pre_turn"
    )
    assert ctrl.trigger == "pre_turn"


def test_harness_control_frozen():
    from llm_code.harness.config import HarnessControl

    ctrl = HarnessControl(name="x", category="guide", kind="computational")
    try:
        ctrl.name = "y"  # type: ignore[misc]
        assert False, "Should be frozen"
    except AttributeError:
        pass


def test_harness_config_defaults():
    from llm_code.harness.config import HarnessConfig

    cfg = HarnessConfig()
    assert cfg.template == "auto"
    assert cfg.controls == ()


def test_harness_config_with_overrides():
    from llm_code.harness.config import HarnessConfig, HarnessControl

    ctrl = HarnessControl(name="test_runner", category="sensor", kind="computational", enabled=True)
    cfg = HarnessConfig(template="python-web", controls=(ctrl,))
    assert cfg.template == "python-web"
    assert len(cfg.controls) == 1
    assert cfg.controls[0].name == "test_runner"


def test_harness_config_frozen():
    from llm_code.harness.config import HarnessConfig

    cfg = HarnessConfig()
    try:
        cfg.template = "node"  # type: ignore[misc]
        assert False, "Should be frozen"
    except AttributeError:
        pass


def test_harness_finding_fields():
    from llm_code.harness.config import HarnessFinding

    f = HarnessFinding(sensor="lsp_diagnose", message="type error", file_path="foo.py", severity="error")
    assert f.sensor == "lsp_diagnose"
    assert f.message == "type error"
    assert f.file_path == "foo.py"
    assert f.severity == "error"
