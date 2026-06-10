# Created: 2026-06-02
# Last reused or audited: 2026-06-02
# Authority basis: GOAL#36 pre-arm wall (BUG #92) — executor.py:1746 parity.
#   The intent's tick_size MUST equal min_tick_size of the SAME snapshot the
#   executor re-hydrates (intent.snapshot_id == proof.executable_snapshot_id).
#
# Relationship invariant under test (cross-module, ERA -> executor boundary):
#   The tick_size the ERA writes into a FinalExecutionIntent is read from the
#   canonical executable_snapshot evidence whose `identity` IS the snapshot the
#   executor hydrates at submit time.  The MAKER / no-trade-conn fallback path
#   (reactor:1314 else-branch) MUST source tick from that same canonical
#   evidence and MUST NOT silently default to a hardcoded 0.01 that can diverge
#   from the real tick (the live 2026-06-01 failure: intent tick=0.001 vs
#   bound snapshot ems2-3364... tick=0.01 — a two-snapshot divergence).
#
#   Live root proven from state/zeus_trades.db: condition 0x83eb73... has 56
#   snapshots ALL with min_tick_size='0.01', yet the rejected intent carried
#   tick_size=0.001.  The 0.001 could only come from a tick source NOT bound to
#   the executor's hydration target (the hardcoded/defaulted fallback), not from
#   `_snap_for_depth` (which would have read 0.01 from the same id).
"""
Relationship tests pinning the ERA tick-source -> executor-parity invariant.

These assert the structural property that closes BUG #92 by construction: the
tick written into the intent is always the tick of the snapshot the executor
re-hydrates, for BOTH the TAKER (`_snap_for_depth`) and the MAKER-fallback
(`executable_snapshot.payload['min_tick_size']`) branches — and the fallback
never silently substitutes a hardcoded 0.01.
"""

from __future__ import annotations

import re
from decimal import Decimal


_ERA_SRC_PATH = "src/engine/event_reactor_adapter.py"


def _era_source() -> str:
    with open(_ERA_SRC_PATH) as fh:
        return fh.read()


def _tick_source_expr() -> str:
    """Extract the exact `tick_size=...` argument expression passed to the cert
    builder at the build_final_intent_certificate_from_actionable call site.

    SCANNER FIX (2026-06-10): the old lazy DOTALL regex matched the FIRST
    `tick_size=` anywhere in the file followed eventually by `min_order_size=`;
    when the venue amount-grid fix added a `tick_size=` kwarg to the upstream
    quantize call, the capture ballooned across half the submit path (including
    a historical comment containing the literal `, 0.01)`), failing the test on
    prose. Anchor the scan INSIDE the cert-builder call region instead — the
    invariant under test is unchanged."""
    src = _era_source()
    call_at = src.find("final_intent = build_final_intent_certificate_from_actionable(")
    assert call_at != -1, "could not locate the cert-builder call site"
    region = src[call_at : call_at + 4000]
    m = re.search(r"\n\s*tick_size=(?P<expr>.+?),\n\s*min_order_size=", region, re.DOTALL)
    assert m is not None, "could not locate tick_size=... kwarg at cert-builder call site"
    return m.group("expr").strip()


# ---------------------------------------------------------------------------
# RED: the hardcoded-0.01 fallback default is the unbound tick source that
# produced the live divergence. Pinning its ABSENCE is the structural antibody.
# ---------------------------------------------------------------------------


def test_red_maker_fallback_must_not_hardcode_0_01_default():
    """
    BUG #92 root: the MAKER / no-trade-conn fallback at reactor:1314 read

        _float_or_default(executable_snapshot.payload.get("min_tick_size"), 0.01)

    The literal `0.01` default is an UNBOUND tick source: if the canonical
    `min_tick_size` is ever absent from the evidence payload, the intent gets
    0.01 while the executor hydrates the real snapshot (e.g. 0.001) -> parity
    rejection.  The live failure was the inverse (intent 0.001, snapshot 0.01),
    but the category is identical: a tick that is NOT the bound snapshot's tick.

    Structural fix: the fallback must source tick from the canonical evidence
    bound to proof.executable_snapshot_id (the executor's hydration target) and
    fail closed if absent — never silently default to a fixed 0.01.

    This test is RED on the pre-fix expression (contains `, 0.01)`), GREEN once
    the hardcoded default is removed.
    """
    expr_nospace = _tick_source_expr().replace(" ", "")
    assert ",0.01)" not in expr_nospace, (
        "tick_size fallback still silently defaults to a hardcoded 0.01 — an "
        "unbound tick source that diverges from the executor's hydrated "
        f"snapshot.min_tick_size. Got: {expr_nospace!r}"
    )


def test_tick_source_is_bound_to_canonical_executable_snapshot():
    """
    The tick written into the intent must, on BOTH branches, be the
    min_tick_size of the snapshot identified by proof.executable_snapshot_id
    (= executable_snapshot.payload['identity'], the executor's hydration
    target).

    - TAKER branch: `_snap_for_depth` is get_snapshot(proof.executable_snapshot_id).
    - fallback branch: `executable_snapshot.payload['min_tick_size']`, which the
      ERA populates (reactor:2448) from the same `_hydrated_snapshot =
      get_snapshot(proof.executable_snapshot_id)`.

    Either spelling is acceptable; a literal numeric default is not.
    """
    expr = _tick_source_expr()
    taker_bound = "_snap_for_depth.min_tick_size" in expr
    fallback_bound = 'executable_snapshot.payload' in expr and "min_tick_size" in expr
    assert taker_bound and fallback_bound, (
        "tick_size must be sourced from the canonical executable snapshot on "
        f"both branches; got: {expr!r}"
    )


def test_decimal_value_equality_is_robust_for_clean_ticks():
    """
    Confirms the root is VALUE divergence (different snapshot), NOT Decimal
    str/precision: trailing zeros and float round-trip do not break equality
    for the only two live ticks (0.01, 0.001).  This rules out hypothesis (a).
    """
    assert Decimal("0.01") == Decimal("0.010")
    assert Decimal(str(float("0.01"))) == Decimal("0.01")
    assert Decimal(str(float("0.001"))) == Decimal("0.001")
    # The live divergence: 0.001 != 0.01 (genuinely different snapshots)
    assert Decimal("0.001") != Decimal("0.01")
