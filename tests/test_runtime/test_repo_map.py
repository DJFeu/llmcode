"""Tests for llm_code.runtime.repo_map -- AST-based symbol index."""
from __future__ import annotations

from pathlib import Path


from llm_code.runtime.repo_map import build_repo_map, RepoMap


class TestBuildRepoMapPython:
    def test_extracts_classes_and_functions(self, tmp_path: Path) -> None:
        code = '''
class UserService:
    def create_user(self, name: str) -> None:
        pass
    def delete_user(self, uid: int) -> bool:
        return True

def standalone_helper() -> str:
    return "hi"
'''
        (tmp_path / "service.py").write_text(code)

        repo_map = build_repo_map(tmp_path)
        assert isinstance(repo_map, RepoMap)
        assert len(repo_map.files) == 1

        fs = repo_map.files[0]
        assert fs.path == "service.py"
        assert len(fs.classes) == 1
        assert fs.classes[0].name == "UserService"
        assert "create_user" in fs.classes[0].methods
        assert "delete_user" in fs.classes[0].methods
        assert "standalone_helper" in fs.functions

    def test_multiple_classes(self, tmp_path: Path) -> None:
        code = '''
class Foo:
    def bar(self): ...

class Baz:
    def qux(self): ...
'''
        (tmp_path / "models.py").write_text(code)

        repo_map = build_repo_map(tmp_path)
        fs = repo_map.files[0]
        assert len(fs.classes) == 2
        names = {c.name for c in fs.classes}
        assert names == {"Foo", "Baz"}

    def test_empty_file(self, tmp_path: Path) -> None:
        (tmp_path / "empty.py").write_text("")

        repo_map = build_repo_map(tmp_path)
        assert len(repo_map.files) == 1
        fs = repo_map.files[0]
        assert fs.classes == ()
        assert fs.functions == ()


class TestBuildRepoMapNonPython:
    def test_js_regex_extraction(self, tmp_path: Path) -> None:
        code = '''
class MyComponent {
  constructor() {}
}

function handleClick() {}

export const API_URL = "http://example.com";
export function fetchData() {}
'''
        (tmp_path / "app.js").write_text(code)

        repo_map = build_repo_map(tmp_path)
        assert len(repo_map.files) == 1
        fs = repo_map.files[0]
        assert any(c.name == "MyComponent" for c in fs.classes)
        assert "handleClick" in fs.functions or "fetchData" in fs.functions

    def test_unsupported_file_shows_path_only(self, tmp_path: Path) -> None:
        (tmp_path / "data.json").write_text('{"key": "value"}')

        repo_map = build_repo_map(tmp_path)
        assert len(repo_map.files) == 1
        fs = repo_map.files[0]
        assert fs.path == "data.json"
        assert fs.classes == ()
        assert fs.functions == ()


class TestRepoMapCompact:
    def test_compact_format(self, tmp_path: Path) -> None:
        code = '''
class Client:
    def send(self): ...
    def recv(self): ...

def connect() -> None: ...
'''
        (tmp_path / "net.py").write_text(code)

        repo_map = build_repo_map(tmp_path)
        compact = repo_map.to_compact()
        assert "net.py:" in compact
        assert "Client" in compact
        assert "send" in compact
        assert "connect" in compact

    def test_compact_respects_token_budget(self, tmp_path: Path) -> None:
        # Create many files to exceed budget
        for i in range(50):
            (tmp_path / f"mod_{i}.py").write_text(f"class C{i}:\n    def m{i}(self): ...\n")

        repo_map = build_repo_map(tmp_path)
        compact = repo_map.to_compact(max_tokens=200)
        # Rough: 200 tokens * 4 chars = 800 chars max
        assert len(compact) <= 1200  # generous margin

    def test_skips_hidden_and_pycache(self, tmp_path: Path) -> None:
        (tmp_path / "good.py").write_text("def ok(): ...")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "good.cpython-311.pyc").write_bytes(b"\x00")
        (tmp_path / ".hidden").mkdir()
        (tmp_path / ".hidden" / "secret.py").write_text("x = 1")

        repo_map = build_repo_map(tmp_path)
        paths = [f.path for f in repo_map.files]
        assert "good.py" in paths
        assert not any("__pycache__" in p for p in paths)
        assert not any(".hidden" in p for p in paths)
