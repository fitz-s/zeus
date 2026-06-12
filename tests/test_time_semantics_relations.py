# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator time-semantics directive 2026-06-10

"""Auto-generated relation assertions for the time-semantics contract layer.

Every relation declared in src/contracts/time_semantics.REGISTRY becomes ONE
parametrized test here. This is the antibody (Fitz constraint #3): any agent who
later changes one time constant without honoring its declared relations breaks CI
loudly with a message naming BOTH constants and the margin — not a doc, not an
alert, a failing test.

A relation that is currently VIOLATED against live values (a discovered live
mismatch, e.g. the Gamma effective-window-vs-measured-p95 residual) is NOT silently
fixed and NOT silently skipped: it is marked xfail with the incident reference, so
CI surfaces it (xfail in the report) without blocking, exactly as the directive
requires. The set of known-violations is pinned below; if a NEW relation starts
failing it is NOT in that set and fails HARD — that is the whole point.

DO NOT change any live constant's value to make these pass. To clear an xfail,
either (a) the underlying constant is intentionally re-tuned in its own source of
truth, or (b) the relation's model is corrected here — never edit a value to
silence the contract.
"""

from __future__ import annotations

import pytest

from src.contracts import time_semantics as ts

# ---------------------------------------------------------------------------
# Known live VIOLATIONS, pinned by (entry_name, incident). Each is xfail(strict)
# so it shows in CI as an expected failure tied to its incident. A relation NOT
# in this set that fails will fail HARD (the antibody fires for new regressions).
# ---------------------------------------------------------------------------

# (Resolved 2026-06-10: the cluster-1 gamma-effective-window violation was fixed
# by widening ZEUS_REACTOR_GAMMA_DRAIN_GRACE_SECONDS 1.5 -> 2.0 in src/main.py,
# making slice 2.0 + grace 2.0 = 4.0s >= the 3.774s measured floor. The relation
# now passes and is enforced like every other.)
_KNOWN_VIOLATIONS: dict[tuple[str, str], str] = {}


def _relation_id(entry: ts.Entry, rel: ts.Relation) -> str:
    return f"{entry.name}::{rel.kind.value}::{rel.incident}"


_ALL_RELATIONS = [
    pytest.param(entry, rel, id=_relation_id(entry, rel))
    for entry in ts.all_entries()
    for rel in entry.relations
]


@pytest.mark.parametrize("entry,relation", _ALL_RELATIONS)
def test_declared_time_relation_holds(entry: ts.Entry, relation: ts.Relation) -> None:
    """Each declared registry relation must hold against current live values."""
    key = (entry.name, relation.incident)
    if key in _KNOWN_VIOLATIONS:
        pytest.xfail(_KNOWN_VIOLATIONS[key])

    check = ts.evaluate_relation(entry, relation)
    assert check.holds, check.message


def test_registry_has_relations_for_every_incident_cluster() -> None:
    """Sanity: the registry must cover at least the 7 seed incident clusters.

    Each declared relation tags its incident with a `cluster-N` prefix; assert all
    seven clusters that have a RELATIONAL invariant are represented. (Cluster 7 —
    per-city local-time / DST — is structural, covered by test_city_time_semantics.py
    and asserted there, so it is allowed to be absent from the relation set.)
    """
    clusters = set()
    for entry in ts.all_entries():
        for rel in entry.relations:
            token = rel.incident.split()[0]  # e.g. "cluster-1" or "cluster-3/6"
            for part in token.replace("cluster-", "").split("/"):
                if part.isdigit():
                    clusters.add(int(part))
    # Clusters 1-6 each carry at least one relational invariant.
    for expected in (1, 2, 3, 4, 5, 6):
        assert expected in clusters, (
            f"cluster {expected} has no declared time relation; "
            f"covered clusters = {sorted(clusters)}"
        )


def test_known_violations_are_still_actually_violated() -> None:
    """Anti-stale-xfail guard: every pinned known-violation must STILL fail.

    If a known violation starts PASSING (e.g. someone widened the gamma window),
    this test fails LOUDLY so the xfail is removed and the relation re-armed as a
    hard assertion. Without this, a fixed mismatch would silently keep its xfail and
    a future regression would be masked. (Fitz: an antibody must not rot into a
    permanently-suppressed alert.)
    """
    still_violated = {
        (c.entry_name, c.relation.incident)
        for c in ts.evaluate_all()
        if not c.holds
    }
    for key, note in _KNOWN_VIOLATIONS.items():
        assert key in still_violated, (
            f"PINNED VIOLATION NOW PASSES: {key} — remove its xfail entry and "
            f"re-arm the hard assertion. Note was: {note}"
        )


def test_no_unexpected_live_violations() -> None:
    """The ONLY currently-failing relations must be the pinned known ones.

    This is the load-bearing antibody assertion: if a NEW relation starts failing
    (a fresh live mismatch introduced by changing some constant), it is not in
    _KNOWN_VIOLATIONS and this test fails hard, naming the offending relation.
    """
    unexpected = [
        c
        for c in ts.evaluate_all()
        if not c.holds and (c.entry_name, c.relation.incident) not in _KNOWN_VIOLATIONS
    ]
    assert not unexpected, "Unexpected live time-semantics violations:\n" + "\n".join(
        c.message for c in unexpected
    )


def test_guess_basis_entries_are_enumerable() -> None:
    """The operator's audit surface: basis=GUESS entries must be listable.

    This does not fail on guesses (guesses are honest and expected) — it asserts the
    registry can ENUMERATE them, so a future audit / report can list every unmeasured
    constant. A registry with zero guesses would mean every value is anchored; today
    that is not true and the test documents the current count for visibility.
    """
    guesses = ts.guess_entries()
    names = sorted(e.name for e in guesses)
    # There ARE guesses today (most constants were invented ad-hoc — the whole
    # reason this layer exists). Assert the audit surface is non-empty and stable.
    assert names, "expected the registry to expose its guess-basis audit surface"
    # Each guess must honestly say so in its prose basis.
    for entry in guesses:
        assert "guess" in entry.basis.lower(), (
            f"{entry.name} has basis_kind=GUESS but its prose basis does not own it "
            f"as a guess: {entry.basis!r}"
        )


def test_every_entry_reads_a_live_value_without_mutation() -> None:
    """Single-source-of-truth: reading an entry's value twice is stable and side-effect-free."""
    for entry in ts.all_entries():
        v1 = entry.value()
        v2 = entry.value()
        assert v1 == v2, f"{entry.name} value is not stable across reads ({v1} != {v2})"
        assert isinstance(v1, float)
