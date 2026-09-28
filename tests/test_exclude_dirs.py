"""
Covers exclude_dirs — verified manually earlier against requests/packages/
(vendored urllib3), where it correctly dropped hidden-coupling noise from
477 pairs to 0. These tests lock in that behavior at both layers it
touches: the dependency graph (current file tree) and git history
(commit-log filtering, which is where the vendored-code noise actually
came from).
"""

from conftest import write_and_commit
from dependency_graph import build_graph
from git_history import mine_history
from score import score_repo


def test_exclude_dirs_removes_files_from_graph(git_repo):
    write_and_commit(git_repo, {
        "core.py": "import vendor.lib\n",
        "vendor/lib.py": "x = 1\n",
    })
    graph = build_graph(git_repo, exclude_dirs={"vendor"})
    paths = [p for p in graph.reports.keys()]
    assert not any("vendor" in p for p in paths), "excluded directory's files must not appear in the graph at all"


def test_exclude_dirs_removes_files_from_git_history(git_repo):
    write_and_commit(git_repo, {"core.py": "x = 1\n", "vendor/lib.py": "y = 1\n"})
    write_and_commit(git_repo, {"core.py": "x = 2\n", "vendor/lib.py": "y = 2\n"}, "second edit")

    report_unfiltered = mine_history(git_repo)
    report_filtered = mine_history(git_repo, exclude_dirs={"vendor"})

    assert "vendor/lib.py" in report_unfiltered.file_churn
    assert "vendor/lib.py" not in report_filtered.file_churn


def test_exclude_dirs_removes_coupling_noise_end_to_end(git_repo):
    """The exact scenario this feature was built for: two vendored files
    in different subdirectories that always change together shouldn't
    show up as 'hidden coupling' once their parent directory is excluded.
    (Files must be in different subdirectories — same-directory pairs are
    already filtered by score.py's own _same_directory check, which would
    otherwise mask what this test is actually verifying.)"""
    write_and_commit(git_repo, {
        "core.py": "x = 1\n",
        "vendor/sub1/a.py": "a = 1\n",
        "vendor/sub2/b.py": "b = 1\n",
    })
    write_and_commit(git_repo, {
        "vendor/sub1/a.py": "a = 2\n",
        "vendor/sub2/b.py": "b = 2\n",
    }, "vendored files change together")

    unfiltered = score_repo(git_repo)
    filtered = score_repo(git_repo, exclude_dirs={"vendor"})

    assert any("vendor" in a or "vendor" in b for a, b, _ in unfiltered.hidden_coupling)
    assert not any("vendor" in a or "vendor" in b for a, b, _ in filtered.hidden_coupling)
