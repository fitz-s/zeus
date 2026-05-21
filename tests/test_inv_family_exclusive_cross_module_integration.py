# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=2026-05-21
# Purpose: Cross-module relationship antibody — P0-1 STAGE A family-exclusive entry gate
#          fires through execute_discovery_phase (cycle_runtime), not just in unit isolation.
#          P0-1 opus critic Major #1 follow-up.
# Authority basis: operator P0-1 live-money spec 2026-05-20/21 (mutually-exclusive
#                  weather family sizing), STAGE A.

"""Cross-module integration: family-exclusive entry gate through cycle_runtime.

Relationship under test (Fitz §3):
    deps.evaluate_candidate → [3 should_trade=True decisions, same family]
        │
        └─ cycle_runtime.execute_discovery_phase
               │
               └─ dedup_mutually_exclusive_families   ← gate lives HERE
                        │
                        ├─ 1 decision: should_trade=True   → reaches execution dispatch
                        └─ 2 decisions: should_trade=False  → silently dropped (STAGE A
                                        pure runtime gating, no artifact.add_no_trade)

Why this is NOT a unit test:
    The existing test at test_inv_family_exclusive_sizing.py:151 imports
    ``dedup_mutually_exclusive_families`` and calls it directly with constructed
    inputs. That test verifies the function's internal logic.

    THIS test verifies the CROSS-MODULE RELATIONSHIP: that ``execute_discovery_phase``
    (cycle_runtime) invokes ``dedup_mutually_exclusive_families`` at the right point in
    the pipeline and that the gate's in-place mutation propagates to the execution
    dispatch layer. The test uses a mocked ``deps.evaluate_candidate`` to inject
    3 family-conflicting decisions and observes the post-gate decision state.

STAGE A boundary constraint (preserved in test assertions):
    Dedup-dropped decisions have ``rejection_stage="MUTUALLY_EXCLUSIVE_FAMILY"`` and
    ``"mutually_exclusive_family_dedup" in rejection_reasons`` but do NOT produce
    no_trade_events DB rows (``rejection_reason_enum`` stays None). STAGE B will
    promote the string to a NoTradeReason enum + DB migration. Test assertions must
    NOT assert any DB row or enum existence — that would be a false-positive failure
    today and a Stage-B blocker test.

Observation mechanism:
    ``dedup_mutually_exclusive_families`` mutates EdgeDecision objects in-place.
    The ``deps.evaluate_candidate`` wrapper captures the returned list *before*
    cycle_runtime calls dedup. After ``execute_discovery_phase`` returns, the
    same list objects reflect the post-dedup mutations (Python reference semantics).
"""

from __future__ import annotations

import logging
import types
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import MagicMock

import pytest

from src.config import City
from src.engine.cycle_runtime import execute_discovery_phase
from src.engine.discovery_mode import DiscoveryMode
from src.engine.evaluator import EdgeDecision, MarketCandidate
from src.state.decision_chain import CycleArtifact, NoTradeCase
from src.state.portfolio import PortfolioState
from src.strategy.family_exclusive_dedup import MUTUALLY_EXCLUSIVE_FAMILY_DEDUP
from src.types import Bin, BinEdge

# ── Fixtures ─────────────────────────────────────────────────────────────────

CITY_NAME = "Chicago"
TARGET_DATE = "2026-06-15"
METRIC = "high"

_CHICAGO = City(
    name=CITY_NAME,
    lat=41.98,
    lon=-87.90,
    timezone="America/Chicago",
    settlement_unit="F",
    cluster="midwest",
    wu_station="KORD",
)

# Five mutually-exclusive temperature bins for ONE (city, date, metric) market.
_BIN_SPECS = [
    (None, 19, "19°F or below", 0),
    (20, 21, "20-21°F", 1),
    (22, 23, "22-23°F", 2),
    (24, 25, "24-25°F", 3),
    (26, None, "26°F or above", 4),
]


def _bin_edge(spec, *, entry_price: float, forward_edge: float) -> BinEdge:
    low, high, label, support = spec
    return BinEdge(
        bin=Bin(low=low, high=high, unit="F", label=label),
        direction="buy_yes",
        edge=forward_edge,
        ci_lower=0.02,
        ci_upper=0.18,
        p_model=0.40,
        p_market=entry_price,
        p_posterior=0.40,
        entry_price=entry_price,
        p_value=0.01,
        vwmp=entry_price,
        forward_edge=forward_edge,
        support_index=support,
    )


def _trade_decision(
    spec,
    *,
    size_usd: float,
    forward_edge: float,
    entry_price: float = 0.45,
    decision_id: Optional[str] = None,
) -> EdgeDecision:
    """Minimal should_trade=True EdgeDecision as the evaluator emits per selected bin."""
    label = spec[2]
    return EdgeDecision(
        should_trade=True,
        edge=_bin_edge(spec, entry_price=entry_price, forward_edge=forward_edge),
        tokens={"yes_token_id": f"tok-{label}", "executable_snapshot_min_order_size": 1.0},
        size_usd=size_usd,
        decision_id=decision_id or f"dec-{label}",
        decision_snapshot_id="snap-1",
        strategy_key="shoulder_buy",
        selected_method="entry_forecast",
    )


def _three_family_decisions() -> list[EdgeDecision]:
    """3 bins from one (city, date, metric) family, all should_trade=True.

    Differentiated sizes so single-best selection is unambiguous:
    22-23°F has the largest size_usd ($20) → gate keeps this one.
    """
    bins = {s[2]: s for s in _BIN_SPECS}
    return [
        _trade_decision(bins["20-21°F"], size_usd=12.0, forward_edge=0.05),
        _trade_decision(bins["22-23°F"], size_usd=20.0, forward_edge=0.07),  # best
        _trade_decision(bins["26°F or above"], size_usd=8.0, forward_edge=0.04),
    ]


def _market_dict() -> dict:
    """Minimal market dict for the loop — passes MarketCandidate construction."""
    return {
        "city": _CHICAGO,
        "target_date": TARGET_DATE,
        "temperature_metric": METRIC,
        "outcomes": [
            {"title": s[2], "range_low": s[0], "range_high": s[1]}
            for s in _BIN_SPECS
        ],
        "hours_since_open": 30.0,  # >= 24 to pass UPDATE_REACTION min_hours_since_open filter
        "hours_to_resolution": 48.0,
        "event_id": "evt-chicago-test",
        "slug": "will-chicago-high-temp-on-2026-06-15",
        "condition_id": None,
    }


# ── Minimal deps builder ──────────────────────────────────────────────────────

def _build_deps(
    decisions: list[EdgeDecision],
    captured: list,
) -> types.SimpleNamespace:
    """Build the minimal deps namespace needed for execute_discovery_phase.

    ``captured`` is a mutable list the wrapper appends decisions to.
    After execute_discovery_phase returns, the same decision objects
    reflect post-dedup in-place mutations.
    """
    # evaluate_candidate wrapper: capture decisions, return them.
    def _evaluate_candidate(candidate, conn, portfolio, clob, limits, **kwargs):
        # Return fresh references to the same objects so dedup mutates them.
        captured.append(decisions)
        return decisions

    # NoTradeCase from decision_chain (real dataclass).
    def _no_trade_case(**kwargs):
        return NoTradeCase(**kwargs)

    deps = types.SimpleNamespace(
        logger=logging.getLogger("test.execute_discovery_phase"),
        MODE_PARAMS={
            DiscoveryMode.UPDATE_REACTION: {
                "min_hours_since_open": 24,
                "min_hours_to_resolution": 6,
            },
        },
        find_weather_markets=lambda **kwargs: [_market_dict()],
        MarketCandidate=MarketCandidate,
        evaluate_candidate=_evaluate_candidate,
        NoTradeCase=_no_trade_case,
        _classify_edge_source=lambda mode, edge: "ens",
        is_strategy_enabled=lambda strategy_key: True,
        # Optional deps — provide as no-ops so cycle_runtime's
        # getattr(deps, ...) guards succeed gracefully.
        oracle_penalty_reload=None,
        # Return VERIFIED so execute_discovery_phase doesn't short-circuit
        # the market loop with scan_availability_status="DATA_UNAVAILABLE".
        get_last_scan_authority=lambda: "VERIFIED",
        capture_executable_market_snapshot=None,
        reprice_from_snapshot=None,
        execute_final_intent=None,
        add_position=lambda portfolio, pos: None,
    )
    return deps


def _build_harness(decisions: list[EdgeDecision]):
    """Return (deps, captured, artifact, portfolio, summary)."""
    captured: list[list[EdgeDecision]] = []
    deps = _build_deps(decisions, captured)
    artifact = CycleArtifact(
        mode="update_reaction",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    portfolio = PortfolioState(bankroll=5000.0, positions=[])
    summary: dict = {"no_trades": 0, "candidates": 0}
    return deps, captured, artifact, portfolio, summary


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_gate_on_collapses_family_to_single_best_through_execute_discovery_phase(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """RELATIONSHIP test: execute_discovery_phase invokes dedup; exactly 1 entry
    survives the exclusive-family gate for a same-(city, date, metric) family.

    Cross-module contract:
        evaluate_candidate returns 3 should_trade=True decisions
        → execute_discovery_phase (cycle_runtime) calls dedup in-line
        → after the call, exactly 1 decision has should_trade=True (the $20 bin)
        → the other 2 carry the auditable STAGE A rejection contract:
              rejection_stage == "MUTUALLY_EXCLUSIVE_FAMILY"
              "mutually_exclusive_family_dedup" in rejection_reasons
              rejection_reason_detail includes kept_bin label
              rejection_reason_enum IS None (STAGE A pure runtime gating)

    Unlike the unit test in test_inv_family_exclusive_sizing.py, this test does
    NOT import or call dedup_mutually_exclusive_families directly. The gate fires
    as a SIDE EFFECT of execute_discovery_phase, proving the cross-module wiring.
    """
    monkeypatch.setenv("ZEUS_LIVE_MAX_ONE_ENTRY_PER_WEATHER_FAMILY", "1")

    decisions = _three_family_decisions()
    deps, captured, artifact, portfolio, summary = _build_harness(decisions)

    decision_time = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)

    with caplog.at_level(logging.INFO, logger="src.strategy.family_exclusive_dedup"):
        execute_discovery_phase(
            conn=None,
            clob=MagicMock(),
            portfolio=portfolio,
            artifact=artifact,
            tracker=MagicMock(),
            limits=MagicMock(),
            mode=DiscoveryMode.UPDATE_REACTION,
            summary=summary,
            entry_bankroll=5000.0,
            decision_time=decision_time,
            env="paper",
            deps=deps,
        )

    # evaluate_candidate was called → decisions were captured.
    assert len(captured) >= 1, "evaluate_candidate was not called by execute_discovery_phase"
    post_dedup = captured[0]

    # ── Core cross-module invariant ───────────────────────────────────────────
    survivors = [d for d in post_dedup if d.should_trade]
    dropped = [d for d in post_dedup if not d.should_trade]

    assert len(survivors) == 1, (
        f"gate must leave exactly 1 should_trade=True entry for exclusive family; "
        f"got {len(survivors)}. "
        "If 3 survive, dedup_mutually_exclusive_families is NOT being called "
        "from within execute_discovery_phase (wiring broken)."
    )
    assert len(dropped) == 2, f"expected 2 dropped decisions; got {len(dropped)}"

    # ── Single-best selection: $20 bin (22-23°F) must win ────────────────────
    kept = survivors[0]
    kept_label = kept.edge.bin.label if kept.edge else None
    assert kept.edge is not None and kept.edge.bin.label == "22-23°F", (
        f"dedup must keep the highest size_usd bin (22-23°F, $20); "
        f"kept {kept_label!r}"
    )

    # ── STAGE A audit trail on dropped decisions ──────────────────────────────
    for d in dropped:
        bin_label = d.edge.bin.label if d.edge else "?"
        assert d.rejection_stage == "MUTUALLY_EXCLUSIVE_FAMILY", (
            f"dropped bin {bin_label!r}: "
            f"rejection_stage expected 'MUTUALLY_EXCLUSIVE_FAMILY', got {d.rejection_stage!r}"
        )
        assert MUTUALLY_EXCLUSIVE_FAMILY_DEDUP in (d.rejection_reasons or []), (
            f"dropped bin {bin_label!r} must list "
            f"{MUTUALLY_EXCLUSIVE_FAMILY_DEDUP!r} in rejection_reasons"
        )
        assert d.rejection_reason_detail and "kept_bin='22-23°F'" in d.rejection_reason_detail, (
            "rejection_reason_detail must name the kept bin for auditability"
        )
        # STAGE A: enum MUST stay None (no schema-derived CHECK touched).
        assert d.rejection_reason_enum is None, (
            "STAGE A must not set rejection_reason_enum on dedup-dropped decisions "
            "(no NoTradeReason enum member exists yet; Stage B adds it + migration)"
        )

    # ── Log line emitted by dedup (proves the module path was traversed) ──────
    dedup_log_lines = [
        r.getMessage() for r in caplog.records
        if "MUTUALLY_EXCLUSIVE_FAMILY_DEDUP" in r.getMessage()
    ]
    assert len(dedup_log_lines) >= 1, (
        "Expected at least one MUTUALLY_EXCLUSIVE_FAMILY_DEDUP log line from "
        "src.strategy.family_exclusive_dedup — the gate may not have fired "
        "or the import path is broken"
    )


def test_gate_disabled_all_three_decisions_reach_execution_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RELATIONSHIP test: when gate is OFF all 3 decisions flow through dedup
    unchanged — execute_discovery_phase does NOT suppress any family members.

    This is the RED baseline: what the pipeline produces WITHOUT the P0-1 gate.
    Together with the gate-ON test above, it proves the gate flag is the
    exact cause of the 3→1 reduction — not a fixture artifact.
    """
    monkeypatch.setenv("ZEUS_LIVE_MAX_ONE_ENTRY_PER_WEATHER_FAMILY", "0")

    decisions = _three_family_decisions()
    deps, captured, artifact, portfolio, summary = _build_harness(decisions)

    decision_time = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)

    execute_discovery_phase(
        conn=None,
        clob=MagicMock(),
        portfolio=portfolio,
        artifact=artifact,
        tracker=MagicMock(),
        limits=MagicMock(),
        mode=DiscoveryMode.UPDATE_REACTION,
        summary=summary,
        entry_bankroll=5000.0,
        decision_time=decision_time,
        env="paper",
        deps=deps,
    )

    assert len(captured) >= 1, "evaluate_candidate was not called"
    post_dedup = captured[0]

    survivors = [d for d in post_dedup if d.should_trade]
    assert len(survivors) == 3, (
        f"gate OFF must leave all 3 decisions with should_trade=True; "
        f"got {len(survivors)}. "
        "If only 1 survives, gate is ignoring the env flag (wiring broken)."
    )
    # None of the decisions should carry the dedup reason when gate is off.
    assert all(
        MUTUALLY_EXCLUSIVE_FAMILY_DEDUP not in (d.rejection_reasons or [])
        for d in post_dedup
    ), "gate-disabled path must not stamp any dedup rejection reason"


def test_structural_sentinel_dedup_called_from_cycle_runtime() -> None:
    """STRUCTURAL sentinel: dedup_mutually_exclusive_families is imported and
    called inside execute_discovery_phase's source, not just in tests.

    Grep-anchors the cross-module wiring at source level. If the import or
    call is removed from cycle_runtime.py, this sentinel fails immediately
    without waiting for an end-to-end test run.

    Sed-flip verifiable: comment out the import line in cycle_runtime.py
    → ``dedup_mutually_exclusive_families`` disappears from source → test fails.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "engine"
        / "cycle_runtime.py"
    )
    text = src.read_text()

    # Import inside execute_discovery_phase (lazy import pattern used in runtime).
    assert "from src.strategy.family_exclusive_dedup import" in text, (
        "SENTINEL FAIL: cycle_runtime.py no longer imports family_exclusive_dedup. "
        "The P0-1 dedup hook has been removed from execute_discovery_phase."
    )
    assert "dedup_mutually_exclusive_families(" in text, (
        "SENTINEL FAIL: dedup_mutually_exclusive_families call not found in "
        "cycle_runtime.py — the cross-module hook has been deleted."
    )

    # Tighten kwarg checks: find the actual dedup call block (3-line span starting
    # at "decisions = dedup_mutually_exclusive_families(") and verify the required
    # family-key kwargs appear in the ~10-line window around that call.
    # This avoids false positives from unrelated uses of "city=", "target_date=",
    # "temperature_metric=" elsewhere in cycle_runtime.py.
    call_marker = "decisions = dedup_mutually_exclusive_families("
    call_idx = text.find(call_marker)
    assert call_idx != -1, (
        "SENTINEL FAIL: 'decisions = dedup_mutually_exclusive_families(' assignment "
        "not found in cycle_runtime.py — the call site may have been refactored."
    )
    # Slice the 400 chars after the call open-paren — covers the kwargs block.
    call_window = text[call_idx : call_idx + 400]
    for kwarg in ("city=city", "target_date=candidate", "temperature_metric=candidate"):
        assert kwarg in call_window, (
            f"SENTINEL FAIL: expected kwarg pattern {kwarg!r} not found in the "
            "dedup_mutually_exclusive_families call window in cycle_runtime.py. "
            "The call signature may have changed or the kwargs were renamed."
        )
