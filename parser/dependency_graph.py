"""
Builds a file-level dependency graph from parsed AST reports (Python + JS/TS).
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from ast_parser import FileReport, parse_repo
from js_parser import parse_repo as parse_js_repo
from js_resolver import resolve_js_import


@dataclass
class DependencyGraph:
    module_to_file: dict[str, str] = field(default_factory=dict)
    edges: dict[str, set[str]] = field(default_factory=dict)
    reverse_edges: dict[str, set[str]] = field(default_factory=dict)
    reports: dict[str, object] = field(default_factory=dict)

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
    rel = file_path.relative_to(repo_root)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].removesuffix(".py")
    return ".".join(parts)


def build_graph(repo_root: Path, exclude_dirs: set[str] | None = None) -> DependencyGraph:
    repo_root = repo_root.resolve()
    exclude_dirs = exclude_dirs or set()

    py_ignore = {
        ".git", "venv", ".venv", "__pycache__", "node_modules",
        "build", "dist", ".mypy_cache", ".pytest_cache",
    } | exclude_dirs
    js_ignore = {
        "node_modules", ".git", "dist", "build", ".next", "coverage", "__pycache__",
    } | exclude_dirs

    reports = parse_repo(repo_root, ignore_dirs=py_ignore)

    graph = DependencyGraph()
    for report in reports:
        file_path = Path(report.path)
        graph.reports[report.path] = report
        module_path = _file_to_module_path(file_path, repo_root)
        graph.module_to_file[module_path] = report.path
        graph.edges.setdefault(report.path, set())
        graph.reverse_edges.setdefault(report.path, set())

    for report in reports:
        file_path = Path(report.path)
        own_module = _file_to_module_path(file_path, repo_root)
        own_package_parts = own_module.split(".")
        is_init = file_path.name == "__init__.py"
        own_package_parts = own_package_parts if is_init else own_package_parts[:-1]

        targets: list[str] = []

        for imp in report.imports:
            targets.append(imp)

        for fi in report.from_imports:
            if fi.level == 0:
                if fi.module:
                    targets.append(fi.module)
            else:
                base_parts = own_package_parts[: len(own_package_parts) - (fi.level - 1)] \
                    if fi.level > 1 else own_package_parts
                base = ".".join(base_parts)
                if fi.module:
                    targets.append(f"{base}.{fi.module}" if base else fi.module)
                else:
                    for name in fi.names:
                        targets.append(f"{base}.{name}" if base else name)
                    if base:
                        targets.append(base)

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

    # --- JS/TS files ---
    js_reports = parse_js_repo(repo_root, ignore_dirs=js_ignore)
    for js_report in js_reports:
        if js_report.parse_error:
            continue
        graph.reports[js_report.path] = js_report
        graph.edges.setdefault(js_report.path, set())
        graph.reverse_edges.setdefault(js_report.path, set())

    for js_report in js_reports:
        if js_report.parse_error:
            continue
        file_path = Path(js_report.path)
        for spec in js_report.imports:
            resolved = resolve_js_import(spec, file_path, repo_root)
            if resolved is None:
                continue
            resolved_str = str(resolved)
            if resolved_str not in graph.reports:
                continue
            if resolved_str != js_report.path:
                graph.edges[js_report.path].add(resolved_str)
                graph.reverse_edges[resolved_str].add(js_report.path)

    return graph


def print_summary(graph: DependencyGraph):
    print(f"\n{len(graph.reports)} files, {sum(len(v) for v in graph.edges.values())} internal dependency edges\n")
    fan_in = sorted(graph.reverse_edges.items(), key=lambda kv: -len(kv[1]))[:5]
    print("Most depended-upon files (high fan-in):")
    for path, deps in fan_in:
        if deps:
            print(f"  {path}  <- depended on by {len(deps)} files")
    fan_out = sorted(graph.edges.items(), key=lambda kv: -len(kv[1]))[:5]
    print("\nFiles with most dependencies (high fan-out):")
    for path, deps in fan_out:
        if deps:
            print(f"  {path}  -> depends on {len(deps)} files")


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    graph = build_graph(target)

    if len(graph.reports) == 0:
        print(f"No supported files (.py, .js, .jsx, .ts, .tsx) found under {target}")
    else:
        print_summary(graph)
        out_path = Path("dependency_graph.json")
        out_path.write_text(json.dumps(graph.to_json_dict(), indent=2))
        print(f"\nFull graph written to {out_path}")