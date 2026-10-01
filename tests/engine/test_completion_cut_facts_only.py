# Created: 2026-09-30
# Last reused or audited: 2026-09-30
# Authority basis: live 08:11-08:41Z 09-30 (live cf424e239): 24 of 31 INCOMPLETE
#   global cuts died at scope_scan to generic_completion_fence/latch at ~29.5 s.
#   Operator law: a cut is cancelled only when a fact it depends on changes.
"""A generic held completion cut ends only on a fact, and its probe is cheap.

Two defects compounded on live:

1. Probe cost. Every scope-scan checkpoint (~400 per held-only scan, ~6,100 per
   full scan) ran the generic completion probe, and each probe re-walked the
   22k-file durable wake queue for exact held-SELL wake ids (twice: latch and
   fence) at ~3.4 ms per walk. A 0.15 s scan became ~28 s.
2. A timer posing as a fact. The latch and fence each cancelled at an absolute
   30 s deadline, a second cancel path outside ``cut_invalidating_wakes`` that
   named no changed fact. The slow scan always reached it.
"""

from __future__ import annotations

import ast
import json
import os
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DALLAS = "edli_family_021210275a8f78ba61213662"  # Dallas|2026-07-11|high


def _write_queue(reactor_wake, path: Path, count: int) -> None:
    queue_dir = reactor_wake._wake_queue_dir(path)
    queue_dir.mkdir(parents=True, exist_ok=True)
    base = datetime(2026, 9, 30, 8, tzinfo=timezone.utc)
    for index in range(count):
        wake = reactor_wake.ReactorWake(
            wake_id=f"wake-{index:05d}",
            published_at=(base + timedelta(microseconds=index)).isoformat(),
            source="probe-cost-antibody",
            reason="forecast_posterior_advanced",
            forecast_families=(("Dallas", "2026-09-30", "high"),),
        )
        reactor_wake._atomic_write_wake(
            queue_dir / f"{index:020d}-{wake.wake_id}.json", wake
        )


@pytest.mark.parametrize("legacy_pointer", (False, True))
def test_exact_debt_probe_at_live_checkpoint_count_walks_the_queue_once(
    tmp_path, monkeypatch, legacy_pointer
):
    """A live-shape scan probes exact debt at every checkpoint without re-walking.

    Live: ~400 checkpoints per held-only scope scan x 2 probes, each walking a
    22k-file queue (~3.4 ms) = ~28 s. Base walks the queue on every probe; now
    an unchanged queue revision (two stats) reuses the id set, so the walk
    count is independent of the checkpoint count and the scan stays in budget.
    """

    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    _write_queue(reactor_wake, path, 2_000)
    if legacy_pointer:
        reactor_wake._atomic_write_wake(path, _legacy_wake(reactor_wake))
    walks = [0]
    walk = reactor_wake._queued_wakes

    def counted(*args, **kwargs):
        walks[0] += 1
        return walk(*args, **kwargs)

    monkeypatch.setattr(reactor_wake, "_queued_wakes", counted)
    started = time.monotonic()
    for _ in range(2 * 400):
        assert reactor_wake.exact_held_sell_completion_wake_ids(
            path=path, fail_on_error=True
        ) == frozenset()
    assert walks[0] == 1
    assert time.monotonic() - started < 2.0


def test_cached_exact_debt_ids_see_a_newly_published_exact_wake(tmp_path):
    """Reuse never hides new exact debt: publishing changes the revision."""

    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    _write_queue(reactor_wake, path, 3)
    assert reactor_wake.exact_held_sell_completion_wake_ids(
        path=path, fail_on_error=True
    ) == frozenset()
    request = reactor_wake.make_held_sell_reauction_request(
        position_id="pos-1",
        family=("Dallas", "2026-09-30", "high"),
        probability_content_identity="q-1",
        held_token_id="tok-1",
        held_best_bid=0.4,
        bid_observed_at="2026-09-30T08:00:00+00:00",
    )
    wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=path,
        held_sell_reauction_requests=(request,),
    )
    assert reactor_wake.exact_held_sell_completion_wake_ids(
        path=path, fail_on_error=True
    ) == frozenset({wake.wake_id})


def _legacy_wake(reactor_wake, *, wake_id="legacy-known", exact=False):
    request = reactor_wake.make_held_sell_reauction_request(
        position_id="pos-1",
        family=("Dallas", "2026-09-30", "high"),
        probability_content_identity="q-1",
        held_token_id="tok-1",
        held_best_bid=0.4,
        bid_observed_at="2026-09-30T08:00:00+00:00",
    )
    return reactor_wake.ReactorWake(
        wake_id=wake_id,
        published_at="2026-09-30T08:00:00+00:00",
        source="private-strict-cache-antibody",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        forecast_families=(("Dallas", "2026-09-30", "high"),),
        held_sell_reauction_requests=(request,) if exact else (),
    )


@contextmanager
def _deny_private_path_access(path, mechanism):
    """Change only a newly created private fixture's real OS permissions."""
    import pwd
    import stat
    import subprocess
    import sys

    original_mode = stat.S_IMODE(path.stat().st_mode)
    acl = mechanism.startswith("acl_")
    if acl:
        if sys.platform != "darwin":
            pytest.skip("macOS ACL list/search/read test requires a macOS filesystem")
        user = pwd.getpwuid(os.getuid()).pw_name
        permission = mechanism.removeprefix("acl_")
        subprocess.run(
            ["chmod", "+a", f"user:{user} deny {permission}", str(path)],
            check=True, capture_output=True, timeout=3,
        )
    else:
        removed = 0o444 if mechanism == "no_read" else 0o111
        path.chmod(original_mode & ~removed)
    try:
        yield
    finally:
        if acl:
            subprocess.run(
                ["chmod", "-N", str(path)],
                check=True, capture_output=True, timeout=3,
            )
        path.chmod(original_mode)


def _clear_private_queue_cache(reactor_wake, path):
    queue_dir = reactor_wake._wake_queue_dir(path)
    with reactor_wake._WAKE_QUEUE_CACHE_LOCK:
        reactor_wake._EXACT_HELD_SELL_WAKE_IDS.pop(queue_dir, None)
        reactor_wake._WAKE_QUEUE_CACHE.pop(queue_dir, None)
        reactor_wake._WAKE_QUEUE_REVISIONS.pop(queue_dir, None)


@pytest.mark.parametrize("mechanism", ("no_read", "acl_read"))
@pytest.mark.parametrize("warm", (False, True))
def test_strict_cached_legacy_read_error_is_real_and_resets(tmp_path, mechanism, warm):
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    _write_queue(reactor_wake, path, 1)
    reactor_wake._atomic_write_wake(path, _legacy_wake(reactor_wake))
    probe = lambda: reactor_wake.exact_held_sell_completion_wake_ids(
        path=path, fail_on_error=True
    )
    assert probe() == frozenset()
    revision = reactor_wake._wake_queue_revision(
        reactor_wake._wake_queue_dir(path), path=path, fail_on_error=True
    )
    with _deny_private_path_access(path, mechanism):
        assert reactor_wake._wake_queue_revision(
            reactor_wake._wake_queue_dir(path), path=path, fail_on_error=True
        ) == revision
        if not warm:
            _clear_private_queue_cache(reactor_wake, path)
        with pytest.raises(PermissionError):
            probe()
    assert probe() == frozenset()


@pytest.mark.parametrize("mechanism", ("no_read", "no_search", "acl_list", "acl_search"))
@pytest.mark.parametrize("warm", (False, True))
def test_strict_cached_directory_needs_real_list_and_search_access(tmp_path, mechanism, warm):
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    _write_queue(reactor_wake, path, 1)
    probe = lambda: reactor_wake.exact_held_sell_completion_wake_ids(
        path=path, fail_on_error=True
    )
    assert probe() == frozenset()
    with _deny_private_path_access(reactor_wake._wake_queue_dir(path), mechanism):
        if not warm:
            _clear_private_queue_cache(reactor_wake, path)
        with pytest.raises(PermissionError):
            probe()
    assert probe() == frozenset()


def test_cached_exact_ids_use_current_legacy_even_with_the_same_revision(tmp_path):
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    _write_queue(reactor_wake, path, 1)
    first = _legacy_wake(reactor_wake, wake_id="legacy-first", exact=True)
    reactor_wake._atomic_write_wake(path, first)
    probe = lambda: reactor_wake.exact_held_sell_completion_wake_ids(
        path=path, fail_on_error=True
    )
    assert probe() == frozenset({first.wake_id})
    original = path.stat()
    revision = reactor_wake._wake_queue_revision(
        reactor_wake._wake_queue_dir(path), path=path, fail_on_error=True
    )
    payload = json.loads(path.read_text())
    payload["wake_id"] = "legacy-later"
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert reactor_wake._wake_queue_revision(
        reactor_wake._wake_queue_dir(path), path=path, fail_on_error=True
    ) == revision
    assert probe() == frozenset({"legacy-later"})


def test_cached_exact_ids_legacy_replace_delete_corruption_and_restore(tmp_path):
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    _write_queue(reactor_wake, path, 1)
    first = _legacy_wake(reactor_wake, exact=True)
    reactor_wake._atomic_write_wake(path, first)
    probe = lambda: reactor_wake.exact_held_sell_completion_wake_ids(
        path=path, fail_on_error=True
    )
    assert probe() == frozenset({first.wake_id})
    later = replace(first, wake_id="legacy-next")
    reactor_wake._atomic_write_wake(path, later)
    assert probe() == frozenset({later.wake_id})
    original = path.stat()
    path.write_text("{" + " " * (original.st_size - 1))
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    with pytest.raises(ValueError, match="REACTOR_WAKE_INVALID"):
        probe()
    path.unlink()
    assert probe() == frozenset()  # known absence is not unreadable truth
    reactor_wake._atomic_write_wake(path, later)
    assert probe() == frozenset({later.wake_id})


def test_warm_legacy_read_failure_cancels_actual_selection_and_final(tmp_path, monkeypatch):
    import src.engine.event_reactor_adapter as era
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    _write_queue(reactor_wake, path, 1)
    reactor_wake._atomic_write_wake(path, _legacy_wake(reactor_wake))
    probe = reactor_wake.exact_held_sell_completion_wake_ids
    monkeypatch.setattr(
        reactor_wake, "exact_held_sell_completion_wake_ids",
        lambda **kwargs: probe(path=path, **kwargs),
    )
    captured = _run_generic_completion_batch(monkeypatch, era)
    for name in ("selection_cancelled", "final_actuation_cancelled"):
        assert captured[name]() is False
    with _deny_private_path_access(path, "no_read"):
        for name in ("selection_cancelled", "final_actuation_cancelled"):
            assert captured[name]() == "exact_held_sell_queue_unreadable"
    for name in ("selection_cancelled", "final_actuation_cancelled"):
        assert captured[name]() is False


def test_lenient_invalid_queue_read_never_seeds_a_strict_exact_cache(tmp_path):
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    _write_queue(reactor_wake, path, 1)
    queue_dir = reactor_wake._wake_queue_dir(path)
    (queue_dir / "invalid.json").write_text("{")
    assert reactor_wake.exact_held_sell_completion_wake_ids(path=path) == frozenset()
    with pytest.raises(ValueError, match="REACTOR_WAKE_INVALID"):
        reactor_wake.exact_held_sell_completion_wake_ids(path=path, fail_on_error=True)


def _publish_private_v4_then_ordinary(path, metric, wake_id, q_identity):
    """The normal publisher, with only private queue/lineage/socket paths."""
    from src.runtime import reactor_wake

    now = datetime.now(timezone.utc)
    family = ("Dallas", "2026-09-30", metric)
    request = reactor_wake.make_held_sell_reauction_request(
        position_id=f"pos-{metric}", family=family,
        probability_content_identity=q_identity,
        held_token_id=f"token-{metric}", held_best_bid=0.4,
        bid_observed_at=now.isoformat(), probability_observed_at=now.isoformat(),
        schema_version=4, generation="one-private-debt",
        completion_deadline_at=(now + timedelta(seconds=30)).isoformat(),
        selection_epoch_identity="private-epoch",
        sell_book_witness_identity="private-book",
        debt_event_id="private-debt", monitor_event_id="private-monitor",
    )
    wake = reactor_wake.publish_reactor_wake(
        path=path, source="private-normal-publisher",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        wake_id=wake_id, forecast_families=(family,),
        held_sell_reauction_requests=(request,),
    )
    reactor_wake.publish_reactor_wake(
        path=path, source="private-normal-publisher",
        reason="forecast_posterior_advanced", forecast_families=(family,),
    )
    return wake


@pytest.mark.parametrize("other_process", (False, True))
@pytest.mark.parametrize("metric", ("high", "low"))
def test_cached_exact_ids_follow_normal_v4_slot_replacement_and_reset(
    tmp_path, monkeypatch, other_process, metric
):
    import subprocess
    import sys
    import src.engine.event_reactor_adapter as era
    from src.runtime import reactor_wake

    path = tmp_path / "wake.json"
    _write_queue(reactor_wake, path, 1)
    probe = reactor_wake.exact_held_sell_completion_wake_ids
    monkeypatch.setattr(
        reactor_wake, "exact_held_sell_completion_wake_ids",
        lambda **kwargs: probe(path=path, **kwargs),
    )
    captured = _run_generic_completion_batch(monkeypatch, era)
    assert captured["selection_cancelled"]() is False
    assert captured["final_actuation_cancelled"]() is False

    first = _publish_private_v4_then_ordinary(path, metric, "wake-old", "q-old")
    assert probe(path=path, fail_on_error=True) == frozenset({first.wake_id})
    original_slot = reactor_wake._wake_queue_target(first, path=path)

    def publish(wake_id, q_identity):
        if other_process:
            subprocess.run(
                [sys.executable, "-c",
                 "from pathlib import Path; import sys; "
                 "from tests.engine.test_completion_cut_facts_only import "
                 "_publish_private_v4_then_ordinary; "
                 "_publish_private_v4_then_ordinary(Path(sys.argv[1]),"
                 "sys.argv[2],sys.argv[3],sys.argv[4])",
                 str(path), metric, wake_id, q_identity],
                cwd=_ROOT, check=True, capture_output=True, timeout=8,
            )
        else:
            _publish_private_v4_then_ordinary(path, metric, wake_id, q_identity)

    for wake_id, q_identity in (("wake-new", "q-new"), ("wake-old", "q-old")):
        publish(wake_id, q_identity)
        legacy = reactor_wake._read_reactor_wake_path(path, fail_on_error=True)
        assert legacy.reason == "forecast_posterior_advanced"
        warm_ids = probe(path=path, fail_on_error=True)
        assert warm_ids == frozenset({wake_id})
        slots = [
            (file, wake)
            for file, wake in reactor_wake._queued_wakes(path, fail_on_error=True)
            if wake.held_sell_reauction_requests
        ]
        assert len(slots) == 1
        assert slots[0][0] == original_slot
        assert slots[0][1].held_sell_reauction_requests[0].probability_content_identity == q_identity
        assert captured["selection_cancelled"]() == "exact_held_sell_pending"
        assert captured["final_actuation_cancelled"]() == "exact_held_sell_pending"
        _clear_private_queue_cache(reactor_wake, path)
        assert probe(path=path, fail_on_error=True) == warm_ids

    assert reactor_wake.acknowledge_reactor_wake(slots[0][1], path=path)
    assert probe(path=path, fail_on_error=True) == frozenset()
    assert captured["selection_cancelled"]() is False
    assert captured["final_actuation_cancelled"]() is False


def test_a_request_marker_or_elapsed_time_never_cancels_a_generic_completion(
    monkeypatch,
):
    """The completion cut's cancel probes name facts only.

    A queued REQUEST marker (another generic completion) is not a fact change,
    and no amount of elapsed time is one. Base: the fence returned
    ``generic_completion_fence`` once the 30 s latch deadline passed, with no
    fact named.
    """

    import src.engine.event_reactor_adapter as era
    import src.runtime.reactor_wake as reactor_wake
    clock = [1_000.0]
    monkeypatch.setattr(era._time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        reactor_wake, "exact_held_sell_completion_wake_ids", lambda **_kw: ()
    )
    revision = [(1, 1, 1)]
    monkeypatch.setattr(reactor_wake, "reactor_urgent_wake_revision", lambda: revision[0])
    monkeypatch.setattr(
        reactor_wake, "_read_reactor_wake_path", lambda *_a, **_kw: None
    )
    request_marker = reactor_wake.ReactorWake(
        wake_id="generic-completion-2",
        published_at="2026-07-10T08:10:01+00:00",
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        forecast_families=(("Dallas", "2026-07-11", "high"),),
    )
    queued = [request_marker]
    monkeypatch.setattr(
        reactor_wake, "reactor_wakes_since", lambda *_a, **_kw: tuple(queued)
    )
    assert reactor_wake.cut_invalidating_wakes(
        (request_marker,),
        reactor_wake.CutDependency(
            published=True, hard_family_keys=None, belief_family_keys=None
        ),
    ) == reactor_wake.CutInvalidation()

    captured = _run_generic_completion_batch(monkeypatch, era)
    final = captured["final_actuation_cancelled"]
    selection = captured["selection_cancelled"]
    assert not final()
    assert not selection()
    clock[0] += 3_600.0  # an hour later: still no fact changed
    assert not final()
    assert not selection()

    # A real fact still ends it, labelled by the fact (one predicate).
    day0 = reactor_wake.ReactorWake(
        wake_id="day0-dallas",
        published_at="2026-07-10T08:10:02+00:00",
        source="day0",
        reason="day0_extreme_event_committed",
        forecast_families=(("Dallas", "2026-07-11", "high"),),
    )
    queued.append(day0)
    revision[0] = (2, 2, 2)
    captured["cut_scope_observer"](
        reactor_wake.CutScope(winner_family_key=_DALLAS)
    )
    assert final() == "wake:day0_extreme_event_committed"
    assert selection() == "wake:day0_extreme_event_committed"


def _run_generic_completion_batch(monkeypatch, era):
    import sqlite3
    from decimal import Decimal
    from types import SimpleNamespace

    import src.engine.global_batch_runtime as global_batch_runtime
    from src.events.candidate_binding import weather_family_id
    from tests.engine.test_day0_preemption_scope import _forecast_event

    trade = sqlite3.connect(":memory:")
    trade.row_factory = sqlite3.Row
    trade.execute(
        "CREATE TABLE risk_actions (action_id TEXT PRIMARY KEY, strategy_key TEXT,"
        " action_type TEXT, value TEXT, issued_at TEXT, effective_until TEXT,"
        " precedence INTEGER, status TEXT)"
    )
    forecast = sqlite3.connect(":memory:")
    topology = sqlite3.connect(":memory:")
    world = sqlite3.connect(":memory:")
    captured: dict = {}

    def fake_process(events, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(events=tuple(events), winner_event_id=None, receipts={})

    monkeypatch.setattr(global_batch_runtime, "process_current_global_batch", fake_process)
    monkeypatch.setattr(
        era, "_entry_global_submit_suppression_reason", lambda: "entries_paused:test"
    )
    monkeypatch.setattr(era, "_entry_pause_blocks_live_submit", lambda _conn: None)

    class CapacityAuthority:
        def capacity_usd(self, **kwargs):
            return Decimal("17")

    held = weather_family_id(city="Dallas", target_date="2026-07-11", metric="high")
    adapter = era.event_bound_live_adapter_from_trade_conn(
        trade,
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=forecast,
        topology_conn=topology,
        calibration_conn=world,
        portfolio_state_provider=lambda: None,
        auction_capital_authority=CapacityAuthority(),
        family_scoped_held_completion=True,
        selection_completion_reserved=True,
        required_held_family_keys=frozenset({held}),
    )
    adapter.process_global_batch(
        (_forecast_event("Dallas"),),
        datetime(2026, 7, 10, 8, 10, tzinfo=timezone.utc),
    )
    return captured


def test_no_cut_cancel_path_is_a_timer():
    """Structural antibody: no cancel leaf or fence reads a completion deadline.

    The removed second path compared ``monotonic()`` to a generic completion
    deadline inside cancel probes. Every cut-cancel path now goes through
    ``cut_invalidating_wakes`` (facts), exact held-SELL debt, or the cut's own
    work deadline (a WorkContext DEADLINE, which defers and never claims a
    fact changed).
    """

    for rel in ("src/events/reactor.py", "src/engine/event_reactor_adapter.py"):
        text = (_ROOT / rel).read_text()
        for name in (
            "generic_completion_latch",
            "generic_completion_fence",
            "generic_completion_deadline_monotonic",
            "_try_latch_generic_held_completion",
            "_generic_held_completion_latch_cancelled",
            "_generic_final_actuation_is_cancelled",
        ):
            assert name not in text, (rel, name)
    tree = ast.parse((_ROOT / "src/events/reactor.py").read_text())
    pending = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_process_pending_cancelled"
    )
    assert "monotonic" not in ast.unparse(pending)
