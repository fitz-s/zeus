# Created: 2026-07-02
# Last reused or audited: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   section 1 row "q_version + input HWMs (A1)" (W0.1).
"""TDD for W0.1: the LIVE decision hook (build_replacement_forecast_event_hook) must
reject a served posterior when a newer raw model input cycle already exists, the same
way the no-submit-cert read path (event_reactor_adapter._forecast_authority_payload_from_posterior)
and the manual event_reactor_adapter.py:17963 call site already do. Before this fix,
this read path (feeds effective_q_posterior/effective_q_lcb/effective_kelly_fraction into
the live decision) had NO raw-input HWM tripwire at all.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from types import SimpleNamespace

from src.data.replacement_forecast_bundle_reader import (
    HIGH_DATA_VERSION,
    PRODUCT_ID,
    SOURCE_ID,
)
from src.data.replacement_forecast_readiness import (
    LIVE_RUNTIME_LAYER,
    ReplacementForecastDependency,
    build_replacement_forecast_readiness,
)
from src.data.replacement_forecast_runtime_policy import LIVE_STATUS, ReplacementForecastRuntimePolicy
from src.data.replacement_forecast_switch_decision import SWITCH_LIVE, ReplacementForecastSwitchDecision
from src.engine.replacement_forecast_hook_factory import (
    ReplacementForecastHookFactoryInput,
    build_replacement_forecast_event_hook,
)
from src.state.schema.v2_schema import apply_canonical_schema

UTC = timezone.utc


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    return conn


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


def _insert_posterior(conn: sqlite3.Connection) -> int:
    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, q_lcb_json, q_ucb_json, posterior_method,
            dependency_source_run_ids_json, provenance_json,
            family_id, bin_topology_hash, dependency_hash, posterior_config_hash,
            posterior_identity_hash, runtime_layer, training_allowed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            SOURCE_ID,
            PRODUCT_ID,
            HIGH_DATA_VERSION,
            "Shanghai",
            "2026-06-07",
            "high",
            "2026-06-06T00:00:00+00:00",
            _dt(3).isoformat(),
            _dt(3, 5).isoformat(),
            json.dumps({"cold": 0.2, "warm": 0.8}),
            json.dumps({"cold": 0.1, "warm": 0.7}),
            json.dumps({"cold": 0.3, "warm": 0.9}),
            "openmeteo_ecmwf_ifs9_bayes_fusion",
            json.dumps({"baseline_b0": "b0-run", "openmeteo_ifs9_anchor": "om9-run"}),
            json.dumps({"replacement_q_mode": "FUSED_NORMAL_FULL", "bin_topology_hash": "topology-hash"}),
            "Shanghai:2026-06-07:high:topology-hash",
            "topology-hash",
            "dependency-hash",
            "config-hash",
            "identity-hash-1",
            LIVE_RUNTIME_LAYER,
            0,
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _insert_raw_model_forecast(conn: sqlite3.Connection, *, model: str, source_cycle_time: datetime) -> None:
    conn.execute(
        """
        INSERT INTO raw_model_forecasts (
            model, city, target_date, metric, source_cycle_time,
            source_available_at, captured_at, lead_days, forecast_value_c, endpoint,
            coverage_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            model,
            "Shanghai",
            "2026-06-07",
            "high",
            source_cycle_time.isoformat(),
            source_cycle_time.isoformat(),
            source_cycle_time.isoformat(),
            1,
            28.0,
            "single_runs",
            "COVERED",
        ),
    )


def _readiness(*, posterior_id: int):
    dependencies = (
        ReplacementForecastDependency(
            role="baseline_b0",
            source_id="ecmwf_open_data",
            product_id="ecmwf_opendata_ifs_ens_0p25",
            data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
            source_run_id="b0-run",
            source_available_at=_dt(2),
        ),
        ReplacementForecastDependency(
            role="openmeteo_ifs9_anchor",
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version="openmeteo_ecmwf_ifs9_anchor_localday_high",
            source_run_id="om9-run",
            source_available_at=_dt(2),
            anchor_id=22,
        ),
        ReplacementForecastDependency(
            role="soft_anchor_posterior",
            source_id=SOURCE_ID,
            product_id=PRODUCT_ID,
            data_version=HIGH_DATA_VERSION,
            source_run_id="posterior-run",
            source_available_at=_dt(3),
            posterior_id=posterior_id,
        ),
    )
    return build_replacement_forecast_readiness(
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(4),
        computed_at=_dt(4, 1),
        expires_at=_dt(6),
        dependencies=dependencies,
    )


def _proof() -> SimpleNamespace:
    return SimpleNamespace(
        candidate=SimpleNamespace(
            condition_id="cond-1",
            city="Shanghai",
            target_date="2026-06-07",
            metric="high",
        ),
        token_id="yes-1",
        direction="buy_yes",
        q_posterior=0.6,
        q_lcb_5pct=0.5,
        executable_snapshot_id="snap-1",
        bin_topology_hash="topology-hash",
    )


def _build_hook(conn: sqlite3.Connection, monkeypatch):
    from src.engine import replacement_forecast_hook_factory as hook_factory

    live_policy = ReplacementForecastRuntimePolicy(
        status=LIVE_STATUS,
        reason_codes=(),
        live_enabled=True,
        kelly_increase_enabled=True,
        direction_flip_enabled=True,
    )
    monkeypatch.setattr(hook_factory, "resolve_replacement_forecast_runtime_policy", lambda *a, **k: live_policy)
    live_switch_decision = ReplacementForecastSwitchDecision(
        status=SWITCH_LIVE,
        reason_codes=(),
        can_read_live_posterior=True,
        can_apply_reactor_hook=True,
        can_initiate_trade=True,
        can_increase_kelly=True,
        can_flip_direction=True,
        readiness_id=None,
    )
    monkeypatch.setattr(
        hook_factory, "evaluate_replacement_forecast_switch_decision", lambda *a, **k: live_switch_decision
    )

    request = ReplacementForecastHookFactoryInput(
        forecast_conn=conn,
        trade_conn=conn,
        runtime_flags={},
        baseline_bundle_provider=None,
    )
    return build_replacement_forecast_event_hook(request)


def test_hook_rejects_when_raw_input_newer_than_served_posterior(monkeypatch) -> None:
    """W0.1 tripwire: the LIVE hook must not serve a posterior once a materializable raw
    input cycle newer than that posterior's source_cycle_time exists."""
    conn = _conn()
    posterior_id = _insert_posterior(conn)  # source_cycle_time = 2026-06-06T00:00:00+00:00
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(conn, model=model, source_cycle_time=_dt(3))

    from src.engine import replacement_forecast_hook_factory as hook_factory

    monkeypatch.setattr(
        hook_factory, "_latest_replacement_readiness", lambda *a, **k: _readiness(posterior_id=posterior_id)
    )
    hook = _build_hook(conn, monkeypatch)

    result = hook(_proof(), SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY", payload_json="{}"), _dt(4))

    assert result is not None
    assert result.status == "BLOCKED"
    assert any(code.startswith("REPLACEMENT_RAW_INPUT_HWM:") for code in result.reason_codes)


def test_hook_serves_posterior_when_no_newer_raw_input_exists(monkeypatch) -> None:
    """Control: with no newer raw input, the same wiring must still serve the posterior
    (the tripwire must not false-positive on an ordinary fresh serve)."""
    conn = _conn()
    posterior_id = _insert_posterior(conn)
    for model in ("ecmwf_ifs", "gfs"):
        _insert_raw_model_forecast(conn, model=model, source_cycle_time=_dt(0))

    from src.engine import replacement_forecast_hook_factory as hook_factory

    monkeypatch.setattr(
        hook_factory, "_latest_replacement_readiness", lambda *a, **k: _readiness(posterior_id=posterior_id)
    )
    hook = _build_hook(conn, monkeypatch)

    result = hook(_proof(), SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY", payload_json="{}"), _dt(4))

    assert result is not None
    assert result.status != "BLOCKED"
    assert not any(code.startswith("REPLACEMENT_RAW_INPUT_HWM:") for code in result.reason_codes)
