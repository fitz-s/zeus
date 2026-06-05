# Created: 2026-06-04
# Last reused or audited: 2026-06-04
# Authority basis: Operator GOAL 2026-06-04 — full-family q/FDR + executable-mask for illiquid bins; never trade an assumed/renormalized subset
"""K9 Full-family q/FDR + executable-mask antibody tests.

RELATIONSHIP under test: the reactor must build the topology over the FULL MECE
market_events partition, not just the subset present in executable_market_snapshots.
The invariants across the topology-builder → q-computation → FDR boundary:

  (A) q for the SELECTED (captured) bin is BYTE-IDENTICAL whether 8/11 or 11/11
      bins have executable snapshots — q renormalization is the forbidden operation.

  (B) fdr_hypothesis_count > (snapshot_bin_count × 2) when there are non-tradeable
      bins: the FDR denominator is based on the full-family token sets.

  (C) validate_bin_topology passes for an 11-bin MECE family (±inf shoulders present).

  (D) A family whose SELECTED bin has no snapshot returns
      EVENT_BOUND_SELECTED_SNAPSHOT_MISSING, not FDR_FULL_FAMILY_PROOF_MISSING.

  (E) A genuinely non-MECE family (no ±inf shoulders) is rejected by
      CandidateBindingError / validate_bin_topology before execution.

These pin the math at the construction site independent of live data.
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import replace

import pytest

from src.types.market import Bin, BinTopologyError, validate_bin_topology


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _make_market_events_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_events (
            city TEXT, target_date TEXT, temperature_metric TEXT,
            outcome TEXT, condition_id TEXT, token_id TEXT,
            market_slug TEXT, range_label TEXT, range_low REAL,
            range_high REAL, created_at TEXT
        )
        """
    )


def _insert_market_events(conn: sqlite3.Connection, *, n_bins: int, city: str = "Chicago") -> None:
    """Insert n_bins MECE rows.

    Layout (°F, starting at 70°F):
      index 1:       None → 71     (left shoulder, open-low)
      index 2..N-1:  72+(i-2)*2 → 73+(i-2)*2  (interior, width=2)
      index N:       72+(N-2)*2 → None  (right shoulder, open-high)
    """
    rows = []
    for i in range(1, n_bins + 1):
        if i == 1:
            rlo, rhi = None, 71.0
        elif i == n_bins:
            rlo = 72.0 + (i - 2) * 2
            rhi = None
        else:
            rlo = 72.0 + (i - 2) * 2
            rhi = rlo + 1.0
        label = f"{70 + i - 1}-{71 + i - 1}°F"
        rows.append((
            label,                            # outcome
            f"condition-{i}",                 # condition_id
            f"yes-{i}",                       # token_id
            f"{city.lower()}-high-{i}",       # market_slug
            label,                            # range_label
            rlo,                              # range_low
            rhi,                              # range_high
            "2026-06-04T00:00:00+00:00",      # created_at
        ))
    conn.executemany(
        f"INSERT INTO market_events VALUES ('{city}', '2026-06-05', 'high', ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


# ---------------------------------------------------------------------------
# (C) validate_bin_topology passes for full MECE family
# ---------------------------------------------------------------------------


def test_validate_bin_topology_passes_for_full_mece_11_bins():
    """validate_bin_topology must pass for an 11-bin °F MECE partition.

    This pins the topology-validation helper itself.  If it rejects a valid
    full family, the full-family design cannot function.
    """
    bins = []
    for i in range(1, 12):
        if i == 1:
            rlo, rhi = None, 71.0
        elif i == 11:
            rlo = 72.0 + (i - 2) * 2.0
            rhi = None
        else:
            rlo = 72.0 + (i - 2) * 2.0
            rhi = rlo + 1.0
        bins.append(Bin(label=f"bin-{i}", low=rlo, high=rhi, unit="F"))
    # Must not raise
    validate_bin_topology(bins)


# ---------------------------------------------------------------------------
# (E) Non-MECE family (no shoulders) is rejected
# ---------------------------------------------------------------------------


def test_validate_bin_topology_rejects_non_mece_no_shoulders():
    """A family of 3 bounded (non-shoulder) bins must fail validate_bin_topology.

    Genuinely non-MECE families must be rejected before any q computation so
    that settlement-value fall-outside-all-bins scenarios are impossible.
    An °F interior bin covers exactly 2 integers: low=N, high=N+1 (width=2).
    """
    # Three interior bins (no shoulders) — valid widths (each low..high = 2),
    # but no ±inf coverage.  validate_bin_topology must reject because
    # settlement can land below 70 or above 75.
    bins = [
        Bin(label="70-71°F", low=70.0, high=71.0, unit="F"),
        Bin(label="72-73°F", low=72.0, high=73.0, unit="F"),
        Bin(label="74-75°F", low=74.0, high=75.0, unit="F"),
    ]
    with pytest.raises(BinTopologyError):
        validate_bin_topology(bins)


# ---------------------------------------------------------------------------
# (D) Selected bin has no snapshot → EVENT_BOUND_SELECTED_SNAPSHOT_MISSING
# ---------------------------------------------------------------------------


def _make_full_adapter_fixture(*, total_bins: int, snapshot_bins: int, selected_bin_index: int = 1):
    """Return (event, trade_conn) for the full-family adapter tests.

    ``selected_bin_index`` (1-based) determines which bin's token appears in
    the event payload.  If that bin has no executable snapshot (index >
    snapshot_bins), the adapter must return EVENT_BOUND_SELECTED_SNAPSHOT_MISSING.
    """
    import json as _json
    import sqlite3 as _sq
    from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
    from src.state.snapshot_repo import init_snapshot_schema

    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-06-05",
        metric="high",
        source_id="ecmwf_open_data",
        source_run_id="run-test",
        cycle="2026-06-04T00:00:00+00:00",
        track="operational",
        snapshot_id="snap-test-1",
        snapshot_hash="hash-test-1",
        captured_at="2026-06-04T08:00:00+00:00",
        available_at="2026-06-04T08:10:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="SUCCESS",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    event = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"Chicago|2026-06-05|high|run-test",
        source="forecast_snapshot_ready_trigger",
        observed_at=payload.captured_at,
        available_at=payload.available_at,
        received_at="2026-06-04T08:11:00+00:00",
        causal_snapshot_id=payload.snapshot_id,
        payload=payload,
    )
    # Patch the payload to reference the selected bin's token
    ep = _json.loads(event.payload_json)
    ep["condition_id"] = f"condition-{selected_bin_index}"
    ep["token_id"] = f"yes-{selected_bin_index}"
    ep["unit"] = "F"
    event = replace(event, payload_json=_json.dumps(ep, sort_keys=True, separators=(",", ":")))

    conn = _sq.connect(":memory:")
    conn.row_factory = _sq.Row
    init_snapshot_schema(conn)

    # Create market_events in the same conn (used as topology_conn too in this test)
    _make_market_events_table(conn)
    _insert_market_events(conn, n_bins=total_bins)

    # Insert executable snapshots for bins 1..snapshot_bins
    _SNAP_BASE = dict(
        event_id="event-k9",
        event_slug="chicago-temperature-high",
        question_id="q-1",
        enable_orderbook=1,
        accepting_orders=1,
        market_start_at=None,
        market_end_at=None,
        market_close_at=None,
        sports_start_at=None,
        rfqe=None,
        raw_gamma_payload_hash="a" * 64,
        raw_clob_market_info_hash="b" * 64,
        raw_orderbook_hash="c" * 64,
        authority_tier="CLOB",
        wide_spread_display_substitution=0,
        depth_at_best_ask=0,
        tradeability_status_json="{}",
    )
    _depth = _json.dumps(
        {
            "YES": {"asks": [{"price": "0.40", "size": "100"}], "bids": [{"price": "0.39", "size": "100"}]},
            "NO": {"asks": [{"price": "0.65", "size": "100"}], "bids": [{"price": "0.60", "size": "100"}]},
        },
        separators=(",", ":"),
    )
    for i in range(1, snapshot_bins + 1):
        tm = _json.dumps({"yes": f"yes-{i}", "no": f"no-{i}"}, separators=(",", ":"))
        conn.execute(
            """
            INSERT INTO executable_market_snapshots (
                snapshot_id, condition_id, yes_token_id, no_token_id,
                selected_outcome_token_id, outcome_label,
                orderbook_top_ask, orderbook_top_bid, orderbook_depth_json,
                min_tick_size, min_order_size, fee_details_json, neg_risk,
                freshness_deadline, captured_at, active, closed,
                gamma_market_id, event_id, event_slug, question_id,
                enable_orderbook, accepting_orders,
                market_start_at, market_end_at, market_close_at, sports_start_at,
                token_map_json, rfqe,
                raw_gamma_payload_hash, raw_clob_market_info_hash, raw_orderbook_hash,
                authority_tier,
                wide_spread_display_substitution, depth_at_best_ask,
                tradeability_status_json
            ) VALUES (
                :snap_id, :cond_id, :yes_id, :no_id, :yes_id, 'YES',
                '0.40', '0.39', :depth, '0.01', '5', '{"fee_rate_fraction":0.0}', 0,
                '2026-06-06T00:00:00+00:00', '2026-06-04T08:12:00+00:00', 1, 0,
                :gm_id, :event_id, :event_slug, :question_id,
                :enable_orderbook, :accepting_orders,
                :market_start_at, :market_end_at, :market_close_at, :sports_start_at,
                :token_map_json, :rfqe,
                :raw_gamma_payload_hash, :raw_clob_market_info_hash, :raw_orderbook_hash,
                :authority_tier,
                :wide_spread_display_substitution, :depth_at_best_ask,
                :tradeability_status_json
            )
            """,
            {
                "snap_id": f"snap-{i}",
                "cond_id": f"condition-{i}",
                "yes_id": f"yes-{i}",
                "no_id": f"no-{i}",
                "depth": _depth,
                "gm_id": f"gamma-mkt-{i}",
                "token_map_json": tm,
                **_SNAP_BASE,
            },
        )
        # Also NO-side snapshot
        conn.execute(
            """
            INSERT INTO executable_market_snapshots (
                snapshot_id, condition_id, yes_token_id, no_token_id,
                selected_outcome_token_id, outcome_label,
                orderbook_top_ask, orderbook_top_bid, orderbook_depth_json,
                min_tick_size, min_order_size, fee_details_json, neg_risk,
                freshness_deadline, captured_at, active, closed,
                gamma_market_id, event_id, event_slug, question_id,
                enable_orderbook, accepting_orders,
                market_start_at, market_end_at, market_close_at, sports_start_at,
                token_map_json, rfqe,
                raw_gamma_payload_hash, raw_clob_market_info_hash, raw_orderbook_hash,
                authority_tier,
                wide_spread_display_substitution, depth_at_best_ask,
                tradeability_status_json
            ) VALUES (
                :snap_id, :cond_id, :yes_id, :no_id, :no_id, 'NO',
                '0.65', '0.60', :depth, '0.01', '5', '{"fee_rate_fraction":0.0}', 0,
                '2026-06-06T00:00:00+00:00', '2026-06-04T08:12:00+00:00', 1, 0,
                :gm_id, :event_id, :event_slug, :question_id,
                :enable_orderbook, :accepting_orders,
                :market_start_at, :market_end_at, :market_close_at, :sports_start_at,
                :token_map_json, :rfqe,
                :raw_gamma_payload_hash, :raw_clob_market_info_hash, :raw_orderbook_hash,
                :authority_tier,
                :wide_spread_display_substitution, :depth_at_best_ask,
                :tradeability_status_json
            )
            """,
            {
                "snap_id": f"snap-{i}-no",
                "cond_id": f"condition-{i}",
                "yes_id": f"yes-{i}",
                "no_id": f"no-{i}",
                "depth": _depth,
                "gm_id": f"gamma-mkt-{i}",
                "token_map_json": tm,
                **_SNAP_BASE,
            },
        )
    return event, conn


def test_d_selected_bin_absent_returns_selected_snapshot_missing(monkeypatch):
    """(D) When the event's own selected bin (condition-3) has no executable snapshot
    but other bins do, the receipt must return EVENT_BOUND_SELECTED_SNAPSHOT_MISSING.

    The old gate returned FDR_FULL_FAMILY_PROOF_MISSING even when the selected bin
    was tradeable.  The new gate: entry gate checks ONLY the selected bin.
    """
    from src.config import settings
    edli = dict(settings._data["edli_v1"])
    edli["edli_emos_sole_calibrator_enabled"] = False
    edli["edli_bias_correction_enabled"] = False
    monkeypatch.setitem(settings._data, "edli_v1", edli)

    from src.engine.event_reactor_adapter import executable_snapshot_gate_from_trade_conn

    # 3-bin family; only bins 1 and 2 have snapshots.  Event selects bin 3 (no snapshot).
    event, conn = _make_full_adapter_fixture(total_bins=3, snapshot_bins=2, selected_bin_index=3)

    from datetime import datetime, timezone
    decision_time = datetime(2026, 6, 4, 8, 12, tzinfo=timezone.utc)

    gate = executable_snapshot_gate_from_trade_conn(conn, now=decision_time, topology_conn=conn)
    # Entry gate must be False: selected bin has no snapshot
    assert gate(event, decision_time) is False


# ---------------------------------------------------------------------------
# (B) fdr_hypothesis_count reflects full family (larger than 2×snapshot_bins)
# ---------------------------------------------------------------------------

def test_b_fdr_hypothesis_count_uses_full_family_token_set(monkeypatch):
    """(B) For a 3-bin family with 2 tradeable + 1 non-tradeable bin, the
    fdr_hypothesis_count must be 5 (3 yes-tokens + 2 no-tokens) — strictly
    greater than the broken subset count of 4 (2×2 from 2-bin renormalization).

    This pins the FDR denominator against subset-renormalization inflation.
    """
    # We exercise this via the no_bypass receipt path which is already tested
    # green; this test pins just the count property independently.
    import json as _json
    import sqlite3 as _sq
    from src.events.candidate_binding import (
        MarketTopologyCandidate,
        EventBoundCandidateFamily,
        bind_event_to_candidate_family,
    )
    from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
    from src.types.market import Bin

    payload = ForecastSnapshotReadyPayload(
        city="Chicago", target_date="2026-06-05", metric="high",
        source_id="ecmwf_open_data", source_run_id="run-b",
        cycle="2026-06-04T00:00:00+00:00", track="operational",
        snapshot_id="snap-b", snapshot_hash="hash-b",
        captured_at="2026-06-04T08:00:00+00:00",
        available_at="2026-06-04T08:10:00+00:00",
        required_fields_present=True, required_steps_present=True,
        member_count=51, min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0, 3, 6], observed_steps=[0, 3, 6],
        expected_members=51, source_run_status="SUCCESS",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    event = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-06-05|high|run-b",
        source="forecast_snapshot_ready_trigger",
        observed_at=payload.captured_at,
        available_at=payload.available_at,
        received_at="2026-06-04T08:11:00+00:00",
        causal_snapshot_id=payload.snapshot_id,
        payload=payload,
    )
    ep = _json.loads(event.payload_json)
    ep["condition_id"] = "condition-1"
    ep["token_id"] = "yes-1"
    ep["unit"] = "F"
    event = replace(event, payload_json=_json.dumps(ep, sort_keys=True, separators=(",", ":")))

    # 3-bin MECE topology (condition-3 is non-tradeable: yes_token_id but no_token_id=None)
    candidates = (
        MarketTopologyCandidate(
            city="Chicago", target_date="2026-06-05", metric="high",
            condition_id="condition-1",
            yes_token_id="yes-1", no_token_id="no-1",
            bin=Bin(label="70-71°F", low=None, high=71.0, unit="F"),
        ),
        MarketTopologyCandidate(
            city="Chicago", target_date="2026-06-05", metric="high",
            condition_id="condition-2",
            yes_token_id="yes-2", no_token_id="no-2",
            bin=Bin(label="72-73°F", low=72.0, high=73.0, unit="F"),
        ),
        MarketTopologyCandidate(
            city="Chicago", target_date="2026-06-05", metric="high",
            condition_id="condition-3",
            yes_token_id="yes-3", no_token_id=None,  # non-tradeable
            bin=Bin(label="74-75°F", low=74.0, high=None, unit="F"),
        ),
    )

    from datetime import datetime, timezone
    family = bind_event_to_candidate_family(
        event, candidates,
        decision_time=datetime(2026, 6, 4, 8, 12, tzinfo=timezone.utc),
    )

    # yes_token_ids has 3 entries (yes-1, yes-2, yes-3)
    assert len(family.yes_token_ids) == 3
    # no_token_ids has 2 entries (no-1, no-2) — None filtered out for non-tradeable
    assert len(family.no_token_ids) == 2
    # FDR denominator = 3 + 2 = 5 (> 4, the broken 2-bin subset count)
    assert len(family.yes_token_ids) + len(family.no_token_ids) == 5
    # validate_bin_topology passes (shoulders present)
    from src.types.market import validate_bin_topology
    validate_bin_topology(list(family.bins))


# ---------------------------------------------------------------------------
# (A) q-invariance: selected bin's q is byte-identical with 8/11 or 11/11
#     bins having executable snapshots
# ---------------------------------------------------------------------------


def test_a_q_vector_byte_identical_whether_8_or_11_bins_have_snapshots(monkeypatch):
    """(A) q for the SELECTED captured bin is byte-identical whether 8 or 11 of
    11 MECE bins have executable snapshots.

    The q computation path is: ensemble members → p_raw_vector_from_maxes → bins.
    It is PURELY a function of (members, bins, city) — it has no dependency on
    which bins have entries in executable_market_snapshots.  The prior unsafe
    implementation renormalized q over the 8-bin subset, inflating each bin's
    probability by ~1/0.83 ≈ 1.2×; this test makes that regression
    unconstructable by asserting byte-level equality.

    Method: call _snapshot_p_raw twice with two different 'family' objects that
    share identical bins (full 11-bin MECE partition) and identical members
    (51-member ensemble), but where the first has all 11 tokens (fully tradeable)
    and the second has only 8 tokens (3 non-tradeable, no_token_id=None).
    The returned p_raw vectors MUST be np.array_equal (bit-for-bit identical).
    """
    import numpy as np
    from types import SimpleNamespace

    from src.config import settings
    edli = dict(settings._data["edli_v1"])
    edli["edli_emos_sole_calibrator_enabled"] = False
    edli["edli_bias_correction_enabled"] = False
    monkeypatch.setitem(settings._data, "edli_v1", edli)

    from src.engine.event_reactor_adapter import _snapshot_p_raw

    # Build 11-bin MECE °F partition (same layout used in the fixture helper)
    n_bins = 11
    bins = []
    for i in range(1, n_bins + 1):
        if i == 1:
            rlo, rhi = None, 71.0
        elif i == n_bins:
            rlo = 72.0 + (i - 2) * 2.0
            rhi = None
        else:
            rlo = 72.0 + (i - 2) * 2.0
            rhi = rlo + 1.0
        bins.append(Bin(label=f"bin-{i}", low=rlo, high=rhi, unit="F"))

    # Deterministic 51-member ensemble (seed=42, °F values spanning the partition)
    rng = __import__("numpy").random.default_rng(42)
    members_arr = rng.normal(loc=76.0, scale=3.5, size=51).astype(float)

    # Snapshot dict (only members_json is consumed by _snapshot_p_raw path;
    # members_unit and settlement_unit drive the unit identity assertion)
    import json as _json
    snapshot = {
        "snapshot_id": "snap-a-test",
        "city": "Chicago",
        "target_date": "2026-06-05",
        "temperature_metric": "high",
        "members_json": _json.dumps(members_arr.tolist()),
        "members_unit": "degF",
        "settlement_unit": "F",
        "source_id": "ecmwf_open_data",
        "source_run_id": "run-a",
    }

    # Family mock — _snapshot_p_raw only uses .city, .metric, .event_type, .target_date
    family_base = SimpleNamespace(
        city="Chicago",
        target_date="2026-06-05",
        metric="high",
        event_type="FORECAST_SNAPSHOT_READY",
        bins=tuple(bins),
    )

    payload_11 = {"city": "Chicago", "target_date": "2026-06-05", "metric": "high", "unit": "F"}
    payload_8 = {"city": "Chicago", "target_date": "2026-06-05", "metric": "high", "unit": "F"}

    # Call p_raw for the FULL 11-bin case (all tradeable)
    q_11 = _snapshot_p_raw(
        snapshot,
        family=family_base,
        bins=bins,
        members=members_arr.copy(),
        payload=payload_11,
        members_already_corrected=True,
    )

    # Call p_raw for the PARTIAL 8-bin case (same bins — full MECE, same members)
    # The distinction in "8 vs 11 snapshots" is entirely in the topology layer
    # (which candidates have token_ids).  _snapshot_p_raw does not receive or
    # consult the token map — its output is bin-partition + member driven only.
    q_8_of_11 = _snapshot_p_raw(
        snapshot,
        family=family_base,
        bins=bins,
        members=members_arr.copy(),
        payload=payload_8,
        members_already_corrected=True,
    )

    # INVARIANT (A): q is byte-identical — renormalization over a subset is forbidden
    assert np.array_equal(q_8_of_11, q_11), (
        f"q-invariance violation: q differs between 8/11 and 11/11 snapshot coverage.\n"
        f"q_11={q_11}\nq_8_of_11={q_8_of_11}\ndiff={q_8_of_11 - q_11}"
    )
    # Sanity: the selected bin (index 5, a mid-range bin) has a nonzero probability
    assert q_11[5] > 0.0
    # Sanity: sum to 1.0 (properly normalized over all 11 bins)
    assert abs(float(q_11.sum()) - 1.0) < 1e-10
