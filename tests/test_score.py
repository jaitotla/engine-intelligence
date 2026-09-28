"""
Covers the three score.py bugs found while testing against real repos:
1. Naive "top-level directory = module" broke on the common src/ layout
   convention (every file's top-level dir was just "src").
2. The test-pairing filter only caught exact "test_foo.py <-> foo.py"
   name matches, missing broader cases like "app.py <-> test_basic.py".
3. Once fixed, "examples/" and other non-domain dirs still fragmented
   into their own noisy sub-boundaries instead of collapsing together.
"""

import score as score_module
from dependency_graph import build_graph
from score import _top_level_module, _is_test_pairing, _same_directory, score_repo
from conftest import write_and_commit


def test_src_layout_finds_real_subpackage_boundary():
    """flask/app.py and flask/json/__init__.py must NOT collapse to the
    same 'src' boundary — the real fix distinguishes package-root files
    ("(core)") from files inside a named sub-package."""
    assert _top_level_module("src/flask/app.py") == "(core)"
    assert _top_level_module("src/flask/json/__init__.py") == "json"
    assert _top_level_module("src/flask/sansio/app.py") == "sansio"


def test_non_domain_dirs_collapse_to_one_boundary():
    """Every file under examples/ should report the SAME boundary,
    not fragment into per-example-app sub-boundaries (the second bug
    found after the first src-layout fix)."""
    assert _top_level_module("examples/flaskr/app.py") == "examples"
    assert _top_level_module("examples/tutorial/db.py") == "examples"


def test_test_pairing_excludes_entire_test_directory():
    """The broadened filter: ANY file under tests/ paired with ANY other
    file counts as expected noise, not just exact name matches."""
    assert _is_test_pairing("src/app.py", "tests/test_basic.py") is True
    assert _is_test_pairing("requests/utils.py", "tests/test_utils.py") is True


def test_same_directory_pairs_are_not_flagged():
    assert _same_directory("pkg/a.py", "pkg/b.py") is True
    assert _same_directory("pkg/a.py", "other/b.py") is False


def test_hotspot_score_is_complexity_times_churn(git_repo):
    """Locks in the deliberate design choice to NOT fold coupling into
    the same score (see project notes) — hotspot score is purely
    complexity x churn, so a file with churn=0 never becomes a hotspot
    no matter how complex, and a real churn produces a real score."""
    write_and_commit(git_repo, {
        "risky.py": "def f(a, b, c):\n    if a:\n        if b:\n            if c:\n                return 1\n    return 0\n",
    })
    write_and_commit(git_repo, {"risky.py": "def f(a, b, c):\n    if a:\n        if b:\n            if c:\n                return 2\n    return 0\n"}, "second edit")
    report = score_repo(git_repo)
    risky = next(fs for fs in report.file_scores if fs.path == "risky.py")
    assert risky.churn == 2
    assert risky.hotspot_score == risky.complexity * risky.churn
    assert risky.hotspot_score > 0


def test_score_repo_skips_rebuild_when_graph_is_passed_in(git_repo, monkeypatch):
    """Regression test for the double-parse performance bug found while
    validating against Django (~2,900 files): the API used to call
    build_graph() once itself, then score_repo() called it AGAIN
    internally, silently doubling the most expensive part of the
    pipeline. score_repo must reuse a graph it's handed instead of
    rebuilding it."""
    write_and_commit(git_repo, {"a.py": "import b\n", "b.py": "x = 1\n"})

    call_count = {"n": 0}
    real_build_graph = score_module.build_graph

    def counting_build_graph(*args, **kwargs):
        call_count["n"] += 1
        return real_build_graph(*args, **kwargs)

    monkeypatch.setattr(score_module, "build_graph", counting_build_graph)

    # Caller builds the graph itself, exactly like the API does, then
    # passes it in — build_graph should NOT be invoked again inside score_repo.
    graph = counting_build_graph(git_repo)
    assert call_count["n"] == 1

    score_repo(git_repo, graph=graph)
    assert call_count["n"] == 1, "score_repo must reuse the passed-in graph, not rebuild it"


def test_score_repo_still_builds_graph_when_not_provided(git_repo):
    """The default (no graph passed) path must keep working — most callers,
    including every existing test in this file, rely on it."""
    write_and_commit(git_repo, {"a.py": "x = 1\n"})
    report = score_repo(git_repo)  # no graph= argument
    assert any(fs.path == "a.py" for fs in report.file_scores)