# Created: 2026-06-21
# Last reused or audited: 2026-08-12
# Lifecycle: created=2026-06-21; last_reviewed=2026-08-12; last_reused=2026-08-12
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

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

BIN = "Will the highest temperature in Karachi be 37°C on June 12?"


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


def test_day0_visibility_retry_recovers_raw_hwm_after_250ms(monkeypatch):
    """A matching posterior published within the short budget restores fresh q."""
    import src.engine.monitor_refresh as mr

    clock = [10.0]
    attempts = 0
    build_deadlines: list[float] = []
    snapshot = SimpleNamespace()

    def build(*_args, deadline_monotonic, **_kwargs):
        nonlocal attempts
        attempts += 1
        build_deadlines.append(deadline_monotonic)
        if clock[0] < 10.25:
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
    assert attempts == 4
    assert build_deadlines == pytest.approx([11.0, 10.35, 10.35, 10.35])
    assert clock[0] == pytest.approx(10.3)


def test_day0_primary_snapshot_read_does_not_use_visibility_retry_budget(monkeypatch):
    """A normal primary authority read may outlive the publish-retry window."""
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
        deadline_monotonic=20.0,
    )

    assert held_prob == pytest.approx(0.30)
    assert is_fresh is True
    assert build_deadlines == pytest.approx([15.0])


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
    monkeypatch.setattr(db, "get_world_connection_read_only", lambda: world)
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
    monkeypatch.setattr(db, "get_world_connection_read_only", lambda: world)
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

    world = _day0_event_connection()
    forecasts = sqlite3.connect(":memory:")
    pinned_bundle = SimpleNamespace(posterior_id="complete-00")
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
        raise PinnedRoutePrepared

    monkeypatch.setattr(mr, "_canonical_condition_id", lambda _position: "condition-1")
    monkeypatch.setattr(mr, "_target_day_has_canonical_observation", lambda *_a, **_k: False)
    monkeypatch.setattr(db, "get_world_connection_read_only", lambda: world)
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

    assert observed == {"forecast_connections": 1, "hwm_connections": 0}


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
        "get_world_connection_read_only",
        lambda: read_only(world_path),
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
        assert shared_flags == [
            ("held_monitor_probability_prepare:world", False),
            ("held_monitor_probability_prepare:forecasts", False),
        ]
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

    monkeypatch.setattr(db, "get_world_connection_read_only", lambda: read_only(world_path))
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
    monkeypatch.setattr(db, "get_world_connection_read_only", lambda: world)
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

    assert attempts == 4
    assert clock[0] == pytest.approx(
        20.0 + mr._DAY0_MATERIALIZATION_VISIBILITY_RETRY_BUDGET_SECONDS
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
