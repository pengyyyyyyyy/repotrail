from pathlib import Path
import subprocess

import pytest

from repotrail.index import build_index
from repotrail.repository import acquire


def test_public_clone_preserves_name_and_reuses_complete_cache(tmp_path, monkeypatch):
    calls = []

    def clone(command, **kwargs):
        calls.append(command)
        checkout = Path(command[-1])
        (checkout / "main.py").write_text("def main():\n    return 1\n")

    monkeypatch.setattr(subprocess, "run", clone)
    source = "https://github.com/example/project.git"
    first = acquire(source, tmp_path)
    second = acquire(source, tmp_path)
    assert first == second
    assert first.name == "project"
    assert len(calls) == 1
    assert "credential.helper=" in calls[0]


@pytest.mark.parametrize("source", ["https://example.com/project", "http://github.com/o/r",
                                        "ssh://github.com/o/r", "https://github.com/o/../r"])
def test_remote_input_is_scoped_to_public_github_https(tmp_path, source):
    with pytest.raises(ValueError):
        acquire(source, tmp_path)


def test_failed_clone_does_not_publish_partial_checkout(tmp_path, monkeypatch):
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(ValueError, match="无法克隆"):
        acquire("https://github.com/example/project", tmp_path)
    assert not list(tmp_path.glob("repositories/*/project"))


def test_subdirectory_does_not_bind_tour_to_parent_git_root(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    sample = tmp_path / "sample"
    sample.mkdir()
    (sample / "main.py").write_text("def main():\n    return 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=RepoTrail test",
                    "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"],
                   check=True, capture_output=True)
    assert build_index(sample).revision is None
    root_index = build_index(tmp_path)
    assert root_index.revision is not None
    assert not root_index.dirty
    (sample / "main.py").write_text("def main():\n    return 2\n")
    assert build_index(tmp_path).dirty

