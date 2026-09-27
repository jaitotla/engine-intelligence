"""
Phase 3: Technical debt scoring.
"""

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "parser"))
from dependency_graph import build_graph  # noqa: E402
from git_history import mine_history  # noqa: E402


@dataclass
class FileScore:
    path: str
    complexity: float
    churn: int
    hotspot_score: float
    loc: int
    num_functions: int


@dataclass
class DebtReport:
    file_scores: list[FileScore] = field(default_factory=list)
    hidden_coupling: list[tuple[str, str, int]] = field(default_factory=list)
    boundary_violations: list[tuple[str, str, str]] = field(default_factory=list)


def _to_repo_relative(abs_path: str, repo_root: Path) -> str:
    return str(Path(abs_path).resolve().relative_to(repo_root)).replace("\\", "/")


_GENERIC_CONTAINERS = {"src", "lib", "app", "source"}
_NON_DOMAIN_DIRS = {"tests", "test", "spec", "specs", "docs", "doc", "examples", "scripts", "(root)"}


def _top_level_module(rel_path: str) -> str:
    parts = rel_path.split("/")
    idx = 0
    while idx < len(parts) - 1 and parts[idx] in _GENERIC_CONTAINERS:
        idx += 1
    remaining = parts[idx:]
    if len(remaining) <= 1:
        return "(root)"
    if remaining[0] in _NON_DOMAIN_DIRS:
        return remaining[0]
    if len(remaining) == 2:
        return "(core)"
    return remaining[1]


def _is_test_pairing(a: str, b: str) -> bool:
    a_top = _top_level_module(a)
    b_top = _top_level_module(b)
    if a_top in _NON_DOMAIN_DIRS or b_top in _NON_DOMAIN_DIRS:
        return True
    a_name = Path(a).stem
    b_name = Path(b).stem
    a_core = re.sub(r"^test_|_test$", "", a_name)
    b_core = re.sub(r"^test_|_test$", "", b_name)
    return a_core == b_core and a_core != a_name


def _same_directory(a: str, b: str) -> bool:
    return str(Path(a).parent) == str(Path(b).parent)


def score_repo(repo_root: Path, max_commits: int = 2000, exclude_dirs: set[str] | None = None) -> DebtReport:
    repo_root = repo_root.resolve()
    exclude_dirs = exclude_dirs or set()
    graph = build_graph(repo_root, exclude_dirs=exclude_dirs)
    churn_report = mine_history(repo_root, max_commits=max_commits, exclude_dirs=exclude_dirs)

    report = DebtReport()

    for abs_path, file_report in graph.reports.items():
        if file_report.parse_error:
            continue
        rel_path = _to_repo_relative(abs_path, repo_root)
        complexity = max((f.complexity for f in file_report.functions), default=1)
        churn = churn_report.file_churn.get(rel_path, 0)
        report.file_scores.append(
            FileScore(
                path=rel_path,
                complexity=complexity,
                churn=churn,
                hotspot_score=complexity * churn,
                loc=file_report.loc,
                num_functions=len(file_report.functions),
            )
        )
    report.file_scores.sort(key=lambda fs: -fs.hotspot_score)

    for (a, b), count in churn_report.co_change.items():
        if _same_directory(a, b):
            continue
        if _is_test_pairing(a, b):
            continue
        report.hidden_coupling.append((a, b, count))
    report.hidden_coupling.sort(key=lambda t: -t[2])

    violation_counts: dict[tuple[str, str], list[str]] = {}

    def _record_violation(a_rel: str, b_rel: str):
        mod_a, mod_b = _top_level_module(a_rel), _top_level_module(b_rel)
        if mod_a == mod_b:
            return
        if mod_a in _NON_DOMAIN_DIRS or mod_b in _NON_DOMAIN_DIRS:
            return
        key = tuple(sorted((mod_a, mod_b)))
        violation_counts.setdefault(key, []).append(f"{a_rel} <-> {b_rel}")

    for abs_src, targets in graph.edges.items():
        src_rel = _to_repo_relative(abs_src, repo_root)
        for abs_tgt in targets:
            tgt_rel = _to_repo_relative(abs_tgt, repo_root)
            _record_violation(src_rel, tgt_rel)

    for a, b, _count in report.hidden_coupling:
        _record_violation(a, b)

    for (mod_a, mod_b), examples in sorted(violation_counts.items(), key=lambda kv: -len(kv[1])):
        report.boundary_violations.append((mod_a, mod_b, f"{len(examples)} crossings, e.g. {examples[0]}"))

    return report


def print_summary(report: DebtReport, top_n: int = 10):
    print("\n=== TOP HOTSPOTS (complexity x churn) ===")
    for fs in report.file_scores[:top_n]:
        if fs.hotspot_score > 0:
            print(f"  {fs.hotspot_score:6.0f}  {fs.path}  (complexity={fs.complexity:.0f}, churn={fs.churn})")
    print("\n=== HIDDEN COUPLING ===")
    for a, b, count in report.hidden_coupling[:top_n]:
        print(f"  {count:4d} co-commits  {a}  <->  {b}")
    print("\n=== BOUNDARY VIOLATIONS ===")
    for mod_a, mod_b, detail in report.boundary_violations[:top_n]:
        print(f"  {mod_a} <-> {mod_b}: {detail}")


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    report = score_repo(target)
    print_summary(report)

    out = {
        "hotspots": [
            {"path": fs.path, "score": fs.hotspot_score, "complexity": fs.complexity,
             "churn": fs.churn, "loc": fs.loc}
            for fs in report.file_scores if fs.hotspot_score > 0
        ],
        "hidden_coupling": [
            {"file_a": a, "file_b": b, "co_change_count": c}
            for a, b, c in report.hidden_coupling
        ],
        "boundary_violations": [
            {"module_a": a, "module_b": b, "detail": d}
            for a, b, d in report.boundary_violations
        ],
    }
    out_path = Path("debt_report.json")
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nFull report written to {out_path}")