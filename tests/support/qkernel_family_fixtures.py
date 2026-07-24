# Created: 2026-07-23
# Last reused/audited: 2026-07-23
# Authority basis: current global-auction integration fixtures
"""Non-production family, proof, and payload fixtures for auction tests."""

from __future__ import annotations

import json
from src.engine import event_reactor_adapter as era
from src.events.candidate_binding import (
    EventBoundCandidateFamily,
    MarketTopologyCandidate,
)
from src.types.market import Bin

CITY = "Paris"  # a real registered C-unit, wmo_half_up settlement city
TARGET_DATE = "2026-06-14"
METRIC = "high"
def _row(*, condition_id, yes_token, no_token, yes_ask, no_ask, snapshot_id):
    depth = {
        "YES": {
            "asks": [{"price": f"{yes_ask:.2f}", "size": "100000"}],
            "bids": [{"price": f"{max(yes_ask - 0.01, 0.01):.2f}", "size": "100"}],
        },
        "NO": {
            "asks": [{"price": f"{no_ask:.2f}", "size": "100000"}],
            "bids": [{"price": f"{max(no_ask - 0.01, 0.01):.2f}", "size": "100"}],
        },
    }
    return {
        "snapshot_id": snapshot_id,
        "condition_id": condition_id,
        "yes_token_id": yes_token,
        "no_token_id": no_token,
        "selected_outcome_token_id": "",
        "outcome_label": "",
        "min_tick_size": "0.01",
        "min_order_size": "5",
        "fee_details_json": json.dumps({"fee_rate_fraction": 0.0}),
        "neg_risk": 0,
        "orderbook_depth_json": json.dumps(depth),
        "tradeability_status_json": "{}",
        "book_hash": f"book-{snapshot_id}",
        "orderbook_top_bid": str(max(yes_ask - 0.01, 0.01)),
    }


def _candidate(*, condition_id, yes_token, no_token, bin_obj):
    return MarketTopologyCandidate(
        city=CITY,
        target_date=TARGET_DATE,
        metric=METRIC,
        condition_id=condition_id,
        yes_token_id=yes_token,
        no_token_id=no_token,
        bin=bin_obj,
    )


def _proof(*, direction, row, token_id, q_posterior, q_lcb_5pct, bin_obj, trade_score=1.0):
    ep, _pfill, _c95 = era._execution_price_from_snapshot(
        row, selected_token_id=token_id, direction=direction
    )
    return era._CandidateProof(
        candidate=_candidate(
            condition_id=str(row.get("condition_id") or ""),
            yes_token=str(row.get("yes_token_id") or ""),
            no_token=str(row.get("no_token_id") or ""),
            bin_obj=bin_obj,
        ),
        token_id=token_id,
        direction=direction,
        row=row,
        executable_snapshot_id=str(row.get("snapshot_id") or ""),
        execution_price=ep,
        q_posterior=q_posterior,
        q_lcb_5pct=q_lcb_5pct,
        c_cost_95pct=None,
        p_fill_lcb=1.0,
        trade_score=trade_score,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="cal-hash",
        p_live_vector_hash="live-hash",
        missing_reason=None,
    )


def _family(bins):
    candidates = tuple(
        _candidate(
            condition_id=f"cond-{i}",
            yes_token=f"yes-{i}",
            no_token=f"no-{i}",
            bin_obj=b,
        )
        for i, b in enumerate(bins)
    )
    return EventBoundCandidateFamily(
        family_id="edli_family_smoke_w5b",
        event_id="evt-smoke-w5b",
        event_type="FORECAST_SNAPSHOT_READY",
        city=CITY,
        target_date=TARGET_DATE,
        metric=METRIC,
        condition_ids=tuple(c.condition_id for c in candidates),
        yes_token_ids=tuple(c.yes_token_id for c in candidates),
        no_token_ids=tuple(c.no_token_id for c in candidates),
        bins=tuple(bins),
        candidates=candidates,
        causal_snapshot_id="snap-smoke",
        market_topology_source="executable_market_snapshots",
        binding_hash="hash-smoke",
    )


# A complete MECE 3-bin C family around 20 C: [<=19], [20], [21], [>=22] shoulders.
# Point bins (low==high) on the 1-degree integer grid + open shoulders for completeness.
def _three_bin_family():
    bins = [
        Bin(low=None, high=19.0, unit="C", label="19C or below"),
        Bin(low=20.0, high=20.0, unit="C", label="20C"),
        Bin(low=21.0, high=21.0, unit="C", label="21C"),
        Bin(low=22.0, high=None, unit="C", label="22C or above"),
    ]
    return _family(bins), bins


def _proofs_for(family, *, yes_asks, no_asks, q_by_bin, q_lcb_by_bin):
    """Build buy_yes + buy_no proofs per candidate with the given prices/qs."""
    proofs = []
    for i, candidate in enumerate(family.candidates):
        row = _row(
            condition_id=candidate.condition_id,
            yes_token=candidate.yes_token_id,
            no_token=candidate.no_token_id,
            yes_ask=yes_asks[i],
            no_ask=no_asks[i],
            snapshot_id=f"snap-{i}",
        )
        q = q_by_bin[i]
        q_lcb = q_lcb_by_bin[i]
        proofs.append(
            _proof(
                direction="buy_yes",
                row=row,
                token_id=candidate.yes_token_id,
                q_posterior=q,
                q_lcb_5pct=q_lcb,
                bin_obj=candidate.bin,
            )
        )
        proofs.append(
            _proof(
                direction="buy_no",
                row=row,
                token_id=candidate.no_token_id,
                q_posterior=float(min(max(1.0 - q, 0.0), 1.0)),
                q_lcb_5pct=float(min(max(1.0 - q, 0.0), 1.0)) * 0.9,
                bin_obj=candidate.bin,
            )
        )
    return proofs


# Forecast source cycle that lands the (Paris, 2026-06-14, high) case in the 24h lead
# bucket (the replay-validated bucket): cycle 2026-06-13T00:00Z -> finalization
# 2026-06-14T10:00Z (Paris noon local) = 34h -> "24h" bucket.
SOURCE_CYCLE_TIME_UTC = "2026-06-13T00:00:00Z"


def _payload_with_spine_inputs(*, mu, sigma, members):
    return {
        "family_id": "edli_family_smoke_w5b",
        "event_id": "evt-smoke-w5b",
        "_edli_spine_mu_native": float(mu),
        "_edli_spine_sigma_native": float(sigma),
        "_edli_spine_debiased_members_native": [float(x) for x in members],
        "_edli_spine_raw_members_native": [float(x) for x in members],
        "_edli_spine_source_cycle_time_utc": SOURCE_CYCLE_TIME_UTC,
    }
