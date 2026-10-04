# Created: 2026-10-03
# Last reused/audited: 2026-10-03
# Authority basis: docs/operations/current/plans/canonical_execution_lease.md (Part B).
"""Canonical execution lease: identity exclusion, liveness, order, verdict scope."""
from __future__ import annotations

import fcntl
import json
import os
import socket
import sqlite3
import subprocess
import sys
import types
from pathlib import Path

import pytest

import src.data.materialization_execution_lease as lease
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


@pytest.fixture(autouse=True)
def _release_claims():
    before = set(queue._HELD_CLAIM_LEASES)
    yield
    for batch in set(queue._HELD_CLAIM_LEASES) - before:
        queue._release_claim_batch(Path(batch))


def _queued(tmp_path, *names, **overrides):
    requests = tmp_path / "requests"
    requests.mkdir(exist_ok=True)
    paths = []
    for name in names:
        path = requests / name
        path.write_text(json.dumps(_request(**overrides)), encoding="utf-8")
        paths.append(path)
    return requests, tmp_path / queue.MATERIALIZATION_INFLIGHT_DIR_NAME, paths


# --- lease primitive ---------------------------------------------------------


def test_acquire_all_is_all_or_none_and_releases_on_conflict(tmp_path):
    a, b = tmp_path / "leases" / "a.lease", tmp_path / "leases" / "b.lease"
    held_b = lease.acquire_all([b])
    assert held_b is not None
    assert lease.acquire_all([a, b]) is None
    # ``a`` was released when ``b`` conflicted: another owner can take it.
    held_a = lease.acquire_all([a])
    assert held_a is not None
    lease.release(held_a + held_b)
    assert not (tmp_path / "leases").exists()  # swept once free


def test_unreadable_lease_is_unknown_not_free(tmp_path):
    path = tmp_path / "leases" / "x.lease"
    held = lease.acquire_all([path])
    path.chmod(0)
    try:
        if os.access(path, os.R_OK):
            pytest.skip("test uid bypasses mode bits")
        state, taken = lease.observe([path])
        assert state is lease.LeaseState.UNKNOWN and taken == []
    finally:
        path.chmod(0o644)
        lease.release(held)


def test_shared_descriptor_keeps_the_lease_after_the_sender_closes(tmp_path):
    """SCM_RIGHTS: the receiver holds the same lock after the sender's copy closes."""
    path = tmp_path / "leases" / "x.lease"
    held = lease.acquire_all([path])
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    receiver = subprocess.Popen(
        [sys.executable, "-c",
         "import socket,sys;s=socket.socket(fileno=int(sys.argv[1]));"
         "m,f,_,_=socket.recv_fds(s,16,1);print('got',flush=True);sys.stdin.read()",
         str(child.fileno())],
        pass_fds=(child.fileno(),), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    child.close()
    try:
        socket.send_fds(parent, [b"x"], [held[0].fd])
        assert receiver.stdout.readline().strip() == "got"
        os.close(held[0].fd)  # the sender (queue parent) lets go, or dies
        assert lease.observe([path])[0] is lease.LeaseState.HELD
        receiver.stdin.close()
        receiver.wait(timeout=10)
        state, taken = lease.observe([path])
        assert state is lease.LeaseState.ACQUIRED_FOR_RECOVERY
        lease.release(taken)
    finally:
        parent.close()
        if receiver.poll() is None:
            receiver.kill()


# --- claims ------------------------------------------------------------------


def test_every_lane_shares_one_identity_lease(tmp_path):
    requests, inflight, (old, new) = _queued(tmp_path, "London.old.json", "London.new.json")
    first = queue._new_claim_batch(inflight, (old,))
    with pytest.raises(queue._ClaimIdentityOwned):
        queue._new_claim_batch(inflight, (new,))
    assert new.exists()
    batch, claimed, reasons = queue._claim_available_slots(inflight, (new,))
    assert batch is None and claimed == ()
    assert reasons == (queue._CLAIM_IDENTITY_LEASED_REASON,)
    queue._release_claim_batch(first)
    batch, claimed, reasons = queue._claim_available_slots(inflight, (new,))
    assert batch is not None and claimed == (new,) and reasons == ()


def test_claim_metadata_is_typed_and_ordered(tmp_path):
    _requests, inflight, paths = _queued(tmp_path, "b.json", "a.json")
    payload_b = _request(city="B")
    paths[0].write_text(json.dumps(payload_b), encoding="utf-8")
    batch = queue._new_claim_batch(inflight, paths)
    metadata = json.loads((batch / queue._CLAIM_METADATA_NAME).read_text())
    assert metadata["protocol"] == lease.LEASE_PROTOCOL
    assert [r["name"] for r in metadata["records"]] == ["b.json", "a.json"]
    assert {r["owner_generation"] for r in metadata["records"]} == {metadata["owner_generation"]}
    assert [r.slot for r in queue._claim_records(batch)] == [0, 1]
    assert queue._claim_owner_alive(batch) is True


def test_dead_lease_owner_is_recovered_at_once_whatever_its_age(tmp_path):
    requests, inflight, (path,) = _queued(tmp_path, "London.json")
    batch = queue._new_claim_batch(inflight, (path,))
    queue._release_claim_batch(batch)  # the owner is gone; its claimed_at is fresh
    _keys, recovered, unknown = queue._recover_stale_claims(
        request_path=requests, inflight_path=inflight,
    )
    assert recovered == 1 and unknown == ()
    assert path.exists() and not batch.exists()


def test_held_lease_is_never_reclaimed_by_age(tmp_path, monkeypatch):
    requests, inflight, (path,) = _queued(tmp_path, "London.json")
    batch = queue._new_claim_batch(inflight, (path,))
    monkeypatch.setattr(queue, "_claim_age_seconds", lambda _batch: 10_000_000.0)
    _keys, recovered, _unknown = queue._recover_stale_claims(
        request_path=requests, inflight_path=inflight,
    )
    assert recovered == 0 and (batch / path.name).exists()


def test_unreadable_lease_metadata_keeps_a_lease_v1_batch_unknown(tmp_path, monkeypatch):
    requests, inflight, (path,) = _queued(tmp_path, "London.json")
    batch = queue._new_claim_batch(inflight, (path,))
    queue._release_claim_batch(batch)
    (batch / queue._CLAIM_METADATA_NAME).write_text("{", encoding="utf-8")
    monkeypatch.setattr(queue, "_claim_age_seconds", lambda _batch: 10_000_000.0)
    _keys, recovered, _unknown = queue._recover_stale_claims(
        request_path=requests, inflight_path=inflight,
    )
    # Typed by its name, so neither LEGACY age nor a free lease can restore it.
    assert recovered == 0 and (batch / path.name).exists()


def test_legacy_batch_alone_keeps_the_age_bound(tmp_path, monkeypatch):
    requests = tmp_path / "requests"
    requests.mkdir()
    batch = tmp_path / queue.MATERIALIZATION_INFLIGHT_DIR_NAME / "20260101T000000Z.pid1"
    batch.mkdir(parents=True)
    (batch / "London.json").write_text(json.dumps(_request()), encoding="utf-8")
    (batch / queue._CLAIM_METADATA_NAME).write_text(
        json.dumps({"claimed_at": "2000-01-01T00:00:00+00:00"}), encoding="utf-8",
    )
    _keys, recovered, _unknown = queue._recover_stale_claims(
        request_path=requests, inflight_path=batch.parent, lease_only=True,
    )
    assert recovered == 0  # lease-only callers never touch legacy work
    _keys, recovered, _unknown = queue._recover_stale_claims(
        request_path=requests, inflight_path=batch.parent,
    )
    assert recovered == 1


def test_dispatch_follows_claim_records_not_a_rerank(tmp_path, monkeypatch):
    requests, inflight, paths = _queued(tmp_path, "c.json", "a.json", "b.json")
    for path, city in zip(paths, ("C", "A", "B")):
        path.write_text(json.dumps(_request(city=city)), encoding="utf-8")
    batch = queue._new_claim_batch(inflight, paths)
    monkeypatch.setattr(queue, "_cycle_advance_seed_priority_map",
                        lambda *_a, **_kw: pytest.fail("a leased batch is never re-ranked"))
    seen: list[str] = []

    def runner(argv):
        seen.append(Path(argv[argv.index("--input-json") + 1]).name)
        return subprocess.CompletedProcess(list(argv), 0, stdout="", stderr="")

    queue._process_claimed_materialization_batch(
        request_path=batch, processed_path=tmp_path / "processed",
        failed_path=tmp_path / "failed", forecast_db=None, limit=3, runner=runner,
    )
    assert seen == ["c.json", "a.json", "b.json"]


# --- verdict scope -----------------------------------------------------------


def test_forecast_input_identity_excludes_the_envelope_and_names_its_fence(tmp_path):
    db = tmp_path / "forecasts.db"
    sqlite3.connect(db).close()
    witness = {"city": "London", "target_date": "2026-08-25", "metric": "high",
               "target_cycle_time": "2026-08-24T00:00:00+00:00",
               "conditioning_identity": "{}"}
    fingerprints = {
        queue._blocked_attempt_fingerprint(
            input_json=tmp_path / "requests" / "x.json", forecast_db=db, payload=payload,
        )
        for payload in (
            _request(),
            _request(day0_enqueue_owner_witness={**witness, "seed_file": "/q/seeds/a.json"}),
            _request(day0_enqueue_owner_witness={**witness, "seed_file": "/q/seeds/b.json"}),
        )
    }
    assert len(fingerprints) == 1 and None not in fingerprints
    assert queue.MATERIALIZATION_FENCE_VERSION == "f2-forecast-input"


def test_old_fence_marker_never_suppresses(tmp_path):
    """A marker fingerprinted under the envelope-inclusive identity never matches."""
    db = tmp_path / "forecasts.db"
    sqlite3.connect(db).close()
    payload = _request()
    marker_dir = tmp_path / "blocked_attempts"
    marker = queue._blocked_attempt_marker_path(marker_dir, payload)
    marker.parent.mkdir(parents=True)
    current = queue._blocked_attempt_fingerprint(
        input_json=tmp_path / "requests" / "x.json", forecast_db=db, payload=payload,
    )
    old = queue.MATERIALIZATION_FENCE_VERSION
    try:
        queue.MATERIALIZATION_FENCE_VERSION = "f1-envelope-inclusive"
        stale = queue._blocked_attempt_fingerprint(
            input_json=tmp_path / "requests" / "x.json", forecast_db=db, payload=payload,
        )
    finally:
        queue.MATERIALIZATION_FENCE_VERSION = old
    assert stale != current
    marker.write_text(json.dumps({"attempt_fingerprint": stale}), encoding="utf-8")
    assert queue._blocked_attempt_state(
        marker_dir=marker_dir, input_json=tmp_path / "requests" / "x.json",
        payload=payload, forecast_db=db,
    )[2] is False


def test_envelope_verdict_is_terminal_and_writes_no_marker(tmp_path, monkeypatch):
    requests, inflight, (path,) = _queued(tmp_path, "London.json")
    batch = queue._new_claim_batch(inflight, (path,))
    monkeypatch.setattr(queue, "_blocked_attempt_fingerprint", lambda **_kw: "fp")
    error = {"status": "ERROR", "error_type": "RequestInputInvalid",
             "failure_category": "INPUT_VERDICT", "verdict_scope": "envelope",
             "error": "day0_enqueue_owner_witness missing seed_file"}
    report = queue._process_claimed_materialization_batch(
        request_path=batch, processed_path=tmp_path / "processed",
        failed_path=tmp_path / "failed", forecast_db=None, limit=1,
        runner=lambda argv: subprocess.CompletedProcess(list(argv), 2, "", json.dumps(error)),
        marker_dir=tmp_path / "blocked_attempts",
    )
    assert report.failed_count == 1
    assert queue._ENVELOPE_INVALID_REASON in json.loads(
        Path(report.failed_files[0] + ".receipt.json").read_text()
    )["reason_codes"]
    assert not (tmp_path / "blocked_attempts").exists()


def test_producer_drops_a_republish_whose_forecast_inputs_already_blocked(tmp_path, monkeypatch):
    db = tmp_path / "forecasts.db"
    sqlite3.connect(db).close()
    queue_root = tmp_path / "replacement_forecast_live"
    seeds = queue_root / "seeds"
    seeds.mkdir(parents=True)
    request = _request()
    witness = {"city": "London", "target_date": "2026-08-25", "metric": "high",
               "target_cycle_time": "2026-08-24T00:00:00+00:00",
               "seed_file": str(seeds / "first.json"), "conditioning_identity": "{}"}
    published = {**request, "day0_enqueue_owner_witness": witness}
    marker = queue._blocked_attempt_marker_path(queue_root / "blocked_attempts", published)
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"attempt_fingerprint": queue._blocked_attempt_fingerprint(
        input_json=queue_root / "requests" / "first.json", forecast_db=db, payload=published,
    )}), encoding="utf-8")
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
    real_state = queue._blocked_attempt_state
    monkeypatch.setattr(queue, "_blocked_attempt_state",
                        lambda **kw: real_state(**{**kw, "forecast_db": db}))
    _processed, failed, reasons = queue._prepare_seed_requests(
        seed_dir=seeds, seed_processed_dir=queue_root / "seed_processed",
        seed_failed_dir=queue_root / "seed_failed", request_dir=queue_root / "requests",
        forecast_db=tmp_path / "absent.db", limit=1,
    )
    assert failed == []
    assert queue._UNCHANGED_BLOCKED_SEED_SKIP_REASON in reasons
    assert not list((queue_root / "requests").glob("*.json"))


# --- quiescent migration reconcile -------------------------------------------


def test_reconcile_restores_only_ownerless_claims_and_refuses_live_ones(tmp_path):
    requests, inflight, (dead, live) = _queued(tmp_path, "Dead.json", "Live.json")
    live.write_text(json.dumps(_request(city="Live")), encoding="utf-8")
    dead_batch = queue._new_claim_batch(inflight, (dead,))
    queue._release_claim_batch(dead_batch)
    live_batch = queue._new_claim_batch(inflight, (live,))
    legacy = inflight / f"20260101T000000Z.pid{os.getpid()}"  # this process is alive
    legacy.mkdir()
    (legacy / "Legacy.json").write_text(json.dumps(_request(city="Legacy")), encoding="utf-8")

    dry = queue.reconcile_inflight_for_migration(request_path=requests)
    assert (dead_batch / "Dead.json").exists()  # a dry run moves nothing
    report = queue.reconcile_inflight_for_migration(request_path=requests, apply=True)
    assert dry.refused == report.refused
    assert dict(report.refused) == {live_batch.name: "HELD", legacy.name: "LEGACY_OWNER_ALIVE"}
    assert not report.quiescent
    assert (requests / "Dead.json").exists() and not dead_batch.exists()
    assert (live_batch / "Live.json").exists() and (legacy / "Legacy.json").exists()

    queue._release_claim_batch(live_batch)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    legacy.rename(inflight / f"20260101T000000Z.pid{gone.pid}")
    report = queue.reconcile_inflight_for_migration(request_path=requests, apply=True)
    assert report.quiescent
    assert {p.name for p in requests.glob("*.json")} == {"Dead.json", "Live.json", "Legacy.json"}
    assert not list(inflight.iterdir())


# --- mutable request aliases --------------------------------------------------


def test_symlinked_request_is_quarantined_without_touching_its_target(tmp_path):
    requests, inflight, (regular,) = _queued(tmp_path, "London.json")
    target = tmp_path / "publisher-latest.json"
    target.write_text(json.dumps(_request(city="Paris")), encoding="utf-8")
    target_bytes = target.read_bytes()
    alias = requests / "Paris.alias.json"
    alias.symlink_to(target)
    batch, claimed, reasons = queue._claim_available_slots(inflight, (alias, regular))
    assert claimed == (regular,) and queue._REQUEST_ALIAS_QUARANTINED_REASON in reasons
    assert not alias.exists() and not alias.is_symlink()
    assert target.read_bytes() == target_bytes  # never followed or mutated
    # Capture-then-classify: one exclusive capture directory per quarantine,
    # holding the captured link and its exclusively created receipt.
    captures = list((tmp_path / queue._REQUEST_ALIAS_DIR).glob(".capture.Paris.alias.json.*"))
    assert len(captures) == 1
    links = [p for p in captures[0].iterdir() if p.is_symlink()]
    receipts = [p for p in captures[0].iterdir() if p.name == queue._ALIAS_RECEIPT_NAME]
    assert len(links) == 1 and len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["kind"] == "symlink" and receipt["forecast_input_fence"] is False
    assert not (tmp_path / "blocked_attempts").exists()
    with pytest.raises(queue.RequestNotRegular):
        queue._new_claim_batch(inflight, (links[0],))


# --- immutable publication ------------------------------------------------------


def test_republish_is_a_new_inode_so_a_claimed_hardlink_keeps_its_bytes(tmp_path):
    requests, inflight, (a,) = _queued(tmp_path, "a.json")
    b = requests / "b.json"
    os.link(a, b)
    before = a.read_bytes()
    batch = queue._new_claim_batch(inflight, (a,))
    queue._write_request(b, _request(baseline_source_run_id="new-baseline-run"))
    assert (batch / "a.json").read_bytes() == before
    assert (batch / "a.json").stat().st_ino != b.stat().st_ino


def test_executor_refuses_bytes_that_do_not_match_the_claim_record(tmp_path):
    from scripts import materialize_replacement_forecast_live as worker

    requests, inflight, (a,) = _queued(tmp_path, "a.json")
    batch = queue._new_claim_batch(inflight, (a,))
    claimed = batch / "a.json"
    worker._require_claimed_bytes(claimed, claimed.read_bytes())  # matching bytes pass
    with pytest.raises(worker.ClaimedBytesMismatch):
        worker._require_claimed_bytes(claimed, b'{"tampered": true}')
    outside = tmp_path / "dry-run.json"
    outside.write_text("{}")
    worker._require_claimed_bytes(outside, outside.read_bytes())  # no claim, no record


def test_fifo_request_is_rejected_without_waiting_for_a_writer(tmp_path):
    """The classifier opens O_NONBLOCK: a FIFO is rejected at once, no writer needed.

    Same property as the round-4 FIFO probe, with a harness that reads the
    child's whole output (the probe's text-mode readline() can buffer the
    second line before communicate() reads the raw pipe, losing it).
    """
    import sys as _sys

    fifo = tmp_path / "request.json"
    os.mkfifo(fifo)
    code = (
        "import sys;sys.path.insert(0,%r)\n"
        "import src.data.replacement_forecast_live_materialization_queue as q\n"
        "from pathlib import Path\n"
        "try:\n q.read_regular_request(Path(%r));print('READ')\n"
        "except q.RequestNotRegular:print('REJECTED')\n"
    ) % (str(Path.cwd()), str(fifo))
    done = subprocess.run([_sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert done.stdout.strip() == "REJECTED", (done.stdout, done.stderr)


def test_resident_invocation_refuses_a_claim_whose_leases_it_does_not_hold(tmp_path, monkeypatch):
    from scripts import materialize_replacement_forecast_live as worker

    requests, inflight, (a,) = _queued(tmp_path, "a.json")
    batch = queue._new_claim_batch(inflight, (a,))
    claimed = batch / "a.json"
    monkeypatch.setattr(worker, "_INVOCATION_LEASE_FDS", ())
    with pytest.raises(worker.ClaimLeaseNotCovered):  # bytes match, ownership does not
        worker._require_lease_coverage(claimed)
    monkeypatch.setattr(worker, "_INVOCATION_LEASE_FDS", queue._claim_lease_fds([claimed]))
    worker._require_lease_coverage(claimed)  # exactly the transferred union: accepted
