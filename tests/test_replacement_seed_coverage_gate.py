# Created: 2026-06-10
# Last reused or audited: 2026-06-10
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
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from src.data.replacement_forecast_live_materialization_queue import SOURCE_ID, _seed_already_covered
from src.data.replacement_input_hwm import latest_raw_artifact_input_cycle
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


def _seed() -> dict[str, object]:
    return {
        "city": _CITY,
        "target_date": _TARGET_DATE,
        "temperature_metric": _METRIC,
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
                    "used_models": ["gfs_global"],
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
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE forecast_posteriors SET provenance_json = ?",
        (
            json.dumps(
                {
                    "q_lcb_basis": "fused_center_bootstrap_p05",
                    "day0_conditioning": {
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
    seed = {
        **_seed(),
        "computed_at": "2026-06-06T03:00:00+00:00",
        "day0_observed_extreme_observation_time": "2026-06-06T02:00:00+00:00",
    }

    assert _seed_already_covered(forecast_db=db_path, seed=seed) is False


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
                        "used_models": ["gfs_global", "regional_clock"],
                        "current_value_serving": {
                            "gfs_global": {
                                "served_cycle": "2026-06-06T00:00:00+00:00",
                                "captured_at": "2026-06-06T08:00:00+00:00",
                            },
                            "regional_clock": {
                                "served_cycle": "2026-06-06T08:30:00+00:00",
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
        ("regional_clock", "2026-06-06T08:30:00+00:00", "2026-06-06T09:00:00+00:00"),
    ):
        conn.execute(
            """
            INSERT INTO raw_model_forecasts (
                model, city, target_date, metric, source_cycle_time,
                source_available_at, captured_at, lead_days, forecast_value_c,
                endpoint, coverage_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model,
                _CITY,
                _TARGET_DATE,
                _METRIC,
                cycle,
                captured,
                captured,
                1,
                24.0,
                "single_runs",
                "COVERED",
            ),
        )
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
