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
# Seed sites are pinned by (file, enclosing function) — NOT line number —
# because sibling packets keep shifting offsets (LX-G moved exchange_reconcile,
# LX-E moved live_profit_audit). The site's identity is the function.
# settlement_skill_attribution.writeback_settlement_pnl_to_audit is absent by
# DESIGN: LX-E excised it (world_grade_pnl_usd lives in the grade receipt now).
# position_duplicate_consolidator._merge_equivalent_rows is absent by DESIGN:
# F2 (2794f8bb0) excised its synthesized-economics writes — it now appends only
# the POSITION_IDENTITY_SUPERSEDED fact.
_SEED_BYPASS_WRITERS = {
    ("src/state/position_duplicate_consolidator.py", "_void_row"),
    ("src/execution/command_recovery.py", "_append_exit_order_fill_projection"),
    ("src/execution/command_recovery.py", "_append_exit_filled_projection"),
    ("src/execution/command_recovery.py", "repair_confirmed_phantom_voids"),
    ("src/execution/command_recovery.py", "repair_confirmed_chain_absence_positive_projections"),
    ("src/execution/exit_lifecycle.py", "_close_pending_exit_from_trade_fact"),
    ("src/execution/exchange_reconcile.py", "_restore_position_to_pending_exit_for_recovered_sell"),
    ("src/execution/exchange_reconcile.py", "_tag_external_operator_closed_position_holdings"),
    ("src/events/edli_position_bridge.py", "_absorb_same_order_duplicate_bridge_fill"),
}

_SEED_FUNNEL_WRITER = ("src/state/projection.py", "upsert_position_current")

_SEED_EDLI_AUDIT_WRITERS = {
    ("src/events/live_profit_audit.py", "LiveProfitAuditLedger.insert_record"),
}


@pytest.fixture(scope="module")
def hits():
    return scan_all()


@pytest.fixture(scope="module")
def writer_locations(hits) -> set[tuple[str, str]]:
    return {(h.file, h.function) for h in hits if h.kind == "WRITE"}


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
        if h.kind == "WRITE" and (h.file, h.function) == _SEED_FUNNEL_WRITER
    ]
    assert len(funnel) == 1
    hit = funnel[0]
    assert hit.resolved is False
    assert set(hit.columns) == FORBIDDEN_COLUMNS_BY_TABLE["position_current"]


def test_dict_driven_dynamic_writer_resolves_to_its_actual_columns(hits) -> None:
    """_void_row's UPDATE names its columns inside the SAME function — the
    scanner must resolve the real column names, not fall back to the full
    forbidden set. (_merge_equivalent_rows, the original dict-driven exemplar,
    was excised by F2 — _void_row keeps this scanner behavior covered.)"""
    hit = next(
        h for h in hits
        if h.kind == "WRITE" and (h.file, h.function) == ("src/state/position_duplicate_consolidator.py", "_void_row")
    )
    assert hit.resolved is True
    assert set(hit.columns) == {"shares", "cost_basis_usd"}


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
    """The committed manifest's writer/reader IDENTITY set — (file, function,
    verb, table, sorted columns), deliberately NOT file:line — must match a
    fresh scan of the current tree. This is the CI drift gate itself,
    exercised directly (not just via subprocess) so a failure here points
    straight at the mismatch. Line-number-only drift (an unrelated commit
    moving code around) must NOT fail this test — see
    test_check_ignores_pure_line_shift below for that antibody."""
    from scripts.gen_economics_writer_manifest import _identity, _parse_manifest_identities

    fresh_hits = scan_all()
    fresh_writers = frozenset(_identity(h) for h in fresh_hits if h.kind == "WRITE")
    fresh_readers = frozenset(_identity(h) for h in fresh_hits if h.kind == "READ")
    assert OUTPUT_PATH.exists(), f"{OUTPUT_PATH} must be committed"
    committed_writers, committed_readers = _parse_manifest_identities(OUTPUT_PATH.read_text())
    assert fresh_writers == committed_writers, (
        "committed economics_writer_manifest.md writer set has drifted from a fresh scan — "
        "run `python scripts/gen_economics_writer_manifest.py` and commit the result. "
        f"added={fresh_writers - committed_writers} removed={committed_writers - fresh_writers}"
    )
    assert fresh_readers == committed_readers, (
        "committed economics_writer_manifest.md reader set has drifted from a fresh scan — "
        "run `python scripts/gen_economics_writer_manifest.py` and commit the result. "
        f"added={fresh_readers - committed_readers} removed={committed_readers - fresh_readers}"
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


def test_check_ignores_pure_line_shift(tmp_path, monkeypatch) -> None:
    """A commit that shifts every file:line in the tree (without changing any
    writer/reader's identity) must NOT trip --check. This is the wave-1.5
    repair antibody for the MAJOR defect: the old full-text comparison went
    red on pure line-drift, training operators to ignore the gate."""
    import re as _re

    import scripts.gen_economics_writer_manifest as gen_mod

    committed = OUTPUT_PATH.read_text()
    shifted = _re.sub(
        r"(`[^`:]+:)(\d+)(`)",
        lambda m: f"{m.group(1)}{int(m.group(2)) + 37}{m.group(3)}",
        committed,
    )
    assert shifted != committed  # sanity: the fixture actually changed something

    fake_output = tmp_path / "economics_writer_manifest.md"
    fake_output.write_text(shifted)
    monkeypatch.setattr(gen_mod, "OUTPUT_PATH", fake_output)

    rc = gen_mod.main(["--check"])
    assert rc == 0


def test_check_catches_new_writer_added(tmp_path, monkeypatch) -> None:
    """A real writer added to the tree (not just a line renumbering) must
    still trip --check."""
    import scripts.gen_economics_writer_manifest as gen_mod

    committed = OUTPUT_PATH.read_text()
    fabricated_row = (
        "| `src/fake_module_for_test.py:999` | INSERT | position_current | shares | "
        "no | yes | `fake_write_fn` |\n"
    )
    marker = "\n## Readers ("
    assert marker in committed
    mutated = committed.replace(marker, fabricated_row + marker, 1)
    assert mutated != committed

    fake_output = tmp_path / "economics_writer_manifest.md"
    fake_output.write_text(mutated)
    monkeypatch.setattr(gen_mod, "OUTPUT_PATH", fake_output)

    rc = gen_mod.main(["--check"])
    assert rc == 1
