# Created: 2026-10-04
# Last reused or audited: 2026-10-04
# Lifecycle: created=2026-10-04; last_reviewed=2026-10-04; last_reused=2026-10-04
# Purpose: Typed BLOCKED evidence for the missing Day0 51-member ENS bundle and
#   for an OM9 response whose own bytes refuse local-day extraction: fenced on
#   exactly the inputs the decision read, re-decided when any of them changes.
# Reuse: Run for materialization_block_evidence, the live queue fence, or the
#   materializer's Day0 / OM9 BLOCKED paths.
# Authority basis: live 2026-10-04 — DAY0_CONDITIONAL_HIGH_ENSEMBLE_UNAVAILABLE
#   (Singapore/Busan/Wuhan/Hong Kong ...) and Atlanta 2026-10-03 OM9_SOURCE_RESPONSE_INVALID
#   BLOCKED verdicts were UNCLASSIFIED + VERDICT_NOT_BOUND_TO_INPUTS and retried every tick.
"""A typed Day0-ENS / OM9 BLOCKED fences its inputs; anything it read reopens it."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.data import materialization_block_evidence as evidence
from src.data import replacement_forecast_live_materialization_queue as queue
from src.data import replacement_forecast_materializer as materializer
from src.data.day0_hourly_vectors import _ensure_schema
from src.data.replacement_forecast_materializer import ReplacementForecastMaterializeRequest
from src.state.schema.v2_schema import apply_canonical_schema

UTC = timezone.utc
ENS = "DAY0_CONDITIONAL_HIGH_ENSEMBLE_UNAVAILABLE"
OM9 = "OM9_SOURCE_RESPONSE_INVALID"
# (city, timezone, metric, Day0 source). Hong Kong LOW is the live anomaly: a
# LOW family blocked by the conditional-HIGH ENS reason, which the current
# materializer reaches only on its open-day HIGH branch.
FAMILIES = {
    "singapore_high": ("Singapore", "Asia/Singapore", "high", "wu_api+same_station_fast_tail"),
    "singapore_low": ("Singapore", "Asia/Singapore", "low", "wu_api+same_station_fast_tail"),
    "hong_kong_low": ("Hong Kong", "Asia/Hong_Kong", "low", "hko_hourly_accumulator"),
}
TARGET = "2026-10-05"
CUT = datetime(2026, 10, 4, 18, 30, tzinfo=UTC)  # 02:30 local on the target day (UTC+8)


def _forecast_db(tmp_path: Path) -> Path:
    from src.state.schema.observation_prints_schema import ensure_table

    db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db)
    apply_canonical_schema(conn, forecast_tables=True)
    _ensure_schema(conn)
    ensure_table(conn)
    conn.commit()
    conn.close()
    return db


def _print(db: Path, observed: datetime, value: float = 27.0) -> None:
    """One same-station settlement-channel print: the current state the ENS read is windowed by."""
    from src.state.schema.observation_prints_schema import append_print

    conn = sqlite3.connect(db)
    assert append_print(conn, city="Singapore", station_id="WSSS", source_channel="noaa_wrh_wsss",
                        publish_ts_utc=observed.isoformat(), value_native=value, unit="C",
                        fetched_at_utc=observed.isoformat(), raw_report=f"WSSS {value}")
    conn.commit()
    conn.close()


def _payload(family: str, **extra) -> dict:
    city, tz, metric, source = FAMILIES[family]
    return {
        "city": city, "city_id": city, "city_timezone": tz, "target_date": TARGET,
        "temperature_metric": metric,
        "baseline_source_run_id": f"b0-{metric}", "baseline_data_version": "fixture",
        "baseline_source_available_at": (CUT - timedelta(hours=6)).isoformat(),
        "openmeteo_source_run_id": f"om-{metric}",
        "openmeteo_source_available_at": (CUT - timedelta(hours=5)).isoformat(),
        "source_cycle_time": "2026-10-04T06:00:00+00:00",
        "openmeteo_source_cycle_time": "2026-10-04T06:00:00+00:00",
        "computed_at": CUT.isoformat(),
        "day0_observed_extreme_c": 26.2,
        "day0_observed_extreme_source": source,
        "day0_observed_extreme_observation_time": (CUT - timedelta(minutes=10)).isoformat(),
        "day0_observed_extreme_unit": "C",
        "bins": [{"bin_id": "26C"}],
        **extra,
    }


def _request(payload: dict, *, raw: bytes | None = None, anchor=None) -> ReplacementForecastMaterializeRequest:
    return ReplacementForecastMaterializeRequest(
        city=payload["city"], city_id=payload["city_id"], city_timezone=payload["city_timezone"],
        target_date=date.fromisoformat(payload["target_date"]),
        temperature_metric=payload["temperature_metric"],
        baseline_source_run_id=payload["baseline_source_run_id"],
        baseline_data_version=payload["baseline_data_version"],
        baseline_source_available_at=datetime.fromisoformat(payload["baseline_source_available_at"]),
        openmeteo_anchor=anchor, openmeteo_source_run_id=payload["openmeteo_source_run_id"],
        openmeteo_source_available_at=datetime.fromisoformat(payload["openmeteo_source_available_at"]),
        bins=(), source_cycle_time=datetime.fromisoformat(payload["source_cycle_time"]),
        computed_at=datetime.fromisoformat(payload["computed_at"]),
        openmeteo_raw_payload_bytes=raw,
        day0_observed_extreme_c=payload.get("day0_observed_extreme_c"),
        day0_observed_extreme_source=payload.get("day0_observed_extreme_source"),
        day0_observed_extreme_observation_time=payload.get("day0_observed_extreme_observation_time"),
        day0_observed_extreme_unit=payload.get("day0_observed_extreme_unit"),
    )


def _consumed_witness(argv) -> dict:
    """What a real worker reports having read: its request and any named input."""
    from scripts.materialize_replacement_forecast_live import _ConsumedInputs, _StageReceipt

    request = Path(argv[argv.index("--input-json") + 1])
    consumed = _ConsumedInputs(_StageReceipt(request, None).attempt_id)
    consumed.read(request, role="request")
    payload = json.loads(request.read_text())
    if payload.get("openmeteo_payload_json"):
        consumed.read(Path(payload["openmeteo_payload_json"]), role="openmeteo_payload")
    return consumed.witness()


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def _add_member(db: Path, family: str, index: int, captured: datetime) -> None:
    city, tz, *_ = FAMILIES[family]
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO day0_hourly_vectors (vector_id, model, city, target_date, timezone_name,"
        " captured_at, endpoint, request_hash, times_json, temps_c_json, source_run_meta_json)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (f"v{index}-{captured.isoformat()}", f"ecmwf_ifs025_member{index:02d}", city, TARGET, tz,
         captured.isoformat(), "single_runs", "sha256:x", json.dumps([f"{TARGET}T03:00"]),
         json.dumps([26.0]), json.dumps({"provider_source_cycle_time_utc": "2026-10-04T00:00:00+00:00"})),
    )
    conn.commit()
    conn.close()


def _logic(tmp_path: Path, monkeypatch) -> list[Path]:
    files = []
    for name in ("day0_hourly_vectors.py", "materialization_block_evidence.py"):
        path = tmp_path / "logic" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("v1")
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))
        files.append(path)
    monkeypatch.setattr(queue, "_logic_revision_paths", lambda: tuple(files))
    return files


class _Worker:
    """The real materializer prepare/validate on a temp forecasts DB; only the
    posterior math (or the OM9 guard's earlier ground read) is replaced by the
    outcome under test. Evidence is computed by the shipped code."""

    def __init__(self, db: Path, reason: str, *, witness: bool = True) -> None:
        self.db, self.reason, self.witness, self.responses = db, reason, witness, []

    def __call__(self, argv):
        path = Path(argv[argv.index("--input-json") + 1])
        payload = json.loads(path.read_text())
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row  # as every production forecasts connection
        try:
            if self.reason == ENS:
                result = materializer.prepare_replacement_forecast_live(conn, _request(payload))
            else:
                raw = Path(payload["openmeteo_payload_json"]).read_bytes()
                validated = materializer._validated_replacement_forecast_request(
                    conn, _request(payload, raw=raw, anchor=_Anchor()))
                result = validated
        finally:
            conn.close()
        body = {"status": result.status, "reason_codes": list(result.reason_codes)}
        if result.evidence is not None:
            body["blocked_evidence"] = dict(result.evidence)
        if self.witness:
            body["consumed_inputs"] = _consumed_witness(argv)
        self.responses.append(body)
        return subprocess.CompletedProcess(list(argv), 1, json.dumps(body) + "\n", "")


class _Anchor:
    """The request's anchor carries the payload's own OM9 cycle, as the builder does."""
    source_cycle_time = datetime(2026, 10, 4, 6, tzinfo=UTC)


@pytest.fixture
def ens_world(tmp_path, monkeypatch):
    def validated(conn, request):
        # The shipped normalization only: clock lift then Day0 frontier.
        request = materializer._request_with_materialization_clock(conn, request)
        normalized = materializer._request_with_day0_physical_frontier(
            conn, request, metric=request.temperature_metric)
        return normalized, request.temperature_metric

    def compute(*_args, **_kwargs):
        raise ValueError(ENS)  # the posterior math ends on the missing 51-member bundle

    monkeypatch.setattr(materializer, "_validated_replacement_forecast_request", validated)
    monkeypatch.setattr(materializer, "_compute_posterior_payload", compute)
    monkeypatch.setattr(queue, "_attach_world_read_only", lambda _conn: None)
    monkeypatch.setattr(queue, "_day0_carrier_vector_preflight_reason", lambda **_k: None)
    db = _forecast_db(tmp_path)
    _print(db, CUT - timedelta(minutes=20))
    return db


def _drive(tmp_path: Path, db: Path, worker, payload: dict, name: str):
    root = tmp_path / "queue"
    for sub in ("requests", "seeds"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    path = root / "requests" / name
    path.write_text(json.dumps(payload))
    report = queue._process_claimed_materialization_batch(
        request_path=root / "requests", processed_path=root / "processed",
        failed_path=root / "failed", forecast_db=db, limit=1, runner=worker,
        marker_dir=root / "blocked_attempts", seed_dir=root / "seeds",
    )
    return report, path, root


@pytest.mark.parametrize("family", ["singapore_high"])
def test_ens_block_with_full_witness_is_fenced_and_not_retried(tmp_path, monkeypatch, ens_world, family):
    _logic(tmp_path, monkeypatch)
    worker = _Worker(ens_world, ENS)
    payload = _payload(family)
    name = f"{family}.json"
    report, path, root = _drive(tmp_path, ens_world, worker, payload, name)
    proof = worker.responses[0].get("blocked_evidence")
    assert proof is not None and proof["reason"] == ENS, worker.responses
    assert proof["scope"] == {"city": payload["city"], "target_date": TARGET,
                              "temperature_metric": payload["temperature_metric"]}
    item = proof["items"][-1]
    assert item["selected_member_count"] == 0 and item["member_rows"]["count"] == 0
    assert item["routing"]["source"] == payload["day0_observed_extreme_source"]
    assert item["current_state"]["source"] == "noaa_wrh_wsss"
    assert queue._UNBOUND_VERDICT_REASON not in report.reason_codes
    assert queue._UNCHANGED_BLOCKED_SKIP_REASON in report.reason_codes
    assert not path.exists()
    marker = queue._blocked_attempt_marker_path(root / "blocked_attempts", payload)
    assert json.loads(marker.read_text())["blocked_evidence"]["scope"]["temperature_metric"] == payload["temperature_metric"]
    # Next tick, unchanged inputs: no worker.
    second, _path, _root = _drive(tmp_path, ens_world, worker, payload, name)
    assert len(worker.responses) == 1
    assert queue._UNCHANGED_BLOCKED_SKIP_REASON in second.reason_codes
    # The fence is per family/metric: the other metric of the same city/date is not fenced.
    other = _payload("singapore_low")
    assert queue._blocked_attempt_marker_path(root / "blocked_attempts", other) != marker
    assert not queue._blocked_attempt_state(marker_dir=root / "blocked_attempts",
        input_json=path, payload=other, forecast_db=ens_world)[2]


def test_ens_member_arrival_redecides(tmp_path, monkeypatch, ens_world):
    _logic(tmp_path, monkeypatch)
    worker = _Worker(ens_world, ENS)
    payload = _payload("singapore_high")
    _drive(tmp_path, ens_world, worker, payload, "s.json")
    proof = worker.responses[0]["blocked_evidence"]
    conn = _connect(ens_world)
    try:
        assert evidence.evidence_holds(conn, proof, exact_request=payload)
    finally:
        conn.close()
    _add_member(ens_world, "singapore_high", 0, CUT - timedelta(hours=1))
    conn = _connect(ens_world)
    try:
        # The exact verdict no longer holds: a row it judged changed.
        assert not evidence.evidence_holds(conn, proof, exact_request=payload)
    finally:
        conn.close()
    _drive(tmp_path, ens_world, worker, payload, "s.json")
    assert len(worker.responses) == 2, "a new ENS member row must re-decide the family"


def test_new_current_state_redecides(tmp_path, monkeypatch, ens_world):
    _logic(tmp_path, monkeypatch)
    worker = _Worker(ens_world, ENS)
    payload = _payload("singapore_high")
    _drive(tmp_path, ens_world, worker, payload, "s.json")
    proof = worker.responses[0]["blocked_evidence"]
    _print(ens_world, CUT - timedelta(minutes=5), 27.5)  # the read's window start moves
    conn = _connect(ens_world)
    try:
        assert not evidence.evidence_holds(conn, proof, exact_request=payload)
    finally:
        conn.close()


@pytest.mark.parametrize("family", ["hong_kong_low", "singapore_low"])
def test_low_metric_ens_block_is_not_fenced(tmp_path, monkeypatch, ens_world, family):
    """Hong Kong 2026-10-05 LOW: the conditional-HIGH reason on a LOW family. The
    current materializer decides it only for open-day HIGH, so no LOW witness is
    complete: the verdict binds nothing and the request keeps its owner."""
    _logic(tmp_path, monkeypatch)
    worker = _Worker(ens_world, ENS)
    payload = _payload(family)
    report, path, root = _drive(tmp_path, ens_world, worker, payload, f"{family}.json")
    assert "blocked_evidence" not in worker.responses[0]
    assert path.exists() and queue._UNBOUND_VERDICT_REASON in report.reason_codes
    assert not (root / "blocked_attempts").exists() or not list((root / "blocked_attempts").glob("*.json"))


def test_ens_member_after_the_cut_does_not_reopen(tmp_path, monkeypatch, ens_world):
    _logic(tmp_path, monkeypatch)
    worker = _Worker(ens_world, ENS)
    payload = _payload("singapore_high")
    _drive(tmp_path, ens_world, worker, payload, "s.json")
    proof = worker.responses[0]["blocked_evidence"]
    _add_member(ens_world, "singapore_high", 0, CUT + timedelta(hours=1))
    conn = _connect(ens_world)
    try:
        assert evidence.evidence_holds(conn, proof, exact_request=payload)
    finally:
        conn.close()


def test_logic_revision_redecides_the_ens_fence(tmp_path, monkeypatch, ens_world):
    logic = _logic(tmp_path, monkeypatch)
    worker = _Worker(ens_world, ENS)
    payload = _payload("singapore_high")
    _drive(tmp_path, ens_world, worker, payload, "s.json")
    _drive(tmp_path, ens_world, worker, payload, "s.json")
    assert len(worker.responses) == 1
    os.utime(logic[0], ns=(2_000_000_000, 2_000_000_000))  # the materializer fix deploys
    _drive(tmp_path, ens_world, worker, payload, "s.json")
    assert len(worker.responses) == 2


def test_real_logic_set_names_the_day0_decision_modules():
    names = {path.name for path in queue._logic_revision_paths()}
    assert {"day0_hourly_vectors.py", "materialization_block_evidence.py",
            "day0_observation_reader.py", "openmeteo_ecmwf_ifs9_anchor.py",
            "replacement_forecast_materializer.py"} <= names


@pytest.mark.parametrize("fault", ("no_witness", "unreadable_store"))
def test_ens_block_on_incomplete_witness_is_retained(tmp_path, monkeypatch, ens_world, fault):
    _logic(tmp_path, monkeypatch)
    worker = _Worker(ens_world, ENS, witness=fault != "no_witness")
    if fault == "unreadable_store":
        def unreadable(*_a, **_k):
            raise sqlite3.OperationalError("disk I/O error")
        monkeypatch.setattr(evidence, "day0_ensemble_unavailable_item", unreadable)
    payload = _payload("singapore_high")
    report, path, root = _drive(tmp_path, ens_world, worker, payload, "s.json")
    if fault == "unreadable_store":
        assert "blocked_evidence" not in worker.responses[0]
    assert path.exists(), "an unbound verdict keeps its single request owner"
    assert queue._UNBOUND_VERDICT_REASON in report.reason_codes
    assert not (root / "blocked_attempts").exists() or not list((root / "blocked_attempts").glob("*.json"))


@pytest.mark.parametrize("change", ["source", "no_state", "day_ended"])
def test_ens_item_is_none_off_the_decided_branch(tmp_path, ens_world, change):
    payload = _payload("singapore_high")
    if change == "source":
        payload["day0_observed_extreme_source"] = "wu_icao_history"
    elif change == "no_state":
        (tmp_path / "empty").mkdir()
        ens_world = _forecast_db(tmp_path / "empty")
    else:
        payload["computed_at"] = (CUT + timedelta(days=1)).isoformat()
    conn = _connect(ens_world)
    try:
        assert evidence.day0_ensemble_unavailable_item(conn, _request(payload)) is None
    finally:
        conn.close()


def _partial_om9(tmp_path: Path) -> Path:
    # Hourly slots with a gap inside the owned remaining window: extraction refuses.
    hours = [f"{TARGET}T{h:02d}:00" for h in range(24) if h != 15]
    body = {"latitude": 1.35, "longitude": 103.99, "elevation": 10.0, "timezone": "Asia/Singapore",
            "utc_offset_seconds": 28800, "hourly_units": {"temperature_2m": "°C"},
            "hourly": {"time": hours, "temperature_2m": [27.0] * len(hours)}}
    path = tmp_path / "om9.json"
    path.write_text(json.dumps(body))
    return path


@pytest.fixture
def om9_world(tmp_path, monkeypatch):
    monkeypatch.setattr(materializer, "_prewrite_block_reasons", lambda _r: ())
    monkeypatch.setattr(materializer, "_cycle_monotone_block_reasons", lambda *_a, **_k: ())
    # The guard's ground read precedes extraction; the extraction is what refuses.
    monkeypatch.setattr(materializer, "_precision_guard_block_reason", lambda *_a, **_k: (OM9,))
    monkeypatch.setattr(queue, "_day0_carrier_vector_preflight_reason", lambda **_k: None)
    return _forecast_db(tmp_path)


def test_om9_invalid_bytes_fence_and_redecide_on_new_bytes(tmp_path, monkeypatch, om9_world):
    _logic(tmp_path, monkeypatch)
    raw = _partial_om9(tmp_path)
    payload = _payload("singapore_high", openmeteo_payload_json=str(raw))
    worker = _Worker(om9_world, OM9)
    report, path, _root = _drive(tmp_path, om9_world, worker, payload, "a.json")
    proof = worker.responses[0].get("blocked_evidence")
    assert proof is not None and proof["reason"] == OM9, worker.responses
    assert "partial local-day coverage" in proof["items"][0]["refusal"]
    assert queue._UNCHANGED_BLOCKED_SKIP_REASON in report.reason_codes and not path.exists()
    _drive(tmp_path, om9_world, worker, payload, "a.json")
    assert len(worker.responses) == 1
    body = json.loads(raw.read_text())
    body["hourly"]["time"] = [f"{TARGET}T{h:02d}:00" for h in range(24)]
    body["hourly"]["temperature_2m"] = [27.0] * 24
    raw.write_text(json.dumps(body))
    _drive(tmp_path, om9_world, worker, payload, "a.json")
    assert len(worker.responses) == 2, "new OM9 bytes must re-decide"


def test_om9_proof_is_exact_request_only_and_valid_bytes_emit_none(tmp_path):
    payload = _payload("singapore_high")
    bad = _request(payload, raw=_partial_om9(tmp_path).read_bytes(), anchor=_Anchor())
    item = evidence.om9_response_invalid_item(bad)
    proof = evidence.blocked_evidence(None, bad, evidence.OM9_INVALID, [item])
    assert evidence.evidence_holds(None, proof, exact_request=payload)
    assert evidence.evidence_holds(None, proof, payload)  # same cut: same bytes re-fail
    moved = {**payload, "computed_at": (CUT + timedelta(minutes=5)).isoformat()}
    assert not evidence.evidence_holds(None, proof, exact_request=moved)
    assert not evidence.evidence_holds(None, proof, moved)  # a new cut is re-decided
    assert not evidence.evidence_holds(None, proof, {**payload, "temperature_metric": "low"})
    full = json.dumps({"timezone": "Asia/Singapore", "utc_offset_seconds": 28800,
        "hourly": {"time": [f"{TARGET}T{h:02d}:00" for h in range(24)],
                   "temperature_2m": [27.0] * 24}}).encode()
    assert evidence.om9_response_invalid_item(replace(bad, openmeteo_raw_payload_bytes=full)) is None
