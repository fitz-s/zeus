# Created: 2026-07-26
# Last reused/audited: 2026-07-26
# Lifecycle: created=2026-07-26; last_reviewed=2026-07-26; last_reused=2026-07-26
# Purpose: INV-47 / NC-24 antibody -- every fail-closed gate site named in the
#   INV-47 registry must carry an adjacent SCOPE/DRAIN/RESET declaration.
# Reuse: Adding a new gate to the INV-47 registry (AGENTS.md line ~179) means
#   adding it to GATE_SITES here with a real anchor, not editing the parser.
# Authority basis: AGENTS.md root INV-47 (commit 7125dc633);
#   architecture/invariants.yaml INV-47; architecture/negative_constraints.yaml NC-24.
"""Site-anchored SCOPE/DRAIN/RESET enforcement for INV-47 fail-closed gates.

A repo-wide grep for the string "SCOPE" is worthless -- it would match
unrelated prose and never notice a gate site being renamed or deleted. This
module instead anchors each gate to an exact, unique source string (its
literal condition or defining signature) and only then checks a bounded
window of surrounding text for the three markers. If an anchor is missing or
duplicated, the site lookup itself raises loudly -- it never silently
degrades to "no assertion ran".
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_MARKERS = ("# SCOPE:", "# DRAIN:", "# RESET:")


@dataclass(frozen=True)
class GateSite:
    name: str
    relpath: str
    anchor: str
    lines_before: int
    lines_after: int


GATE_SITES: tuple[GateSite, ...] = (
    GateSite(
        name="entry_readiness_main",
        relpath="src/main.py",
        anchor='def _edli_live_entry_readiness_block(',
        lines_before=0,
        lines_after=45,
    ),
    GateSite(
        name="day0_oracle_guard",
        relpath="src/data/day0_oracle_anomaly.py",
        anchor="def is_day0_family_paused(",
        lines_before=0,
        lines_after=25,
    ),
    GateSite(
        name="posterior_identity_check",
        relpath="src/engine/event_reactor_adapter.py",
        anchor='_fail(f"model_identity_drift:{model}")',
        lines_before=25,
        lines_after=0,
    ),
    GateSite(
        name="request_governor_circuit",
        relpath="src/data/polymarket_request_governor.py",
        anchor='raise RequestAdmissionDenied(f"POLYMARKET_ENDPOINT_EMBARGOED:{endpoint}:{circuit_until.isoformat()}")',
        lines_before=32,
        lines_after=0,
    ),
)


def _read_lines(relpath: str) -> list[str]:
    path = ROOT / relpath
    assert path.is_file(), f"gate site file missing: {relpath}"
    return path.read_text().splitlines()


def _anchor_window(site: GateSite) -> str:
    """Locate ``site.anchor`` uniquely in its file and return the surrounding window.

    Fails loudly (AssertionError, not a silent skip/pass) if the anchor is
    absent or ambiguous -- a renamed or duplicated gate site must break this
    test, never quietly stop being checked.
    """
    lines = _read_lines(site.relpath)
    hits = [i for i, line in enumerate(lines) if site.anchor in line]
    assert hits, (
        f"INV-47 gate site {site.name!r} anchor not found in {site.relpath} -- "
        f"the gate was renamed, moved, or deleted. Update GATE_SITES in "
        f"tests/test_gate_scope_drain_reset.py (and re-declare SCOPE/DRAIN/RESET "
        f"at the new location) rather than removing this check."
    )
    assert len(hits) == 1, (
        f"INV-47 gate site {site.name!r} anchor is ambiguous in {site.relpath} "
        f"({len(hits)} occurrences) -- narrow the anchor string so it is unique."
    )
    idx = hits[0]
    start = max(0, idx - site.lines_before)
    end = min(len(lines), idx + site.lines_after + 1)
    return "\n".join(lines[start:end])


def _assert_declares_scope_drain_reset(site: GateSite) -> None:
    window = _anchor_window(site)
    missing = [marker for marker in _MARKERS if marker not in window]
    assert not missing, (
        f"INV-47 gate site {site.name!r} ({site.relpath}) is missing "
        f"{missing} adjacent to its condition -- every fail-closed gate must "
        f"declare SCOPE, DRAIN, and RESET in code adjacent to its condition "
        f"or it is presumed defective (INV-47)."
    )


def test_entry_readiness_gate_declares_scope_drain_reset():
    _assert_declares_scope_drain_reset(GATE_SITES[0])


def test_day0_oracle_guard_declares_scope_drain_reset():
    _assert_declares_scope_drain_reset(GATE_SITES[1])


def test_posterior_identity_check_declares_scope_drain_reset():
    _assert_declares_scope_drain_reset(GATE_SITES[2])


def test_request_governor_circuit_declares_scope_drain_reset():
    _assert_declares_scope_drain_reset(GATE_SITES[3])


def test_unknown_gate_site_fails_loudly_not_silently():
    """A renamed/removed anchor must raise, never silently pass with 0 hits."""
    ghost = GateSite(
        name="ghost_site_for_negative_test",
        relpath=GATE_SITES[0].relpath,
        anchor="this_anchor_string_will_never_exist_in_source_2026_07_26",
        lines_before=0,
        lines_after=10,
    )
    with pytest.raises(AssertionError, match="anchor not found"):
        _anchor_window(ghost)

    dup = GateSite(
        name="dup_site_for_negative_test",
        relpath=GATE_SITES[0].relpath,
        # `def ` appears many times in src/main.py -- deliberately ambiguous.
        anchor="def ",
        lines_before=0,
        lines_after=10,
    )
    with pytest.raises(AssertionError, match="ambiguous"):
        _anchor_window(dup)


def test_all_registered_gate_sites_are_distinct():
    names = [site.name for site in GATE_SITES]
    assert len(names) == len(set(names)), "duplicate GateSite name in GATE_SITES"
