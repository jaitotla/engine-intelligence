"""
JavaScript/TypeScript parser using tree-sitter.

Mirrors ast_parser.py's FileReport shape so the rest of the pipeline
(dependency_graph, git_history, score) can treat JS/TS files the same
way as Python files, with one key difference: JS import resolution is
filesystem-path-based (./foo, ../bar) rather than dotted-module-based,
so resolution happens separately in dependency_graph.py.
"""

import warnings
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter_languages import get_parser

warnings.filterwarnings("ignore", category=FutureWarning, module="tree_sitter")

_PARSERS = {}


def _parser_for(path: Path):
    suffix = path.suffix
    lang = {
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
    }.get(suffix)
    if lang is None:
        return None
    if lang not in _PARSERS:
        _PARSERS[lang] = get_parser(lang)
    return _PARSERS[lang]


@dataclass
class JsFunctionInfo:
    name: str
    complexity: int


@dataclass
class JsFileReport:
    path: str
    imports: list[str] = field(default_factory=list)  # raw specifiers: "./utils", "react", etc.
    functions: list[JsFunctionInfo] = field(default_factory=list)
    loc: int = 0
    parse_error: str | None = None


_BRANCH_NODE_TYPES = {
    "if_statement", "for_statement", "for_in_statement", "while_statement",
    "do_statement", "catch_clause", "switch_case", "ternary_expression",
    "conditional_expression",
}
_LOGICAL_OP_TYPE = "binary_expression"  # && / || nested inside these


def _count_complexity(node) -> int:
    count = 1

    def walk(n):
        nonlocal count
        if n.type in _BRANCH_NODE_TYPES:
            count += 1
        if n.type == _LOGICAL_OP_TYPE:
            op_child = n.child_by_field_name("operator")
            if op_child and op_child.text in (b"&&", b"||"):
                count += 1
        for child in n.children:
            walk(child)

    walk(node)
    return count


_FUNCTION_NODE_TYPES = {
    "function_declaration", "function_expression", "arrow_function", "method_definition",
}
_IMPORT_STRING_PARENTS = {"import_statement", "export_statement", "call_expression"}


def parse_file(path: Path) -> JsFileReport:
    report = JsFileReport(path=str(path))
    parser = _parser_for(path)
    if parser is None:
        report.parse_error = f"unsupported extension: {path.suffix}"
        return report

    try:
        source = path.read_bytes()
    except Exception as e:
        report.parse_error = f"read error: {e}"
        return report

    report.loc = source.count(b"\n") + 1

    try:
        tree = parser.parse(source)
    except Exception as e:
        report.parse_error = f"parse error: {e}"
        return report

    root = tree.root_node

    def walk(node, in_require_call=False):
        # import ... from "spec"; export ... from "spec";
        if node.type in ("import_statement", "export_statement"):
            for child in node.children:
                if child.type == "string":
                    spec = child.text.decode("utf-8", errors="replace").strip("'\"")
                    report.imports.append(spec)

        # require("spec")
        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn and fn.text == b"require":
                args = node.child_by_field_name("arguments")
                if args:
                    for arg in args.children:
                        if arg.type == "string":
                            spec = arg.text.decode("utf-8", errors="replace").strip("'\"")
                            report.imports.append(spec)

        if node.type in _FUNCTION_NODE_TYPES:
            name_node = node.child_by_field_name("name")
            name = name_node.text.decode("utf-8") if name_node else "<anonymous>"
            report.functions.append(JsFunctionInfo(name=name, complexity=_count_complexity(node)))
            # don't recurse into nested functions for complexity counting purposes,
            # but DO still walk them to find imports inside (rare, but possible)
            for child in node.children:
                walk(child)
            return

        for child in node.children:
            walk(child)

    walk(root)
    return report


def parse_repo(repo_root: Path, ignore_dirs: set[str] | None = None) -> list[JsFileReport]:
    ignore_dirs = ignore_dirs or {
        "node_modules", ".git", "dist", "build", ".next", "coverage", "__pycache__",
    }
    extensions = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
    reports = []
    for path in repo_root.rglob("*"):
        if path.suffix not in extensions or not path.is_file():
            continue
        if any(part in ignore_dirs for part in path.parts):
            continue
        reports.append(parse_file(path))
    return reports


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    reports = parse_repo(target)
    print(f"Parsed {len(reports)} JS/TS files under {target}")
    errors = [r for r in reports if r.parse_error]
    if errors:
        print(f"  {len(errors)} files had errors")
    total_funcs = sum(len(r.functions) for r in reports)
    total_imports = sum(len(r.imports) for r in reports)
    print(f"  {total_funcs} functions, {total_imports} import statements found")
