# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md (LX-0R
#   deliverable 3) + docs/rebuild/consult_answers/local_ledger_excision_delta_round2_2026-07-13.txt
#   census §精化 #1/#2 (the named bypass-writer seed set).

"""Tests for scripts/gen_economics_writer_manifest.py.

Covers: determinism (two scans of the same tree produce byte-identical
output), the ``--check`` drift gate, and the seed-completeness expectation —
every bypass writer named in the local-ledger-excision census must appear in
a fresh scan of THIS repo's actual src/ tree (not a synthetic fixture: the
whole point of the manifest is to prove the real codebase, not a stand-in,
carries no un-catalogued forbidden-column writer).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.gen_economics_writer_manifest import (  # noqa: E402
    OUTPUT_PATH,
    ROOT,
    render_manifest,
    scan_all,
)

# The named bypass writers below the projection funnel (census §精化 #1) +
# the EDLI disease-surface writers (census §精化 #2). (file, line) here uses
# the CURRENT repo's line numbers (drift from the original census's line
# numbers is expected as the surrounding code moves; the site itself must
# still be found).
_SEED_BYPASS_WRITERS = {
    ("src/state/position_duplicate_consolidator.py", 185),
    ("src/state/position_duplicate_consolidator.py", 370),
    ("src/execution/command_recovery.py", 2460),
    ("src/execution/command_recovery.py", 6214),
    ("src/execution/command_recovery.py", 8281),
    ("src/execution/command_recovery.py", 8386),
    ("src/execution/command_recovery.py", 8617),
    ("src/execution/command_recovery.py", 8682),
    ("src/execution/exit_lifecycle.py", 4679),
    ("src/execution/exchange_reconcile.py", 1417),
    ("src/execution/exchange_reconcile.py", 1812),
    ("src/events/edli_position_bridge.py", 1002),
}

_SEED_FUNNEL_WRITER = ("src/state/projection.py", 659)

_SEED_EDLI_AUDIT_WRITERS = {
    ("src/events/live_profit_audit.py", 237),
    ("src/analysis/settlement_skill_attribution.py", 1127),
}


@pytest.fixture(scope="module")
def hits():
    return scan_all()


@pytest.fixture(scope="module")
def writer_locations(hits) -> set[tuple[str, int]]:
    return {(h.file, h.line) for h in hits if h.kind == "WRITE"}


def test_seed_bypass_writers_all_present(writer_locations: set[tuple[str, int]]) -> None:
    missing = _SEED_BYPASS_WRITERS - writer_locations
    assert not missing, f"scanner missed named bypass writer(s): {sorted(missing)}"


def test_seed_funnel_writer_present(writer_locations: set[tuple[str, int]]) -> None:
    assert _SEED_FUNNEL_WRITER in writer_locations


def test_seed_edli_audit_writers_present(writer_locations: set[tuple[str, int]]) -> None:
    missing = _SEED_EDLI_AUDIT_WRITERS - writer_locations
    assert not missing, f"scanner missed EDLI audit writer(s): {sorted(missing)}"


def test_funnel_writer_unresolved_assumes_full_forbidden_set(hits) -> None:
    """projection.py's INSERT builds its column list from an imported
    constant (CANONICAL_POSITION_CURRENT_COLUMNS) — no literal column name
    lives in the enclosing function's own text. The scanner must NOT report
    a spurious partial match; it must fall back to the full forbidden set for
    position_current and mark it unresolved."""
    from src.contracts.economics_ownership import FORBIDDEN_COLUMNS_BY_TABLE

    funnel = [
        h for h in hits
        if h.kind == "WRITE" and (h.file, h.line) == _SEED_FUNNEL_WRITER
    ]
    assert len(funnel) == 1
    hit = funnel[0]
    assert hit.resolved is False
    assert set(hit.columns) == FORBIDDEN_COLUMNS_BY_TABLE["position_current"]


def test_dict_driven_dynamic_writer_resolves_to_its_actual_columns(hits) -> None:
    """position_duplicate_consolidator.py's UPDATE builds its SET clause from
    a local dict (`updates = {...}`) inside the SAME function — the scanner
    must resolve the real column names from that dict, not fall back to the
    full forbidden set."""
    hit = next(
        h for h in hits
        if h.kind == "WRITE" and (h.file, h.line) == ("src/state/position_duplicate_consolidator.py", 370)
    )
    assert hit.resolved is True
    assert set(hit.columns) == {"shares", "cost_basis_usd", "size_usd", "chain_shares", "entry_price"}


def test_no_duplicate_hits_for_the_same_site() -> None:
    """f-string literal segments are visited both as part of their JoinedStr
    and (via ast.walk) as independent Constant nodes — regression coverage
    for the double-count bug where a single dynamic UPDATE was emitted twice."""
    hits_local = scan_all()
    seen = [(h.file, h.line, h.kind, h.table, h.columns) for h in hits_local]
    assert len(seen) == len(set(seen))


# --------------------------------------------------------------------------- #
# Determinism                                                                 #
# --------------------------------------------------------------------------- #

def test_render_manifest_is_deterministic_across_two_scans() -> None:
    content_a = render_manifest(scan_all())
    content_b = render_manifest(scan_all())
    assert content_a == content_b


# --------------------------------------------------------------------------- #
# --check mode                                                                #
# --------------------------------------------------------------------------- #

def test_check_mode_passes_against_committed_manifest() -> None:
    """The committed manifest must match a fresh scan of the current tree —
    this is the CI drift gate itself, exercised directly (not just via
    subprocess) so a failure here points straight at the mismatch."""
    fresh = render_manifest(scan_all())
    assert OUTPUT_PATH.exists(), f"{OUTPUT_PATH} must be committed"
    committed = OUTPUT_PATH.read_text()
    assert fresh == committed, (
        "committed economics_writer_manifest.md has drifted from a fresh scan — "
        "run `python scripts/gen_economics_writer_manifest.py` and commit the result"
    )


def test_check_subprocess_exits_zero_on_no_drift() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/gen_economics_writer_manifest.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_check_subprocess_exits_nonzero_on_drift(tmp_path, monkeypatch) -> None:
    import scripts.gen_economics_writer_manifest as gen_mod

    fake_output = tmp_path / "economics_writer_manifest.md"
    fake_output.write_text("stale content that will never match a fresh scan\n")
    monkeypatch.setattr(gen_mod, "OUTPUT_PATH", fake_output)

    rc = gen_mod.main(["--check"])
    assert rc == 1
