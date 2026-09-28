"""
Covers reading_order.py's core guarantee — a file never appears before
something it depends on — plus the circular-dependency fallback found
to be a real, common case (most of Flask's actual core files were caught
in one cycle cluster during testing).
"""

from reading_order import compute_reading_order


def test_dependency_always_read_before_dependent():
    """The one guarantee this feature exists to provide: never recommend
    reading a file before something it depends on."""
    edges = {"a.py": {"b.py"}, "b.py": set()}
    reverse = {"b.py": {"a.py"}, "a.py": set()}
    order = compute_reading_order(edges, reverse, {})
    positions = {e.path: e.position for e in order}
    assert positions["b.py"] < positions["a.py"]


def test_foundational_file_ranked_first_in_its_tier():
    """Among files with zero dependencies, higher fan-in (more things
    depend on it) should rank first."""
    edges = {"popular.py": set(), "obscure.py": set(), "user.py": {"popular.py"}}
    reverse = {"popular.py": {"user.py"}, "obscure.py": set(), "user.py": set()}
    order = compute_reading_order(edges, reverse, {})
    tier1 = [e.path for e in order if e.position <= 2]
    assert tier1[0] == "popular.py", "the file 2 others could depend on should rank before an isolated file"


def test_circular_dependency_does_not_crash_and_is_flagged():
    """A -> B -> A: no valid topological position exists for either file.
    Must not infinite-loop or crash, and both should be marked in_cycle."""
    edges = {"a.py": {"b.py"}, "b.py": {"a.py"}}
    reverse = {"a.py": {"b.py"}, "b.py": {"a.py"}}
    order = compute_reading_order(edges, reverse, {})
    assert len(order) == 2
    assert all(e.in_cycle for e in order)


def test_cycle_members_still_ranked_by_fan_in():
    """Even within an unresolvable cycle, the fix applied after the first
    naive version: rank by fan-in rather than leaving it arbitrary."""
    # a <-> b (cycle), but c also depends on b, giving b higher fan-in
    edges = {"a.py": {"b.py"}, "b.py": {"a.py"}, "c.py": {"b.py"}}
    reverse = {"a.py": {"b.py"}, "b.py": {"a.py", "c.py"}, "c.py": set()}
    order = compute_reading_order(edges, reverse, {})
    cycle_entries = [e for e in order if e.in_cycle]
    # b.py has fan_in=2 (a.py and c.py depend on it) vs a.py's fan_in=1
    b_entry = next(e for e in cycle_entries if e.path == "b.py")
    a_entry = next(e for e in cycle_entries if e.path == "a.py")
    assert b_entry.position < a_entry.position


def test_every_file_appears_exactly_once():
    """No file should be silently dropped or duplicated across tiers
    and the cycle fallback."""
    edges = {"a.py": {"b.py"}, "b.py": {"c.py"}, "c.py": set(), "d.py": {"e.py"}, "e.py": {"d.py"}}
    reverse = {"a.py": set(), "b.py": {"a.py"}, "c.py": {"b.py"}, "d.py": {"e.py"}, "e.py": {"d.py"}}
    order = compute_reading_order(edges, reverse, {})
    paths = [e.path for e in order]
    assert sorted(paths) == sorted(edges.keys())
    assert len(paths) == len(set(paths))
