"""
Covers the relative-import resolution bug: build_graph originally didn't
account for a file's own package location when resolving "from . import x"
or "from ..pkg import y", so it resolved 0 edges on real repos like Flask
until fixed. These tests would have caught that regression immediately.
"""

from conftest import write_and_commit
from dependency_graph import build_graph


def test_resolves_dotted_relative_import(git_repo):
    write_and_commit(git_repo, {
        "pkg/__init__.py": "",
        "pkg/a.py": "from . import b\n",
        "pkg/b.py": "x = 1\n",
    })
    graph = build_graph(git_repo)
    a_path = str((git_repo / "pkg/a.py").resolve())
    b_path = str((git_repo / "pkg/b.py").resolve())
    assert b_path in graph.edges[a_path], "a.py should resolve 'from . import b' to pkg/b.py"


def test_resolves_named_relative_import(git_repo):
    write_and_commit(git_repo, {
        "pkg/__init__.py": "",
        "pkg/a.py": "from .b import thing\n",
        "pkg/b.py": "thing = 1\n",
    })
    graph = build_graph(git_repo)
    a_path = str((git_repo / "pkg/a.py").resolve())
    b_path = str((git_repo / "pkg/b.py").resolve())
    assert b_path in graph.edges[a_path]


def test_resolves_parent_level_relative_import(git_repo):
    """The '..' case — walks up one package level from the importing file."""
    write_and_commit(git_repo, {
        "pkg/__init__.py": "",
        "pkg/sub/__init__.py": "",
        "pkg/sub/a.py": "from ..utils import helper\n",
        "pkg/utils.py": "def helper(): pass\n",
    })
    graph = build_graph(git_repo)
    a_path = str((git_repo / "pkg/sub/a.py").resolve())
    utils_path = str((git_repo / "pkg/utils.py").resolve())
    assert utils_path in graph.edges[a_path]


def test_external_import_not_tracked_as_edge(git_repo):
    write_and_commit(git_repo, {"a.py": "import numpy\nimport os\n"})
    graph = build_graph(git_repo)
    a_path = str((git_repo / "a.py").resolve())
    assert graph.edges[a_path] == set(), "external packages should never become graph edges"


def test_functions_grouped_under_their_class(git_repo):
    """Locks in the class-attribution fix: methods must report which class
    they belong to, not just appear in one flat, ungrouped list."""
    write_and_commit(git_repo, {
        "a.py": (
            "class Foo:\n"
            "    def method_one(self): pass\n"
            "    def method_two(self): pass\n"
            "\n"
            "def top_level(): pass\n"
        ),
    })
    graph = build_graph(git_repo)
    a_path = str((git_repo / "a.py").resolve())
    report = graph.reports[a_path]

    by_name = {f.name: f.class_name for f in report.functions}
    assert by_name["method_one"] == "Foo"
    assert by_name["method_two"] == "Foo"
    assert by_name["top_level"] is None
