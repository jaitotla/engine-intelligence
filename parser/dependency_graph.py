"""
Builds a file-level dependency graph from parsed AST reports.

The hard part isn't parsing imports (ast_parser.py does that) — it's
RESOLVING them. "import foo.bar" needs to map to an actual file path
in the repo, handling packages, relative imports, and __init__.py
files. This module does that resolution and produces a graph.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from ast_parser import FileReport, parse_repo


@dataclass
class DependencyGraph:
    # module dotted-path -> file path, e.g. "pkg.sub.mod" -> "pkg/sub/mod.py"
    module_to_file: dict[str, str] = field(default_factory=dict)
    # file path -> set of file paths it depends on
    edges: dict[str, set[str]] = field(default_factory=dict)
    # file path -> set of file paths that depend on it (reverse edges)
    reverse_edges: dict[str, set[str]] = field(default_factory=dict)
    # file path -> raw report (loc, functions, complexity, etc.)
    reports: dict[str, FileReport] = field(default_factory=dict)

    def to_json_dict(self) -> dict:
        return {
            "nodes": [
                {
                    "id": path,
                    "loc": r.loc,
                    "num_functions": len(r.functions),
                    "avg_complexity": (
                        sum(f.complexity for f in r.functions) / len(r.functions)
                        if r.functions else 0
                    ),
                    "max_complexity": max((f.complexity for f in r.functions), default=0),
                    "parse_error": r.parse_error,
                }
                for path, r in self.reports.items()
            ],
            "edges": [
                {"source": src, "target": tgt}
                for src, targets in self.edges.items()
                for tgt in targets
            ],
        }


def _file_to_module_path(file_path: Path, repo_root: Path) -> str:
    """Convert a file path to its dotted module path relative to repo root."""
    rel = file_path.relative_to(repo_root)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].removesuffix(".py")
    return ".".join(parts)


def build_graph(repo_root: Path) -> DependencyGraph:
    repo_root = repo_root.resolve()
    reports = parse_repo(repo_root)

    graph = DependencyGraph()
    for report in reports:
        file_path = Path(report.path)
        graph.reports[report.path] = report
        module_path = _file_to_module_path(file_path, repo_root)
        graph.module_to_file[module_path] = report.path
        graph.edges.setdefault(report.path, set())
        graph.reverse_edges.setdefault(report.path, set())

    # Second pass: resolve imports now that we know every module in the repo
    for report in reports:
        file_path = Path(report.path)
        own_module = _file_to_module_path(file_path, repo_root)
        own_package_parts = own_module.split(".")
        # the package a file lives in is itself minus the last component,
        # UNLESS this file is an __init__.py, in which case it IS the package
        is_init = file_path.name == "__init__.py"
        own_package_parts = own_package_parts if is_init else own_package_parts[:-1]

        targets: list[str] = []

        # Plain "import x.y" — always absolute
        for imp in report.imports:
            targets.append(imp)

        # "from X import Y" — X may be absolute (level 0) or relative (level > 0)
        for fi in report.from_imports:
            if fi.level == 0:
                if fi.module:
                    targets.append(fi.module)
            else:
                # relative import: walk up `level` packages from this file's own package,
                # then append the stated module (if any), e.g. "from ..utils import helper"
                base_parts = own_package_parts[: len(own_package_parts) - (fi.level - 1)] \
                    if fi.level > 1 else own_package_parts
                base = ".".join(base_parts)
                if fi.module:
                    targets.append(f"{base}.{fi.module}" if base else fi.module)
                else:
                    # "from . import x, y" — each imported name might itself be a submodule
                    for name in fi.names:
                        targets.append(f"{base}.{name}" if base else name)
                    if base:
                        targets.append(base)  # the package itself, e.g. re-exports via __init__

        for target_module in targets:
            candidate = target_module
            resolved_file = None
            while candidate:
                if candidate in graph.module_to_file:
                    resolved_file = graph.module_to_file[candidate]
                    break
                if "." not in candidate:
                    break
                candidate = candidate.rsplit(".", 1)[0]

            if resolved_file and resolved_file != report.path:
                graph.edges[report.path].add(resolved_file)
                graph.reverse_edges[resolved_file].add(report.path)
            # else: external dependency (e.g. "numpy") — not tracked in the graph

    return graph


def print_summary(graph: DependencyGraph):
    print(f"\n{len(graph.reports)} files, {sum(len(v) for v in graph.edges.values())} internal dependency edges\n")

    # Most depended-upon files (highest fan-in) — these are your "load-bearing" files
    fan_in = sorted(graph.reverse_edges.items(), key=lambda kv: -len(kv[1]))[:5]
    print("Most depended-upon files (high fan-in):")
    for path, deps in fan_in:
        if deps:
            print(f"  {path}  <- depended on by {len(deps)} files")

    # Files with most outgoing dependencies (highest fan-out) — potential god-modules
    fan_out = sorted(graph.edges.items(), key=lambda kv: -len(kv[1]))[:5]
    print("\nFiles with most dependencies (high fan-out):")
    for path, deps in fan_out:
        if deps:
            print(f"  {path}  -> depends on {len(deps)} files")


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    graph = build_graph(target)
    print_summary(graph)

    out_path = Path("dependency_graph.json")
    out_path.write_text(json.dumps(graph.to_json_dict(), indent=2))
    print(f"\nFull graph written to {out_path}")
