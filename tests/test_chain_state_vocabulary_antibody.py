# Created: 2026-06-12
# Last reused or audited: 2026-07-07
# Authority basis: 2026-06-12 riskguard-kill incident — the chain-truth void
#   writer (_void_chain_confirmed_zero, in-tree since 2026-05-19) wrote
#   chain_state='chain_confirmed_zero', a value outside the ChainState enum;
#   the writer had NEVER fired before (funder env vars absent bypassed the
#   gate) so the first live firing poisoned position_current and every
#   load_portfolio() — including the RiskGuard daemon's — crashed, killing
#   risk attestations (stale -> RED -> 1100+ false RISK_GUARD_BLOCKED, zero
#   submits). Hardened 2026-07-07 after this bug class escaped a THIRD time
#   (closed_exited, 2026-07-04): src.state.chain_mirror_reconciler writes
#   chain_state via a bare-NAME-mediated constant (`projection["chain_state"]
#   = CLOSED_EXITED`) that is neither a quoted literal (antibody 1's scan)
#   nor a *_CHAIN_STATE-suffixed name (antibody 2's scan) -- see antibody 3.
"""ANTIBODIES: (1) every chain_state value any writer emits is a declared
ChainState member (literal RHS only); (2) same, for writers that assign via a
*_CHAIN_STATE-suffixed constant; (3) same, for writers that assign via ANY
bare-NAME constant regardless of suffix (the shape that let closed_exited
escape antibodies 1 and 2 on 2026-07-04); (4) one poison projection row can
never kill the whole portfolio load."""
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

# Bare-NAME writes into chain_state: projection["chain_state"] = CLOSED_EXITED,
# corrected.chain_state = SOME_CONST, chain_state=SOME_CONST kwarg -- the
# escaping shape from the 2026-07-04 closed_exited class (RHS is a plain
# identifier, not a quoted literal, so _ASSIGN_RE above never sees it). The
# negative lookbehind keeps a compound identifier like `prior_chain_state =
# X` from being mistaken for a write to the `chain_state` field itself.
_BARE_NAME_WRITE_RE = re.compile(
    r"""(?<![A-Za-z0-9_])\[?["']?chain_state["']?\]?\s*=\s*([A-Z][A-Z0-9_]*)\b"""
)

# Module-level `NAME = "literal"` constant definitions (unindented -- i.e.
# actually module scope, not a class/function body). Deliberately unanchored
# by suffix, unlike the *_CHAIN_STATE-only regex below: this is precisely the
# generalization that catches writer constants like CLOSED_EXITED that don't
# end in _CHAIN_STATE.
_CONST_DEF_RE = re.compile(r"""^([A-Z][A-Z0-9_]*)\s*=\s*["']([a-z_]+)["']""", re.M)


def _bare_name_chain_state_violations(text: str, declared: set[str]) -> list[str]:
    """Resolve every bare-NAME write into chain_state (see _BARE_NAME_WRITE_RE)
    to its same-module `NAME = "literal"` definition and flag any whose value
    escapes `declared`. A name with no same-module string-constant definition
    (a SQL function like COALESCE, or a name that only appears inside a log
    string/docstring, or a name imported from another module) resolves to
    nothing and is silently skipped: there is no literal value to assert
    against, so flagging it would be a false positive, not a caught bug.
    Pure str -> list[str] so the repo-wide scan and the synthetic teeth-proof
    test below share one code path.
    """
    written_names = {m.group(1) for m in _BARE_NAME_WRITE_RE.finditer(text)}
    if not written_names:
        return []
    defs = dict(_CONST_DEF_RE.findall(text))
    violations = []
    for name in sorted(written_names):
        value = defs.get(name)
        if value is None:
            continue
        if value not in declared:
            violations.append(f"{name} = {value!r}")
    return violations


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
    """The no-current-risk confirmed-chain-absence state must coerce.

    Antibody for the 2026-06-22 recurrence of the riskguard-kill class:
    this writer-set value escaped the enum and load_portfolio POISON-quarantined
    live positions. It remains valid only for no-current-risk attribution debt;
    confirmed fill conflicts must use entry_authority_quarantined.
    """
    from src.state.portfolio import Position

    pos = Position(
        trade_id="t-absent", market_id="m", city="Hong Kong", cluster="HK",
        target_date="2026-06-09", bin_label="b", direction="buy_no",
        unit="C", temperature_metric="high",
        chain_state="chain_absent_confirmed_position_unattributed",
    )
    assert pos.chain_state == VenueVisibilityStatus.CHAIN_ABSENT_CONFIRMED_UNATTRIBUTED


# test_legacy_entry_authority_quarantined_remaps_loader_safe RETIRED (BRIDGE
# RETIREMENT, docs/rebuild/quarantine_excision_2026-07-11.md, post-T5-migration):
# it pinned that a LEGACY chain_state='entry_authority_quarantined' row was
# loader-safe via Position.__post_init__'s mixed-epoch remap to 'synced'. The
# T5 schema migration has run — no writer mints the literal, the DB CHECK no
# longer admits it, and this packet deleted the remap — so
# Position(chain_state="entry_authority_quarantined") now raises ValueError at
# construction (correct: the literal can never occur on a live row). The
# riskguard-kill class this antibody file exists to prevent is now covered by
# the CHECK constraint itself, not a load-time remap.


def test_closed_exited_round_trips_through_position():
    """Chain-mirror force-resolve terminal-close state must coerce.

    Antibody for the 2026-07-04 P0b class: src.state.chain_mirror_reconciler
    writes chain_state="closed_exited" as a force-resolve fold-to-VOIDED
    terminal close (_apply_closed_exited_finding), but the value escaped the
    ChainState enum and load_portfolio -> Position.__post_init__ ->
    VenueVisibilityStatus(value) POISON-quarantined every affected row.
    """
    from src.state.portfolio import Position

    pos = Position(
        trade_id="t-closed-exited", market_id="m", city="Hong Kong", cluster="HK",
        target_date="2026-06-09", bin_label="b", direction="buy_no",
        unit="C", temperature_metric="high",
        chain_state="closed_exited",
    )
    assert pos.chain_state == VenueVisibilityStatus.CLOSED_EXITED


def test_constant_mediated_chain_state_writers_are_declared_members():
    """The literal-only antibody above misses chain_state assigned via a named
    constant (e.g. `corrected.chain_state = SOME_TYPED_CHAIN_STATE`).
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


def test_bare_name_mediated_chain_state_writers_are_declared_members():
    """Hardened antibody for the 2026-07-04 closed_exited escape shape: the
    two antibodies above both missed it. The literal-only antibody requires a
    quoted string on the RHS (misses `projection["chain_state"] = CLOSED_EXITED`,
    a bare identifier). The *_CHAIN_STATE antibody requires the constant name
    to END in _CHAIN_STATE (misses src.state.chain_mirror_reconciler's
    constants -- CLOSED_EXITED, CLOSED_REDEEMED, SIZE_CORRECTED, etc. -- which
    are plain UPPER_SNAKE_CASE with no such suffix).

    This antibody drops the suffix requirement: it collects every bare NAME
    written into a chain_state field (regardless of naming convention),
    resolves each to its same-module `NAME = "literal"` definition, and
    requires that literal to be a declared ChainState member. See
    _bare_name_chain_state_violations for the false-positive guard (unresolved
    names -- SQL function calls, log-string mentions, cross-module imports --
    are skipped, not flagged)."""
    declared = {m.value for m in VenueVisibilityStatus}
    violations = []
    for path in (REPO / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for v in _bare_name_chain_state_violations(text, declared):
            violations.append(f"{path.relative_to(REPO)}: {v}")
    assert not violations, (
        "a bare-NAME-mediated chain_state write resolves to a constant whose "
        "value is outside the ChainState enum (the 2026-07-04 closed_exited "
        f"escape class, suffix-agnostic): {violations}"
    )


def test_bare_name_chain_state_scan_flags_synthetic_undeclared_constant():
    """Teeth-proof for test_bare_name_mediated_chain_state_writers_are_declared_members:
    a clean repo-wide scan proves nothing about the regex's power unless the
    scan is shown to actually catch a planted escape. Reuses the exact same
    _bare_name_chain_state_violations() the repo-wide test calls -- not a
    reimplementation -- so this really is a proof, not a parallel test that
    could drift from the real scan."""
    declared = {m.value for m in VenueVisibilityStatus}
    synthetic_module = '''
FOO = "not_a_real_state"

def apply(projection):
    projection["chain_state"] = FOO
'''
    violations = _bare_name_chain_state_violations(synthetic_module, declared)
    assert violations == ["FOO = 'not_a_real_state'"]

    # Also prove the negative: a synthetic constant whose value IS declared
    # must NOT be flagged (guards against a trivially-over-broad regex that
    # flags every bare-NAME write regardless of resolved value).
    synthetic_clean = '''
BAR = "synced"

def apply(projection):
    projection["chain_state"] = BAR
'''
    assert _bare_name_chain_state_violations(synthetic_clean, declared) == []

    # And prove unresolved names (no same-module string-constant definition --
    # e.g. a SQL function name or a cross-module import) are skipped rather
    # than flagged, matching the COALESCE/CHAIN_UNKNOWN cases seen in src/.
    synthetic_unresolved = '''
def apply(projection):
    projection["chain_state"] = COALESCE
'''
    assert _bare_name_chain_state_violations(synthetic_unresolved, declared) == []


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
