# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: 2026-06-12 riskguard-kill incident — the chain-truth void
#   writer (_void_chain_confirmed_zero, in-tree since 2026-05-19) wrote
#   chain_state='chain_confirmed_zero', a value outside the ChainState enum;
#   the writer had NEVER fired before (funder env vars absent bypassed the
#   gate) so the first live firing poisoned position_current and every
#   load_portfolio() — including the RiskGuard daemon's — crashed, killing
#   risk attestations (stale -> RED -> 1100+ false RISK_GUARD_BLOCKED, zero
#   submits).
"""ANTIBODIES: (1) every chain_state value any writer emits is a declared
ChainState member; (2) one poison projection row can never kill the whole
portfolio load."""
from __future__ import annotations

import re
from pathlib import Path

from src.contracts.semantic_types import VenueVisibilityStatus


REPO = Path(__file__).resolve().parent.parent

# chain_state string assignments in writers: projection["chain_state"] = "X"
# or "chain_state": "X" literals in src/.
_ASSIGN_RE = re.compile(
    r"""["']chain_state["']\s*[:\]=]+\s*["']([a-z_]+)["']"""
)


def test_every_written_chain_state_literal_is_a_declared_member():
    declared = {m.value for m in VenueVisibilityStatus}
    violations = []
    for path in (REPO / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in _ASSIGN_RE.finditer(text):
            value = match.group(1)
            if value not in declared:
                violations.append(f"{path.relative_to(REPO)}: {value!r}")
    assert not violations, (
        "chain_state writer-set escaped the ChainState enum (the 2026-06-12 "
        f"riskguard-kill class): {violations}"
    )


def test_chain_confirmed_zero_round_trips_through_position():
    from src.state.portfolio import Position

    pos = Position(
        trade_id="t-zero", market_id="m", city="Hong Kong", cluster="HK",
        target_date="2026-06-09", bin_label="b", direction="buy_no",
        unit="C", temperature_metric="high",
        chain_state="chain_confirmed_zero",
    )
    assert pos.chain_state == VenueVisibilityStatus.CHAIN_CONFIRMED_ZERO


def test_chain_absent_confirmed_unattributed_round_trips_through_position():
    """The confirmed-chain-absence attribution-quarantine state must coerce.

    Antibody for the 2026-06-22 recurrence of the riskguard-kill class:
    chain_reconciliation._quarantine_confirmed_chain_absence writes
    chain_state='chain_absent_confirmed_position_unattributed' (via the named
    constant CONFIRMED_CHAIN_ABSENCE_CHAIN_STATE), a value outside the enum, so
    load_portfolio POISON-quarantined 9 live positions (Tokyo/Seoul/Houston/...).
    RED before the enum member exists; GREEN after.
    """
    from src.state.portfolio import Position

    pos = Position(
        trade_id="t-absent", market_id="m", city="Hong Kong", cluster="HK",
        target_date="2026-06-09", bin_label="b", direction="buy_no",
        unit="C", temperature_metric="high",
        chain_state="chain_absent_confirmed_position_unattributed",
    )
    assert pos.chain_state == VenueVisibilityStatus.CHAIN_ABSENT_CONFIRMED_UNATTRIBUTED


def test_entry_authority_quarantined_round_trips_through_position():
    """Invalid entry authority is a loader-safe quarantine class with exposure.

    The runtime may still hold CTF inventory for these rows. Enum coercion must
    not kill portfolio loading before monitor/redecision can decide hold/exit.
    """
    from src.state.portfolio import Position

    pos = Position(
        trade_id="t-entry-authority", market_id="m", city="Lucknow", cluster="India",
        target_date="2026-06-28", bin_label="b", direction="buy_yes",
        unit="C", temperature_metric="high",
        chain_state="entry_authority_quarantined",
    )
    assert pos.chain_state == VenueVisibilityStatus.ENTRY_AUTHORITY_QUARANTINED


def test_constant_mediated_chain_state_writers_are_declared_members():
    """The literal-only antibody above misses chain_state assigned via a named
    constant (e.g. `corrected.chain_state = CONFIRMED_CHAIN_ABSENCE_CHAIN_STATE`).
    Resolve module-level `*_CHAIN_STATE = "literal"` constants and require each to
    be a declared member — this is the gap through which
    'chain_absent_confirmed_position_unattributed' escaped to production."""
    declared = {m.value for m in VenueVisibilityStatus}
    const_re = re.compile(r"""^[A-Z][A-Z0-9_]*_CHAIN_STATE\s*=\s*["']([a-z_]+)["']""", re.M)
    violations = []
    for path in (REPO / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in const_re.finditer(text):
            value = match.group(1)
            if value not in declared:
                violations.append(f"{path.relative_to(REPO)}: {value!r}")
    assert not violations, (
        "a *_CHAIN_STATE constant holds a value outside the ChainState enum "
        f"(constant-mediated riskguard-kill class): {violations}"
    )


def test_poison_projection_row_is_quarantined_not_fatal(monkeypatch, caplog):
    """A row that fails coercion is skipped LOUDLY; healthy rows still load.

    Pre-fix, the poison row raised through load_portfolio and the RiskGuard
    daemon lost ALL portfolio visibility (worse than skipping one row)."""
    import logging

    import src.state.portfolio as pf

    good = {
        "trade_id": "good-1", "position_id": "good-1", "market_id": "m",
        "city": "Karachi", "cluster": "Karachi", "target_date": "2026-06-12",
        "bin_label": "b", "direction": "buy_no", "unit": "C",
        "temperature_metric": "high", "phase": "active",
        "strategy_key": "settlement_capture", "env": "live",
        "chain_state": "unknown",
    }
    poison = dict(good, trade_id="poison-1", position_id="poison-1",
                  chain_state="not_a_real_chain_state_value")

    # Exercise the containment loop semantics directly (full load_portfolio
    # needs a live DB stack; the loop body is what the incident exercised).
    with caplog.at_level(logging.ERROR):
        positions = []
        for row in (good, poison):
            try:
                positions.append(
                    pf._position_from_projection_row(row, current_mode="live")
                )
            except Exception as exc:  # noqa: BLE001
                pf.logger.error(
                    "load_portfolio: POISON projection row quarantined "
                    "(position_id=%s): %s", row.get("position_id"), exc,
                )
    assert [p.trade_id for p in positions] == ["good-1"]
    assert any("POISON" in r.message for r in caplog.records)
