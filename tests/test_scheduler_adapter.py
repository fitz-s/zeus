# Lifecycle: created=2026-05-24; last_reviewed=2026-09-25; last_reused=2026-09-25
# Purpose: Current single-live scheduler set and causal executor-class assignment.
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-09-25
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR6);
#   operator spec §7 (Scheduler adapter / executor classes).
"""PR6: registry -> scheduler executor-class assignment (pure planner, daemon wiring deferred)."""
from __future__ import annotations

import pytest


@pytest.fixture
def broad_reseed_join(monkeypatch):
    """Finish the async source-clock worker before monkeypatch restores triggers."""
    def join():
        import src.ingest_main as ingest_main

        worker = getattr(ingest_main, "_BROAD_RESEED_THREAD", None)
        if worker is not None:
            worker.join(timeout=5)
            assert not worker.is_alive()
        assert getattr(ingest_main, "_BROAD_RESEED_ACTIVE", None) is None
        assert getattr(ingest_main, "_BROAD_RESEED_PENDING", None) is None

    yield join
    join()


def test_source_clock_poll_returns_while_broad_trigger_is_blocked(
    monkeypatch, broad_reseed_join,
) -> None:
    """Regression: broad work must not consume the next 15-second probe slot."""
    import threading

    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as probe
    import src.ingest_main as ingest_main

    entered = threading.Event()
    release = threading.Event()
    returned = threading.Event()
    result: list[dict[str, object]] = []

    class _Changed:
        updated_sources = ("icon_global",)

        def as_dict(self):
            return {"status": "SOURCE_CLOCK_UPDATES_CHANGED", "updated_sources": ["icon_global"]}

    monkeypatch.setattr(prod, "_replacement_forecast_live_materialization_queue_config", lambda: {"test": 1})
    monkeypatch.setattr(prod, "_recover_held_common_cycle_anchors_if_needed", lambda *_a, **_k: None)
    monkeypatch.setattr(probe, "probe_openmeteo_source_clock_updates", lambda **_k: _Changed())
    monkeypatch.setattr(prod, "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed", lambda *_a, **_k: {"status": "SOURCE_CLOCK_BPF_SCOPED_NO_TARGETS", "updated_sources": ["icon_global"]})
    monkeypatch.setattr(probe, "source_clock_scoped_download_cursor_sources", lambda *_a, **_k: ("icon_global",))
    monkeypatch.setattr(probe, "advance_source_clock_cursor", lambda *_a, **_k: ("icon_global",))

    def fusion(_cfg, **_kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return {"status": "FUSION_UPGRADE_TRIGGER"}

    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", fusion)
    monkeypatch.setattr(prod, "_enqueue_cycle_advance_reseeds_if_needed", lambda *_a, **_k: {"status": "CYCLE_ADVANCE_TRIGGER"})

    def poll():
        try:
            result.append(ingest_main._replacement_availability_poll_tick.__wrapped__())
        finally:
            returned.set()

    polling = threading.Thread(target=poll)
    polling.start()
    try:
        assert entered.wait(timeout=2)
        assert returned.wait(timeout=1), "broad trigger held the source-clock poll"
        assert result[0]["source_clock_cursor_advanced_sources"] == ()
    finally:
        release.set()
        polling.join(timeout=5)
        broad_reseed_join()
    assert not polling.is_alive()


def test_source_clock_broad_reseed_does_not_hold_next_provider_poll(
    monkeypatch, broad_reseed_join,
) -> None:
    """A later raw commit receives its own scan and cursor proof after the active scan."""
    import threading

    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as probe
    import src.ingest_main as ingest_main

    entered = threading.Event()
    release = threading.Event()
    cycles = iter(("2026-09-23T06:00:00+00:00", "2026-09-23T12:00:00+00:00"))
    advances: list[tuple[str, str]] = []
    trigger_calls: list[tuple[str, object]] = []

    monkeypatch.setattr(prod, "_replacement_forecast_live_materialization_queue_config", lambda: {"test": 1})
    monkeypatch.setattr(prod, "_recover_held_common_cycle_anchors_if_needed", lambda *_a, **_k: None)
    def probe_next(**_kwargs):
        cycle = next(cycles)
        return probe.SourceClockUpdateProbeReport(
            status="SOURCE_CLOCK_UPDATES_CHANGED",
            model_count=1,
            updated_sources=("icon_global",),
            affected_cities=("Munich",),
            model_updates_path="/tmp/test-source-clock-updates",
            cursor_path="/tmp/test-source-clock-cursor",
            cursor_values=(("icon_global", cycle),),
            cursor_preimage=(("icon_global", None),),
            source_runs=(("icon_global", cycle, cycle, 3600),),
        )

    monkeypatch.setattr(probe, "probe_openmeteo_source_clock_updates", probe_next)

    def download(_cfg, *, source_clock_report, **_kwargs):
        return {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "source_results": {
                "icon_global": {
                    "status": "SOURCE_CLOCK_SOURCE_RAW_INPUTS_DOWNLOADED",
                    "cycle": source_clock_report.source_runs[0][1],
                },
            },
        }

    monkeypatch.setattr(prod, "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed", download)
    monkeypatch.setattr(probe, "advance_source_clock_cursor", lambda payload, *, sources: advances.append((sources[0], payload["cursor_values"][sources[0]])) or sources)

    def fusion(_cfg, *, manifest_snapshot=None, **_kwargs):
        trigger_calls.append(("fusion", manifest_snapshot))
        if len(trigger_calls) == 1:
            entered.set()
            assert release.wait(timeout=5)
        return {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 1}

    def cycle(_cfg, *, manifest_snapshot=None, **_kwargs):
        trigger_calls.append(("cycle", manifest_snapshot))
        return {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 1}

    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", fusion)
    monkeypatch.setattr(prod, "_enqueue_cycle_advance_reseeds_if_needed", cycle)
    try:
        first = ingest_main._replacement_availability_poll_tick.__wrapped__()
        assert entered.wait(timeout=2)
        second = ingest_main._replacement_availability_poll_tick.__wrapped__()
        assert first["reseed_maintenance_status"] == "SOURCE_BROAD_RESEEDS_ASYNC_PENDING"
        assert second["reseed_maintenance_status"] == "SOURCE_BROAD_RESEEDS_ASYNC_PENDING"
        assert len(ingest_main._BROAD_RESEED_PENDING["requests"]) == 1
        assert advances == []
    finally:
        release.set()
        broad_reseed_join()

    assert [name for name, _ in trigger_calls] == ["fusion", "cycle", "fusion", "cycle"]
    assert trigger_calls[0][1] is trigger_calls[1][1]
    assert trigger_calls[2][1] is trigger_calls[3][1]
    assert trigger_calls[0][1] is not trigger_calls[2][1]
    assert advances == [
        ("icon_global", "2026-09-23T06:00:00+00:00"),
        ("icon_global", "2026-09-23T12:00:00+00:00"),
    ]


def test_same_cycle_raw_inflight_and_failed_pending_keep_cursor_unadvanced(
    monkeypatch, broad_reseed_join,
) -> None:
    import threading

    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as probe
    import src.ingest_main as ingest_main

    raw_inflight = threading.Event()
    release_raw = threading.Event()
    first_scan_started = threading.Event()
    release_first_scan = threading.Event()
    first_scan_done = threading.Event()
    advances: list[tuple[str, ...]] = []
    eligible_batches: list[tuple[str, ...]] = []
    scans = [0]
    cycle_time = "2026-09-23T06:00:00+00:00"
    poll_thread: threading.Thread | None = None

    def source_report(**_kwargs):
        return probe.SourceClockUpdateProbeReport(
            status="SOURCE_CLOCK_UPDATES_CHANGED", model_count=1,
            updated_sources=("icon_global",), affected_cities=("Munich",),
            model_updates_path="/tmp/updates", cursor_path="/tmp/cursor",
            cursor_values=(("icon_global", cycle_time),),
            cursor_preimage=(("icon_global", None),),
            source_runs=(("icon_global", cycle_time, cycle_time, 3600),),
        )

    def download(_cfg, *, source_clock_report, **_kwargs):
        if scans[0] > 0:
            raw_inflight.set()
            assert release_raw.wait(timeout=5)
        return {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "source_results": {"icon_global": {
                "status": "SOURCE_CLOCK_SOURCE_RAW_INPUTS_DOWNLOADED",
                "cycle": source_clock_report.source_runs[0][1],
            }},
        }

    def fusion(_cfg, **_kwargs):
        scans[0] += 1
        if scans[0] == 1:
            first_scan_started.set()
            assert release_first_scan.wait(timeout=5)
        return {
            "status": (
                "FUSION_UPGRADE_TRIGGER" if scans[0] == 1
                else "FUSION_UPGRADE_TRIGGER_FAILSOFT_SKIPPED"
            ),
        }

    def cycle_trigger(_cfg, **_kwargs):
        first_scan_done.set()
        return {"status": "CYCLE_ADVANCE_TRIGGER"}

    monkeypatch.setattr(prod, "_replacement_forecast_live_materialization_queue_config", lambda: {"test": 1})
    monkeypatch.setattr(prod, "_recover_held_common_cycle_anchors_if_needed", lambda *_a, **_k: None)
    monkeypatch.setattr(probe, "probe_openmeteo_source_clock_updates", source_report)
    real_cursor_sources = probe.source_clock_scoped_download_cursor_sources

    def eligible_sources(report, *, source_clock_report):
        result = real_cursor_sources(report, source_clock_report=source_clock_report)
        eligible_batches.append(result)
        return result

    monkeypatch.setattr(probe, "source_clock_scoped_download_cursor_sources", eligible_sources)
    monkeypatch.setattr(prod, "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed", download)
    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", fusion)
    monkeypatch.setattr(prod, "_enqueue_cycle_advance_reseeds_if_needed", cycle_trigger)
    monkeypatch.setattr(probe, "advance_source_clock_cursor", lambda _payload, *, sources: advances.append(sources) or sources)

    try:
        first = ingest_main._replacement_availability_poll_tick.__wrapped__()
        assert first["source_clock_cursor_advanced_sources"] == ()
        assert first_scan_started.wait(timeout=2)
        assert tuple(ingest_main._BROAD_RESEED_ACTIVE["requests"].values())[0][
            "cursor_sources"
        ] == ("icon_global",)
        poll_thread = threading.Thread(target=ingest_main._replacement_availability_poll_tick.__wrapped__)
        poll_thread.start()
        assert raw_inflight.wait(timeout=2)
        release_first_scan.set()
        assert first_scan_done.wait(timeout=2)
        assert advances == [], "first scan cannot acknowledge while later raw is in flight"
    finally:
        release_first_scan.set()
        release_raw.set()
        if poll_thread is not None:
            poll_thread.join(timeout=5)
        broad_reseed_join()

    assert poll_thread is not None and not poll_thread.is_alive()
    assert scans == [2]
    assert eligible_batches == [("icon_global",), ("icon_global",)]
    assert advances == [], "failed pending same-cycle raw must also block old cursor"


def test_broad_reseed_pending_is_bounded_and_preserves_distinct_sources(
    monkeypatch, broad_reseed_join,
) -> None:
    import threading

    import src.ingest_main as ingest_main

    entered = threading.Event()
    release = threading.Event()
    seen: list[tuple[str, ...]] = []

    def run(batch):
        seen.append(tuple(source for request in batch["requests"].values() for source in request["cursor_sources"]))
        if len(seen) == 1:
            entered.set()
            assert release.wait(timeout=5)
        return True

    monkeypatch.setattr(ingest_main, "_run_broad_reseed_batch", run)
    monkeypatch.setattr("src.data.source_clock_update_probe.advance_source_clock_cursor", lambda _payload, *, sources: sources)

    def enqueue(source, *, cfg=None):
        return ingest_main._enqueue_broad_reseed_batch(
            cfg or {"test": 1},
            include_cycle_advance=source == "cycle",
            source_clock_payload={"cursor_path": "/tmp/test", "updated_sources": [source], "cursor_values": {source: "v1"}, "cursor_preimage": {source: None}},
            cursor_sources=(source,),
            download_report={"status": "downloaded"},
        )

    try:
        assert enqueue("active") == "SOURCE_BROAD_RESEEDS_ASYNC_PENDING"
        assert entered.wait(timeout=2)
        assert enqueue("other", cfg={"test": 2}) == "SOURCE_BROAD_RESEEDS_DEFERRED_CONFIG"
        for i in range(64):
            assert enqueue(f"source{i}") == "SOURCE_BROAD_RESEEDS_ASYNC_PENDING"
        assert enqueue("overflow") == "SOURCE_BROAD_RESEEDS_ASYNC_CAPACITY_DEFERRED"
        assert len(ingest_main._BROAD_RESEED_PENDING["requests"]) == 64
    finally:
        release.set()
        broad_reseed_join()

    assert seen[0] == ("active",)
    assert seen[1] == tuple(f"source{i}" for i in range(64))


@pytest.mark.parametrize("batch_fails", [False, True])
def test_broad_reseed_coalesces_only_non_authorizing_pending_retries(
    monkeypatch, broad_reseed_join, batch_fails,
) -> None:
    import threading

    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    entered = threading.Event()
    release = threading.Event()
    scans: list[object] = []
    advances: list[object] = []

    def fusion(_cfg, *, manifest_snapshot):
        scans.append(manifest_snapshot)
        if len(scans) == 1:
            entered.set()
            assert release.wait(timeout=5)
        return {"status": "FUSION_UPGRADE_TRIGGER_FAILSOFT_SKIPPED"
                if batch_fails and len(scans) > 1 else "FUSION_UPGRADE_TRIGGER"}

    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", fusion)
    monkeypatch.setattr(prod, "_enqueue_cycle_advance_reseeds_if_needed",
                        lambda *_a, **_k: {"status": "CYCLE_ADVANCE_TRIGGER"})
    monkeypatch.setattr("src.data.source_clock_update_probe.advance_source_clock_cursor",
                        lambda _p, *, sources: advances.append(sources) or sources)
    payload = {
        "cursor_path": "/tmp/non-authorizing-retry-cursor",
        "updated_sources": ["icon_global"],
        "cursor_values": {"icon_global": "v1"},
        "cursor_preimage": {"icon_global": None},
        "source_runs": {"icon_global": {"initialisation_time": "v1"}},
    }
    retry = {
        "status": "SOURCE_CLOCK_SOURCE_TRANSPORT_RETRYABLE",
        "written_row_count": 0, "committed_families": (),
        "source_commit_notifications": 0, "source_commit_notifications_pending": 0,
    }

    def enqueue(report, sources=()):
        return ingest_main._enqueue_broad_reseed_batch(
            {"test": 1}, include_cycle_advance=True,
            source_clock_payload=payload, cursor_sources=sources,
            download_report=report,
        )

    try:
        enqueue(retry)
        assert entered.wait(timeout=2)
        for _ in range(27):
            enqueue(retry)
        assert len(ingest_main._BROAD_RESEED_PENDING["requests"]) == 1
        enqueue({**retry, "written_row_count": 1,
                 "committed_families": (("London", "2026-09-25", "high"),)},
                ("icon_global",))
        assert len(ingest_main._BROAD_RESEED_PENDING["requests"]) == 2
        assert advances == []
    finally:
        release.set()
        broad_reseed_join()
    assert len(scans) == 3
    assert scans[0] is not scans[1] and scans[1] is scans[2]
    # The zero-write reports never grant cursor authority themselves. The later
    # eligible receipt re-measured the source, so its published scan proves it;
    # an earlier retry must not veto that proof (the 2026-09-23 livelock).
    assert advances == ([] if batch_fails else [("icon_global",)])


@pytest.mark.parametrize("change", [
    {"written_row_count": 1}, {"written_row_count": None},
    {"source_commit_notifications": 1}, {"source_commit_notifications_pending": 1},
    {"source_commit_notification_errors": ("failed",)},
    {"reseed_errors": ("failed",)}, {"source_clock_anchor_download": {}},
])
def test_broad_reseed_uncertain_or_new_commit_receipts_keep_distinct_work(
    monkeypatch, broad_reseed_join, change,
) -> None:
    import threading
    import src.ingest_main as ingest_main

    entered = threading.Event()
    release = threading.Event()
    seen: list[int] = []

    def run(batch):
        seen.append(len(batch["requests"]))
        if len(seen) == 1:
            entered.set()
            assert release.wait(timeout=5)
        return True

    monkeypatch.setattr(ingest_main, "_run_broad_reseed_batch", run)
    report = {
        "written_row_count": 0, "committed_families": (),
        "source_commit_notifications": 0, "source_commit_notifications_pending": 0,
        **change,
    }

    def enqueue():
        ingest_main._enqueue_broad_reseed_batch(
            {"test": 1}, include_cycle_advance=True,
            source_clock_payload={"cursor_path": "/tmp/uncertain-retry", "updated_sources": []},
            cursor_sources=(), download_report=report,
        )

    try:
        enqueue()
        assert entered.wait(timeout=2)
        enqueue()
        enqueue()
        assert len(ingest_main._BROAD_RESEED_PENDING["requests"]) == 2
    finally:
        release.set()
        broad_reseed_join()
    assert seen == [1, 2]


def test_broad_reseed_failure_defers_cursor_and_next_poll_retries(
    monkeypatch, broad_reseed_join,
) -> None:
    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as probe
    import src.ingest_main as ingest_main

    attempts = [0]
    advances: list[tuple[str, ...]] = []
    snapshots: list[dict[str, object]] = []

    def fusion(_cfg, *, manifest_snapshot):
        attempts[0] += 1
        snapshots.append(manifest_snapshot)
        if attempts[0] == 1:
            return {"status": "FUSION_UPGRADE_TRIGGER_FAILSOFT_SKIPPED"}
        return {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 1}

    def cycle(_cfg, *, manifest_snapshot):
        assert manifest_snapshot is snapshots[-1]
        return {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 1}

    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", fusion)
    monkeypatch.setattr(prod, "_enqueue_cycle_advance_reseeds_if_needed", cycle)
    monkeypatch.setattr(probe, "advance_source_clock_cursor", lambda _payload, *, sources: advances.append(tuple(sources)) or sources)

    def enqueue():
        return ingest_main._enqueue_broad_reseed_batch(
            {"test": 1},
            include_cycle_advance=True,
            source_clock_payload={"cursor_path": "/tmp/test", "updated_sources": ["icon_global"], "cursor_values": {"icon_global": "v1"}, "cursor_preimage": {"icon_global": None}},
            cursor_sources=("icon_global",),
            download_report={"status": "downloaded"},
        )

    assert enqueue() == "SOURCE_BROAD_RESEEDS_ASYNC_PENDING"
    broad_reseed_join()
    assert advances == []
    assert enqueue() == "SOURCE_BROAD_RESEEDS_ASYNC_PENDING"
    broad_reseed_join()
    assert attempts == [2]
    assert advances == [("icon_global",)]


def test_broad_reseed_partial_source_proof_advances_only_eligible_source(
    monkeypatch, broad_reseed_join,
) -> None:
    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as probe
    import src.ingest_main as ingest_main

    advanced: list[str] = []
    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", lambda _cfg, **_kw: {"status": "FUSION_UPGRADE_TRIGGER"})
    monkeypatch.setattr(prod, "_enqueue_cycle_advance_reseeds_if_needed", lambda _cfg, **_kw: {"status": "CYCLE_ADVANCE_TRIGGER"})
    monkeypatch.setattr(probe, "advance_source_clock_cursor", lambda _payload, *, sources: advanced.extend(sources) or sources)
    assert ingest_main._enqueue_broad_reseed_batch(
        {"test": 1}, include_cycle_advance=True,
        source_clock_payload={
            "cursor_path": "/tmp/private-cursor", "updated_sources": ["icon_d2", "ukmo"],
            "cursor_values": {"icon_d2": "v1", "ukmo": "v1"},
            "cursor_preimage": {"icon_d2": None, "ukmo": None},
        },
        cursor_sources=("ukmo",),
        download_report={"status": "partial"},
    ) == "SOURCE_BROAD_RESEEDS_ASYNC_PENDING"
    broad_reseed_join()
    assert advanced == ["ukmo"]


def test_poll_exception_after_raw_commit_invalidates_earlier_cursor_candidate(
    monkeypatch, broad_reseed_join,
) -> None:
    import threading

    import pytest

    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as probe
    import src.ingest_main as ingest_main

    first_scan_started = threading.Event()
    release_scan = threading.Event()
    raw_committed = threading.Event()
    advances: list[str] = []

    def fusion(_cfg, **_kwargs):
        first_scan_started.set()
        assert release_scan.wait(timeout=5)
        return {"status": "FUSION_UPGRADE_TRIGGER"}

    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", fusion)
    monkeypatch.setattr(prod, "_enqueue_cycle_advance_reseeds_if_needed", lambda _cfg, **_kwargs: {"status": "CYCLE_ADVANCE_TRIGGER"})
    monkeypatch.setattr(probe, "advance_source_clock_cursor", lambda _payload, *, sources: advances.extend(sources) or sources)
    assert ingest_main._enqueue_broad_reseed_batch(
        {"test": 1}, include_cycle_advance=True,
        source_clock_payload={
            "cursor_path": "/tmp/test", "updated_sources": ["icon_global"],
            "cursor_values": {"icon_global": "v1"},
            "cursor_preimage": {"icon_global": None},
        },
        cursor_sources=("icon_global",),
        download_report={"status": "downloaded"},
    ) == "SOURCE_BROAD_RESEEDS_ASYNC_PENDING"
    try:
        assert first_scan_started.wait(timeout=2)

        def failing_poll():
            raw_committed.set()
            raise RuntimeError("download failed after raw commit")

        with pytest.raises(RuntimeError):
            ingest_main._source_clock_poll_in_flight(failing_poll)()
        assert raw_committed.is_set()
    finally:
        release_scan.set()
        broad_reseed_join()
    assert advances == []


def test_pending_distinct_raw_receipts_each_keep_their_trigger_limit(
    monkeypatch, tmp_path, broad_reseed_join,
) -> None:
    import threading

    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as probe
    import src.ingest_main as ingest_main

    entered = threading.Event()
    release = threading.Event()
    seeds: list[str] = []
    advances: list[tuple[str, ...]] = []

    def fusion(cfg, *, manifest_snapshot):
        assert cfg["seed_limit"] == 1
        assert isinstance(manifest_snapshot, dict)
        if not seeds:
            entered.set()
            assert release.wait(timeout=5)
        # Model the producer's one-seed-per-call limit: the second distinct
        # receipt needs another call even when the first reports success.
        seeds.append(("scope-A", "scope-B")[len(seeds)])
        return {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 1}

    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", fusion)
    monkeypatch.setattr(probe, "advance_source_clock_cursor", lambda _payload, *, sources: advances.append(sources) or sources)

    def enqueue(scope):
        return ingest_main._enqueue_broad_reseed_batch(
            {"seed_limit": 1, "seed_dir": tmp_path},
            include_cycle_advance=False,
            source_clock_payload={
                "cursor_path": str(tmp_path / "cursor"),
                "updated_sources": ["icon_global"],
                "cursor_values": {"icon_global": "same-cycle"},
                "cursor_preimage": {"icon_global": None},
            },
            cursor_sources=("icon_global",),
            download_report={"status": scope},
        )

    try:
        assert enqueue("scope-A") == "SOURCE_BROAD_RESEEDS_ASYNC_PENDING"
        assert entered.wait(timeout=2)
        assert enqueue("scope-B") == "SOURCE_BROAD_RESEEDS_ASYNC_PENDING"
        assert advances == []
    finally:
        release.set()
        broad_reseed_join()
    assert seeds == ["scope-A", "scope-B"]
    # One cursor value, one CAS, and only after both receipts were scanned.
    assert advances == [("icon_global",)]


def test_broad_reseed_cursor_cas_cannot_rewind_later_provider_run(
    monkeypatch, tmp_path, broad_reseed_join,
) -> None:
    """Use the production cursor writer against a private test cursor file."""
    import json

    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    cursor_path = tmp_path / "cursor.json"
    early = "2026-09-23T06:00:00+00:00"
    later = "2026-09-23T12:00:00+00:00"
    route_hash = "a" * 64

    def payload(cycle):
        return {
            "cursor_path": str(cursor_path),
            "updated_sources": ["icon_global"],
            "cursor_values": {"icon_global": f"v4:{cycle}:{route_hash}"},
            "cursor_preimage": {"icon_global": None},
            "source_runs": {"icon_global": {"initialisation_time": cycle}},
        }

    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", lambda _cfg, **_kw: {"status": "FUSION_UPGRADE_TRIGGER"})
    monkeypatch.setattr(prod, "_enqueue_cycle_advance_reseeds_if_needed", lambda _cfg, **_kw: {"status": "CYCLE_ADVANCE_TRIGGER"})
    for cycle in (later, early):
        assert ingest_main._enqueue_broad_reseed_batch(
            {"test": 1},
            include_cycle_advance=True,
            source_clock_payload=payload(cycle),
            cursor_sources=("icon_global",),
            download_report={"status": "downloaded"},
        ) == "SOURCE_BROAD_RESEEDS_ASYNC_PENDING"
        broad_reseed_join()

    assert json.loads(cursor_path.read_text(encoding="utf-8"))["icon_global"] == (
        f"v4:{later}:{route_hash}"
    )


def test_source_clock_cursor_advances_while_broad_reseeds_stay_busy(
    monkeypatch, tmp_path, broad_reseed_join,
) -> None:
    """Live 2026-09-23 shape: a proven source's cursor must commit under constant churn.

    Polls every 15 s kept a pending batch behind every broad scan, so proofs
    only accumulated. A later chained scan then failed on an unrelated family's
    CYCLE_ADVANCE_RETRY_PENDING and discarded every earlier proof: 1082 of 1577
    eligible receipts died that way and no cursor committed for 36 h. The real
    probe payload and real cursor file are used here; only the provider is fake.
    """
    import json
    import threading

    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as probe
    import src.ingest_main as ingest_main

    route = "a" * 64
    run = "2026-09-23T18:00:00+00:00"
    cursor = tmp_path / "cursor.json"
    cursor.write_text(json.dumps({
        "icon_eu": f"v4:2026-09-23T12:00:00+00:00:{route}",
        "gfs_hrrr": f"v4:2026-09-23T12:00:00+00:00:{route}",
    }), encoding="utf-8")

    def report(**_kwargs):
        cursor_now = json.loads(cursor.read_text(encoding="utf-8"))
        changed = tuple(
            source for source in ("gfs_hrrr", "icon_eu")
            if cursor_now[source] != f"v4:{run}:{route}"
        )
        return probe.SourceClockUpdateProbeReport(
            status="SOURCE_CLOCK_UPDATES_CHANGED", model_count=2,
            updated_sources=changed, affected_cities=("Madrid",),
            model_updates_path=str(tmp_path / "updates.jsonl"),
            cursor_path=str(cursor),
            cursor_values=tuple((s, f"v4:{run}:{route}") for s in changed),
            cursor_preimage=tuple((s, cursor_now[s]) for s in changed),
            source_runs=tuple((s, run, run, 3600) for s in changed),
        )

    downloads = [0]

    def download(_cfg, *, source_clock_report, **_kwargs):
        downloads[0] += 1
        results = {
            "gfs_hrrr": {  # never materializable: stays retryable every poll
                "status": "SOURCE_CLOCK_SOURCE_TRANSPORT_RETRYABLE",
                "cycle": run, "written_row_count": 0,
            },
            "icon_eu": {  # first poll captured raw; later polls find it covered
                "status": (
                    "SOURCE_CLOCK_SOURCE_RAW_INPUTS_DOWNLOADED"
                    if downloads[0] == 1 else "SOURCE_CLOCK_SOURCE_NO_TARGETS"
                ),
                "cycle": run, "written_row_count": 18 if downloads[0] == 1 else 0,
            },
        }
        return {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "source_results": {
                s: results[s] for s in source_clock_report.updated_sources
            },
            "written_row_count": sum(
                results[s]["written_row_count"]
                for s in source_clock_report.updated_sources
            ),
            "committed_families": (),
            "source_commit_notifications": 0,
            "source_commit_notifications_pending": 0,
        }

    scan_started = threading.Event()
    release_scan = threading.Event()
    scans = [0]

    def fusion(_cfg, **_kwargs):
        scans[0] += 1
        if scans[0] == 1:
            scan_started.set()
            assert release_scan.wait(timeout=5)
        return {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 0}

    monkeypatch.setattr(prod, "_replacement_forecast_live_materialization_queue_config", lambda: {"test": 1})
    monkeypatch.setattr(prod, "_recover_held_common_cycle_anchors_if_needed", lambda *_a, **_k: None)
    monkeypatch.setattr(probe, "probe_openmeteo_source_clock_updates", report)
    monkeypatch.setattr(prod, "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed", download)
    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", fusion)

    def cycle_trigger(*_a, **_k):
        # The first scan publishes; every later one meets an unrelated family's
        # owner still in flight, as in the live .err log.
        return {"status": "CYCLE_ADVANCE_TRIGGER" if scans[0] == 1 else "CYCLE_ADVANCE_RETRY_PENDING"}

    monkeypatch.setattr(prod, "_enqueue_cycle_advance_reseeds_if_needed", cycle_trigger)
    poll = ingest_main._replacement_availability_poll_tick.__wrapped__

    try:
        first = poll()  # icon_eu raw lands; its broad scan starts and blocks
        assert scan_started.wait(timeout=2)
        assert first["source_clock_cursor_advanced_sources"] == ()
        busy = [poll() for _ in range(3)]  # re-detections while the scan is active
        assert all(r["source_clock_cursor_advanced_sources"] == () for r in busy)
        assert json.loads(cursor.read_text(encoding="utf-8"))["icon_eu"].startswith(
            "v4:2026-09-23T12:00"
        ), "cursor must not pass raw the running scan has not consumed"
    finally:
        release_scan.set()
        broad_reseed_join()

    state = json.loads(cursor.read_text(encoding="utf-8"))
    assert state["icon_eu"] == f"v4:{run}:{route}"
    assert state["gfs_hrrr"] == f"v4:2026-09-23T12:00:00+00:00:{route}"
    # The probe stops re-detecting the proven model; only the unproven one repeats.
    assert report().updated_sources == ("gfs_hrrr",)


def _late_callback_poll_harness(monkeypatch, tmp_path, *, polls, inline=()):
    """Real probe payload and cursor file; only the provider and triggers are fake.

    Live 2026-09-25 shape: a raw-writing poll leaves its commit callbacks
    running past the fanout deadline (pending > 0), and the next polls
    re-detect the same unadvanced runs. ``polls`` scripts each poll's
    per-source verdict and which sources get a callback that outlives it.
    """
    import json
    import threading

    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as probe

    route = "a" * 64
    run = "2026-09-25T06:00:00+00:00"
    old = f"v4:2026-09-25T00:00:00+00:00:{route}"
    new = f"v4:{run}:{route}"
    sources = ("icon_eu", "icon_global", "ncep_nbm_conus")
    cursor = tmp_path / "cursor.json"
    cursor.write_text(json.dumps({s: old for s in sources}), encoding="utf-8")

    def report(**_kwargs):
        now = json.loads(cursor.read_text(encoding="utf-8"))
        changed = tuple(s for s in sources if now[s] != new)
        return probe.SourceClockUpdateProbeReport(
            status="SOURCE_CLOCK_UPDATES_CHANGED", model_count=len(changed),
            updated_sources=changed, affected_cities=("Madrid",),
            model_updates_path=str(tmp_path / "updates.jsonl"),
            cursor_path=str(cursor),
            cursor_values=tuple((s, new) for s in changed),
            cursor_preimage=tuple((s, now[s]) for s in changed),
            source_runs=tuple((s, run, run, 3600) for s in changed),
        )

    gates: dict[str, threading.Event] = {}
    callbacks: list[threading.Thread] = []
    callback_errors: list[BaseException] = []
    script = iter(polls)

    def download(_cfg, *, source_clock_report, on_source_commit, **_kwargs):
        verdicts, late = next(script)
        results = {
            s: {"status": f"SOURCE_CLOCK_SOURCE_{verdicts[s][0]}", "cycle": run,
                "written_row_count": verdicts[s][1]}
            for s in source_clock_report.updated_sources
        }
        notified, failed = 0, []
        for source in inline:  # finishes inside the fanout deadline
            try:
                on_source_commit(source, {
                    "written_row_count": results[source]["written_row_count"],
                    "committed_families": (),
                })
                notified += 1
            except Exception as exc:  # noqa: BLE001 - the report carries it
                failed.append(f"{source}:{type(exc).__name__}: {exc}")
        for source in late:
            gate = gates.setdefault(source, threading.Event())

            def late_callback(source=source, gate=gate):
                assert gate.wait(timeout=5)
                try:
                    on_source_commit(source, {
                        "written_row_count": results[source]["written_row_count"],
                        "committed_families": (),
                    })
                except BaseException as exc:  # noqa: BLE001 - asserted by the test
                    callback_errors.append(exc)

            callbacks.append(threading.Thread(
                target=late_callback, name=f"late-{source}", daemon=True,
            ))
            callbacks[-1].start()
        return {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "source_results": results,
            "written_row_count": sum(r["written_row_count"] for r in results.values()),
            "committed_families": (),
            "source_commit_notifications": notified,
            "source_commit_notifications_pending": len(late),
            "source_commit_notification_errors": tuple(failed),
        }

    monkeypatch.setattr(prod, "_replacement_forecast_live_materialization_queue_config", lambda: {"test": 1})
    monkeypatch.setattr(prod, "_recover_held_common_cycle_anchors_if_needed", lambda *_a, **_k: None)
    monkeypatch.setattr(probe, "probe_openmeteo_source_clock_updates", report)
    monkeypatch.setattr(prod, "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed", download)
    return cursor, new, old, gates, callbacks, callback_errors


def _cursor_state(cursor):
    import json

    return json.loads(cursor.read_text(encoding="utf-8"))


@pytest.mark.parametrize("redetect", [False, True])
def test_late_commit_callback_proves_its_receipt_after_completion(
    monkeypatch, tmp_path, broad_reseed_join, redetect,
) -> None:
    """Live 2026-09-25 04:48-07:14 local: all 9 raw-writing receipts ended pending > 0.

    The poll forced cursor_sources=() whenever a callback outlived the fanout,
    so a productive source was never proven; the late completion was only
    logged. A late callback is a completed publication: it must prove its own
    receipt's sources once it finishes, and never before. The next poll
    re-detects the same run meanwhile; that re-detection must not void it.
    """
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    raw = {"icon_eu": ("RAW_INPUTS_DOWNLOADED", 12),
           "icon_global": ("RAW_INPUTS_DOWNLOADED", 216),
           "ncep_nbm_conus": ("TRANSPORT_RETRYABLE", 0)}
    same_run = {"icon_eu": ("NO_TARGETS", 0), "icon_global": ("NO_TARGETS", 0),
                "ncep_nbm_conus": ("TRANSPORT_RETRYABLE", 0)}
    cursor, new, old, gates, callbacks, errors = _late_callback_poll_harness(
        monkeypatch, tmp_path,
        polls=[(raw, ("icon_eu", "icon_global")), (same_run, ())][: 1 + redetect],
    )
    published: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        prod, "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg, **kw: published.append(tuple(kw.get("changed_sources") or ()))
        or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 0},
    )
    monkeypatch.setattr(
        prod, "_enqueue_cycle_advance_reseeds_if_needed",
        lambda *_a, **_k: {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 0},
    )
    poll = ingest_main._replacement_availability_poll_tick.__wrapped__
    try:
        first = poll()
        assert first["source_commit_notifications_pending"] == 2
        assert first["source_clock_cursor_advanced_sources"] == ()
        broad_reseed_join()
        assert set(_cursor_state(cursor).values()) == {old}, (
            "cursor must not pass raw whose commit callback has not published"
        )
        if redetect:  # the same unadvanced run, before the callbacks finish
            second = poll()
            broad_reseed_join()
            assert second["source_clock_cursor_advanced_sources"] == ()
            assert set(_cursor_state(cursor).values()) == {old}
        gates["icon_eu"].set()
        callbacks[0].join(timeout=5)
        assert _cursor_state(cursor)["icon_eu"] == old, (
            "one finished callback cannot release a receipt another still holds"
        )
        gates["icon_global"].set()
        callbacks[1].join(timeout=5)
    finally:
        for gate in gates.values():
            gate.set()
        for thread in callbacks:
            thread.join(timeout=5)
        broad_reseed_join()
    assert errors == []
    state = _cursor_state(cursor)
    assert state["icon_eu"] == new and state["icon_global"] == new
    assert state["ncep_nbm_conus"] == old, "a TRANSPORT_RETRYABLE sibling stays unproven"
    assert ingest_main._BROAD_RESEED_OPEN == {}


@pytest.mark.parametrize("late", [True, False])
def test_late_commit_callback_failure_proves_nothing(
    monkeypatch, tmp_path, broad_reseed_join, late,
) -> None:
    """A commit callback whose scoped publication raises leaves its source unproven.

    Its raw is unconsumed by any published reseed. The receipt is numbered
    before its raw lands, so the block lands above that receipt's own proof
    whether the callback fails late or inside the fanout deadline.
    """
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    raw = {"icon_eu": ("RAW_INPUTS_DOWNLOADED", 12),
           "icon_global": ("RAW_INPUTS_DOWNLOADED", 216),
           "ncep_nbm_conus": ("RAW_INPUTS_DOWNLOADED", 3)}
    cursor, new, old, gates, callbacks, errors = _late_callback_poll_harness(
        monkeypatch, tmp_path, polls=[(raw, ("icon_global",) if late else ())],
        inline=() if late else ("icon_global",),
    )
    failing = [not late]  # an inline callback runs on the poll thread

    import threading

    def fusion(_cfg, **_kw):
        if failing[0] or threading.current_thread().name == "late-icon_global":
            failing[0] = False
            return {"status": "FUSION_UPGRADE_TRIGGER_FAILSOFT_SKIPPED"}
        return {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 0}

    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", fusion)
    monkeypatch.setattr(
        prod, "_enqueue_cycle_advance_reseeds_if_needed",
        lambda *_a, **_k: {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 0},
    )
    try:
        first = ingest_main._replacement_availability_poll_tick.__wrapped__()
        broad_reseed_join()
        if late:
            assert first["source_clock_cursor_advanced_sources"] == ()
            gates["icon_global"].set()
            callbacks[0].join(timeout=5)
        else:
            assert first["source_commit_notification_errors"]
    finally:
        for gate in gates.values():
            gate.set()
        for thread in callbacks:
            thread.join(timeout=5)
        broad_reseed_join()
    assert len(errors) == int(late)
    assert all("source commit reseed unproven" in str(exc) for exc in errors)
    state = _cursor_state(cursor)
    assert state["icon_global"] == old, "a failed late callback must not prove"
    # Siblings the broad scan published are not held hostage by the failure.
    assert state["icon_eu"] == new and state["ncep_nbm_conus"] == new
    assert ingest_main._BROAD_RESEED_OPEN == {}


def test_same_run_redetection_does_not_void_earlier_proof(
    monkeypatch, tmp_path, broad_reseed_join,
) -> None:
    """Live 2026-09-25 06:58 local: advanced_sources=() with the productive sources
    deferred. The next poll re-detected the same unadvanced runs, found nothing
    new (NO_TARGETS, or still retryable), and blocked every source it could not
    prove, voiding the earlier proof of that exact cursor value. A re-detection
    that writes no raw is not new unconsumed raw and must leave the proof intact.
    """
    import threading

    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    raw = {"icon_eu": ("RAW_INPUTS_DOWNLOADED", 12),
           "icon_global": ("RAW_INPUTS_DOWNLOADED", 216),
           "ncep_nbm_conus": ("TRANSPORT_RETRYABLE", 0)}
    same_run = {"icon_eu": ("TRANSPORT_RETRYABLE", 0), "icon_global": ("NO_TARGETS", 0),
                "ncep_nbm_conus": ("TRANSPORT_RETRYABLE", 0)}
    cursor, new, old, _gates, _callbacks, _errors = _late_callback_poll_harness(
        monkeypatch, tmp_path, polls=[(raw, ()), (same_run, ())],
    )
    scan_started = threading.Event()
    release_scan = threading.Event()
    scans = [0]

    def fusion(_cfg, **_kw):
        scans[0] += 1
        if scans[0] == 1:  # the first receipt's scan is still consuming its raw
            scan_started.set()
            assert release_scan.wait(timeout=5)
        return {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 0}

    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", fusion)
    monkeypatch.setattr(
        prod, "_enqueue_cycle_advance_reseeds_if_needed",
        lambda *_a, **_k: {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 0},
    )
    poll = ingest_main._replacement_availability_poll_tick.__wrapped__
    try:
        poll()
        assert scan_started.wait(timeout=2)
        again = poll()  # the same runs, while the first scan still runs
        assert again["source_clock_cursor_advanced_sources"] == ()
        assert set(_cursor_state(cursor).values()) == {old}
    finally:
        release_scan.set()
        broad_reseed_join()
    state = _cursor_state(cursor)
    assert state["icon_eu"] == new and state["icon_global"] == new
    assert state["ncep_nbm_conus"] == old


def test_legacy_scheduler_mode_flags_deleted() -> None:
    """R3 (2026-07-08): the legacy hand-coded add_job() scheduler mode and its mode-selection
    flags were deleted (zero-caller-verified — no deploy/launchd plist ever set them). The
    registry-built scheduler is unconditional now, not merely the default."""
    from src.data import scheduler_adapter as sa

    for removed in (
        "DATA_COLLECTION_MODE_FLAG", "LEGACY_DATA_COLLECTION_FLAG", "SCHEDULER_REGISTRY_FLAG",
        "REGISTRY_MODE", "LEGACY_MODE", "data_collection_mode", "registry_scheduler_active",
        "assert_single_collection_mode",
    ):
        assert not hasattr(sa, removed), f"{removed} should have been deleted with legacy mode"


def test_no_db_writer_on_file_only_executor() -> None:
    """STRUCTURAL ANTIBODY: every writes_db job is assigned a *_db executor class, never
    io/heartbeat. This is the lock-starvation fix the whole 'fast' split exists for."""
    from src.data.scheduler_adapter import build_job_specs, validate_executor_assignment

    specs = build_job_specs()
    assert validate_executor_assignment(specs) == []
    for s in specs:
        if s.is_db_writer:
            assert s.executor_class.endswith("_db")
            assert s.executor_class not in ("io", "diagnostic_io", "heartbeat")


def test_validator_catches_writes_db_on_file_only_lane() -> None:
    """ANTIBODY (PR #329 review P2): the validator must compare the REGISTRY writes_db truth
    against the assigned executor class. The prior check used ``is_db_writer`` (==
    executor_class.endswith('_db')), making ``is_db_writer and class in (io,heartbeat)``
    unreachable — a tautology that could never fire. Plant a writes_db job on the heartbeat lane
    and require a violation, so a future executor_class_for() regression is caught."""
    from src.data.scheduler_adapter import JobBuildSpec, validate_executor_assignment

    # ingest_market_scan is writes_db=True in the registry; route it to a file-only lane:
    planted = [JobBuildSpec("ingest_market_scan", "ingest_main", "heartbeat", 1, True, 60)]
    violations = validate_executor_assignment(planted)
    assert violations and "ingest_market_scan" in violations[0], (
        "validator failed to flag a writes_db job on a file-only executor (tautology regression)"
    )


def test_retired_alternate_jobs_are_not_schedulable() -> None:
    """Single-live semantics must not silently revive retired alternate writers."""
    from src.data.scheduler_adapter import build_job_specs

    by_id = {s.job_id: s for s in build_job_specs()}
    assert "ingest_uma_resolution_listener" not in by_id
    assert "ingest_calibration_auto_promote" not in by_id
    assert by_id["ingest_harvester_truth_writer"].executor_class == "settlement_db"


def test_executor_class_assignments_by_role() -> None:
    from src.data.scheduler_adapter import build_job_specs

    by_id = {s.job_id: s for s in build_job_specs()}
    assert by_id["ingest_harvester_truth_writer"].executor_class == "settlement_db"
    assert by_id["ingest_market_scan"].executor_class == "market_topology_db"
    assert by_id["ingest_k2_forecasts_daily"].executor_class == "forecast_archive_db"
    assert by_id["ingest_opendata_daily_mx2t6"].executor_class == "forecast_source_db"
    assert by_id["ingest_replacement_availability_poll"].executor_class == "forecast_clock_db"
    assert (
        by_id["ingest_station_forecast_source_clock"].executor_class
        == "station_forecast_clock_db"
    )
    assert by_id["ingest_replacement_maintenance"].executor_class == "forecast_repair_db"
    assert by_id["ingest_etl_recalibrate"].executor_class == "derived_db"
    assert by_id["ingest_day0_oracle_anomaly"].executor_class == "oracle_guard_db"
    assert by_id["ingest_k2_obs_fast_tick"].executor_class == "observation_db"
    assert by_id["ingest_tigge_archive_backfill"].executor_class == "backfill_db"
    assert by_id["ingest_heartbeat"].executor_class == "heartbeat"
    assert by_id["ingest_source_health_probe"].executor_class == "health_io"


def test_unclassified_live_db_writer_fails_closed() -> None:
    import pytest

    from src.data.scheduler_adapter import executor_class_for
    from src.data.source_job_registry import SourceJobSpec

    unknown = SourceJobSpec("new_live_writer", "ingest_main", "live", "default", True)
    with pytest.raises(ValueError, match="no explicit causal executor lane"):
        executor_class_for(unknown)


def test_all_jobs_single_instance_coalesce_preserved() -> None:
    """F10: every job (incl. heartbeat/health/status) is single-instance + coalesce, matching
    the current scheduler. The prior 3/coalesce=False for non-DB jobs would have made
    heartbeats/health overlap on activation — not behavior-preserving."""
    from src.data.scheduler_adapter import build_job_specs

    for s in build_job_specs():
        assert s.max_instances == 1, f"{s.job_id} max_instances must be 1"
        assert s.coalesce is True, f"{s.job_id} must coalesce"


def test_forecast_repair_lane_admits_only_the_ingest_maintenance_owner() -> None:
    from src.data.scheduler_adapter import (
        JobBuildSpec, build_job_specs, validate_executor_assignment,
        validate_lane_separation,
    )

    specs = build_job_specs()
    assert validate_lane_separation(specs) == []
    repair = [spec for spec in specs if spec.executor_class == "forecast_repair_db"]
    assert [(spec.job_id, spec.owner_daemon) for spec in repair] == [
        ("ingest_replacement_maintenance", "ingest_main")
    ]
    planted = [
        JobBuildSpec("ingest_etl_recalibrate", "ingest_main", "forecast_repair_db", 1, True, 300),
        JobBuildSpec("ingest_replacement_maintenance", "forecast_live_daemon", "forecast_repair_db", 1, True, 300),
        JobBuildSpec("ingest_replacement_maintenance", "ingest_main", "derived_db", 1, True, 300),
        JobBuildSpec("unregistered_writer", "ingest_main", "forecast_repair_db", 1, True, 300),
    ]
    for validator in (validate_executor_assignment, validate_lane_separation):
        violations = validator(planted)
        assert len(violations) == 4
        assert "ingest_etl_recalibrate" in violations[0]
        assert "ingest_replacement_maintenance" in violations[1]
        assert "ingest_replacement_maintenance" in violations[2]
        assert "unregistered_writer" in violations[3]


def test_real_scheduler_runs_forecast_repair_while_derived_busy_without_overlap() -> None:
    from datetime import datetime, timezone
    from threading import Event

    from apscheduler.events import EVENT_JOB_MAX_INSTANCES
    from apscheduler.schedulers.background import BackgroundScheduler

    from src.data.scheduler_adapter import build_job_specs, registry_executor_pools

    specs = {spec.job_id: spec for spec in build_job_specs("ingest_main")}
    derived = specs["ingest_etl_recalibrate"]
    repair = specs["ingest_replacement_maintenance"]
    derived_entered, derived_release = Event(), Event()
    repair_entered, repair_release, repair_exited = Event(), Event(), Event()
    overlap_rejected = Event()
    repair_runs: list[int] = []
    scheduler = BackgroundScheduler(executors=registry_executor_pools(), timezone=timezone.utc)
    scheduler.add_listener(
        lambda event: overlap_rejected.set()
        if event.job_id == repair.job_id else None,
        EVENT_JOB_MAX_INSTANCES,
    )

    def derived_job() -> None:
        derived_entered.set()
        assert derived_release.wait(3)

    def repair_job() -> None:
        repair_runs.append(1)
        repair_entered.set()
        try:
            assert repair_release.wait(3)
        finally:
            repair_exited.set()

    try:
        scheduler.start()
        scheduler.add_job(
            derived_job, "date", run_date=datetime.now(timezone.utc),
            id=derived.job_id, executor=derived.executor_class,
            max_instances=derived.max_instances, coalesce=derived.coalesce,
        )
        assert derived_entered.wait(2), "long recalibration never entered derived_db"
        scheduler.add_job(
            repair_job, "interval", seconds=0.05,
            next_run_time=datetime.now(timezone.utc),
            id=repair.job_id, executor=repair.executor_class,
            max_instances=repair.max_instances, coalesce=repair.coalesce,
        )
        assert repair_entered.wait(2), "forecast repair starved behind recalibration"
        assert overlap_rejected.wait(2), "second repair run should be rejected while first is active"
        assert repair_runs == [1]
        assert not derived_release.is_set(), "repair must start while derived lane is still busy"
        scheduler.remove_job(repair.job_id)
    finally:
        repair_release.set()
        derived_release.set()
        scheduler.shutdown(wait=True)
    assert repair_exited.is_set()
    assert repair_runs == [1]


def test_forecast_repair_cannot_be_reclassified_as_file_only_or_other_owner() -> None:
    from dataclasses import replace

    from src.data.scheduler_adapter import executor_class_for
    from src.data.source_job_registry import JOB_REGISTRY

    maintenance = JOB_REGISTRY["ingest_replacement_maintenance"]
    for forged in (
        replace(maintenance, writes_db=False),
        replace(maintenance, role="health"),
        replace(maintenance, owner_daemon="forecast_live_daemon"),
    ):
        with pytest.raises(ValueError, match="requires ingest_main derived DB writer"):
            executor_class_for(forged)


def test_replacement_availability_poll_uses_fast_source_clock_cadence(monkeypatch) -> None:
    """The source-clock download poll must not sit behind the old 5-minute interval."""
    import src.ingest_main as ingest_main

    def _poll_kwargs() -> dict:
        for _fn, trigger, kwargs in ingest_main._ingest_main_job_specs():
            if kwargs.get("id") == "ingest_replacement_availability_poll":
                assert trigger == "interval"
                return kwargs
        raise AssertionError("ingest_replacement_availability_poll spec missing")

    def _maintenance_kwargs() -> dict:
        for _fn, trigger, kwargs in ingest_main._ingest_main_job_specs():
            if kwargs.get("id") == "ingest_replacement_maintenance":
                assert trigger == "interval"
                return kwargs
        raise AssertionError("ingest_replacement_maintenance spec missing")

    monkeypatch.delenv(ingest_main.REPLACEMENT_AVAILABILITY_POLL_SECONDS_ENV, raising=False)
    kwargs = _poll_kwargs()
    assert kwargs["seconds"] == 15
    assert "minutes" not in kwargs
    assert kwargs["misfire_grace_time"] == 120
    assert kwargs["next_run_time"] is not None
    assert _maintenance_kwargs()["seconds"] == 60

    monkeypatch.setenv(ingest_main.REPLACEMENT_AVAILABILITY_POLL_SECONDS_ENV, "20")
    assert _poll_kwargs()["seconds"] == 20

    monkeypatch.setenv(ingest_main.REPLACEMENT_AVAILABILITY_POLL_SECONDS_ENV, "5")
    assert _poll_kwargs()["seconds"] == 15


def test_replacement_current_target_maintenance_stays_minute_bounded(
    monkeypatch,
) -> None:
    import src.ingest_main as ingest_main

    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.delenv(
        ingest_main.REPLACEMENT_AVAILABILITY_POLL_SECONDS_ENV,
        raising=False,
    )

    assert ingest_main._replacement_maintenance_due(now_monotonic=100.0)
    assert not ingest_main._replacement_maintenance_due(now_monotonic=159.999)
    assert ingest_main._replacement_maintenance_due(now_monotonic=160.0)


@pytest.mark.parametrize(
    ("source_status", "source_error", "expected_status", "expected_failed"),
    (
        (
            "SOURCE_CLOCK_NO_PUBLICLY_USABLE_CHANGE",
            None,
            "SOURCE_CLOCK_POLL_CURRENT",
            False,
        ),
        (
            "SOURCE_CLOCK_MODEL_UPDATES_DEGRADED_CACHE",
            "metadata transport unavailable",
            "SOURCE_CLOCK_MODEL_UPDATES_DEGRADED_CACHE",
            True,
        ),
    ),
)
def test_replacement_availability_fast_poll_skips_heavy_path_when_source_clock_current(
    monkeypatch, source_status, source_error, expected_status, expected_failed
) -> None:
    """The source-clock poll must stay lightweight when no public run changed."""
    import src.ingest_main as ingest_main
    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as source_clock_probe

    class _NoChange:
        updated_sources = ()

        def as_dict(self):
            return {
                "status": source_status,
                "updated_sources": [],
                "affected_cities": [],
                "error": source_error,
            }

    def _scoped_path(*_args, **_kwargs):
        raise AssertionError("scoped source-clock download path should not run without a source-clock change")

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    call_order: list[str] = []
    probe_kwargs: list[dict[str, object]] = []

    def _probe(**kwargs):
        call_order.append("probe")
        probe_kwargs.append(kwargs)
        return _NoChange()

    monkeypatch.setattr(source_clock_probe, "probe_openmeteo_source_clock_updates", _probe)
    monkeypatch.setattr(source_clock_probe, "advance_source_clock_cursor", lambda report: ())
    monkeypatch.setattr(prod, "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed", _scoped_path)
    current_target_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda cfg, **_kwargs: call_order.append("current_targets")
        or current_target_calls.append(dict(cfg))
        or {
            "status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS",
            "coverage": {
                "status": "CURRENT_TARGETS_MISSING_REPLACEMENT_COVERAGE",
                "target_count": 2,
                "covered_count": 1,
                "missing_coverage_count": 1,
                "can_seed_count": 0,
                "missing_openmeteo_manifest_count": 0,
                "day0_observed_extreme_required_count": 0,
            },
        },
    )
    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", lambda cfg: None)
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda cfg: {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 0, "advances_detected": 0},
    )

    result = ingest_main._replacement_availability_poll_tick.__wrapped__()

    assert result["status"] == expected_status
    assert result["source_clock_status"] == source_status
    assert ingest_main._classify_result(result)[0] is expected_failed
    assert result["source_clock_updated_sources"] == []
    assert result["maintenance_status"] == "REPLACEMENT_MAINTENANCE_DECOUPLED"
    assert current_target_calls == []
    assert probe_kwargs == [{"advance_cursor": False}]
    assert call_order == ["probe"]


def test_replacement_availability_drains_exact_cycle_anchor_residual_on_priority_lane(
    monkeypatch, tmp_path
) -> None:
    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as source_clock_probe
    import src.ingest_main as ingest_main

    class _NoChange:
        updated_sources = ()

        def as_dict(self):
            return {
                "status": "SOURCE_CLOCK_NO_PUBLICLY_USABLE_CHANGE",
                "updated_sources": [],
                "affected_cities": [],
                "error": None,
                "source_runs": {
                    "ecmwf_ifs": {
                        "initialisation_time": "2026-08-21T12:00:00+00:00"
                    }
                },
            }

    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {
            "download_current_targets_enabled": True,
            "forecast_db": tmp_path / "forecasts.db",
        },
    )
    monkeypatch.setattr(
        source_clock_probe,
        "probe_openmeteo_source_clock_updates",
        lambda **_kwargs: _NoChange(),
    )
    monkeypatch.setattr(prod, "_current_target_anchor_gap_count", lambda *_args: 205)

    def _download(_cfg, **kwargs):
        calls.append(("download", kwargs))
        return {
            "status": "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED",
            "written_manifest_count": 10,
        }

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _download,
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unchanged source clock must not run BPF source fanout")
        ),
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg, **kwargs: calls.append(("fusion", kwargs))
        or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 10},
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg, **kwargs: calls.append(("cycle", kwargs))
        or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 10},
    )

    result = ingest_main._replacement_availability_poll_tick.__wrapped__()

    assert result["anchor_missing_scope_count"] == 205
    assert result["source_clock_anchor_residual_download"] == {
        "status": "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED",
        "fusion_upgrade_status": "FUSION_UPGRADE_TRIGGER",
        "fusion_upgrade_seeds_enqueued": 10,
        "cycle_advance_status": "CYCLE_ADVANCE_TRIGGER",
        "cycle_advance_seeds_enqueued": 10,
    }
    assert calls[0][0] == "download"
    assert calls[0][1]["quota_priority"] is True
    assert 0.0 < calls[0][1]["max_wall_clock_seconds"] <= 20.0
    assert calls[1:] == [
        ("fusion", {"changed_sources": ("ecmwf_ifs",)}),
        ("cycle", {}),
    ]


def test_replacement_materializer_default_limit_matches_seed_burst(monkeypatch) -> None:
    """Defaults keep both capacity and the canonical live repair lane available."""
    import src.data.replacement_forecast_production as prod
    from src.config import STATE_DIR

    source = prod.settings._data if hasattr(prod.settings, "_data") else prod.settings
    monkeypatch.setitem(source, "replacement_forecast_live", {})

    cfg = prod._replacement_forecast_live_materialization_queue_config()

    assert cfg["seed_discovery_limit"] == 80
    assert cfg["seed_limit"] == 80
    assert cfg["limit"] == 80
    assert cfg["poll_batch_limit"] == 8
    assert cfg["limit"] >= cfg["seed_limit"]
    assert cfg["forecast_db"] == STATE_DIR / "zeus-forecasts.db"
    assert cfg["raw_manifest_dir"] == (
        STATE_DIR / "replacement_forecast_live" / "raw_manifests"
    )


def test_replacement_materialize_poll_reclaims_priority_after_each_worker_tranche(
    monkeypatch,
) -> None:
    """Every hot-queue branch must use the configured bounded micro-batch."""
    import src.data.replacement_forecast_production as prod
    import src.ingest.forecast_live_daemon as daemon

    cfg = {
        "request_dir": "requests",
        "seed_dir": "seeds",
        "poll_batch_limit": 8,
    }
    pending = {"request_dir": False, "seed_dir": False, "inflight": False}
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        daemon,
        "_replacement_forecast_queue_pending",
        lambda _cfg, key: pending[key],
    )
    monkeypatch.setattr(
        daemon,
        "_replacement_forecast_inflight_pending",
        lambda _cfg: pending["inflight"],
    )
    monkeypatch.setattr(
        daemon,
        "_replacement_forecast_materialize_job",
        lambda **kwargs: calls.append(kwargs),
    )

    pending["request_dir"] = True
    daemon._replacement_forecast_materialize_poll_job()
    pending["seed_dir"] = True
    daemon._replacement_forecast_materialize_poll_job()
    pending["request_dir"] = False
    daemon._replacement_forecast_materialize_poll_job()
    pending["seed_dir"] = False
    pending["inflight"] = True
    daemon._replacement_forecast_materialize_poll_job()

    assert calls == [
        {"discover": False, "limit": 1, "seed_limit": 0},
        {"discover": False, "limit": 1, "seed_limit": 8},
        {"discover": False, "limit": 1, "seed_limit": 8},
        {"discover": False, "limit": 1, "seed_limit": 0},
    ]


def test_replacement_discovery_is_not_limited_by_poll_claim_size(
    monkeypatch, tmp_path
) -> None:
    """Discovery may queue the configured burst; the poller still claims it incrementally."""
    import src.data.replacement_forecast_production as prod
    import src.data.replacement_forecast_seed_discovery as discovery
    import src.ingest.forecast_live_daemon as daemon

    cfg = {
        "forecast_db": tmp_path / "forecast.db",
        "raw_manifest_dir": tmp_path / "raw",
        "seed_dir": tmp_path / "seeds",
        "request_dir": tmp_path / "requests",
        "inflight_dir": tmp_path / "claims",
        "seed_discovery_limit": 80,
        "poll_batch_limit": 8,
    }
    calls: list[dict[str, object]] = []

    class _Report:
        status = "NO_ELIGIBLE_TARGETS"
        discovered_count = 80

        @staticmethod
        def as_dict() -> dict[str, object]:
            return {}

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        daemon,
        "_replacement_forecast_discovery_revision",
        lambda _cfg: ("revision",),
    )
    monkeypatch.setattr(
        discovery,
        "discover_replacement_forecast_materialization_seeds",
        lambda **kwargs: calls.append(kwargs) or _Report(),
    )
    monkeypatch.setattr(daemon, "_replacement_forecast_last_discovery_revision", None)

    daemon._replacement_forecast_discovery_job.__wrapped__()

    assert calls == [
        {
            "forecast_db": cfg["forecast_db"],
            "raw_manifest_dir": cfg["raw_manifest_dir"],
            "seed_dir": cfg["seed_dir"],
            "request_dir": cfg["request_dir"],
            "inflight_dir": cfg["inflight_dir"],
            "limit": 80,
        }
    ]
    assert daemon._replacement_forecast_last_discovery_revision is None

    _Report.discovered_count = 7
    daemon._replacement_forecast_discovery_job.__wrapped__()

    assert daemon._replacement_forecast_last_discovery_revision == ("revision",)


def test_replacement_discovery_revision_advances_on_fast_observation_print(
    monkeypatch, tmp_path
) -> None:
    """A new fast METAR must invalidate Day0 materialization discovery."""
    import sqlite3

    import src.ingest.forecast_live_daemon as daemon
    import src.state.db as state_db

    forecast_db = tmp_path / "forecast.db"
    forecast = sqlite3.connect(forecast_db)
    forecast.executescript(
        """
        CREATE TABLE market_events (event_id INTEGER PRIMARY KEY);
        CREATE TABLE raw_model_forecasts (raw_model_forecast_id INTEGER PRIMARY KEY);
        CREATE TABLE raw_forecast_artifacts (artifact_id INTEGER PRIMARY KEY);
        CREATE TABLE source_run_coverage (source_run_id TEXT);
        CREATE TABLE readiness_state (expires_at TEXT);
        """
    )
    forecast.commit()
    forecast.close()

    world_db = tmp_path / "world.db"
    world = sqlite3.connect(world_db)
    world.executescript(
        """
        CREATE TABLE observation_instants (id INTEGER PRIMARY KEY);
        CREATE TABLE observation_prints (id INTEGER PRIMARY KEY);
        INSERT INTO observation_instants(id) VALUES (7);
        INSERT INTO observation_prints(id) VALUES (11);
        """
    )
    world.commit()
    world.close()
    monkeypatch.setattr(state_db, "ZEUS_WORLD_DB_PATH", world_db)

    cfg = {"forecast_db": forecast_db}
    before = daemon._replacement_forecast_discovery_revision(cfg)

    world = sqlite3.connect(world_db)
    world.execute("INSERT INTO observation_prints(id) VALUES (12)")
    world.commit()
    world.close()
    after = daemon._replacement_forecast_discovery_revision(cfg)

    assert before is not None and after is not None
    assert before[-3:] == (7, 11, before[-1])
    assert after[-3:] == (7, 12, after[-1])
    assert before != after


def test_replacement_discovery_runs_with_backlog_and_retries_pending_family(
    monkeypatch, tmp_path
) -> None:
    import src.data.replacement_forecast_production as prod
    import src.data.replacement_forecast_seed_discovery as discovery
    import src.ingest.forecast_live_daemon as daemon

    cfg = {
        "forecast_db": tmp_path / "forecast.db",
        "raw_manifest_dir": tmp_path / "raw",
        "seed_dir": tmp_path / "seeds",
        "request_dir": tmp_path / "requests",
        "inflight_dir": tmp_path / "claims",
        "seed_discovery_limit": 10,
    }
    cfg["request_dir"].mkdir()
    (cfg["request_dir"] / "unrelated.json").write_text("{}")
    calls: list[dict[str, object]] = []

    class _Report:
        status = "NO_ELIGIBLE_TARGETS"
        discovered_count = 0
        reason_codes = (
            "REPLACEMENT_SEED_DISCOVERY_TARGET_ALREADY_PENDING_SKIPPED",
        )

        @staticmethod
        def as_dict() -> dict[str, object]:
            return {}

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: cfg,
    )
    monkeypatch.setattr(
        daemon,
        "_replacement_forecast_discovery_revision",
        lambda _cfg: ("revision",),
    )
    monkeypatch.setattr(
        discovery,
        "discover_replacement_forecast_materialization_seeds",
        lambda **kwargs: calls.append(kwargs) or _Report(),
    )
    monkeypatch.setattr(daemon, "_replacement_forecast_last_discovery_revision", None)

    daemon._replacement_forecast_discovery_job.__wrapped__()

    assert len(calls) == 1
    assert calls[0]["request_dir"] == cfg["request_dir"]
    assert calls[0]["inflight_dir"] == cfg["inflight_dir"]
    assert daemon._replacement_forecast_last_discovery_revision is None


def test_replacement_availability_fast_poll_passes_changed_source_clock_report(
    monkeypatch, broad_reseed_join
) -> None:
    """A scoped commit must run one broad catch-up without duplicating its markers."""
    import src.ingest_main as ingest_main
    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as source_clock_probe

    class _Changed:
        updated_sources = ("icon_global",)

        def as_dict(self):
            return {
                "status": "SOURCE_CLOCK_UPDATES_CHANGED",
                "updated_sources": ["icon_global"],
                "affected_cities": ["Munich"],
                "error": None,
            }

    changed_report = _Changed()
    call_order: list[str] = []

    def _scoped_path(
        cfg,
        *,
        source_clock_report=None,
        max_wall_clock_seconds=None,
        on_source_commit=None,
    ):
        call_order.append("scoped_download")
        assert cfg["download_current_targets_enabled"] is True
        assert source_clock_report is changed_report
        assert max_wall_clock_seconds == 45.0
        assert on_source_commit is not None
        on_source_commit(
            "icon_global",
            {
                "written_row_count": 9,
                "committed_families": (
                    ("Seoul", "2026-07-03", "high"),
                    ("Wellington", "2026-07-03", "high"),
                ),
            },
        )
        call_order.append("scoped_download_complete")
        return {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "updated_sources": ["icon_global"],
            "source_clock_status": "SOURCE_CLOCK_UPDATES_CHANGED",
            "source_clock_updated_sources": ["icon_global"],
        }

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    probe_kwargs: list[dict[str, object]] = []

    def _probe(**kwargs):
        call_order.append("probe")
        probe_kwargs.append(kwargs)
        return changed_report

    monkeypatch.setattr(source_clock_probe, "probe_openmeteo_source_clock_updates", _probe)
    monkeypatch.setattr(
        source_clock_probe,
        "advance_source_clock_cursor",
        lambda report, *, sources=None: call_order.append("cursor")
        or tuple(sources or ()),
    )
    monkeypatch.setattr(prod, "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed", _scoped_path)
    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery.held_position_family_priorities",
        lambda: {
            ("Seoul", "2026-07-03", "high"): 0,
            ("Wellington", "2026-07-03", "high"): 1,
        },
    )
    anchor_calls: list[dict[str, object]] = []

    def _download_anchor(_cfg, **kwargs):
        call_order.append("anchor_scope_download")
        anchor_calls.append(kwargs)
        cities = tuple(scope[0] for scope in kwargs["required_scopes"])
        return {
            "status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS",
            "available_cycle": "2026-07-02T12:00:00+00:00",
            "written_manifest_count": len(cities),
            "written_manifests": [
                f"/tmp/{city.lower()}-high.manifest.json" for city in cities
            ],
            "coverage": {
                "status": "CURRENT_TARGETS_MISSING_REPLACEMENT_COVERAGE",
                "target_count": 2,
                "covered_count": 2,
                "missing_coverage_count": 0,
                "can_seed_count": 0,
                "missing_openmeteo_manifest_count": 0,
                "day0_observed_extreme_required_count": 0,
            },
        }

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _download_anchor,
    )
    fusion_calls: list[dict[str, object]] = []
    raw_revision = "icon_global:2026-07-03T12:00:00Z"
    all_changed_scopes = (
        ("Seoul", "2026-07-03", "high"),
        ("Wellington", "2026-07-03", "high"),
        ("Paris", "2026-07-03", "high"),
    )
    markers: set[tuple[tuple[str, str, str], str]] = set()

    def _fusion_reseed(_cfg, **kwargs):
        call_order.append("fusion_reseed")
        fusion_calls.append(kwargs)
        scopes = tuple(kwargs.get("scopes") or all_changed_scopes)
        enqueued = [
            scope for scope in scopes if (scope, raw_revision) not in markers
        ]
        markers.update((scope, raw_revision) for scope in enqueued)
        return {
            "status": "FUSION_UPGRADE_TRIGGER",
            "seeds_enqueued": len(enqueued),
        }

    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", _fusion_reseed)
    cycle_calls: list[dict[str, object]] = []

    def _cycle_reseed(_cfg, **kwargs):
        call_order.append("cycle_reseed")
        cycle_calls.append(kwargs)
        return {
            "status": "CYCLE_ADVANCE_TRIGGER",
            "seeds_enqueued": 2,
            "advances_detected": 2,
            "held_advances_detected": 1,
            "freshest_materializable_cycle": "2026-07-02T12:00:00+00:00",
        }

    monkeypatch.setattr(prod, "_enqueue_cycle_advance_reseeds_if_needed", _cycle_reseed)

    result = ingest_main._replacement_availability_poll_tick.__wrapped__()
    broad_reseed_join()

    assert result["status"] == "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    assert result["source_clock_updated_sources"] == ["icon_global"]
    assert "current_target_download" not in result
    assert result["fusion_upgrade_seeds_enqueued"] == 2
    assert "broad_fusion_upgrade_seeds_enqueued" not in result
    assert result["cycle_advance_seeds_enqueued"] == 2
    assert result["cycle_advance_detail"]["held_advances_detected"] == 1
    assert result["source_clock_cursor_advanced_sources"] == ()
    assert result["source_clock_cursor_deferred_sources"] == ("icon_global",)
    assert probe_kwargs == [{"advance_cursor": False}]
    assert fusion_calls[0]["scopes"] == (
        ("Seoul", "2026-07-03", "high"),
        ("Wellington", "2026-07-03", "high"),
    )
    assert fusion_calls[0]["changed_sources"] == ("icon_global",)
    assert fusion_calls[0]["manifest_snapshot"] is cycle_calls[0]["manifest_snapshot"]
    assert fusion_calls[0]["manifest_snapshot"]["manifest_paths"] == (
        "/tmp/seoul-high.manifest.json",
        "/tmp/wellington-high.manifest.json",
    )
    assert len(anchor_calls) == 1
    assert anchor_calls[0]["required_scopes"] == (
        ("Seoul", "2026-07-03", "high"),
        ("Wellington", "2026-07-03", "high"),
    )
    assert anchor_calls[0]["quota_critical"] is True
    assert 0.0 < anchor_calls[0]["max_wall_clock_seconds"] <= 10.0
    assert "quota_priority" not in anchor_calls[0]
    assert cycle_calls[0]["scopes"] == (
        ("Seoul", "2026-07-03", "high"),
        ("Wellington", "2026-07-03", "high"),
    )
    assert fusion_calls[1] == {"manifest_snapshot": {}}
    assert fusion_calls[1]["manifest_snapshot"] is not cycle_calls[0]["manifest_snapshot"]
    assert markers == {
        (scope, raw_revision) for scope in all_changed_scopes
    }
    assert call_order == [
        "probe",
        "scoped_download",
        "anchor_scope_download",
        "fusion_reseed",
        "cycle_reseed",
        "scoped_download_complete",
        "fusion_reseed",
        "cursor",
    ]
    assert result["reseed_maintenance_status"] == "SOURCE_BROAD_RESEEDS_ASYNC_PENDING"


def test_replacement_availability_pending_callback_runs_broad_fusion_catchup(
    monkeypatch, broad_reseed_join,
) -> None:
    """A callback that outlives the poll cannot suppress the one broad fusion catch-up."""
    import threading

    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as source_clock_probe
    import src.ingest_main as ingest_main

    class _Changed:
        updated_sources = ("icon_global",)

        def as_dict(self):
            return {
                "status": "SOURCE_CLOCK_UPDATES_CHANGED",
                "updated_sources": ["icon_global"],
                "affected_cities": ["Seoul", "Wellington"],
                "error": None,
            }

    callback_started = threading.Event()
    release_callback = threading.Event()
    callback_errors: list[BaseException] = []
    late_callback: threading.Thread | None = None

    def _scoped_path(_cfg, *, on_source_commit=None, **_kwargs):
        nonlocal late_callback
        assert on_source_commit is not None

        def _late_callback() -> None:
            callback_started.set()
            release_callback.wait(timeout=5)
            try:
                on_source_commit(
                    "icon_global",
                    {
                        "written_row_count": 9,
                        "committed_families": (
                            ("Seoul", "2026-07-03", "high"),
                            ("Wellington", "2026-07-03", "high"),
                        ),
                    },
                )
            except BaseException as exc:  # noqa: BLE001 - surfaced in the parent test
                callback_errors.append(exc)

        late_callback = threading.Thread(target=_late_callback, daemon=True)
        late_callback.start()
        assert callback_started.wait(timeout=2)
        return {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "updated_sources": ["icon_global"],
            "source_commit_notifications": 0,
            "source_commit_notifications_pending": 1,
            "source_commit_notification_errors": (),
        }

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        source_clock_probe,
        "probe_openmeteo_source_clock_updates",
        lambda **_kwargs: _Changed(),
    )
    monkeypatch.setattr(
        source_clock_probe,
        "source_clock_scoped_download_cursor_sources",
        lambda _report, **_kwargs: ("icon_global",),
    )
    monkeypatch.setattr(
        source_clock_probe,
        "advance_source_clock_cursor",
        lambda _report, *, sources=None: tuple(sources or ()),
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed",
        _scoped_path,
    )
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: {
            "status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS",
            "written_manifest_count": 1,
            "written_manifests": ["/tmp/seoul-high.manifest.json"],
        },
    )
    fusion_calls: list[dict[str, object]] = []
    cycle_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg, **kwargs: fusion_calls.append(kwargs)
        or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 0},
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg, **kwargs: cycle_calls.append(kwargs)
        or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 0},
    )

    try:
        result = ingest_main._replacement_availability_poll_tick.__wrapped__()

        assert result["reseed_maintenance_status"] == (
            "SOURCE_COMMIT_RESEEDS_DEFERRED"
        )
        assert result["source_clock_cursor_advanced_sources"] == ()
        assert result["source_clock_cursor_deferred_sources"] == ("icon_global",)
        broad_reseed_join()
        assert fusion_calls == [{"manifest_snapshot": {}}]
        assert cycle_calls == []
    finally:
        release_callback.set()
        if late_callback is not None:
            late_callback.join(timeout=5)

    assert late_callback is not None and not late_callback.is_alive()
    assert callback_errors == []
    assert len(fusion_calls) == 2
    assert fusion_calls[1]["scopes"] == (
        ("Seoul", "2026-07-03", "high"),
        ("Wellington", "2026-07-03", "high"),
    )
    assert fusion_calls[1]["changed_sources"] == ("icon_global",)
    assert len(cycle_calls) == 1


def test_pending_callback_broad_trigger_persists_missed_revision_before_cursor(
    monkeypatch,
    tmp_path,
    broad_reseed_join,
) -> None:
    """The real broad trigger durably queues a missed raw revision before cursor advance."""
    import json
    import sqlite3
    import threading
    from datetime import datetime, timezone
    from pathlib import Path
    from types import SimpleNamespace

    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_production as prod
    import src.data.replacement_fusion_upgrade_trigger as fusion_trigger
    import src.data.source_clock_update_probe as source_clock_probe
    import src.ingest_main as ingest_main
    from src.data.replacement_forecast_readiness import SOURCE_ID
    from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

    db = tmp_path / "forecasts.db"
    seed_dir = tmp_path / "seeds"
    raw_dir = tmp_path / "raw"
    carrier = "2026-07-28T06:00:00+00:00"
    newer = "2026-07-28T12:00:00+00:00"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    conn.execute(
        """
        INSERT INTO raw_model_forecasts
            (model, city, target_date, metric, source_cycle_time, source_available_at,
             captured_at, lead_days, forecast_value_c, endpoint)
        VALUES ('icon_global', 'London', '2026-07-30', 'low', ?, ?, ?, 2, 18.0,
                'single_runs')
        """,
        (carrier, carrier, carrier),
    )
    old_raw_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    provenance = {
        "bayes_precision_fusion": {
            "used_models": ["icon_global"],
            "current_value_serving": {
                "icon_global": {"raw_model_forecast_id": old_raw_id},
            },
            "source_clock_one_scheme": {
                "configured_sources": ["icon_global"],
            },
        },
    }
    conn.execute(
        """
        INSERT INTO forecast_posteriors
            (source_id, product_id, data_version, city, target_date,
             temperature_metric, source_cycle_time, source_available_at,
             computed_at, q_json, q_lcb_json, posterior_method,
             dependency_source_run_ids_json, provenance_json,
             runtime_layer, training_allowed)
        VALUES (?, 'pid', 'dv', 'London', '2026-07-30', 'low', ?, ?, ?,
                '{}', '{}', ?, '{}', ?, 'live', 0)
        """,
        (
            SOURCE_ID,
            carrier,
            carrier,
            "2026-07-28T10:00:00+00:00",
            SOURCE_ID,
            json.dumps(provenance),
        ),
    )
    conn.execute(
        """
        INSERT INTO raw_model_forecasts
            (model, city, target_date, metric, source_cycle_time, source_available_at,
             captured_at, lead_days, forecast_value_c, endpoint)
        VALUES ('icon_global', 'London', '2026-07-30', 'low', ?, ?, ?, 2, 17.0,
                'single_runs')
        """,
        (newer, newer, newer),
    )
    new_raw_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.commit()
    conn.close()

    class _Changed:
        updated_sources = ("icon_global",)

        def as_dict(self):
            return {
                "status": "SOURCE_CLOCK_UPDATES_CHANGED",
                "updated_sources": ["icon_global"],
                "affected_cities": ["London", "Seoul"],
                "error": None,
            }

    callback_started = threading.Event()
    release_callback = threading.Event()
    callback_thread: threading.Thread | None = None

    def _scoped_path(_cfg, *, on_source_commit=None, **_kwargs):
        nonlocal callback_thread
        assert on_source_commit is not None

        def _late_callback() -> None:
            callback_started.set()
            release_callback.wait(timeout=5)
            on_source_commit(
                "icon_global",
                {
                    "written_row_count": 2,
                    "committed_families": (
                        ("Seoul", "2026-07-30", "low"),
                    ),
                },
            )

        callback_thread = threading.Thread(target=_late_callback, daemon=True)
        callback_thread.start()
        assert callback_started.wait(timeout=2)
        return {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "source_commit_notifications": 0,
            "source_commit_notifications_pending": 1,
            "source_commit_notification_errors": (),
        }

    def _build_private(_conn, **build_kwargs):
        stage = Path(build_kwargs["seed_file"])
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_text(
            json.dumps(
                {
                    "scope": "London-low",
                    "raw_revision": new_raw_id,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return stage

    cursor_evidence: list[tuple[str, str]] = []

    def _advance_cursor(_report, *, sources=None):
        evidence_conn = sqlite3.connect(db)
        marker = evidence_conn.execute(
            """
            SELECT capturable_family_set, seed_file
            FROM fusion_upgrade_enqueues
            WHERE city = 'London' AND target_date = '2026-07-30' AND metric = 'low'
            """
        ).fetchone()
        evidence_conn.close()
        assert marker is not None
        assert f"|input_revision=icon_global:{new_raw_id}" in marker[0]
        assert Path(marker[1]).is_file()
        seed_payload = json.loads(Path(marker[1]).read_text(encoding="utf-8"))
        assert seed_payload["raw_revision"] == new_raw_id
        assert len(list(seed_dir.glob("*.json"))) == 1
        cursor_evidence.append((marker[0], marker[1]))
        return tuple(sources or ())

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {
            "download_current_targets_enabled": True,
            "forecast_db": db,
            "seed_dir": seed_dir,
            "raw_manifest_dir": raw_dir,
            "seed_limit": 4,
        },
    )
    monkeypatch.setattr(
        prod,
        "_prepared_reseed_manifests",
        lambda *_args, **_kwargs: (
            datetime(2026, 7, 28, 13, 0, tzinfo=timezone.utc),
            (),
        ),
    )
    monkeypatch.setattr(
        target_plan,
        "build_replacement_forecast_current_target_plan",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="READY",
            reason_codes=(),
            rows=(
                SimpleNamespace(
                    city="London",
                    target_date="2026-07-30",
                    temperature_metric="low",
                    day0_observed_extreme_required=False,
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        fusion_trigger,
        "_build_and_write_upgrade_seed",
        _build_private,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        source_clock_probe,
        "probe_openmeteo_source_clock_updates",
        lambda **_kwargs: _Changed(),
    )
    monkeypatch.setattr(
        source_clock_probe,
        "source_clock_scoped_download_cursor_sources",
        lambda _report, **_kwargs: ("icon_global",),
    )
    monkeypatch.setattr(
        source_clock_probe,
        "advance_source_clock_cursor",
        _advance_cursor,
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed",
        _scoped_path,
    )
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: {
            "status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS",
            "written_manifest_count": 0,
        },
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda *_args, **_kwargs: {
            "status": "CYCLE_ADVANCE_TRIGGER",
            "seeds_enqueued": 0,
        },
    )

    try:
        result = ingest_main._replacement_availability_poll_tick.__wrapped__()
        broad_reseed_join()
        assert cursor_evidence == [], "the pending callback still holds the cursor"
    finally:
        release_callback.set()
        if callback_thread is not None:
            callback_thread.join(timeout=5)

    assert result["reseed_maintenance_status"] == "SOURCE_COMMIT_RESEEDS_DEFERRED"
    assert "broad_fusion_upgrade_seeds_enqueued" not in result
    assert result["source_clock_cursor_advanced_sources"] == ()
    assert result["source_clock_cursor_deferred_sources"] == ("icon_global",)
    # The late completion proves its receipt; _advance_cursor has asserted the
    # missed revision was durably queued before this single advance.
    assert len(cursor_evidence) == 1
    assert callback_thread is not None and not callback_thread.is_alive()


def test_pending_broad_receipts_preserve_real_producer_limit_one_per_scan(
    monkeypatch, tmp_path, broad_reseed_join,
) -> None:
    """Two raw families need two real trigger calls when the seed limit is one."""
    import json
    import sqlite3
    import threading
    from datetime import datetime, timezone
    from pathlib import Path
    from types import SimpleNamespace

    import src.data.replacement_forecast_current_target_plan as target_plan
    import src.data.replacement_forecast_production as prod
    import src.data.replacement_fusion_upgrade_trigger as fusion_trigger
    import src.data.source_clock_update_probe as probe
    import src.ingest_main as ingest_main
    from src.data.replacement_forecast_readiness import SOURCE_ID
    from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

    db = tmp_path / "forecasts.db"
    seed_dir = tmp_path / "seeds"
    carrier = "2026-07-28T06:00:00+00:00"
    newer = "2026-07-28T12:00:00+00:00"
    conn = sqlite3.connect(db)
    ensure_replacement_forecast_live_schema(conn)
    raw_ids = {}
    for city in ("London", "Paris"):
        conn.execute(
            """INSERT INTO raw_model_forecasts
                (model, city, target_date, metric, source_cycle_time,
                 source_available_at, captured_at, lead_days, forecast_value_c, endpoint)
                VALUES ('icon_global', ?, '2026-07-30', 'low', ?, ?, ?, 2, 18.0, 'single_runs')""",
            (city, carrier, carrier, carrier),
        )
        old_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        provenance = {"bayes_precision_fusion": {
            "used_models": ["icon_global"],
            "current_value_serving": {"icon_global": {"raw_model_forecast_id": old_id}},
            "source_clock_one_scheme": {"configured_sources": ["icon_global"]},
        }}
        conn.execute(
            """INSERT INTO forecast_posteriors
                (source_id, product_id, data_version, city, target_date,
                 temperature_metric, source_cycle_time, source_available_at,
                 computed_at, q_json, q_lcb_json, posterior_method,
                 dependency_source_run_ids_json, provenance_json,
                 runtime_layer, training_allowed)
                VALUES (?, 'pid', 'dv', ?, '2026-07-30', 'low', ?, ?, ?,
                        '{}', '{}', ?, '{}', ?, 'live', 0)""",
            (SOURCE_ID, city, carrier, carrier, "2026-07-28T10:00:00+00:00",
             SOURCE_ID, json.dumps(provenance)),
        )
        conn.execute(
            """INSERT INTO raw_model_forecasts
                (model, city, target_date, metric, source_cycle_time,
                 source_available_at, captured_at, lead_days, forecast_value_c, endpoint)
                VALUES ('icon_global', ?, '2026-07-30', 'low', ?, ?, ?, 2, 17.0, 'single_runs')""",
            (city, newer, newer, newer),
        )
        raw_ids[city] = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.commit()
    conn.close()

    monkeypatch.setattr(target_plan, "build_replacement_forecast_current_target_plan", lambda *_a, **_k: SimpleNamespace(
        status="READY", reason_codes=(), rows=tuple(
            SimpleNamespace(city=city, target_date="2026-07-30", temperature_metric="low",
                            day0_observed_extreme_required=False)
            for city in ("London", "Paris")
        ),
    ))
    monkeypatch.setattr(prod, "_prepared_reseed_manifests", lambda *_a, **_k: (
        datetime(2026, 7, 28, 13, tzinfo=timezone.utc), (),
    ))

    def build_private(_conn, **kwargs):
        stage = Path(kwargs["seed_file"])
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_text(json.dumps({"city": kwargs["city"]}) + "\n", encoding="utf-8")
        return stage

    monkeypatch.setattr(fusion_trigger, "_build_and_write_upgrade_seed", build_private)
    original_fusion = prod._enqueue_fusion_upgrade_reseeds_if_needed
    entered = threading.Event()
    release = threading.Event()
    calls = [0]

    def fusion(cfg, **kwargs):
        calls[0] += 1
        if calls[0] == 1:
            entered.set()
            assert release.wait(timeout=5)
        return original_fusion(cfg, **kwargs)

    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", fusion)
    advanced: list[str] = []
    monkeypatch.setattr(probe, "advance_source_clock_cursor", lambda _payload, *, sources: advanced.extend(sources) or sources)
    cfg = {"forecast_db": db, "seed_dir": seed_dir, "raw_manifest_dir": tmp_path / "raw", "seed_limit": 1}
    payload = {
        "cursor_path": str(tmp_path / "cursor"), "updated_sources": ["icon_global"],
        "cursor_values": {"icon_global": "same-cycle"},
        "cursor_preimage": {"icon_global": None},
    }
    try:
        assert ingest_main._enqueue_broad_reseed_batch(
            cfg, include_cycle_advance=False, source_clock_payload=payload,
            cursor_sources=("icon_global",), download_report={"status": "raw-A"},
        ) == "SOURCE_BROAD_RESEEDS_ASYNC_PENDING"
        assert entered.wait(timeout=2)
        assert ingest_main._enqueue_broad_reseed_batch(
            cfg, include_cycle_advance=False, source_clock_payload=payload,
            cursor_sources=("icon_global",), download_report={"status": "raw-B"},
        ) == "SOURCE_BROAD_RESEEDS_ASYNC_PENDING"
        assert advanced == []
    finally:
        release.set()
        broad_reseed_join()

    evidence = sqlite3.connect(db).execute(
        """SELECT city, capturable_family_set, seed_file FROM fusion_upgrade_enqueues
           WHERE city IN ('London', 'Paris') ORDER BY city"""
    ).fetchall()
    assert calls == [2]
    assert {row[0] for row in evidence} == {"London", "Paris"}
    for city, marker, seed_file in evidence:
        assert f"|input_revision=icon_global:{raw_ids[city]}" in marker
        assert Path(seed_file).is_file()
        assert json.loads(Path(seed_file).read_text(encoding="utf-8"))["city"] == city
    assert advanced == ["icon_global"]


def test_ecmwf_source_clock_captures_anchor_before_single_runs_fanout(monkeypatch) -> None:
    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as source_clock_probe
    import src.ingest_main as ingest_main

    class _Changed:
        updated_sources = ("ecmwf_ifs",)

        def as_dict(self):
            return {
                "status": "SOURCE_CLOCK_UPDATES_CHANGED",
                "updated_sources": ["ecmwf_ifs"],
                "affected_cities": ["Shanghai"],
                "error": None,
            }

    calls: list[str] = []
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    monkeypatch.setattr(
        source_clock_probe,
        "probe_openmeteo_source_clock_updates",
        lambda **_kwargs: calls.append("probe") or _Changed(),
    )
    held_scope = ("Dallas", "2026-08-17", "high")
    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery.held_position_family_priorities",
        lambda: {held_scope: 0},
    )

    def _anchor(_cfg, **kwargs):
        if kwargs.get("quota_critical"):
            calls.append("held_anchor")
            assert kwargs == {
                "max_wall_clock_seconds": 10.0,
                "required_scopes": (held_scope,),
                "quota_critical": True,
            }
            return {
                "status": "CURRENT_TARGET_CRITICAL_SCOPES_ALREADY_COVERED",
                "written_manifest_count": 0,
            }
        calls.append("anchor")
        assert kwargs == {
            "max_wall_clock_seconds": 10.0,
            "quota_priority": True,
        }
        return {
            "status": "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED",
            "written_manifest_count": 2,
        }

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _anchor,
    )

    def _scoped(_cfg, **_kwargs):
        calls.append("scoped_download")
        assert calls == [
            "probe",
            "held_anchor",
            "held_fusion_reseed",
            "held_cycle_reseed",
            "anchor",
            "anchor_fusion_reseed",
            "anchor_cycle_reseed",
            "scoped_download",
        ]
        return {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "written_row_count": 0,
        }

    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed",
        _scoped,
    )
    def _fusion_reseed(_cfg, **kwargs):
        calls.append(
            "held_fusion_reseed" if kwargs.get("scopes") else "anchor_fusion_reseed"
        )
        return {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 1}

    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        _fusion_reseed,
    )

    def _cycle_reseed(_cfg, **kwargs):
        calls.append(
            "held_cycle_reseed" if kwargs.get("scopes") else "anchor_cycle_reseed"
        )
        return {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 1}

    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        _cycle_reseed,
    )
    monkeypatch.setattr(
        source_clock_probe,
        "source_clock_scoped_download_cursor_sources",
        lambda _report, **_kwargs: (),
    )

    result = ingest_main._replacement_availability_poll_tick.__wrapped__()

    assert result["source_clock_anchor_download"] == {
        "status": "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED",
        "fusion_upgrade_status": "FUSION_UPGRADE_TRIGGER",
        "fusion_upgrade_seeds_enqueued": 1,
        "cycle_advance_status": "CYCLE_ADVANCE_TRIGGER",
        "cycle_advance_seeds_enqueued": 1,
    }
    assert result["source_clock_held_anchor_download"] == {
        "status": "CURRENT_TARGET_CRITICAL_SCOPES_ALREADY_COVERED",
        "fusion_upgrade_status": "FUSION_UPGRADE_TRIGGER",
        "fusion_upgrade_seeds_enqueued": 1,
        "cycle_advance_status": "CYCLE_ADVANCE_TRIGGER",
        "cycle_advance_seeds_enqueued": 1,
    }
    assert result["reseed_maintenance_status"] == "SOURCE_ANCHOR_RESEEDS_PUBLISHED"
    assert calls == [
        "probe",
        "held_anchor",
        "held_fusion_reseed",
        "held_cycle_reseed",
        "anchor",
        "anchor_fusion_reseed",
        "anchor_cycle_reseed",
        "scoped_download",
    ]


def test_held_anchor_already_covered_defers_to_db_lookup_not_full_scan(
    monkeypatch,
) -> None:
    """Regression: CRITICAL_SCOPES_ALREADY_COVERED with no held manifests must pass
    manifest_snapshot=None (per-family DB lookup), not {} (forces a full raw_manifest_dir
    tree scan via _prepared_reseed_manifests -- the exact anti-pattern
    src/data/replacement_cycle_advance_trigger.py's own comment reserves for the
    untargeted scopes=None global catch-up plan). A held-anchor receipt with zero
    written manifests has nothing new to reseed from; scanning tens of thousands of
    manifest files to learn that is pure waste on the source-clock's critical path."""
    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as source_clock_probe
    import src.ingest_main as ingest_main

    class _Changed:
        updated_sources = ("ecmwf_ifs",)

        def as_dict(self):
            return {
                "status": "SOURCE_CLOCK_UPDATES_CHANGED",
                "updated_sources": ["ecmwf_ifs"],
                "affected_cities": ["Shanghai"],
                "error": None,
            }

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    monkeypatch.setattr(
        source_clock_probe,
        "probe_openmeteo_source_clock_updates",
        lambda **_kwargs: _Changed(),
    )
    held_scope = ("Dallas", "2026-08-17", "high")
    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery.held_position_family_priorities",
        lambda: {held_scope: 0},
    )

    def _anchor(_cfg, **kwargs):
        if kwargs.get("quota_critical"):
            # No written_manifests key: an already-covered receipt with nothing new.
            return {
                "status": "CURRENT_TARGET_CRITICAL_SCOPES_ALREADY_COVERED",
                "written_manifest_count": 0,
            }
        return {
            "status": "CURRENT_TARGET_RAW_INPUTS_DOWNLOADED",
            "written_manifest_count": 2,
        }

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _anchor,
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed",
        lambda _cfg, **_kwargs: {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "written_row_count": 0,
        },
    )
    held_calls: list[dict[str, object]] = []

    def _fusion_reseed(_cfg, **kwargs):
        if kwargs.get("scopes") == (held_scope,):
            held_calls.append(dict(kwargs))
        return {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 1}

    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        _fusion_reseed,
    )

    def _cycle_reseed(_cfg, **kwargs):
        if kwargs.get("scopes") == (held_scope,):
            held_calls.append(dict(kwargs))
        return {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 1}

    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        _cycle_reseed,
    )
    monkeypatch.setattr(
        source_clock_probe,
        "source_clock_scoped_download_cursor_sources",
        lambda _report, **_kwargs: (),
    )

    ingest_main._replacement_availability_poll_tick.__wrapped__()

    assert len(held_calls) == 2, "expected one fusion-upgrade and one cycle-advance call"
    for kwargs in held_calls:
        assert kwargs.get("manifest_snapshot") is None, (
            "held-anchor call with zero written manifests must omit manifest_snapshot "
            "(or pass None), never {} -- {} is not None and forces "
            "_prepared_reseed_manifests to run the full raw_manifest_dir tree scan"
        )


def test_source_commit_reseed_triggers_share_one_manifest_snapshot(
    monkeypatch,
    tmp_path,
) -> None:
    import src.data.replacement_cycle_advance_trigger as cycle_trigger
    import src.data.replacement_forecast_production as prod
    import src.data.replacement_forecast_seed_discovery as discovery
    import src.data.replacement_fusion_upgrade_trigger as fusion_trigger

    loaded = (object(),)
    load_calls = []
    trigger_calls = []
    monkeypatch.setattr(
        discovery,
        "_load_manifests",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("scoped source commit must not scan manifest inventory")
        ),
    )
    monkeypatch.setattr(
        discovery,
        "_load_manifest_files",
        lambda paths, *, computed_at: load_calls.append((paths, computed_at)) or loaded,
    )
    monkeypatch.setattr(
        fusion_trigger,
        "enqueue_fusion_upgrade_reseeds",
        lambda **kwargs: trigger_calls.append(("fusion", kwargs)) or {},
    )
    monkeypatch.setattr(
        cycle_trigger,
        "enqueue_cycle_advance_reseeds",
        lambda **kwargs: trigger_calls.append(("cycle", kwargs)) or {},
    )
    cfg = {
        "forecast_db": tmp_path / "forecast.db",
        "seed_dir": tmp_path / "seeds",
        "raw_manifest_dir": tmp_path / "raw",
        "limit": 8,
    }
    manifest_path = tmp_path / "anchor.manifest.json"
    snapshot = {"manifest_paths": (str(manifest_path),)}

    prod._enqueue_fusion_upgrade_reseeds_if_needed(
        cfg,
        scopes=(("Paris", "2026-07-18", "high"),),
        changed_sources=("ecmwf_ifs",),
        manifest_snapshot=snapshot,
    )
    prod._enqueue_cycle_advance_reseeds_if_needed(
        cfg,
        scopes=(("Paris", "2026-07-18", "high"),),
        manifest_snapshot=snapshot,
        causal_baseline_source_run_id="ecmwf-open-data:12z",
    )

    assert len(load_calls) == 1
    assert load_calls[0][0] == (str(manifest_path),)
    assert trigger_calls[0][1]["manifests"] is loaded
    assert trigger_calls[1][1]["manifests"] is loaded
    assert trigger_calls[1][1]["include_missing_posterior"] is True
    assert trigger_calls[1][1]["causal_baseline_source_run_id"] == (
        "ecmwf-open-data:12z"
    )
    assert trigger_calls[0][1]["computed_at"] == trigger_calls[1][1]["computed_at"]


def test_replacement_availability_notification_error_keeps_global_reseed(
    monkeypatch, broad_reseed_join,
) -> None:
    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as source_clock_probe
    import src.ingest_main as ingest_main

    class _Changed:
        updated_sources = ("icon_global",)

        def as_dict(self):
            return {
                "status": "SOURCE_CLOCK_UPDATES_CHANGED",
                "updated_sources": ["icon_global"],
                "affected_cities": ["Munich"],
                "error": None,
            }

    def _scoped_path(_cfg, *, on_source_commit=None, **_kwargs):
        try:
            on_source_commit(
                "icon_global",
                {
                    "written_row_count": 1,
                    "committed_families": (
                        ("Munich", "2026-07-03", "high"),
                    ),
                },
            )
        except RuntimeError as exc:
            errors = (f"icon_global:RuntimeError: {exc}",)
        else:
            errors = ()
        return {
            "status": "SOURCE_CLOCK_SCOPED_BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "updated_sources": ["icon_global"],
            "source_commit_notification_errors": errors,
        }

    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    monkeypatch.setattr(ingest_main, "_REPLACEMENT_BPF_NO_PROGRESS_FAILURES", 3)
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_BPF_NO_PROGRESS_RETRY_NOT_BEFORE_MONOTONIC",
        999.0,
    )
    monkeypatch.setattr(
        source_clock_probe,
        "probe_openmeteo_source_clock_updates",
        lambda **_kwargs: _Changed(),
    )
    monkeypatch.setattr(
        source_clock_probe,
        "advance_source_clock_cursor",
        lambda _report, *, sources=None: tuple(sources or ()),
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed",
        _scoped_path,
    )
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("anchor unavailable")
        ),
    )
    fusion_calls: list[dict[str, object]] = []
    cycle_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg, **kwargs: fusion_calls.append(kwargs) or None,
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg, **kwargs: cycle_calls.append(kwargs) or None,
    )

    result = ingest_main._replacement_availability_poll_tick.__wrapped__()
    broad_reseed_join()

    assert result["source_commit_notification_errors"]
    assert fusion_calls == [{"manifest_snapshot": cycle_calls[0]["manifest_snapshot"]}]
    assert cycle_calls == [{"manifest_snapshot": fusion_calls[0]["manifest_snapshot"]}]
    assert result["reseed_maintenance_status"] == (
        "SOURCE_BROAD_RESEEDS_ASYNC_PENDING"
    )
    assert "reseed_errors" not in result
    assert result["source_clock_cursor_advanced_sources"] == ()
    assert result["source_clock_cursor_deferred_sources"] == ("icon_global",)
    assert ingest_main._REPLACEMENT_BPF_NO_PROGRESS_FAILURES == 0
    assert ingest_main._REPLACEMENT_BPF_NO_PROGRESS_RETRY_NOT_BEFORE_MONOTONIC == 0.0


def test_replacement_availability_cooldown_keeps_metadata_probe_alive_but_suppresses_reseeds(
    monkeypatch, broad_reseed_join,
) -> None:
    import src.data.replacement_forecast_production as prod
    import src.data.source_clock_update_probe as source_clock_probe
    import src.ingest_main as ingest_main

    class _Changed:
        updated_sources = ("icon_global",)

        def as_dict(self):
            return {
                "status": "SOURCE_CLOCK_UPDATES_CHANGED",
                "updated_sources": ["icon_global"],
                "affected_cities": ["Munich"],
                "error": None,
            }

    calls: list[str] = []
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    monkeypatch.setattr(
        source_clock_probe,
        "probe_openmeteo_source_clock_updates",
        lambda **_kwargs: calls.append("probe") or _Changed(),
    )
    monkeypatch.setattr(
        source_clock_probe,
        "source_clock_scoped_download_cursor_sources",
        lambda _report, **_kwargs: (),
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_source_clock_raw_inputs_if_needed",
        lambda *_args, **_kwargs: calls.append("scoped_download")
        or {
            "status": "SOURCE_CLOCK_BPF_SCOPED_QUOTA_COOLDOWN_SKIPPED",
            "cooldown_seconds": 241,
        },
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg, **_kwargs: calls.append("fusion_reseed")
        or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 0},
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg, **_kwargs: calls.append("cycle_reseed")
        or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 0},
    )
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )

    first = ingest_main._replacement_availability_poll_tick.__wrapped__()
    second = ingest_main._replacement_availability_poll_tick.__wrapped__()
    broad_reseed_join()

    assert first["reseed_maintenance_status"] == "SOURCE_BROAD_RESEEDS_ASYNC_PENDING"
    assert second["reseed_maintenance_status"] == (
        "RESEED_MAINTENANCE_NOT_DUE"
    )
    assert "fusion_upgrade_status" not in second
    assert [call for call in calls if call in {"probe", "scoped_download"}] == [
        "probe", "scoped_download", "probe", "scoped_download",
    ]
    assert [call for call in calls if call.endswith("reseed")] == [
        "fusion_reseed", "cycle_reseed",
    ]
    assert ingest_main._REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC == 341.0


def test_replacement_maintenance_tick_throttles_timeboxed_repair(monkeypatch) -> None:
    """A timeboxed repair defers broad reseeds instead of multiplying the tick budget."""
    import src.ingest_main as ingest_main
    import src.data.replacement_forecast_production as prod
    import src.observability.scheduler_health as scheduler_health

    now = [100.0]
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: now[0])
    monkeypatch.setenv(ingest_main.REPLACEMENT_CURRENT_TARGET_POLL_TIMEOUT_SECONDS_ENV, "1")
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    monkeypatch.setattr(ingest_main, "_all_held_current_target_scopes", lambda **_kw: ())
    monkeypatch.setattr(
        ingest_main, "_replacement_bpf_no_progress_retry_after_seconds", lambda: 0.0
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download.bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    calls: list[float | None] = []
    bpf_budgets: list[float] = []

    def _timeboxed(_cfg, *, max_wall_clock_seconds=None):
        calls.append(max_wall_clock_seconds)
        return {
            "status": "CURRENT_TARGET_RAW_INPUTS_TIMEBOXED_INCOMPLETE",
            "timeboxed_incomplete": True,
            "unattempted_target_count": 2,
            "max_wall_clock_seconds": max_wall_clock_seconds,
        }

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _timeboxed,
    )
    def _extras(_cfg, *, max_wall_clock_seconds):
        bpf_budgets.append(max_wall_clock_seconds)
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_NO_TARGETS",
        }

    monkeypatch.setattr(
        prod, "_download_bayes_precision_fusion_extra_raw_inputs_if_needed", _extras,
    )
    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", lambda cfg: None)
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda cfg: {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 3, "advances_detected": 0},
    )
    health: list[dict[str, object]] = []
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda job_name, **kwargs: health.append(
            {"job_name": job_name, **kwargs}
        ),
    )

    result = ingest_main._replacement_maintenance_tick()

    assert result["status"] == "REPLACEMENT_MAINTENANCE_PARTIAL"
    assert result["retryable"] is True
    assert result["maintenance_errors"] == (
        "current_target:CURRENT_TARGET_RAW_INPUTS_TIMEBOXED_INCOMPLETE",
    )
    assert result["current_target_download"]["status"] == "CURRENT_TARGET_RAW_INPUTS_TIMEBOXED_INCOMPLETE"
    assert result["current_target_download"]["timeboxed_incomplete"] is True
    assert result["current_target_download"]["unattempted_target_count"] == 2
    assert result["reseed_maintenance_status"] == (
        "REPLACEMENT_MAINTENANCE_RESEEDS_DEFERRED_DEADLINE"
    )
    assert "cycle_advance_seeds_enqueued" not in result
    assert health[-1] == {
        "job_name": "ingest_replacement_maintenance",
        "failed": True,
        "reason": "replacement_maintenance_partial",
    }

    second = ingest_main._replacement_maintenance_tick()
    assert second["status"] == "REPLACEMENT_MAINTENANCE_NOT_DUE"
    assert bpf_budgets == [0.5]
    assert calls == [1.0]


def test_replacement_maintenance_uses_one_parent_deadline(monkeypatch) -> None:
    """BPF's reserved slice and broad anchor share one parent deadline."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    now = [100.0]
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: now[0])
    monkeypatch.setenv(
        ingest_main.REPLACEMENT_CURRENT_TARGET_POLL_TIMEOUT_SECONDS_ENV,
        "10",
    )
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    budgets: list[tuple[str, float]] = []

    def _current(_cfg, *, max_wall_clock_seconds):
        budgets.append(("current", max_wall_clock_seconds))
        now[0] += 7.0
        return {"status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"}

    def _extras(_cfg, *, max_wall_clock_seconds):
        budgets.append(("extras", max_wall_clock_seconds))
        now[0] += max_wall_clock_seconds
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TIMEBOXED_INCOMPLETE",
            "timeboxed_incomplete": True,
            "written_row_count": 2,
            "committed_families": (
                ("Shanghai", "2026-08-12", "high"),
                ("Munich", "2026-08-13", "high"),
            ),
        }

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _current,
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        _extras,
    )
    reseeds: list[tuple[str, object, object]] = []
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg, *, scopes=None, limit=None: (
            reseeds.append(("fusion", scopes, limit))
            or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 2}
        ),
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg, *, scopes=None, limit=None: (
            reseeds.append(("cycle", scopes, limit))
            or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 2}
        ),
    )

    result = ingest_main._replacement_maintenance_tick.__wrapped__()

    assert budgets == [("extras", 5.0), ("current", 5.0)]
    scopes = (
        ("Munich", "2026-08-13", "high"),
        ("Shanghai", "2026-08-12", "high"),
    )
    assert reseeds == [("fusion", scopes, 2), ("cycle", scopes, 2)]
    assert result["status"] == "REPLACEMENT_MAINTENANCE_PARTIAL"
    assert result["reseed_maintenance_status"] == (
        "REPLACEMENT_MAINTENANCE_COMMITTED_RESEEDS_PUBLISHED"
    )
    assert result["committed_family_count"] == 2


def test_replacement_maintenance_reports_stage_costs_including_post_deadline_candidate(
    monkeypatch, caplog,
) -> None:
    import logging

    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    now = [100.0]
    calls: list[str] = []
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        ingest_main, "_replacement_current_target_poll_timeout_seconds",
        lambda _poll: 20.0,
    )
    monkeypatch.setattr(ingest_main, "_replacement_maintenance_due", lambda: True)
    monkeypatch.setattr(ingest_main, "_all_held_current_target_scopes", lambda **_kw: ())
    monkeypatch.setattr(
        ingest_main, "_replacement_bpf_no_progress_retry_after_seconds", lambda: 0.0,
    )
    monkeypatch.setattr(
        ingest_main, "_record_replacement_bpf_maintenance_progress", lambda _report: None,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download.bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod, "_replacement_forecast_live_materialization_queue_config", lambda: {},
    )

    def bpf(_cfg, *, max_wall_clock_seconds):
        calls.append("bpf")
        assert max_wall_clock_seconds == 8.0
        now[0] += 4.0
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TIMEBOXED_INCOMPLETE",
            "timeboxed_incomplete": True,
            "committed_families": (("Dallas", "2026-09-24", "high"),),
        }

    def broad(_cfg, *, max_wall_clock_seconds):
        calls.append("broad")
        assert max_wall_clock_seconds == 16.0
        now[0] += 6.0
        return {"status": "CURRENT_TARGET_RAW_INPUTS_TIMEBOXED_INCOMPLETE", "timeboxed_incomplete": True}

    def reseed(_cfg, *, scopes, limit):
        calls.append("reseed")
        assert scopes == (("Dallas", "2026-09-24", "high"),)
        assert limit == 1
        now[0] += 1.0
        return {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 0}

    def candidate(_cfg):
        calls.append("candidate")
        now[0] += 30.0
        return {"status": "BAYES_PRECISION_FUSION_EXTRA_NO_TARGETS"}

    monkeypatch.setattr(prod, "_download_bayes_precision_fusion_extra_raw_inputs_if_needed", bpf)
    monkeypatch.setattr(prod, "_download_replacement_forecast_current_targets_if_needed", broad)
    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", reseed)
    monkeypatch.setattr(prod, "_enqueue_cycle_advance_reseeds_if_needed", reseed)
    monkeypatch.setattr(prod, "_download_bayes_precision_fusion_candidate_accrual_if_needed", candidate)

    with caplog.at_level(logging.INFO, logger="zeus.ingest"):
        report = ingest_main._replacement_maintenance_tick.__wrapped__()

    assert calls == ["bpf", "broad", "reseed", "reseed", "candidate"]
    assert report["status"] == "REPLACEMENT_MAINTENANCE_PARTIAL"
    stages = report["stage_timings"]
    assert stages["bpf"] == {"elapsed_seconds": 4.0, "remaining_seconds": 16.0}
    assert stages["broad"] == {"elapsed_seconds": 6.0, "remaining_seconds": 10.0}
    assert stages["committed_reseed"] == {"elapsed_seconds": 2.0, "remaining_seconds": 8.0}
    assert stages["candidate"] == {"elapsed_seconds": 30.0, "remaining_seconds": 0.0}
    assert stages["held"]["remaining_seconds"] == 20.0
    assert "replacement maintenance stage candidate begin remaining_seconds=8.000" in caplog.text
    assert "replacement maintenance stage candidate end elapsed_seconds=30.000" in caplog.text


def test_replacement_maintenance_reserves_held_probability_repair_budget(
    monkeypatch,
) -> None:
    """Stalled anchor partitions cannot consume the held-q repair budget."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    day0_scope = ("Mexico City", "2026-08-18", "high")
    future_scope = ("Busan", "2026-08-19", "high")
    now = [100.0]
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        ingest_main,
        "_replacement_current_target_poll_timeout_seconds",
        lambda _poll_seconds: 20.0,
    )
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_BPF_NO_PROGRESS_RETRY_NOT_BEFORE_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        ingest_main,
        "_all_held_current_target_scopes",
        lambda **_kwargs: (day0_scope, future_scope),
    )
    monkeypatch.setattr(
        ingest_main,
        "_held_day0_current_target_scopes",
        lambda scopes, **_kwargs: tuple(scope for scope in scopes if scope == day0_scope),
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    budgets: list[tuple[str, float]] = []

    def _anchors(_cfg, *, max_wall_clock_seconds, **_kwargs):
        budgets.append(("anchor", max_wall_clock_seconds))
        now[0] += max_wall_clock_seconds
        return {
            "status": "CURRENT_TARGET_RAW_INPUTS_TIMEBOXED_INCOMPLETE",
            "timeboxed_incomplete": True,
            "unattempted_target_count": 1,
        }

    def _extras(_cfg, *, max_wall_clock_seconds):
        budgets.append(("bpf", max_wall_clock_seconds))
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "written_row_count": 2,
        }

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _anchors,
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        _extras,
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda *_args, **_kwargs: {
            "status": "FUSION_UPGRADE_TRIGGER",
            "seeds_enqueued": 0,
        },
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda *_args, **_kwargs: {
            "status": "CYCLE_ADVANCE_TRIGGER",
            "seeds_enqueued": 0,
        },
    )

    result = ingest_main._replacement_maintenance_tick.__wrapped__()

    assert budgets == [
        ("anchor", 6.0),
        ("anchor", 6.0),
        ("bpf", pytest.approx(8.0)),
        ("anchor", 8.0),
    ]
    assert result["bayes_precision_fusion_extra_status"] == (
        "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    )
    assert result["bayes_precision_fusion_extra_rows_written"] == 2


@pytest.mark.parametrize("held", (False, True))
def test_maintenance_preflight_deadline_preserves_bpf_on_every_tick(
    monkeypatch, tmp_path, held,
) -> None:
    """A slow real anchor wrapper cannot starve active BPF in either market lane."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    now = [100.0]
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        ingest_main, "_replacement_current_target_poll_timeout_seconds", lambda _poll: 20.0
    )
    monkeypatch.setattr(ingest_main, "_replacement_maintenance_due", lambda: True)
    monkeypatch.setattr(
        ingest_main, "_replacement_bpf_no_progress_retry_after_seconds", lambda: 0.0
    )
    monkeypatch.setattr(
        ingest_main, "_record_replacement_bpf_maintenance_progress", lambda _report: None
    )
    day0 = ("Amsterdam", "2026-09-23", "high")
    ordinary = ("Amsterdam", "2026-09-24", "low")
    monkeypatch.setattr(
        ingest_main, "_all_held_current_target_scopes",
        lambda **_kwargs: (day0, ordinary) if held else (),
    )
    monkeypatch.setattr(
        ingest_main, "_held_day0_current_target_scopes",
        lambda _scopes, **_kwargs: (day0,) if held else (),
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download.bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod, "_replacement_forecast_live_materialization_queue_config",
        lambda: {"forecast_db": tmp_path / "unused.db", "raw_manifest_dir": tmp_path},
    )
    probes: list[float] = []

    def _slow_probe(*, deadline_monotonic):
        probes.append(deadline_monotonic)
        now[0] = deadline_monotonic
        raise TimeoutError("probe reached its lane deadline")

    monkeypatch.setattr(prod, "_probe_resolved_available_cycle", _slow_probe)
    bpf_budgets: list[float] = []
    monkeypatch.setattr(
        prod, "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        lambda _cfg, *, max_wall_clock_seconds: (
            bpf_budgets.append(max_wall_clock_seconds)
            or {"status": "BAYES_PRECISION_FUSION_EXTRA_NO_TARGETS"}
        ),
    )
    monkeypatch.setattr(
        prod, "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda *_args, **_kwargs: {"status": "NO_CHANGE"},
    )
    monkeypatch.setattr(
        prod, "_enqueue_cycle_advance_reseeds_if_needed",
        lambda *_args, **_kwargs: {"status": "NO_CHANGE"},
    )
    monkeypatch.setattr(
        prod, "_download_bayes_precision_fusion_candidate_accrual_if_needed",
        lambda *_args, **_kwargs: {"status": "NO_TARGETS"},
    )

    for _ in range(10):
        ingest_main._replacement_maintenance_tick.__wrapped__()
        now[0] += 40.0

    assert len(bpf_budgets) == 10
    assert all(budget == pytest.approx(8.0) for budget in bpf_budgets)
    assert len(probes) == (30 if held else 10)


def test_held_scope_discovery_interrupts_real_sqlite_query(monkeypatch, tmp_path) -> None:
    import sqlite3
    import time

    from src.data import replacement_forecast_seed_discovery as discovery

    trade_db = tmp_path / "slow-held.db"
    with sqlite3.connect(trade_db) as conn:
        conn.execute(
            "CREATE VIEW position_current AS WITH RECURSIVE seq(n) AS "
            "(SELECT 1 UNION ALL SELECT n+1 FROM seq WHERE n<1000000) "
            "SELECT 'Amsterdam' AS city, '2026-09-25' AS target_date, "
            "'high' AS temperature_metric, 'active' AS phase FROM seq"
        )
    monkeypatch.setattr(discovery, "_zeus_trade_db_path", lambda: trade_db)

    with pytest.raises(TimeoutError, match="held-family discovery deadline expired"):
        discovery.held_position_family_priorities(
            deadline_monotonic=time.monotonic() + 0.02
        )


def test_held_quota_revalidation_uses_child_deadline_before_bpf(
    monkeypatch, tmp_path,
) -> None:
    from datetime import datetime, timezone

    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    now = [100.0]
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        ingest_main, "_replacement_current_target_poll_timeout_seconds", lambda _poll: 20.0
    )
    monkeypatch.setattr(ingest_main, "_replacement_maintenance_due", lambda: True)
    monkeypatch.setattr(
        ingest_main, "_replacement_bpf_no_progress_retry_after_seconds", lambda: 0.0
    )
    monkeypatch.setattr(
        ingest_main, "_record_replacement_bpf_maintenance_progress", lambda _report: None
    )
    scopes = (
        ("Amsterdam", "2026-09-23", "high"),
        ("Amsterdam", "2026-09-24", "low"),
    )
    monkeypatch.setattr(
        ingest_main, "_all_held_current_target_scopes", lambda **_kwargs: scopes
    )
    monkeypatch.setattr(
        ingest_main, "_held_day0_current_target_scopes",
        lambda _scopes, **_kwargs: scopes[:1],
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download.bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod, "_replacement_forecast_live_materialization_queue_config",
        lambda: {"forecast_db": tmp_path / "unused.db", "raw_manifest_dir": tmp_path},
    )
    cycle = datetime(2026, 9, 23, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(prod, "_probe_resolved_available_cycle", lambda **_kwargs: cycle)
    monkeypatch.setattr(
        prod, "_max_downloaded_current_target_cycle", lambda *_args, **_kwargs: None
    )
    revalidations: list[float] = []

    def _slow_held(*, deadline_monotonic):
        revalidations.append(deadline_monotonic)
        now[0] = deadline_monotonic
        raise TimeoutError("bounded canonical held read")

    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery.held_position_family_priorities",
        _slow_held,
    )
    bpf_budgets: list[float] = []
    monkeypatch.setattr(
        prod, "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        lambda _cfg, *, max_wall_clock_seconds: (
            bpf_budgets.append(max_wall_clock_seconds)
            or {"status": "BAYES_PRECISION_FUSION_EXTRA_NO_TARGETS"}
        ),
    )
    monkeypatch.setattr(
        prod, "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda *_args, **_kwargs: {"status": "NO_CHANGE"},
    )
    monkeypatch.setattr(
        prod, "_enqueue_cycle_advance_reseeds_if_needed",
        lambda *_args, **_kwargs: {"status": "NO_CHANGE"},
    )
    monkeypatch.setattr(
        prod, "_download_bayes_precision_fusion_candidate_accrual_if_needed",
        lambda *_args, **_kwargs: {"status": "NO_TARGETS"},
    )

    ingest_main._replacement_maintenance_tick.__wrapped__()
    assert revalidations == [106.0, 112.0]
    assert bpf_budgets == [8.0]


def test_anchor_acquisition_never_enters_slow_readiness_manifest_plan(
    monkeypatch, tmp_path,
) -> None:
    from datetime import datetime, timedelta, timezone
    import sqlite3

    import scripts.download_replacement_forecast_current_targets as downloader
    import src.data.replacement_forecast_current_target_plan as plan_mod
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main
    forecast_db = tmp_path / "forecast.db"
    with sqlite3.connect(forecast_db) as conn:
        conn.execute(
            "CREATE TABLE market_events(city TEXT,target_date TEXT,"
            "temperature_metric TEXT,token_id TEXT,range_label TEXT)"
        )
        conn.execute(
            "CREATE TABLE forecast_posteriors(city TEXT,target_date TEXT,"
            "temperature_metric TEXT,source_id TEXT,data_version TEXT,"
            "training_allowed INTEGER,runtime_layer TEXT,q_lcb_json TEXT)"
        )
        conn.execute(
            "CREATE TABLE readiness_state(strategy_key TEXT,provenance_json TEXT,"
            "status TEXT,expires_at TEXT,dependency_json TEXT)"
        )
        conn.execute(
            "CREATE TABLE raw_forecast_artifacts(source_id TEXT,data_version TEXT,"
            "artifact_path TEXT,product_id TEXT,sha256 TEXT,byte_size INTEGER,"
            "artifact_metadata_json TEXT,source_cycle_time TEXT)"
        )
        target_date = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
        conn.executemany(
            "INSERT INTO market_events VALUES(?,?,?,?,?)",
            [
                (city, target_date, "high", "token", "range")
                for city in ("Amsterdam", "London", "Paris")
            ],
        )
    now = [100.0]
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        ingest_main, "_replacement_current_target_poll_timeout_seconds", lambda _poll: 20.0
    )
    monkeypatch.setattr(ingest_main, "_replacement_maintenance_due", lambda: True)
    monkeypatch.setattr(ingest_main, "_all_held_current_target_scopes", lambda **_kwargs: ())
    monkeypatch.setattr(
        ingest_main, "_replacement_bpf_no_progress_retry_after_seconds", lambda: 0.0
    )
    monkeypatch.setattr(
        ingest_main, "_record_replacement_bpf_maintenance_progress", lambda _report: None
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download.bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod, "_replacement_forecast_live_materialization_queue_config",
        lambda: {"forecast_db": forecast_db, "raw_manifest_dir": tmp_path},
    )
    cycle = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(prod, "_probe_resolved_available_cycle", lambda **_kwargs: cycle)
    monkeypatch.setattr(
        prod, "_max_downloaded_current_target_cycle", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        plan_mod,
        "build_replacement_forecast_current_target_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("readiness plan must not run during raw acquisition")
        ),
    )
    monkeypatch.setattr(
        plan_mod, "prime_frozen_replacement_artifact_hwm",
        lambda *_args, **_kwargs: lambda: None,
    )
    manifest_scopes: list[str] = []

    def _slow_manifest(_manifests, *, target_date, **_kwargs):
        manifest_scopes.append(target_date)
        now[0] = 112.0
        return 0, None, None

    monkeypatch.setattr(plan_mod, "_openmeteo_manifest_coverage", _slow_manifest)
    writes: list[object] = []
    monkeypatch.setattr(
        downloader,
        "download_current_target_openmeteo_inputs",
        lambda **kwargs: writes.append(kwargs) or {"status": "DOWNLOADED"},
    )
    bpf_budgets: list[float] = []
    monkeypatch.setattr(
        prod, "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        lambda _cfg, *, max_wall_clock_seconds: (
            bpf_budgets.append(max_wall_clock_seconds)
            or {"status": "BAYES_PRECISION_FUSION_EXTRA_NO_TARGETS"}
        ),
    )
    monkeypatch.setattr(
        prod, "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda *_args, **_kwargs: {"status": "NO_CHANGE"},
    )
    monkeypatch.setattr(
        prod, "_enqueue_cycle_advance_reseeds_if_needed",
        lambda *_args, **_kwargs: {"status": "NO_CHANGE"},
    )
    monkeypatch.setattr(
        prod, "_download_bayes_precision_fusion_candidate_accrual_if_needed",
        lambda *_args, **_kwargs: {"status": "NO_TARGETS"},
    )

    report = ingest_main._replacement_maintenance_tick.__wrapped__()
    assert manifest_scopes == []
    assert report["current_target_download"]["status"] == "DOWNLOADED"
    assert len(writes) == 1
    assert writes[0]["required_scopes"] == tuple(
        (city, target_date, "high") for city in ("Amsterdam", "London", "Paris")
    )
    assert bpf_budgets == [8.0]


def test_attempted_bpf_transport_without_commit_does_not_wake_source(
    monkeypatch,
) -> None:
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    monkeypatch.setattr(ingest_main, "_replacement_maintenance_due", lambda: True)
    monkeypatch.setattr(ingest_main, "_all_held_current_target_scopes", lambda **_kwargs: ())
    monkeypatch.setattr(
        ingest_main, "_replacement_bpf_no_progress_retry_after_seconds", lambda: 0.0
    )
    monkeypatch.setattr(
        ingest_main, "_record_replacement_bpf_maintenance_progress", lambda _report: None
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download.bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod, "_replacement_forecast_live_materialization_queue_config", lambda: {}
    )
    monkeypatch.setattr(
        prod, "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: {"status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"},
    )
    monkeypatch.setattr(
        prod, "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        lambda *_args, **_kwargs: {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
            "attempted_target_group_count": 1,
            "written_row_count": 0,
            "committed_families": (),
        },
    )
    scoped_wakes: list[object] = []

    def _reseed(_cfg, **kwargs):
        if kwargs.get("scopes"):
            scoped_wakes.append(kwargs["scopes"])
        return {"status": "NO_CHANGE", "seeds_enqueued": 0}

    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", _reseed)
    monkeypatch.setattr(prod, "_enqueue_cycle_advance_reseeds_if_needed", _reseed)
    monkeypatch.setattr(
        prod, "_download_bayes_precision_fusion_candidate_accrual_if_needed",
        lambda *_args, **_kwargs: {"status": "NO_TARGETS"},
    )

    report = ingest_main._replacement_maintenance_tick.__wrapped__()
    assert report["bayes_precision_fusion_extra_rows_written"] == 0
    assert "committed_family_count" not in report
    assert scoped_wakes == []


def test_replacement_maintenance_does_not_publish_failsoft_committed_reseed(
    monkeypatch,
) -> None:
    """A trigger error remains retryable; it is never evidence that q was reseeded."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    now = [100.0]
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: now[0])
    monkeypatch.setenv(
        ingest_main.REPLACEMENT_CURRENT_TARGET_POLL_TIMEOUT_SECONDS_ENV,
        "10",
    )
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )

    def _current(_cfg, *, max_wall_clock_seconds):
        now[0] += 7.0
        return {"status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"}

    def _extras(_cfg, *, max_wall_clock_seconds):
        now[0] += max_wall_clock_seconds
        return {
            "status": "BAYES_PRECISION_FUSION_EXTRA_TIMEBOXED_INCOMPLETE",
            "timeboxed_incomplete": True,
            "written_row_count": 1,
            "committed_families": (("Shanghai", "2026-08-12", "high"),),
        }

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _current,
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        _extras,
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda *_args, **_kwargs: {
            "status": "FUSION_UPGRADE_TRIGGER_FAILSOFT_SKIPPED",
            "error": "seed writer unavailable",
        },
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda *_args, **_kwargs: {
            "status": "CYCLE_ADVANCE_TRIGGER",
            "seeds_enqueued": 1,
        },
    )

    result = ingest_main._replacement_maintenance_tick.__wrapped__()

    assert result["status"] == "REPLACEMENT_MAINTENANCE_PARTIAL"
    assert result["reseed_maintenance_status"] == (
        "REPLACEMENT_MAINTENANCE_RESEEDS_DEFERRED_DEADLINE"
    )
    assert result["committed_fusion_upgrade_status"] == (
        "FUSION_UPGRADE_TRIGGER_FAILSOFT_SKIPPED"
    )
    assert result["committed_cycle_advance_status"] == "CYCLE_ADVANCE_TRIGGER"
    assert "committed_fusion_upgrade:FUSION_UPGRADE_TRIGGER_FAILSOFT_SKIPPED" in (
        result["maintenance_errors"]
    )


def test_replacement_maintenance_broad_none_is_retryable(monkeypatch) -> None:
    """Missing broad trigger configuration cannot disappear as a completed repair."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: {
            "status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"
        },
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        lambda *_args, **_kwargs: {
            "status": "BAYES_PRECISION_FUSION_EXTRA_NO_TARGETS"
        },
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda *_args, **_kwargs: {
            "status": "CYCLE_ADVANCE_TRIGGER",
            "seeds_enqueued": 0,
        },
    )

    result = ingest_main._replacement_maintenance_tick.__wrapped__()

    assert result["status"] == "REPLACEMENT_MAINTENANCE_PARTIAL"
    assert result["maintenance_errors"] == (
        "fusion_upgrade:RESEED_CONFIGURATION_UNAVAILABLE",
    )
    assert result["cycle_advance_status"] == "CYCLE_ADVANCE_TRIGGER"


def test_replacement_maintenance_quota_cooldown_is_partial_but_reseeds(
    monkeypatch,
) -> None:
    """Global download cooldown defers transport, not independent durable reseed drains."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main
    import src.observability.scheduler_health as scheduler_health

    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 120,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )

    def _unexpected_download(*_args, **_kwargs):
        raise AssertionError("quota cooldown must defer download transport")

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _unexpected_download,
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        _unexpected_download,
    )
    reseeds: list[str] = []
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg: reseeds.append("fusion")
        or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 1},
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg: reseeds.append("cycle")
        or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 2},
    )
    health: list[dict[str, object]] = []
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda job_name, **kwargs: health.append(
            {"job_name": job_name, **kwargs}
        ),
    )

    result = ingest_main._replacement_maintenance_tick()

    assert result["status"] == "REPLACEMENT_MAINTENANCE_PARTIAL"
    assert result["retryable"] is True
    assert result["cooldown_seconds"] == 120
    assert result["maintenance_errors"] == (
        "bayes_precision_fusion_extra:"
        "BAYES_PRECISION_FUSION_EXTRA_QUOTA_COOLDOWN_SKIPPED",
    )
    assert result["fusion_upgrade_seeds_enqueued"] == 1
    assert result["cycle_advance_seeds_enqueued"] == 2
    assert reseeds == ["fusion", "cycle"]
    assert health[-1] == {
        "job_name": "ingest_replacement_maintenance",
        "failed": True,
        "reason": "replacement_maintenance_partial",
    }


def test_held_current_target_repair_covers_day0_and_future_exposure(
    monkeypatch,
) -> None:
    import src.ingest_main as ingest_main

    day0_scope = ("NYC", "2026-08-17", "low")
    future_scope = ("Busan", "2026-08-19", "high")
    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery.held_position_family_priorities",
        lambda: {day0_scope: 0, future_scope: 1},
    )

    assert ingest_main._all_held_current_target_scopes() == tuple(
        sorted((day0_scope, future_scope))
    )


@pytest.mark.parametrize(
    ("held_status", "written_manifest_count"),
    (
        ("CURRENT_TARGET_CRITICAL_SCOPES_ALREADY_COVERED", 0),
        ("CURRENT_TARGET_RAW_INPUTS_DOWNLOADED", 1),
    ),
)
def test_replacement_maintenance_repairs_held_anchor_during_broad_cooldown(
    monkeypatch, held_status, written_manifest_count,
) -> None:
    """Held current-q repair cannot wait for another source-clock transition."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    held_scope = ("NYC", "2026-08-17", "low")
    past_scope = ("Hong Kong", "2026-08-15", "high")
    now = [100.0]
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        ingest_main,
        "_all_held_current_target_scopes",
        lambda **_kwargs: (past_scope, held_scope),
    )
    monkeypatch.setattr(
        ingest_main,
        "_held_day0_current_target_scopes",
        lambda scopes, **_kwargs: scopes,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 120,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    downloads: list[dict[str, object]] = []

    def _download(_cfg, **kwargs):
        downloads.append(kwargs)
        return {
            "status": held_status,
            "written_manifest_count": written_manifest_count,
            "required_scope_count": 2,
            "structurally_unservable_scopes": [list(past_scope)],
        }

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _download,
    )
    reseeds: list[tuple[str, object, object]] = []
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg, *, scopes=None, limit=None: (
            reseeds.append(("fusion", scopes, limit))
            or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 1}
        ),
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg, *, scopes=None, limit=None: (
            reseeds.append(("cycle", scopes, limit))
            or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 1}
        ),
    )

    result = ingest_main._replacement_maintenance_tick.__wrapped__()
    now[0] = 160.0
    second = ingest_main._replacement_maintenance_tick.__wrapped__()

    assert len(downloads) == 2
    assert all(
        call["required_scopes"] == (past_scope, held_scope)
        for call in downloads
    )
    assert all(call["quota_critical"] is True for call in downloads)
    assert all(0 < call["max_wall_clock_seconds"] <= 10.0 for call in downloads)
    assert result["held_current_target_download"] == {
        "status": held_status,
    }
    assert reseeds[:2] == [
        ("fusion", (held_scope,), 1),
        ("cycle", (held_scope,), 1),
    ]
    assert result["maintenance_errors"] == (
        "bayes_precision_fusion_extra:"
        "BAYES_PRECISION_FUSION_EXTRA_QUOTA_COOLDOWN_SKIPPED",
    )
    assert second["broad_maintenance_status"] == "REPLACEMENT_MAINTENANCE_NOT_DUE"
    assert second["reseed_maintenance_status"] == (
        "REPLACEMENT_MAINTENANCE_HELD_RESEEDS_PUBLISHED"
    )
    assert "maintenance_errors" not in second
    assert reseeds[-2:] == [
        ("fusion", (held_scope,), 1),
        ("cycle", (held_scope,), 1),
    ]


@pytest.mark.parametrize(
    ("critical_timeout", "timeout_s"),
    ((False, 120.0), (True, 120.0), (False, 1.0)),
)
def test_replacement_maintenance_partitions_all_held_scopes_by_quota_lane(
    monkeypatch, critical_timeout, timeout_s,
) -> None:
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    day0_scope = ("NYC", "2026-08-17", "low")
    future_scope = ("Busan", "2026-08-19", "high")
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_HELD_PARTITION_FIRST",
        "critical",
    )
    monkeypatch.setattr(
        ingest_main,
        "_replacement_current_target_poll_timeout_seconds",
        lambda _poll_seconds: timeout_s,
    )
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        200.0,
    )
    monkeypatch.setattr(
        ingest_main,
        "_all_held_current_target_scopes",
        lambda **_kwargs: (day0_scope, future_scope),
    )
    monkeypatch.setattr(
        ingest_main,
        "_held_day0_current_target_scopes",
        lambda scopes, **_kwargs: tuple(scope for scope in scopes if scope == day0_scope),
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 120,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    downloads: list[dict[str, object]] = []

    def _download(_cfg, **kwargs):
        downloads.append(kwargs)
        if critical_timeout and kwargs.get("required_scopes") == (day0_scope,):
            raise TimeoutError("critical lane deadline")
        return {
            "status": (
                "CURRENT_TARGET_CRITICAL_SCOPES_ALREADY_COVERED"
                if kwargs.get("quota_critical")
                else "CURRENT_TARGETS_ALREADY_COVERED"
            ),
            "written_manifest_count": 0,
        }

    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        _download,
    )
    reseeds: list[tuple[str, object, object]] = []
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg, *, scopes=None, limit=None: (
            reseeds.append(("fusion", scopes, limit))
            or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": len(scopes or ())}
        ),
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg, *, scopes=None, limit=None: (
            reseeds.append(("cycle", scopes, limit))
            or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": len(scopes or ())}
        ),
    )

    result = ingest_main._replacement_maintenance_tick.__wrapped__()

    lane_budget = min(10.0, timeout_s / 2.0)
    assert downloads == [
        {
            "max_wall_clock_seconds": lane_budget,
            "required_scopes": (day0_scope,),
            "quota_critical": True,
        },
        {
            "max_wall_clock_seconds": lane_budget,
            "required_scopes": (future_scope,),
            "quota_critical": True,
        },
    ]
    reseed_scopes = (
        (future_scope,)
        if critical_timeout
        else tuple(sorted((day0_scope, future_scope)))
    )
    assert reseeds == [
        ("fusion", reseed_scopes, len(reseed_scopes)),
        ("cycle", reseed_scopes, len(reseed_scopes)),
    ]
    assert result["held_current_target_download"]["status"] == (
        "CURRENT_TARGET_DOWNLOAD_TIMEOUT"
        if critical_timeout
        else "CURRENT_TARGET_CRITICAL_SCOPES_ALREADY_COVERED"
    )
    assert result["held_ordinary_current_target_download"]["status"] == (
        "CURRENT_TARGET_CRITICAL_SCOPES_ALREADY_COVERED"
    )
    assert result["broad_maintenance_status"] == "REPLACEMENT_MAINTENANCE_NOT_DUE"
    if critical_timeout:
        assert result["maintenance_errors"] == (
            "held_current_target:CURRENT_TARGET_DOWNLOAD_TIMEOUT",
        )
    else:
        assert "maintenance_errors" not in result


def test_replacement_held_partitions_alternate_first_lane(monkeypatch) -> None:
    """Repeated timeboxes cannot permanently strand the ordinary held partition."""
    import src.ingest_main as ingest_main

    critical_scope = ("NYC", "2026-08-17", "low")
    ordinary_scope = ("Busan", "2026-08-19", "high")
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_HELD_PARTITION_FIRST",
        "critical",
    )

    assert ingest_main._next_replacement_held_partition_order(
        (critical_scope,),
        (ordinary_scope,),
    ) == (
        ("critical", (critical_scope,)),
        ("ordinary", (ordinary_scope,)),
    )
    assert ingest_main._next_replacement_held_partition_order(
        (critical_scope,),
        (ordinary_scope,),
    ) == (
        ("ordinary", (ordinary_scope,)),
        ("critical", (critical_scope,)),
    )


def test_replacement_maintenance_repairs_full_extras_before_reseed(
    monkeypatch,
) -> None:
    """The sole maintenance owner heals missing extras without a source-clock change."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    calls: list[str] = []
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: calls.append("current_targets")
        or {"status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"},
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        lambda *_args, **_kwargs: calls.append("full_extras")
        or {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "written_row_count": 2,
        },
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg: calls.append("fusion_reseed")
        or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 1},
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg: calls.append("cycle_reseed")
        or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 0},
    )

    result = ingest_main._replacement_maintenance_tick.__wrapped__()

    assert calls == [
        "full_extras",
        "current_targets",
        "fusion_reseed",
        "cycle_reseed",
    ]
    assert result["bayes_precision_fusion_extra_status"] == (
        "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    )
    assert result["bayes_precision_fusion_extra_rows_written"] == 2
    assert result["fusion_upgrade_seeds_enqueued"] == 1
    assert "held_current_target_download" not in result
    assert "maintenance_errors" not in result


def test_replacement_maintenance_runs_candidate_accrual_after_committed_reseeds(
    monkeypatch,
) -> None:
    """The scheduled maintenance job, not the telemetry-only production wrapper,
    gives candidate capture a bounded recovery opportunity after every active
    committed-family reaction has been published."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    calls: list[str] = []
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(ingest_main, "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC", 0.0)
    monkeypatch.setattr(ingest_main, "_all_held_current_target_scopes", lambda **_kwargs: ())
    monkeypatch.setattr(ingest_main, "_replacement_bpf_no_progress_retry_after_seconds", lambda: 0)
    monkeypatch.setattr(ingest_main, "_record_replacement_bpf_maintenance_progress", lambda _report: None)
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download.bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"forecast_db": "unused.db", "download_current_targets_enabled": True},
    )
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: calls.append("current")
        or {"status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"},
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        lambda *_args, **_kwargs: calls.append("extras")
        or {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "written_row_count": 1,
            "committed_families": (("Helsinki", "2026-09-21", "high"),),
        },
    )

    def _fusion_reseed(_cfg, *, scopes=None, limit=None):
        calls.append("committed_fusion_reseed" if scopes else "ordinary_fusion_reseed")
        return {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 1}

    def _cycle_reseed(_cfg, *, scopes=None, limit=None):
        calls.append("committed_cycle_reseed" if scopes else "ordinary_cycle_reseed")
        return {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 1}

    monkeypatch.setattr(prod, "_enqueue_fusion_upgrade_reseeds_if_needed", _fusion_reseed)
    monkeypatch.setattr(prod, "_enqueue_cycle_advance_reseeds_if_needed", _cycle_reseed)
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_candidate_accrual_if_needed",
        lambda _cfg: calls.append("candidate")
        or {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "written_row_count": 1,
            "candidate_accrual_only": True,
        },
    )

    result = ingest_main._replacement_maintenance_tick.__wrapped__()

    assert calls[:5] == [
        "extras",
        "current",
        "committed_fusion_reseed",
        "committed_cycle_reseed",
        "candidate",
    ]
    assert result["bayes_precision_fusion_candidate_accrual_status"] == (
        "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
    )
    assert "candidate_accrual_only" not in result


@pytest.mark.parametrize(
    "zero_progress_status",
    (
        "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE",
        "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
    ),
)
def test_replacement_maintenance_backs_off_only_zero_progress_bpf_fanout(
    monkeypatch,
    zero_progress_status,
) -> None:
    """A transient broad fan-out cannot spend quota every minute without new rows."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    now = [100.0]
    monkeypatch.setattr(ingest_main.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(ingest_main, "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC", 0.0)
    monkeypatch.setattr(ingest_main, "_REPLACEMENT_BPF_NO_PROGRESS_FAILURES", 0)
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_BPF_NO_PROGRESS_RETRY_NOT_BEFORE_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    current_calls: list[float] = []
    extras_reports = [
        {
            "status": zero_progress_status,
            "written_row_count": 0,
        },
        {
            "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
            "written_row_count": 2,
        },
    ]
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: current_calls.append(now[0])
        or {"status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"},
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        lambda *_args, **_kwargs: extras_reports.pop(0),
    )
    reseeds: list[str] = []
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg: reseeds.append("fusion") or None,
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg: reseeds.append("cycle") or None,
    )

    first = ingest_main._replacement_maintenance_tick.__wrapped__()
    assert first["bayes_precision_fusion_extra_status"] == zero_progress_status
    assert ingest_main._REPLACEMENT_BPF_NO_PROGRESS_FAILURES == 1

    now[0] = 160.0
    monkeypatch.setattr(ingest_main, "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC", 0.0)
    second = ingest_main._replacement_maintenance_tick.__wrapped__()
    assert second["bayes_precision_fusion_extra_status"] == (
        "BAYES_PRECISION_FUSION_EXTRA_NO_PROGRESS_BACKOFF_SKIPPED"
    )
    assert len(extras_reports) == 1
    assert current_calls == [100.0, 160.0]
    assert reseeds == ["fusion", "cycle", "fusion", "cycle"]

    now[0] = 401.0
    monkeypatch.setattr(ingest_main, "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC", 0.0)
    third = ingest_main._replacement_maintenance_tick.__wrapped__()
    assert third["bayes_precision_fusion_extra_rows_written"] == 2
    assert ingest_main._REPLACEMENT_BPF_NO_PROGRESS_FAILURES == 0
    assert ingest_main._REPLACEMENT_BPF_NO_PROGRESS_RETRY_NOT_BEFORE_MONOTONIC == 0.0


@pytest.mark.parametrize(
    ("lane", "status"),
    (
        ("current_target", "CURRENT_TARGET_DOWNLOAD_TIMEOUT"),
        ("current_target", "CURRENT_TARGET_DOWNLOAD_FAILSOFT"),
        ("current_target", "CURRENT_TARGET_RAW_INPUTS_TRANSPORT_RETRYABLE"),
        ("current_target", "CURRENT_TARGET_DOWNLOAD_INFLIGHT_SKIP"),
        ("current_target", "CYCLE_PROBE_UNRESOLVED_SKIP"),
        ("extras", "BAYES_PRECISION_FUSION_EXTRA_CAPTURE_FAILSOFT_SKIPPED"),
        ("extras", "BAYES_PRECISION_FUSION_EXTRA_TIMEBOXED_INCOMPLETE"),
        ("extras", "BAYES_PRECISION_FUSION_EXTRA_TRANSPORT_RETRYABLE"),
        ("extras", "BAYES_PRECISION_FUSION_EXTRA_QUOTA_COOLDOWN_SKIPPED"),
        ("extras", "BAYES_PRECISION_FUSION_EXTRA_CYCLE_PROBE_UNRESOLVED_SKIP"),
    ),
)
def test_replacement_maintenance_retryable_status_contract_runs_reseeds(
    monkeypatch,
    lane,
    status,
) -> None:
    """Known incomplete inner statuses are explicit PARTIAL, never healthy substring guesses."""
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(ingest_main, "_REPLACEMENT_BPF_NO_PROGRESS_FAILURES", 0)
    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_BPF_NO_PROGRESS_RETRY_NOT_BEFORE_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    current_status = (
        status if lane == "current_target" else "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"
    )
    extras_status = (
        status if lane == "extras" else "BAYES_PRECISION_FUSION_EXTRA_NO_TARGETS"
    )
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: {"status": current_status},
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        lambda *_args, **_kwargs: {"status": extras_status},
    )
    reseeds: list[str] = []
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg: reseeds.append("fusion")
        or {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 1},
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg: reseeds.append("cycle")
        or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 2},
    )

    result = ingest_main._replacement_maintenance_tick.__wrapped__()

    expected_lane = (
        "current_target" if lane == "current_target" else "bayes_precision_fusion_extra"
    )
    assert result["status"] == "REPLACEMENT_MAINTENANCE_PARTIAL"
    assert result["retryable"] is True
    assert result["maintenance_errors"] == (f"{expected_lane}:{status}",)
    assert result["fusion_upgrade_seeds_enqueued"] == 1
    assert result["cycle_advance_seeds_enqueued"] == 2
    assert reseeds == ["fusion", "cycle"]
    assert ingest_main._classify_result(result) == (
        True,
        "replacement_maintenance_partial",
    )


def test_replacement_maintenance_isolates_reseed_failures(monkeypatch) -> None:
    import src.data.replacement_forecast_production as prod
    import src.ingest_main as ingest_main

    monkeypatch.setattr(
        ingest_main,
        "_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC",
        0.0,
    )
    monkeypatch.setattr(
        "src.data.bayes_precision_fusion_download."
        "bayes_precision_fusion_quota_cooldown_seconds",
        lambda: 0,
    )
    monkeypatch.setattr(
        prod,
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"download_current_targets_enabled": True},
    )
    monkeypatch.setattr(
        prod,
        "_download_replacement_forecast_current_targets_if_needed",
        lambda *_args, **_kwargs: {"status": "CURRENT_TARGETS_HAVE_RAW_MANIFESTS"},
    )
    monkeypatch.setattr(
        prod,
        "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
        lambda *_args, **_kwargs: {
            "status": "BAYES_PRECISION_FUSION_EXTRA_NO_TARGETS",
        },
    )
    monkeypatch.setattr(
        prod,
        "_enqueue_fusion_upgrade_reseeds_if_needed",
        lambda _cfg: (_ for _ in ()).throw(RuntimeError("fusion busy")),
    )
    cycle_calls: list[bool] = []
    monkeypatch.setattr(
        prod,
        "_enqueue_cycle_advance_reseeds_if_needed",
        lambda _cfg: cycle_calls.append(True)
        or {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 2},
    )

    result = ingest_main._replacement_maintenance_tick.__wrapped__()

    assert result["status"] == "REPLACEMENT_MAINTENANCE_PARTIAL"
    assert result["cycle_advance_seeds_enqueued"] == 2
    assert cycle_calls == [True]
    assert result["maintenance_errors"][0].startswith("fusion_upgrade:RuntimeError")


def test_replacement_availability_fast_poll_caps_scoped_download_under_cadence(monkeypatch) -> None:
    """The scoped download keeps a useful budget across faster metadata polls."""
    import src.ingest_main as ingest_main

    monkeypatch.setenv(ingest_main.REPLACEMENT_AVAILABILITY_POLL_SECONDS_ENV, "20")
    monkeypatch.delenv(ingest_main.REPLACEMENT_SOURCE_CLOCK_DOWNLOAD_BUDGET_SECONDS_ENV, raising=False)
    assert ingest_main._replacement_source_clock_download_budget_seconds(20) == 45.0

    monkeypatch.setenv(ingest_main.REPLACEMENT_SOURCE_CLOCK_DOWNLOAD_BUDGET_SECONDS_ENV, "999")
    assert ingest_main._replacement_source_clock_download_budget_seconds(20) == 60.0

    monkeypatch.setenv(ingest_main.REPLACEMENT_SOURCE_CLOCK_DOWNLOAD_BUDGET_SECONDS_ENV, "0")
    assert ingest_main._replacement_source_clock_download_budget_seconds(20) == 1.0


def test_build_job_specs_owner_filter() -> None:
    """F9: build_job_specs(owner) must return ONLY that daemon's jobs — otherwise activation
    would cross-schedule both daemons and bypass the OpenData singleton."""
    from src.data.scheduler_adapter import build_job_specs

    ingest = build_job_specs("ingest_main")
    assert ingest and all(s.owner_daemon == "ingest_main" for s in ingest)
    assert not any(s.job_id.startswith("forecast_live_") for s in ingest)

    fl = build_job_specs("forecast_live_daemon")
    assert fl and all(s.owner_daemon == "forecast_live_daemon" for s in fl)
    assert not any(s.job_id.startswith("ingest_") for s in fl)

    assert len(build_job_specs()) == len(ingest) + len(fl)   # None = full inventory


class _FakeScheduler:
    """Captures add_job calls so build_registry_scheduler can be tested without APScheduler."""
    def __init__(self):
        self.jobs = []
    def add_job(self, fn, trigger, *, id, executor, max_instances, coalesce, misfire_grace_time, **kw):
        self.jobs.append({"id": id, "executor": executor, "trigger": trigger,
                          "max_instances": max_instances, "coalesce": coalesce,
                          "misfire_grace_time": misfire_grace_time, "kw": kw})


def _ingest_main_job_defs():
    """Daemon-supplied (callable, trigger, trigger_kwargs) for EXACTLY the registry's ingest_main
    expected set (OpenData owned by ingest_main)."""
    from src.data.scheduler_adapter import expected_registry_job_ids
    expected = expected_registry_job_ids("ingest_main", "ingest_main")
    return {jid: ((lambda: None), "interval", {"minutes": 5}) for jid in expected}


def test_build_registry_scheduler_builds_exact_set_and_routes_executors() -> None:
    """PR #329 review A acceptance: in registry mode the daemon builds its jobs FROM the registry —
    every expected job is added with the registry's executor class (lane), not a hand-coded one,
    and the manual add_job set is fully replaced."""
    from src.data.scheduler_adapter import build_registry_scheduler, executor_class_for
    from src.data.source_job_registry import JOB_REGISTRY

    sched = _FakeScheduler()
    job_defs = _ingest_main_job_defs()
    built = build_registry_scheduler(sched, "ingest_main", job_defs, forecast_live_owner_env="ingest_main")

    assert set(built) == set(job_defs)                       # built exactly the registry set
    assert {j["id"] for j in sched.jobs} == set(job_defs)
    # each job routed to its REGISTRY executor class (lane), and all are valid lanes:
    for j in sched.jobs:
        assert j["executor"] == executor_class_for(JOB_REGISTRY[j["id"]])
        assert j["executor"] in (
            "source_clock_db",
            "hko_source_clock_db",
            "hko_final_source_clock_db",
            "forecast_clock_db",
            "forecast_repair_db",
            "station_forecast_clock_db",
            "oracle_guard_db",
            "observation_db",
            "forecast_source_db",
            "forecast_archive_db",
            "market_topology_db",
            "settlement_db",
            "venue_event_db",
            "backfill_db",
            "derived_db",
            "io",
            "health_io",
            "heartbeat",
        )
        assert j["max_instances"] == 1 and j["coalesce"] is True   # anti-overlap preserved


def test_ingest_main_registry_scheduler_replaces_manual_add_job_when_enabled() -> None:
    """PR #329 review A acceptance (named, integration): the REAL ingest_main spec list drives the
    registry build to EXACTLY the registry's expected set — no live job dropped, none invented —
    and every job lands on its registry executor lane (the manual 2-pool add_job is fully replaced).
    """
    import os

    import src.ingest_main as im
    from src.data.scheduler_adapter import (
        build_registry_scheduler, executor_class_for, expected_registry_job_ids, job_defs_from_specs,
    )
    from src.data.source_job_registry import JOB_REGISTRY

    os.environ.pop("ZEUS_FORECAST_LIVE_OWNER", None)   # ingest_main owns OpenData (default)
    specs = im._ingest_main_job_specs()
    job_defs = job_defs_from_specs(specs)
    expected = expected_registry_job_ids("ingest_main", im._forecast_live_owner())
    assert set(job_defs) == expected, f"spec/registry drift: {set(job_defs) ^ expected}"

    sched = _FakeScheduler()
    built = build_registry_scheduler(sched, "ingest_main", job_defs,
                                     forecast_live_owner_env=im._forecast_live_owner())
    assert set(built) == expected
    # every built job routed to its registry lane (manual executor='fast'/'default' replaced):
    for j in sched.jobs:
        assert j["executor"] == executor_class_for(JOB_REGISTRY[j["id"]])
    by_id = {j["id"]: j for j in sched.jobs}
    assert by_id["ingest_day0_metar_source_clock"]["executor"] == "source_clock_db"
    assert by_id["ingest_k2_hko_tick"]["executor"] == "hko_source_clock_db"
    assert (
        by_id["ingest_k2_hko_daily_final"]["executor"]
        == "hko_final_source_clock_db"
    )
    assert by_id["ingest_replacement_availability_poll"]["executor"] == "forecast_clock_db"
    assert (
        by_id["ingest_station_forecast_source_clock"]["executor"]
        == "station_forecast_clock_db"
    )
    assert by_id["ingest_replacement_maintenance"]["executor"] == "forecast_repair_db"
    assert by_id["ingest_etl_recalibrate"]["executor"] == "derived_db"
    assert "ingest_day0_metar_commit_retry" not in by_id
    assert by_id["ingest_day0_oracle_anomaly"]["executor"] == "oracle_guard_db"
    assert by_id["ingest_harvester_truth_writer"]["executor"] == "settlement_db"
    assert by_id["ingest_market_scan"]["executor"] == "market_topology_db"
    assert by_id["ingest_k2_forecasts_daily"]["executor"] == "forecast_archive_db"
    assert by_id["ingest_k2_obs_fast_tick"]["executor"] == "observation_db"
    assert by_id["ingest_k2_hourly_instants"]["executor"] == "backfill_db"
    assert by_id["ingest_heartbeat"]["executor"] == "heartbeat"
    assert by_id["ingest_status_rollup"]["executor"] == "health_io"
    assert "ingest_uma_resolution_listener" not in by_id
    assert "ingest_calibration_auto_promote" not in by_id


def test_ingest_main_non_owner_excludes_opendata_from_registry_build() -> None:
    """The OpenData singleton holds through the spec list: when ingest_main does NOT own OpenData,
    its spec list (and thus the registry build) drops the 3 OpenData jobs — matching the registry's
    expected set, so the boot assert passes and OpenData is never double-scheduled."""
    import os

    import src.ingest_main as im
    from src.data.scheduler_adapter import expected_registry_job_ids, job_defs_from_specs

    os.environ["ZEUS_FORECAST_LIVE_OWNER"] = "forecast_live"
    try:
        job_defs = job_defs_from_specs(im._ingest_main_job_specs())
        assert "ingest_opendata_daily_mx2t6" not in job_defs   # OpenData not owned -> not built
        assert job_defs.keys() == expected_registry_job_ids("ingest_main", "forecast_live")
    finally:
        os.environ.pop("ZEUS_FORECAST_LIVE_OWNER", None)


def test_build_registry_scheduler_boot_assert_catches_drift() -> None:
    """The fail-fast boot assert: a daemon whose job_defs miss a registry job (or add an unknown
    one) must REFUSE to boot rather than run a schedule that diverges from the registry."""
    import pytest

    from src.data.scheduler_adapter import build_registry_scheduler, expected_registry_job_ids

    expected = expected_registry_job_ids("ingest_main", "ingest_main")
    # drop one expected job -> mismatch -> raise
    short = {jid: ((lambda: None), "interval", {"minutes": 5}) for jid in list(expected)[1:]}
    with pytest.raises(RuntimeError, match="job-set mismatch"):
        build_registry_scheduler(_FakeScheduler(), "ingest_main", short, forecast_live_owner_env="ingest_main")
    # add an unknown job -> mismatch -> raise
    extra = {jid: ((lambda: None), "interval", {"minutes": 5}) for jid in expected}
    extra["not_a_real_job"] = ((lambda: None), "interval", {"minutes": 5})
    with pytest.raises(RuntimeError, match="job-set mismatch"):
        build_registry_scheduler(_FakeScheduler(), "ingest_main", extra, forecast_live_owner_env="ingest_main")


def test_forecast_live_legacy_and_registry_triggers_are_equivalent(monkeypatch) -> None:
    """BRIDGE EQUIVALENCE (advisor #1): the registry path and the legacy path are TWO CONSUMERS of
    ONE spec list, so per job the (id, trigger_type, trigger_params) must be identical. The
    boot-assert guards the id SET; this guards the trigger PARAMS — catching a future edit where
    the two paths silently diverge on cadence. Executor/concurrency intentionally differ (lanes)."""
    import src.ingest.forecast_live_daemon as fld
    from datetime import datetime, timezone
    from src.config import settings
    from src.data.scheduler_adapter import REGISTRY_OWNED_KWARGS

    specs = fld.forecast_live_job_specs(startup_run_date=datetime(2026, 5, 24, tzinfo=timezone.utc))

    # legacy view: id -> (trigger, sorted trigger-only kwargs)
    owned = REGISTRY_OWNED_KWARGS
    legacy = {
        str(kw["id"]): (trig, sorted((k, str(v)) for k, v in kw.items() if k not in owned))
        for _fn, trig, kw in specs
    }
    # registry view from the SAME derivation used at boot:
    registry = {
        jid: (trig, sorted((k, str(v)) for k, v in tkw.items()))
        for jid, (_fn, trig, tkw) in fld._job_defs_from_specs(specs).items()
    }
    assert legacy == registry, "forecast_live legacy vs registry trigger divergence (cadence drift risk)"


def test_forecast_live_boot_assert_holds_in_both_owner_envs(monkeypatch) -> None:
    """PR #329 review #2+#3: forecast_live_daemon only runs as the OpenData owner, so its expected
    registry set is its full 8 jobs REGARDLESS of ZEUS_FORECAST_LIVE_OWNER — the boot assert must
    not crash the forecast daemon (total OpenData-collection outage) if the env var is unset. This
    is the coverage gap that let the fragility hide while 46 tests passed."""
    import src.ingest.forecast_live_daemon as fld
    from datetime import datetime, timezone
    from src.data.scheduler_adapter import (
        build_registry_scheduler, expected_registry_job_ids, job_defs_from_specs,
    )
    from src.config import settings

    specs = fld.forecast_live_job_specs(startup_run_date=datetime(2026, 5, 24, tzinfo=timezone.utc))
    job_defs = job_defs_from_specs(specs)
    assert len(job_defs) == 8

    for env in ("", "forecast_live", "ingest_main"):
        expected = expected_registry_job_ids("forecast_live_daemon", env)
        assert set(job_defs) == expected, (
            f"forecast_live boot assert would FAIL with ZEUS_FORECAST_LIVE_OWNER={env!r}: "
            f"built 8 vs expected {len(expected)} (daemon refuses to boot -> OpenData outage)"
        )
        # and the build actually succeeds (no RuntimeError) in each env:
        built = build_registry_scheduler(_FakeScheduler(), "forecast_live_daemon", job_defs,
                                         forecast_live_owner_env=env)
        assert len(built) == 8


def test_ingest_main_opendata_still_env_gated() -> None:
    """The #2 fix must NOT break the ingest_main side of the singleton: ingest_main (which runs
    regardless of ownership) still drops OpenData when it is not the active owner."""
    from src.data.scheduler_adapter import expected_registry_job_ids

    owns = expected_registry_job_ids("ingest_main", "ingest_main")
    not_owns = expected_registry_job_ids("ingest_main", "forecast_live")
    assert "ingest_opendata_daily_mx2t6" in owns
    assert "ingest_opendata_daily_mx2t6" not in not_owns   # singleton preserved
    assert len(owns) - len(not_owns) == 3                  # the 3 OpenData jobs (2 daily + startup)
