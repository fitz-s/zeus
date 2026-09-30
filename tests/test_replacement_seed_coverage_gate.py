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
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

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


def _seed_from_certificate(normal, *, conditioning=False):
    request = normal.request
    seed = dict(city=request.city, target_date=str(request.target_date),
        temperature_metric=request.temperature_metric, computed_at=request.computed_at.isoformat(),
        baseline_source_run_id=request.baseline_source_run_id,
        openmeteo_source_run_id=request.openmeteo_source_run_id)
    if conditioning:
        observed_at = request.day0_observed_extreme_observation_time
        assert observed_at is not None and request.day0_observed_extreme_c is not None, (
            "Conditioned coverage requires a real metric-specific Day0 collector/public world")
        seed.update(day0_observed_extreme_c=request.day0_observed_extreme_c,
            day0_observed_extreme_source=request.day0_observed_extreme_source,
            day0_observed_extreme_observation_time=(observed_at.isoformat()
                if isinstance(observed_at, datetime) else observed_at),
            day0_observed_extreme_unit=request.day0_observed_extreme_unit)
    return seed


def _pin_seed_consumer_now(conn, cut, builtin):
    """Declared private SQL consumer time, without renewing any stored clock."""
    conn.create_function("strftime", 2, lambda fmt, value:
        cut.isoformat(timespec="milliseconds")
        if (fmt, value) == ("%Y-%m-%dT%H:%M:%f+00:00", "now") else
        cut.strftime(fmt) if (fmt, value) == ("%Y-%m-%dT%H:%M:%S", "now") else
        builtin.execute("SELECT strftime(?,?)", (fmt, value)).fetchone()[0])


@pytest.fixture
def _normal_seed_certificate(tmp_path, monkeypatch, request):
    """Read-import the actual HIGH/LOW collector/public worlds, never retag a quantity."""
    from tests.test_replacement_forecast_bundle_reader import (
        _shanghai_reader_current_certificate, _shanghai_reader_certificate,
    )
    from src.data.station_ground_evidence import forecast_db_from_connection

    params = getattr(request.node, "callspec", SimpleNamespace(params={})).params
    metric = params.get("metric", "high")
    source = None
    if metric == "low":
        normal = request.getfixturevalue("_normal_kord_fast_coverage")
    else:
        if params.get("conditioned"):
            source = _shanghai_reader_certificate(tmp_path.resolve(), monkeypatch, expires_at=None,
                target_date=date(2026, 10, 1), source_cycle_time=datetime(2026, 9, 30, 12, tzinfo=UTC),
                first_compute_at=datetime(2026, 9, 30, 20, 5, tzinfo=UTC),
                computed_at=datetime(2026, 10, 1, 8, 15, tzinfo=UTC),
                ground_recorded_at=datetime(2026, 9, 30, 13, tzinfo=UTC))
        else:
            source = _shanghai_reader_current_certificate.__wrapped__(tmp_path, monkeypatch)
        normal = next(source)
    builtin = sqlite3.connect(":memory:")
    try:
        cut = normal.request.computed_at
        # Consumer wall time is the declared private decision cut. It does not
        # renew any published source/receipt/posterior/expiry field.
        _pin_seed_consumer_now(normal.conn, cut, builtin)
        yield SimpleNamespace(conn=normal.conn, db=forecast_db_from_connection(normal.conn),
            request=normal.request, cut=cut)
    finally:
        if source is not None:
            next(source, None)
        builtin.close()


@pytest.fixture
def _normal_ordinary_kord_seed_certificate(tmp_path, monkeypatch):
    """Reuse the approved ordinary WRH setup, before its offline-fit experiment."""
    import inspect
    from tests import test_day0_remaining_day_pricing as ordinary

    # One existing normal collector/WRH-trigger/materializer/public setup.
    # This thin read-import changes neither its forecast inputs nor authority;
    # it only yields the world before that test starts the unrelated fit twin.
    name = "test_ordinary_wrh_amber_current_kernel_ignores_age_fit"
    setup = inspect.getsource(getattr(ordinary, name))
    setup = setup[:setup.index("        # A real, hash-sealed controlled offline artifact;")]
    setup = setup.replace(f"def {name}(", "def _ordinary_seed_world(", 1)
    setup += """        fixture.cut = cut
        _pin_seed_consumer_now(fixture.conn, cut, fixture.builtin)
        yield fixture
    finally:
        if fixture is not None:
            fixture.conn.close()
            fixture.builtin.close()
        next(native, None)
"""
    namespace = dict(vars(ordinary))
    namespace["_pin_seed_consumer_now"] = _pin_seed_consumer_now
    exec(compile(setup, ordinary.__file__, "exec"), namespace)
    yield from namespace["_ordinary_seed_world"](tmp_path.resolve(), monkeypatch)


def test_tradeable_posterior_with_fresh_readiness_is_covered(_normal_seed_certificate) -> None:
    """A tradeable-grade (q_lcb non-NULL) posterior + fresh readiness DOES count as coverage."""
    normal = _normal_seed_certificate
    assert _seed_already_covered(forecast_db=normal.db, forecast_conn=normal.conn,
        seed=_seed_from_certificate(normal)) is True


def test_coverage_gate_preserves_indexed_computed_at_order(
    _normal_seed_certificate,
) -> None:
    normal = _normal_seed_certificate
    trace: list[str] = []
    normal.conn.set_trace_callback(trace.append)
    assert _seed_already_covered(forecast_db=normal.db, forecast_conn=normal.conn,
        seed=_seed_from_certificate(normal)) is True
    normal.conn.set_trace_callback(None)
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


def test_day0_seed_coverage_requires_exact_conditioning_identity(_normal_kord_fast_coverage) -> None:
    """Actual LOW FAST authority; every requested identity field remains exact."""
    from src.data.replacement_cycle_advance_trigger import _active_day0_provisional_or_conditioning

    normal = _normal_kord_fast_coverage
    _pin_seed_consumer_now(normal.conn, normal.cut, normal.builtin)
    seed = _seed_from_certificate(normal, conditioning=True)
    row = dict(normal.conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",
        (normal.result.posterior_id,)).fetchone())
    provenance = json.loads(row["provenance_json"])
    matching = dict(_active_day0_provisional_or_conditioning(provenance))
    assert _seed_already_covered(forecast_db=normal.db, forecast_conn=normal.conn, seed=seed) is True
    # Both legacy selector branches still enforce the same identity. Only
    # the actual active FAST branch is claimed to carry public authority.
    for key in ("day0_conditioning", "day0_provisional_observation"):
        assert _day0_seed_matches_conditioning(seed,
            _active_day0_provisional_or_conditioning({key: matching}))
    for field, changed in (
        ("day0_observed_extreme_source", "wu_icao_history"),
        ("day0_observed_extreme_c", seed["day0_observed_extreme_c"] + 1),
        ("temperature_metric", "high"),
        ("day0_observed_extreme_unit", "C" if seed["day0_observed_extreme_unit"] == "F" else "F"),
        ("day0_observed_extreme_observation_time",
            (datetime.fromisoformat(seed["day0_observed_extreme_observation_time"])
                - timedelta(minutes=1)).isoformat()),
    ):
        assert _seed_already_covered(forecast_db=normal.db, forecast_conn=normal.conn,
            seed={**seed, field: changed}) is False, field
    assert _seed_already_covered(forecast_db=normal.db, forecast_conn=normal.conn, seed=seed) is True
    assert dict(normal.conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",
        (normal.result.posterior_id,)).fetchone()) == row


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


def test_day0_coverage_prefers_active_provisional_over_fallback_conditioning(
    _normal_ordinary_kord_seed_certificate,
) -> None:
    """Queue coverage uses the same active-provisional identity as drained-marker completion."""
    from src.data.replacement_cycle_advance_trigger import _active_day0_provisional_or_conditioning

    normal = _normal_ordinary_kord_seed_certificate
    seed = _seed_from_certificate(normal, conditioning=True)
    conn = normal.conn
    row = dict(conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",
        (normal.result.posterior_id,)).fetchone())
    proof = json.loads(row["provenance_json"])
    matching = dict(_active_day0_provisional_or_conditioning(proof))
    assert matching["source"] == "noaa_wrh_kord"
    assert "fast_residual_likelihood" not in matching
    assert _seed_already_covered(forecast_db=normal.db, forecast_conn=conn, seed=seed) is True
    stale = {
        "metric": normal.request.temperature_metric,
        "source": "stale_fallback",
        "observed_extreme_c": 0.0,
        "observation_time": (normal.cut - timedelta(hours=1)).isoformat(),
        "unit": "C",
    }

    def set_provenance(*, provisional: dict[str, object], conditioning: dict[str, object]) -> None:
        varied = {**proof, "day0_provisional_observation": provisional,
            "day0_conditioning": conditioning}
        conn.execute("PRAGMA query_only=OFF")
        conn.execute("UPDATE forecast_posteriors SET provenance_json=? WHERE posterior_id=?",
            (json.dumps(varied), row["posterior_id"]))
        conn.commit()
    try:
        set_provenance(provisional=matching, conditioning=stale)
        assert _seed_already_covered(forecast_db=normal.db, forecast_conn=conn, seed=seed) is True

        set_provenance(provisional={**stale, "active": True}, conditioning=matching)
        assert _seed_already_covered(forecast_db=normal.db, forecast_conn=conn, seed=seed) is False

        set_provenance(provisional={**stale, "active": False}, conditioning=matching)
        assert _seed_already_covered(forecast_db=normal.db, forecast_conn=conn, seed=seed) is True
    finally:
        # Only this private selector-metadata attack varies; source/native/q
        # proof and the original published tuple are restored exactly.
        conn.execute("PRAGMA query_only=OFF")
        conn.execute("UPDATE forecast_posteriors SET provenance_json=? WHERE posterior_id=?",
            (row["provenance_json"], row["posterior_id"]))
        conn.commit()
    assert dict(conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",
        (row["posterior_id"],)).fetchone()) == row
    assert _seed_already_covered(forecast_db=normal.db, forecast_conn=conn, seed=seed) is True


def test_consumed_regional_clock_newer_than_anchor_cycle_is_covered(tmp_path, monkeypatch) -> None:
    """A real US HRRR full-prior source can be newer than its native ENS/anchor."""
    import math
    from dataclasses import replace
    from datetime import date
    from zoneinfo import ZoneInfo
    from tests.integration import test_w3_solve_seam_g3 as normal
    from src.data import bayes_precision_fusion_download as dl, openmeteo_model_surface as surface
    from src.data.openmeteo_ecmwf_ifs9_anchor import SINGLE_RUNS_FORECAST_URL
    from src.data.replacement_current_value_serving import read_current_instrument_values
    from src.data.replacement_forecast_materializer import materialize_replacement_forecast_live

    root = tmp_path.resolve()
    native = normal._noaa_native_sources.__wrapped__(root, monkeypatch)
    next(native)
    fixture = None
    try:
        # The next local day is necessary: a 06Z HRRR cannot reconstruct the
        # original KORD Day0 prefix starting 05Z. The real collector builds
        # lead1, rather than relabelling a Day0 LOW snapshot or its request.
        fixture = normal._kord_normal_prior_fixture(root, monkeypatch,
            target_date=date(2026, 10, 2))
        normal._kord_public_bundles(fixture, monkeypatch, at=fixture.cut)
        original_id = fixture.result.posterior_id
        original = tuple(fixture.conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",
            (original_id,)).fetchone())
        run = fixture.request.source_cycle_time + timedelta(hours=6)
        capture, cut = fixture.cut + timedelta(minutes=5), fixture.cut + timedelta(minutes=10)
        fixture.sql_clock[0] = capture
        target = dl.BayesPrecisionFusionDownloadTarget(city=fixture.city.name, metric="low",
            target_date=str(fixture.request.target_date), lead_days=1,
            latitude=fixture.city.lat, longitude=fixture.city.lon, timezone_name=fixture.city.timezone)
        profile = surface._profile("gfs_hrrr")
        px, py = surface._project(profile, latitude=target.latitude, longitude=target.longitude)
        indices = [int(surface._float32(surface._float32(value-profile[f"origin_{axis}"])
            / profile["dx" if axis == "x" else "dy"]) + .5)
            for value, axis in ((px, "x"), (py, "y"))]
        selected_lat, selected_lon = surface._project(profile,
            x=surface._float32(surface._float32(surface._float32(indices[0])*profile["dx"])+profile["origin_x"]),
            y=surface._float32(surface._float32(surface._float32(indices[1])*profile["dy"])+profile["origin_y"]))
        selected_lon = surface._float32(math.fmod(surface._float32(selected_lon+180), 360)-180)
        params = dict(latitude=target.latitude, longitude=target.longitude, timezone=target.timezone_name,
            models="gfs_hrrr", hourly="temperature_2m", temperature_unit="celsius",
            cell_selection="land", run=run.replace(tzinfo=None).isoformat())
        midnight = datetime.combine(fixture.request.target_date, datetime.min.time(),
            tzinfo=ZoneInfo(target.timezone_name))
        payload = dict(latitude=selected_lat, longitude=selected_lon, elevation=32.,
            timezone=target.timezone_name, utc_offset_seconds=int(midnight.utcoffset().total_seconds()),
            hourly_units={"temperature_2m": "°C"},
            hourly={"time": [f"{target.target_date}T{hour:02d}:00" for hour in range(24)],
                "temperature_2m": [19.0]*24})
        body = json.dumps(payload, sort_keys=True).encode()
        from tests.test_openmeteo_cell_selection_and_elevation_are_product_identity import _download_time
        with monkeypatch.context() as acquisition:
            # Both acquisition and canonical INSERT use this actual private
            # event time; no post-publication clock field is rewritten.
            _download_time(acquisition, dl, capture)
            bound = dl._bind_physical_response(payload, model="gfs_hrrr", url=SINGLE_RUNS_FORECAST_URL,
                params=params, run=run, captures=[(body, capture.timestamp())],
                network_captures=[(body, capture.timestamp(), {"content-type": "application/json"})])
            row = dict(model="gfs_hrrr", city=target.city, metric=target.metric, target_date=target.target_date,
                source_cycle_time=run.isoformat(), source_available_at=capture.isoformat(),
                captured_at=capture.isoformat(), lead_days=1, forecast_value_c=19.0, endpoint="single_runs",
                _physical_response=bound[dl._BATCH_PHYSICAL_RESPONSE_KEY],
                **dl._bayes_precision_fusion_product_identity("gfs_hrrr", "single_runs", target))
            assert dl._persist_rows(fixture.conn, [row]) == 1
        fixture.conn.commit()
        served = read_current_instrument_values(fixture.conn, city=target.city, metric="low",
            target_date=target.target_date, source_cycle_time_iso=fixture.request.source_cycle_time.isoformat(),
            decision_time_iso=cut.isoformat())
        assert "gfs_hrrr" in served, sorted(served)
        assert served["gfs_hrrr"].served_cycle == run.isoformat()
        fixture.sql_clock[0] = cut
        fixture.request = replace(fixture.request, computed_at=cut)
        fixture.result = materialize_replacement_forecast_live(fixture.conn, fixture.request)
        assert fixture.result.ok, fixture.result.reason_codes
        fixture.conn.commit()
        public = normal._kord_public_bundles(fixture, monkeypatch, at=cut)
        proof = next(iter(public.values())).provenance_json["bayes_precision_fusion"]
        assert "gfs_hrrr" in proof["used_models"]
        assert datetime.fromisoformat(proof["current_value_serving"]["gfs_hrrr"]["served_cycle"]) > fixture.request.source_cycle_time
        _pin_seed_consumer_now(fixture.conn, cut, fixture.builtin)
        seed = _seed_from_certificate(fixture)
        assert _seed_already_covered(forecast_db=fixture.db, forecast_conn=fixture.conn, seed=seed) is True
        # The former bare GFS identity remains unsupported; no metadata can
        # grant it the native HRRR proof used by the lawful positive.
        fixture.conn.execute("PRAGMA query_only=OFF")
        raw_before = [tuple(item) for item in fixture.conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id")]
        from tests.test_openmeteo_cell_selection_and_elevation_are_product_identity import _persist_exact_provider_body
        with pytest.raises(ValueError, match="MODEL_SURFACE_UNSUPPORTED"):
            _persist_exact_provider_body(fixture.conn, root, city=target.city, metric="low",
                target_date=target.target_date, model="gfs_global", cycle=run.isoformat(),
                captured=capture.isoformat(), value=19.0)
        assert [tuple(item) for item in fixture.conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id")] == raw_before
        assert _seed_already_covered(forecast_db=fixture.db, forecast_conn=fixture.conn, seed=seed) is True
        assert tuple(fixture.conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",
            (original_id,)).fetchone()) == original
    finally:
        if fixture is not None:
            fixture.conn.close()
            fixture.builtin.close()
        next(native, None)


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
@pytest.mark.parametrize("posterior_offset_minutes, expected", [
    (-1, False),
    (0, True),
    (1, True),
])
def test_recompute_seed_requires_posterior_at_or_after_requested_clock(
    _normal_seed_certificate, metric, posterior_offset_minutes, expected, conditioned,
):
    normal = _normal_seed_certificate
    assert normal.request.temperature_metric == metric
    row_before = [tuple(row) for row in normal.conn.execute("SELECT * FROM forecast_posteriors ORDER BY posterior_id")]
    seed = _seed_from_certificate(normal, conditioning=conditioned)
    # Keep the licensed certificate and its clocks immutable. The requested
    # recomputation is before/equal/after that real publication, all after
    # native/FAST first possession, rather than relabelling June rows H/L.
    seed["computed_at"] = (normal.cut - timedelta(minutes=posterior_offset_minutes)).isoformat()
    assert _seed_already_covered(forecast_db=normal.db, forecast_conn=normal.conn, seed=seed) is True
    seed["upgrade_trigger"] = "held_belief_computed_age_expired"
    assert _seed_already_covered(forecast_db=normal.db, forecast_conn=normal.conn, seed=seed) is expected
    if conditioned:
        seed["day0_observed_extreme_c"] += 1.0
        assert _seed_already_covered(forecast_db=normal.db, forecast_conn=normal.conn, seed=seed) is False
    assert [tuple(row) for row in normal.conn.execute("SELECT * FROM forecast_posteriors ORDER BY posterior_id")] == row_before


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
