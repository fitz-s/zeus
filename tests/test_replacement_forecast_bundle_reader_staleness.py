# Created: 2026-06-07
# Last reused/audited: 2026-09-30
# Authority basis: docs/authority/replacement_final_form_2026_06_09.md
"""H3 antibody — readiness expiry / source-cycle age must be a HARD gate.

Relationship test across the readiness->bundle boundary: a READY posterior whose
``readiness.expires_at < decision_time`` (or whose ``source_cycle_time`` is older
than the operator-configured horizon) must FAIL CLOSED in the bundle reader so
both the live 0.1 path and the legacy hook inherit ONE staleness gate. Trading a
dead/stale forecast as live is the inverse of the zero-trade fault.

The gate lives in ``read_replacement_forecast_bundle`` (the single bundle reader)
so it cannot be bypassed by either consuming path.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone

import pytest

from src.data.replacement_forecast_cycle_policy import (
    CURRENT_EVIDENCE_SEMANTICS_REVISION,
    TRADEABLE_GRADE_QLCB_BASIS,
)
from src.data.replacement_forecast_bundle_reader import (
    HIGH_DATA_VERSION,
    PRODUCT_ID,
    SOURCE_ID,
    read_replacement_forecast_bundle,
)
from src.data.replacement_forecast_readiness import (
    ReplacementForecastDependency,
    build_replacement_forecast_readiness,
)
from src.state.schema.v2_schema import apply_canonical_schema
from tests.test_replacement_forecast_materializer import _hko_native_surfaces, _hko_source_surface
from tests.test_replacement_forecast_bundle_reader import _shanghai_reader_current_certificate


def _hwm_consumed_context(serving):
    """Exact HWM input context only, never a live-grade posterior certificate."""
    return {"bayes_precision_fusion": {"used_models": list(serving), "current_value_serving": serving}}


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("change", ("cohort_loss", "cohort_sigma"))
def test_raw_hwm_equal_value_new_cycle_requires_current_cohort_redecision(tmp_path, monkeypatch, metric, change):
    """Actual typed source/HWM/shape relationship; not a public-q or venue grant."""
    import math
    import socket
    import httpx
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from src.calibration.emos import bin_probability_settlement
    from src.data import bayes_precision_fusion_download as dl
    from src.data.openmeteo_ecmwf_ifs9_anchor import SINGLE_RUNS_FORECAST_URL
    from src.data.replacement_current_value_serving import read_current_instrument_values
    from src.data.replacement_forecast_materializer import _current_evidence_shape_from_values
    from src.data.replacement_input_hwm import _exact_current_value_serving_lag
    from tests.test_bayes_precision_fusion_download import _real_capture_world
    from tests.test_openmeteo_cell_selection_and_elevation_are_product_identity import _selected_test_cell
    from tests.test_openmeteo_ecmwf_ifs9_bucket_transport import _actual_o1280_static_fixture

    def unexpected_network(*_args, **_kwargs):
        raise AssertionError("unexpected external network in private HWM fixture")
    monkeypatch.setattr(socket, "create_connection", unexpected_network)
    world = _real_capture_world(tmp_path, monkeypatch, "single", metric, private_sql_clock=True)
    target = world.targets[0]
    values = {"icon_global": 20., "ukmo_global_deterministic_10km": 20.}
    if change == "cohort_sigma":
        values.update(ukmo_global_deterministic_10km=24., ecmwf_ifs=20.)
    transport, static_path, _data, _write, static_clock, _args = _actual_o1280_static_fixture(tmp_path, monkeypatch)
    static_clock[0] = world.clock[0]
    monkeypatch.setattr(transport, "HSURF_LOCAL_CACHE", str(static_path))
    original_get = world.provider.get

    def source_response(url, *, params=None, timeout=None):
        if url.endswith("/meta.json"):
            return original_get(url, params=params, timeout=timeout)
        assert url == SINGLE_RUNS_FORECAST_URL
        model = params["models"]
        assert model in values
        world.clock[0] += timedelta(milliseconds=3)
        world.calls.append(dict(params))
        lat, lon = float(params["latitude"]), float(params["longitude"])
        if model == "ecmwf_ifs":
            point = transport.select_terrain_optimised_point(lat, lon, 123., local_cache=str(static_path))
            selected_lat, selected_lon = point.grid_latitude, (point.grid_longitude_east + 180) % 360 - 180
        else:
            selected_lat, selected_lon = _selected_test_cell(model, lat, lon)
        day = datetime.fromisoformat(target.target_date)
        payload = {"latitude": selected_lat, "longitude": selected_lon, "elevation": 123.,
            "timezone": target.timezone_name,
            "utc_offset_seconds": int(day.replace(tzinfo=ZoneInfo(target.timezone_name)).utcoffset().total_seconds()),
            "hourly_units": {"temperature_2m": "°C"},
            "hourly": {"time": [(day + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(24)],
                "temperature_2m": [values[model] - 10 if i == 0 else values[model] if i == 12 else values[model] - 5
                    for i in range(24)]}}
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(world.provider, "get", source_response)

    def capture(models, run):
        return dl.download_bayes_precision_fusion_extra_raw_inputs(**{**world.kwargs, "models": tuple(models),
            "frozen_source_runs": {model: (run, run + timedelta(hours=4)) for model in models}}, targets=[target])

    assert capture(set(values) - {"icon_global"}, world.run)["written_row_count"] == len(values) - 1
    with world.open_forecast(world.db) as conn:
        def current():
            return read_current_instrument_values(conn, city=target.city, metric=metric,
                target_date=target.target_date, source_cycle_time_iso=world.run.isoformat(),
                decision_time_iso=world.clock[0].isoformat(), include_station_sources=True)

        consumed = current()
        assert set(consumed) == set(values)
        source_values = {model: value.value_c for model, value in consumed.items()}
        center = sum(source_values.values()) / len(source_values)

        def shape(serving):
            return _current_evidence_shape_from_values(snapshot_id=1,
                source_cycle_time=world.run.isoformat(), source_available_at=(world.run + timedelta(hours=4)).isoformat(),
                members_c=[center + (i - 25)*.01 for i in range(51)], provider_values_c=source_values,
                provider_weights={model: 1/len(source_values) for model in source_values},
                center_c=center, provider_cycles={model: value.served_cycle for model, value in serving.items()})

        def lag(serving, computed_at):
            return _exact_current_value_serving_lag(conn, city=target.city, target_date=target.target_date,
                metric=metric, decision_time=world.clock[0], posterior_computed_at=computed_at,
                provenance=_hwm_consumed_context({model: value.as_provenance() for model, value in serving.items()}))[1]

        old_cut = world.clock[0]
        old_shape = shape(consumed)
        assert old_shape.between_cohort_status == "SIMULTANEOUS_PROVEN"
        assert lag(consumed, old_cut) is None
        world.clock[0] = world.clock[0].replace(hour=23, minute=30)
        newer_cycle = world.run + timedelta(hours=6)
        assert capture(("ukmo_global_deterministic_10km",), newer_cycle)["written_row_count"] == 1
        selected = current()
        assert selected["ukmo_global_deterministic_10km"].served_cycle == newer_cycle.isoformat()
        assert {model: value.value_c for model, value in selected.items()} == source_values
        if change == "cohort_loss":
            with pytest.raises(ValueError, match="two simultaneous provider families"):
                shape(selected)
        else:
            current_shape = shape(selected)
            assert set(current_shape.between_cohort_models) == {"icon_global", "ecmwf_ifs"}
            assert current_shape.predictive_sigma_c < old_shape.predictive_sigma_c
            # Hong Kong's actual Celsius integer truncation resolver. Opposite
            # shoulders show that old wider q is not conservative for both sides.
            boundary = math.ceil(center + 3)
            old_above = bin_probability_settlement(center, old_shape.predictive_sigma_c, boundary, None,
                rounding_rule="oracle_truncate")
            new_above = bin_probability_settlement(center, current_shape.predictive_sigma_c, boundary, None,
                rounding_rule="oracle_truncate")
            old_below = bin_probability_settlement(center, old_shape.predictive_sigma_c, None, boundary - 1,
                rounding_rule="oracle_truncate")
            new_below = bin_probability_settlement(center, current_shape.predictive_sigma_c, None, boundary - 1,
                rounding_rule="oracle_truncate")
            assert old_above > new_above and old_below < new_below
        # The parent's already-correct physical dependency gate fires before
        # the removed numeric alias. This test is baseline GREEN, not the
        # separately retained exact-df legacy RED counter.
        reason = lag(consumed, old_cut)
        assert reason is not None and "physical_proof_dependency_changed" in reason
        if change == "cohort_loss":
            assert capture(("icon_global",), newer_cycle)["written_row_count"] == 1
        selected = current()
        assert shape(selected).between_cohort_status == "SIMULTANEOUS_PROVEN"
        # Component RESET binds actual newly consumed rows/proofs. Full normal
        # materializer -> public posterior is a separate integration obligation.
        assert lag(selected, world.clock[0]) is None
        repeat_calls = len(world.calls)
        assert capture(tuple(values), newer_cycle if change == "cohort_loss" else world.run)["written_row_count"] == 0
        assert len(world.calls) == repeat_calls
        assert lag(selected, world.clock[0]) is None


@pytest.mark.parametrize("metric", ("high", "low"))
def test_raw_hwm_real_same_value_receipt_progress_and_zero_cost_repeat(tmp_path, monkeypatch, metric):
    """Real private body/native/ground/client/writer; no mocked proof or HWM result."""
    import socket
    from pathlib import Path
    from src.data import bayes_precision_fusion_download as dl
    from src.data.replacement_current_value_serving import physical_capture_debt_reason
    from src.data.replacement_input_hwm import _exact_current_value_serving_lag
    from tests.test_bayes_precision_fusion_download import _real_capture_world, _served_in_world

    def unexpected_network(*_args, **_kwargs):
        raise AssertionError("unexpected external network in private HWM fixture")
    monkeypatch.setattr(socket, "create_connection", unexpected_network)
    world = _real_capture_world(tmp_path, monkeypatch, "single", metric, private_sql_clock=True)
    target = world.targets[0]
    with world.open_forecast(world.db) as conn:
        original = _served_in_world(conn, world, target)["icon_global"]
        original_raw = conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall()
        original_count = conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0]
        original_receipt = original.physical_response["capture_receipt_artifact_id"]
        original_provenance = _hwm_consumed_context({"icon_global": original.as_provenance()})
        old_cut = world.clock[0]

        def lag(provenance, computed_at, cut=None):
            return _exact_current_value_serving_lag(conn, city=target.city, target_date=target.target_date,
                metric=metric, decision_time=cut or world.clock[0], posterior_computed_at=computed_at,
                provenance=provenance)[1]

        assert lag(original_provenance, old_cut) is None
        calls = len(world.calls)
        repeated = dl.download_bayes_precision_fusion_extra_raw_inputs(**world.kwargs, targets=[target])
        assert repeated["written_row_count"] == 0 and len(world.calls) == calls
        assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == original_count
        assert lag(original_provenance, old_cut) is None
        receipt_path = conn.execute("SELECT artifact_path FROM raw_forecast_artifacts WHERE artifact_id=?",
            (original_receipt,)).fetchone()[0]
        Path(receipt_path).unlink()  # Only this fixture-owned receipt is removed.
        assert physical_capture_debt_reason(conn, raw_model_forecast_id=original.raw_model_forecast_id,
            decision_time_iso=world.clock[0].isoformat()) == "HTTP_CAPTURE_RECEIPT_MISSING"
        repaired = dl.download_bayes_precision_fusion_extra_raw_inputs(**world.kwargs, targets=[target],
            network_capture_reason="HTTP_CAPTURE_RECEIPT_MISSING",
            capture_debt_raw_ids=(original.raw_model_forecast_id,))
        assert repaired["written_row_count"] == 0
        assert repaired["physical_capture_recovered_raw_ids"] == (original.raw_model_forecast_id,)
        assert len(world.calls) == calls + 1
        assert conn.execute("SELECT * FROM raw_model_forecasts ORDER BY raw_model_forecast_id").fetchall() == original_raw
        current = _served_in_world(conn, world, target)["icon_global"]
        assert current.value_c == original.value_c and current.raw_model_forecast_id == original.raw_model_forecast_id
        assert current.physical_response["capture_receipt_artifact_id"] != original_receipt
        assert "physical_proof_dependency_changed" in lag(original_provenance, old_cut)
        assert lag(original_provenance, old_cut, old_cut) is not None
        rebound = _hwm_consumed_context({"icon_global": current.as_provenance()})
        assert lag(rebound, world.clock[0]) is None
        repaired_count = conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0]
        repeated = dl.download_bayes_precision_fusion_extra_raw_inputs(**world.kwargs, targets=[target])
        assert repeated["written_row_count"] == 0 and len(world.calls) == calls + 1
        assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == repaired_count
        assert lag(rebound, world.clock[0]) is None


def _normal_read_at(normal, monkeypatch, *, cut=None, readiness=None):
    """Advance a private reader clock; never rewrite source/row/readiness clocks."""
    from src.data import replacement_forecast_bundle_reader as reader
    cut = cut or normal.request.computed_at
    class ClockType(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)
    class ReaderClock(datetime, metaclass=ClockType):
        @classmethod
        def now(cls, tz=None):
            return cut.astimezone(tz or UTC)
    monkeypatch.setattr(reader, "datetime", ReaderClock)
    return reader.read_replacement_forecast_bundle(normal.conn,
        **{**normal.kwargs, "decision_time": cut,
           "readiness": readiness or normal.readiness})


UTC = timezone.utc
_TOPO_HASH = "topo-hash-fixed-001"


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


def _provenance(*, source_cycle_time: datetime) -> dict[str, object]:
    return {
        "replacement_q_mode": "FUSED_NORMAL_FULL",
        "q_lcb_basis": TRADEABLE_GRADE_QLCB_BASIS,
        "bin_topology_hash": _TOPO_HASH,
        "bayes_precision_fusion": {
            "current_evidence_shape": {
                "semantics_revision": CURRENT_EVIDENCE_SEMANTICS_REVISION,
                "shape_lag_hours": 0.0,
                "source_cycle_time": source_cycle_time.isoformat(),
                "translation_applied": False,
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


def _insert_posterior(
    conn: sqlite3.Connection,
    *,
    source_cycle_time: datetime,
    source_available_at: datetime,
    computed_at: datetime,
) -> int:
    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at,
            computed_at, q_json, q_lcb_json, posterior_method,
            dependency_source_run_ids_json, provenance_json,
            training_allowed, runtime_layer,
            bin_topology_hash, posterior_identity_hash, dependency_hash,
            posterior_config_hash, q_ucb_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            SOURCE_ID,
            PRODUCT_ID,
            HIGH_DATA_VERSION,
            "Shanghai",
            "2026-06-07",
            "high",
            source_cycle_time.isoformat(),
            source_available_at.isoformat(),
            computed_at.isoformat(),
            json.dumps({"cold": 0.2, "warm": 0.8}),
            json.dumps({"cold": 0.1, "warm": 0.7}),
            "openmeteo_ifs9_aifs_sampled_2t_soft_anchor",
            json.dumps(
                {
                    "baseline_b0": "b0-run",
                    "aifs_sampled_2t": "aifs-run",
                    "openmeteo_ifs9_anchor": "om9-run",
                }
            ),
            json.dumps(_provenance(source_cycle_time=source_cycle_time)),
            0,
            "live",
            _TOPO_HASH,
            "pid-hash",
            "dep-hash",
            "cfg-hash",
            json.dumps({"cold": 0.3, "warm": 0.9}),
        ),
    )
    return int(conn.execute("SELECT posterior_id FROM forecast_posteriors").fetchone()[0])


def _readiness(*, posterior_id: int, computed_at: datetime, expires_at: datetime, decision_time: datetime):
    dependencies = (
        ReplacementForecastDependency(
            role="baseline_b0",
            source_id="ecmwf_open_data",
            product_id="ecmwf_opendata_ifs_ens_0p25",
            data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
            source_run_id="b0-run",
            source_available_at=_dt(6, 0),
        ),
        ReplacementForecastDependency(
            role="aifs_sampled_2t",
            source_id="ecmwf_aifs_ens",
            product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
            data_version="ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
            source_run_id="aifs-run",
            source_available_at=_dt(6, 0),
            artifact_id=11,
        ),
        ReplacementForecastDependency(
            role="openmeteo_ifs9_anchor",
            source_id="openmeteo_ecmwf_ifs_9km",
            product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
            data_version="openmeteo_ecmwf_ifs9_anchor_localday_high",
            source_run_id="om9-run",
            source_available_at=_dt(6, 0),
            anchor_id=22,
        ),
        ReplacementForecastDependency(
            role="soft_anchor_posterior",
            source_id=SOURCE_ID,
            product_id=PRODUCT_ID,
            data_version=HIGH_DATA_VERSION,
            source_run_id="posterior-run",
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


def test_bundle_reader_blocks_expired_readiness(
    monkeypatch, _shanghai_reader_current_certificate,
) -> None:
    """Expired point-in-time authority cannot license a new capital decision."""
    normal = _shanghai_reader_current_certificate
    assert _normal_read_at(normal, monkeypatch).ok
    # The original normal producer's expiry is untouched. At its actual
    # boundary neither ENTRY nor a stale point-in-time authority can bind.
    result = _normal_read_at(normal, monkeypatch, cut=normal.readiness.expires_at)
    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_LIVE_READINESS_EXPIRED"


def test_bundle_reader_blocks_stale_source_cycle() -> None:
    """A source cycle beyond the live staleness bound fails closed."""
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(4, 0),   # cycle 60h before decision
        source_available_at=_dt(4, 1),
        computed_at=_dt(4, 1, 30),
    )
    readiness = _readiness(
        posterior_id=posterior_id,
        computed_at=_dt(6, 11),
        expires_at=_dt(6, 23),         # not expired by wall clock
        decision_time=_dt(6, 11),
    )
    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=readiness,
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(6, 12),
        current_bin_topology_hash=_TOPO_HASH,
    )
    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_LIVE_CYCLE_AGE_EXCEEDS_BOUND"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("source_id", "wrong-source"),
        ("product_id", "wrong-product"),
        ("data_version", "wrong-data"),
        ("status", "BLOCKED"),
        ("source_available_at", "2026-06-06T12:01:00+00:00"),
        ("posterior_id", "1"),
    ),
)
def test_bundle_reader_rejects_forged_soft_anchor_dependency(
    field: str,
    bad_value: object,
    monkeypatch,
    _shanghai_reader_current_certificate,
) -> None:
    from datetime import timedelta
    normal = _shanghai_reader_current_certificate
    readiness = normal.readiness
    assert _normal_read_at(normal, monkeypatch).ok
    dependency_json = json.loads(json.dumps(readiness.dependency_json))
    soft_anchor = next(
        item
        for item in dependency_json["dependencies"]
        if item["role"] == "soft_anchor_posterior"
    )
    if field == "source_available_at":
        bad_value = (normal.request.computed_at + timedelta(microseconds=1)).isoformat()
    elif field == "posterior_id":
        bad_value = str(normal.row["posterior_id"])
    soft_anchor[field] = bad_value
    forged = replace(readiness, dependency_json=dependency_json)

    result = _normal_read_at(normal, monkeypatch, readiness=forged)
    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READINESS_MISMATCH"


def test_bundle_reader_blocks_red_staleness_entry() -> None:
    """§1e degrade ladder: an aged carrier in the RED band (24h < age < 30h EXPIRED
    wall) isolates NEW ENTRIES — the bundle read (entry authority) fails closed, while
    the held-position monitor/exit lanes read their own paths and stay active."""
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(5, 11),   # 25h before the 06-06 12:00 decision -> RED
        source_available_at=_dt(5, 12),
        computed_at=_dt(5, 12, 30),
    )
    readiness = _readiness(
        posterior_id=posterior_id,
        computed_at=_dt(6, 11),
        expires_at=_dt(6, 23),          # NOT expired by wall clock or the 30h bound
        decision_time=_dt(6, 11),
    )
    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=readiness,
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(6, 12),
        current_bin_topology_hash=_TOPO_HASH,
    )
    assert result.ok is False
    assert result.reason_code == "REPLACEMENT_STALENESS_RED_ENTRY_ISOLATED"


def test_bundle_reader_amber_still_binds() -> None:
    """AMBER (18h < age <= 24h) keeps trading — the bundle binds; the fitted sigma
    inflation is applied at the admission sigma seam, never by withholding the belief."""
    conn = _conn()
    posterior_id = _insert_posterior(
        conn,
        source_cycle_time=_dt(5, 16),   # 20h before the 06-06 12:00 decision -> AMBER
        source_available_at=_dt(5, 17),
        computed_at=_dt(5, 17, 30),
    )
    readiness = _readiness(
        posterior_id=posterior_id,
        computed_at=_dt(6, 11),
        expires_at=_dt(6, 23),
        decision_time=_dt(6, 11),
    )
    result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=_BaselineBundle(_Evidence("b0-run")),
        readiness=readiness,
        city="Shanghai",
        target_date=date(2026, 6, 7),
        temperature_metric="high",
        decision_time=_dt(6, 12),
        current_bin_topology_hash=_TOPO_HASH,
    )
    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"
    assert result.bundle is not None


def test_bundle_reader_accepts_fresh_readiness(
    monkeypatch, _shanghai_reader_current_certificate,
) -> None:
    """Fresh forecast (not expired, recent SYNOPTIC cycle) still binds — gate is not over-broad.

    Uses a 12Z (synoptic) cycle so this asserts the STALENESS gate is not over-broad without
    tripping the separate intermediate-cycle (06/18Z) shadow-only gate. The intermediate-phase
    admission behaviour is covered by test_replacement_forecast_cycle_phase_admission.py.
    """
    normal = _shanghai_reader_current_certificate
    result = _normal_read_at(normal, monkeypatch)
    assert result.ok is True
    assert result.reason_code == "REPLACEMENT_POSTERIOR_READY"
    assert result.bundle is not None
    assert result.bundle.posterior_id == normal.row["posterior_id"]
