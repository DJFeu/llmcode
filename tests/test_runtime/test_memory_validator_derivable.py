"""Tests for derivable-content rejection in memory_validator."""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_code.runtime.memory_validator import (
    DerivableContentError,
    validate_non_derivable,
)


def test_warn_only_default_does_not_raise(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    text = "Here is code:\n```python\nx = 1\n```"
    with caplog.at_level("WARNING"):
        validate_non_derivable(text, tmp_path)
    assert any("derivable" in r.message for r in caplog.records)


def test_strict_rejects_fenced_code_blocks(tmp_path: Path) -> None:
    text = "Snippet:\n```js\nconsole.log(1);\n```"
    with pytest.raises(DerivableContentError) as exc:
        validate_non_derivable(text, tmp_path, strict=True)
    assert any("fenced" in r for r in exc.value.reasons)


def test_strict_rejects_git_sha(tmp_path: Path) -> None:
    text = "See commit deadbeefcafebabe1234567890abcdef12345678 for details."
    with pytest.raises(DerivableContentError) as exc:
        validate_non_derivable(text, tmp_path, strict=True)
    assert any("SHA" in r for r in exc.value.reasons)


def test_strict_rejects_on_disk_absolute_path(tmp_path: Path) -> None:
    target = tmp_path / "real_file.txt"
    target.write_text("hi")
    text = f"See {target} for the data."
    with pytest.raises(DerivableContentError) as exc:
        validate_non_derivable(text, tmp_path, strict=True)
    assert any("on-disk" in r for r in exc.value.reasons)


def test_strict_passes_clean_content(tmp_path: Path) -> None:
    validate_non_derivable("User prefers concise summaries.", tmp_path, strict=True)
