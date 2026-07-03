# Created: 2026-06-30
# Last audited: 2026-06-30
# Authority basis: docs/operations/current/reports/state_vocabulary_canonical_redesign_2026-06-29.md;
#   consult round-2 (thread 6a42bc3d) migration step 1 — "Freeze the source-owner list.
#   For each mechanism create an owner module and an explicit no-other-writer antibody.
#   This is more important than deleting enum values."

"""INV-OWNER-1 antibody: one sanctioned SQL writer per stored-state mechanism.

The ideal state model (consult round-2 premise verdict) is an append-only fact/decision
log with ONE reducer per mechanism; every materialized projection has a single writer and
is recomputable from source facts. Multiple independent writers of one stored mechanism is
the root mechanism behind the scar-tissue states — phantom void reasons, stale terminal
projection events, quarantine-coercion crashes: a second writer lets the stored projection
drift from the facts, and the drift then needs a repair pass / a downstream ignore-filter.

Two tiers:
  * SINGLE-OWNER mechanisms are already clean (exactly one writer) and are LOCKED — they
    may not grow a second writer.
  * MULTI-WRITER mechanisms are baselined at their current writer set (may only SHRINK)
    with a documented single-owner RATCHET TARGET. `position_current` (the materialized A5
    position projection) is the worst case: 7 writers today, ideal is the projector alone.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"

# Mechanisms that already have exactly ONE writer — LOCK the owner; forbid any second writer.
_SINGLE_OWNER: dict[str, str] = {
    "venue_order_facts": "state/venue_command_repo.py",
    "venue_trade_facts": "state/venue_command_repo.py",
    "position_lots": "state/venue_command_repo.py",
    "settlement_outcomes": "state/db.py",
}

# Mechanisms with multiple writers today — FREEZE the current set (may only shrink),
# ratchet toward the single ideal owner. value = (current writer set, ideal single owner).
_MULTI_WRITER_BASELINE: dict[str, tuple[frozenset[str], str]] = {
    # The materialized A5 position projection. Ideal: the projector is the sole writer;
    # ledger append + dedup repair + recovery/bridge/reconcile/main paths should feed the
    # reducer, not write the projection row directly.
    "position_current": (
        frozenset({
            "state/projection.py",
            "state/ledger.py",
            "state/position_duplicate_consolidator.py",
            "events/edli_position_bridge.py",
            "execution/command_recovery.py",
            "execution/exchange_reconcile.py",
            "main.py",
        }),
        "state/projection.py",
    ),
    # Command/outbox: the repo owns command truth; the reconciler should emit command
    # events for the reducer, not UPDATE venue_commands.state directly.
    "venue_commands": (
        frozenset({"state/venue_command_repo.py", "execution/exchange_reconcile.py"}),
        "state/venue_command_repo.py",
    ),
    # Redemption accounting: the settlement-command repo owns it; executor should route
    # through it rather than write settlement_commands directly.
    "settlement_commands": (
        frozenset({"execution/settlement_commands.py", "execution/executor.py"}),
        "execution/settlement_commands.py",
    ),
}


def _writer_regex(table: str) -> re.Pattern[str]:
    """INSERT/REPLACE INTO <tbl> or UPDATE <tbl> SET, with optional schema/ATTACH prefix."""
    t = re.escape(table)
    return re.compile(
        rf"(?:INSERT\s+INTO|REPLACE\s+INTO)\s+(?:\w+\.)?{t}\b"
        rf"|UPDATE\s+(?:\w+\.)?{t}\s+SET",
        re.IGNORECASE,
    )


def _current_writers(table: str) -> set[str]:
    rx = _writer_regex(table)
    writers: set[str] = set()
    for py in _SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        if rx.search(text):
            writers.add(py.relative_to(_SRC).as_posix())
    return writers


def test_single_owner_mechanisms_have_no_second_writer() -> None:
    """The already-clean mechanisms must keep exactly their one sanctioned writer."""
    violations: dict[str, set[str]] = {}
    for table, owner in _SINGLE_OWNER.items():
        extra = _current_writers(table) - {owner}
        if extra:
            violations[table] = extra
    assert not violations, (
        "INV-OWNER-1 violated: a single-owner stored mechanism grew a second SQL writer "
        "(drift risk — route it through the owner/reducer instead). "
        f"{ {k: sorted(v) for k, v in violations.items()} }"
    )


def test_single_owner_baseline_not_stale() -> None:
    """If a locked owner no longer writes its table, the baseline is wrong — fix it."""
    missing = {t: o for t, o in _SINGLE_OWNER.items() if o not in _current_writers(t)}
    assert not missing, (
        f"INV-OWNER-1 single-owner baseline stale (owner no longer writes table): {missing}"
    )


def test_multi_writer_mechanisms_do_not_grow() -> None:
    """Baselined multi-writer mechanisms may only SHRINK toward their single-owner target."""
    grew: dict[str, set[str]] = {}
    for table, (baseline, _ideal) in _MULTI_WRITER_BASELINE.items():
        extra = _current_writers(table) - baseline
        if extra:
            grew[table] = extra
    assert not grew, (
        "INV-OWNER-1 violated: a multi-writer mechanism gained a NEW writer instead of "
        "ratcheting down toward its single-owner target. "
        f"{ {k: sorted(v) for k, v in grew.items()} }"
    )


def test_multi_writer_baseline_not_stale() -> None:
    """If a baselined writer stopped writing (ratchet progress!), tighten the baseline."""
    stale: dict[str, set[str]] = {}
    for table, (baseline, _ideal) in _MULTI_WRITER_BASELINE.items():
        gone = baseline - _current_writers(table)
        if gone:
            stale[table] = gone
    assert not stale, (
        "INV-OWNER-1 multi-writer baseline stale — a writer was removed (good!); tighten "
        f"_MULTI_WRITER_BASELINE to lock the progress: { {k: sorted(v) for k, v in stale.items()} }"
    )
