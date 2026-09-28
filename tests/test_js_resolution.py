"""
Covers JS/TS import resolution: relative paths, extension guessing,
index-file resolution, and external-package detection — the second
language's resolution logic, tested the same rigorous way as Python's.
"""

from pathlib import Path

from conftest import write_and_commit
from dependency_graph import build_graph


def test_resolves_extensionless_relative_import(git_repo):
    write_and_commit(git_repo, {
        "a.ts": "import { thing } from './b';\n",
        "b.ts": "export const thing = 1;\n",
    })
    graph = build_graph(git_repo)
    a_path = str((git_repo / "a.ts").resolve())
    b_path = str((git_repo / "b.ts").resolve())
    assert b_path in graph.edges[a_path]


def test_resolves_directory_index_import(git_repo):
    write_and_commit(git_repo, {
        "a.ts": "import { thing } from './lib';\n",
        "lib/index.ts": "export const thing = 1;\n",
    })
    graph = build_graph(git_repo)
    a_path = str((git_repo / "a.ts").resolve())
    index_path = str((git_repo / "lib/index.ts").resolve())
    assert index_path in graph.edges[a_path]


def test_resolves_require_call(git_repo):
    write_and_commit(git_repo, {
        "a.js": "const b = require('./b');\n",
        "b.js": "module.exports = {};\n",
    })
    graph = build_graph(git_repo)
    a_path = str((git_repo / "a.js").resolve())
    b_path = str((git_repo / "b.js").resolve())
    assert b_path in graph.edges[a_path]


def test_bare_specifier_not_tracked_as_edge(git_repo):
    write_and_commit(git_repo, {"a.ts": "import React from 'react';\n"})
    graph = build_graph(git_repo)
    a_path = str((git_repo / "a.ts").resolve())
    assert graph.edges[a_path] == set(), "bare package specifiers are external, not local edges"


def test_parent_relative_import(git_repo):
    write_and_commit(git_repo, {
        "sub/a.ts": "import { helper } from '../utils';\n",
        "utils.ts": "export function helper() {}\n",
    })
    graph = build_graph(git_repo)
    a_path = str((git_repo / "sub/a.ts").resolve())
    utils_path = str((git_repo / "utils.ts").resolve())
    assert utils_path in graph.edges[a_path]


def test_class_methods_grouped_correctly(git_repo):
    write_and_commit(git_repo, {
        "a.ts": (
            "class Foo {\n"
            "  methodOne() {}\n"
            "  methodTwo() {}\n"
            "}\n"
            "function topLevel() {}\n"
        ),
    })
    graph = build_graph(git_repo)
    a_path = str((git_repo / "a.ts").resolve())
    report = graph.reports[a_path]

    by_name = {f.name: f.class_name for f in report.functions}
    assert by_name["methodOne"] == "Foo"
    assert by_name["methodTwo"] == "Foo"
    assert by_name["topLevel"] is None
