"""
Git history mining: churn + co-change coupling.
"""

import subprocess
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path


@dataclass
class ChurnReport:
    file_churn: Counter = field(default_factory=Counter)
    co_change: Counter = field(default_factory=Counter)
    first_seen: dict = field(default_factory=dict)
    last_seen: dict = field(default_factory=dict)
    total_commits_analyzed: int = 0


def _run_git(repo_root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root)] + args,
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def mine_history(
    repo_root: Path,
    max_commits: int = 2000,
    file_suffixes: tuple[str, ...] = (".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"),
    exclude_dirs: set[str] | None = None,
) -> ChurnReport:
    exclude_dirs = exclude_dirs or set()
    report = ChurnReport()

    log_output = _run_git(
        repo_root,
        [
            "log",
            f"-{max_commits}",
            "--no-merges",
            "--pretty=format:__COMMIT__%H|%aI",
            "--name-only",
        ],
    )

    commits: list[tuple[str, str, list[str]]] = []
    current_hash, current_date, current_files = None, None, []

    for line in log_output.splitlines():
        if line.startswith("__COMMIT__"):
            if current_hash is not None:
                commits.append((current_hash, current_date, current_files))
            _, meta = line.split("__COMMIT__", 1)
            current_hash, current_date = meta.split("|", 1)
            current_files = []
        elif line.strip():
            if line.endswith(file_suffixes):
                if not any(part in exclude_dirs for part in line.split("/")):
                    current_files.append(line.strip())
    if current_hash is not None:
        commits.append((current_hash, current_date, current_files))

    report.total_commits_analyzed = len(commits)

    for commit_hash, date, files in commits:
        files = sorted(set(files))
        for f in files:
            report.file_churn[f] += 1
            if f not in report.first_seen or date < report.first_seen[f]:
                report.first_seen[f] = date
            if f not in report.last_seen or date > report.last_seen[f]:
                report.last_seen[f] = date

        if 1 < len(files) <= 30:
            for a, b in combinations(files, 2):
                report.co_change[(a, b)] += 1

    return report


def top_hotspot_candidates(report: ChurnReport, n: int = 10) -> list[tuple[str, int]]:
    return report.file_churn.most_common(n)


def top_coupled_pairs(report: ChurnReport, n: int = 10) -> list[tuple[tuple[str, str], int]]:
    return report.co_change.most_common(n)


def print_summary(report: ChurnReport):
    print(f"\nAnalyzed {report.total_commits_analyzed} commits\n")
    print("Highest-churn files (changed most often):")
    for f, count in top_hotspot_candidates(report):
        print(f"  {count:4d} commits  {f}")
    print("\nMost tightly coupled file pairs (change together most often):")
    for (a, b), count in top_coupled_pairs(report):
        print(f"  {count:4d} co-commits  {a}  <->  {b}")


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    report = mine_history(target)
    print_summary(report)