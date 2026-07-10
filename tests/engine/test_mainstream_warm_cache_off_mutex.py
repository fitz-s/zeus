# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: consolidated timeliness/tradeability fix (architect design) STEP 7 (E2) —
#                  mainstream fetch moved OFF the mutex-held decision path into a warm cache.
"""RED→GREEN T5: the mainstream point is never fetched on the decision path.

E2: the Open-Meteo fetch (whose client time.sleeps on Retry-After) must run only
in the dedicated warm-cache job, never inside the reactor proof path that holds
the world_write_mutex. The proof path reads a cache only and fail-closes to None
on a miss.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest


def test_proof_path_never_calls_blocking_fetch_under_mutex(monkeypatch):
    import src.data.openmeteo_client as openmeteo_client
    import src.data.mainstream_forecast_source as mfs
    import src.strategy.mainstream_agreement as mainstream_agreement
    from src.engine.event_reactor_adapter import _evaluate_and_store_mainstream_agreement
    from src.state.db import world_write_mutex

    fetch_calls: list[tuple] = []

    def _blocking_fetch(url, params, **kwargs):
        fetch_calls.append((url, kwargs.get("endpoint_label")))
        time.sleep(10.0)  # the Retry-After-style block we must never hit on the decision path
        return {}

    monkeypatch.setattr(openmeteo_client, "fetch", _blocking_fetch)
    # Focus the test purely on "is the fetch called on the decision path"; stub
    # the per-candidate verdict eval (covered by its own unit tests).
    monkeypatch.setattr(
        mainstream_agreement,
        "evaluate_mainstream_agreement",
        lambda **_kw: SimpleNamespace(to_dict=lambda: {}),
    )
    # Ensure the warm cache is EMPTY → a fetch would be needed if the path fetched.
    with mfs._WARM_CACHE_LOCK:
        mfs._WARM_CACHE.clear()

    family = SimpleNamespace(
        city="Chicago",
        target_date="2026-06-06",
        metric="high",
        candidates=[SimpleNamespace(condition_id="cond-1", bin="b70")],
    )
    analysis = SimpleNamespace(
        member_maxes=np.array([70.0, 71.0, 72.0]),
        raw_member_maxes=np.array([70.0, 71.0, 72.0]),
        bins=["b69", "b70", "b71"],
        unit="C",
        precision=1.0,
    )
    payload: dict = {}

    mutex = world_write_mutex()
    mutex.acquire()
    start = time.monotonic()
    try:
        _evaluate_and_store_mainstream_agreement(
            event=SimpleNamespace(event_id="e1"),
            family=family,
            analysis=analysis,
            payload=payload,
        )
    finally:
        mutex.release()
    elapsed = time.monotonic() - start

    assert fetch_calls == [], "proof path must NOT call the blocking Open-Meteo fetch"
    assert elapsed < 1.0, f"proof path blocked on a fetch under the mutex: {elapsed:.2f}s"


def test_warm_function_is_the_only_fetcher(monkeypatch):
    """The warm-cache function performs the fetch and populates the cache that the
    proof path then reads (cache-only)."""
    import src.data.mainstream_forecast_source as mfs

    # The autouse conftest fixture forbids the network-backed fetch_mainstream_point
    # in tests. This test legitimately exercises the warm path, so substitute a
    # deterministic stub that returns a fixed snapshot (the warm function's only
    # job is to fetch-and-store; the fetch internals have their own tests).
    fetched: list[tuple] = []

    def _stub_fetch_point(city, target_date, *, metric, **_kw):
        fetched.append((city, target_date, metric))
        return {
            "point": 71.5,
            "unit": "C",
            "metric": metric,
            "source": "open_meteo_standard_forecast",
            "authority_tier": "mainstream",
            "fetched_at_utc": __import__("datetime").datetime.now(
                tz=__import__("datetime").timezone.utc
            ).isoformat(),
            "latitude": 41.0,
            "longitude": -87.0,
            "target_date": target_date,
        }

    monkeypatch.setattr(mfs, "fetch_mainstream_point", _stub_fetch_point)
    with mfs._WARM_CACHE_LOCK:
        mfs._WARM_CACHE.clear()

    # Before warming, the cache-only read returns None (fail-closed).
    assert mfs.read_mainstream_point_cached("Chicago", "2026-06-06", metric="high") is None

    snap = mfs.warm_mainstream_point("Chicago", "2026-06-06", metric="high")
    assert snap is not None and fetched, "warm must perform the fetch"

    # After warming, a repeated warm and the cache-only read both serve it
    # without any further fetch.
    fetched.clear()
    rewarmed = mfs.warm_mainstream_point("Chicago", "2026-06-06", metric="high")
    assert rewarmed is not None and rewarmed["point"] == pytest.approx(71.5)
    assert fetched == [], "warm must not re-fetch a fresh cached point"

    cached = mfs.read_mainstream_point_cached("Chicago", "2026-06-06", metric="high")
    assert cached is not None and cached["point"] == pytest.approx(71.5)
    assert fetched == [], "read must be cache-only — no fetch"
