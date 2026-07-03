# Created: 2026-06-14
# Last reused or audited: 2026-06-14 (loop-back: added end-to-end producer-stash tests that
#   drive the real q-build, after critic found NOTHING in src/ wrote the _edli_spine_* keys)
# Authority basis: docs/rebuild/consult_build_spec.md Stage 0 (lines 994-1033;
#   RED-on-revert name :1030 test_candidate_receipt_reconstructs_forecast_q_route_and_size;
#   producer-stash RED-on-revert test_live_qbuild_writes_spine_keys_onto_payload_end_to_end;
#   one-invariant :5-12) + spec_vs_live_drift_ledger.md (src/decision/ is net-new; schema is
#   src/state/schema/no_trade_events_schema.py)

"""Stage-0 RED-on-revert contract: the decision receipt reconstructs the live decision.

The one rebuild invariant (consult_build_spec.md:5-12) requires that for every live candidate
the receipt PROVES forecast -> q -> route -> size. Stage 0 proves it to the extent the CURRENT
live path provides those legs (mu/sigma/member envelope/q_source/route).

CORRECTED-TRANSFORMATION (operator law — no detector/gate/clamp): the receipt's coherence is
made *unconstructable-when-wrong*, not flagged after the fact. The reconstruction reads the
envelope and q_sum that ``DecisionReceipt.from_q_build`` DERIVED from the same arrays the
q-build integrated, so:
  - member_min_native <= member_max_native is true by construction;
  - q_sum equals Sigma of the q vector the receipt was built from.

Each test below FAILS if the corrected transform is reverted to the broken behavior the spec
replaces — a receipt that drops the member envelope (so a center leaving consensus is
invisible) or whose q_sum is a free field disagreeing with the q vector (the raw-per-bin
percentile incoherence of the old _build_fused_q_bounds, lifted into the receipt).
"""

from __future__ import annotations

import math

import pytest

from src.decision.decision_receipt import (
    DecisionReceipt,
    ForecastSpine,
    QSpine,
    RECEIPT_SPINE_COLUMNS,
    RouteSpine,
    SizeSpine,
)


# Fresh model consensus is 20..23 (the spec's Tokyo example: members 20-23, μ must not select
# 26). The live q-build integrates the predictive distribution N(mu, sigma) over the bins.
_RAW_MEMBERS = [20.0, 21.0, 22.0, 23.0]
_MU_NATIVE = 21.5
_SIGMA_NATIVE = 1.4
# A normalized joint q over five bins (Sigma q = 1.0): what the live calibrated point
# distribution p_cal yields.
_Q_VECTOR = [0.05, 0.20, 0.45, 0.25, 0.05]


def _live_q_build_receipt() -> DecisionReceipt:
    """The receipt the live path would emit for a forecast candidate (EMOS lane)."""
    return DecisionReceipt.from_q_build(
        q_source="emos",
        q_vector=_Q_VECTOR,
        mu_native=_MU_NATIVE,
        sigma_native=_SIGMA_NATIVE,
        raw_members_native=_RAW_MEMBERS,
        debiased_members_native=_RAW_MEMBERS,  # EMOS lane: no shift, debiased == raw
        rounding_rule="wmo_half_up",
        sizing_authority="BH_FDR",
    )


def test_candidate_receipt_reconstructs_forecast_q_route_and_size() -> None:
    """The spec-named RED-on-revert test (consult_build_spec.md:1030).

    A live-built receipt reconstructs forecast (mu/sigma/member envelope), q (source/sum), and
    the route/size legs to the extent the current path provides them. The reconstruction is
    COHERENT by construction: the member envelope brackets the fresh consensus and q_sum is the
    true mass of the q vector. This is the corrected transformation — there is no detector that
    could be removed to let an incoherent receipt through.
    """
    receipt = _live_q_build_receipt()
    recon = receipt.reconstruct_forecast_q_route_and_size()

    # --- forecast leg: mu/sigma + the fresh member envelope the spec requires on every receipt
    forecast = recon["forecast"]
    assert forecast["mu_native"] == pytest.approx(_MU_NATIVE)
    assert forecast["sigma_native"] == pytest.approx(_SIGMA_NATIVE)
    # member_min/max are DERIVED from the same member array -> they bracket the consensus and
    # min <= max is structural. A reverted transform that dropped the envelope would return
    # None here and fail (the spec's "no receipt lacks member envelope" signal, :1033).
    assert forecast["member_min_native"] == pytest.approx(min(_RAW_MEMBERS))
    assert forecast["member_max_native"] == pytest.approx(max(_RAW_MEMBERS))
    assert forecast["member_min_native"] <= forecast["member_max_native"]
    # The Tokyo guard (spec:28/1086): a center of 26 cannot sit inside a 20..23 envelope. The
    # receipt makes that checkable because mu and the envelope travel together on one object.
    assert forecast["member_min_native"] <= forecast["mu_native"] <= forecast["member_max_native"]

    # --- q leg: source + DERIVED q_sum (the coherence the old raw-percentile path lacked)
    q = recon["q"]
    assert q["q_source"] == "emos"
    assert q["rounding_rule"] == "wmo_half_up"
    assert q["q_sum"] == pytest.approx(sum(_Q_VECTOR))
    assert q["q_sum"] == pytest.approx(1.0, abs=1e-9)

    # --- route + size legs: present to the extent the current path provides (sizing authority)
    assert recon["size"]["sizing_authority"] == "BH_FDR"

    # The Stage-0 live signal (spec:1033): no candidate receipt lacks mu/sigma/member
    # envelope/q_source. has_forecast_spine + has_q_spine make that queryable.
    assert receipt.has_forecast_spine() is True
    assert receipt.has_q_spine() is True
    assert receipt.envelope_is_coherent() is True


def test_q_sum_is_derived_from_the_q_vector_not_a_free_field() -> None:
    """Corrected transform: q_sum can ONLY be the mass of the q vector it was built from.

    The broken behavior this replaces is q_lcb / q being read off raw per-bin masses with no
    row-normalization (the old _build_fused_q_bounds: np.percentile(probs, 5, axis=0) over
    un-normalized draws — spec:41). If that incoherent vector were lifted into a receipt, its
    "q_sum" would not equal the modeled q. Here q_sum is DERIVED, so a receipt whose q_sum
    disagrees with its q vector is unconstructable through from_q_build.
    """
    coherent = DecisionReceipt.from_q_build(q_source="emos", q_vector=[0.1, 0.6, 0.3])
    assert coherent.q.q_sum == pytest.approx(1.0)

    # An UN-normalized draw (what the old raw-percentile path produced): q_sum reflects the
    # ACTUAL mass, never a fabricated 1.0. The receipt cannot claim normalization it does not
    # have — that is the detector-free guarantee.
    incoherent_input = DecisionReceipt.from_q_build(q_source="raw_honest", q_vector=[0.1, 0.6, 0.9])
    assert incoherent_input.q.q_sum == pytest.approx(1.6)
    assert not math.isclose(incoherent_input.q.q_sum, 1.0, abs_tol=1e-6)
    # envelope_is_coherent() reports the incoherence structurally (Sigma q != 1) — it is a
    # read-only observation derived from the same q_sum, NOT a gate that mutates the value.
    assert incoherent_input.envelope_is_coherent() is False


def test_member_envelope_min_le_max_is_unconstructable_otherwise() -> None:
    """The member envelope is min()/max() of one array, so min <= max is structural.

    A reverted transform that let a receipt assert a center outside the fresh consensus while
    reporting a coherent-looking envelope is impossible: the envelope is the literal extrema of
    the member array the q-build used, so it always brackets that array and min <= max always
    holds. The spec's μ*-envelope invariant (spec:1084-1089) is checkable on the receipt
    because the receipt cannot misrepresent its own envelope.
    """
    receipt = DecisionReceipt.from_q_build(
        q_source="emos",
        q_vector=_Q_VECTOR,
        mu_native=_MU_NATIVE,
        raw_members_native=[23.0, 20.0, 22.0, 21.0],  # unsorted on purpose
    )
    assert receipt.forecast.member_min_native == pytest.approx(20.0)
    assert receipt.forecast.member_max_native == pytest.approx(23.0)
    assert receipt.forecast.member_min_native <= receipt.forecast.member_max_native


def test_debiased_envelope_is_derived_so_applied_debias_is_consistent() -> None:
    """When a debias shift ran, the debiased envelope is the extrema of the debiased array.

    The applied shift and the debiased envelope therefore agree by construction: you cannot
    record a debiased envelope that does not match the array the shift produced (the spec's
    'stale/oversized de-bias can reach members' surface, spec:25 — Stage 0 makes the applied
    shift and the resulting envelope co-recorded so a later stage's DebiasAuthority is auditable
    against the receipt).
    """
    raw = [20.0, 21.0, 22.0, 23.0]
    debiased = [21.0, 22.0, 23.0, 24.0]  # +1.0 warm shift
    receipt = DecisionReceipt.from_q_build(
        q_source="bias_platt",
        q_vector=_Q_VECTOR,
        raw_members_native=raw,
        debiased_members_native=debiased,
        applied_debias_native=1.0,
    )
    assert receipt.forecast.debiased_member_min_native == pytest.approx(21.0)
    assert receipt.forecast.debiased_member_max_native == pytest.approx(24.0)
    # The recorded shift equals (debiased - raw): consistent with the recorded envelopes.
    assert receipt.forecast.applied_debias_native == pytest.approx(1.0)
    deb_mean = sum(debiased) / len(debiased)
    raw_mean = sum(raw) / len(raw)
    assert receipt.forecast.applied_debias_native == pytest.approx(deb_mean - raw_mean)


def test_spine_only_fields_are_none_until_their_stage_wires_them() -> None:
    """Stage 0 leaves the not-yet-computed spine fields as None (drift-ledger contract).

    predictive_distribution_id, q_band_basis, market_implied_q, route_id, payoff_vector_hash,
    edge_lcb, delta_u are owned by later stages. Stage 0 must carry them as None — not fabricate
    them — so the receipt honestly reflects which legs the current path can reconstruct.
    """
    receipt = _live_q_build_receipt()
    assert receipt.forecast.predictive_distribution_id is None
    assert receipt.q.q_band_basis is None
    assert receipt.q.market_implied_q is None
    assert receipt.route.route_id is None
    assert receipt.route.payoff_vector_hash is None
    assert receipt.route.edge_lcb is None
    assert receipt.route.delta_u is None


def test_to_row_carries_exactly_the_spec_columns_and_round_trips() -> None:
    """to_row()/from_row() preserve the spine; columns match the spec field vocabulary.

    The schema column list (no_trade_events_schema._RECEIPT_SPINE_COLUMN_DEFS) and the dataclass
    row are one vocabulary (consult_build_spec.md:1008-1027). A round trip must be lossless so a
    persisted receipt can be replayed.
    """
    from src.state.schema.no_trade_events_schema import _RECEIPT_SPINE_COLUMN_DEFS

    receipt = _live_q_build_receipt()
    row = receipt.to_row()
    assert tuple(row.keys()) == RECEIPT_SPINE_COLUMNS
    # Schema columns and dataclass columns are the SAME set, in the SAME order.
    assert tuple(name for name, _type in _RECEIPT_SPINE_COLUMN_DEFS) == RECEIPT_SPINE_COLUMNS

    rebuilt = DecisionReceipt.from_row(row)
    assert rebuilt.to_row() == row
    assert rebuilt.forecast.member_min_native == receipt.forecast.member_min_native
    assert rebuilt.q.q_sum == receipt.q.q_sum


def test_schema_table_has_all_spine_columns_nullable() -> None:
    """The 19/20 spec columns exist on no_trade_events and are NULLABLE (additive, never gate).

    Observability-only: every spine column must be NULL-capable so existing writers that omit
    them are unaffected. (spec_vs_live_drift_ledger.md: schema is the REAL
    src/state/schema/no_trade_events_schema.py, never src/events/...)
    """
    import sqlite3

    from src.state.schema.no_trade_events_schema import (
        _RECEIPT_SPINE_COLUMN_DEFS,
        ensure_table,
    )

    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    info = {row[1]: row for row in conn.execute("PRAGMA table_info(no_trade_events)").fetchall()}
    for name, _sql_type in _RECEIPT_SPINE_COLUMN_DEFS:
        assert name in info, f"spine column {name} missing from no_trade_events"
        # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk).
        notnull = info[name][3]
        assert notnull == 0, f"spine column {name} must be NULLABLE (observability-only)"


def test_ensure_table_additively_backfills_spine_on_pre_stage0_table() -> None:
    """A pre-Stage-0 no_trade_events table gains the spine columns in place (no data loss).

    The live world DB already has a no_trade_events table from before Stage 0; CREATE TABLE IF
    NOT EXISTS is a no-op there, so ensure_table must ALTER-add the spine columns. Pre-existing
    rows survive with NULL spine values.
    """
    import sqlite3

    from src.state.schema.no_trade_events_schema import (
        _RECEIPT_SPINE_COLUMN_DEFS,
        ensure_table,
    )

    conn = sqlite3.connect(":memory:")
    # Old table shape: no spine columns.
    conn.execute(
        """
        CREATE TABLE no_trade_events (
            market_slug TEXT NOT NULL, temperature_metric TEXT NOT NULL,
            target_date TEXT NOT NULL, observation_time TEXT NOT NULL,
            decision_seq INTEGER NOT NULL, reason TEXT NOT NULL,
            reason_detail TEXT, strategy_key TEXT, event_source TEXT, observed_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            schema_compatibility TEXT NOT NULL DEFAULT 'current',
            PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
        )
        """
    )
    conn.execute(
        "INSERT INTO no_trade_events VALUES "
        "('m','high','2026-06-14','12:00',1,'uncategorized',NULL,NULL,NULL,0,"
        "'2026-06-14T00:00:00Z',42,'current')"
    )
    ensure_table(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(no_trade_events)").fetchall()}
    for name, _t in _RECEIPT_SPINE_COLUMN_DEFS:
        assert name in cols
    # Pre-existing row preserved; spine values are NULL.
    row = conn.execute(
        "SELECT decision_seq, mu_native, q_source, q_sum FROM no_trade_events"
    ).fetchone()
    assert row == (1, None, None, None)


def test_live_emission_helper_reconstructs_from_threaded_payload_inputs() -> None:
    """The reactor's READ-ONLY emission helper reconstructs the spine from q-build inputs.

    _build_decision_receipt_spine consumes the dict the q-build lifted onto provenance_capture
    (decision_receipt_spine_inputs) plus the receipt's q_source / selection_authority, and emits
    a coherent DecisionReceipt — proving the live path is reconstructable forecast -> q -> route
    -> size. (consult_build_spec.md:996-998, 1032-1033.)
    """
    from src.engine.event_reactor_adapter import _build_decision_receipt_spine
    from src.events.reactor import EventSubmissionReceipt

    spine_inputs = {
        "_edli_spine_mu_native": _MU_NATIVE,
        "_edli_spine_sigma_native": _SIGMA_NATIVE,
        "_edli_spine_raw_members_native": list(_RAW_MEMBERS),
        "_edli_spine_debiased_members_native": None,  # EMOS lane: no shift
        "_edli_spine_q_vector": list(_Q_VECTOR),
        "_edli_q_source": "emos",
        "city": "Tokyo",
    }
    receipt = EventSubmissionReceipt(
        submitted=False,
        event_id="evt-1",
        q_source="emos",
        selection_authority="BH_FDR",
    )
    spine = _build_decision_receipt_spine(spine_inputs, receipt)
    assert spine is not None
    recon = spine.reconstruct_forecast_q_route_and_size()
    assert recon["forecast"]["mu_native"] == pytest.approx(_MU_NATIVE)
    assert recon["forecast"]["member_min_native"] == pytest.approx(min(_RAW_MEMBERS))
    assert recon["forecast"]["member_max_native"] == pytest.approx(max(_RAW_MEMBERS))
    assert recon["q"]["q_source"] == "emos"
    assert recon["q"]["q_sum"] == pytest.approx(sum(_Q_VECTOR))
    assert recon["size"]["sizing_authority"] == "BH_FDR"
    assert spine.has_forecast_spine() is True
    assert spine.has_q_spine() is True


def test_emission_helper_returns_none_when_no_q_build_ran() -> None:
    """Gate-reject receipts (no q integration) have no forecast spine to emit.

    When the q-build never stashed spine inputs (an early gate reject), the helper returns None
    rather than fabricating a spine — honest absence, not invented data.
    """
    from src.engine.event_reactor_adapter import _build_decision_receipt_spine
    from src.events.reactor import EventSubmissionReceipt

    receipt = EventSubmissionReceipt(submitted=False, event_id="evt-2")
    assert _build_decision_receipt_spine(None, receipt) is None
    assert _build_decision_receipt_spine({}, receipt) is None
    assert _build_decision_receipt_spine({"city": "Tokyo"}, receipt) is None


# ===========================================================================================
# END-TO-END PRODUCER STASH (loop-back fix, 2026-06-14)
#
# Prior-critic finding: NOTHING in src/ wrote the `_edli_spine_*` keys, so
# `_build_decision_receipt_spine` returned None for EVERY live candidate (spec:998/1033
# violated). The 10 green tests did not catch it because the only live-path test
# (test_live_emission_helper_reconstructs_from_threaded_payload_inputs above) HAND-BUILDS the
# spine dict — masking the missing producer. The tests below drive the ACTUAL live q-build
# (_market_analysis_from_event_snapshot) and assert the real execution writes the keys onto the
# threaded payload, then run the real reactor consumer over what the producer wrote.
# ===========================================================================================

import json as _json
import sqlite3 as _sqlite3
from types import SimpleNamespace as _NS


def _e2e_two_bins():
    from src.types.market import Bin

    return [
        Bin(23, 23, "C", "23°C"),
        Bin(24, None, "C", "24°C or higher"),
    ]


def _e2e_family(bins, city="Tokyo", metric="high"):
    candidates = [
        _NS(condition_id=f"cond-{i}", bin=b, yes_token_id=f"yes-{i}", no_token_id=f"no-{i}")
        for i, b in enumerate(bins)
    ]
    return _NS(
        city=city, metric=metric, target_date="2026-06-14",
        event_type="FORECAST_SNAPSHOT_READY", bins=bins, candidates=candidates,
        yes_token_ids=[f"yes-{i}" for i in range(len(bins))],
        no_token_ids=[f"no-{i}" for i in range(len(bins))], family_id="e2e-fam",
    )


def _e2e_snapshot(members):
    return {
        "settlement_unit": "C", "temperature_metric": "high",
        "members_json": _json.dumps([float(m) for m in members]), "members_precision": 1.0,
        "source_id": "ecmwf_open_data", "issue_time": "2026-06-12T00:00:00+00:00",
        "dataset_id": "e2e_v1", "data_version": "e2e_v1",
    }


def _e2e_costs(bins, no_price=0.75, yes_price=0.25):
    from src.contracts.execution_price import ExecutionPrice as EP

    costs = {}
    for i, _ in enumerate(bins):
        cid = f"cond-{i}"
        costs[(cid, "buy_yes")] = (
            None, EP(yes_price, "ask", fee_deducted=True, currency="probability_units"),
            yes_price, None, None,
        )
        costs[(cid, "buy_no")] = (
            None, EP(no_price, "ask", fee_deducted=True, currency="probability_units"),
            no_price, None, None,
        )
    return costs


def _drive_live_q_build(payload, *, members, monkeypatch=None):
    """Run the REAL q-build (_market_analysis_from_event_snapshot) on a Tokyo-like high family.

    Returns the MarketAnalysis. Mutates `payload` in place — exactly as the live reactor threads
    it (so the `_edli_spine_*` keys the producer writes are observable to the caller, mirroring
    the live lift seam in _generate_candidate_proofs).

    The EMOS sole-calibrator flag is forced OFF here so the run deterministically routes through
    the bias/Platt branch — the DEFAULT live maze path, which computes NO explicit predictive
    mu/sigma. That is the strongest proof of the producer stash: this branch wrote NOTHING before
    the loop-back fix and must now derive mu/sigma from the integrated member array. (Self-contained
    regardless of the conftest pin, so the test states its own branch intent.)
    """
    from src.config import runtime_cities_by_name, settings
    from src.engine.event_reactor_adapter import _market_analysis_from_event_snapshot

    if runtime_cities_by_name().get("Tokyo") is None:
        pytest.skip("Tokyo city config missing")

    if monkeypatch is not None:
        _edli_cfg = getattr(settings, "_data", {}).get("edli")
        if isinstance(_edli_cfg, dict):
            monkeypatch.setitem(_edli_cfg, "edli_emos_sole_calibrator_enabled", False)

    bins = _e2e_two_bins()
    return _market_analysis_from_event_snapshot(
        calibration_conn=_sqlite3.connect(":memory:"),
        snapshot=_e2e_snapshot(members),
        family=_e2e_family(bins),
        native_costs=_e2e_costs(bins),
        payload=payload,
        decision_time=None,
    )


def test_live_qbuild_writes_spine_keys_onto_payload_end_to_end(monkeypatch) -> None:
    """RED-on-revert: the LIVE q-build writes the `_edli_spine_*` keys onto the threaded payload.

    This is the producer half of the Stage-0 receipt spine. It drives the real
    _market_analysis_from_event_snapshot (NOT a hand-built dict) on a real Tokyo-like high
    candidate (fresh members ~21..23) and asserts:

      1. The threaded payload actually carries the producer keys after the q-build runs —
         _edli_spine_q_vector, _edli_spine_mu_native, _edli_spine_sigma_native,
         _edli_spine_raw_members_native, _edli_spine_debiased_members_native. Before this fix
         NOTHING in src/ wrote them, so this assertion FAILS on revert (spec:998/1033).
      2. The reactor's REAL consumer (_build_decision_receipt_spine) over those producer-written
         inputs reconstructs a non-None DecisionReceipt whose mu / sigma / member envelope /
         q_sum are all non-None — the live verification signal "no candidate receipt lacks
         mu/sigma/member envelope/q_source/route" (spec:1033).

    The producer is READ-ONLY: the q vector it stashes is byte-identical to the MarketAnalysis
    p_cal the decision uses (asserted), so the stash can never have altered the decision.
    """
    from src.engine.event_reactor_adapter import _build_decision_receipt_spine
    from src.events.reactor import EventSubmissionReceipt

    members = [21.0, 22.0, 22.0, 23.0, 21.5, 22.5]
    payload: dict = {}
    analysis = _drive_live_q_build(payload, members=members, monkeypatch=monkeypatch)

    # (1) the producer wrote the spine keys onto the threaded payload — the missing half.
    assert "_edli_spine_q_vector" in payload, (
        "live q-build did not stash _edli_spine_q_vector onto the payload — producer missing"
    )
    assert "_edli_spine_mu_native" in payload
    assert "_edli_spine_sigma_native" in payload
    assert "_edli_spine_raw_members_native" in payload
    assert "_edli_spine_debiased_members_native" in payload
    assert "_edli_q_source" in payload  # provenance the existing seam already set

    # READ-ONLY proof: the stashed q vector IS the decision's p_cal (no divergence). The stash
    # copies already-computed values; it cannot have changed the distribution the decision used.
    p_cal = [float(x) for x in analysis.p_cal.tolist()]
    assert payload["_edli_spine_q_vector"] == pytest.approx(p_cal)
    # The raw members the producer stashed are the genuine uncorrected snapshot members.
    assert payload["_edli_spine_raw_members_native"] == pytest.approx(members)

    # (2) drive the REAL consumer over what the producer wrote (mirroring the live lift seam:
    # _generate_candidate_proofs lifts payload[_edli_spine_*] onto provenance_capture, then the
    # wrapper calls _build_decision_receipt_spine). We do NOT hand-build the dict — we read it
    # back from the payload the real producer mutated.
    spine_inputs = {
        k: payload[k]
        for k in (
            "_edli_spine_mu_native",
            "_edli_spine_sigma_native",
            "_edli_spine_raw_members_native",
            "_edli_spine_debiased_members_native",
            "_edli_spine_q_vector",
            "_edli_q_source",
        )
        if k in payload
    }
    spine_inputs["city"] = "Tokyo"
    receipt = EventSubmissionReceipt(
        submitted=False, event_id="e2e-1",
        q_source=payload.get("_edli_q_source"), selection_authority="BH_FDR",
    )
    spine = _build_decision_receipt_spine(spine_inputs, receipt)

    assert spine is not None, "consumer returned None — producer wrote no usable spine"
    # The Stage-0 live signal: no candidate receipt lacks mu/sigma/member envelope/q_source.
    assert spine.has_forecast_spine() is True
    assert spine.has_q_spine() is True
    recon = spine.reconstruct_forecast_q_route_and_size()
    assert recon["forecast"]["mu_native"] is not None
    assert recon["forecast"]["sigma_native"] is not None
    assert recon["forecast"]["member_min_native"] is not None
    assert recon["forecast"]["member_max_native"] is not None
    assert recon["forecast"]["member_min_native"] <= recon["forecast"]["member_max_native"]
    assert recon["q"]["q_source"] is not None
    assert recon["q"]["q_sum"] is not None
    # The reconstructed q_sum is the true mass of the LIVE q vector (normalized to 1 by the
    # live calibrated distribution) — coherence derived from the producer's own q vector.
    assert recon["q"]["q_sum"] == pytest.approx(sum(p_cal))
    # The member envelope brackets the empirical center the producer recorded (mu in [min,max]).
    assert (
        recon["forecast"]["member_min_native"]
        <= recon["forecast"]["mu_native"]
        <= recon["forecast"]["member_max_native"]
    )
