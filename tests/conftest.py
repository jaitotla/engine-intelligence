"""
Shared fixtures. Tests build small, real git repos on disk (not mocks) so
the git-history and scoring code is exercised the same way it runs against
a real repo — this is deliberately consistent with how every bug in this
project was actually found during development.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "parser"))
sys.path.insert(0, str(Path(__file__).parent.parent / "analysis"))


def _git(repo: Path, *args):
    subprocess.run(["git", "-C", str(repo)] + list(args), check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path):
    """A bare, empty git repo ready for files + commits."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def write_and_commit(repo: Path, files: dict[str, str], message: str = "commit"):
    """files: {relative_path: content}. Writes each, stages, commits."""
    for rel_path, content in files.items():
        full = repo / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
