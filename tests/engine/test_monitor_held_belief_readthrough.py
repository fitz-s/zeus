# Created: 2026-06-21
# Last reused or audited: 2026-09-04
# Lifecycle: created=2026-06-21; last_reviewed=2026-09-04; last_reused=2026-09-04
# Authority basis: docs/evidence/live_order_pathology/2026-06-21_forward_chain_diagnosis.md
#   "CHOSEN FIX (consult-validated, two layers)" — LAYER 2 monitor read-through.
"""ANTIBODY: stale held belief must recover without blocking portfolio monitoring.

The disease (live −$27.63): a held family's cached forecast_posteriors row goes
stale and the monitor fail-closes to HOLD (BELIEF_AUTHORITY_FAULT) FOREVER —
never recomputing — so the conservative CI_SEPARATED_REVERSAL exit is starved and
the position rides physics reversals to full settlement loss. These tests pin:

1. An unbounded diagnostic read-through may restore same-authority probability.
   The bounded live portfolio monitor never runs that Python fusion inline; it
   fails closed and dispatches the independent producer for the next re-decision.
2. When inputs are genuinely insufficient, the monitor STILL fail-closes (is_fresh
   not True) AND records a DURABLE, RETRYABLE belief_debt marker — never a silent
   permanent freeze.
3. NO FALSE EXIT: the monitor only supplies a fresh belief; it never itself decides
   an exit. A freshly-recomputed belief that has NOT reversed simply becomes fresh
   authority (HOLD is still decided downstream by the untouched CI gate).

These are antibodies: removing the bounded producer/consumer split can again
let one family retain the whole portfolio beyond its deadline; removing the
belief_debt record makes producer failure silent.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

BIN = "Will the highest temperature in Karachi be 37°C on June 12?"


@contextmanager
def _monitor_forecast_world_reader(conn):
    """Test double for the monitor's single forecast-MAIN/world reader."""
    try:
        yield conn
    finally:
        conn.close()


def test_held_a_prime_tel_aviv_eleven_bin_rebuild_has_500_coherent_rows():
    import src.engine.event_reactor_adapter as era
    from src.types.market import Bin as MarketBin

    bounds = [(None, 29)] + [
        (value, value) for value in range(30, 39)
    ] + [(39, None)]
    family = SimpleNamespace(
        city="Tel Aviv",
        metric="high",
        candidates=[
            SimpleNamespace(bin=MarketBin(low, high, "C", f"bin-{index}"))
            for index, (low, high) in enumerate(bounds)
        ],
    )
    likelihood = {
        "semantics": "same_station_preliminary_report_survival_likelihood_v1",
        "cutoff": "2026-08-24T12:45:00+00:00",
        "successes": [],
        "failures": [],
        "unconfirmed_awc_ids": [1],
        "alpha": 0.5,
        "beta": 0.5,
        "station_id": "LLBG",
        "source_channel_pair": {
            "awc": "aviationweather_metar",
            "ogimet": "ogimet_metar_llbg",
        },
    }
    likelihood["identity_hash"] = hashlib.sha256(
        json.dumps(likelihood, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    payload = {
        "metric": "high",
        "settlement_source": "aviationweather_metar",
        "evidence_finality": "MONOTONE_SETTLEMENT_BOUND",
        "rounded_value": 33.0,
        "_edli_day0_redecision_authority_scope": (
            "held_exposure_current_day0_only_v1"
        ),
        "_edli_day0_source_clock_predictive_sigma_native": 1.2,
        "_edli_day0_provisional_revision_likelihood": {
            **likelihood,
            "boundary_survival_probability": 0.9705882352941176,
        },
        "_edli_day0_provisional_boundary_survival_probability": (
            0.9705882352941176
        ),
    }
    era._rebuild_held_day0_shared_carrier(
        payload=payload,
        family=family,
        unit="C",
        decision_time=datetime(2026, 8, 24, 12, 45, tzinfo=timezone.utc),
        future_extremes_c=(28.5, 29.0, 30.5, 31.25),
    )
    assert len(payload["_edli_day0_remaining_carrier_q"]) == 11
    assert payload["_edli_day0_remaining_probability_sample_count"] == 500
    assert len(payload["_edli_day0_remaining_probability_samples"]) == 500
    assert all(
        sum(row) == pytest.approx(1.0)
        for row in payload["_edli_day0_remaining_probability_samples"]
    )
    assert payload["_edli_day0_remaining_content_identity"]


def _pos():
    from src.state.portfolio import Position

    return Position(
        trade_id="t-readthrough-1",
        market_id="m1",
        city="Karachi",
        cluster="Karachi",
        target_date="2026-06-12",
        bin_label=BIN,
        direction="buy_no",
        unit="C",
        temperature_metric="high",
        entry_method="ens_member_counting",
        entry_price=0.66,
        p_posterior=0.855,
    )


def _stale_belief():
    from src.engine.position_belief import ReplacementBelief

    return ReplacementBelief(
        held_side_prob=0.758, held_side_lcb=0.69, held_side_ucb=0.82,
        q_yes_bin=0.242, q_yes_lcb=0.18, q_yes_ucb=0.31, posterior_id="p9",
        computed_at="2026-06-12T00:00:00+00:00", age_hours=99.0,
        fresh=False, bin_key=BIN, direction="buy_no",
    )


def test_monitor_causal_bundle_mismatch_is_stale_and_auditable(monkeypatch):
    import src.engine.monitor_refresh as mr

    position = _pos()
    receipt = {
        "reason": "DAY0_CAUSAL_EVIDENCE_BUNDLE_MISMATCH",
        "expected_bundle_identity": "bundle-old",
        "actual_bundle_identity": "bundle-new",
        "expected_carrier_vector_identity": "vector-old",
        "actual_carrier_vector_identity": "vector-new",
        "expected_carrier_vector_hash": "hash-old",
        "actual_carrier_vector_hash": "hash-new",
    }
    error = ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_MISMATCH")
    setattr(error, "day0_causal_bundle_validation_receipt", receipt)
    monkeypatch.setattr(
        mr, "_day0_absorbing_hard_fact_overlay", lambda **_: None
    )
    monkeypatch.setattr(mr, "_would_use_day0_monitor_lane", lambda *_: True)
    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _: "0x" + "1" * 64)
    monkeypatch.setattr(
        mr,
        "_refresh_current_global_day0_probability",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    refreshed_prob, refreshed, fresh = mr.monitor_probability_refresh(
        position,
        conn=sqlite3.connect(":memory:"),
        city=SimpleNamespace(
            name="Karachi",
            timezone="Asia/Karachi",
            settlement_source_type="noaa",
        ),
        target_d="2026-06-12",
    )

    assert refreshed_prob == position.p_posterior
    assert fresh is False
    monitor_receipt = getattr(refreshed, "_day0_monitor_probability_receipt")
    assert monitor_receipt["causal_evidence_bundle_validation"] == receipt
    assert refreshed.last_monitor_prob_is_fresh is False


def test_readthrough_fresh_recompute_restores_probability_authority(monkeypatch):
    """Stale cached belief + a successful read-through recompute → is_fresh True.

    Antibody: without the read-through call this returns is_fresh False (the live
    freeze). The recompute yields the held-side prob and the monitor attests it.
    """
    import src.engine.monitor_refresh as mr
    import src.engine.position_belief as pb

    monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: _stale_belief())
    # The legacy chain must NEVER be the freshness source.
    monkeypatch.setattr(mr, "_refresh_ens_member_counting", lambda **kw: (0.5, []))
    # Read-through recompute succeeds and returns the held-side prob (e.g. NO has
    # collapsed to 0.30 — a reversal the frozen 0.758 belief could never see).
    monkeypatch.setattr(
        mr, "_attempt_held_belief_readthrough", lambda *a, **k: (0.30, 0.22, 0.41)
    )

    pos = _pos()
    prob, refresh_pos, is_fresh = mr.monitor_probability_refresh(
        pos, conn=None, city=object(), target_d=None,
    )

    assert is_fresh is True
    assert prob == pytest.approx(0.30)
    assert getattr(
        refresh_pos,
        "_replacement_current_evidence_held_bounds",
    ) == pytest.approx((0.22, 0.41))
    # The belief is branded as a same-authority read-through, never a legacy substitution.
    assert any(
        "readthrough" in v or "read_through" in v
        for v in refresh_pos.applied_validations
    )
    assert not any(v == "legacy_belief_substitution_suppressed" for v in refresh_pos.applied_validations)


def test_readthrough_insufficient_inputs_failclose_with_durable_belief_debt(monkeypatch):
    """Stale cached belief + read-through NOT eligible → fail-close AND a durable,
    retryable belief_debt marker (family/reason/first_failed_at/attempts).

    Antibody: removing the belief_debt record makes this assertion fail — a silent
    permanent freeze (the chronic Karachi case) would be undetectable.
    """
    import src.engine.monitor_refresh as mr
    import src.engine.position_belief as pb

    monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: _stale_belief())
    monkeypatch.setattr(mr, "_refresh_ens_member_counting", lambda **kw: (0.5, []))
    # Read-through cannot honestly recompute (no current single_runs / no on-disk anchor).
    monkeypatch.setattr(mr, "_attempt_held_belief_readthrough", lambda *a, **k: None)
    reseed_called: list[tuple] = []
    monkeypatch.setattr(
        mr, "_enqueue_single_family_belief_reseed_failsoft",
        lambda **kw: reseed_called.append((kw.get("city"), kw.get("target_date"), kw.get("metric"))) or None,
    )

    pos = _pos()
    prob, refresh_pos, is_fresh = mr.monitor_probability_refresh(
        pos, conn=None, city=object(), target_d=None,
    )

    assert is_fresh is not True
    # Still fail-closed under the belief-authority guard.
    assert any(v == "BELIEF_AUTHORITY_FAULT" for v in pos.applied_validations)
    # Durable, retryable belief-debt record exists and carries the family + reason.
    debt = [v for v in pos.applied_validations if v.startswith("belief_debt")]
    assert debt, f"no belief_debt marker recorded: {pos.applied_validations}"
    marker = debt[0]
    assert "Karachi" in marker
    assert "2026-06-12" in marker
    assert "high" in marker
    # The existing reseed repair lane still fires (NOT a silent freeze).
    assert reseed_called == [("Karachi", "2026-06-12", "high")]


def test_monitor_read_unavailable_stays_evidence_unavailable_then_recovers(
    monkeypatch,
    tmp_path,
):
    """Real selector/HWM/belief reads fail closed once, then recover."""
    import src.engine.monitor_refresh as mr
    import src.engine.position_belief as pb
    from src.data.replacement_forecast_cycle_policy import (
        CURRENT_EVIDENCE_SEMANTICS_REVISION,
    )

    decision_now = datetime(2026, 6, 6, 4, tzinfo=timezone.utc)
    source_cycle = datetime(2026, 6, 6, 0, tzinfo=timezone.utc)
    captured_at = source_cycle + timedelta(minutes=5)
    computed_at = decision_now - timedelta(hours=1)
    forecasts_db = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(forecasts_db)
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id TEXT, city TEXT, target_date TEXT,
            temperature_metric TEXT, computed_at TEXT, q_json TEXT,
            q_lcb_json TEXT, q_ucb_json TEXT, source_cycle_time TEXT,
            runtime_layer TEXT, source_id TEXT, posterior_method TEXT,
            provenance_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            raw_model_forecast_id INTEGER PRIMARY KEY,
            model TEXT, city TEXT, target_date TEXT, metric TEXT,
            source_cycle_time TEXT, source_available_at TEXT, captured_at TEXT,
            lead_days INTEGER, forecast_value_c REAL, endpoint TEXT,
            coverage_status TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE raw_forecast_artifacts (
            source_cycle_time TEXT, captured_at TEXT,
            source_available_at TEXT, artifact_metadata_json TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO raw_model_forecasts VALUES
        (1, 'gfs', 'Karachi', '2026-06-12', 'high', ?, ?, ?, 1, 37.0,
         'single_runs', 'COVERED')
        """,
        (
            source_cycle.isoformat(),
            captured_at.isoformat(),
            captured_at.isoformat(),
        ),
    )
    provenance = {
        "bayes_precision_fusion": {
            "used_models": ["gfs"],
            "current_value_serving": {
                "gfs": {
                    "raw_model_forecast_id": 1,
                    "served_cycle": source_cycle.isoformat(),
                    "captured_at": captured_at.isoformat(),
                    "served_via": "single_runs",
                }
            },
                "current_evidence_shape": {
                    "semantics_revision": CURRENT_EVIDENCE_SEMANTICS_REVISION,
                    "shape_lag_hours": 0.0,
                    "source_cycle_time": source_cycle.isoformat(),
                    "stale_shape_reused": False,
                    "translation_applied": False,
                },
        },
        "q_bootstrap_samples_basis": (
            "global_simplex_current_finite_moment_evidence_v3"
        ),
        "q_bootstrap_samples_by_bin": {BIN: [0.25, 0.25]},
    }
    conn.execute(
        "INSERT INTO forecast_posteriors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "p-current-serving",
            "Karachi",
            "2026-06-12",
            "high",
            computed_at.isoformat(),
            json.dumps({BIN: 0.25}),
            json.dumps({BIN: 0.20}),
            json.dumps({BIN: 0.30}),
            source_cycle.isoformat(),
            "live",
            pb.LIVE_REPLACEMENT_POSTERIOR_SOURCE_ID,
            "openmeteo_ecmwf_ifs9_bayes_fusion",
            json.dumps(provenance),
        ),
    )
    conn.commit()
    conn.close()

    fault = {"armed": True}

    class InterruptOnceConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            normalized = " ".join(str(sql).split()).lower()
            if fault["armed"] and "endpoint in (?, ?)" in normalized:
                fault["armed"] = False
                raise sqlite3.OperationalError("interrupted")
            return super().execute(sql, parameters)

    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        kwargs["factory"] = InterruptOnceConnection
        return real_connect(*args, **kwargs)

    real_load = pb.load_replacement_belief
    observed_beliefs = []

    def load(**kwargs):
        belief = real_load(
            **kwargs,
            db_path=str(forecasts_db),
            now=decision_now,
        )
        observed_beliefs.append(belief)
        return belief

    monkeypatch.setattr(pb.sqlite3, "connect", connect)
    monkeypatch.setattr(pb, "load_replacement_belief", load)
    monkeypatch.setattr(mr, "_attempt_held_belief_readthrough", lambda *a, **k: None)
    monkeypatch.setattr(
        mr,
        "_enqueue_single_family_belief_reseed_failsoft",
        lambda **_kw: None,
    )

    pos = _pos()
    first_prob, first_pos, first_fresh = mr.monitor_probability_refresh(
        pos, conn=None, city=object(), target_d=None,
    )
    assert first_prob == pytest.approx(pos.p_posterior)
    assert first_fresh is False
    assert "BELIEF_AUTHORITY_FAULT" in first_pos.applied_validations
    assert observed_beliefs[0] is not None
    assert observed_beliefs[0].fresh is False
    assert observed_beliefs[0].raw_input_lag_reason == (
        "basis=current_value_serving_read_unavailable:sqlite_error=interrupted"
    )

    second_prob, second_pos, second_fresh = mr.monitor_probability_refresh(
        pos, conn=None, city=object(), target_d=None,
    )
    assert second_prob == pytest.approx(0.75)
    assert second_fresh is True
    assert observed_beliefs[1] is not None
    assert observed_beliefs[1].fresh is True
    assert observed_beliefs[1].raw_input_lag_reason is None
    assert getattr(second_pos, "_replacement_current_evidence_held_bounds") == pytest.approx(
        (0.70, 0.80)
    )


def test_bounded_monitor_defers_sync_readthrough_to_independent_producer(monkeypatch):
    """One stale family cannot retain the portfolio monitor in Python fusion."""
    import src.engine.monitor_refresh as mr
    import src.engine.position_belief as pb

    belief_deadlines = []
    monkeypatch.setattr(
        pb,
        "load_replacement_belief",
        lambda **kw: belief_deadlines.append(kw.get("deadline_monotonic"))
        or _stale_belief(),
    )
    monkeypatch.setattr(mr.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        mr,
        "_attempt_held_belief_readthrough",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("bounded portfolio monitor must not run synchronous fusion")
        ),
    )
    reseed_called = []
    monkeypatch.setattr(
        mr,
        "_enqueue_single_family_belief_reseed_failsoft",
        lambda **kw: reseed_called.append(kw) or None,
    )

    pos = _pos()
    prob, refresh_pos, is_fresh = mr.monitor_probability_refresh(
        pos,
        conn=None,
        city=object(),
        target_d=None,
        deadline_monotonic=123.0,
    )

    assert prob == pytest.approx(pos.p_posterior)
    assert refresh_pos is pos
    assert is_fresh is False
    assert belief_deadlines == [pytest.approx(105.0)]
    assert reseed_called == [
        {"city": "Karachi", "target_date": "2026-06-12", "metric": "high"}
    ]
    assert "replacement_belief_readthrough_deferred_to_independent_producer" in (
        pos.applied_validations
    )
    assert any(
        "bounded_monitor_reseed_required" in validation
        for validation in pos.applied_validations
    )


def test_reseed_routes_same_cycle_input_revision_before_cycle_advance(
    monkeypatch,
    tmp_path,
):
    """A same-cycle provider revision must use its own resettable repair lane."""
    import src.data.replacement_forecast_production as production
    import src.data.replacement_fusion_upgrade_trigger as fusion
    import src.data.replacement_cycle_advance_trigger as cycle
    import src.engine.monitor_refresh as mr

    forecast_db = tmp_path / "forecasts.db"
    forecast_db.touch()
    cfg = {
        "forecast_db": forecast_db,
        "seed_dir": tmp_path / "seeds",
        "raw_manifest_dir": tmp_path / "raw",
    }
    monkeypatch.setattr(
        production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    captured = {}

    def enqueue_revision(**kwargs):
        captured.update(kwargs)
        return {
            "status": "FUSION_UPGRADE_TRIGGER",
            "seeds_enqueued": 1,
            "already_enqueued": 0,
        }

    monkeypatch.setattr(fusion, "enqueue_fusion_upgrade_reseeds", enqueue_revision)
    monkeypatch.setattr(
        cycle,
        "enqueue_single_family_cycle_advance_reseed",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("same-cycle input revision must not fall through")
        ),
    )
    monkeypatch.setattr(mr, "_day0_observed_extreme_reseed_payload", lambda **_kw: {})
    # Fixture date is not the point under test here (input-revision routing);
    # stub the target-local-day-ended gate so a fixed past date does not trip
    # it as real wall-clock time advances (see test_reseed_skips_when_target_
    # local_day_has_ended for that gate itself).
    monkeypatch.setattr(mr, "_is_position_after_target_local_day", lambda *a, **k: False)

    report = mr._perform_single_family_belief_reseed_failsoft(
        city="Warsaw",
        target_date="2026-08-12",
        metric="high",
    )

    assert report is not None
    assert report["status"] == "BELIEF_INPUT_REVISION_RESEED_ENQUEUED"
    assert report["repair_lane"] == "input_revision"
    assert report["enqueued"] is True
    assert captured["scopes"] == (("Warsaw", "2026-08-12", "high"),)
    assert captured["limit"] == 1


def test_reseed_falls_through_to_cycle_advance_without_input_revision(
    monkeypatch,
    tmp_path,
):
    """A stale family with no same-cycle revision still uses newer-cycle repair."""
    import src.data.replacement_forecast_production as production
    import src.data.replacement_fusion_upgrade_trigger as fusion
    import src.data.replacement_cycle_advance_trigger as cycle
    import src.engine.monitor_refresh as mr

    forecast_db = tmp_path / "forecasts.db"
    forecast_db.touch()
    cfg = {
        "forecast_db": forecast_db,
        "seed_dir": tmp_path / "seeds",
        "raw_manifest_dir": tmp_path / "raw",
    }
    monkeypatch.setattr(
        production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        fusion,
        "enqueue_fusion_upgrade_reseeds",
        lambda **_kwargs: {
            "status": "FUSION_UPGRADE_TRIGGER",
            "seeds_enqueued": 0,
            "already_enqueued": 0,
        },
    )
    captured = {}

    def enqueue_cycle(**kwargs):
        captured.update(kwargs)
        return {
            "status": "CYCLE_ADVANCE_ENQUEUED",
            "enqueued": True,
        }

    monkeypatch.setattr(
        cycle,
        "enqueue_single_family_cycle_advance_reseed",
        enqueue_cycle,
    )
    monkeypatch.setattr(mr, "_day0_observed_extreme_reseed_payload", lambda **_kw: {})
    # Fixture date is not the point under test here (cycle-advance fallthrough);
    # stub the target-local-day-ended gate (see test_reseed_skips_when_target_
    # local_day_has_ended for that gate itself).
    monkeypatch.setattr(mr, "_is_position_after_target_local_day", lambda *a, **k: False)

    report = mr._perform_single_family_belief_reseed_failsoft(
        city="Warsaw",
        target_date="2026-08-12",
        metric="high",
    )

    assert report is not None
    assert report["status"] == "CYCLE_ADVANCE_ENQUEUED"
    assert report["repair_lane"] == "cycle_advance"
    assert report["input_revision_status"] == "FUSION_UPGRADE_TRIGGER"
    assert captured["held_position"] is True
    cutoff = captured["minimum_posterior_computed_at"]
    assert cutoff.tzinfo is not None and cutoff.utcoffset() is not None
    from src.engine.position_belief import monitor_belief_max_age_hours

    expected_age = timedelta(hours=monitor_belief_max_age_hours())
    assert expected_age - timedelta(seconds=2) <= datetime.now(timezone.utc) - cutoff
    assert datetime.now(timezone.utc) - cutoff <= expected_age + timedelta(seconds=2)


def test_day0_reseed_requires_posterior_newer_than_current_inputs(
    monkeypatch,
    tmp_path,
):
    """A Day0 held repair must not accept a posterior computed before current inputs."""
    import src.data.replacement_forecast_production as production
    import src.data.replacement_fusion_upgrade_trigger as fusion
    import src.data.replacement_cycle_advance_trigger as cycle
    import src.engine.monitor_refresh as mr

    forecast_db = tmp_path / "forecasts.db"
    forecast_db.touch()
    cfg = {
        "forecast_db": forecast_db,
        "seed_dir": tmp_path / "seeds",
        "raw_manifest_dir": tmp_path / "raw",
    }
    monkeypatch.setattr(
        production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        fusion,
        "enqueue_fusion_upgrade_reseeds",
        lambda **_kwargs: {
            "status": "FUSION_UPGRADE_TRIGGER",
            "seeds_enqueued": 0,
            "already_enqueued": 0,
        },
    )
    captured = {}

    def enqueue_cycle(**kwargs):
        captured.update(kwargs)
        return {"status": "DAY0_OBSERVATION_ADVANCE_ENQUEUED", "enqueued": True}

    monkeypatch.setattr(
        cycle,
        "enqueue_single_family_cycle_advance_reseed",
        enqueue_cycle,
    )
    monkeypatch.setattr(
        mr,
        "_day0_observed_extreme_reseed_payload",
        lambda **_kw: {
            "day0_observed_extreme_c": 27.0,
            "day0_observed_extreme_source": "aviationweather_metar",
            "day0_observed_extreme_observation_time": "2026-08-27T10:00:00+00:00",
            "day0_observed_extreme_sample_count": 12,
            "day0_observed_extreme_unit": "C",
        },
    )
    # Fixture date is not the point under test here (Day0 posterior-freshness
    # bound); stub the target-local-day-ended gate (see
    # test_reseed_skips_when_target_local_day_has_ended for that gate itself).
    monkeypatch.setattr(mr, "_is_position_after_target_local_day", lambda *a, **k: False)
    before = datetime.now(timezone.utc)

    report = mr._perform_single_family_belief_reseed_failsoft(
        city="Jinan",
        target_date="2026-08-27",
        metric="high",
    )

    after = datetime.now(timezone.utc)
    assert report is not None
    assert report["status"] == "DAY0_OBSERVATION_ADVANCE_ENQUEUED"
    cutoff = captured["minimum_posterior_computed_at"]
    assert before <= cutoff <= after
    assert captured["held_position"] is True
    assert captured["day0_observed_extreme_c"] == 27.0


def _day0_reseed_test_setup(monkeypatch, tmp_path):
    """Shared scaffolding for the Day0 same-identity idempotency tests below."""
    import src.data.replacement_forecast_production as production
    import src.data.replacement_fusion_upgrade_trigger as fusion
    import src.engine.monitor_refresh as mr

    forecast_db = tmp_path / "forecasts.db"
    forecast_db.touch()
    cfg = {
        "forecast_db": forecast_db,
        "seed_dir": tmp_path / "seeds",
        "raw_manifest_dir": tmp_path / "raw",
    }
    monkeypatch.setattr(
        production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        fusion,
        "enqueue_fusion_upgrade_reseeds",
        lambda **_kwargs: {
            "status": "FUSION_UPGRADE_TRIGGER",
            "seeds_enqueued": 0,
            "already_enqueued": 0,
        },
    )
    # Isolate the module-level first-seen ledger from other tests in this session.
    # (guarded: absent entirely on the pre-fix parent, which these tests must
    # still be able to run against to prove the regression they pin.)
    if hasattr(mr, "_day0_reseed_gap_first_seen_at"):
        monkeypatch.setattr(mr, "_day0_reseed_gap_first_seen_at", {})
    # These tests use a fixed target_date one calendar day ahead of authoring
    # time as "still open"; that is not robust across a real local-midnight
    # rollover during a later test run and is not what this fixture group
    # tests (same-identity idempotency, not the day-ended gate). Stub the
    # gate closed so it never depends on wall-clock date (guarded: absent
    # entirely on the pre-fix parent).
    if hasattr(mr, "_is_position_after_target_local_day"):
        monkeypatch.setattr(mr, "_is_position_after_target_local_day", lambda *a, **k: False)
    return mr


def test_day0_reseed_second_call_recognizes_own_prior_success(monkeypatch, tmp_path):
    """FIX (T_beijing_belief, 2026-09-13): a posterior committed after the FIRST
    call's repair began must satisfy the SECOND call's freshness check for the
    SAME Day0 conditioning identity. Before the fix, `minimum_posterior_computed_at`
    was recomputed to wall-clock `now()` on every call, so a posterior committed a
    moment after call 1 began could never satisfy call 2's check -- causing 42
    redundant re-enqueues for one unchanged Beijing extreme value in 17 minutes.
    This test FAILS on parent commit 43a3b7894 (see commit body for proof)."""
    import src.data.replacement_cycle_advance_trigger as cycle

    mr = _day0_reseed_test_setup(monkeypatch, tmp_path)

    day0_payload = {
        "day0_observed_extreme_c": 17.0,
        "day0_observed_extreme_source": "aviationweather_metar",
        "day0_observed_extreme_observation_time": "2026-09-13T17:00:00+00:00",
        "day0_observed_extreme_sample_count": 4,
        "day0_observed_extreme_unit": "C",
    }
    monkeypatch.setattr(
        mr, "_day0_observed_extreme_reseed_payload", lambda **_kw: dict(day0_payload)
    )

    calls: list[dict] = []
    posterior_committed_at: list[datetime] = []

    def enqueue_cycle(**kwargs):
        calls.append(kwargs)
        min_computed = kwargs["minimum_posterior_computed_at"]
        if posterior_committed_at and min_computed <= posterior_committed_at[0]:
            return {"status": "CYCLE_ADVANCE_NOT_NEEDED", "enqueued": False}
        return {"status": "DAY0_OBSERVATION_ADVANCE_ENQUEUED", "enqueued": True}

    monkeypatch.setattr(
        cycle, "enqueue_single_family_cycle_advance_reseed", enqueue_cycle
    )

    first = mr._perform_single_family_belief_reseed_failsoft(
        city="Beijing", target_date="2026-09-14", metric="high",
    )
    assert first["status"] == "DAY0_OBSERVATION_ADVANCE_ENQUEUED"

    # The repair "completes": a live posterior commits just after call 1 began.
    posterior_committed_at.append(
        calls[0]["minimum_posterior_computed_at"] + timedelta(milliseconds=1)
    )

    second = mr._perform_single_family_belief_reseed_failsoft(
        city="Beijing", target_date="2026-09-14", metric="high",
    )

    assert len(calls) == 2
    assert calls[1]["minimum_posterior_computed_at"] == calls[0]["minimum_posterior_computed_at"], (
        "the second call must reuse the first call's first-detection cutoff, "
        "not recompute to now()"
    )
    assert second["status"] == "CYCLE_ADVANCE_NOT_NEEDED"


def test_day0_reseed_new_identity_starts_fresh_window(monkeypatch, tmp_path):
    """A rollover to a NEW Day0 observation identity (the extreme advances) must
    start a fresh first-seen window -- it must not inherit the prior identity's
    (now-irrelevant) detection time."""
    import src.data.replacement_cycle_advance_trigger as cycle

    mr = _day0_reseed_test_setup(monkeypatch, tmp_path)

    payload_box = {
        "payload": {
            "day0_observed_extreme_c": 17.0,
            "day0_observed_extreme_source": "aviationweather_metar",
            "day0_observed_extreme_observation_time": "2026-09-13T17:00:00+00:00",
            "day0_observed_extreme_sample_count": 4,
            "day0_observed_extreme_unit": "C",
        }
    }
    monkeypatch.setattr(
        mr,
        "_day0_observed_extreme_reseed_payload",
        lambda **_kw: dict(payload_box["payload"]),
    )

    calls: list[dict] = []

    def enqueue_cycle(**kwargs):
        calls.append(kwargs)
        return {"status": "DAY0_OBSERVATION_ADVANCE_ENQUEUED", "enqueued": True}

    monkeypatch.setattr(
        cycle, "enqueue_single_family_cycle_advance_reseed", enqueue_cycle
    )

    mr._perform_single_family_belief_reseed_failsoft(
        city="Beijing", target_date="2026-09-14", metric="high",
    )
    first_cutoff = calls[0]["minimum_posterior_computed_at"]

    # A new hourly METAR advances the observed extreme -> a NEW conditioning identity.
    payload_box["payload"] = {
        **payload_box["payload"],
        "day0_observed_extreme_c": 20.0,
        "day0_observed_extreme_observation_time": "2026-09-13T18:00:00+00:00",
    }
    before2 = datetime.now(timezone.utc)
    mr._perform_single_family_belief_reseed_failsoft(
        city="Beijing", target_date="2026-09-14", metric="high",
    )
    after2 = datetime.now(timezone.utc)
    second_cutoff = calls[1]["minimum_posterior_computed_at"]

    assert second_cutoff != first_cutoff
    assert before2 <= second_cutoff <= after2


def test_day0_reseed_clears_first_seen_only_when_posterior_actually_matched(
    monkeypatch, tmp_path
):
    """R-AG review of d749a8fdb (2026-09-13): CYCLE_ADVANCE_NOT_NEEDED fires for "still
    queued, unprocessed" and "owner ACTIVE, being worked" too -- not just "a posterior
    actually matched this identity". Only the last one means the gap is drained. Clearing
    the first-seen record on the bare status alone (as the first cut of this fix did) would
    let a queue backlog reproduce a throttled version of the original self-defeating-
    freshness defect: `enqueue_single_family_cycle_advance_reseed` now sets a dedicated
    `day0_posterior_matched` field at the source (replacement_cycle_advance_trigger.py,
    proven against a real DB in test_day0_extreme_updated_materialization_bridge.py::
    test_day0_report_marks_posterior_matched_only_when_gap_actually_drained); this test
    proves the monitor's clear trigger gates on THAT field, not the umbrella status."""
    import src.data.replacement_cycle_advance_trigger as cycle

    mr = _day0_reseed_test_setup(monkeypatch, tmp_path)

    day0_payload = {
        "day0_observed_extreme_c": 17.0,
        "day0_observed_extreme_source": "aviationweather_metar",
        "day0_observed_extreme_observation_time": "2026-09-13T17:00:00+00:00",
        "day0_observed_extreme_sample_count": 4,
        "day0_observed_extreme_unit": "C",
    }
    monkeypatch.setattr(
        mr, "_day0_observed_extreme_reseed_payload", lambda **_kw: dict(day0_payload)
    )

    responses = iter(
        [
            {"status": "DAY0_OBSERVATION_ADVANCE_ENQUEUED", "enqueued": True},  # call 1
            {
                "status": "CYCLE_ADVANCE_NOT_NEEDED",  # call 2: still queued, unprocessed
                "enqueued": False,
                "day0_posterior_matched": False,
            },
            {
                "status": "CYCLE_ADVANCE_NOT_NEEDED",  # call 3: owner ACTIVE, being worked
                "enqueued": False,
                "day0_posterior_matched": False,
            },
            {
                "status": "CYCLE_ADVANCE_NOT_NEEDED",  # call 4: gap genuinely drained
                "enqueued": False,
                "day0_posterior_matched": True,
            },
            {"status": "DAY0_OBSERVATION_ADVANCE_ENQUEUED", "enqueued": True},  # call 5: fresh gap
        ]
    )
    calls: list[dict] = []

    def enqueue_cycle(**kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(
        cycle, "enqueue_single_family_cycle_advance_reseed", enqueue_cycle
    )

    for _ in range(4):
        mr._perform_single_family_belief_reseed_failsoft(
            city="Beijing", target_date="2026-09-14", metric="high",
        )
    before5 = datetime.now(timezone.utc)
    mr._perform_single_family_belief_reseed_failsoft(
        city="Beijing", target_date="2026-09-14", metric="high",
    )
    after5 = datetime.now(timezone.utc)

    cutoffs = [c["minimum_posterior_computed_at"] for c in calls]

    # "still queued" (call 2) and "owner ACTIVE" (call 3) must NOT clear the record.
    assert cutoffs[1] == cutoffs[0]
    assert cutoffs[2] == cutoffs[0]
    assert cutoffs[3] == cutoffs[0]
    # call 4's genuine posterior match clears it, so call 5's (new) gap starts fresh.
    assert cutoffs[4] != cutoffs[0]
    assert before5 <= cutoffs[4] <= after5


def test_reseed_pending_input_revision_does_not_veto_cycle_advance(
    monkeypatch,
    tmp_path,
):
    """A durable same-cycle marker cannot strand a newer carrier-cycle repair."""
    import src.data.replacement_forecast_production as production
    import src.data.replacement_fusion_upgrade_trigger as fusion
    import src.data.replacement_cycle_advance_trigger as cycle
    import src.engine.monitor_refresh as mr

    forecast_db = tmp_path / "forecasts.db"
    forecast_db.touch()
    cfg = {
        "forecast_db": forecast_db,
        "seed_dir": tmp_path / "seeds",
        "raw_manifest_dir": tmp_path / "raw",
    }
    monkeypatch.setattr(
        production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        fusion,
        "enqueue_fusion_upgrade_reseeds",
        lambda **_kwargs: {
            "status": "FUSION_UPGRADE_TRIGGER",
            "seeds_enqueued": 0,
            "already_enqueued": 1,
        },
    )
    cycle_calls = []

    def enqueue_cycle(**kwargs):
        cycle_calls.append(kwargs)
        return {"status": "CYCLE_ADVANCE_ENQUEUED", "enqueued": True}

    monkeypatch.setattr(
        cycle,
        "enqueue_single_family_cycle_advance_reseed",
        enqueue_cycle,
    )
    monkeypatch.setattr(mr, "_day0_observed_extreme_reseed_payload", lambda **_kw: {})
    # Fixture date is not the point under test here (input-revision-pending
    # does not veto cycle-advance); stub the target-local-day-ended gate (see
    # test_reseed_skips_when_target_local_day_has_ended for that gate itself).
    monkeypatch.setattr(mr, "_is_position_after_target_local_day", lambda *a, **k: False)

    report = mr._perform_single_family_belief_reseed_failsoft(
        city="Madrid",
        target_date="2026-08-14",
        metric="high",
    )

    assert report is not None
    assert report["status"] == "CYCLE_ADVANCE_ENQUEUED"
    assert report["repair_lane"] == "cycle_advance"
    assert report["input_revision_status"] == "BELIEF_INPUT_REVISION_RESEED_PENDING"
    cutoff = cycle_calls[0].pop("minimum_posterior_computed_at")
    assert cutoff.tzinfo is not None and cutoff.utcoffset() is not None
    assert cycle_calls == [
        {
            "forecast_db": forecast_db,
            "seed_dir": tmp_path / "seeds",
            "raw_manifest_dir": tmp_path / "raw",
            "city": "Madrid",
            "target_date": "2026-08-14",
            "metric": "high",
            "held_position": True,
        }
    ]


def _frozen_datetime(mr, monkeypatch, fixed_now: datetime):
    """Pin ``mr.datetime.now()`` so the local-day-ended gate is deterministic."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(mr, "datetime", _Frozen)


def test_reseed_skips_when_target_local_day_has_ended(monkeypatch, tmp_path):
    """A held family's target local day having ended must block the enqueue before
    any DB/file work runs (London 2026-09-13 high, receipt seed_failed/
    London.2026-09-13.high.20260914T030722Z...json, request_written:false —
    `_is_position_target_local_day`'s exact-date match already empties the Day0
    payload past local midnight, so the seed builder fails the request closed
    with OM9_LOCALDAY_HOURLY_COVERAGE_INCOMPLETE; the enqueue should never even
    be attempted for an ended local day)."""
    import src.data.replacement_forecast_production as production
    import src.data.replacement_fusion_upgrade_trigger as fusion
    import src.data.replacement_cycle_advance_trigger as cycle
    import src.engine.monitor_refresh as mr

    def _boom(**_kwargs):
        raise AssertionError("no DB/file work may run once the local day has ended")

    monkeypatch.setattr(
        production, "_replacement_forecast_live_materialization_queue_config", _boom
    )
    monkeypatch.setattr(fusion, "enqueue_fusion_upgrade_reseeds", _boom)
    monkeypatch.setattr(cycle, "enqueue_single_family_cycle_advance_reseed", _boom)
    monkeypatch.setattr(mr, "_day0_observed_extreme_reseed_payload", _boom)

    # London BST (+1): local day 2026-09-13 ends at 2026-09-13T23:00:00Z. Pin
    # "now" 4h07m after that, matching the real receipt.
    _frozen_datetime(mr, monkeypatch, datetime(2026, 9, 14, 3, 7, 22, tzinfo=timezone.utc))
    ledger = {"London|2026-09-13|high": ("some-identity", "2026-09-14T00:00:00+00:00")}
    monkeypatch.setattr(mr, "_day0_reseed_gap_first_seen_at", ledger)

    report = mr._perform_single_family_belief_reseed_failsoft(
        city="London",
        target_date="2026-09-13",
        metric="high",
    )

    assert report == {
        "status": "RESEED_SKIPPED_TARGET_LOCAL_DAY_ENDED",
        "city": "London",
        "target_date": "2026-09-13",
        "metric": "high",
        "enqueued": False,
    }
    assert "London|2026-09-13|high" not in ledger


def test_reseed_still_enqueues_when_target_local_day_is_open(monkeypatch, tmp_path):
    """Parity: an in-progress local day must enqueue exactly as before the gate."""
    import src.data.replacement_forecast_production as production
    import src.data.replacement_fusion_upgrade_trigger as fusion
    import src.data.replacement_cycle_advance_trigger as cycle
    import src.engine.monitor_refresh as mr

    forecast_db = tmp_path / "forecasts.db"
    forecast_db.touch()
    cfg = {
        "forecast_db": forecast_db,
        "seed_dir": tmp_path / "seeds",
        "raw_manifest_dir": tmp_path / "raw",
    }
    monkeypatch.setattr(
        production,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        fusion,
        "enqueue_fusion_upgrade_reseeds",
        lambda **_kwargs: {
            "status": "FUSION_UPGRADE_TRIGGER",
            "seeds_enqueued": 0,
            "already_enqueued": 0,
        },
    )
    cycle_calls = []

    def enqueue_cycle(**kwargs):
        cycle_calls.append(kwargs)
        return {"status": "CYCLE_ADVANCE_ENQUEUED", "enqueued": True}

    monkeypatch.setattr(cycle, "enqueue_single_family_cycle_advance_reseed", enqueue_cycle)
    monkeypatch.setattr(mr, "_day0_observed_extreme_reseed_payload", lambda **_kw: {})

    # Same instant London's local day ends (23:00Z), one hour earlier: still
    # 2026-09-13 local (BST +1) — the day has NOT ended yet.
    _frozen_datetime(mr, monkeypatch, datetime(2026, 9, 13, 22, 0, 0, tzinfo=timezone.utc))

    report = mr._perform_single_family_belief_reseed_failsoft(
        city="London",
        target_date="2026-09-13",
        metric="high",
    )

    assert report is not None
    assert report["status"] == "CYCLE_ADVANCE_ENQUEUED"
    assert report["repair_lane"] == "cycle_advance"
    cutoff = cycle_calls[0].pop("minimum_posterior_computed_at")
    assert cutoff.tzinfo is not None and cutoff.utcoffset() is not None
    assert cycle_calls == [
        {
            "forecast_db": forecast_db,
            "seed_dir": tmp_path / "seeds",
            "raw_manifest_dir": tmp_path / "raw",
            "city": "London",
            "target_date": "2026-09-13",
            "metric": "high",
            "held_position": True,
        }
    ]


def test_reseed_target_local_day_ended_boundary_is_pinned(monkeypatch, tmp_path):
    """Boundary: `_is_position_after_target_local_day` compares LOCAL DATES, so the
    exact local-midnight instant already counts as the day having ended, while one
    microsecond before it does not (pinned per the helper's own docstring: "whether
    no forecast hours can remain in the contract-local target day")."""
    import src.data.replacement_forecast_production as production
    import src.engine.monitor_refresh as mr

    def _boom(**_kwargs):
        raise AssertionError("no DB/file work may run once the local day has ended")

    monkeypatch.setattr(
        production, "_replacement_forecast_live_materialization_queue_config", _boom
    )
    monkeypatch.setattr(mr, "_day0_observed_extreme_reseed_payload", _boom)
    monkeypatch.setattr(mr, "_day0_reseed_gap_first_seen_at", {})

    # Exactly London's local midnight (2026-09-14T00:00:00+01:00 == 2026-09-13T23:00:00Z):
    # local date has already rolled to 2026-09-14 -> ended.
    _frozen_datetime(mr, monkeypatch, datetime(2026, 9, 13, 23, 0, 0, tzinfo=timezone.utc))
    ended_report = mr._perform_single_family_belief_reseed_failsoft(
        city="London", target_date="2026-09-13", metric="high",
    )
    assert ended_report is not None
    assert ended_report["status"] == "RESEED_SKIPPED_TARGET_LOCAL_DAY_ENDED"

    # One microsecond earlier: still 2026-09-13 local -> NOT ended, so the gate
    # must not fire (falls through past the boom stubs into real DB/file work,
    # which is not configured here, so the fail-soft outer except returns None).
    _frozen_datetime(
        mr, monkeypatch, datetime(2026, 9, 13, 22, 59, 59, 999999, tzinfo=timezone.utc)
    )
    open_report = mr._perform_single_family_belief_reseed_failsoft(
        city="London", target_date="2026-09-13", metric="high",
    )
    assert open_report is None


def test_day0_unobserved_prefix_forwards_portfolio_deadline(monkeypatch):
    """The Day0 zero-observation fallback cannot reopen an unbounded DB read."""
    import src.engine.monitor_refresh as mr
    import src.engine.position_belief as pb

    captured_deadlines = []
    monkeypatch.setattr(mr.time, "monotonic", lambda: 300.0)
    monkeypatch.setattr(mr, "_day0_absorbing_hard_fact_overlay", lambda **_kw: None)
    monkeypatch.setattr(mr, "_would_use_day0_monitor_lane", lambda *_a: True)
    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _pos: "condition-1")
    monkeypatch.setattr(
        mr,
        "_refresh_current_global_day0_probability",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            mr._Day0UnobservedPrefixUnavailable("zero observations")
        ),
    )
    monkeypatch.setattr(
        pb,
        "load_replacement_belief",
        lambda **kw: captured_deadlines.append(kw.get("deadline_monotonic")) or None,
    )
    monkeypatch.setattr(
        mr,
        "_enqueue_single_family_belief_reseed_failsoft",
        lambda **_kw: None,
    )

    _prob, _refreshed, is_fresh = mr.monitor_probability_refresh(
        _pos(),
        conn=object(),
        city=object(),
        target_d=None,
        deadline_monotonic=321.0,
    )

    assert is_fresh is False
    assert captured_deadlines == [305.0]


def test_readthrough_does_not_itself_decide_an_exit(monkeypatch):
    """NO FALSE EXIT: a fresh recompute only supplies belief; the monitor returns
    a probability + is_fresh, never an exit verdict. The CI separation conservatism
    lives entirely downstream and is untouched here."""
    import src.engine.monitor_refresh as mr
    import src.engine.position_belief as pb

    monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: _stale_belief())
    monkeypatch.setattr(mr, "_refresh_ens_member_counting", lambda **kw: (0.5, []))
    # A fresh belief that has NOT reversed (still favors the held NO side).
    monkeypatch.setattr(
        mr,
        "_attempt_held_belief_readthrough",
        lambda *a, **k: (0.80, 0.70, 0.88),
    )

    pos = _pos()
    result = mr.monitor_probability_refresh(pos, conn=None, city=object(), target_d=None)

    # The contract is exactly (prob, Position, is_fresh) — a belief, not an exit.
    assert isinstance(result, tuple) and len(result) == 3
    prob, refresh_pos, is_fresh = result
    assert is_fresh is True
    assert prob == pytest.approx(0.80)
    from src.state.portfolio import Position
    assert isinstance(refresh_pos, Position)


def test_readthrough_restamps_expired_seed_ttl_to_decision_now(
    monkeypatch,
    tmp_path,
):
    """An expired on-disk seed must not poison the live read-through request.

    The source-cycle identity remains from the seed, but computed_at/expires_at
    are monitor-decision-time fields in this read-only path. Regression target:
    live monitor logs with ``expires_at must be after computed_at``.
    """
    import tests.test_replacement_forecast_materializer as base
    import src.data.replacement_forecast_materialization_request_builder as rb
    import src.data.replacement_forecast_materializer as mat
    import src.engine.monitor_refresh as mr
    import src.state.db as db

    monitor_now = datetime(2026, 6, 25, 14, 58, tzinfo=timezone.utc)
    expired_seed_payload = {
        "city": "Karachi",
        "target_date": "2026-06-12",
        "temperature_metric": "high",
        "computed_at": "2026-06-12T00:00:00+00:00",
        "expires_at": "2026-06-12T03:00:00+00:00",
    }
    monkeypatch.setattr(
        mr,
        "_freshest_family_seed_on_disk",
        lambda **kw: (tmp_path / "Karachi.2026-06-12.high.seed.json", expired_seed_payload),
    )
    monkeypatch.setattr(mr, "_seed_payload_covers_target_local_day", lambda **kw: True)
    monkeypatch.setattr(mr, "_held_side_probability_from_yes_bin_probability", lambda q, direction: 1.0 - q)
    monkeypatch.setattr(mr, "_match_bin", lambda q, label: (BIN, q[BIN]), raising=False)
    monkeypatch.setattr(
        "src.engine.position_belief.monitor_belief_max_age_hours",
        lambda: 3.0,
    )

    captured: dict[str, object] = {}

    def fake_build(payload, *, base_dir):
        captured["payload"] = dict(payload)
        computed_at = datetime.fromisoformat(str(payload["computed_at"]))
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        assert computed_at == monitor_now
        assert expires_at == monitor_now + timedelta(hours=3)
        return SimpleNamespace(ok=True, request=dict(payload))

    def fake_dataclass(request_json, *, base_dir):
        return base._request(
            source_cycle_time=datetime(2026, 6, 25, 12, tzinfo=timezone.utc),
            computed_at=datetime.fromisoformat(str(request_json["computed_at"])),
            expires_at=datetime.fromisoformat(str(request_json["expires_at"])),
        )

    def fake_compute(conn, request):
        captured["request"] = request
        assert request.computed_at == monitor_now
        assert request.expires_at == monitor_now + timedelta(hours=3)
        return SimpleNamespace(
            live_eligible=True,
            q={BIN: 0.25},
            q_lcb_map={BIN: 0.18},
            q_ucb_map={BIN: 0.33},
            decorrelated_providers_served=2,
            decorrelated_providers_expected=3,
        )

    monkeypatch.setattr(rb, "build_replacement_forecast_materialization_request", fake_build)
    monkeypatch.setattr(rb, "build_materialize_request_dataclass", fake_dataclass)
    monkeypatch.setattr(mat, "compute_replacement_posterior_readonly", fake_compute)
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_read_only",
        lambda **_kwargs: sqlite3.connect(":memory:"),
    )

    held_prob, held_lcb, held_ucb = mr._attempt_held_belief_readthrough(
        _pos(),
        city=object(),
        target_d=None,
        metric="high",
        decision_now=monitor_now,
    )

    assert held_prob == pytest.approx(0.75)
    assert held_lcb == pytest.approx(0.67)
    assert held_ucb == pytest.approx(0.82)
    assert captured["payload"]["computed_at"] == monitor_now.isoformat()


def test_readthrough_sqlite_work_is_interrupted_at_monitor_deadline(
    monkeypatch,
    tmp_path,
):
    """One stale family cannot overrun the whole-book monitor cycle budget."""
    import tests.test_replacement_forecast_materializer as base
    import src.data.replacement_forecast_materialization_request_builder as rb
    import src.data.replacement_forecast_materializer as mat
    import src.engine.monitor_refresh as mr
    import src.state.db as db

    monitor_now = datetime(2026, 6, 25, 14, 58, tzinfo=timezone.utc)
    seed_payload = {
        "city": "Karachi",
        "target_date": "2026-06-12",
        "temperature_metric": "high",
        "computed_at": monitor_now.isoformat(),
        "expires_at": (monitor_now + timedelta(hours=3)).isoformat(),
    }
    monkeypatch.setattr(
        mr,
        "_freshest_family_seed_on_disk",
        lambda **kw: (
            tmp_path / "Karachi.2026-06-12.high.seed.json",
            seed_payload,
        ),
    )
    monkeypatch.setattr(mr, "_seed_payload_covers_target_local_day", lambda **kw: True)
    monkeypatch.setattr(
        rb,
        "build_replacement_forecast_materialization_request",
        lambda payload, *, base_dir: SimpleNamespace(ok=True, request=dict(payload)),
    )
    monkeypatch.setattr(
        rb,
        "build_materialize_request_dataclass",
        lambda request_json, *, base_dir: base._request(
            source_cycle_time=datetime(2026, 6, 25, 12, tzinfo=timezone.utc),
            computed_at=monitor_now,
            expires_at=monitor_now + timedelta(hours=3),
        ),
    )

    def deliberately_unbounded_sql(conn, request):
        del request
        conn.execute(
            """
            WITH RECURSIVE spin(value) AS (
                SELECT 1
                UNION ALL
                SELECT value + 1 FROM spin
            )
            SELECT SUM(value) FROM spin
            """
        ).fetchone()
        raise AssertionError("deadline failed to interrupt SQLite")

    monkeypatch.setattr(mat, "compute_replacement_posterior_readonly", deliberately_unbounded_sql)
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_read_only",
        lambda **_kwargs: sqlite3.connect(":memory:"),
    )

    started = time.monotonic()
    result = mr._attempt_held_belief_readthrough(
        _pos(),
        city=object(),
        target_d=None,
        metric="high",
        decision_now=monitor_now,
        deadline_monotonic=started + 0.02,
    )

    assert result is None
    assert time.monotonic() - started < 1.0


def test_day0_visibility_retry_recovers_new_snapshot_after_one_poll(monkeypatch):
    """One fresh builder retry recovers a successor committed within 100ms."""
    import src.engine.monitor_refresh as mr

    clock = [10.0]
    attempts = 0
    build_deadlines: list[float] = []
    snapshot = SimpleNamespace()

    def build(*_args, deadline_monotonic, **_kwargs):
        nonlocal attempts
        attempts += 1
        build_deadlines.append(deadline_monotonic)
        if clock[0] < 10.05:
            raise ValueError("GLOBAL_CURRENT_BUNDLE_BLOCKED:REPLACEMENT_RAW_INPUT_HWM")
        return snapshot

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(mr, "_build_current_global_day0_family_snapshot", build)
    monkeypatch.setattr(
        mr,
        "_materialize_current_global_day0_probability",
        lambda position, built: (0.30, position, built is snapshot),
    )
    monkeypatch.setattr(mr.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mr.time, "sleep", sleep)

    result = mr._refresh_current_global_day0_probability(
        _pos(),
        trade_conn=object(),
        deadline_monotonic=11.0,
    )

    held_prob, refresh_pos, is_fresh = result
    assert held_prob == pytest.approx(0.30)
    assert refresh_pos is not None
    assert is_fresh is True
    assert attempts == 2
    assert build_deadlines == pytest.approx([10.65, 10.35])
    assert clock[0] == pytest.approx(10.1)


def test_day0_primary_snapshot_read_reserves_visibility_retry_budget(monkeypatch):
    """Primary authority read leaves the full retry window below outer expiry."""
    import src.engine.monitor_refresh as mr

    clock = [10.0]
    build_deadlines = []
    snapshot = SimpleNamespace()

    def build(*_args, deadline_monotonic, **_kwargs):
        build_deadlines.append(deadline_monotonic)
        clock[0] = 10.5
        if clock[0] >= deadline_monotonic:
            raise mr._Day0SnapshotReadDeadlineExceeded("primary read interrupted")
        return snapshot

    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(mr, "_build_current_global_day0_family_snapshot", build)
    monkeypatch.setattr(
        mr,
        "_materialize_current_global_day0_probability",
        lambda position, built: (0.30, position, built is snapshot),
    )
    monkeypatch.setattr(mr.time, "monotonic", lambda: clock[0])

    held_prob, _refresh_pos, is_fresh = mr._refresh_current_global_day0_probability(
        _pos(),
        trade_conn=object(),
        deadline_monotonic=15.1,
    )

    assert held_prob == pytest.approx(0.30)
    assert is_fresh is True
    assert build_deadlines == pytest.approx([14.75])


def test_day0_visibility_retry_zero_remaining_budget_fails_closed(monkeypatch):
    """No outer budget means neither a snapshot read nor a retry may borrow time."""
    import src.engine.monitor_refresh as mr

    attempts = []

    def build(*_args, deadline_monotonic, **_kwargs):
        attempts.append(deadline_monotonic)
        raise mr._Day0SnapshotReadDeadlineExceeded("primary read expired")

    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(mr, "_build_current_global_day0_family_snapshot", build)
    monkeypatch.setattr(mr.time, "monotonic", lambda: 10.0)

    with pytest.raises(mr._Day0SnapshotReadDeadlineExceeded):
        mr._refresh_current_global_day0_probability(
            _pos(), trade_conn=object(), deadline_monotonic=10.0
        )

    assert attempts == [pytest.approx(9.65)]


def _day0_event_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE opportunity_events (
            event_id TEXT,
            event_type TEXT,
            entity_key TEXT,
            source TEXT,
            observed_at TEXT,
            available_at TEXT,
            received_at TEXT,
            causal_snapshot_id TEXT,
            payload_hash TEXT,
            idempotency_key TEXT,
            priority INTEGER,
            expires_at TEXT,
            payload_json TEXT,
            schema_version INTEGER,
            created_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_opportunity_events_day0_family_extreme "
        "ON opportunity_events(event_type)"
    )
    at = "2026-06-12T12:00:00+00:00"
    conn.execute(
        "INSERT INTO opportunity_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "event-1",
            "DAY0_EXTREME_UPDATED",
            "Karachi|2026-06-12|high",
            "test",
            at,
            at,
            at,
            "snapshot-1",
            "payload-hash",
            "idem-1",
            1,
            "2026-06-12T13:00:00+00:00",
            json.dumps(
                {
                    "city": "Karachi",
                    "target_date": "2026-06-12",
                    "metric": "high",
                }
            ),
            1,
            at,
        ),
    )
    return conn


def test_day0_monitor_selects_latest_event_as_of_frozen_decision_time(monkeypatch):
    """A newer committed event cannot hide the latest causal monitor event."""
    import src.engine.event_reactor_adapter as era
    import src.engine.monitor_refresh as mr
    import src.state.db as db

    world = _day0_event_connection()
    future = "2026-06-12T12:00:10+00:00"
    world.execute(
        "INSERT INTO opportunity_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "event-2",
            "DAY0_EXTREME_UPDATED",
            "Karachi|2026-06-12|high",
            "test",
            future,
            future,
            future,
            "snapshot-2",
            "payload-hash-2",
            "idem-2",
            1,
            "2026-06-12T13:00:00+00:00",
            json.dumps(
                {
                    "city": "Karachi",
                    "target_date": "2026-06-12",
                    "metric": "high",
                }
            ),
            1,
            future,
        ),
    )
    forecasts = sqlite3.connect(":memory:")
    hwm = sqlite3.connect(":memory:")
    selected = {}

    class EventSelected(RuntimeError):
        pass

    def prepare(event, **_kwargs):
        selected["event_id"] = event.event_id
        raise EventSelected

    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(
        mr,
        "_target_day_has_canonical_observation",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_with_world_read_only",
        lambda **_kwargs: _monitor_forecast_world_reader(world),
    )
    connections = iter((forecasts, hwm))
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_read_only",
        lambda **_kwargs: next(connections),
    )
    monkeypatch.setattr(era, "_prepare_current_global_probability_family", prepare)

    with pytest.raises(EventSelected):
        mr._build_current_global_day0_family_snapshot(
            _pos(),
            trade_conn=sqlite3.connect(":memory:"),
            decision_time=datetime(
                2026, 6, 12, 12, 0, 5, tzinfo=timezone.utc
            ),
            cached_snapshots=(),
            deadline_monotonic=time.monotonic() + 2.5,
            hwm_deadline_monotonic=time.monotonic() + 2.5,
        )

    assert selected["event_id"] == "event-1"


def test_day0_hwm_budget_starts_at_actual_prepare_handoff(monkeypatch):
    import src.engine.event_reactor_adapter as era
    import src.engine.monitor_refresh as mr
    import src.state.db as db

    world = _day0_event_connection()
    forecasts = sqlite3.connect(":memory:")
    hwm = sqlite3.connect(":memory:")
    clock = [10.0]
    observed = {}

    class HandoffObserved(RuntimeError):
        pass

    def prepare(*_args, **kwargs):
        clock[0] = 12.0
        observed["deadline"] = kwargs["before_raw_input_hwm_read"]()
        raise HandoffObserved

    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(mr, "_target_day_has_canonical_observation", lambda *_a, **_k: False)
    monkeypatch.setattr(mr.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_with_world_read_only",
        lambda **_kwargs: _monitor_forecast_world_reader(world),
    )
    connections = iter((forecasts, hwm))
    observed_connection_deadlines = []

    def forecasts_connection(*, deadline_monotonic=None):
        if deadline_monotonic is not None:
            observed_connection_deadlines.append(deadline_monotonic)
        return next(connections)

    monkeypatch.setattr(db, "get_forecasts_connection_read_only", forecasts_connection)
    monkeypatch.setattr(era, "_prepare_current_global_probability_family", prepare)

    with pytest.raises(HandoffObserved):
        mr._build_current_global_day0_family_snapshot(
            _pos(),
            trade_conn=sqlite3.connect(":memory:"),
            decision_time=datetime(2026, 6, 12, 12, tzinfo=timezone.utc),
            cached_snapshots=(),
            deadline_monotonic=20.0,
            hwm_deadline_monotonic=20.0,
        )

    assert observed_connection_deadlines == [pytest.approx(12.5)]
    assert observed["deadline"] == pytest.approx(12.5)


def test_day0_pinned_complete_route_skips_raw_hwm_handoff(monkeypatch):
    import src.data.replacement_forecast_bundle_reader as bundle_reader
    import src.engine.event_reactor_adapter as era
    import src.engine.monitor_refresh as mr
    import src.state.db as db
    from src.data.day0_hourly_vectors import build_day0_causal_evidence_bundle

    world = _day0_event_connection()
    forecasts = sqlite3.connect(":memory:")
    vector_witness = {
        "vector_ids_by_model": {"ecmwf_ifs": "vector-1"},
    }
    causal_bundle = build_day0_causal_evidence_bundle(
        city="Karachi",
        target_date="2026-06-12",
        metric="high",
        observation_context={
            "source": "aviationweather_metar",
            "observed_extreme_native": 34.0,
        },
        cutoff_utc="2026-06-12T12:00:00+00:00",
        vector_witness=vector_witness,
    )
    pinned_bundle = SimpleNamespace(
        posterior_id="complete-00",
        provenance_json={
            "day0_provisional_observation": {
                "active": True,
                "metric": "high",
                "source": "aviationweather_metar",
                "observation_time": "2026-06-12T12:00:00+00:00",
                "observed_extreme_c": 34.0,
                "unit": "C",
            },
            "day0_remaining_vector_witness": vector_witness,
            "day0_causal_evidence_bundle": causal_bundle,
        },
    )
    world.execute(
        "UPDATE opportunity_events SET payload_json = ? WHERE event_id = 'event-1'",
        (
            json.dumps(
                {
                    "city": "Karachi",
                    "target_date": "2026-06-12",
                    "metric": "high",
                    "settlement_source": "aviationweather_metar",
                    "observation_time": "2026-06-12T12:00:00+00:00",
                    "raw_value": 34.0,
                    "settlement_unit": "C",
                }
            ),
        ),
    )
    observed = {"forecast_connections": 0, "hwm_connections": 0}

    class PinnedRoutePrepared(RuntimeError):
        pass

    def forecasts_connection(*, deadline_monotonic=None):
        observed["forecast_connections"] += 1
        if deadline_monotonic is not None:
            observed["hwm_connections"] += 1
        return forecasts

    def prepare(*_args, **kwargs):
        assert kwargs["pinned_complete_bundle"] is pinned_bundle
        assert kwargs["raw_input_hwm_conn"] is None
        assert kwargs["raw_input_hwm_read_max_seconds"] is None
        assert kwargs["before_raw_input_hwm_read"] is None
        raise PinnedRoutePrepared

    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(mr, "_target_day_has_canonical_observation", lambda *_a, **_k: False)
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_with_world_read_only",
        lambda **_kwargs: _monitor_forecast_world_reader(world),
    )
    monkeypatch.setattr(db, "get_forecasts_connection_read_only", forecasts_connection)
    monkeypatch.setattr(
        bundle_reader,
        "read_prior_complete_replacement_forecast_bundle",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="READY", ok=True, bundle=pinned_bundle
        ),
    )
    monkeypatch.setattr(era, "_prepare_current_global_probability_family", prepare)

    with pytest.raises(PinnedRoutePrepared):
        mr._build_current_global_day0_family_snapshot(
            _pos(),
            trade_conn=sqlite3.connect(":memory:"),
            decision_time=datetime(2026, 6, 12, 12, tzinfo=timezone.utc),
            cached_snapshots=(),
            deadline_monotonic=time.monotonic() + 2.5,
            hwm_deadline_monotonic=time.monotonic() + 2.5,
        )

    assert observed == {"forecast_connections": 0, "hwm_connections": 0}


def test_day0_newer_observation_reseeds_instead_of_pinning_prior_carrier(monkeypatch):
    """A t1 carrier must not make a t2 Day0 event look fresh."""
    import src.data.replacement_forecast_bundle_reader as bundle_reader
    import src.engine.event_reactor_adapter as era
    import src.engine.monitor_refresh as mr
    import src.state.db as db

    world = _day0_event_connection()
    hwm = sqlite3.connect(":memory:")
    world.execute(
        "UPDATE opportunity_events SET payload_json = ? WHERE event_id = 'event-1'",
        (
            json.dumps(
                {
                    "city": "Karachi",
                    "target_date": "2026-06-12",
                    "metric": "high",
                    "settlement_source": "aviationweather_metar",
                    "observation_time": "2026-06-12T12:00:00+00:00",
                    "raw_value": 34.0,
                    "settlement_unit": "C",
                }
            ),
        ),
    )
    prior_bundle = SimpleNamespace(
        posterior_id="t1",
        provenance_json={
            "day0_provisional_observation": {
                "active": True,
                "metric": "high",
                "source": "aviationweather_metar",
                "observation_time": "2026-06-12T11:00:00+00:00",
                "observed_extreme_c": 33.0,
                "unit": "C",
            }
        },
    )
    observed = {}

    class CurrentAuthorityPrepared(RuntimeError):
        pass

    def prepare(*_args, **kwargs):
        observed["pinned"] = kwargs["pinned_complete_bundle"]
        observed["hwm"] = kwargs["raw_input_hwm_conn"]
        observed["hwm_callback"] = kwargs["before_raw_input_hwm_read"]
        raise CurrentAuthorityPrepared

    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(mr, "_target_day_has_canonical_observation", lambda *_a, **_k: False)
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_with_world_read_only",
        lambda **_kwargs: _monitor_forecast_world_reader(world),
    )
    monkeypatch.setattr(db, "get_forecasts_connection_read_only", lambda **_kwargs: hwm)
    monkeypatch.setattr(
        bundle_reader,
        "read_prior_complete_replacement_forecast_bundle",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="READY", ok=True, bundle=prior_bundle
        ),
    )
    monkeypatch.setattr(era, "_prepare_current_global_probability_family", prepare)

    with pytest.raises(CurrentAuthorityPrepared):
        mr._build_current_global_day0_family_snapshot(
            _pos(),
            trade_conn=sqlite3.connect(":memory:"),
            decision_time=datetime(2026, 6, 12, 12, tzinfo=timezone.utc),
            cached_snapshots=(),
            deadline_monotonic=time.monotonic() + 2.5,
            hwm_deadline_monotonic=time.monotonic() + 2.5,
        )

    assert observed["pinned"] is None
    assert observed["hwm"] is hwm
    assert observed["hwm_callback"] is not None


@pytest.mark.parametrize(
    ("metric", "raw_value"),
    (("high", 34.0), ("low", 12.0)),
)
def test_day0_blocked_prior_station_mismatch_defers_to_current_authority(
    monkeypatch,
    metric,
    raw_value,
):
    """A rejected t1 station carrier cannot veto the t2 current-event route."""
    import src.data.replacement_forecast_bundle_reader as bundle_reader
    import src.engine.event_reactor_adapter as era
    import src.engine.monitor_refresh as mr
    import src.state.db as db

    world = _day0_event_connection()
    hwm = sqlite3.connect(":memory:")
    world.execute(
        "UPDATE opportunity_events SET payload_json = ? WHERE event_id = 'event-1'",
        (
            json.dumps(
                {
                    "city": "Karachi",
                    "target_date": "2026-06-12",
                    "metric": metric,
                    "settlement_source": "aviationweather_metar",
                    "observation_time": "2026-06-12T12:00:00+00:00",
                    "raw_value": raw_value,
                    "settlement_unit": "C",
                }
            ),
        ),
    )
    observed = {"reader_calls": 0}

    class CurrentAuthorityPrepared(RuntimeError):
        pass

    def read_prior(*_args, **_kwargs):
        observed["reader_calls"] += 1
        return SimpleNamespace(
            status="BLOCKED",
            ok=False,
            bundle=None,
            reason_code="REPLACEMENT_PINNED_DAY0_SOURCE_STATION_MISMATCH",
        )

    def prepare(*_args, **kwargs):
        observed["pinned"] = kwargs["pinned_complete_bundle"]
        observed["hwm"] = kwargs["raw_input_hwm_conn"]
        raise CurrentAuthorityPrepared

    position = _pos()
    position.temperature_metric = metric
    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(mr, "_target_day_has_canonical_observation", lambda *_a, **_k: False)
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_with_world_read_only",
        lambda **_kwargs: _monitor_forecast_world_reader(world),
    )
    monkeypatch.setattr(db, "get_forecasts_connection_read_only", lambda **_kwargs: hwm)
    monkeypatch.setattr(
        bundle_reader,
        "read_prior_complete_replacement_forecast_bundle",
        read_prior,
    )
    monkeypatch.setattr(era, "_prepare_current_global_probability_family", prepare)

    with pytest.raises(CurrentAuthorityPrepared):
        mr._build_current_global_day0_family_snapshot(
            position,
            trade_conn=sqlite3.connect(":memory:"),
            decision_time=datetime(2026, 6, 12, 12, tzinfo=timezone.utc),
            cached_snapshots=(),
            deadline_monotonic=time.monotonic() + 2.5,
            hwm_deadline_monotonic=time.monotonic() + 2.5,
        )

    assert observed == {
        "reader_calls": 2,
        "pinned": None,
        "hwm": hwm,
    }


def test_day0_blocked_prior_station_mismatch_keeps_unmaterialized_t2_closed(
    monkeypatch,
):
    """No t2 posterior means a fail-closed error, never the rejected t1 q."""
    import src.data.replacement_forecast_bundle_reader as bundle_reader
    import src.engine.event_reactor_adapter as era
    import src.engine.monitor_refresh as mr
    import src.state.db as db

    world = _day0_event_connection()
    hwm = sqlite3.connect(":memory:")
    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(mr, "_target_day_has_canonical_observation", lambda *_a, **_k: False)
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_with_world_read_only",
        lambda **_kwargs: _monitor_forecast_world_reader(world),
    )
    monkeypatch.setattr(db, "get_forecasts_connection_read_only", lambda **_kwargs: hwm)
    monkeypatch.setattr(
        bundle_reader,
        "read_prior_complete_replacement_forecast_bundle",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="BLOCKED",
            ok=False,
            bundle=None,
            reason_code="REPLACEMENT_PINNED_DAY0_SOURCE_STATION_MISMATCH",
        ),
    )
    monkeypatch.setattr(
        era,
        "_prepare_current_global_probability_family",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("GLOBAL_DAY0_PROVISIONAL_POSTERIOR_IDENTITY_MISSING")
        ),
    )

    with pytest.raises(
        ValueError, match="GLOBAL_DAY0_PROVISIONAL_POSTERIOR_IDENTITY_MISSING"
    ):
        mr._build_current_global_day0_family_snapshot(
            _pos(),
            trade_conn=sqlite3.connect(":memory:"),
            decision_time=datetime(2026, 6, 12, 12, tzinfo=timezone.utc),
            cached_snapshots=(),
            deadline_monotonic=time.monotonic() + 2.5,
            hwm_deadline_monotonic=time.monotonic() + 2.5,
        )


def test_day0_corrupt_pinned_carrier_block_remains_fail_closed(monkeypatch):
    """Reader integrity failures must not be downgraded with station mismatch."""
    import src.data.replacement_forecast_bundle_reader as bundle_reader
    import src.engine.monitor_refresh as mr
    import src.state.db as db

    world = _day0_event_connection()
    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(mr, "_target_day_has_canonical_observation", lambda *_a, **_k: False)
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_with_world_read_only",
        lambda **_kwargs: _monitor_forecast_world_reader(world),
    )
    monkeypatch.setattr(
        bundle_reader,
        "read_prior_complete_replacement_forecast_bundle",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="BLOCKED",
            ok=False,
            bundle=None,
            reason_code="REPLACEMENT_PINNED_DAY0_CARRIER_SHAPE_INVALID",
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            "GLOBAL_HELD_PINNED_COMPLETE_POSTERIOR_BLOCKED:"
            "REPLACEMENT_PINNED_DAY0_CARRIER_SHAPE_INVALID"
        ),
    ):
        mr._build_current_global_day0_family_snapshot(
            _pos(),
            trade_conn=sqlite3.connect(":memory:"),
            decision_time=datetime(2026, 6, 12, 12, tzinfo=timezone.utc),
            cached_snapshots=(),
            deadline_monotonic=time.monotonic() + 2.5,
            hwm_deadline_monotonic=time.monotonic() + 2.5,
        )


@pytest.mark.parametrize("probability_use", ["HELD_MONITOR", "REDUCE_ONLY_EXIT"])
def test_day0_pinned_current_local_day_requires_hwm_station_witness(
    monkeypatch,
    probability_use,
):
    """A pinned held carrier is complete on the current local day too.

    The persisted carrier has already passed the bounded raw-input HWM and
    station/source-pair gate.  Both held uses retain that immutable identity;
    neither route may silently substitute an unbound local fixture.
    """
    import src.data.replacement_forecast_bundle_reader as bundle_reader
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_readiness as readiness_reader
    import src.engine.event_reactor_adapter as era
    import src.engine.qkernel_spine_bridge as qkernel
    import src.execution.day0_hard_fact_exit as day0_hard_fact_exit
    import src.solve.solver as solver

    candidates = (
        SimpleNamespace(
            condition_id="condition-33",
            yes_token_id="yes-33",
            no_token_id="no-33",
            bin=SimpleNamespace(low=33.0, high=None, unit="C", label="33°C+"),
        ),
        SimpleNamespace(
            condition_id="condition-32",
            yes_token_id="yes-32",
            no_token_id="no-32",
            bin=SimpleNamespace(low=32.0, high=32.0, unit="C", label="32°C"),
        ),
    )
    family = SimpleNamespace(
        city="Tel Aviv",
        target_date="2026-06-09",
        metric="high",
        family_id="Tel Aviv|2026-06-09|high",
        binding_hash="family-binding",
        candidates=candidates,
    )
    observation_time = "2026-06-09T10:00:00+00:00"
    fact = {
        "observation_source": "aviationweather_metar",
        "observation_time": observation_time,
        "observation_available_at": observation_time,
        "observed_extreme_native": 33.0,
        "unit": "C",
        "source": "aviationweather_metar",
        "station_id": "LLBG",
    }
    event = SimpleNamespace(
        event_id="event-pinned-current-day",
        event_type="DAY0_EXTREME_UPDATED",
        causal_snapshot_id="snapshot-pinned-current-day",
        payload_json=json.dumps(
            {
                "city": "Tel Aviv",
                "target_date": "2026-06-09",
                "metric": "high",
                "unit": "C",
                "settlement_source": "aviationweather_metar",
                "observation_time": observation_time,
                "rounded_value": 33,
                "source_authorized_status": "AUTHORIZED",
                "live_authority_status": "live",
            }
        ),
    )
    likelihood = {
        "semantics": "same_station_preliminary_report_survival_likelihood_v1",
        "cutoff": observation_time,
        "successes": [],
        "failures": [],
        "unconfirmed_awc_ids": [1],
        "alpha": 0.5,
        "beta": 0.5,
        "station_id": "LLBG",
        "source_channel_pair": {
            "awc": "aviationweather_metar",
            "ogimet": "ogimet_metar_llbg",
        },
    }
    likelihood["identity_hash"] = hashlib.sha256(
        json.dumps(likelihood, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    pinned_provenance = {
        "day0_provisional_observation": {
            "active": True,
            "metric": "high",
            "unit": "C",
            "source": "aviationweather_metar",
        },
        "day0_preliminary_report_survival_likelihood": likelihood,
        "day0_remaining_carrier_content_identity": "carrier-content-hash",
        "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_v1",
        "day0_remaining_carrier_q": [0.0, 1.0],
        "day0_remaining_carrier_probability_samples": [[0.0, 1.0]] * 500,
        "day0_remaining_carrier_sample_count": 500,
        "day0_remaining_carrier_future_extremes_c": [20.0, 21.0],
        "day0_remaining_carrier_path_error_sigma_c": 0.5,
        "day0_remaining_carrier_probability_cutoff_utc": observation_time,
        "day0_remaining_vector_witness": {
            "vector_id": "vector-id-1",
            "expected_models": ["ecmwf_ifs"],
            "actual_models": ["ecmwf_ifs"],
            "capture_times_by_model_utc": {"ecmwf_ifs": observation_time},
            "provider_source_cycle_time_by_model_utc": {"ecmwf_ifs": observation_time},
            "provider_source_available_at_by_model_utc": {"ecmwf_ifs": observation_time},
            "source_run_id_by_model": {"ecmwf_ifs": "source-run-1"},
            "provider_run_id_by_model": {"ecmwf_ifs": "provider-run-1"},
            "request_hash_by_model": {"ecmwf_ifs": "request-hash-1"},
        },
        "bayes_precision_fusion": {
            "current_evidence_shape": {
                "source_cycle_time": observation_time,
                "member_values_hash": "member-values-hash",
            },
            "current_value_serving": {
                "ecmwf_ifs": {
                    "raw_model_forecast_id": "raw-ifs-1",
                    "served_cycle": observation_time,
                    "captured_at": observation_time,
                }
            },
        },
    }
    pinned_provenance.update(
        {
            "q_bootstrap_samples_basis": "global_simplex_v1",
            "q_bootstrap_samples_by_bin": {
                "bin-33": [0.0] * 500,
                "bin-32": [1.0] * 500,
            },
            "bin_topology": [
                {"bin_id": "bin-33", "lower_c": 33.0, "upper_c": None},
                {"bin_id": "bin-32", "lower_c": 32.0, "upper_c": 32.0},
            ],
        }
    )
    pinned_bundle = SimpleNamespace(
        posterior_id=123,
        posterior_identity_hash="pinned-posterior-identity",
        dependency_hash="pinned-dependency",
        posterior_config_hash="pinned-config",
        q={"bin-33": 0.0, "bin-32": 1.0},
        source_cycle_time="2026-06-09T00:00:00+00:00",
        source_available_at="2026-06-09T06:00:00+00:00",
        provenance_json=pinned_provenance,
    )
    observed = {
        "hwm_callback": 0,
        "generic_reader": 0,
        "current_day0_facts": None,
    }

    class _Bound:
        def evaluate(self, _request):
            return SimpleNamespace(
                status="CANDIDATE_FAMILY_READY",
                candidate_family=family,
            )

    class _Witness:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def fail_generic_reader(*_args, **_kwargs):
        observed["generic_reader"] += 1
        raise AssertionError("pinned route reached generic replacement reader")

    def fail_hwm_callback():
        observed["hwm_callback"] += 1
        raise AssertionError("pinned route invoked HWM callback")

    monkeypatch.setattr(era, "EventBoundDecisionEngine", _Bound)
    monkeypatch.setattr(era, "_event_family_market_topology_rows", lambda *_: [0, 1])
    monkeypatch.setattr(
        era,
        "_topology_candidate_from_market_event",
        lambda row, *_: candidates[int(row)],
    )
    monkeypatch.setattr(
        bundle_reader,
        "market_bin_topology_hash_from_rows",
        lambda *_args, **_kwargs: "topology-hash",
    )
    monkeypatch.setattr(
        era,
        "runtime_cities_by_name",
        lambda: {
            "Tel Aviv": SimpleNamespace(
                timezone="Asia/Jerusalem",
                settlement_unit="C",
                settlement_source_type="noaa",
                wu_station="LLBG",
            )
        },
    )
    monkeypatch.setattr(day0_hard_fact_exit, "_final_daily_observation_extreme", lambda **_: None)
    monkeypatch.setattr(target_plan, "_latest_authorized_day0_fact", lambda *_a, **_k: fact)
    def capture_global_day0_payload(*_args, **kwargs):
        observed["current_day0_facts"] = kwargs.get("current_day0_facts")
        return {
            "_edli_global_day0_binding": {"observation_time": observation_time},
            "settlement_source": "aviationweather_metar",
            "observation_time": observation_time,
            "observed_extreme_native": 33.0,
            "rounded_value": 33.0,
            "evidence_finality": "MONOTONE_SETTLEMENT_BOUND",
            "settlement_unit": "C",
            "_edli_day0_remaining_vector_witness": pinned_provenance[
                "day0_remaining_vector_witness"
            ],
            "_edli_day0_source_clock_carrier_provenance": {
                "posterior_identity_hash": pinned_bundle.posterior_identity_hash,
                "source_cycle_time": pinned_bundle.source_cycle_time,
            },
        }

    monkeypatch.setattr(
        era,
        "_global_day0_execution_payload",
        capture_global_day0_payload,
    )
    monkeypatch.setattr(
        era,
        "_replacement_global_probability_components",
        lambda *_args, **_kwargs: (
            np.asarray([[0.0, 1.0], [0.0, 1.0]], dtype=float),
            np.asarray([0.0, 1.0], dtype=float),
            "pinned-basis",
        ),
    )
    monkeypatch.setattr(era, "_day0_global_candidate_payoff_q_lcb_caps", lambda **_: ())
    monkeypatch.setattr(era, "_day0_payoff_truth_rows", lambda **_: ())
    monkeypatch.setattr(era, "_amber_inflated_predictive_sigma_c", lambda *_a, **_k: 1.0)
    monkeypatch.setattr(qkernel, "build_forecast_case", lambda *_a, **_k: object())
    monkeypatch.setattr(
        qkernel,
        "build_outcome_space",
        lambda *_a, **_k: SimpleNamespace(
            resolution=SimpleNamespace(measurement_unit="C"),
            bins=tuple(
                SimpleNamespace(
                    bin_id=f"bin-{value}",
                    condition_id=f"condition-{value}",
                    yes_token_id=f"yes-{value}",
                    no_token_id=f"no-{value}",
                )
                for value in (33, 32)
            ),
            topology_hash="topology-hash",
        ),
    )
    monkeypatch.setattr(qkernel, "_event_resolution_identity", lambda *_: "resolution")
    monkeypatch.setattr(solver, "JointOutcomeProbabilityWitness", _Witness)
    monkeypatch.setattr(bundle_reader, "read_replacement_forecast_bundle", fail_generic_reader)
    monkeypatch.setattr(
        readiness_reader,
        "latest_replacement_readiness",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pinned route read current replacement readiness")
        ),
    )

    observation_conn = sqlite3.connect(":memory:")
    observation_conn.execute(
        "CREATE TABLE observation_instants ("
        "city TEXT, target_date TEXT, running_max REAL, utc_timestamp TEXT, "
        "local_timestamp TEXT, source TEXT, causality_status TEXT, authority TEXT, "
        "source_role TEXT, training_allowed INTEGER)"
    )
    payload_out = {}
    prepared = era._prepare_current_global_probability_family(
        event,
        forecast_conn=observation_conn,
        topology_conn=observation_conn,
        observation_conn=observation_conn,
        decision_time=datetime(2026, 6, 9, 12, tzinfo=timezone.utc),
        max_age=timedelta(hours=2),
        day0_payload_out=payload_out,
        probability_use=getattr(era._CurrentProbabilityUse, probability_use),
        raw_input_hwm_conn=None,
        raw_input_hwm_deadline_monotonic=None,
        raw_input_hwm_read_max_seconds=None,
        before_raw_input_hwm_read=fail_hwm_callback,
        pinned_complete_bundle=pinned_bundle,
    )

    assert observed == {
        "hwm_callback": 0,
        "generic_reader": 0,
        "current_day0_facts": (fact, fact),
    }
    assert prepared.posterior_id == pinned_bundle.posterior_id
    assert isinstance(
        prepared.probability_witness,
        solver.DeterministicBinPayoffWitness,
    )
    assert prepared.probability_witness.exact_yes_payoffs == (
        ("bin-32", 0),
        ("bin-33", 1),
    )
    assert prepared.probability_witness.posterior_identity_hash != (
        pinned_bundle.posterior_identity_hash
    )
    assert payload_out["_edli_day0_held_pinned_posterior_identity"] == (
        pinned_bundle.posterior_identity_hash
    )
    assert payload_out["_edli_day0_remaining_vector_witness"]["vector_id"] == (
        "vector-id-1"
    )
    assert payload_out["_edli_day0_source_clock_carrier_provenance"][
        "posterior_identity_hash"
    ] == pinned_bundle.posterior_identity_hash
    assert pinned_bundle.provenance_json["day0_preliminary_report_survival_likelihood"][
        "station_id"
    ] == "LLBG"
    assert len(
        pinned_bundle.provenance_json["day0_remaining_carrier_probability_samples"]
    ) == 500


def _post_local_day0_family_common_setup(monkeypatch, *, fact):
    """Shared scaffolding for the post-local-day METAR/no-fact finality tests.

    Both tests drive the FRESH (non-pinned) `_prepare_current_global_probability_family`
    path for a HELD_MONITOR position whose target local day has already ended
    (`family.target_date` is one day before `decision_time`'s São Paulo local date).
    `fact` is the mocked `_latest_authorized_day0_fact` return value (a METAR dict,
    or None for the no-observation/London shape).
    """
    import src.data.replacement_forecast_bundle_reader as bundle_reader
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_readiness as readiness_reader
    import src.engine.event_reactor_adapter as era
    import src.engine.qkernel_spine_bridge as qkernel
    import src.execution.day0_hard_fact_exit as day0_hard_fact_exit

    candidates = (
        SimpleNamespace(
            condition_id="condition-30",
            yes_token_id="yes-30",
            no_token_id="no-30",
            bin=SimpleNamespace(low=None, high=30.0, unit="C", label="<30C"),
        ),
        SimpleNamespace(
            condition_id="condition-30plus",
            yes_token_id="yes-30plus",
            no_token_id="no-30plus",
            bin=SimpleNamespace(low=30.0, high=None, unit="C", label="30C+"),
        ),
    )
    family = SimpleNamespace(
        city="Sao Paulo",
        target_date="2026-06-08",
        metric="high",
        family_id="Sao Paulo|2026-06-08|high",
        binding_hash="family-binding-sp",
        candidates=candidates,
    )
    event = SimpleNamespace(
        event_id="event-post-local-sp",
        event_type="DAY0_EXTREME_UPDATED",
        causal_snapshot_id="snapshot-post-local-sp",
        payload_json=json.dumps(
            {
                "city": "Sao Paulo",
                "target_date": "2026-06-08",
                "metric": "high",
            }
        ),
    )

    class _Bound:
        def evaluate(self, _request):
            return SimpleNamespace(
                status="CANDIDATE_FAMILY_READY",
                candidate_family=family,
            )

    monkeypatch.setattr(era, "EventBoundDecisionEngine", _Bound)
    monkeypatch.setattr(era, "_event_family_market_topology_rows", lambda *_: [0, 1])
    monkeypatch.setattr(
        era,
        "_topology_candidate_from_market_event",
        lambda row, *_: candidates[int(row)],
    )
    monkeypatch.setattr(
        bundle_reader,
        "market_bin_topology_hash_from_rows",
        lambda *_args, **_kwargs: "topology-hash",
    )
    monkeypatch.setattr(
        era,
        "runtime_cities_by_name",
        lambda: {
            "Sao Paulo": SimpleNamespace(
                timezone="America/Sao_Paulo",
                settlement_unit="C",
                settlement_source_type="wu",
                wu_station="SBSP",
            )
        },
    )
    monkeypatch.setattr(
        day0_hard_fact_exit, "_final_daily_observation_extreme", lambda **_: None
    )
    monkeypatch.setattr(target_plan, "_latest_authorized_day0_fact", lambda *_a, **_k: fact)
    monkeypatch.setattr(
        readiness_reader, "latest_replacement_readiness", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        qkernel,
        "build_forecast_case",
        lambda *_a, **_k: object(),
    )
    monkeypatch.setattr(
        qkernel,
        "build_outcome_space",
        lambda *_a, **_k: SimpleNamespace(
            resolution=SimpleNamespace(measurement_unit="C"),
            bins=tuple(
                SimpleNamespace(
                    bin_id=candidate.condition_id,
                    condition_id=candidate.condition_id,
                    yes_token_id=candidate.yes_token_id,
                    no_token_id=candidate.no_token_id,
                )
                for candidate in candidates
            ),
            topology_hash="topology-hash",
        ),
    )
    monkeypatch.setattr(qkernel, "_event_resolution_identity", lambda *_: "resolution")
    monkeypatch.setattr(era, "_day0_global_candidate_payoff_q_lcb_caps", lambda **_: ())
    monkeypatch.setattr(era, "_day0_payoff_truth_rows", lambda **_: ())

    observation_conn = sqlite3.connect(":memory:")
    observation_conn.execute(
        "CREATE TABLE observation_instants ("
        "city TEXT, target_date TEXT, running_max REAL, utc_timestamp TEXT, "
        "local_timestamp TEXT, source TEXT, causality_status TEXT, authority TEXT, "
        "source_role TEXT, training_allowed INTEGER)"
    )
    decision_time = datetime(2026, 6, 10, 2, 0, tzinfo=timezone.utc)
    return era, event, observation_conn, decision_time


def test_day0_post_local_day_metar_monitor_rebuilds_remaining_carrier(monkeypatch):
    """FIX (X-BB-postday-finality): a METAR-sourced Day0 fact whose local day has
    already ended must still feed `_edli_day0_provisional_revision_likelihood`
    into the HELD_MONITOR family, exactly as it already does during the live
    local day (`_provisional_day0_revision_likelihood` -> METAR branch ->
    `same_station_preliminary_report_survival_likelihood`). Before the fix, the
    producer gate at `_prepare_current_global_probability_family` compared
    `day0_evidence_finality(...)` by strict equality to
    `DAY0_PROVISIONAL_CURRENT_SNAPSHOT` only; a METAR source always classifies
    as `DAY0_MONOTONE_SETTLEMENT_BOUND`, so the likelihood was never built and
    the family raised `DAY0_NOAA_PRELIMINARY_CARRIER_IDENTITY_MISSING` on every
    monitor cycle for a held position whose local day ended (the live symptom
    on positions 5655b06f-d10 / e8d47201-444). This test FAILS on parent
    9408a5658 with that exact ValueError and PASSES after broadening the gate
    to accept both finality values, mirroring the already-correct consumers.
    """
    observation_time = "2026-06-08T22:00:00+00:00"
    fact = {
        "observation_source": "aviationweather_metar",
        "observation_time": observation_time,
        "observation_available_at": observation_time,
        "observed_extreme_native": 29.5,
        "unit": "C",
        "source": "aviationweather_metar",
        "station_id": "SBSP",
    }
    era, event, observation_conn, decision_time = _post_local_day0_family_common_setup(
        monkeypatch, fact=fact
    )

    likelihood = {
        "semantics": "same_station_preliminary_report_survival_likelihood_v1",
        "boundary_survival_probability": 0.9,
        "identity_hash": "fake-metar-likelihood-identity",
        "station_id": "SBSP",
        "source_channel_pair": {
            "awc": "aviationweather_metar",
            "ogimet": "ogimet_metar_sbsp",
        },
    }
    monkeypatch.setattr(
        era,
        "_provisional_day0_revision_likelihood",
        lambda *_a, **_k: dict(likelihood),
    )
    monkeypatch.setattr(
        era, "_assert_day0_post_local_vector_witness", lambda *_a, **_k: None
    )

    def fake_global_day0_execution_payload(*_args, **_kwargs):
        return {
            "_edli_global_day0_binding": {"observation_time": observation_time},
            "settlement_source": "aviationweather_metar",
            "evidence_finality": "MONOTONE_SETTLEMENT_BOUND",
            "observation_time": observation_time,
            "observed_extreme_native": 29.5,
            "rounded_value": 29.5,
            "settlement_unit": "C",
            "_edli_day0_remaining_vector_witness": {"vector_id": "vector-sp-1"},
            "_edli_day0_remaining_capture_times_utc": [observation_time],
        }

    monkeypatch.setattr(
        era,
        "_global_day0_execution_payload",
        fake_global_day0_execution_payload,
    )

    def fake_remaining_components(
        _event,
        *,
        forecast_conn,
        calibration_conn,
        family,
        payload,
        decision_time,
        snapshot,
        entry_authority,
    ):
        # Reproduces, faithfully, the real `_day0_remaining_global_probability_components`
        # contract this test pins: it raises the exact live symptom string when the
        # provisional revision likelihood was never produced, and otherwise builds the
        # remaining-day carrier's content identity -- exactly what the real function
        # does via `_day0_remaining_p_raw_vector`'s required-fields check.
        likelihood_payload = payload.get("_edli_day0_provisional_revision_likelihood")
        if not isinstance(likelihood_payload, dict) or not str(
            likelihood_payload.get("identity_hash") or ""
        ):
            raise ValueError("DAY0_NOAA_PRELIMINARY_CARRIER_IDENTITY_MISSING")
        payload["_edli_day0_remaining_content_identity"] = "sp-remaining-content-hash"
        rng = np.random.default_rng(0)
        yes_column = np.clip(rng.normal(0.35, 0.05, size=500), 0.01, 0.99)
        samples = np.stack([yes_column, 1.0 - yes_column], axis=1)
        point_q = np.array([0.35, 0.65], dtype=np.float64)
        return samples, point_q, "sp-post-local-band-basis"

    monkeypatch.setattr(
        era,
        "_day0_remaining_global_probability_components",
        fake_remaining_components,
    )

    payload_out: dict[str, object] = {}
    prepared = era._prepare_current_global_probability_family(
        event,
        forecast_conn=observation_conn,
        topology_conn=observation_conn,
        observation_conn=observation_conn,
        decision_time=decision_time,
        max_age=timedelta(hours=6),
        day0_payload_out=payload_out,
        allow_provisional_day0_replacement=True,
        probability_use=era._CurrentProbabilityUse.HELD_MONITOR,
    )

    assert prepared.probability_witness is not None
    assert payload_out["_edli_day0_remaining_content_identity"] == (
        "sp-remaining-content-hash"
    )
    assert payload_out["_edli_day0_provisional_revision_likelihood"][
        "identity_hash"
    ] == "fake-metar-likelihood-identity"


def test_day0_post_local_day_without_observation_still_waits(monkeypatch):
    """The DESIGNED terminal state must not regress: a post-local-day family with
    NO provisional settlement-channel fact at all (the London shape) still raises
    `POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE` after the finality-gate fix.
    """
    import src.engine.event_reactor_adapter as era

    era, event, observation_conn, decision_time = _post_local_day0_family_common_setup(
        monkeypatch, fact=None
    )

    with pytest.raises(
        ValueError, match="POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE"
    ):
        era._prepare_current_global_probability_family(
            event,
            forecast_conn=observation_conn,
            topology_conn=observation_conn,
            observation_conn=observation_conn,
            decision_time=decision_time,
            max_age=timedelta(hours=6),
            allow_provisional_day0_replacement=True,
            probability_use=era._CurrentProbabilityUse.HELD_MONITOR,
        )


def test_day0_live_entry_metar_admission_unaffected_by_held_finality_broadening(
    monkeypatch,
):
    """R-BB review of a9a2c139f (FIX-FIRST): the finality broadening for METAR/
    monotone sources must stay scoped to the held continuation path
    (`current_day0_redecision_only` / `post_local_incomplete_monitor_authority`,
    both structurally False whenever `probability_use is ENTRY`). It must NOT
    also change live intraday ENTRY admission for the same METAR fact. This
    pins the three consumers R-BB flagged as byte-identical to parent 9408a5658
    for a live-day (not post-local-day) ENTRY with a METAR settlement-channel
    fact and a mainline replacement bundle present:

    1. `candidate_payoff_q_lcb_caps` stays populated (not suppressed) --
       `provisional_day0_observation` is still `False` for METAR (unchanged
       narrow `==` comparison), so `not provisional_day0_observation` still
       holds at the caps-assignment site.
    2. The bundle-bypass / posterior-identity recompute condition still fires
       (`not provisional_day0_observation` alone already satisfied this before
       the fix; unchanged).
    3. `_edli_day0_provisional_revision_likelihood` is NOT stamped into the
       payload for this live entry -- proving the new
       `settlement_bound_day0_observation` branch at the carrier-rebuild
       producer is correctly gated to the held path and never fires here.

    A second scenario in the same test (`readiness is None`, the degraded
    fallback arm) proves the `direct_day0_entry_carrier` branch stays dead for
    METAR entries exactly as it did on parent -- it requires
    `provisional_day0_observation` (unchanged, still False for METAR), never
    `settlement_bound_day0_observation`.
    """
    import src.data.replacement_forecast_bundle_reader as bundle_reader
    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_readiness as readiness_reader
    import src.engine.event_reactor_adapter as era
    import src.engine.qkernel_spine_bridge as qkernel
    import src.execution.day0_hard_fact_exit as day0_hard_fact_exit

    candidates = (
        SimpleNamespace(
            condition_id="condition-30",
            yes_token_id="yes-30",
            no_token_id="no-30",
            bin=SimpleNamespace(low=None, high=30.0, unit="C", label="<30C"),
        ),
        SimpleNamespace(
            condition_id="condition-30plus",
            yes_token_id="yes-30plus",
            no_token_id="no-30plus",
            bin=SimpleNamespace(low=30.0, high=None, unit="C", label="30C+"),
        ),
    )
    family = SimpleNamespace(
        city="Sao Paulo",
        target_date="2026-06-08",
        metric="high",
        family_id="Sao Paulo|2026-06-08|high",
        binding_hash="family-binding-sp-entry",
        candidates=candidates,
    )
    event = SimpleNamespace(
        event_id="event-live-entry-sp",
        event_type="DAY0_EXTREME_UPDATED",
        causal_snapshot_id="snapshot-live-entry-sp",
        payload_json=json.dumps(
            {"city": "Sao Paulo", "target_date": "2026-06-08", "metric": "high"}
        ),
    )
    observation_time = "2026-06-08T15:00:00+00:00"
    fact = {
        "observation_source": "aviationweather_metar",
        "observation_time": observation_time,
        "observation_available_at": observation_time,
        "observed_extreme_native": 29.5,
        "unit": "C",
        "source": "aviationweather_metar",
        "station_id": "SBSP",
    }
    # Same local day as decision_time (LIVE, not post-local-day).
    decision_time = datetime(2026, 6, 8, 18, 0, tzinfo=timezone.utc)

    class _Bound:
        def evaluate(self, _request):
            return SimpleNamespace(
                status="CANDIDATE_FAMILY_READY",
                candidate_family=family,
            )

    monkeypatch.setattr(era, "EventBoundDecisionEngine", _Bound)
    monkeypatch.setattr(era, "_event_family_market_topology_rows", lambda *_: [0, 1])
    monkeypatch.setattr(
        era,
        "_topology_candidate_from_market_event",
        lambda row, *_: candidates[int(row)],
    )
    monkeypatch.setattr(
        bundle_reader,
        "market_bin_topology_hash_from_rows",
        lambda *_args, **_kwargs: "topology-hash",
    )
    monkeypatch.setattr(
        era,
        "runtime_cities_by_name",
        lambda: {
            "Sao Paulo": SimpleNamespace(
                timezone="America/Sao_Paulo",
                settlement_unit="C",
                settlement_source_type="wu",
                wu_station="SBSP",
            )
        },
    )
    monkeypatch.setattr(
        day0_hard_fact_exit, "_final_daily_observation_extreme", lambda **_: None
    )
    monkeypatch.setattr(target_plan, "_latest_authorized_day0_fact", lambda *_a, **_k: fact)
    monkeypatch.setattr(
        qkernel, "build_forecast_case", lambda *_a, **_k: object()
    )
    monkeypatch.setattr(
        qkernel,
        "build_outcome_space",
        lambda *_a, **_k: SimpleNamespace(
            resolution=SimpleNamespace(measurement_unit="C"),
            bins=tuple(
                SimpleNamespace(
                    bin_id=candidate.condition_id,
                    condition_id=candidate.condition_id,
                    yes_token_id=candidate.yes_token_id,
                    no_token_id=candidate.no_token_id,
                )
                for candidate in candidates
            ),
            topology_hash="topology-hash",
        ),
    )
    monkeypatch.setattr(qkernel, "_event_resolution_identity", lambda *_: "resolution")
    monkeypatch.setattr(
        era, "_day0_payoff_truth_rows", lambda **_: ()
    )
    monkeypatch.setattr(
        era, "_day0_physical_frontier_supersedes_settlement", lambda **_: False
    )

    observation_conn = sqlite3.connect(":memory:")
    observation_conn.execute(
        "CREATE TABLE observation_instants ("
        "city TEXT, target_date TEXT, running_max REAL, utc_timestamp TEXT, "
        "local_timestamp TEXT, source TEXT, causality_status TEXT, authority TEXT, "
        "source_role TEXT, training_allowed INTEGER)"
    )

    # --- Scenario 1: mainline bundle-present ENTRY path ---
    bundle = SimpleNamespace(
        posterior_identity_hash="bundle-posterior-identity",
        dependency_hash="bundle-dependency",
        posterior_config_hash="bundle-config",
        source_cycle_time="2026-06-08T12:00:00+00:00",
        source_available_at="2026-06-08T12:05:00+00:00",
        posterior_id=99,
    )
    monkeypatch.setattr(
        readiness_reader,
        "latest_replacement_readiness",
        lambda *_a, **_k: SimpleNamespace(dependency_json={}),
    )
    monkeypatch.setattr(
        bundle_reader,
        "read_replacement_forecast_bundle",
        lambda *_a, **_k: SimpleNamespace(ok=True, bundle=bundle, reason_code=None),
    )
    monkeypatch.setattr(
        era, "_replacement_uses_provisional_day0_conditioning", lambda *_a, **_k: False
    )
    monkeypatch.setattr(
        era, "_fast_residual_day0_conditioning", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        era, "_day0_replacement_conditioning", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        era, "_amber_inflated_predictive_sigma_c", lambda *_a, **_k: 1.0
    )

    def fake_global_day0_execution_payload(*_args, **_kwargs):
        return {
            "_edli_global_day0_binding": {"observation_time": observation_time},
            "settlement_source": "aviationweather_metar",
            "evidence_finality": "MONOTONE_SETTLEMENT_BOUND",
            "observation_time": observation_time,
            "observed_extreme_native": 29.5,
            "rounded_value": 29.5,
            "settlement_unit": "C",
        }

    monkeypatch.setattr(
        era, "_global_day0_execution_payload", fake_global_day0_execution_payload
    )

    samples = np.array(
        [[0.4, 0.6], [0.42, 0.58], [0.38, 0.62]] * 200, dtype=np.float64
    )
    point_q = np.array([0.4, 0.6], dtype=np.float64)
    monkeypatch.setattr(
        era,
        "_day0_absorbing_exact_probability_components",
        lambda **_k: (samples, point_q, "sp-entry-band-basis"),
    )

    def fail_remaining_components(*_a, **_k):
        raise AssertionError(
            "live METAR entry with an absorbing exact result reached the "
            "remaining-day builder -- the exact-components mock should have "
            "short-circuited it"
        )

    monkeypatch.setattr(
        era, "_day0_remaining_global_probability_components", fail_remaining_components
    )
    monkeypatch.setattr(
        era, "_day0_global_candidate_payoff_q_lcb_caps", lambda **_k: (("f", "c", "b", "YES", 0.5),)
    )
    # Orthogonal to this fix: the unconditional exact-payoff-witness lookup near
    # the function's end runs for both entry and held, and only short-circuits
    # early when target_date != decision_time's local date. For this live-day
    # (target_date == local date) scenario it would otherwise need a full
    # `assert_absorbing_day0_payload_authority` fixture unrelated to what this
    # test pins.
    monkeypatch.setattr(
        era, "_prepare_current_day0_exact_family", lambda *_a, **_k: None
    )

    payload_out: dict[str, object] = {}
    prepared = era._prepare_current_global_probability_family(
        event,
        forecast_conn=observation_conn,
        topology_conn=observation_conn,
        observation_conn=observation_conn,
        decision_time=decision_time,
        max_age=timedelta(hours=6),
        day0_payload_out=payload_out,
        allow_provisional_day0_replacement=True,
        probability_use=era._CurrentProbabilityUse.ENTRY,
    )

    # (1) caps not suppressed -- `not provisional_day0_observation` (still False
    # for METAR, unchanged) keeps this assignment identical to parent.
    assert prepared.candidate_payoff_q_lcb_caps == (("f", "c", "b", "YES", 0.5),)
    # (2) bundle-bypass / posterior-identity recompute still fires (unchanged:
    # `not provisional_day0_observation` alone already satisfied this on parent).
    assert prepared.probability_witness.posterior_identity_hash != (
        bundle.posterior_identity_hash
    )
    # (3) the held-path-only carrier-rebuild producer must not fire for entry.
    assert "_edli_day0_provisional_revision_likelihood" not in payload_out

    # --- Scenario 2: `readiness is None` degraded fallback arm ---
    monkeypatch.setattr(
        readiness_reader, "latest_replacement_readiness", lambda *_a, **_k: None
    )

    with pytest.raises(ValueError, match="GLOBAL_CURRENT_REPLACEMENT_READINESS_MISSING"):
        era._prepare_current_global_probability_family(
            event,
            forecast_conn=observation_conn,
            topology_conn=observation_conn,
            observation_conn=observation_conn,
            decision_time=decision_time,
            max_age=timedelta(hours=6),
            allow_provisional_day0_replacement=True,
            probability_use=era._CurrentProbabilityUse.ENTRY,
        )


def test_day0_pinned_carrier_rejects_entry_authority():
    import src.engine.event_reactor_adapter as era

    with pytest.raises(ValueError, match="GLOBAL_HELD_PINNED_RECOMPUTE_ENTRY_FORBIDDEN"):
        era._prepare_current_global_probability_family(
            SimpleNamespace(event_type="DAY0_EXTREME_UPDATED"),
            forecast_conn=sqlite3.connect(":memory:"),
            topology_conn=sqlite3.connect(":memory:"),
            observation_conn=sqlite3.connect(":memory:"),
            decision_time=datetime(2026, 6, 9, 12, tzinfo=timezone.utc),
            max_age=timedelta(hours=2),
            pinned_complete_bundle=SimpleNamespace(
                posterior_identity_hash="pinned-posterior-identity",
                dependency_hash="pinned-dependency",
                posterior_config_hash="pinned-config",
            ),
        )


def test_reduce_only_actuation_rehydrates_selected_pinned_identity(monkeypatch):
    import src.data.replacement_forecast_bundle_reader as bundle_reader
    import src.engine.event_reactor_adapter as era

    event = SimpleNamespace(
        event_type="DAY0_EXTREME_UPDATED",
        payload_json=json.dumps(
            {
                "city": "Karachi",
                "target_date": "2026-06-12",
                "metric": "high",
            }
        ),
    )
    selected = SimpleNamespace(posterior_identity_hash="pinned-00-identity")
    bundle = SimpleNamespace(posterior_identity_hash="pinned-00-identity")
    monkeypatch.setattr(
        bundle_reader,
        "read_prior_complete_replacement_forecast_bundle",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="READY", ok=True, bundle=bundle
        ),
    )

    rehydrated = era._rehydrate_held_pinned_bundle_for_actuation(
        event,
        selected=selected,
        probability_use=era._CurrentProbabilityUse.REDUCE_ONLY_EXIT,
        forecast_conn=sqlite3.connect(":memory:"),
        decision_time=datetime(2026, 6, 12, 12, tzinfo=timezone.utc),
    )

    assert rehydrated is bundle


def test_reduce_only_actuation_does_not_pin_superseded_prior_identity(monkeypatch):
    """A direct current-evidence held witness must reach content revalidation."""

    import src.data.replacement_forecast_bundle_reader as bundle_reader
    import src.engine.event_reactor_adapter as era

    event = SimpleNamespace(
        event_type="DAY0_EXTREME_UPDATED",
        payload_json=json.dumps(
            {
                "city": "Taipei",
                "target_date": "2026-09-02",
                "metric": "high",
            }
        ),
    )
    selected = SimpleNamespace(
        posterior_identity_hash="held-current-evidence-identity"
    )
    prior_bundle = SimpleNamespace(
        posterior_identity_hash="superseded-prior-bundle-identity"
    )
    monkeypatch.setattr(
        bundle_reader,
        "read_prior_complete_replacement_forecast_bundle",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="READY", ok=True, bundle=prior_bundle
        ),
    )

    rehydrated = era._rehydrate_held_pinned_bundle_for_actuation(
        event,
        selected=selected,
        probability_use=era._CurrentProbabilityUse.REDUCE_ONLY_EXIT,
        forecast_conn=sqlite3.connect(":memory:"),
        decision_time=datetime(2026, 9, 2, 6, 20, tzinfo=timezone.utc),
    )

    assert rehydrated is None


def test_partial_deterministic_child_must_cover_requested_held_bin():
    """An exact sibling cannot replace the held bin's current statistical q."""

    import src.engine.event_reactor_adapter as era

    exact_sibling = (("bin-31", 0),)

    assert era._deterministic_payoffs_cover_required_bin(exact_sibling, None)
    assert era._deterministic_payoffs_cover_required_bin(
        exact_sibling,
        "bin-31",
    )
    assert not era._deterministic_payoffs_cover_required_bin(
        exact_sibling,
        "bin-32",
    )
    assert not era._deterministic_payoffs_cover_required_bin((), "bin-32")


def test_pinned_day0_overlay_rejection_conditions_surviving_joint_draws(
    monkeypatch,
):
    """Incompatible bootstrap rows cannot erase unresolved held-bin q."""

    import src.engine.event_reactor_adapter as era

    samples = np.asarray(
        (
            (1.0, 0.0, 0.0),
            (0.0, 0.6, 0.4),
            (0.0, 0.2, 0.8),
        ),
        dtype=float,
    )
    monkeypatch.setattr(
        era,
        "_replacement_global_probability_components",
        lambda *_args, **_kwargs: (
            samples,
            np.asarray((1.0, 0.0, 0.0), dtype=float),
            "pinned",
        ),
    )
    monkeypatch.setattr(
        era,
        "_day0_absorbing_mask",
        lambda **_kwargs: np.asarray((0.0, 1.0, 1.0), dtype=float),
    )
    monkeypatch.setattr(
        era,
        "_day0_deterministic_bin_payoffs",
        lambda **_kwargs: (("bin-0", 0),),
    )
    payload = {}

    conditioned, point_q, basis = era._held_pinned_day0_probability_components(
        object(),
        payload=payload,
        family=object(),
        candidates=(),
        bindings=(),
    )

    assert conditioned == pytest.approx(
        np.asarray(((0.0, 0.6, 0.4), (0.0, 0.2, 0.8)))
    )
    assert point_q == pytest.approx(np.asarray((0.0, 0.4, 0.6)))
    assert basis == era._GLOBAL_DAY0_CURRENT_SETTLEMENT_SIMPLEX_BAND_BASIS
    assert payload["_edli_day0_held_pinned_zero_support_payoffs"] == (
        ("bin-0", 0),
    )
    assert payload["_edli_day0_held_pinned_rejected_sample_count"] == 1
    assert payload["_edli_day0_held_pinned_overlay"] == (
        "authorized_monotone_day0_rejection_conditioning_v1"
    )


@pytest.mark.parametrize(
    (
        "parent_identity",
        "child_parent_identity",
        "child_payoffs",
        "expected_error",
    ),
    (
        ("parent-a", "parent-b", (("bin-32", 0), ("bin-33", 1)), True),
        ("parent-a", "parent-a", (("bin-32", 1), ("bin-33", 0)), True),
        ("parent-a", "parent-a", (("bin-32", 0), ("bin-33", 1)), False),
    ),
)
def test_reduce_only_deterministic_child_rehydrates_parent_then_revalidates(
    monkeypatch,
    parent_identity,
    child_parent_identity,
    child_payoffs,
    expected_error,
):
    """Deterministic zero-support children use the parent carrier at JIT."""

    import src.data.replacement_forecast_bundle_reader as bundle_reader
    import src.engine.event_reactor_adapter as era
    from src.engine.qkernel_spine_bridge import PreparedGlobalFamily
    from src.solve.solver import OutcomeTokenBinding

    bindings = (
        OutcomeTokenBinding("bin-32", "condition-32", "yes-32", "no-32"),
        OutcomeTokenBinding("bin-33", "condition-33", "yes-33", "no-33"),
    )
    family = SimpleNamespace(
        family_id="Tel Aviv|2026-06-09|high",
        binding_hash="family-binding",
    )

    def deterministic_child(base_identity, exact_yes_payoffs):
        witness, _payload = era._build_day0_deterministic_witness(
            event=SimpleNamespace(
                event_id="event-zero-support-jit",
                causal_snapshot_id="snapshot-zero-support-jit",
            ),
            family=family,
            omega=SimpleNamespace(topology_hash="topology"),
            bindings=bindings,
            exact_yes_payoffs=exact_yes_payoffs,
            payload={
                "_edli_day0_held_pinned_zero_support_reason": "zero-support",
            },
            current_day0_payload={
                "_edli_global_day0_binding": {
                    "observation_time": "2026-08-28T12:00:00+00:00",
                }
            },
            day0_base_identity=base_identity,
            source_cycle=datetime(2026, 8, 28, 0, tzinfo=timezone.utc),
            source_available_at="2026-08-28T06:00:00+00:00",
            resolution_identity="resolution",
            max_age=timedelta(minutes=15),
            decision_time=datetime(2026, 8, 28, 12, tzinfo=timezone.utc),
            day0_payload_out={},
        )
        return witness

    selected = deterministic_child(
        parent_identity,
        (("bin-32", 0), ("bin-33", 1)),
    )
    parent_bundle = SimpleNamespace(
        posterior_identity_hash=child_parent_identity,
    )
    monkeypatch.setattr(
        bundle_reader,
        "read_prior_complete_replacement_forecast_bundle",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="READY", ok=True, bundle=parent_bundle
        ),
    )
    monkeypatch.setattr(
        era,
        "_current_probability_use_for_global_candidate",
        lambda _candidate: era._CurrentProbabilityUse.REDUCE_ONLY_EXIT,
    )
    monkeypatch.setattr(
        era,
        "_rebind_current_actuation_probability_tokens",
        lambda witness, _selected: witness,
    )
    prepared_parent = {}

    def prepare(*_args, **kwargs):
        prepared_parent["bundle"] = kwargs["pinned_complete_bundle"]
        return PreparedGlobalFamily(
            decision_id="current-zero-support",
            probability_witness=deterministic_child(
                child_parent_identity,
                child_payoffs,
            ),
            candidate_seeds=(),
        )

    monkeypatch.setattr(era, "_prepare_current_global_probability_family", prepare)
    actuation = SimpleNamespace(
        probability_witness=selected,
        decision=SimpleNamespace(candidate=SimpleNamespace(condition_id="condition-33")),
    )
    event = SimpleNamespace(
        event_type="DAY0_EXTREME_UPDATED",
        payload_json=json.dumps(
            {"city": "Tel Aviv", "target_date": "2026-08-28", "metric": "high"}
        ),
    )

    def call():
        return era._current_global_actuation_prepared_family(
            event,
            global_actuation=actuation,
            forecast_conn=sqlite3.connect(":memory:"),
            topology_conn=sqlite3.connect(":memory:"),
            observation_conn=sqlite3.connect(":memory:"),
            decision_time=datetime(2026, 8, 28, 12, tzinfo=timezone.utc),
        )
    if expected_error:
        with pytest.raises(
            ValueError, match="GLOBAL_ACTUATION_PROBABILITY_SUPERSEDED"
        ):
            call()
    else:
        rebound, _payload = call()
        assert rebound.probability_witness is selected
    assert prepared_parent["bundle"] is parent_bundle


def test_reduce_only_statistical_child_keeps_original_parent_identity_gate(
    monkeypatch,
):
    """Normal-support statistical rehydrate retains its existing identity gate."""

    import src.data.replacement_forecast_bundle_reader as bundle_reader
    import src.engine.event_reactor_adapter as era
    from src.engine.qkernel_spine_bridge import PreparedGlobalFamily

    content = {
        field: f"statistical-{field}"
        for field in era._GLOBAL_PROBABILITY_ACTION_CONTENT_FIELDS
    }
    selected = SimpleNamespace(
        **content,
        posterior_identity_hash="statistical-parent",
        source_truth_identity="statistical-source",
        q_version="statistical-q-v1",
        bindings=(SimpleNamespace(condition_id="condition-33", bin_id="bin-33"),),
        yes_point_q=np.asarray((0.2,), dtype=np.float64),
        witness_identity="statistical-selected",
    )
    current = SimpleNamespace(**selected.__dict__)
    parent_bundle = SimpleNamespace(posterior_identity_hash="statistical-parent")
    monkeypatch.setattr(
        bundle_reader,
        "read_prior_complete_replacement_forecast_bundle",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="READY", ok=True, bundle=parent_bundle
        ),
    )
    monkeypatch.setattr(
        era,
        "_current_probability_use_for_global_candidate",
        lambda _candidate: era._CurrentProbabilityUse.REDUCE_ONLY_EXIT,
    )
    monkeypatch.setattr(
        era,
        "_rebind_current_actuation_probability_tokens",
        lambda witness, _selected: witness,
    )
    prepared_parent = {}

    def prepare(*_args, **kwargs):
        prepared_parent["bundle"] = kwargs["pinned_complete_bundle"]
        return PreparedGlobalFamily(
            decision_id="current-statistical",
            probability_witness=current,
            candidate_seeds=(),
        )

    monkeypatch.setattr(era, "_prepare_current_global_probability_family", prepare)
    rebound, _payload = era._current_global_actuation_prepared_family(
        SimpleNamespace(
            event_type="DAY0_EXTREME_UPDATED",
            payload_json=json.dumps(
                {
                    "city": "Tel Aviv",
                    "target_date": "2026-08-28",
                    "metric": "high",
                }
            ),
        ),
        global_actuation=SimpleNamespace(
            probability_witness=selected,
            decision=SimpleNamespace(candidate=SimpleNamespace(condition_id="condition-33")),
        ),
        forecast_conn=sqlite3.connect(":memory:"),
        topology_conn=sqlite3.connect(":memory:"),
        observation_conn=sqlite3.connect(":memory:"),
        decision_time=datetime(2026, 8, 28, 12, tzinfo=timezone.utc),
    )

    assert rebound.probability_witness is selected
    assert prepared_parent["bundle"] is parent_bundle


def test_day0_prepare_file_reads_do_not_wait_on_shared_snapshot_fence(
    monkeypatch,
    tmp_path,
):
    import src.engine.event_reactor_adapter as era
    import src.engine.global_auction_universe as universe
    import src.engine.monitor_refresh as mr
    import src.state.db as db

    world_memory = _day0_event_connection()
    world_path = tmp_path / "world.db"
    world_memory.commit()
    with sqlite3.connect(world_path) as target:
        world_memory.backup(target)
    world_memory.close()
    forecasts_path = tmp_path / "forecasts.db"
    sqlite3.connect(forecasts_path).close()

    opened = []

    def read_only(path):
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        opened.append(conn)
        return conn

    monkeypatch.setattr(
        db,
        "get_forecasts_connection_with_world_read_only",
        lambda **_kwargs: _monitor_forecast_world_reader(read_only(world_path)),
    )
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_read_only",
        lambda **_kwargs: read_only(forecasts_path),
    )
    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(
        mr,
        "_target_day_has_canonical_observation",
        lambda *_a, **_k: False,
    )

    class PreparedWithoutFence(RuntimeError):
        pass

    def prepare(*_args, **_kwargs):
        raise PreparedWithoutFence

    monkeypatch.setattr(era, "_prepare_current_global_probability_family", prepare)

    original_bounded = universe.bounded_work_sqlite
    original_connect_read_only = universe._connect_read_only
    shared_flags = []
    derived = []

    def connect_read_only(path):
        conn = original_connect_read_only(path)
        derived.append(conn)
        return conn

    @contextmanager
    def recording_bounded(
        conn,
        work_context,
        *,
        stage,
        shared_connection=False,
        keep_independent_connection_open=False,
    ):
        shared_flags.append((stage, shared_connection))
        with original_bounded(
            conn,
            work_context,
            stage=stage,
            shared_connection=shared_connection,
            keep_independent_connection_open=keep_independent_connection_open,
        ) as bounded:
            yield bounded

    holder_entered = threading.Event()
    release_holder = threading.Event()
    holder_conn = sqlite3.connect(":memory:", check_same_thread=False)

    def hold_shared_fence():
        with original_bounded(
            holder_conn,
            universe.WorkContext(deadline_monotonic=None),
            stage="test_shared_holder",
            shared_connection=True,
        ):
            holder_entered.set()
            assert release_holder.wait(2.0)

    holder = threading.Thread(target=hold_shared_fence, daemon=True)
    holder.start()
    assert holder_entered.wait(1.0)
    monkeypatch.setattr(universe, "_connect_read_only", connect_read_only)
    monkeypatch.setattr(universe, "bounded_work_sqlite", recording_bounded)
    started = time.monotonic()
    try:
        with pytest.raises(PreparedWithoutFence):
            mr._build_current_global_day0_family_snapshot(
                _pos(),
                trade_conn=sqlite3.connect(":memory:"),
                decision_time=datetime(2026, 6, 12, 12, tzinfo=timezone.utc),
                cached_snapshots=(),
                deadline_monotonic=time.monotonic() + 2.5,
                hwm_deadline_monotonic=time.monotonic() + 2.5,
            )
        assert time.monotonic() - started < 1.0
        assert holder.is_alive()
        assert shared_flags == []
        for conn in opened:
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")
        for conn in derived:
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")
    finally:
        release_holder.set()
        holder.join(2.0)
        holder_conn.close()


def test_day0_hwm_handoff_keeps_independent_prepare_reads_alive(
    monkeypatch,
    tmp_path,
):
    import src.engine.event_reactor_adapter as era
    import src.engine.monitor_refresh as mr
    import src.state.db as db

    world_memory = _day0_event_connection()
    world_path = tmp_path / "world.db"
    world_memory.commit()
    with sqlite3.connect(world_path) as target:
        world_memory.backup(target)
    world_memory.close()
    forecasts_path = tmp_path / "forecasts.db"
    sqlite3.connect(forecasts_path).close()

    def read_only(path):
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(
        db,
        "get_forecasts_connection_with_world_read_only",
        lambda **_kwargs: _monitor_forecast_world_reader(read_only(world_path)),
    )
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_read_only",
        lambda **_kwargs: read_only(forecasts_path),
    )
    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(mr, "_target_day_has_canonical_observation", lambda *_a, **_k: False)

    class HandoffReadSucceeded(RuntimeError):
        pass

    def prepare(*_args, **kwargs):
        kwargs["before_raw_input_hwm_read"]()
        kwargs["forecast_conn"].execute("SELECT 1").fetchone()
        kwargs["topology_conn"].execute("SELECT 1").fetchone()
        kwargs["observation_conn"].execute("SELECT 1").fetchone()
        raise HandoffReadSucceeded

    monkeypatch.setattr(era, "_prepare_current_global_probability_family", prepare)

    with pytest.raises(HandoffReadSucceeded):
        mr._build_current_global_day0_family_snapshot(
            _pos(),
            trade_conn=sqlite3.connect(":memory:"),
            decision_time=datetime(2026, 6, 12, 12, tzinfo=timezone.utc),
            cached_snapshots=(),
            deadline_monotonic=time.monotonic() + 2.5,
            hwm_deadline_monotonic=time.monotonic() + 2.5,
        )


def test_day0_prepare_timeout_does_not_start_or_mislabel_hwm(monkeypatch):
    import src.engine.event_reactor_adapter as era
    import src.engine.monitor_refresh as mr
    import src.state.db as db
    from src.engine.global_auction_universe import WorkDeferred

    world = _day0_event_connection()
    forecasts = sqlite3.connect(":memory:")
    hwm = sqlite3.connect(":memory:")
    clock = [10.0]

    def prepare(*_args, **kwargs):
        clock[0] = 12.6
        kwargs["before_raw_input_hwm_read"]()
        raise AssertionError("expired preparation reached HWM")

    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(mr, "_target_day_has_canonical_observation", lambda *_a, **_k: False)
    monkeypatch.setattr(mr.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_with_world_read_only",
        lambda **_kwargs: _monitor_forecast_world_reader(world),
    )
    connections = iter((forecasts, hwm))
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_read_only",
        lambda **_kwargs: next(connections),
    )
    monkeypatch.setattr(era, "_prepare_current_global_probability_family", prepare)

    with pytest.raises(WorkDeferred) as raised:
        mr._build_current_global_day0_family_snapshot(
            _pos(),
            trade_conn=sqlite3.connect(":memory:"),
            decision_time=datetime(2026, 6, 12, 12, tzinfo=timezone.utc),
            cached_snapshots=(),
            deadline_monotonic=20.0,
            hwm_deadline_monotonic=20.0,
        )

    assert raised.value.stage == "held_monitor_probability_prepare:hwm_handoff"
    assert "HWM" not in str(raised.value)


def test_day0_visibility_retry_fails_closed_when_event_never_publishes(monkeypatch):
    """Canonical observation without its Day0 event never reuses stale or market q."""
    import src.engine.monitor_refresh as mr

    clock = [20.0]
    attempts = 0

    def build(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise mr.ObservationUnavailableError(
            mr._DAY0_CANONICAL_OBSERVATION_EVENT_NOT_VISIBLE
        )

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(mr, "_build_current_global_day0_family_snapshot", build)
    monkeypatch.setattr(mr.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mr.time, "sleep", sleep)

    with pytest.raises(
        mr.ObservationUnavailableError,
        match=mr._DAY0_CANONICAL_OBSERVATION_EVENT_NOT_VISIBLE,
    ):
        mr._refresh_current_global_day0_probability(
            _pos(),
            trade_conn=object(),
            deadline_monotonic=95.0,
        )

    assert attempts == 2
    assert clock[0] == pytest.approx(
        20.0 + mr._DAY0_MATERIALIZATION_VISIBILITY_RETRY_SECONDS
    )


def test_day0_snapshot_build_sql_guard_interrupts_at_effective_deadline(monkeypatch):
    """The build's SQLite boundary interrupts at its effective monitor deadline."""
    import src.engine.monitor_refresh as mr

    conn = sqlite3.connect(":memory:")
    clock = [0.0]

    def monotonic():
        current = clock[0]
        clock[0] += 0.001
        return current
    monkeypatch.setattr(mr.time, "monotonic", monotonic)
    effective_deadline = mr._day0_materialization_visibility_retry_deadline(75.0)

    with pytest.raises(mr._Day0SnapshotReadDeadlineExceeded):
        with mr._day0_snapshot_sqlite_read_deadline(conn, effective_deadline):
            conn.execute(
                """
                WITH RECURSIVE spin(value) AS (
                    SELECT 1
                    UNION ALL
                    SELECT value + 1 FROM spin
                )
                SELECT SUM(value) FROM spin
                """
            ).fetchone()

    conn.close()
    assert effective_deadline == pytest.approx(0.35)
    assert clock[0] < 0.5


def test_day0_snapshot_tokens_use_closed_independent_trade_reader(monkeypatch):
    """The snapshot bind read must never install a handler on shared trade_conn."""
    import src.engine.monitor_refresh as mr
    import src.state.db as db

    class SharedTradeConnection(sqlite3.Connection):
        def set_progress_handler(self, *_args, **_kwargs):
            raise AssertionError("shared trade connection must not receive a handler")

    class SnapshotTradeConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    shared = sqlite3.connect(":memory:", factory=SharedTradeConnection)
    snapshot = sqlite3.connect(":memory:", factory=SnapshotTradeConnection)
    snapshot.row_factory = sqlite3.Row
    snapshot.execute(
        """
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT,
            captured_at TEXT,
            snapshot_id TEXT
        )
        """
    )
    snapshot.execute(
        """
        INSERT INTO executable_market_snapshot_latest VALUES
        ('condition-1', 'yes-1', 'no-1', '2026-07-30T00:00:00+00:00', 'snapshot-1')
        """
    )
    monkeypatch.setattr(db, "get_trade_connection_read_only", lambda: snapshot)

    rows = mr._read_current_global_day0_snapshot_tokens(
        trade_conn=shared,
        condition_ids=("condition-1",),
        deadline_monotonic=time.monotonic() + 1.0,
    )

    shared.close()
    assert rows[0]["yes_token_id"] == "yes-1"
    assert snapshot.closed is True


def test_freshest_seed_skips_payload_without_target_local_day(tmp_path, monkeypatch):
    """Newest seed can be a poison file; read-through must pick the newest usable one."""
    import src.data.replacement_forecast_production as prod
    import src.engine.monitor_refresh as mr

    root = tmp_path / "replacement_forecast_live"
    seed_dir = root / "seeds"
    processed_dir = root / "seeds_processed"
    queue_processed_dir = root / "processed"
    raw_dir = root / "raw_manifests"
    for path in (seed_dir, processed_dir, queue_processed_dir, raw_dir):
        path.mkdir(parents=True)

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {
            "seed_dir": str(seed_dir),
            "seed_processed_dir": str(processed_dir),
            "processed_dir": str(queue_processed_dir),
        },
    )

    bad_payload = raw_dir / "openmeteo_Hong_Kong_2026-06-25_low.json"
    bad_payload.write_text(
        json.dumps({"hourly": {"time": ["2026-06-25T01:00"], "temperature_2m": [28.0]}}),
        encoding="utf-8",
    )
    good_payload = raw_dir / "openmeteo_Hong_Kong_2026-06-26_low.json"
    good_payload.write_text(
        json.dumps({"hourly": {"time": ["2026-06-26T01:00"], "temperature_2m": [27.0]}}),
        encoding="utf-8",
    )

    def write_seed(stamp: str, payload_path) -> None:
        seed = {
            "city": "Hong Kong",
            "target_date": "2026-06-26",
            "temperature_metric": "low",
            "city_timezone": "Asia/Hong_Kong",
            "openmeteo_payload_json": f"../raw_manifests/{payload_path.name}",
        }
        (seed_dir / f"Hong_Kong.2026-06-26.low.{stamp}.json").write_text(
            json.dumps(seed),
            encoding="utf-8",
        )

    write_seed("20260624T222604Z", bad_payload)
    write_seed("20260624T222503Z", good_payload)

    selected = mr._freshest_family_seed_on_disk(
        city="Hong Kong",
        target_date="2026-06-26",
        metric="low",
    )

    assert selected is not None
    selected_path, selected_payload = selected
    assert selected_path.name.endswith("20260624T222503Z.json")
    assert selected_payload["openmeteo_payload_json"].endswith("2026-06-26_low.json")


def test_freshest_seed_reads_latest_cache_without_enumerating_archives(
    tmp_path, monkeypatch
):
    import os
    from pathlib import Path

    import src.data.replacement_forecast_production as prod
    import src.engine.monitor_refresh as mr

    seed_dir = tmp_path / "seeds"
    seed_processed_dir = tmp_path / "seeds_processed"
    processed_dir = tmp_path / "processed"
    for path in (seed_dir, seed_processed_dir, processed_dir):
        path.mkdir()
    latest_dir = tmp_path / "seeds_latest"
    latest_dir.mkdir()

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {
            "seed_dir": str(seed_dir),
            "seed_processed_dir": str(seed_processed_dir),
            "processed_dir": str(processed_dir),
        },
    )
    payload_path = tmp_path / "current.json"
    payload_path.write_text(
        json.dumps(
            {
                "hourly": {
                    "time": ["2026-07-22T01:00"],
                    "temperature_2m": [29.0],
                }
            }
        ),
        encoding="utf-8",
    )
    latest_seed = latest_dir / "Seoul.2026-07-22.high.json"
    latest_seed.write_text(
        json.dumps(
            {
                "city": "Seoul",
                "target_date": "2026-07-22",
                "temperature_metric": "high",
                "city_timezone": "Asia/Seoul",
                "openmeteo_payload_json": str(payload_path),
            }
        ),
        encoding="utf-8",
    )
    real_scandir = os.scandir

    def guarded_scandir(path):
        raise AssertionError(f"unexpected directory enumeration: {Path(path)}")

    monkeypatch.setattr(os, "scandir", guarded_scandir)

    selected = mr._freshest_family_seed_on_disk(
        city="Seoul",
        target_date="2026-07-22",
        metric="high",
    )

    assert selected is not None
    assert selected[0] == latest_seed


def test_freshest_seed_does_not_enumerate_processed_archives(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    import src.data.replacement_forecast_production as prod
    import src.engine.monitor_refresh as mr

    seed_dir = tmp_path / "seeds"
    seed_processed_dir = tmp_path / "seeds_processed"
    processed_dir = tmp_path / "processed"
    for path in (seed_dir, seed_processed_dir, processed_dir):
        path.mkdir()

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {
            "seed_dir": str(seed_dir),
            "seed_processed_dir": str(seed_processed_dir),
            "processed_dir": str(processed_dir),
        },
    )
    real_scandir = os.scandir

    def guarded_scandir(path):
        assert Path(path) == seed_dir
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", guarded_scandir)

    assert mr._freshest_family_seed_on_disk(
        city="Seoul",
        target_date="2026-07-22",
        metric="high",
    ) is None


def test_freshest_seed_caps_pending_queue_enumeration(tmp_path, monkeypatch):
    import os

    import src.data.replacement_forecast_production as prod
    import src.engine.monitor_refresh as mr

    seed_dir = tmp_path / "seeds"
    seed_dir.mkdir()
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"seed_dir": str(seed_dir)},
    )
    monkeypatch.setattr(mr, "_HELD_BELIEF_PENDING_SEED_SCAN_LIMIT", 3)
    seen = 0

    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal seen
            seen += 1
            return SimpleNamespace(name=f"unrelated-{seen}.json")

    monkeypatch.setattr(os, "scandir", lambda _path: Entries())

    assert mr._freshest_family_seed_on_disk(
        city="Seoul",
        target_date="2026-07-22",
        metric="high",
    ) is None
    assert seen == 3


# ---------------------------------------------------------------------------
# Post-local-day day0 exit routing (XBL: Seattle 440a58af-e7b / 571a7579-002,
# Chicago 7319687c-842, 2026-09-14). A day0_window position whose contract-
# local target day has already ended kept getting evaluated on
# day0_observation_remaining_window: that bootstrap has zero remaining hours
# to sample and degenerates to a maximally-wide-but-finite belief band that
# still passes the held-side evidence_ok check, so the exit organ silently
# went blind while reporting itself fresh (INCOMPLETE_EXIT_CONTEXT
# (missing=belief)). _day0_absorbing_hard_fact_overlay's target-local-day gate
# is widened to also try (and, if unavailable, name) the durable hard-fact
# lane once the local day has ended, before the remaining-window recompute
# ever runs.
# ---------------------------------------------------------------------------


def _post_day_hard_fact_position():
    from src.state.portfolio import Position

    return Position(
        trade_id="t-postday-1",
        market_id="m-postday-1",
        city="Seattle",
        cluster="Seattle",
        target_date="2026-06-12",
        bin_label="Will the highest temperature in Seattle be between 60-61°F on June 12?",
        direction="buy_no",
        unit="F",
        temperature_metric="high",
        entry_method="qkernel_spine",
        entry_price=0.55,
        p_posterior=0.46,
    )


def _post_day_hard_fact_verdict_and_belief():
    verdict = SimpleNamespace(
        metric="high",
        rounded_extreme=58.0,
        source="ogimet_metar_kseattle",
        evidence=SimpleNamespace(
            is_complete_for=lambda _city: True,
            station_id="KSEA",
            observed_at="2026-06-13T00:00:00+00:00",
            payload_identity="postday-evidence-1",
            as_dict=lambda: {"station_id": "KSEA"},
        ),
    )
    belief = SimpleNamespace(
        yes_verdict="STRUCTURAL_LOSS",
        held_verdict="STRUCTURAL_WIN",
        yes_prob=0.0,
        held_side_prob=1.0,
    )
    return verdict, belief


def test_post_local_day_selects_hard_fact_when_final_extreme_available(monkeypatch):
    """(a) local day ended, final extreme available -> hard-fact method, fresh."""
    import src.engine.monitor_refresh as mr
    import src.execution.day0_hard_fact_exit as hfe

    position = _post_day_hard_fact_position()
    monkeypatch.setattr(mr, "_is_position_target_local_day", lambda *_a, **_k: False)
    monkeypatch.setattr(mr, "_is_position_after_target_local_day", lambda *_a, **_k: True)
    verdict, belief = _post_day_hard_fact_verdict_and_belief()
    monkeypatch.setattr(hfe, "evaluate_hard_fact_exit", lambda **_kw: verdict)
    monkeypatch.setattr(hfe, "hard_fact_monitor_belief", lambda **_kw: belief)

    city = SimpleNamespace(name="Seattle", timezone="America/Los_Angeles", settlement_source_type="noaa")
    prob, refreshed, is_fresh = mr.monitor_probability_refresh(
        position, conn=object(), city=city, target_d="2026-06-12",
    )

    assert is_fresh is True
    assert prob == pytest.approx(1.0)
    assert refreshed.selected_method == mr.SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT
    assert getattr(refreshed, mr._DAY0_ZERO_PROBABILITY_EXIT_AUTHORITY_ATTR) is True
    assert "POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE" not in refreshed.applied_validations
    assert any(
        v.startswith("belief_source=day0_absorbing_hard_fact")
        for v in refreshed.applied_validations
    )


def test_post_local_day_declines_named_when_final_extreme_unavailable(monkeypatch):
    """(b) local day ended, no durable evidence resolves a verdict/belief ->
    the named POST_LOCAL_DAY reason, probability marked not fresh, no
    placeholder, exit authority not granted."""
    import src.engine.monitor_refresh as mr
    import src.execution.day0_hard_fact_exit as hfe

    position = _post_day_hard_fact_position()
    monkeypatch.setattr(mr, "_is_position_target_local_day", lambda *_a, **_k: False)
    monkeypatch.setattr(mr, "_is_position_after_target_local_day", lambda *_a, **_k: True)
    verdict, _belief = _post_day_hard_fact_verdict_and_belief()
    monkeypatch.setattr(hfe, "evaluate_hard_fact_exit", lambda **_kw: verdict)
    # A verdict exists but cannot be resolved to a directional belief.
    monkeypatch.setattr(hfe, "hard_fact_monitor_belief", lambda **_kw: None)

    city = SimpleNamespace(name="Seattle", timezone="America/Los_Angeles", settlement_source_type="noaa")
    prob, refreshed, is_fresh = mr.monitor_probability_refresh(
        position, conn=object(), city=city, target_d="2026-06-12",
    )

    assert is_fresh is False
    assert prob == pytest.approx(position.p_posterior)
    assert refreshed.selected_method == mr.SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT
    assert "POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE" in refreshed.applied_validations
    assert getattr(refreshed, mr._DAY0_ZERO_PROBABILITY_EXIT_AUTHORITY_ATTR) is False
    assert not any("belief=" in v and "belief_source" not in v for v in refreshed.applied_validations)


def test_local_day_still_open_hard_fact_overlay_unchanged(monkeypatch):
    """(c) local day still open -> overlay behaves exactly as parent (verdict
    None falls through to None; the day-ended branch never engages)."""
    import src.engine.monitor_refresh as mr
    import src.execution.day0_hard_fact_exit as hfe

    position = _post_day_hard_fact_position()
    monkeypatch.setattr(mr, "_is_position_target_local_day", lambda *_a, **_k: True)
    monkeypatch.setattr(mr, "_is_position_after_target_local_day", lambda *_a, **_k: False)
    monkeypatch.setattr(hfe, "evaluate_hard_fact_exit", lambda **_kw: None)

    result = mr._day0_absorbing_hard_fact_overlay(
        pos=position, conn=object(),
        city=SimpleNamespace(name="Seattle", timezone="America/Los_Angeles", settlement_source_type="noaa"),
        target_d="2026-06-12",
    )

    assert result is None


# ---------------------------------------------------------------------------
# 440a58af-e7b replay (team-lead acceptance criterion, corrected 2026-09-14):
# Seattle 2026-09-13 high, buy_no, entry 0.60, phase day0_window, post-local-day
# since 07:00Z. world.observation_instants' ogimet_metar_ksea coverage is
# PARTIAL (00:00-10:00 local only; running_max 55.04F) — the local day's METAR
# record does NOT cover the day end, so the settlement-grade final extreme is
# genuinely unavailable, not merely unread. _post_local_day_final_daily_verdict
# (monitor_refresh.py) reads it via _final_daily_observation_extreme
# (day0_hard_fact_exit.py), which requires either a VERIFIED daily row or
# every expected local-day UTC hour present plus proof of next-day advancement
# before returning a value — partial coverage correctly yields None here, not
# a verdict built from the incomplete running max.
# ---------------------------------------------------------------------------


def _440a58af_shaped_position():
    from src.state.portfolio import Position

    return Position(
        trade_id="440a58af-e7b",
        market_id="m-440a58af",
        city="Seattle",
        cluster="Seattle",
        target_date="2026-09-13",
        bin_label="Will the highest temperature in Seattle be between 60-61°F on September 13?",
        direction="buy_no",
        unit="F",
        temperature_metric="high",
        entry_method="qkernel_spine",
        entry_price=0.60,
        p_posterior=0.4558,
        state="day0_window",
        condition_id="0x" + "eb" * 32,
    )


def test_440a58af_replay_partial_coverage_declines_named_calibration_never_reached(
    monkeypatch,
):
    """440a58af-e7b's actual shape: post-local-day, PARTIAL METAR coverage
    (running_max 55.04F over 00:00-10:00 local only, day not complete) ->
    routed to the hard-fact method, coverage incomplete -> the named
    POST_LOCAL_DAY reason, missing= names fresh_prob_is_fresh (not 'belief'),
    both freshness flags revoked, Position._exit_q_mean_and_source never
    reaches the market-anchored calibration branch (source stays "raw")."""
    import src.engine.monitor_refresh as mr
    import src.execution.day0_hard_fact_exit as hfe
    from src.engine.cycle_runtime import (
        _incomplete_exit_observability_reason,
        _revoke_monitor_action_authority,
    )
    from src.state.portfolio import ExitContext

    position = _440a58af_shaped_position()
    monkeypatch.setattr(mr, "_is_position_target_local_day", lambda *_a, **_k: False)
    monkeypatch.setattr(mr, "_is_position_after_target_local_day", lambda *_a, **_k: True)
    # Complete-day settlement-grade record genuinely unavailable (partial
    # METAR coverage — the real function would also return None here).
    monkeypatch.setattr(hfe, "_final_daily_observation_extreme", lambda **_kw: None)
    # The intraday exclusion machinery also finds no verdict: 55.04F does not
    # exceed the [60,61] bin's upper bound, so hard_fact_bin_verdict declines
    # to confirm OR exclude from partial evidence — exactly as designed.
    monkeypatch.setattr(hfe, "evaluate_hard_fact_exit", lambda **_kw: None)
    monkeypatch.setattr(mr, "_would_use_day0_monitor_lane", lambda *_a, **_k: True)
    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _pos: position.condition_id)
    monkeypatch.setattr(
        mr,
        "_refresh_current_global_day0_probability",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("must not reach the remaining-window recompute path")
        ),
    )

    city = SimpleNamespace(name="Seattle", timezone="America/Los_Angeles", settlement_source_type="noaa")
    prob, refreshed, is_fresh = mr.monitor_probability_refresh(
        position, conn=object(), city=city, target_d="2026-09-13",
    )

    assert is_fresh is False
    assert prob == pytest.approx(position.p_posterior)
    assert "POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE" in refreshed.applied_validations
    assert getattr(refreshed, mr._DAY0_ZERO_PROBABILITY_EXIT_AUTHORITY_ATTR) is False

    # Mirror refresh_position's actual field assignment (monitor_refresh.py
    # ~7666-7668): last_monitor_prob/last_monitor_prob_is_fresh come from this
    # tuple, not from refreshed.p_posterior (the entry-time value, untouched).
    refreshed.last_monitor_prob_is_fresh = is_fresh is True
    refreshed.last_monitor_prob = float(prob) if refreshed.last_monitor_prob_is_fresh else float("nan")

    # Feed the refreshed (non-fresh) position through the real exit-context
    # observability path: missing= must name a real field, never 'belief',
    # and the operator's calibration branch must never be reached.
    ctx = ExitContext(
        fresh_prob=refreshed.last_monitor_prob,
        fresh_prob_is_fresh=refreshed.last_monitor_prob_is_fresh,
        current_market_price=0.0,
        current_market_price_is_fresh=True,
        best_bid=0.0,
        current_ci=None,
        hours_to_settlement=1.0,
        position_state="day0_window",
        market_velocity_1h=0.0,
        divergence_score=0.0,
    )
    reason = _incomplete_exit_observability_reason(
        SimpleNamespace(reason="EVIDENCE_UNAVAILABLE"), ctx, pos=refreshed,
    )
    # is_fresh=False -> refreshed.last_monitor_prob is NaN (mirroring
    # refresh_position), so missing_authority_fields() names the non-finite
    # fresh_prob field itself (not the fresh_prob_is_fresh sub-case, and never
    # the 'belief' placeholder).
    assert reason == "INCOMPLETE_EXIT_CONTEXT (missing=fresh_prob)"
    assert "belief" not in reason

    q_raw, evidence_ok, exit_q_source = refreshed._exit_q_mean_and_source(ctx)
    assert evidence_ok is False
    assert exit_q_source == "raw"
    assert "entry_calibration_unavailable" not in refreshed.applied_validations

    _revoke_monitor_action_authority(
        refreshed, missing_fields={"fresh_prob"},
    )
    assert refreshed.last_monitor_prob_is_fresh is False
    assert refreshed.last_monitor_market_price_is_fresh is False


def test_440a58af_shaped_position_complete_coverage_yields_determinate_exit_q(
    monkeypatch,
):
    """Sibling of the 440a58af replay: same shape, but the complete-day
    settlement-grade final extreme IS on record -> a determinate exit_q
    (hard-fact method, fresh, full exit authority), still never touching the
    market-anchored calibration branch."""
    import src.engine.monitor_refresh as mr
    import src.execution.day0_hard_fact_exit as hfe

    position = _440a58af_shaped_position()
    monkeypatch.setattr(mr, "_is_position_target_local_day", lambda *_a, **_k: False)
    monkeypatch.setattr(mr, "_is_position_after_target_local_day", lambda *_a, **_k: True)
    # A complete local day: final settled high resolves outside [60,61] -> the
    # buy_no held side structurally won.
    observation = hfe.FinalDailyObservation(
        raw_extreme=58.4,
        settled_extreme=58.0,
        source="ogimet_metar_ksea:following_day_observed",
        station_id="KSEA",
        unit="F",
        fetched_at=datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(hfe, "_final_daily_observation_extreme", lambda **_kw: observation)

    city = SimpleNamespace(name="Seattle", timezone="America/Los_Angeles", settlement_source_type="noaa")
    prob, refreshed, is_fresh = mr.monitor_probability_refresh(
        position, conn=object(), city=city, target_d="2026-09-13",
    )

    assert is_fresh is True
    assert prob == pytest.approx(1.0)
    assert refreshed.selected_method == mr.SELECTED_METHOD_DAY0_ABSORBING_HARD_FACT
    assert getattr(refreshed, mr._DAY0_ZERO_PROBABILITY_EXIT_AUTHORITY_ATTR) is True
    assert any(
        v.startswith("belief_source=day0_final_daily_observation")
        for v in refreshed.applied_validations
    )
    assert "POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE" not in refreshed.applied_validations

    from src.state.portfolio import ExitContext

    # Mirror refresh_position's actual field assignment (monitor_refresh.py
    # ~7666-7668): last_monitor_prob/last_monitor_prob_is_fresh come from this
    # tuple, not from refreshed.p_posterior (the entry-time value, untouched).
    refreshed.last_monitor_prob_is_fresh = is_fresh is True
    refreshed.last_monitor_prob = float(prob) if refreshed.last_monitor_prob_is_fresh else float("nan")

    ctx = ExitContext(
        fresh_prob=refreshed.last_monitor_prob,
        fresh_prob_is_fresh=refreshed.last_monitor_prob_is_fresh,
        current_market_price=0.0,
        current_market_price_is_fresh=True,
        best_bid=0.0,
        current_ci=(1.0, 1.0),
        hours_to_settlement=1.0,
        position_state="day0_window",
        market_velocity_1h=0.0,
        divergence_score=0.0,
    )
    import src.calibration.market_anchored_live_fit as calib

    monkeypatch.setattr(calib, "get_active_provider", lambda: None)
    q_raw, evidence_ok, exit_q_source = refreshed._exit_q_mean_and_source(ctx)
    assert evidence_ok is True
    assert exit_q_source == "raw"
    assert float(q_raw) == pytest.approx(1.0)
