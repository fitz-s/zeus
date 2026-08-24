# Created: 2026-06-10
# Last reused/audited: 2026-08-19
# Authority basis: docs/authority/replacement_final_form_2026_06_09.md; 2026-08-19
#   market-relative capital evidence retirement of stale ENS live authority.
"""Relationship tests for readiness-bound replacement posterior selection.

The current readiness dependency is the only posterior identity licensed for a decision.
A non-live-grade bound row, expired readiness, or stale source cycle fails closed; the reader
never substitutes an older row under a different certificate.
"""

from __future__ import annotations

import json
import hashlib
import math
import sqlite3
import numpy as np
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from src.data.replacement_forecast_cycle_policy import (
    CURRENT_EVIDENCE_SEMANTICS_REVISION,
    STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION,
    TRADEABLE_GRADE_QLCB_BASIS,
)
from src.data.replacement_forecast_bundle_reader import (
    HIGH_DATA_VERSION,
    PRODUCT_ID,
    ReplacementForecastAuthorityPurpose,
    SOURCE_ID,
    read_prior_complete_replacement_forecast_bundle,
    read_pinned_replacement_forecast_bundle,
    read_replacement_forecast_bundle,
)
from src.data.replacement_forecast_readiness import (
    ReplacementForecastDependency,
    build_replacement_forecast_readiness,
)
from src.state.schema.v2_schema import apply_canonical_schema


UTC = timezone.utc
_TOPO_HASH = "topo-hash-tradeable-001"
_FUSED_FULL = "FUSED_NORMAL_FULL"
_BAYES_PRECISION_FUSION_MISSING = "BAYES_PRECISION_FUSION_CAPTURE_MISSING"


@dataclass(frozen=True)
class _Evidence:
    source_run_id: str


@dataclass(frozen=True)
class _BaselineBundle:
    evidence: _Evidence


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    return conn


def _dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, day, hour, minute, tzinfo=UTC)


def _provenance(
    *,
    q_mode: str,
    semantics_revision: str = CURRENT_EVIDENCE_SEMANTICS_REVISION,
    shape_lag_hours: float = 0.0,
    stale_shape_reused: bool = False,
    translation_applied: bool = False,
    shape_source_cycle_time: datetime | None = None,
    strict_day0: bool = False,
) -> dict[str, object]:
    provenance: dict[str, object] = {
        "bin_topology_hash": _TOPO_HASH,
        "replacement_q_mode": q_mode,
        "q_lcb_basis": TRADEABLE_GRADE_QLCB_BASIS,
        "q_shape": "fused_normal_direct",
        "bayes_precision_fusion": {
            "current_evidence_shape": {
                "semantics_revision": semantics_revision,
                "shape_lag_hours": shape_lag_hours,
                "source_cycle_time": (
                    shape_source_cycle_time.isoformat()
                    if shape_source_cycle_time is not None
                    else None
                ),
                "stale_shape_reused": stale_shape_reused,
                "translation_applied": translation_applied,
            }
        },
        "bin_topology": [
            {
                "bin_id": "warm",
                "lower_c": 20.0,
                "upper_c": 21.0,
                "center_c": 20.5,
                "settlement_step_c": 1.0,
                "display_unit": "C",
                "settlement_unit": "C",
                "rounding_rule": "wmo_half_up",
            }
        ],
    }
    if strict_day0:
        shape = provenance["bayes_precision_fusion"]["current_evidence_shape"]
        assert isinstance(shape, dict)
        shape.update(
            {
                "member_values_hash": "member-values-hash",
            }
        )
        provenance["bayes_precision_fusion"]["current_value_serving"] = {
            "ecmwf_ifs": {
                "raw_model_forecast_id": "raw-ifs-1",
                "served_cycle": shape["source_cycle_time"],
                "captured_at": shape["source_cycle_time"],
            }
        }
        likelihood = {
            "semantics": "same_station_preliminary_report_survival_likelihood_v1",
            "cutoff": shape["source_cycle_time"],
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
        provenance.update(
            {
                "day0_provisional_observation": {
                    "active": True,
                    "metric": "high",
                    "unit": "C",
                    "source": "aviationweather_metar",
                },
                "day0_preliminary_report_survival_likelihood": likelihood,
                "day0_remaining_carrier_content_identity": "carrier-content-hash",
                "day0_remaining_carrier_operator": "extreme_observed_then_noisy_future_v1",
                "day0_remaining_carrier_q": [0.2, 0.8],
                "day0_remaining_carrier_probability_samples": [[0.2, 0.8]] * 500,
                "day0_remaining_carrier_sample_count": 500,
                "day0_remaining_carrier_future_extremes_c": [20.0, 21.0],
                "day0_remaining_carrier_path_error_sigma_c": 0.5,
                "day0_remaining_carrier_probability_cutoff_utc": shape["source_cycle_time"],
                "day0_remaining_vector_witness": {
                    "vector_id": "vector-id-1",
                    "expected_models": ["ecmwf_ifs"],
                    "actual_models": ["ecmwf_ifs"],
                    "capture_times_by_model_utc": {"ecmwf_ifs": shape["source_cycle_time"]},
                    "provider_source_cycle_time_by_model_utc": {"ecmwf_ifs": shape["source_cycle_time"]},
                    "provider_source_available_at_by_model_utc": {"ecmwf_ifs": shape["source_cycle_time"]},
                    "source_run_id_by_model": {"ecmwf_ifs": "source-run-1"},
                    "provider_run_id_by_model": {"ecmwf_ifs": "provider-run-1"},
                    "request_hash_by_model": {"ecmwf_ifs": "request-hash-1"},
                },
            }
        )
    return provenance


def _insert_posterior(
    conn: sqlite3.Connection,
    *,
    source_cycle_time: datetime,
    source_available_at: datetime,
    computed_at: datetime,
    q_mode: str,
    with_bounds: bool,
    with_ucb: bool | None = None,
    dependency_source_run_ids: dict[str, str] | None = None,
    semantics_revision: str = CURRENT_EVIDENCE_SEMANTICS_REVISION,
    shape_lag_hours: float = 0.0,
    stale_shape_reused: bool = False,
    translation_applied: bool = False,
    shape_source_cycle_time: datetime | None = None,
    decorrelated_providers_complete: bool | None = None,
    city: str = "Shanghai",
    target_date: str = "2026-06-07",
    strict_day0: bool = False,
) -> int:
    # ``with_ucb`` lets a row carry q_lcb_json but NOT q_ucb_json (the freshest-row
    # twin-authority carrier defect: a 13:08Z row HAS q_ucb, its 13:09Z sibling MISSING it).
    # Default: q_ucb tracks q_lcb (a real fused row materializes BOTH bounds together).
    if with_ucb is None:
        with_ucb = with_bounds
    deps = dependency_source_run_ids or {
        "baseline_b0": "b0-run",
        "aifs_sampled_2t": "aifs-run",
        "openmeteo_ifs9_anchor": "om9-run",
    }
    # Each posterior row carries a DISTINCT identity hash (forecast_posteriors enforces
    # UNIQUE(posterior_identity_hash)); keying on cycle+mode keeps two rows of the same scope
    # insertable, matching production where each cycle's materialization is a distinct row.
    identity_suffix = f"{source_cycle_time.isoformat()}|{q_mode}"
    provenance = _provenance(
        q_mode=q_mode,
        semantics_revision=semantics_revision,
        shape_lag_hours=shape_lag_hours,
        stale_shape_reused=stale_shape_reused,
        translation_applied=translation_applied,
        shape_source_cycle_time=(
            shape_source_cycle_time
            if shape_source_cycle_time is not None
            else source_cycle_time - timedelta(hours=shape_lag_hours)
            if math.isfinite(shape_lag_hours)
            else None
        ),
        strict_day0=strict_day0,
    )
    if decorrelated_providers_complete is not None:
        provenance["bayes_precision_fusion"]["decorrelated_providers_complete"] = (
            decorrelated_providers_complete
        )
        provenance["capture_status"] = (
            "FULL_CURRENT" if decorrelated_providers_complete else "PARTIAL_CURRENT"
        )
    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, q_lcb_json, posterior_method,
            dependency_source_run_ids_json, provenance_json,
            runtime_layer, training_allowed,
            bin_topology_hash, posterior_identity_hash, dependency_hash,
            posterior_config_hash, q_ucb_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            SOURCE_ID,
            PRODUCT_ID,
            HIGH_DATA_VERSION,
            city,
            target_date,
            "high",
            source_cycle_time.isoformat(),
            source_available_at.isoformat(),
            computed_at.isoformat(),
            json.dumps({"cold": 0.2, "warm": 0.8}),
            json.dumps({"cold": 0.1, "warm": 0.7}) if with_bounds else None,
            "openmeteo_ifs9_aifs_sampled_2t_soft_anchor",
            json.dumps(deps),
            json.dumps(provenance),
            "live",
            0,
            _TOPO_HASH,
            f"pid-hash-{identity_suffix}",
            f"dep-hash-{identity_suffix}",
            f"cfg-hash-{identity_suffix}",
            json.dumps({"cold": 0.3, "warm": 0.9}) if with_ucb else None,
        ),
    )
    return int(conn.execute("SELECT MAX(posterior_id) FROM forecast_posteriors").fetchone()[0])


def _readiness(
    *,
    posterior_id: int,
    expires_at: datetime,
    decision_time: datetime,
    computed_at: datetime,
    baseline_run_id: str = "b0-run",
    aifs_run_id: str = "aifs-run",
    anchor_run_id: str = "om9-run",
):
    dependencies = (
        ReplacementForecastDependency(
            role="baseline_b0",
            source_id="ecmwf_open_data",
            product_id="ecmwf_opendata_ifs_ens_0p25",
            data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
            source_run_id=baseline_run_id,
            source_available_at=_dt(6, 0),
        ),
        ReplacementForecastDependency(
            role="aifs_sampled_2t",
            source_id="ecmwf_aifs_ens",
            product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
            data_version="ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
            source_run_id=aifs_run_id,
            source_available_at=_dt(6, 0),
            artifact_id=11,
        ),
        ReplacementForecastDependency(
            role="openmeteo_ifs9_anchor",
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version="openmeteo_ecmwf_ifs9_anchor_localday_high",
            source_run_id=anchor_run_id,
            source_available_at=_dt(6, 0),
            anchor_id=22,
        ),
        ReplacementForecastDependency(
            role="soft_anchor_posterior",
            source_id=SOURCE_ID,
            product_id=PRODUCT_ID,
            data_version=HIGH_DATA_VERSION,
            source_run_id=f"posterior:{posterior_id}",
            source_available_at=_dt(6, 0),
            posterior_id=posterior_id,
        ),
    )
    return build_replacement_forecast_readiness(
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=decision_time,
        computed_at=computed_at,
        expires_at=expires_at,
        dependencies=dependencies,
    )


def _bind_test_hwm(monkeypatch, *, frontier: datetime, eligible: datetime) -> None:
    """Provide the typed raw-frontier/ENS HWM for in-memory reader fixtures."""

    from src.data import replacement_forecast_bundle_reader as reader

    monkeypatch.setattr(
        reader,
        "latest_live_input_cycle",
        lambda *args, **kwargs: (frontier, "test-raw-frontier"),
    )
    monkeypatch.setattr(
        reader,
        "latest_eligible_ensemble_input_cycle",
        lambda *args, **kwargs: eligible,
    )
    monkeypatch.setattr(
        reader,
        "replacement_live_input_lag_reason",
        lambda *args, **kwargs: None,
    )


def test_held_provenance_binds_configured_station_and_source_pair() -> None:
    from src.data import replacement_forecast_bundle_reader as reader

    provenance = _provenance(
        q_mode=_FUSED_FULL,
        strict_day0=True,
        shape_source_cycle_time=_dt(6, 0),
    )
    assert reader._held_pinned_provenance_reason(
        provenance,
        city="Tel Aviv",
        metric="high",
        decision_time=_dt(6, 12),
    ) is None

    wrong_station = json.loads(json.dumps(provenance))
    wrong_station["day0_preliminary_report_survival_likelihood"]["station_id"] = "LTFM"
    assert reader._held_pinned_provenance_reason(
        wrong_station,
        city="Tel Aviv",
        metric="high",
        decision_time=_dt(6, 12),
    ) == "REPLACEMENT_PINNED_DAY0_LIKELIHOOD_STATION_MISMATCH"

    wrong_pair = json.loads(json.dumps(provenance))
    wrong_pair["day0_preliminary_report_survival_likelihood"]["source_channel_pair"] = {
        "awc": "aviationweather_metar",
        "ogimet": "ogimet_metar_ltfm",
    }
    assert reader._held_pinned_provenance_reason(
        wrong_pair,
        city="Tel Aviv",
        metric="high",
        decision_time=_dt(6, 12),
    ) == "REPLACEMENT_PINNED_DAY0_LIKELIHOOD_SOURCE_PAIR_MISMATCH"


def test_noaa_producer_likelihood_persists_and_reader_accepts_exact_identity(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from src.data import replacement_forecast_materializer as materializer
    from src.data import replacement_forecast_bundle_reader as reader

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE observation_prints ("
        "id INTEGER PRIMARY KEY, city TEXT, station_id TEXT, source_channel TEXT, "
        "publish_ts_utc TEXT, value_native REAL, unit TEXT, fetched_at_utc TEXT, raw_report TEXT)"
    )
    request = SimpleNamespace(
        city="Tel Aviv",
        city_timezone="Asia/Jerusalem",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        computed_at=_dt(6, 12),
        day0_observed_extreme_source="aviationweather_metar",
        day0_observed_extreme_c=33.0,
    )
    bins = (
        SimpleNamespace(lower_c=None, upper_c=32.0),
        SimpleNamespace(lower_c=33.0, upper_c=33.0),
        SimpleNamespace(lower_c=34.0, upper_c=None),
    )
    carrier, likelihood = materializer._day0_noaa_preliminary_carrier(
        conn,
        request,
        metric="high",
        future_members_c=(28.0, 29.0, 30.0),
        bins=bins,
        path_error_sigma_c=0.5,
    )
    assert likelihood["station_id"] == "LLBG"
    assert likelihood["source_channel_pair"] == {
        "awc": "aviationweather_metar",
        "ogimet": "ogimet_metar_llbg",
    }
    assert len(carrier["samples"]) == 500
    persisted_likelihood = json.loads(json.dumps(likelihood))
    assert persisted_likelihood == likelihood
    provenance = _provenance(
        q_mode=_FUSED_FULL,
        strict_day0=True,
        shape_source_cycle_time=_dt(6, 0),
    )
    provenance["day0_preliminary_report_survival_likelihood"] = persisted_likelihood
    assert reader._held_pinned_provenance_reason(
        provenance,
        city="Tel Aviv",
        metric="high",
        decision_time=_dt(6, 12),
    ) is None

    from src.contracts.settlement_semantics import SettlementSemantics
    from src.engine import event_reactor_adapter as adapter
    from src.config import runtime_cities_by_name

    payload = {
        "metric": "high",
        "settlement_source": "aviationweather_metar",
        "evidence_finality": "PROVISIONAL_CURRENT_SNAPSHOT",
        "settlement_unit": "C",
        "rounded_value": 33.0,
        "_edli_day0_provisional_boundary_survival_probability": likelihood[
            "boundary_survival_probability"
        ],
        "_edli_day0_provisional_revision_likelihood": persisted_likelihood,
        "_edli_day0_remaining_content_identity": carrier["content_identity"],
        "_edli_day0_probability_operator": carrier["operator"],
        "_edli_day0_remaining_carrier_q": carrier["q"],
        "_edli_day0_remaining_probability_samples": carrier["samples"],
        "_edli_day0_remaining_probability_sample_count": carrier["sample_count"],
        "_edli_day0_remaining_carrier_future_extremes_c": [28.0, 29.0, 30.0],
        "_edli_day0_remaining_carrier_path_error_sigma_c": 0.5,
        "_edli_day0_remaining_carrier_probability_cutoff_utc": _dt(6, 12).isoformat(),
        "_edli_day0_remaining_vector_witness": {
            "vector_id": "vector-id-1",
            "expected_models": ["ecmwf_ifs"],
            "actual_models": ["ecmwf_ifs"],
            "capture_times_by_model_utc": {"ecmwf_ifs": _dt(6, 12).isoformat()},
            "provider_source_cycle_time_by_model_utc": {"ecmwf_ifs": _dt(6, 12).isoformat()},
            "provider_source_available_at_by_model_utc": {"ecmwf_ifs": _dt(6, 12).isoformat()},
            "source_run_id_by_model": {"ecmwf_ifs": "source-run-1"},
            "provider_run_id_by_model": {"ecmwf_ifs": "provider-run-1"},
            "request_hash_by_model": {"ecmwf_ifs": "request-hash-1"},
        },
    }
    city = runtime_cities_by_name()["Tel Aviv"]
    replay_q = adapter._day0_remaining_p_raw_vector(
        np.asarray([28.0, 29.0, 30.0], dtype=float),
        city=city,
        settlement_semantics=SettlementSemantics.for_city(city),
        bins=[
            SimpleNamespace(low=None, high=32.0),
            SimpleNamespace(low=33.0, high=33.0),
            SimpleNamespace(low=34.0, high=None),
        ],
        payload=payload,
        extra_member_sigma=0.5,
        decision_time=_dt(6, 12),
    )
    assert np.allclose(replay_q, np.asarray(carrier["q"], dtype=float))


def test_reader_live_eligible_q_mode_set_mirrors_live_gate() -> None:
    """The reader's live-grade q-mode set MUST equal the live gate's eligibility set.

    If the live gate (event_reactor_adapter) ever changes which q-modes are admissible, the
    reader's preference predicate must move with it — a drift here would let the reader serve a
    row the live gate then rejects (or vice-versa), reopening the clobber category by a side door.
    """
    from src.data import replacement_forecast_bundle_reader as reader
    from src.engine import event_reactor_adapter as adapter

    assert reader._REPLACEMENT_Q_MODE_LIVE_ELIGIBLE == adapter._REPLACEMENT_Q_MODE_LIVE_ELIGIBLE


def test_diagnostic_bounded_row_is_not_live_readable() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_BAYES_PRECISION_FUSION_MISSING,
        with_bounds=True,
    )
    readiness = _readiness(
        posterior_id=posterior_id,
        computed_at=_dt(6, 7, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 7, 30),
    )

    result = _read(conn, readiness, decision_time=_dt(6, 12))

    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"
    assert result.bundle is None


def test_anomaly_transport_row_is_not_live_readable() -> None:
    """Ankara-shaped stale transport cannot remain auction probability authority."""

    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        semantics_revision="ensemble_anomaly_transport_v3",
        shape_lag_hours=6.0,
        translation_applied=True,
    )
    readiness = _readiness(
        posterior_id=posterior_id,
        computed_at=_dt(6, 7, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 7, 30),
    )

    result = _read(conn, readiness, decision_time=_dt(6, 12))

    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"
    assert result.bundle is None


def test_missing_current_evidence_shape_is_not_live_readable() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
    )
    row = conn.execute(
        "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
        (posterior_id,),
    ).fetchone()
    provenance = json.loads(row[0])
    del provenance["bayes_precision_fusion"]["current_evidence_shape"]
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), posterior_id),
    )
    readiness = _readiness(
        posterior_id=posterior_id,
        computed_at=_dt(6, 7, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 7, 30),
    )

    result = _read(conn, readiness, decision_time=_dt(6, 12))

    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"


def test_stale_absolute_disagreement_row_is_offline_only() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        semantics_revision=(
            STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
        ),
        shape_lag_hours=6.0,
        stale_shape_reused=True,
    )
    readiness = _readiness(
        posterior_id=posterior_id,
        computed_at=_dt(6, 7, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 7, 30),
    )

    result = _read(conn, readiness, decision_time=_dt(6, 12))

    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"
    assert result.bundle is None


def test_stale_shape_selected_ensemble_beyond_outer_bound_is_blocked() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        semantics_revision=(
            STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
        ),
        shape_lag_hours=30.0,
        stale_shape_reused=True,
        shape_source_cycle_time=_dt(4, 18),
    )
    readiness = _readiness(
        posterior_id=posterior_id,
        computed_at=_dt(6, 7, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 7, 30),
    )

    result = _read(conn, readiness, decision_time=_dt(6, 12))

    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"


def test_same_cycle_shape_selected_ensemble_future_or_old_is_blocked() -> None:
    for shape_cycle_time in (_dt(4, 18), _dt(6, 12, 1)):
        conn = _conn()
        posterior_id = _insert_posterior(
            conn,
            source_cycle_time=_dt(6, 0),
            source_available_at=_dt(6, 7),
            computed_at=_dt(6, 7, 30),
            q_mode=_FUSED_FULL,
            with_bounds=True,
            shape_source_cycle_time=shape_cycle_time,
        )
        readiness = _readiness(
            posterior_id=posterior_id,
            computed_at=_dt(6, 7, 30),
            expires_at=_dt(6, 23),
            decision_time=_dt(6, 7, 30),
        )

        result = _read(conn, readiness, decision_time=_dt(6, 12))

        assert result.ok is False
        assert (
            result.reason_code
            == "REPLACEMENT_ENSEMBLE_CYCLE_AGE_EXCEEDS_BOUND"
        )


def test_same_cycle_shape_without_selected_ensemble_time_is_blocked() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
    )
    row = conn.execute(
        "SELECT provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
        (posterior_id,),
    ).fetchone()
    provenance = json.loads(row[0])
    del provenance["bayes_precision_fusion"]["current_evidence_shape"][
        "source_cycle_time"
    ]
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ? WHERE posterior_id = ?",
        (json.dumps(provenance), posterior_id),
    )
    readiness = _readiness(
        posterior_id=posterior_id,
        computed_at=_dt(6, 7, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 7, 30),
    )

    result = _read(conn, readiness, decision_time=_dt(6, 12))

    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"


def test_stale_absolute_disagreement_row_has_no_held_redecision_authority() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        semantics_revision=(
            STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
        ),
        shape_lag_hours=6.0,
        stale_shape_reused=True,
    )
    readiness = _readiness(
        posterior_id=posterior_id,
        computed_at=_dt(6, 7, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 7, 30),
    )

    result = _read(
        conn,
        readiness,
        decision_time=_dt(6, 12),
        authority_purpose=ReplacementForecastAuthorityPurpose.HELD_REDECISION,
    )

    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"
    assert result.bundle is None


def test_nonfinite_shape_lag_has_no_held_authority() -> None:
    for shape_lag_hours in (float("nan"), float("inf"), float("-inf")):
        conn = _conn()
        posterior_id = _insert_posterior(
            conn,
            source_cycle_time=_dt(6, 0),
            source_available_at=_dt(6, 7),
            computed_at=_dt(6, 7, 30),
            q_mode=_FUSED_FULL,
            with_bounds=True,
            semantics_revision=(
                STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
            ),
            shape_lag_hours=shape_lag_hours,
            stale_shape_reused=True,
        )
        readiness = _readiness(
            posterior_id=posterior_id,
            computed_at=_dt(6, 7, 30),
            expires_at=_dt(6, 23),
            decision_time=_dt(6, 7, 30),
        )

        result = _read(
            conn,
            readiness,
            decision_time=_dt(6, 12),
            authority_purpose=ReplacementForecastAuthorityPurpose.HELD_REDECISION,
        )

        assert result.ok is False
        assert result.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"


def test_translated_stale_shape_has_no_held_redecision_authority() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        semantics_revision=(
            STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
        ),
        shape_lag_hours=6.0,
        stale_shape_reused=True,
        translation_applied=True,
    )
    readiness = _readiness(
        posterior_id=posterior_id,
        computed_at=_dt(6, 7, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 7, 30),
    )

    result = _read(
        conn,
        readiness,
        decision_time=_dt(6, 12),
        authority_purpose=ReplacementForecastAuthorityPurpose.HELD_REDECISION,
    )

    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"


def test_red_staleness_isolates_entry_without_blinding_held_redecision() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
    )
    readiness = _readiness(
        posterior_id=posterior_id,
        computed_at=_dt(6, 7, 30),
        expires_at=_dt(7, 2),
        decision_time=_dt(6, 7, 30),
    )

    entry = _read(conn, readiness, decision_time=_dt(7, 1))
    held = _read(
        conn,
        readiness,
        decision_time=_dt(7, 1),
        authority_purpose=ReplacementForecastAuthorityPurpose.HELD_REDECISION,
    )

    assert entry.ok is False
    assert entry.reason_code == "REPLACEMENT_STALENESS_RED_ENTRY_ISOLATED"
    assert held.ok is True
    assert held.bundle is not None


def _read(
    conn,
    readiness,
    *,
    decision_time,
    authority_purpose=ReplacementForecastAuthorityPurpose.ENTRY,
):
    return read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=readiness,
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=decision_time,
        current_bin_topology_hash=_TOPO_HASH,
        authority_purpose=authority_purpose,
    )


# ---------------------------------------------------------------------------
# Relationship 1: readiness bound to a newer non-live-grade row fails closed;
# an older FUSED row cannot borrow the newer certificate.
# ---------------------------------------------------------------------------
def test_newer_bounds_less_readiness_cannot_borrow_older_fused() -> None:
    conn = _conn()
    # Older live-authority FUSED row (00Z cycle, ~12h before decision -> within staleness bound).
    fused_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
    )
    # NEWER bounds-less diagnostic row (06Z cycle, instruments lag -> BAYES_PRECISION_FUSION_CAPTURE_MISSING).
    diagnostic_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 6),
        source_available_at=_dt(6, 11),
        computed_at=_dt(6, 11, 30),
        q_mode=_BAYES_PRECISION_FUSION_MISSING,
        with_bounds=False,
    )
    assert diagnostic_id > fused_id
    # Readiness points at the newer bounds-less posterior, so the exact certified row
    # is non-executable and the older row cannot be substituted.
    readiness = _readiness(
        posterior_id=diagnostic_id,
        computed_at=_dt(6, 11, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 11, 30),
    )
    result = _read(conn, readiness, decision_time=_dt(6, 12))
    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"


# ---------------------------------------------------------------------------
# Relationship 2: both rows bounds-bearing -> newest wins (no regression in the
#   normal advance-the-cycle case).
# ---------------------------------------------------------------------------
def test_both_bounded_newest_wins() -> None:
    conn = _conn()
    old_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
    )
    new_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 12),
        source_available_at=_dt(6, 18),
        computed_at=_dt(6, 18, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
    )
    assert new_id > old_id
    readiness = _readiness(
        posterior_id=new_id,
        computed_at=_dt(6, 18, 30),
        expires_at=_dt(7, 6),
        decision_time=_dt(6, 18, 30),
    )
    result = _read(conn, readiness, decision_time=_dt(6, 19))
    assert result.ok is True, result.reason_code
    assert result.bundle is not None
    assert result.bundle.posterior_id == new_id
    # No fallback note when the newest row is itself live-grade.
    assert "tradeable_latest_selection" not in dict(result.bundle.provenance_json)


# ---------------------------------------------------------------------------
# Relationship 3: older FUSED row beyond the staleness bound -> NOT served
#   (fail-closed, no silent laundering of a stale cycle into live authority).
# ---------------------------------------------------------------------------
def test_older_fused_beyond_staleness_is_blocked() -> None:
    conn = _conn()
    # FUSED but the cycle is 06-04 00Z vs decision 06-06 12:00 == 60h > 30h bound.
    stale_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(4, 0),
        source_available_at=_dt(4, 7),
        computed_at=_dt(4, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
    )
    # Newer bounds-less diagnostic row on top (also stale-cycle, irrelevant — it's bounds-less).
    _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 6),
        source_available_at=_dt(6, 11),
        computed_at=_dt(6, 11, 30),
        q_mode=_BAYES_PRECISION_FUSION_MISSING,
        with_bounds=False,
    )
    readiness = _readiness(
        posterior_id=stale_id,
        computed_at=_dt(6, 11, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 11, 30),
    )
    result = _read(conn, readiness, decision_time=_dt(6, 12))
    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_LIVE_CYCLE_AGE_EXCEEDS_BOUND"


# ---------------------------------------------------------------------------
# Relationship 4: once readiness advances to a NULL-bounds row, eligibility closes
# until a new live-grade certificate is materialized.
# ---------------------------------------------------------------------------
def test_readiness_advance_to_bounds_less_closes_eligibility() -> None:
    conn = _conn()
    fused_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
    )
    readiness_before = _readiness(
        posterior_id=fused_id,
        computed_at=_dt(6, 7, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 7, 30),
    )
    before = _read(conn, readiness_before, decision_time=_dt(6, 12))
    assert before.ok is True
    assert before.bundle.posterior_id == fused_id

    # The 06Z bounds-less wave lands on top and becomes the exact readiness dependency.
    diagnostic_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 6),
        source_available_at=_dt(6, 11),
        computed_at=_dt(6, 11, 30),
        q_mode=_BAYES_PRECISION_FUSION_MISSING,
        with_bounds=False,
    )
    readiness_after = _readiness(
        posterior_id=diagnostic_id,
        computed_at=_dt(6, 11, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 11, 30),
    )
    after = _read(conn, readiness_after, decision_time=_dt(6, 12))
    assert after.ok is False
    assert after.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"


# ---------------------------------------------------------------------------
# Relationship 5: a readiness-bound row missing q_ucb cannot license either side;
# an older both-bounds row remains a different, uncertified decision-time identity.
def test_readiness_bound_q_ucb_missing_cannot_borrow_older_bounds() -> None:
    conn = _conn()
    # Older row with BOTH bounds (00Z cycle, within staleness).
    both_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        with_ucb=True,
    )
    # NEWER row: FUSED mode, q_lcb present, but q_ucb MISSING (the carrier defect).
    lcb_only_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 6),
        source_available_at=_dt(6, 11),
        computed_at=_dt(6, 11, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        with_ucb=False,
    )
    assert lcb_only_id > both_id
    # Scope readiness points at the newer (q_ucb-less) row, as in production.
    readiness = _readiness(
        posterior_id=lcb_only_id,
        computed_at=_dt(6, 11, 30),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 11, 30),
    )
    result = _read(conn, readiness, decision_time=_dt(6, 12))
    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"


def test_held_pinned_reader_binds_exact_row_with_raw_hwm(monkeypatch) -> None:
    conn = _conn()
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    complete_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        decorrelated_providers_complete=True,
        city="Tel Aviv",
        target_date="2026-06-07",
        strict_day0=True,
    )
    # This newer partial row is intentionally the latest row.  The exact pinned
    # read must still return the older immutable complete carrier; the HWM read is
    # supplied separately and remains read-only.
    _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 6),
        source_available_at=_dt(6, 11),
        computed_at=_dt(6, 11, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        decorrelated_providers_complete=False,
        city="Tel Aviv",
        target_date="2026-06-07",
    )
    statements.clear()
    _bind_test_hwm(monkeypatch, frontier=_dt(6, 7), eligible=_dt(6, 0))

    result = read_pinned_replacement_forecast_bundle(
        conn,
        posterior_id=complete_id,
        city="Tel Aviv",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(6, 12),
        current_bin_topology_hash=_TOPO_HASH,
        authority_purpose=ReplacementForecastAuthorityPurpose.HELD_REDECISION,
        raw_input_hwm_conn=conn,
    )

    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"
    assert result.bundle is not None
    assert result.bundle.posterior_id == complete_id
    assert result.bundle.source_cycle_time == _dt(6, 0).isoformat()
    assert result.bundle.posterior_identity_hash
    assert not any(
        statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))
        for statement in statements
    )

    repeated = read_pinned_replacement_forecast_bundle(
        conn,
        posterior_id=complete_id,
        city="Tel Aviv",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(6, 12),
        current_bin_topology_hash=_TOPO_HASH,
        authority_purpose=ReplacementForecastAuthorityPurpose.HELD_REDECISION,
        raw_input_hwm_conn=conn,
    )
    assert repeated.bundle is not None
    assert repeated.bundle.posterior_identity_hash == result.bundle.posterior_identity_hash


def test_prior_complete_reader_resets_when_newer_cycle_is_complete(monkeypatch) -> None:
    conn = _conn()
    _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        decorrelated_providers_complete=True,
        city="Tel Aviv",
        strict_day0=True,
    )
    _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 6),
        source_available_at=_dt(6, 11),
        computed_at=_dt(6, 11, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        decorrelated_providers_complete=True,
        city="Tel Aviv",
        strict_day0=True,
    )
    _bind_test_hwm(monkeypatch, frontier=_dt(6, 6), eligible=_dt(6, 6))

    result = read_prior_complete_replacement_forecast_bundle(
        conn,
        city="Tel Aviv",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(6, 12),
        current_bin_topology_hash=_TOPO_HASH,
        authority_purpose=ReplacementForecastAuthorityPurpose.HELD_REDECISION,
        raw_input_hwm_conn=conn,
    )

    assert result.ok is False
    assert result.status == "NOT_APPLICABLE"
    assert result.reason_code == "REPLACEMENT_PINNED_COMPLETE_CYCLE_RESET"


def test_prior_complete_frontier_prefers_source_cycle_over_late_old_recompute(monkeypatch) -> None:
    conn = _conn()
    old_complete_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 12),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        decorrelated_providers_complete=True,
        city="Tel Aviv",
        strict_day0=True,
    )
    _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 6),
        source_available_at=_dt(6, 11),
        computed_at=_dt(6, 11),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        decorrelated_providers_complete=False,
        city="Tel Aviv",
    )
    _bind_test_hwm(monkeypatch, frontier=_dt(6, 7), eligible=_dt(6, 6))

    result = read_prior_complete_replacement_forecast_bundle(
        conn,
        city="Tel Aviv",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(6, 13),
        current_bin_topology_hash=_TOPO_HASH,
        authority_purpose=ReplacementForecastAuthorityPurpose.HELD_REDECISION,
        raw_input_hwm_conn=conn,
    )

    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_PINNED_COMPLETE_POSTERIOR_READY"
    assert result.bundle is not None
    assert result.bundle.posterior_id == old_complete_id


def test_prior_complete_frontier_excludes_future_partial_wave(monkeypatch) -> None:
    conn = _conn()
    _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        decorrelated_providers_complete=True,
        city="Tel Aviv",
        strict_day0=True,
    )
    for source_cycle_time, source_available_at, computed_at in (
        (_dt(6, 13), _dt(6, 13, 30), _dt(6, 13, 45)),
        (_dt(6, 11), _dt(6, 13, 30), _dt(6, 13, 45)),
        (_dt(6, 10), _dt(6, 10, 30), _dt(6, 13, 45)),
    ):
        _insert_posterior(
            conn,
            source_cycle_time=source_cycle_time,
            source_available_at=source_available_at,
            computed_at=computed_at,
            q_mode=_FUSED_FULL,
            with_bounds=True,
            decorrelated_providers_complete=False,
            city="Tel Aviv",
        )
    _bind_test_hwm(monkeypatch, frontier=_dt(6, 0), eligible=_dt(6, 0))

    result = read_prior_complete_replacement_forecast_bundle(
        conn,
        city="Tel Aviv",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(6, 12),
        current_bin_topology_hash=_TOPO_HASH,
        authority_purpose=ReplacementForecastAuthorityPurpose.HELD_REDECISION,
        raw_input_hwm_conn=conn,
    )

    assert result.ok is False
    assert result.status == "NOT_APPLICABLE"
    assert result.reason_code == "REPLACEMENT_PINNED_COMPLETE_CYCLE_RESET"


def test_prior_complete_frontier_reaches_carrier_after_large_partial_wave(monkeypatch) -> None:
    conn = _conn()
    old_complete_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 0, 1),
        computed_at=_dt(6, 0, 2),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        decorrelated_providers_complete=True,
        city="Tel Aviv",
        strict_day0=True,
    )
    for minute in range(1, 81):
        source_cycle_time = _dt(6, 0) + timedelta(minutes=minute)
        _insert_posterior(
            conn,
            source_cycle_time=source_cycle_time,
            source_available_at=source_cycle_time + timedelta(minutes=1),
            computed_at=source_cycle_time + timedelta(minutes=2),
            q_mode=_FUSED_FULL,
            with_bounds=True,
            decorrelated_providers_complete=False,
            city="Tel Aviv",
        )
    _bind_test_hwm(monkeypatch, frontier=_dt(6, 1, 21), eligible=_dt(6, 1, 20))

    result = read_prior_complete_replacement_forecast_bundle(
        conn,
        city="Tel Aviv",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(6, 5),
        current_bin_topology_hash=_TOPO_HASH,
        authority_purpose=ReplacementForecastAuthorityPurpose.HELD_REDECISION,
        raw_input_hwm_conn=conn,
    )

    assert result.ok is True
    assert result.bundle is not None
    assert result.bundle.posterior_id == old_complete_id


def test_pinned_reader_is_entry_isolated() -> None:
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(6, 0),
        source_available_at=_dt(6, 7),
        computed_at=_dt(6, 7, 30),
        q_mode=_FUSED_FULL,
        with_bounds=True,
        decorrelated_providers_complete=True,
    )

    result = read_pinned_replacement_forecast_bundle(
        conn,
        posterior_id=posterior_id,
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(6, 12),
        current_bin_topology_hash=_TOPO_HASH,
        authority_purpose=ReplacementForecastAuthorityPurpose.ENTRY,
    )

    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_PINNED_HELD_AUTHORITY_REQUIRED"
