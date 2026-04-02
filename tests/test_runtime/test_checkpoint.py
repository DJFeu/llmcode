import subprocess

import pytest

from llm_code.runtime.checkpoint import Checkpoint, CheckpointManager


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "initial.txt").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    return tmp_path


def test_create_checkpoint(git_repo):
    manager = CheckpointManager(git_repo)
    cp = manager.create("write_file", {"path": "foo.py", "content": "x"})

    assert isinstance(cp, Checkpoint)
    assert len(cp.git_sha) == 40
    # Verify SHA actually exists in the repo
    result = subprocess.run(
        ["git", "cat-file", "-t", cp.git_sha],
        cwd=git_repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "commit"
    assert len(manager.list_checkpoints()) == 1


def test_create_multiple(git_repo):
    manager = CheckpointManager(git_repo)
    manager.create("tool_a", {"x": 1})
    manager.create("tool_b", {"x": 2})
    manager.create("tool_c", {"x": 3})

    assert len(manager.list_checkpoints()) == 3


def test_undo(git_repo):
    manager = CheckpointManager(git_repo)

    # Record the state of initial.txt before any checkpoint
    original_content = (git_repo / "initial.txt").read_text()

    # Create checkpoint (captures current state)
    manager.create("write_file", {"path": "initial.txt"})

    # Modify the file after the checkpoint
    (git_repo / "initial.txt").write_text("modified content")

    # Undo should restore the file to what it was when checkpoint was created
    popped = manager.undo()
    assert popped is not None
    assert (git_repo / "initial.txt").read_text() == original_content


def test_undo_empty_returns_none(git_repo):
    manager = CheckpointManager(git_repo)
    result = manager.undo()
    assert result is None


def test_can_undo(git_repo):
    manager = CheckpointManager(git_repo)
    assert manager.can_undo() is False

    manager.create("some_tool", {})
    assert manager.can_undo() is True


def test_list_checkpoints(git_repo):
    manager = CheckpointManager(git_repo)
    manager.create("tool_1", {"a": 1})
    manager.create("tool_2", {"b": 2})

    checkpoints = manager.list_checkpoints()
    assert len(checkpoints) == 2
    assert checkpoints[0].tool_name == "tool_1"
    assert checkpoints[1].tool_name == "tool_2"


def test_checkpoint_id_format(git_repo):
    manager = CheckpointManager(git_repo)
    cp1 = manager.create("tool_a", {})
    cp2 = manager.create("tool_b", {})
    cp3 = manager.create("tool_c", {})

    assert cp1.id.startswith("cp-")
    assert cp2.id.startswith("cp-")
    assert cp3.id.startswith("cp-")
    # IDs should be distinct and incrementing
    assert cp1.id != cp2.id
    assert cp2.id != cp3.id


def test_undo_restores_file(git_repo):
    manager = CheckpointManager(git_repo)

    # Write a file and create a checkpoint
    (git_repo / "data.txt").write_text("important data")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add data.txt"], cwd=git_repo, capture_output=True)

    cp = manager.create("delete_file", {"path": "data.txt"})

    # Delete the file (simulating a destructive operation)
    (git_repo / "data.txt").unlink()
    subprocess.run(["git", "add", "-A"], cwd=git_repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "deleted data.txt"], cwd=git_repo, capture_output=True)

    assert not (git_repo / "data.txt").exists()

    # Create second checkpoint (after deletion)
    manager.create("some_other_tool", {})

    # Undo the last checkpoint
    manager.undo()

    # After undoing, file is still deleted (we only undid the second checkpoint)
    # Undo the first checkpoint (the one before deletion)
    manager.undo()

    # Now file should be restored
    assert (git_repo / "data.txt").exists()
    assert (git_repo / "data.txt").read_text() == "important data"
