# Created: 2026-09-20
# Last reused/audited: 2026-09-21
# Purpose: Defend causal extraction and native-unit probability comparisons.
# Reuse: Run before changing source-basket validation; no live database needed.
from datetime import date, datetime, timezone
import json
import hashlib
import math
import sqlite3
from types import SimpleNamespace

import pytest

from scripts import validate_source_clock_probabilities as validation
from src.config import City
from src.events.family_book_manifest import (
    ObservationEnvelope,
    _BinProjection,
    build_source_manifest,
    compute_state_identity,
)
from src.state.schema.family_book_observations_schema import (
    ensure_table as ensure_observations_table,
)


def test_declared_universe_preserves_never_observed_combinations(monkeypatch):
    monkeypatch.setattr(validation, "OPENMETEO_MODEL_IDS", {
        "icon_global": "icon_global", "jma_msm": "jma_msm",
    })
    declared = validation.declared_baskets()
    assert "ecmwf_ifs+icon_global" in declared
    assert "icon_global+jma_msm" in declared
    assert "ecmwf_ifs+icon_global+jma_msm" in declared
    # No database, sampled case, or outcome enters the predeclaration.
    clock = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
    vector = validation.ProbabilityVector(("cold", "warm"), (0.7, 0.3), clock)
    case = validation.ProbabilityValidationCase(
        city="Chicago", metric="high", target_date=date(2026, 9, 19),
        decision_at=clock, label_known_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        bin_ids=vector.bin_ids, winner_index=0,
        candidates={"ecmwf_ifs+icon_global": vector}, baseline=vector,
    )
    result = validation.validate_probability_candidates([case], predeclared_candidates=declared)
    assert result.best_tested_direction is None
    assert result.selected_vs_candidates["icon_global+jma_msm"].coverage == 0.0


def city():
    return City(
        name="Chicago",
        lat=41.9742,
        lon=-87.9073,
        timezone="America/Chicago",
        settlement_unit="F",
        cluster="CONUS",
        wu_station="KORD",
        settlement_source_type="noaa",
    )


def bins():
    def c(f):
        return (f - 32) / 1.8

    return [
        dict(
            bin_id=str(i),
            lower_c=None if lo is None else c(lo),
            upper_c=None if hi is None else c(hi),
            settlement_unit="F",
            settlement_step_c=1 / 1.8,
            rounding_rule="wmo_half_up",
        )
        for i, (lo, hi) in enumerate(
            [(None, 75), (76, 76), (77, 77), (78, 78), (79, None)]
        )
    ]


def test_native_fahrenheit_partition_has_one_actual_winner():
    ordered, winner = validation.ordered_bins({"bin_topology": bins()}, city(), 78)
    assert winner == 3
    assert [b["bin_id"] for b in ordered] == [str(i) for i in range(5)]
    broken = bins()
    broken[1]["lower_c"] -= 1 / 1.8
    with pytest.raises(ValueError, match="overlapping"):
        validation.ordered_bins({"bin_topology": broken}, city(), 78)


def test_only_declared_sqlite_record_clock_accepts_naive_utc():
    with pytest.raises(ValueError, match="timezone"):
        validation.aware("2026-09-18 12:00:00")
    assert validation.aware("2026-09-18 12:00:00", sqlite_utc=True) == datetime(
        2026, 9, 18, 12, tzinfo=timezone.utc
    )
    assert validation.aware("2026-09-18T07:00:00-05:00") == datetime(
        2026, 9, 18, 12, tzinfo=timezone.utc
    )


def input_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE ensemble_snapshots(snapshot_id,city,target_date,temperature_metric,authority,boundary_ambiguous,members_unit,source_available_at,fetch_time,recorded_at,source_cycle_time,members_json)"
    )
    conn.execute(
        "INSERT INTO ensemble_snapshots VALUES(1,'Chicago','2026-09-19','high','VERIFIED',0,'C','2026-09-18T07:00:00Z','2026-09-18T07:00:01Z','2026-09-18 07:00:02','2026-09-18T06:00:00Z',?)",
        (json.dumps([25.0, 27.0] * 10),),
    )
    from src.data.openmeteo_ecmwf_ifs9_anchor import SINGLE_RUNS_FORECAST_URL

    for i in (1, 2):
        model = "icon_global" if i == 1 else "ukmo_global_deterministic_10km"
        params = json.dumps(dict(
            latitude=41.9742, longitude=-87.9073, timezone="America/Chicago",
            models=model, temperature_unit="celsius", cell_selection="land",
            hourly="temperature_2m",
        ), sort_keys=True, separators=(",", ":"))
        raw = dict(
            raw_model_forecast_id=i, model=model, city="Chicago", metric="high",
            target_date="2026-09-19", source_cycle_time="2026-09-18T06:00:00Z",
            forecast_value_c=25.0 if i == 1 else 27.0,
            source_available_at="2026-09-18T07:00:00Z",
            captured_at="2026-09-18T07:00:01Z", recorded_at="2026-09-18 07:00:02",
            endpoint="single_runs", endpoint_mode="single_runs", coverage_status="COVERED",
            source_id=model + "_single_runs", source_family="openmeteo_single_runs",
            provider="open-meteo", product_id=model + "::single_runs",
            request_url_hash=hashlib.sha256(f"{SINGLE_RUNS_FORECAST_URL}?{params}".encode()).hexdigest(),
            raw_sha256=None, latitude_requested=41.9742, longitude_requested=-87.9073,
            timezone_requested="America/Chicago", model_name=model, request_params_json=params,
        )
        if i == 1:
            conn.execute("CREATE TABLE raw_model_forecasts(" + ",".join(raw) + ")")
        conn.execute(
            "INSERT INTO raw_model_forecasts VALUES(" + ",".join("?" for _ in raw) + ")",
            tuple(raw.values()),
        )
    return conn


def served():
    return {
        model: SimpleNamespace(
            value_c=value, raw_model_forecast_id=i, served_cycle="2026-09-18T06:00:00Z"
        )
        for model, value, i in [
            ("icon_global", 25.0, 1),
            ("ukmo_global_deterministic_10km", 27.0, 2),
        ]
    }


def candidate_run(conn, monkeypatch, history=()):
    monkeypatch.setattr(
        validation, "read_current_instrument_values", lambda *_args, **_kwargs: served()
    )
    return validation.make_candidates(
        conn,
        dict(
            city="Chicago",
            target_date="2026-09-19",
            temperature_metric="high",
            source_cycle_time="2026-09-18T06:00:00Z",
        ),
        {"bayes_precision_fusion": {"current_evidence_shape": {"snapshot_id": 1}}},
        city(),
        datetime(2026, 9, 18, 12, tzinfo=timezone.utc),
        history,
        bins(),
    )


def test_candidate_uses_real_shape_and_settlement_integration(monkeypatch):
    candidates, values, _ = candidate_run(input_db(), monkeypatch)
    q = next(iter(candidates.values())).values
    # Two equally weighted providers and ENS: within²=1, between²=1,
    # ENS-center disagreement=0. Native F sigma is sqrt(2)*1.8.
    sigma = math.sqrt(2) * 1.8
    center = 26 * 1.8 + 32
    expected_below_75 = 0.5 * (1 + math.erf((75.5 - center) / (sigma * math.sqrt(2))))
    assert q[0] == pytest.approx(expected_below_75, abs=1e-12)
    assert sum(q) == pytest.approx(1)
    assert values == {"icon_global": 25, "ukmo_global_deterministic_10km": 27}


@pytest.mark.parametrize(
    "column,value",
    [
        ("captured_at", "2026-09-18T13:00:00Z"),
        ("recorded_at", "2026-09-18 13:00:00"),
        ("latitude_requested", 0),
        ("coverage_status", "PARTIAL"),
        ("request_url_hash", None),
        ("request_url_hash", "wrong-nonempty-hash"),
        ("forecast_value_c", 24),
        ("source_cycle_time", "2026-09-18T00:00:00Z"),
        ("target_date", "2026-09-20"),
    ],
)
def test_bad_source_proof_cannot_form_a_candidate(monkeypatch, column, value):
    conn = input_db()
    assert column in {
        "captured_at",
        "recorded_at",
        "latitude_requested",
        "coverage_status",
        "forecast_value_c",
        "source_cycle_time",
        "target_date",
        "request_url_hash",
    }
    conn.execute(
        f"UPDATE raw_model_forecasts SET {column}=? WHERE raw_model_forecast_id=2",
        (value,),
    )
    assert not candidate_run(conn, monkeypatch)[0]


def test_future_label_never_changes_candidate_weights(monkeypatch):
    before = candidate_run(input_db(), monkeypatch)[0]
    history = [
        dict(
            target_date="2026-09-17",
            known_at=datetime(2026, 9, 18, 13, tzinfo=timezone.utc),
            values={"icon_global": -100, "ukmo_global_deterministic_10km": 25},
            settlement_c=25,
        )
    ] * 100
    assert candidate_run(input_db(), monkeypatch, history)[0] == before


def test_native_fahrenheit_ensemble_matches_celsius_counterpart(monkeypatch):
    before = next(iter(candidate_run(input_db(), monkeypatch)[0].values())).values
    conn = input_db()
    conn.execute(
        "UPDATE ensemble_snapshots SET members_unit='degF', members_json=?",
        (json.dumps([77.0, 80.6] * 10),),
    )
    after = next(iter(candidate_run(conn, monkeypatch)[0].values())).values
    assert after == pytest.approx(before, abs=1e-12)


def _market_evidence_db(
    *, stale: bool = False, partial: bool = False, model_mismatch: bool = False,
    duplicate_state_bin: bool = False, side_identity_mismatch: bool = False,
    wrong_unit: bool = False, future_selection: bool = False,
):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE family_book_states (
            state_id TEXT PRIMARY KEY, family_id TEXT, topology_hash TEXT,
            complete_book INTEGER, canonical_payload TEXT
        );
        CREATE TABLE family_book_observations (
            observation_id TEXT PRIMARY KEY, family_id TEXT, city TEXT,
            target_date TEXT, temperature_metric TEXT, measurement_unit TEXT,
            decision_time TEXT, complete_book INTEGER, state_id TEXT,
            model_q_identity_hash TEXT, model_q_json TEXT, market_q_json TEXT,
            source_manifest_json TEXT
        );
        """
    )
    baseline_ids = tuple(str(i) for i in range(5))
    baseline_values = (0.10, 0.15, 0.20, 0.25, 0.30)
    token_ids = tuple(reversed([f"token-{i}" for i in range(5)]))
    native_by_baseline = {
        str(index): interval
        for index, interval in enumerate([(None, 75), (76, 76), (77, 77), (78, 78), (79, None)])
    }
    baseline_by_token = {
        token_id: baseline_id for token_id, baseline_id in zip(token_ids, baseline_ids, strict=True)
    }
    captured_at = "2026-09-18T11:56:00+00:00" if stale else "2026-09-18T11:59:00+00:00"
    envelope = ObservationEnvelope(
        family_id="family-chicago", city="Chicago", target_date="2026-09-19",
        temperature_metric="high", decision_id="witness-1", receipt_hash="receipt-1",
        topology_hash="topology-1", complete_book=True, measurement_unit="F",
        our_mu_native=None, our_sigma_native=None, predictive_identity_hash=None,
        model_q_by_bin_id=None, model_q_identity_hash="witness-content-1",
        market_q_by_bin_id=None, market_q_basis=None, market_q_depth_score=None,
        market_q_spread_score=None, market_q_projection_error=None,
        market_q_book_hash=None, pre_veto_selected=False, selected_bin_id=None,
        selected_side=None,
        bins=tuple(
            _BinProjection(
                bin_id=token_id, executable=True,
                lower_native=native_by_baseline[baseline_by_token[token_id]][0],
                upper_native=native_by_baseline[baseline_by_token[token_id]][1],
                condition_id=f"condition-{token_id}", yes_token_id=f"yes-{token_id}",
                no_token_id=f"no-{token_id}", neg_risk=False, min_tick_size="0.01",
                min_order_size="1", fee_rate=0.0, best_yes_ask=0.2, best_yes_bid=0.1,
                executable_snapshot_id=f"yes-snapshot-{token_id}",
                raw_orderbook_hash=f"yes-book-{token_id}",
                source_captured_at=captured_at,
                no_executable_snapshot_id=f"no-snapshot-{token_id}",
                no_raw_orderbook_hash=f"no-book-{token_id}",
                no_source_captured_at=captured_at,
            )
            for token_id in token_ids
        ),
        decision_time=datetime(2026, 9, 18, 11, 59, 30, tzinfo=timezone.utc),
        causal_snapshot_id="causal-1",
    )
    manifest = json.loads(build_source_manifest(envelope))
    model = {
        token_id: baseline_values[baseline_ids.index(baseline_by_token[token_id])]
        for token_id in token_ids
    }
    if model_mismatch:
        model[token_ids[0]] = 0.11
        model[token_ids[1]] = 0.14
    market = {token_id: 0.20 for token_id in token_ids}
    if partial:
        market.pop(token_ids[-1])
    payload = json.loads(compute_state_identity(envelope)[2])
    if side_identity_mismatch:
        payload["bins"][0]["no_raw_orderbook_hash"] = "mismatched-no-book"
    if duplicate_state_bin:
        payload["bins"].append(dict(payload["bins"][0]))
    conn.execute(
        "INSERT INTO family_book_states VALUES (?,?,?,?,?)",
        ("state-1", "family-chicago", "topology-1", 1, json.dumps(payload)),
    )
    conn.execute(
        "INSERT INTO family_book_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "obs-1", "family-chicago", "Chicago", "2026-09-19", "high",
            "C" if wrong_unit else "F",
            "2026-09-18T12:00:01+00:00" if future_selection else "2026-09-18T11:59:30+00:00",
            1, "state-1", "witness-content-1",
            json.dumps(model), json.dumps(market), json.dumps(manifest),
        ),
    )
    baseline = validation.ProbabilityVector(
        bin_ids=baseline_ids,
        values=baseline_values,
        available_at=datetime(2026, 9, 18, 11, 58, tzinfo=timezone.utc),
    )
    return conn, baseline


def test_market_evidence_requires_full_fresh_exact_baseline_mapping():
    conn, baseline = _market_evidence_db()
    vector, reason = validation._market_vector_for_case(
        conn,
        city="Chicago",
        target_date=date(2026, 9, 19),
        metric="high",
        unit="F",
        decision_at=datetime(2026, 9, 18, 12, tzinfo=timezone.utc),
        baseline=baseline,
        bins=bins(),
    )

    assert reason == "market_covered"
    assert vector is not None
    assert vector.bin_ids == baseline.bin_ids
    assert vector.values == (0.20,) * 5
    assert vector.observed_at == datetime(2026, 9, 18, 11, 59, 30, tzinfo=timezone.utc)
    assert vector.quality_proven is True


@pytest.mark.parametrize("kwargs", [
    {"stale": True},
    {"partial": True},
    {"model_mismatch": True},
    {"duplicate_state_bin": True},
    {"side_identity_mismatch": True},
    {"wrong_unit": True},
    {"future_selection": True},
])
def test_market_evidence_rejects_stale_partial_or_nonbaseline_vectors(kwargs):
    conn, baseline = _market_evidence_db(**kwargs)
    vector, reason = validation._market_vector_for_case(
        conn,
        city="Chicago",
        target_date=date(2026, 9, 19),
        metric="high",
        unit="F",
        decision_at=datetime(2026, 9, 18, 12, tzinfo=timezone.utc),
        baseline=baseline,
        bins=bins(),
    )

    assert vector is None
    assert reason == "market_no_exact_complete_fresh_match"


def test_market_evidence_missing_schema_has_zero_coverage():
    baseline = validation.ProbabilityVector(
        bin_ids=tuple(str(i) for i in range(5)),
        values=(0.10, 0.15, 0.20, 0.25, 0.30),
        available_at=datetime(2026, 9, 18, 11, 58, tzinfo=timezone.utc),
    )
    vector, reason = validation._market_vector_for_case(
        sqlite3.connect(":memory:"),
        city="Chicago",
        target_date=date(2026, 9, 19),
        metric="high",
        unit="F",
        decision_at=datetime(2026, 9, 18, 12, tzinfo=timezone.utc),
        baseline=baseline,
        bins=bins(),
    )

    assert vector is None
    assert reason == "market_evidence_schema_unavailable"


def test_market_evidence_schema_upgrade_adds_partial_index_and_keeps_triggers():
    conn = sqlite3.connect(":memory:")
    ensure_observations_table(conn)
    conn.execute("DROP INDEX idx_family_book_observations_market_case")
    ensure_observations_table(conn)

    index_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' "
        "AND name='idx_family_book_observations_market_case'"
    ).fetchone()[0]
    assert "WHERE complete_book = 1" in index_sql
    trigger_names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name IN ('trg_family_book_observations_no_update', "
            "'trg_family_book_observations_no_delete')"
        )
    }
    assert trigger_names == {
        "trg_family_book_observations_no_update",
        "trg_family_book_observations_no_delete",
    }

    plan = conn.execute(
        "EXPLAIN QUERY PLAN "
        "SELECT observation_id FROM family_book_observations "
        "WHERE city=? AND target_date=? AND temperature_metric=? "
        "AND measurement_unit=? AND complete_book=1 "
        "AND julianday(decision_time)>=julianday(?) "
        "AND julianday(decision_time)<=julianday(?) "
        "ORDER BY decision_time DESC, observation_id DESC LIMIT 256",
        ("Chicago", "2026-09-19", "high", "F", "2026-09-18T11:00:00Z", "2026-09-18T12:00:00Z"),
    ).fetchall()
    assert any("idx_family_book_observations_market_case" in row[-1] for row in plan)
    conn.close()


def test_market_evidence_sqlite_interrupt_is_reported_as_timeout(monkeypatch):
    conn, baseline = _market_evidence_db()
    monkeypatch.setattr(
        validation,
        "bounded",
        lambda connection: connection.set_progress_handler(lambda: 1, 1),
    )
    vector, reason = validation._market_vector_for_case(
        conn,
        city="Chicago",
        target_date=date(2026, 9, 19),
        metric="high",
        unit="F",
        decision_at=datetime(2026, 9, 18, 12, tzinfo=timezone.utc),
        baseline=baseline,
        bins=bins(),
    )
    assert vector is None
    assert reason == "market_evidence_query_timeout"


def test_market_evidence_other_sqlite_error_is_not_no_data():
    class _BrokenConnection(sqlite3.Connection):
        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("disk I/O error")

    conn = sqlite3.connect(":memory:", factory=_BrokenConnection)
    baseline = validation.ProbabilityVector(
        bin_ids=tuple(str(i) for i in range(5)),
        values=(0.10, 0.15, 0.20, 0.25, 0.30),
        available_at=datetime(2026, 9, 18, 11, 58, tzinfo=timezone.utc),
    )
    vector, reason = validation._market_vector_for_case(
        conn,
        city="Chicago",
        target_date=date(2026, 9, 19),
        metric="high",
        unit="F",
        decision_at=datetime(2026, 9, 18, 12, tzinfo=timezone.utc),
        baseline=baseline,
        bins=bins(),
    )
    assert vector is None
    assert reason == "market_evidence_query_error"


def test_missing_market_evidence_file_reports_zero_coverage_without_creating_it(
    tmp_path, monkeypatch, capsys,
):
    forecasts = tmp_path / "forecasts.db"
    sqlite3.connect(forecasts).close()
    missing = tmp_path / "missing-family-book.db"
    captured = {}

    def fake_extract(_conn, **kwargs):
        captured.update(kwargs)
        return [], [], {kwargs["market_evidence_unavailable_reason"]: 1}

    monkeypatch.setattr(validation, "extract_cases", fake_extract)
    monkeypatch.setattr(validation, "runtime_cities_by_name", lambda: {})
    validation.main([
        "--forecasts", str(forecasts),
        "--market-evidence", str(missing),
        "--as-of", "2026-09-20T00:00:00+00:00",
        "--start-date", "2026-09-01",
    ])

    output = json.loads(capsys.readouterr().out)
    assert not missing.exists()
    assert captured["market_evidence_conn"] is None
    assert captured["market_evidence_unavailable_reason"] == "market_evidence_file_unavailable"
    assert output["excluded"] == {"market_evidence_file_unavailable": 1}
    assert output["market_comparison"] == "UNAVAILABLE: market_evidence_file_unavailable"
