"""
Core AST parser for Python source files.

Extracts, per file:
- imports (what this file depends on)
- top-level functions and classes (with basic complexity signals)
- lines of code

This is the foundation for the dependency graph: once every file reports
"what it imports," we can resolve those imports to other files in the
same repo and build a graph.
"""

import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FunctionInfo:
    name: str
    lineno: int
    end_lineno: int
    complexity: int  # cyclomatic complexity (branch count + 1)

    @property
    def length(self) -> int:
        return (self.end_lineno or self.lineno) - self.lineno + 1


@dataclass
class FromImport:
    module: str | None   # None for "from . import x" (pure relative, no module name)
    level: int            # 0 = absolute, 1 = ".", 2 = "..", etc.
    names: list[str] = field(default_factory=list)  # imported names, needed when module is None


@dataclass
class FileReport:
    path: str
    imports: list[str] = field(default_factory=list)          # "import x" / "import x.y"
    from_imports: list[FromImport] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    loc: int = 0
    parse_error: str | None = None


class ComplexityVisitor(ast.NodeVisitor):
    """Counts branching constructs to approximate cyclomatic complexity."""

    BRANCH_NODES = (
        ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try,
        ast.ExceptHandler, ast.With, ast.AsyncWith,
        ast.BoolOp,  # and/or short-circuits count as branches
    )

    def __init__(self):
        self.count = 1  # base complexity

    def generic_visit(self, node):
        if isinstance(node, self.BRANCH_NODES):
            self.count += 1
        super().generic_visit(node)


def _complexity_of(node: ast.AST) -> int:
    visitor = ComplexityVisitor()
    visitor.visit(node)
    return visitor.count


def parse_file(path: Path) -> FileReport:
    report = FileReport(path=str(path))
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        report.parse_error = f"read error: {e}"
        return report

    report.loc = len(source.splitlines())

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        report.parse_error = f"syntax error: {e}"
        return report

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                report.imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            report.from_imports.append(
                FromImport(
                    module=node.module,
                    level=node.level,
                    names=[alias.name for alias in node.names],
                )
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # only count top-level-ish defs to avoid double counting nested helpers heavily
            report.functions.append(
                FunctionInfo(
                    name=node.name,
                    lineno=node.lineno,
                    end_lineno=getattr(node, "end_lineno", node.lineno),
                    complexity=_complexity_of(node),
                )
            )
        elif isinstance(node, ast.ClassDef):
            report.classes.append(node.name)

    return report


def parse_repo(repo_root: Path, ignore_dirs: set[str] | None = None) -> list[FileReport]:
    ignore_dirs = ignore_dirs or {
        ".git", "venv", ".venv", "__pycache__", "node_modules",
        "build", "dist", ".mypy_cache", ".pytest_cache",
    }
    reports = []
    for path in repo_root.rglob("*.py"):
        if any(part in ignore_dirs for part in path.parts):
            continue
        reports.append(parse_file(path))
    return reports


if __name__ == "__main__":
    import sys
    import json

    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    reports = parse_repo(target)
    print(f"Parsed {len(reports)} files under {target}")
    errors = [r for r in reports if r.parse_error]
    if errors:
        print(f"  {len(errors)} files had parse errors")
    total_funcs = sum(len(r.functions) for r in reports)
    total_loc = sum(r.loc for r in reports)
    print(f"  {total_funcs} functions found, {total_loc} total lines")
