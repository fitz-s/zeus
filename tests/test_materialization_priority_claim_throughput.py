# Created: 2026-10-02
# Last reused/audited: 2026-10-02
# Authority basis: held-family NBM reseed lag study (2026-10-02); priority claim
#   leased one slot per tick, owner-witness churn defeated the unchanged-blocked
#   fence, and inflight recovery waited on claim age instead of owner liveness.
"""Priority materialization claim throughput, attempt identity and owner liveness."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

import src.data.replacement_forecast_live_materialization_queue as queue


def _request(**overrides) -> dict[str, object]:
    return {
        "city": "London",
        "target_date": "2026-08-25",
        "temperature_metric": "high",
        "source_cycle_time": "2026-08-24T00:00:00+00:00",
        "computed_at": "2026-08-24T08:00:00+00:00",
        "baseline_source_run_id": "baseline-run",
        "openmeteo_source_run_id": "openmeteo-run",
        "openmeteo_payload_json": "payload.json",
        "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "30C"}],
        **overrides,
    }


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


# --- 1. every selected priority slot leases in one tick ----------------------


@pytest.fixture
def three_slots(tmp_path, monkeypatch):
    """Held, global and expansion requests, interleaved exactly as live ranks them."""
    requests = tmp_path / "requests"
    requests.mkdir()
    files = {}
    for city in ("London", "Paris", "Hong Kong"):
        path = requests / f"{city.replace(' ', '_')}.2026-08-25.high.json"
        path.write_text(json.dumps(_request(city=city)), encoding="utf-8")
        files[city] = path
    revision = [1]
    monkeypatch.setattr(queue, "_claim_db_fingerprint", lambda _db: revision[0])
    monkeypatch.setattr(queue, "_current_money_risk_families",
                        lambda *_a, **_kw: frozenset({("London", "2026-08-25", "high")}))
    monkeypatch.setattr(queue, "_current_global_auction_scope_families",
                        lambda *_a, **_kw: frozenset({("Paris", "2026-08-25", "high")}))

    def priority(_db, paths, _payloads, **_kwargs):
        # Expansion ranks first, so only the interleave puts held/global ahead.
        rank = {"Hong_Kong": -3, "London": -2, "Paris": -1}
        return ({p.name: (rank[p.name.split(".")[0]], p.name) for p in paths},
                {p.name for p in paths})

    monkeypatch.setattr(queue, "_priority_map_with_names", priority)

    def plan():
        return queue._build_request_claim_read_plan(
            request_path=requests, processed_path=tmp_path / "processed",
            failed_path=tmp_path / "failed", forecast_db=tmp_path / "forecasts.db",
            limit=3, lane=queue.MATERIALIZATION_LANE_PRIORITY,
        )

    return requests, files, revision, plan


def test_all_three_interleaved_slots_lease_in_one_tick(three_slots):
    _requests, files, _revision, plan = three_slots
    prior = plan()
    assert [p.name.split(".")[0] for p in prior.claim.selected_files] == [
        "London", "Paris", "Hong_Kong",  # held, global, expansion
    ]
    claim, reasons = queue._try_claim_priority_request(prior)
    try:
        assert reasons == ()
        assert claim is not None and claim.claimed_count == 3
        assert sorted(p.name for p in claim.selected_files) == sorted(
            p.name for p in files.values()
        )
        assert not any(p.exists() for p in files.values())
        metadata = json.loads((claim.batch_path / queue._CLAIM_METADATA_NAME).read_text())
        assert sorted(metadata["identities"]) == sorted(p.name for p in files.values())
    finally:
        if claim is not None:
            queue._release_claim_batch(claim.batch_path)


def test_three_slot_tick_runs_all_three_children(three_slots, tmp_path):
    requests, files, _revision, _plan = three_slots
    spawned: list[str] = []

    def runner(argv):
        spawned.append(os.path.basename(argv[argv.index("--input-json") + 1]))
        return subprocess.CompletedProcess(list(argv), 0, stdout="", stderr="")

    report = queue.process_replacement_forecast_live_materialization_queue(
        request_dir=requests, processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed", forecast_db=tmp_path / "forecasts.db",
        seed_limit=0, limit=3, lane=queue.MATERIALIZATION_LANE_PRIORITY, runner=runner,
    )
    assert report.status == "PROCESSED"
    assert sorted(spawned) == sorted(p.name for p in files.values())


def test_changed_slot_defers_alone(three_slots):
    _requests, files, revision, plan = three_slots
    prior = plan()
    files["Paris"].write_text(
        json.dumps(_request(city="Paris", computed_at="2026-08-24T09:00:00+00:00")),
        encoding="utf-8",
    )
    revision[0] = 2
    claim, reasons = queue._try_claim_priority_request(prior)
    try:
        assert claim is not None and claim.claimed_count == 2
        assert files["Paris"].exists()
        assert not files["London"].exists() and not files["Hong Kong"].exists()
        assert queue._PRIORITY_CLAIM_SNAPSHOT_CHANGED_REASON in reasons
        assert queue._PRIORITY_CLAIM_RACED_OWNER_REASON not in reasons
    finally:
        if claim is not None:
            queue._release_claim_batch(claim.batch_path)


def test_owned_slot_defers_alone(three_slots):
    requests, files, _revision, plan = three_slots
    prior = plan()
    duplicate = requests / "Paris.duplicate.json"
    duplicate.write_text(files["Paris"].read_text(), encoding="utf-8")
    owner = queue._new_claim_batch(
        requests.parent / queue.MATERIALIZATION_INFLIGHT_DIR_NAME, (duplicate,),
    )
    claim, reasons = queue._try_claim_priority_request(prior)
    try:
        assert claim is not None and claim.claimed_count == 2
        assert files["Paris"].exists()
        assert queue._PRIORITY_CLAIM_RACED_OWNER_REASON in reasons
    finally:
        queue._release_claim_batch(owner)
        if claim is not None:
            queue._release_claim_batch(claim.batch_path)


# --- 3. a snapshot change is not named a raced owner -------------------------


def test_snapshot_change_without_owner_is_named_snapshot_changed(three_slots):
    _requests, files, revision, plan = three_slots
    prior = plan()
    for city, path in files.items():
        path.write_text(json.dumps(_request(city=city, computed_at="2026-08-24T10:00:00+00:00")),
                        encoding="utf-8")
    revision[0] = 2
    claim, reasons = queue._try_claim_priority_request(prior)
    assert claim is None
    assert reasons == (queue._PRIORITY_CLAIM_SNAPSHOT_CHANGED_REASON,)
    assert not any("RACED_OWNER" in r or "DRAIN_OWNER" in r for r in reasons)


# --- 2. an owner witness is not part of the attempt identity -----------------


def test_owner_witness_does_not_change_the_attempt_fingerprint(tmp_path):
    import sqlite3

    db = tmp_path / "forecasts.db"
    sqlite3.connect(db).close()
    seed_side = _request()
    witness = {"city": "London", "target_date": "2026-08-25", "metric": "high",
               "target_cycle_time": "2026-08-24T00:00:00+00:00",
               "conditioning_identity": "{}"}
    request_side_a = {**seed_side, "day0_enqueue_owner_witness": {**witness,
                      "seed_file": "/q/seeds/London.2026-08-25.high.T1.enqueue-a.json"}}
    request_side_b = {**seed_side, "day0_enqueue_owner_witness": {**witness,
                      "seed_file": "/q/seeds/London.2026-08-25.high.T2.enqueue-b.json"}}
    fingerprints = {
        queue._blocked_attempt_fingerprint(
            input_json=tmp_path / "requests" / "x.json", forecast_db=db, payload=payload,
        )
        for payload in (seed_side, request_side_a, request_side_b)
    }
    assert len(fingerprints) == 1 and None not in fingerprints


def test_producer_drops_republished_unchanged_request_before_the_queue(tmp_path, monkeypatch):
    """The seed producer suppresses a request whose request-side marker already holds."""
    import sqlite3
    import types

    db = tmp_path / "forecasts.db"
    sqlite3.connect(db).close()
    queue_root = tmp_path / "replacement_forecast_live"
    seeds = queue_root / "seeds"
    seeds.mkdir(parents=True)
    request = _request()
    witness = {"city": "London", "target_date": "2026-08-25", "metric": "high",
               "target_cycle_time": "2026-08-24T00:00:00+00:00",
               "seed_file": str(seeds / "first.json"), "conditioning_identity": "{}"}
    # The request side bound its BLOCKED to the owner-published request.
    published = {**request, "day0_enqueue_owner_witness": witness}
    marker = queue._blocked_attempt_marker_path(queue_root / "blocked_attempts", published)
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"attempt_fingerprint": queue._blocked_attempt_fingerprint(
        input_json=queue_root / "requests" / "first.json", forecast_db=db, payload=published,
    )}), encoding="utf-8")
    # A republish: same inputs, new seed file, new decision clock.
    seed_path = seeds / "London.2026-08-25.high.T2.enqueue-b.json"
    seed_path.write_text(json.dumps({**request, "computed_at": "2026-08-24T08:05:00+00:00"}),
                         encoding="utf-8")
    monkeypatch.setattr(queue, "_seed_already_covered", lambda **_kw: False)
    monkeypatch.setattr(queue, "_seed_source_cycle_boundary", lambda **_kw: None)
    import src.data.station_ground_evidence as ground

    monkeypatch.setattr(ground, "archive_station_ground_evidence", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        queue, "build_replacement_forecast_materialization_request",
        lambda seed, *, base_dir: types.SimpleNamespace(
            ok=True, status="READY", reason_codes=("OK",), request=dict(seed)),
    )
    # The producer runs without a forecast connection here (no schema to read);
    # its unchanged check still evaluates the real fingerprint against ``db``.
    real_state = queue._blocked_attempt_state
    monkeypatch.setattr(
        queue, "_blocked_attempt_state",
        lambda **kw: real_state(**{**kw, "forecast_db": db}),
    )
    processed, failed, reasons = queue._prepare_seed_requests(
        seed_dir=seeds, seed_processed_dir=queue_root / "seed_processed",
        seed_failed_dir=queue_root / "seed_failed", request_dir=queue_root / "requests",
        forecast_db=tmp_path / "absent.db", limit=1,
    )
    assert failed == []
    assert queue._UNCHANGED_BLOCKED_SEED_SKIP_REASON in reasons
    assert not list((queue_root / "requests").glob("*.json"))


# --- 4. dead-owner recovery by kernel lock / pid, not by age -----------------


def _inflight_batch(tmp_path, name, *, claimed_at):
    batch = tmp_path / queue.MATERIALIZATION_INFLIGHT_DIR_NAME / name
    batch.mkdir(parents=True)
    (batch / "London.2026-08-25.high.json").write_text(json.dumps(_request()), encoding="utf-8")
    (batch / queue._CLAIM_METADATA_NAME).write_text(
        json.dumps({"claimed_at": claimed_at}), encoding="utf-8",
    )
    return batch


def _recover(tmp_path):
    (tmp_path / "requests").mkdir(exist_ok=True)
    return queue._recover_stale_claims(
        request_path=tmp_path / "requests",
        inflight_path=tmp_path / queue.MATERIALIZATION_INFLIGHT_DIR_NAME,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_dead_pid_claim_is_recovered_immediately(tmp_path):
    batch = _inflight_batch(
        tmp_path, f"priority.20261002T000000000000Z.pid{_dead_pid()}", claimed_at=_now(),
    )
    _keys, recovered, _unknown = _recover(tmp_path)
    assert recovered == 1
    assert (tmp_path / "requests" / "London.2026-08-25.high.json").exists()
    assert not batch.exists()


def test_unheld_owner_lock_is_recovered_immediately_even_with_a_live_pid(tmp_path):
    batch = _inflight_batch(
        tmp_path, f"priority.20261002T000000000000Z.pid{os.getpid()}", claimed_at=_now(),
    )
    (batch / queue._CLAIM_OWNER_LOCK_NAME).write_text("", encoding="utf-8")
    _keys, recovered, _unknown = _recover(tmp_path)
    assert recovered == 1
    assert not batch.exists()


def test_live_owner_is_never_stolen_whatever_its_age(tmp_path):
    batch = _inflight_batch(
        tmp_path, f"priority.20000101T000000000000Z.pid{os.getpid()}",
        claimed_at="2000-01-01T00:00:00+00:00",
    )
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl,os,sys,time;fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR);"
         "fcntl.flock(fd,fcntl.LOCK_EX);print('held',flush=True);time.sleep(60)",
         str(batch / queue._CLAIM_OWNER_LOCK_NAME)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        _keys, recovered, _unknown = _recover(tmp_path)
        assert recovered == 0
        assert (batch / "London.2026-08-25.high.json").exists()
    finally:
        holder.kill()
        holder.wait()
    _keys, recovered, _unknown = _recover(tmp_path)
    assert recovered == 1  # the kernel released the dead owner's lock


def test_lockless_batch_without_a_pid_falls_back_to_the_age_bound(tmp_path):
    fresh = _inflight_batch(tmp_path, "legacy-fresh", claimed_at=_now())
    old = _inflight_batch(tmp_path, "legacy-old", claimed_at="2000-01-01T00:00:00+00:00")
    _keys, recovered, _unknown = _recover(tmp_path)
    assert recovered == 1
    assert fresh.exists() and not old.exists()


def test_new_claims_hold_their_owner_lock_until_released(tmp_path):
    requests = tmp_path / "requests"
    requests.mkdir()
    path = requests / "London.2026-08-25.high.json"
    path.write_text(json.dumps(_request()), encoding="utf-8")
    batch = queue._new_claim_batch(tmp_path / queue.MATERIALIZATION_INFLIGHT_DIR_NAME, (path,))
    assert queue._claim_owner_alive(batch) is True
    _keys, recovered, _unknown = _recover(tmp_path)
    assert recovered == 0
    queue._release_claim_batch(batch)
    assert queue._claim_owner_alive(batch) is False
    _keys, recovered, _unknown = _recover(tmp_path)
    assert recovered == 1


def test_priority_tick_restores_a_dead_owners_batch_without_waiting(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "_current_money_risk_families", lambda *_a, **_kw: frozenset())
    monkeypatch.setattr(queue, "_current_global_auction_scope_families",
                        lambda *_a, **_kw: frozenset())
    monkeypatch.setattr(queue, "_priority_map_with_names", lambda _db, paths, _p, **_kw: (
        {p.name: (-2, p.name) for p in paths}, {p.name for p in paths}))
    requests = tmp_path / "requests"
    requests.mkdir()
    held = requests / "London.2026-08-25.high.json"
    held.write_text(json.dumps(_request()), encoding="utf-8")
    dead = _inflight_batch(
        tmp_path, f"priority.20261002T000000000000Z.pid{_dead_pid()}", claimed_at=_now(),
    )
    (dead / "London.2026-08-25.high.json").rename(dead / "London.stale.json")
    report = queue.process_replacement_forecast_live_materialization_queue(
        request_dir=requests, processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed", forecast_db=tmp_path / "forecasts.db",
        seed_limit=0, limit=1, lane=queue.MATERIALIZATION_LANE_PRIORITY,
        runner=lambda _argv: pytest.fail("the recovery tick must not spawn"),
    )
    assert report.status == "DEFERRED"
    assert "REPLACEMENT_LIVE_MATERIALIZATION_STALE_CLAIM_RECOVERED" in report.reason_codes
    assert not dead.exists()
