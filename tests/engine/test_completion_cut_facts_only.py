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
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def test_exact_debt_probe_at_live_checkpoint_count_walks_the_queue_once(
    tmp_path, monkeypatch
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
        reactor_wake.CutScope(family_keys=frozenset({_DALLAS}))
    )
    captured["dependency_scope_observer"](frozenset({_DALLAS}))
    assert final() == "wake:day0_extreme_event_committed"
    assert selection() == "wake:day0_extreme_event_committed"


def _run_generic_completion_batch(monkeypatch, era):
    import sqlite3
    from decimal import Decimal
    from types import SimpleNamespace

    import src.engine.global_batch_runtime as global_batch_runtime
    from src.events.candidate_binding import weather_family_id
    from tests.engine.test_global_batch_preemption_grace import _forecast_event

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
        (_forecast_event(city="Dallas", source_run_id="run-dallas"),),
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
