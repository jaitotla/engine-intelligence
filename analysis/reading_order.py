"""
Onboarding reading order: recommends what order a new engineer should
read through the codebase.

Core idea: a topological sort of the dependency graph guarantees you
never recommend reading a file before something it depends on — so by
the time you reach any file, everything it needs has already been
introduced. Within each "ready" tier (files whose prerequisites are
all already read), we break ties using hotspot score ascending, so
simple/stable files come before complex/risky ones when there's no
structural reason to prefer one over the other.

Real codebases can have circular imports, which break pure topological
sort (no valid "first" file exists in a cycle). We handle this
explicitly: anything left over after the main sort is part of a cycle,
and gets appended at the end, sorted by hotspot score, with that
noted so it's not silently miscategorized as "safe."
"""

from dataclasses import dataclass, field
from pathlib import Path

_NON_DOMAIN_DIRS = {"tests", "test", "spec", "specs", "docs", "doc", "examples", "scripts", "(root)"}
_GENERIC_CONTAINERS = {"src", "lib", "app", "source"}


def _is_non_domain(rel_path: str) -> bool:
    """Same signal used for boundary-violation detection in score.py: is this
    file part of the core codebase, or peripheral (tests/examples/docs)?
    Reused here so reading order prioritizes real code over test fixtures
    and example apps that happen to also have zero dependencies."""
    parts = rel_path.split("/")
    idx = 0
    while idx < len(parts) - 1 and parts[idx] in _GENERIC_CONTAINERS:
        idx += 1
    remaining = parts[idx:]
    if not remaining:
        return False
    return remaining[0] in _NON_DOMAIN_DIRS


@dataclass
class ReadingOrderEntry:
    path: str
    position: int
    reason: str
    hotspot_score: float
    in_cycle: bool = False


def compute_reading_order(
    edges: dict[str, set[str]],
    reverse_edges: dict[str, set[str]],
    hotspot_scores: dict[str, float],
) -> list[ReadingOrderEntry]:
    """
    edges[f] = set of files f depends on.
    reverse_edges[f] = set of files that depend on f.
    hotspot_scores[f] = complexity x churn (0 if not a hotspot).
    """
    all_files = set(edges.keys())
    remaining_deps = {f: len(edges.get(f, set())) for f in all_files}
    fan_in = {f: len(reverse_edges.get(f, set())) for f in all_files}

    order: list[ReadingOrderEntry] = []
    processed: set[str] = set()
    position = 1

    # Kahn's algorithm, processed in tiers so we can rank each tier by
    # hotspot score (safest first) rather than arbitrary insertion order.
    ready = {f for f in all_files if remaining_deps[f] == 0}

    while ready:
        # within this tier: core files before peripheral (tests/examples/docs),
        # then most-depended-upon first, then lowest hotspot score (safest first)
        tier = sorted(
            ready,
            key=lambda f: (_is_non_domain(f), -fan_in[f], hotspot_scores.get(f, 0)),
        )

        for f in tier:
            score = hotspot_scores.get(f, 0)
            peripheral = _is_non_domain(f)
            if peripheral:
                reason = "Test, example, or docs file — supplementary, not core logic."
            elif len(edges.get(f, set())) == 0:
                reason = "Foundational — has no internal dependencies of its own."
            elif fan_in[f] >= 3:
                reason = f"High-impact — {fan_in[f]} other files depend on this one."
            elif score > 0:
                reason = f"Hotspot (score {score:.0f}) — read once its dependencies are familiar."
            else:
                reason = "Depends only on files already introduced above."

            order.append(ReadingOrderEntry(
                path=f, position=position, reason=reason,
                hotspot_score=score, in_cycle=False,
            ))
            position += 1
            processed.add(f)

        next_ready = set()
        for f in tier:
            for dependent in reverse_edges.get(f, set()):
                if dependent in processed:
                    continue
                remaining_deps[dependent] -= 1
                if remaining_deps[dependent] == 0:
                    next_ready.add(dependent)
        ready = next_ready

    # Anything left over is part of a circular dependency — no valid
    # topological position exists (no single "safe first" file in a cycle).
    # Still apply the same core-vs-peripheral and fan-in signals as a
    # secondary ranking, so a mature codebase with pervasive circular
    # imports (common — e.g. deferred imports, TYPE_CHECKING blocks)
    # doesn't collapse into an undifferentiated dump of its most
    # important files.
    leftover = sorted(
        all_files - processed,
        key=lambda f: (_is_non_domain(f), -fan_in[f], hotspot_scores.get(f, 0)),
    )
    for f in leftover:
        reason = "Part of a circular dependency cluster"
        if fan_in[f] >= 3:
            reason += f" — but {fan_in[f]} files depend on it, so it's worth prioritizing within this group."
        else:
            reason += " — no single safe starting point within this group."
        order.append(ReadingOrderEntry(
            path=f, position=position, reason=reason,
            hotspot_score=hotspot_scores.get(f, 0), in_cycle=True,
        ))
        position += 1

    return order


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "parser"))
    from dependency_graph import build_graph
    from score import score_repo, _to_repo_relative

    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    graph = build_graph(target)
    debt = score_repo(target)
    hotspot_by_path = {fs.path: fs.hotspot_score for fs in debt.file_scores}

    # convert graph's absolute-path edges to repo-relative, matching hotspot_by_path
    rel_edges = {}
    rel_reverse = {}
    for abs_src, targets in graph.edges.items():
        src_rel = _to_repo_relative(abs_src, target.resolve())
        rel_edges[src_rel] = {_to_repo_relative(t, target.resolve()) for t in targets}
    for abs_f, deps in graph.reverse_edges.items():
        f_rel = _to_repo_relative(abs_f, target.resolve())
        rel_reverse[f_rel] = {_to_repo_relative(d, target.resolve()) for d in deps}

    order = compute_reading_order(rel_edges, rel_reverse, hotspot_by_path)
    print(f"\nRecommended reading order ({len(order)} files):\n")
    for entry in order[:20]:
        cycle_flag = " [CYCLE]" if entry.in_cycle else ""
        print(f"  {entry.position:3d}. {entry.path}{cycle_flag}")
        print(f"       {entry.reason}")
