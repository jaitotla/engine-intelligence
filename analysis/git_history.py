"""
Git history mining: churn + co-change coupling.

Two signals extracted from commit history:

1. CHURN — how many times each file has been modified. High churn +
   high complexity is the classic "hotspot" signal for technical debt:
   a file that's both complicated AND constantly changing is where
   bugs cluster and onboarding is hardest.

2. CO-CHANGE COUPLING — files that are frequently modified in the SAME
   commit. This surfaces hidden dependencies the import graph misses
   entirely — e.g. a Python file and a SQL migration that always change
   together even though neither one imports the other. High co-change
   coupling between files in supposedly unrelated modules is a strong
   architectural-drift signal.
"""

import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path


@dataclass
class ChurnReport:
    file_churn: Counter = field(default_factory=Counter)        # file -> commit count
    co_change: Counter = field(default_factory=Counter)          # (file_a, file_b) -> co-commit count
    first_seen: dict = field(default_factory=dict)               # file -> ISO date of first commit touching it
    last_seen: dict = field(default_factory=dict)                # file -> ISO date of most recent commit
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
    file_suffix: str = ".py",
) -> ChurnReport:
    """
    Walks commit history and builds churn + co-change stats.

    max_commits caps how far back we look — full history on a large,
    old repo can be tens of thousands of commits; 2000 is usually
    enough to see meaningful patterns without a multi-minute run.
    """
    report = ChurnReport()

    # --name-only gives us, per commit: the commit metadata line, then
    # one filename per line, then a blank line separating commits.
    # We use a custom format with a sentinel so we can split commits reliably.
    log_output = _run_git(
        repo_root,
        [
            "log",
            f"-{max_commits}",
            "--no-merges",              # merge commits touch huge file lists and skew churn
            "--pretty=format:__COMMIT__%H|%aI",  # sentinel, hash, ISO author date
            "--name-only",
        ],
    )

    commits: list[tuple[str, str, list[str]]] = []  # (hash, date, files)
    current_hash, current_date, current_files = None, None, []

    for line in log_output.splitlines():
        if line.startswith("__COMMIT__"):
            if current_hash is not None:
                commits.append((current_hash, current_date, current_files))
            _, meta = line.split("__COMMIT__", 1)
            current_hash, current_date = meta.split("|", 1)
            current_files = []
        elif line.strip():
            if line.endswith(file_suffix):
                current_files.append(line.strip())
    if current_hash is not None:
        commits.append((current_hash, current_date, current_files))

    report.total_commits_analyzed = len(commits)

    for commit_hash, date, files in commits:
        # dedupe within a commit (renames can list a file twice)
        files = sorted(set(files))
        for f in files:
            report.file_churn[f] += 1
            if f not in report.first_seen or date < report.first_seen[f]:
                report.first_seen[f] = date
            if f not in report.last_seen or date > report.last_seen[f]:
                report.last_seen[f] = date

        # co-change: every pair of files touched in this same commit.
        # Cap per-commit file count to avoid combinatorial blowup on huge
        # "restructure everything" commits (e.g. a repo-wide reformat).
        if 1 < len(files) <= 30:
            for a, b in combinations(files, 2):
                report.co_change[(a, b)] += 1

    return report


def top_hotspot_candidates(report: ChurnReport, n: int = 10) -> list[tuple[str, int]]:
    """Files touched most often — the raw churn signal, before combining with complexity."""
    return report.file_churn.most_common(n)


def top_coupled_pairs(report: ChurnReport, n: int = 10) -> list[tuple[tuple[str, str], int]]:
    """File pairs that change together most often — hidden coupling signal."""
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
