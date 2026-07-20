# Created: 2026-07-20
# Last reused/audited: 2026-07-20
# Authority basis: read-only ingest review pass 2026-07-20, two verified
#   findings against p2 HEAD (~17fd8fb67):
#   FINDING 1 (REAL-DEFECT): HkoExtremaPoller.prefetch() had no absolute
#     total-duration bound. httpx's own timeout is per I/O phase
#     (connect/read/write/pool) and resets on every successful low-level
#     socket read, so a peer that drips bytes just under that per-phase
#     timeout can hold prefetch() open indefinitely. HKO runs on a dedicated
#     single-worker executor with max_instances=1, so a hung tick does not
#     stack concurrently — but it silently freezes HKO ingestion (every
#     future fire is skipped while the one instance hangs).
#   FINDING 2 (MITIGATED, hardened): _bridge_committed_day0_events's
#     per-family seed-enqueue loop ran inside one try/except, so a raise on
#     family N skipped seed pre-warm for every later family. No fact is
#     lost (it is already durably committed before this function runs, and
#     a separate reactor wake + periodic recompute recover it) — this is a
#     latency-isolation hardening, not a fact-loss fix.
"""Antibodies for the HKO prefetch total-duration bound and per-family seed
isolation in the Day0 source-clock materialization bridge.
"""
from __future__ import annotations

import time

import httpx
import pytest


class _NeverCompletesClient:
    """A peer that blocks far longer than any sane total-duration budget.

    Stands in for a peer that drips bytes just under httpx's per-phase
    timeout: from the poller's point of view, the call simply never returns
    inside a bounded window. It does eventually return (so no thread is
    leaked forever in this test), but only long after the poller's budget
    and the assertions below must have already fired.
    """

    def __init__(self, *, sleep_s: float) -> None:
        self.sleep_s = sleep_s
        self.calls = 0

    def get(self, url, *, headers):
        self.calls += 1
        time.sleep(self.sleep_s)
        request = httpx.Request("GET", url, headers=headers)
        return httpx.Response(200, text="unused-late-response", request=request)


def test_hko_prefetch_bounds_total_duration_against_hanging_peer():
    """FINDING 1 antibody.

    A peer that never completes within any per-phase reset window must not
    hold ``prefetch()`` open past the poller's absolute total-duration
    budget, and a timeout must not yield a partial/corrupt fact (it raises
    instead of returning a snapshot).
    """
    from scripts.hko_ingest_tick import HkoExtremaPoller, HkoPrefetchTimeoutError

    budget_s = 0.3
    margin_s = 1.0
    peer_sleep_s = 3.0  # comfortably longer than budget_s + margin_s

    client = _NeverCompletesClient(sleep_s=peer_sleep_s)
    poller = HkoExtremaPoller(client=client, total_budget_s=budget_s)

    started = time.monotonic()
    with pytest.raises(HkoPrefetchTimeoutError):
        poller.prefetch()
    elapsed = time.monotonic() - started

    assert elapsed < budget_s + margin_s
    assert elapsed < peer_sleep_s
    # No partial/corrupt fact: the timeout must short-circuit before any
    # snapshot is built, and the conditional-GET validators (only ever
    # advanced by an explicit post-commit acknowledge()) stay untouched.
    assert poller._etag is None
    assert poller._last_modified is None


def test_bridge_committed_day0_events_seeds_sibling_after_one_family_raises(
    monkeypatch,
):
    """FINDING 2 antibody.

    One family's seed-enqueue raising must not prevent a sibling family's
    seed-enqueue in the same call. The underlying facts are already durably
    committed before this function runs; only pre-warm latency is at stake.
    """
    import src.data.replacement_cycle_advance_trigger as trigger_mod
    import src.ingest_main as im
    import src.runtime.reactor_wake as wake_mod

    processed: list[tuple[str, str, str]] = []

    def _fake_enqueue(*, city, target_date, metric, **_kwargs):
        if city == "Boom City":
            raise RuntimeError("simulated seed-enqueue failure")
        processed.append((city, target_date, metric))
        return {"status": "DAY0_EXTREME_BRIDGE_QUEUED"}

    wake_calls: list[dict] = []
    monkeypatch.setattr(
        trigger_mod,
        "enqueue_day0_extreme_updated_materialization_seed",
        _fake_enqueue,
    )
    monkeypatch.setattr(
        wake_mod,
        "publish_reactor_wake",
        lambda **kwargs: wake_calls.append(kwargs),
    )

    im._bridge_committed_day0_events(
        source="test_source",
        event_ids=("event-1",),
        families=(
            ("Boom City", "2026-07-20", "high"),
            ("Hong Kong", "2026-07-20", "low"),
        ),
    )

    # The failing family raised, but the sibling family after it still ran.
    assert ("Hong Kong", "2026-07-20", "low") in processed
    assert ("Boom City", "2026-07-20", "high") not in processed
    # The unrelated reactor-wake step still runs regardless of the seed error.
    assert len(wake_calls) == 1
