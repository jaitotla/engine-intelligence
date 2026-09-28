"""
Core AST parser for Python source files.
"""

import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FunctionInfo:
    name: str
    lineno: int
    end_lineno: int
    complexity: int
    class_name: str | None = None  # None if a top-level function, else its enclosing class

    @property
    def length(self) -> int:
        return (self.end_lineno or self.lineno) - self.lineno + 1


@dataclass
class FromImport:
    module: str | None
    level: int
    names: list[str] = field(default_factory=list)


@dataclass
class FileReport:
    path: str
    imports: list[str] = field(default_factory=list)
    from_imports: list[FromImport] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    loc: int = 0
    parse_error: str | None = None


class ComplexityVisitor(ast.NodeVisitor):
    BRANCH_NODES = (
        ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try,
        ast.ExceptHandler, ast.With, ast.AsyncWith,
        ast.BoolOp,
    )

    def __init__(self):
        self.count = 1

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

    def _process_body(body, class_name=None):
        """Walks top-level statements only, tracking which class (if any)
        directly contains each function — so 'Contents' can group methods
        under their class instead of a flat, unordered list. Deeply nested
        functions (closures inside functions) are intentionally not listed
        as separate symbols; their complexity still folds into their
        parent's count via ComplexityVisitor's full recursive walk."""
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                report.functions.append(
                    FunctionInfo(
                        name=node.name,
                        lineno=node.lineno,
                        end_lineno=getattr(node, "end_lineno", node.lineno),
                        complexity=_complexity_of(node),
                        class_name=class_name,
                    )
                )
            elif isinstance(node, ast.ClassDef):
                report.classes.append(node.name)
                _process_body(node.body, class_name=node.name)

    _process_body(tree.body)

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

    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    reports = parse_repo(target)
    print(f"Parsed {len(reports)} files under {target}")