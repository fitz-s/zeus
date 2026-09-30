# Created: 2026-06-10
# Purpose: Verify exact posterior probability authority cannot mask normal seed repair.
# Reuse: Inspect source-bound coverage and current Day0 carrier contracts before reuse.
# Last reused or audited: 2026-09-30
# Lifecycle: created=2026-06-10; last_reviewed=2026-09-30; last_reused=2026-09-30
# Authority basis: operator staleness/cycle-physics directive 2026-06-10 (#1 graceful-degradation:
#   readiness expiring + no fresher cycle => re-materialize from newest persisted cycle) +
#   tradeable-grade coverage antibody (a NULL-q_lcb / untradeable posterior must not satisfy the
#   seed coverage gate and permanently mask a re-materializable scope).
"""Relationship tests for the seed coverage gate (_seed_already_covered).

The coverage gate sits across the posterior/readiness -> seed boundary: it decides whether a
discovered seed is skipped as "already covered" or re-queued for (re-)materialization. Two
cross-module invariants are pinned, which together make the mask-and-starve category
unconstructible (Fitz: make the wrong state unrepresentable, not patch each instance):

  1. TRADEABLE-GRADE COVERAGE — a covering posterior must have q_lcb_json IS NOT NULL. A
     NULL-bound posterior (BAYES_PRECISION_FUSION_CAPTURE_MISSING / FUSED_Q_BUILD_FAILED) is NOT live-eligible at
     the bundle reader, so it must NOT count as coverage here — otherwise it masks the scope
     forever and the queue never re-materializes it to fusion grade.

  2. FRESH-READINESS COVERAGE (graceful degradation) — an EXPIRED readiness row must NOT count
     as coverage, so a scope whose 3h TTL lapsed re-seeds from the newest persisted cycle
     instead of going dark. (Re-confirmed here as a regression pin alongside #1.)

  3. EXACT DAY0 IDENTITY — source, metric, observed extreme, and observation
     clock must all match. A timestamp-only match cannot mask corrected evidence.
"""

from __future__ import annotations

import copy
import json
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.data.replacement_forecast_cycle_policy import (
    CURRENT_EVIDENCE_SEMANTICS_REVISION,
)
from src.data.replacement_forecast_live_materialization_queue import (
    SOURCE_ID,
    _day0_seed_matches_conditioning,
    _seed_already_covered,
)
from src.data.replacement_input_hwm import (
    _raw_artifact_cycle_for_frozen_request,
    _raw_artifact_cycles_for_frozen_target,
    latest_raw_artifact_input_cycle,
    prime_frozen_replacement_artifact_hwm,
)
from src.state.db import _create_readiness_state
from src.state.schema.v2_schema import apply_canonical_schema


UTC = timezone.utc
_SOURCE_ID = SOURCE_ID
_STRATEGY_KEY = _SOURCE_ID
_CITY = "Shanghai"
_TARGET_DATE = "2026-06-07"
_METRIC = "high"
_BASELINE_RUN = "b0-run"
_OPENMETEO_RUN = "om9-run"


def _current_geometry_fixture():
    from src.contracts.ensemble_snapshot_provenance import GRID_SURFACE_EVIDENCE_REVISION
    geometry = {"revision": "openmeteo_current_provider_geometry_v1",
        "providers": {"gfs_global": {"model": "gfs_global", "selected_latitude":31.25,
            "selected_longitude":121.5,"target_dem_elevation_m":4,
            "native_grid_elevation_m":None,"native_surface":"UNKNOWN"}}}
    return {"grid_surface_evidence_revision": GRID_SURFACE_EVIDENCE_REVISION,
        "grid_surface_evidence_identity_hash": hashlib.sha256(b"fixture-same-model-land-mask").hexdigest(),
        "provider_geometry_evidence": geometry,
        "provider_geometry_identity_hash": hashlib.sha256(json.dumps(geometry, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def _seed() -> dict[str, object]:
    return {
        "city": _CITY,
        "target_date": _TARGET_DATE,
        "temperature_metric": _METRIC,
        "computed_at": "2026-06-06T02:00:00+00:00",
        "baseline_source_run_id": _BASELINE_RUN,
        "openmeteo_source_run_id": _OPENMETEO_RUN,
    }


def _db(tmp_path) -> str:
    db_path = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn, forecast_tables=True)
    _create_readiness_state(conn)
    conn.commit()
    conn.close()
    return str(db_path)


def _insert_posterior(db_path: str, *, q_lcb_json: str | None) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, q_lcb_json, q_ucb_json, posterior_method,
            dependency_source_run_ids_json, provenance_json,
            runtime_layer, training_allowed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _SOURCE_ID,
            "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
            "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1",
            _CITY,
            _TARGET_DATE,
            _METRIC,
            "2026-06-06T00:00:00+00:00",
            "2026-06-06T01:00:00+00:00",
            "2026-06-06T01:30:00+00:00",
            json.dumps({"cold": 0.2, "warm": 0.8}),
            q_lcb_json,
            None if q_lcb_json is None else json.dumps({"cold": 0.3, "warm": 0.9}),
            "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
            json.dumps({"baseline_b0": _BASELINE_RUN, "openmeteo_ifs9_anchor": _OPENMETEO_RUN}),
            json.dumps(
                {
                    "city": _CITY,
                    "q_lcb_basis": "fused_center_bootstrap_p05",
                    "bayes_precision_fusion": {
                        "used_models": ["gfs_global"],
                        "current_evidence_shape": {
                            **_current_geometry_fixture(),
                            "semantics_revision": CURRENT_EVIDENCE_SEMANTICS_REVISION,
                            "shape_lag_hours": 0.0,
                            "source_cycle_time": "2026-06-06T00:00:00+00:00",
                            "stale_shape_reused": False,
                            "translation_applied": False,
                        },
                    },
                }
            ),
            "live",
            0,
        ),
    )
    conn.commit()
    conn.close()


def _insert_readiness(db_path: str, *, expires_at: datetime) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO readiness_state (
            readiness_id, scope_key, scope_type, status, computed_at, strategy_key,
            expires_at, dependency_json, provenance_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "readiness:test",
            f"{_CITY}|{_TARGET_DATE}|{_METRIC}",
            "strategy",
            "READY",
            "2026-06-06T01:30:00+00:00",
            _STRATEGY_KEY,
            expires_at.isoformat(),
            json.dumps(
                {
                    "dependencies": [
                        {"role": "baseline_b0", "source_run_id": _BASELINE_RUN},
                        {"role": "openmeteo_ifs9_anchor", "source_run_id": _OPENMETEO_RUN},
                    ]
                }
            ),
            json.dumps(
                {"city": _CITY, "target_date": _TARGET_DATE, "temperature_metric": _METRIC}
            ),
        ),
    )
    conn.commit()
    conn.close()


def test_null_q_lcb_posterior_does_not_satisfy_coverage(tmp_path) -> None:
    """A posterior with q_lcb_json NULL (untradeable) must NOT count as coverage.

    Even with a fresh (future-expiry) readiness row present, the NULL-bound posterior is not
    live-eligible, so the scope must remain re-seedable instead of being masked forever.
    """
    db_path = _db(tmp_path)
    _insert_posterior(db_path, q_lcb_json=None)
    _insert_readiness(db_path, expires_at=datetime.now(UTC) + timedelta(hours=3))
    assert _seed_already_covered(forecast_db=db_path, seed=_seed()) is False


def test_tradeable_posterior_with_fresh_readiness_is_covered(tmp_path) -> None:
    """A tradeable-grade (q_lcb non-NULL) posterior + fresh readiness DOES count as coverage."""
    db_path = _db(tmp_path)
    _insert_posterior(db_path, q_lcb_json=json.dumps({"cold": 0.1, "warm": 0.7}))
    _insert_readiness(db_path, expires_at=datetime.now(UTC) + timedelta(hours=3))
    assert _seed_already_covered(forecast_db=db_path, seed=_seed()) is True


def test_coverage_gate_preserves_indexed_computed_at_order(
    tmp_path, monkeypatch
) -> None:
    import src.data.replacement_forecast_live_materialization_queue as queue

    db_path = _db(tmp_path)
    _insert_posterior(db_path, q_lcb_json=json.dumps({"cold": 0.1, "warm": 0.7}))
    _insert_readiness(db_path, expires_at=datetime.now(UTC) + timedelta(hours=3))
    trace: list[str] = []

    def _connect(path, **_kwargs):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.set_trace_callback(trace.append)
        return conn

    monkeypatch.setattr(queue, "_queue_read_only_connection", _connect)

    assert _seed_already_covered(forecast_db=db_path, seed=_seed()) is True
    posterior_query = next(
        statement
        for statement in trace
        if "FROM forecast_posteriors" in statement
        and "dependency_source_run_ids_json" in statement
    )
    assert "ORDER BY computed_at DESC" in posterior_query
    assert "datetime(computed_at)" not in posterior_query


def test_newer_posterior_without_matching_readiness_is_not_covered(tmp_path) -> None:
    db_path = _db(tmp_path)
    _insert_posterior(db_path, q_lcb_json=json.dumps({"cold": 0.1, "warm": 0.7}))
    _insert_readiness(db_path, expires_at=datetime.now(UTC) + timedelta(hours=3))
    conn = sqlite3.connect(db_path)
    first_id = int(
        conn.execute("SELECT MAX(posterior_id) FROM forecast_posteriors").fetchone()[0]
    )
    readiness = json.loads(
        conn.execute("SELECT dependency_json FROM readiness_state").fetchone()[0]
    )
    readiness["dependencies"].append(
        {
            "role": "soft_anchor_posterior",
            "posterior_id": first_id,
            "source_run_id": f"posterior:{first_id}",
        }
    )
    conn.execute(
        "UPDATE readiness_state SET dependency_json = ?",
        (json.dumps(readiness),),
    )
    conn.commit()
    conn.close()
    _insert_posterior(db_path, q_lcb_json=json.dumps({"cold": 0.1, "warm": 0.7}))

    assert _seed_already_covered(forecast_db=db_path, seed=_seed()) is False


def test_expired_readiness_does_not_satisfy_coverage(tmp_path) -> None:
    """Graceful degradation (#1): an EXPIRED readiness must NOT count as coverage.

    A tradeable posterior whose readiness TTL lapsed re-seeds from the newest persisted cycle
    rather than staying dark — the inverse of the stale-after-first-cycle starvation.
    """
    db_path = _db(tmp_path)
    _insert_posterior(db_path, q_lcb_json=json.dumps({"cold": 0.1, "warm": 0.7}))
    _insert_readiness(db_path, expires_at=datetime.now(UTC) - timedelta(hours=1))
    assert _seed_already_covered(forecast_db=db_path, seed=_seed()) is False


def test_same_cycle_late_used_model_input_does_not_satisfy_coverage(tmp_path) -> None:
    db_path = _db(tmp_path)
    _insert_posterior(db_path, q_lcb_json=json.dumps({"cold": 0.1, "warm": 0.7}))
    _insert_readiness(db_path, expires_at=datetime.now(UTC) + timedelta(hours=3))
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ?",
        (
            json.dumps(
                {
                    "q_lcb_basis": "fused_center_bootstrap_p05",
                    "bayes_precision_fusion": {
                        "used_models": ["gfs_global"],
                        "current_value_serving": {
                            "gfs_global": {
                                "served_cycle": "2026-06-06T00:00:00+00:00",
                                "captured_at": "2026-06-06T01:00:00+00:00",
                            }
                        },
                    },
                }
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO raw_model_forecasts (
            model, city, target_date, metric, source_cycle_time,
            source_available_at, captured_at, lead_days, forecast_value_c,
            endpoint, coverage_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "gfs_global",
            _CITY,
            _TARGET_DATE,
            _METRIC,
            "2026-06-06T00:00:00+00:00",
            "2026-06-06T02:00:00+00:00",
            "2026-06-06T02:00:00+00:00",
            1,
            24.0,
            "single_runs",
            "COVERED",
        ),
    )
    conn.commit()
    conn.close()
    seed = {**_seed(), "computed_at": "2026-06-06T03:00:00+00:00"}
    assert _seed_already_covered(forecast_db=db_path, seed=seed) is False


def test_newer_day0_observation_seed_does_not_satisfy_coverage(tmp_path) -> None:
    db_path = _db(tmp_path)
    _insert_posterior(db_path, q_lcb_json=json.dumps({"cold": 0.1, "warm": 0.7}))
    _insert_readiness(db_path, expires_at=datetime.now(UTC) + timedelta(hours=3))
    seed = {
        **_seed(),
        "computed_at": "2026-06-06T03:00:00+00:00",
        "day0_observed_extreme_observation_time": "2026-06-06T02:00:00+00:00",
    }
    for provenance_key in ("day0_conditioning", "day0_provisional_observation"):
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE forecast_posteriors SET provenance_json = ?",
            (
                json.dumps(
                    {
                        "q_lcb_basis": "fused_center_bootstrap_p05",
                        provenance_key: {
                            "active": True,
                            "metric": _METRIC,
                            "observation_time": "2026-06-06T01:00:00+00:00",
                        },
                    }
                ),
            ),
        )
        conn.commit()
        conn.close()

        assert _seed_already_covered(forecast_db=db_path, seed=seed) is False


def test_day0_seed_coverage_requires_exact_conditioning_identity(tmp_path) -> None:
    db_path = _db(tmp_path)
    _insert_posterior(db_path, q_lcb_json=json.dumps({"cold": 0.1, "warm": 0.7}))
    _insert_readiness(db_path, expires_at=datetime.now(UTC) + timedelta(hours=3))
    seed = {
        **_seed(),
        "computed_at": "2026-06-06T03:00:00+00:00",
        "day0_observed_extreme_c": 31.0,
        "day0_observed_extreme_source": "aviationweather_metar",
        "day0_observed_extreme_observation_time": "2026-06-06T02:00:00+00:00",
        "day0_observed_extreme_unit": "C",
    }

    def set_conditioning(provenance_key: str, **overrides) -> None:
        conditioning = {
            "active": True,
            "metric": _METRIC,
            "source": "aviationweather_metar",
            "observed_extreme_c": 31.0,
            "observation_time": "2026-06-06T02:00:00+00:00",
            "unit": "C",
            **overrides,
        }
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE forecast_posteriors SET provenance_json = ?",
            (
                json.dumps(
                    {
                        "q_lcb_basis": "fused_center_bootstrap_p05",
                        "bayes_precision_fusion": {
                            "used_models": ["gfs_global"],
                            "current_evidence_shape": {
                                **_current_geometry_fixture(),
                                "semantics_revision": (
                                    CURRENT_EVIDENCE_SEMANTICS_REVISION
                                ),
                                "shape_lag_hours": 0.0,
                                "source_cycle_time": "2026-06-06T00:00:00+00:00",
                                "stale_shape_reused": False,
                                "translation_applied": False,
                            },
                        },
                        provenance_key: conditioning,
                    }
                ),
            ),
        )
        conn.commit()
        conn.close()

    for provenance_key in ("day0_conditioning", "day0_provisional_observation"):
        set_conditioning(provenance_key)
        assert _seed_already_covered(forecast_db=db_path, seed=seed) is True

        set_conditioning(provenance_key, source="wu_icao_history")
        assert _seed_already_covered(forecast_db=db_path, seed=seed) is False

        set_conditioning(provenance_key, observed_extreme_c=30.0)
        assert _seed_already_covered(forecast_db=db_path, seed=seed) is False

        set_conditioning(provenance_key, metric="low")
        assert _seed_already_covered(forecast_db=db_path, seed=seed) is False

        set_conditioning(provenance_key, unit="F")
        assert _seed_already_covered(forecast_db=db_path, seed=seed) is False


def test_day0_coverage_identity_never_matches_incomplete_evidence() -> None:
    """Valid high/low metrics still require every Day0 identity field on both sides."""
    field_pairs = (
        ("day0_observed_extreme_source", "source"),
        ("day0_observed_extreme_observation_time", "observation_time"),
        ("day0_observed_extreme_c", "observed_extreme_c"),
        ("day0_observed_extreme_unit", "unit"),
    )
    for metric in ("high", "low"):
        seed = {
            "temperature_metric": metric,
            "day0_observed_extreme_source": "aviationweather_metar",
            "day0_observed_extreme_observation_time": "2026-06-06T02:00:00+00:00",
            "day0_observed_extreme_c": 31.0,
            "day0_observed_extreme_unit": "C",
        }
        conditioning = {
            "metric": metric,
            "source": "aviationweather_metar",
            "observation_time": "2026-06-06T02:00:00+00:00",
            "observed_extreme_c": 31.0,
            "unit": "C",
        }
        for seed_field, conditioning_field in field_pairs:
            seed_missing = {key: value for key, value in seed.items() if key != seed_field}
            conditioning_missing = dict(conditioning)
            assert _day0_seed_matches_conditioning(seed_missing, conditioning_missing) is False

            seed_missing = dict(seed)
            conditioning_missing = {
                key: value for key, value in conditioning.items() if key != conditioning_field
            }
            assert _day0_seed_matches_conditioning(seed_missing, conditioning_missing) is False

            seed_missing = {key: value for key, value in seed.items() if key != seed_field}
            conditioning_missing = {
                key: value for key, value in conditioning.items() if key != conditioning_field
            }
            assert _day0_seed_matches_conditioning(seed_missing, conditioning_missing) is False


def test_day0_coverage_prefers_active_provisional_over_fallback_conditioning(tmp_path) -> None:
    """Queue coverage uses the same active-provisional identity as drained-marker completion."""
    db_path = _db(tmp_path)
    _insert_posterior(db_path, q_lcb_json=json.dumps({"cold": 0.1, "warm": 0.7}))
    _insert_readiness(db_path, expires_at=datetime.now(UTC) + timedelta(hours=3))
    seed = {
        **_seed(),
        "computed_at": "2026-06-06T03:00:00+00:00",
        "day0_observed_extreme_c": 31.0,
        "day0_observed_extreme_source": "aviationweather_metar",
        "day0_observed_extreme_observation_time": "2026-06-06T02:00:00+00:00",
        "day0_observed_extreme_unit": "C",
    }
    matching = {
        "active": True,
        "metric": _METRIC,
        "source": "aviationweather_metar",
        "observed_extreme_c": 31.0,
        "observation_time": "2026-06-06T02:00:00+00:00",
        "unit": "C",
    }
    stale = {
        "metric": _METRIC,
        "source": "stale_fallback",
        "observed_extreme_c": 0.0,
        "observation_time": "2026-06-06T01:00:00+00:00",
        "unit": "F",
    }

    def set_provenance(*, provisional: dict[str, object], conditioning: dict[str, object]) -> None:
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE forecast_posteriors SET provenance_json = ?",
            (
                json.dumps(
                    {
                        "q_lcb_basis": "fused_center_bootstrap_p05",
                        "bayes_precision_fusion": {
                            "used_models": ["gfs_global"],
                            "current_evidence_shape": {
                                **_current_geometry_fixture(),
                                "semantics_revision": CURRENT_EVIDENCE_SEMANTICS_REVISION,
                                "shape_lag_hours": 0.0,
                                "source_cycle_time": "2026-06-06T00:00:00+00:00",
                                "stale_shape_reused": False,
                                "translation_applied": False,
                            },
                        },
                        "day0_provisional_observation": provisional,
                        "day0_conditioning": conditioning,
                    }
                ),
            ),
        )
        conn.commit()
        conn.close()

    set_provenance(provisional=matching, conditioning=stale)
    assert _seed_already_covered(forecast_db=db_path, seed=seed) is True

    set_provenance(provisional={**stale, "active": True}, conditioning=matching)
    assert _seed_already_covered(forecast_db=db_path, seed=seed) is False

    set_provenance(provisional={**stale, "active": False}, conditioning=matching)
    assert _seed_already_covered(forecast_db=db_path, seed=seed) is True


def test_consumed_regional_clock_newer_than_anchor_cycle_is_covered(tmp_path) -> None:
    db_path = _db(tmp_path)
    _insert_posterior(db_path, q_lcb_json=json.dumps({"cold": 0.1, "warm": 0.7}))
    _insert_readiness(db_path, expires_at=datetime.now(UTC) + timedelta(hours=3))
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE forecast_posteriors SET computed_at = ?, provenance_json = ?",
        (
            "2026-06-06T10:00:00+00:00",
            json.dumps(
                {
                    "q_lcb_basis": "fused_center_bootstrap_p05",
                    "bayes_precision_fusion": {
                        "used_models": ["gfs_global", "ukmo_global_deterministic_10km"],
                        "current_evidence_shape": {
                            **_current_geometry_fixture(),
                            "semantics_revision": CURRENT_EVIDENCE_SEMANTICS_REVISION,
                            "shape_lag_hours": 0.0,
                            "source_cycle_time": "2026-06-06T00:00:00+00:00",
                            "stale_shape_reused": False,
                            "translation_applied": False,
                        },
                        "current_value_serving": {
                            "gfs_global": {
                                "served_cycle": "2026-06-06T00:00:00+00:00",
                                "captured_at": "2026-06-06T08:00:00+00:00",
                            },
                            "ukmo_global_deterministic_10km": {
                                "served_cycle": "2026-06-06T06:00:00+00:00",
                                "captured_at": "2026-06-06T09:00:00+00:00",
                            },
                        },
                    },
                }
            ),
        ),
    )
    for model, cycle, captured in (
        ("gfs_global", "2026-06-06T00:00:00+00:00", "2026-06-06T08:00:00+00:00"),
        ("ukmo_global_deterministic_10km", "2026-06-06T06:00:00+00:00", "2026-06-06T09:00:00+00:00"),
    ):
        from tests.test_openmeteo_cell_selection_and_elevation_are_product_identity import _persist_exact_provider_body
        _persist_exact_provider_body(conn, tmp_path,city=_CITY,metric=_METRIC,target_date=_TARGET_DATE,
            model=model,cycle=cycle,captured=captured,value=24.0)
    from src.data.replacement_current_value_serving import read_current_instrument_values
    served = read_current_instrument_values(conn,city=_CITY,metric=_METRIC,target_date=_TARGET_DATE,
        source_cycle_time_iso="2026-06-06T00:00:00+00:00",decision_time_iso="2026-06-06T11:00:00+00:00")
    provenance = json.loads(conn.execute("SELECT provenance_json FROM forecast_posteriors").fetchone()[0])
    provenance["bayes_precision_fusion"]["current_value_serving"] = {model: value.as_provenance() for model,value in served.items()}
    conn.execute("UPDATE forecast_posteriors SET provenance_json=?",(json.dumps(provenance),))
    conn.commit()
    conn.close()
    seed = {**_seed(), "computed_at": "2026-06-06T11:00:00+00:00"}
    assert _seed_already_covered(forecast_db=db_path, seed=seed) is True


def test_artifact_without_target_day_samples_is_not_an_input_hwm(tmp_path) -> None:
    payload = tmp_path / "next_day_only.json"
    payload.write_text(
        json.dumps(
            {
                "timezone": "Asia/Shanghai",
                "hourly": {
                    "time": ["2026-06-08T00:00", "2026-06-08T01:00"],
                    "temperature_2m": [25.0, 26.0],
                },
            }
        ),
        encoding="utf-8",
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE raw_forecast_artifacts (
            source_id TEXT,
            source_cycle_time TEXT,
            captured_at TEXT,
            source_available_at TEXT,
            artifact_path TEXT,
            artifact_metadata_json TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO raw_forecast_artifacts VALUES (?, ?, ?, ?, ?, ?)",
        (
            "openmeteo_ecmwf_ifs_9km",
            "2026-06-07T06:00:00+00:00",
            "2026-06-07T12:00:00+00:00",
            "2026-06-07T12:00:00+00:00",
            str(payload),
            json.dumps(
                {
                    "city": _CITY,
                    "target_date": _TARGET_DATE,
                    "metric": _METRIC,
                    "openmeteo_payload_json": str(payload),
                }
            ),
        ),
    )
    assert latest_raw_artifact_input_cycle(
        conn,
        city=_CITY,
        target_date=_TARGET_DATE,
        metric=_METRIC,
        decision_time=datetime(2026, 6, 7, 13, tzinfo=UTC),
    ) is None


def test_frozen_selection_batches_artifact_hwm_for_same_target() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE raw_forecast_artifacts (
            source_id TEXT,
            source_cycle_time TEXT,
            captured_at TEXT,
            source_available_at TEXT,
            artifact_metadata_json TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO raw_forecast_artifacts VALUES (?, ?, ?, ?, ?)",
        (
            (
                "openmeteo_ecmwf_ifs_9km",
                "2026-06-07T06:00:00+00:00",
                "2026-06-07T12:00:00+00:00",
                "2026-06-07T12:00:00+00:00",
                json.dumps(
                    {"city": city, "target_date": _TARGET_DATE, "metric": _METRIC}
                ),
            )
            for city in ("Shanghai", "Tokyo")
        ),
    )
    conn.commit()
    conn.execute("BEGIN")
    traced: list[str] = []
    conn.set_trace_callback(traced.append)
    decision_time = datetime(2026, 6, 7, 13, tzinfo=UTC)

    cycles = {
        city: latest_raw_artifact_input_cycle(
            conn,
            city=city,
            target_date=_TARGET_DATE,
            metric=_METRIC,
            decision_time=decision_time,
        )
        for city in ("Shanghai", "Tokyo")
    }
    conn.set_trace_callback(None)
    conn.rollback()
    _raw_artifact_cycles_for_frozen_target.cache_clear()

    assert all(cycle is not None for cycle in cycles.values())
    assert sum(
        "FROM RAW_FORECAST_ARTIFACTS" in statement.upper() for statement in traced
    ) == 1


def test_global_frozen_artifact_prime_matches_scalar_selector(monkeypatch) -> None:
    import src.data.replacement_input_hwm as input_hwm

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE raw_forecast_artifacts (
            source_id TEXT, source_cycle_time TEXT, captured_at TEXT,
            source_available_at TEXT, artifact_metadata_json TEXT
        )
        """
    )
    requests = (
        ("Shanghai", _TARGET_DATE, _METRIC),
        ("Tokyo", "2026-06-08", _METRIC),
    )
    for city, target_date, metric in requests:
        conn.execute(
            "INSERT INTO raw_forecast_artifacts VALUES (?, ?, ?, ?, ?)",
            (
                "openmeteo_ecmwf_ifs_9km",
                "2026-06-07T06:00:00+00:00",
                "2026-06-07T12:00:00+00:00",
                "2026-06-07T12:00:00+00:00",
                json.dumps({"city": city, "target_date": target_date, "metric": metric}),
            ),
        )
    conn.commit()
    conn.execute("BEGIN")
    decision_time = datetime(2026, 6, 7, 13, tzinfo=UTC)

    def read_cycles() -> dict[tuple[str, str, str], datetime | None]:
        return {
            request: latest_raw_artifact_input_cycle(
                conn,
                city=request[0],
                target_date=request[1],
                metric=request[2],
                decision_time=decision_time,
            )
            for request in requests
        }

    scalar = read_cycles()
    traced: list[str] = []
    conn.set_trace_callback(traced.append)
    release = prime_frozen_replacement_artifact_hwm(
        conn,
        requests=requests,
        decision_time=decision_time,
    )
    assert input_hwm._FROZEN_INPUT_HWM.get() is not None
    primed = read_cycles()
    release()
    assert input_hwm._FROZEN_INPUT_HWM.get() is None
    conn.set_trace_callback(None)

    assert primed == scalar
    assert all(cycle is not None for cycle in primed.values())
    assert sum(
        "FROM RAW_FORECAST_ARTIFACTS AS ARTIFACT" in statement.upper()
        for statement in traced
    ) == 1

    def fail_prime(*_args, **_kwargs):
        raise sqlite3.OperationalError("forced prime failure")

    monkeypatch.setattr(input_hwm, "_batch_artifact_cycles", fail_prime)
    _raw_artifact_cycles_for_frozen_target.cache_clear()
    fallback_trace: list[str] = []
    conn.set_trace_callback(fallback_trace.append)
    # A failed canonical snapshot read is UNKNOWN, not permission to switch
    # authorities mid-plan. Keep the valid scalar/batch equality above.
    with pytest.raises(sqlite3.OperationalError, match="forced prime failure"):
        prime_frozen_replacement_artifact_hwm(conn,requests=requests,decision_time=decision_time)
    conn.set_trace_callback(None)
    conn.rollback()
    _raw_artifact_cycles_for_frozen_target.cache_clear()

    assert input_hwm._FROZEN_INPUT_HWM.get() is None


def test_global_frozen_artifact_prime_uses_product_cycle_partition() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE raw_forecast_artifacts (
            source_id TEXT,
            product_id TEXT,
            source_cycle_time TEXT,
            captured_at TEXT,
            source_available_at TEXT,
            artifact_metadata_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_raw_forecast_artifacts_product_cycle
            ON raw_forecast_artifacts(source_id, product_id, source_cycle_time)
        """
    )
    conn.executemany(
        "INSERT INTO raw_forecast_artifacts VALUES (?, ?, ?, ?, ?, ?)",
        (
            (
                "openmeteo_ecmwf_ifs_9km",
                "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                "2026-06-07T00:00:00+00:00",
                "2026-06-07T06:00:00+00:00",
                "2026-06-07T06:00:00+00:00",
                "{malformed",
            ),
            (
                "openmeteo_ecmwf_ifs_9km",
                "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                "2026-06-07T06:00:00+00:00",
                "2026-06-07T12:00:00+00:00",
                "2026-06-07T12:00:00+00:00",
                json.dumps(
                    {"city": _CITY, "target_date": _TARGET_DATE, "metric": _METRIC}
                ),
            ),
        ),
    )
    conn.commit()
    conn.execute("BEGIN")
    traced: list[str] = []
    conn.set_trace_callback(traced.append)
    decision_time = datetime(2026, 6, 7, 13, tzinfo=UTC)

    release = prime_frozen_replacement_artifact_hwm(
        conn,
        requests=((_CITY, _TARGET_DATE, _METRIC),),
        decision_time=decision_time,
    )
    cycle = latest_raw_artifact_input_cycle(
        conn,
        city=_CITY,
        target_date=_TARGET_DATE,
        metric=_METRIC,
        decision_time=decision_time,
    )
    release()
    conn.set_trace_callback(None)
    conn.rollback()

    assert cycle == datetime(2026, 6, 7, 6, tzinfo=UTC)
    assert any(
        "PRODUCT_ID" in statement.upper()
        and "FROM RAW_FORECAST_ARTIFACTS AS ARTIFACT" in statement.upper()
        and "JSON_EXTRACT(ARTIFACT_METADATA_JSON, '$.CITY')" in statement.upper()
        and "SOURCE_CYCLE_TIME <=" in statement.upper()
        for statement in traced
    )
    assert not any("JOIN REQUESTED" in statement.upper() for statement in traced)


def test_scalar_frozen_artifact_hwm_uses_product_cycle_partition() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE raw_forecast_artifacts (
            source_id TEXT,
            product_id TEXT,
            source_cycle_time TEXT,
            captured_at TEXT,
            source_available_at TEXT,
            artifact_metadata_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_raw_forecast_artifacts_product_cycle
            ON raw_forecast_artifacts(source_id, product_id, source_cycle_time)
        """
    )
    conn.executemany(
        "INSERT INTO raw_forecast_artifacts VALUES (?, ?, ?, ?, ?, ?)",
        (
            (
                "openmeteo_ecmwf_ifs_9km",
                "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                "2026-06-07T00:00:00+00:00",
                "2026-06-07T06:00:00+00:00",
                "2026-06-07T06:00:00+00:00",
                "{malformed",
            ),
            (
                "openmeteo_ecmwf_ifs_9km",
                "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                "2026-06-07T06:00:00+00:00",
                "2026-06-07T12:00:00+00:00",
                "2026-06-07T12:00:00+00:00",
                json.dumps(
                    {"city": _CITY, "target_date": _TARGET_DATE, "metric": _METRIC}
                ),
            ),
        ),
    )
    conn.commit()
    conn.execute("BEGIN")
    traced: list[str] = []
    conn.set_trace_callback(traced.append)

    cycle = latest_raw_artifact_input_cycle(
        conn,
        city=_CITY,
        target_date=_TARGET_DATE,
        metric=_METRIC,
        decision_time=datetime(2026, 6, 7, 13, tzinfo=UTC),
    )
    conn.set_trace_callback(None)
    conn.rollback()
    _raw_artifact_cycle_for_frozen_request.cache_clear()

    assert cycle == datetime(2026, 6, 7, 6, tzinfo=UTC)
    assert any(
        "PRODUCT_ID" in statement.upper()
        and "FROM RAW_FORECAST_ARTIFACTS AS ARTIFACT" in statement.upper()
        and "JSON_EXTRACT(ARTIFACT_METADATA_JSON, '$.CITY')" in statement.upper()
        and "SOURCE_CYCLE_TIME <=" in statement.upper()
        for statement in traced
    )
    assert not any("JOIN REQUESTED" in statement.upper() for statement in traced)


def test_nontransaction_scalar_artifact_hwm_uses_product_cycle_partition() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE raw_forecast_artifacts (
            source_id TEXT,
            product_id TEXT,
            source_cycle_time TEXT,
            captured_at TEXT,
            source_available_at TEXT,
            artifact_metadata_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_raw_forecast_artifacts_product_cycle
            ON raw_forecast_artifacts(source_id, product_id, source_cycle_time)
        """
    )
    conn.executemany(
        "INSERT INTO raw_forecast_artifacts VALUES (?, ?, ?, ?, ?, ?)",
        (
            (
                "openmeteo_ecmwf_ifs_9km",
                "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                "2026-06-07T00:00:00+00:00",
                "2026-06-07T06:00:00+00:00",
                "2026-06-07T06:00:00+00:00",
                json.dumps(
                    {"city": "Tokyo", "target_date": _TARGET_DATE, "metric": _METRIC}
                ),
            ),
            (
                "openmeteo_ecmwf_ifs_9km",
                "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                "2026-06-07T06:00:00+00:00",
                "2026-06-07T12:00:00+00:00",
                "2026-06-07T12:00:00+00:00",
                json.dumps(
                    {"city": _CITY, "target_date": _TARGET_DATE, "metric": _METRIC}
                ),
            ),
        ),
    )
    conn.commit()
    traced: list[str] = []
    conn.set_trace_callback(traced.append)

    cycle = latest_raw_artifact_input_cycle(
        conn,
        city=_CITY,
        target_date=_TARGET_DATE,
        metric=_METRIC,
        decision_time=datetime(2026, 6, 7, 13, tzinfo=UTC),
    )
    conn.set_trace_callback(None)

    assert cycle == datetime(2026, 6, 7, 6, tzinfo=UTC)
    payload_queries = [
        statement.upper()
        for statement in traced
        if "FROM RAW_FORECAST_ARTIFACTS" in statement.upper()
        and "ARTIFACT_METADATA_JSON" in statement.upper()
    ]
    family_queries = [
        statement.upper()
        for statement in traced
        if "FROM RAW_FORECAST_ARTIFACTS AS ARTIFACT" in statement.upper()
    ]
    assert family_queries
    assert all("GROUP BY SOURCE_CYCLE_TIME" not in statement for statement in family_queries)
    assert all("SOURCE_CYCLE_TIME <=" in statement for statement in family_queries)
    assert all("DATETIME(SOURCE_CYCLE_TIME) <=" in statement for statement in family_queries)
    assert all("DATETIME(CAPTURED_AT) <=" in statement for statement in family_queries)
    assert all(
        "DATETIME(SOURCE_AVAILABLE_AT) <=" in statement
        for statement in family_queries
    )
    assert payload_queries
    assert all("SOURCE_CYCLE_TIME <=" in statement for statement in payload_queries)


@pytest.mark.parametrize("conditioned", [False, True])
@pytest.mark.parametrize("metric", ["high", "low"])
@pytest.mark.parametrize("posterior_time, expected", [
    ("2026-06-06T01:30:00+00:00", False),
    ("2026-06-06T02:00:00+00:00", True),
    ("2026-06-06T02:30:00+00:00", True),
])
def test_recompute_seed_requires_posterior_at_or_after_requested_clock(
    tmp_path, metric, posterior_time, expected, conditioned,
):
    db_path = _db(tmp_path)
    _insert_posterior(db_path, q_lcb_json=json.dumps({"cold": 0.1, "warm": 0.7}))
    _insert_readiness(db_path, expires_at=datetime.now(UTC) + timedelta(hours=3))
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE forecast_posteriors SET computed_at=?, temperature_metric=?",
                     (posterior_time, metric))
        conn.execute("UPDATE readiness_state SET provenance_json=json_set(provenance_json, '$.temperature_metric', ?)",
                     (metric,))
    seed = {**_seed(), "temperature_metric": metric}
    if conditioned:
        seed.update({
            "day0_observed_extreme_c": 31.0 if metric == "high" else 12.0,
            "day0_observed_extreme_source": "aviationweather_metar",
            "day0_observed_extreme_observation_time": "2026-06-06T01:00:00+00:00",
            "day0_observed_extreme_unit": "C",
        })
        conditioning = {
            "metric": metric, "source": seed["day0_observed_extreme_source"],
            "observed_extreme_c": seed["day0_observed_extreme_c"],
            "observation_time": seed["day0_observed_extreme_observation_time"], "unit": "C",
        }
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE forecast_posteriors SET provenance_json=json_set(provenance_json, '$.day0_conditioning', json(?))",
                         (json.dumps(conditioning),))
    assert _seed_already_covered(forecast_db=db_path, seed=seed) is True
    seed["upgrade_trigger"] = "held_belief_computed_age_expired"
    assert _seed_already_covered(forecast_db=db_path, seed=seed) is expected
    if conditioned:
        seed["day0_observed_extreme_c"] += 1.0
        assert _seed_already_covered(forecast_db=db_path, seed=seed) is False


@pytest.fixture
def _normal_kord_fast_coverage(tmp_path, monkeypatch):
    """Actual physical/collector/FAST writers; only external forecast bytes controlled."""
    from dataclasses import replace
    from tests.integration import test_w3_solve_seam_g3 as normal
    from src.data import day0_fast_obs as fast, day0_hourly_vectors as hourly
    from src.data.replacement_forecast_materializer import materialize_replacement_forecast_live

    native = normal._noaa_native_sources.__wrapped__(tmp_path, monkeypatch)
    next(native)
    fixture = None
    try:
        fixture = normal._kord_normal_prior_fixture(tmp_path, monkeypatch)
        normal._kord_public_bundles(fixture, monkeypatch, at=fixture.cut)
        ordinary_seed = dict(city=fixture.request.city, target_date=str(fixture.request.target_date),
            temperature_metric=fixture.request.temperature_metric, computed_at=fixture.cut.isoformat(),
            baseline_source_run_id=fixture.request.baseline_source_run_id,
            openmeteo_source_run_id=fixture.request.openmeteo_source_run_id,
            source_cycle_time=fixture.request.source_cycle_time.isoformat())
        assert _seed_already_covered(forecast_db=fixture.db, forecast_conn=fixture.conn, seed=ordinary_seed)
        fixture.conn.execute("PRAGMA query_only=OFF")
        from src.data import replacement_forecast_cycle_policy as policy
        from src.data.replacement_forecast_live_materialization_queue import _blocked_attempt_fingerprint
        ordinary_fp = _blocked_attempt_fingerprint(input_json=tmp_path / "seed.json",
            forecast_db=fixture.db, payload=ordinary_seed)
        assert ordinary_fp is not None
        with monkeypatch.context() as old_route:
            old_route.setattr(policy, "DAY0_FAST_RESIDUAL_COVERAGE_REVISION", "previous-FAST-reader-route")
            assert _blocked_attempt_fingerprint(input_json=tmp_path / "seed.json",
                forecast_db=fixture.db, payload=ordinary_seed) == ordinary_fp
        cut, conditioning = normal._kord_causal_fast_inputs(fixture, monkeypatch)
        fixture.request = replace(fixture.request, computed_at=cut, day0_observation_state=None,
            day0_observed_extreme_c=conditioning.observed_extreme_c,
            day0_observed_extreme_source=fast.FAST_RESIDUAL_CONDITIONING_SOURCE_ID,
            day0_observed_extreme_observation_time=conditioning.observation_time,
            day0_observed_extreme_sample_count=conditioning.sample_count,
            day0_observed_extreme_unit=conditioning.unit)
        calls = []
        builder = hourly.build_day0_remaining_probability_carrier

        def traced(**kwargs):
            calls.append(copy.deepcopy(kwargs))
            return builder(**kwargs)

        with monkeypatch.context() as trace:
            trace.setattr(hourly, "build_day0_remaining_probability_carrier", traced)
            fixture.sql_clock[0] = cut
            fixture.result = materialize_replacement_forecast_live(fixture.conn, fixture.request)
            assert fixture.result.ok, fixture.result.reason_codes
            fixture.conn.commit()
            normal._kord_public_bundles(fixture, monkeypatch, at=cut)
        fixture.cut = cut
        fixture.base_builder_inputs = calls[-1]
        yield fixture
    finally:
        if fixture is not None:
            fixture.conn.close()
            fixture.builtin.close()
        next(native, None)


def test_normal_fast_wrong_channel_cannot_cover_its_seed(_normal_kord_fast_coverage, monkeypatch, tmp_path):
    """Self-consistent foreign product proof cannot starve this family's normal repair."""
    from src.data import day0_fast_obs as fast, day0_hourly_vectors as hourly
    from src.data.replacement_forecast_bundle_reader import _wu_fast_pinned_carrier_reason
    from src.data.replacement_forecast_current_target_plan import _covering_posterior_input_lag_reason
    from src.data.replacement_forecast_cycle_policy import tradeable_grade_coverage_sql
    from src.data import replacement_forecast_live_materialization_queue as queue
    from src import config

    fixture = _normal_kord_fast_coverage
    conn, request = fixture.conn, fixture.request
    seed = dict(city=request.city, target_date=str(request.target_date),
        temperature_metric=request.temperature_metric, computed_at=fixture.cut.isoformat(),
        baseline_source_run_id=request.baseline_source_run_id,
        openmeteo_source_run_id=request.openmeteo_source_run_id)
    row = dict(conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",
        (fixture.result.posterior_id,)).fetchone())
    proof = json.loads(row["provenance_json"])
    assert _seed_already_covered(forecast_db=fixture.db, forecast_conn=conn, seed=seed)
    columns = {str(item[1]) for item in conn.execute("PRAGMA table_info(forecast_posteriors)")}
    clause = tradeable_grade_coverage_sql(posterior_columns=columns, decision_time=fixture.cut, alias="p.")

    def plan_reason():
        return _covering_posterior_input_lag_reason(conn, city=request.city,
            target_date=str(request.target_date), temperature_metric=request.temperature_metric,
            decision_time=fixture.cut, baseline_source_run_id=request.baseline_source_run_id,
            openmeteo_source_run_id=request.openmeteo_source_run_id,
            posterior_tradeable_grade_clause=clause)

    assert plan_reason() is None
    raw_before = [tuple(item) for item in conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id")]
    fp_payload = {**seed, "source_cycle_time": request.source_cycle_time.isoformat()}
    dumped = []
    real_dumps = json.dumps

    def capture_identity(value, *args, **kwargs):
        if isinstance(value, dict) and "logic" in value and "raw" in value and "request" in value:
            dumped.append(copy.deepcopy(value))
        return real_dumps(value, *args, **kwargs)

    with monkeypatch.context() as identity_trace:
        identity_trace.setattr(queue.json, "dumps", capture_identity)
        current_fp = queue._blocked_attempt_fingerprint(input_json=tmp_path / "seed.json",
            forecast_db=fixture.db, payload=fp_payload)
    assert current_fp is not None and len(dumped) == 1
    dependency = dumped[0].pop("fast_residual_coverage")
    assert dependency["settlement_channel"] == "noaa_wrh_kord"
    # Exact pre-fix fingerprint format: every original component unchanged,
    # only the new stable FAST route dependency was absent.
    old_fp = hashlib.sha256(real_dumps(dumped[0], sort_keys=True,
        separators=(",", ":"), default=str).encode()).hexdigest()
    assert old_fp != current_fp
    marker_dir = tmp_path / "blocked"
    marker_dir.mkdir()
    marker = queue._blocked_attempt_marker_path(marker_dir, fp_payload)
    marker.write_text(real_dumps({"attempt_fingerprint": old_fp}))
    assert queue._blocked_attempt_state(marker_dir=marker_dir, input_json=tmp_path / "seed.json",
        forecast_db=fixture.db, payload=fp_payload)[2] is False
    marker.write_text(real_dumps({"attempt_fingerprint": current_fp}))
    assert queue._blocked_attempt_state(marker_dir=marker_dir, input_json=tmp_path / "seed.json",
        forecast_db=fixture.db, payload=fp_payload)[2] is True
    wrong = copy.deepcopy(proof)
    likelihood = wrong["day0_provisional_observation"]["fast_residual_likelihood"]
    likelihood["settlement_channel"] = "wu_icao_history"
    fields = ("semantics_revision", "station_id", "settlement_channel", "fast_channel", "unit",
        "as_of", "window_start", "matched_pairs", "unknown_weight", "settlement_extreme_c")
    identity = {key: likelihood[key] for key in fields}
    identity["residual_weights_c"] = tuple((item["residual_c"], item["weight"])
        for item in likelihood["residual_weights_c"])
    likelihood["identity_hash"] = hashlib.sha256(json.dumps(identity, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    assert fast.validated_fast_residual_day0_conditioning(wrong["day0_provisional_observation"]) is not None
    inputs = copy.deepcopy(fixture.base_builder_inputs)
    inputs["identity_inputs"]["preliminary_survival_identity"] = likelihood["identity_hash"]
    rebuilt = hourly.build_day0_remaining_probability_carrier(**inputs)
    wrong.update(day0_remaining_carrier_content_identity=rebuilt["content_identity"],
        day0_remaining_carrier_q=rebuilt["q"], day0_remaining_carrier_probability_samples=rebuilt["samples"])
    scope = dict(city=request.city, target_date=str(request.target_date),
        metric=request.temperature_metric, decision_time=fixture.cut)
    assert _wu_fast_pinned_carrier_reason(wrong, **scope) is not None
    owner = config.settlement_source_type_for_city
    with monkeypatch.context() as foreign_contract:
        foreign_contract.setattr(config, "settlement_source_type_for_city",
            lambda city, target: "wu_icao" if city.name == "Chicago" else owner(city, target))
        assert _wu_fast_pinned_carrier_reason(wrong, **scope) is None
    # Hostile proof corruption is only in this private fixture. Production
    # repair must append a normal successor, never update the damaged row.
    conn.execute("PRAGMA query_only=OFF")
    conn.execute("UPDATE forecast_posteriors SET provenance_json=? WHERE posterior_id=?",
        (json.dumps(wrong), row["posterior_id"]))
    conn.commit()
    damaged = dict(conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",
        (row["posterior_id"],)).fetchone())
    assert not _seed_already_covered(forecast_db=fixture.db, forecast_conn=conn, seed=seed)
    assert plan_reason() == "basis=current_evidence_probability_authority_invalid"
    from src.data.replacement_forecast_bundle_reader import (
        read_replacement_forecast_bundle, ReplacementForecastAuthorityPurpose,
    )
    from src.data.replacement_forecast_readiness import latest_replacement_readiness
    ready = latest_replacement_readiness(conn, city=request.city, target_date=str(request.target_date),
        temperature_metric=request.temperature_metric, decision_time=fixture.cut)
    from src.data import replacement_forecast_bundle_reader as reader

    class ClockType(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)

    class ReaderClock(datetime, metaclass=ClockType):
        @classmethod
        def now(cls, tz=None):
            return fixture.cut.astimezone(tz) if tz else fixture.cut.replace(tzinfo=None)

    with monkeypatch.context() as consumer:
        consumer.setattr(reader, "datetime", ReaderClock)
        for purpose in ReplacementForecastAuthorityPurpose:
            refused = read_replacement_forecast_bundle(conn, baseline_bundle=None, readiness=ready,
                city=request.city, target_date=str(request.target_date), temperature_metric=request.temperature_metric,
                decision_time=fixture.cut, require_baseline_bundle=False, enforce_raw_input_hwm=True,
                authority_purpose=purpose)
            assert refused.bundle is None
            assert refused.reason_code == "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE"
    from dataclasses import replace
    from src.data.replacement_forecast_materializer import materialize_replacement_forecast_live
    from tests.integration.test_w3_solve_seam_g3 import _kord_public_bundles

    conn.execute("PRAGMA query_only=OFF")
    new_cut = fixture.cut + timedelta(minutes=1)
    fixture.sql_clock[0] = new_cut
    fixture.request = replace(request, computed_at=new_cut)
    fixture.result = materialize_replacement_forecast_live(conn, fixture.request)
    assert fixture.result.ok, fixture.result.reason_codes
    assert fixture.result.posterior_id != row["posterior_id"]
    conn.commit()
    _kord_public_bundles(fixture, monkeypatch, at=new_cut)
    new_seed = {**seed, "computed_at": new_cut.isoformat()}
    assert _seed_already_covered(forecast_db=fixture.db, forecast_conn=conn, seed=new_seed)
    assert _seed_already_covered(forecast_db=fixture.db, forecast_conn=conn, seed=new_seed)
    repaired = dict(conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",
        (fixture.result.posterior_id,)).fetchone())
    assert repaired["computed_at"] != damaged["computed_at"]
    assert repaired["provenance_json"] != damaged["provenance_json"]
    assert json.loads(repaired["provenance_json"])["day0_provisional_observation"]["fast_residual_likelihood"][
        "settlement_channel"] == "noaa_wrh_kord"
    for field in ("source_cycle_time", "source_available_at"):
        assert repaired[field] == damaged[field]
    assert dict(conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",
        (row["posterior_id"],)).fetchone()) == damaged
    assert [tuple(item) for item in conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id")] == raw_before
