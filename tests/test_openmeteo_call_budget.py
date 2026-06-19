# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: API self-DoS collapse spec /tmp/fix_api.md §5; openmeteo_quota DAILY_LIMIT.
"""RED-on-revert: the BPF extra-model download must request each (model,cycle) at most
once and stay under a sane daily call ceiling for the full city x model set.

INVARIANT 1: metric never doubles a fetch — no two calls share endpoint+city+date.
INVARIANT 2: models are batched — at most 2 calls per (city,date) (1 single + 1 prev).
INVARIANT 3: projected daily total <=2000 (a per-model revert blows to ~9600 and fails).
test_jma_not_fetched_at_non_publishing_cycle: R3 cadence gate — jma_seamless excluded at 06Z/18Z.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

import pytest

import src.data.bayes_precision_fusion_download as dl
from src.data.bayes_precision_fusion_download import (
    BayesPrecisionFusionDownloadTarget,
    download_bayes_precision_fusion_extra_raw_inputs,
)

SANE_DAILY_CEILING = 2000


def _all_targets():
    """50 cities x 2 target_dates x 2 metrics — full live scope shape."""
    from src.config import cities as ALL  # type: ignore[attr-defined]

    city_list = list(ALL)[:50]
    out = []
    for c in city_list:
        for td in ("2026-06-14", "2026-06-15"):
            for met in ("high", "low"):
                out.append(
                    BayesPrecisionFusionDownloadTarget(
                        city=c.name,
                        metric=met,
                        target_date=td,
                        lead_days=1,
                        latitude=float(c.lat),
                        longitude=float(c.lon),
                        timezone_name=str(c.timezone),
                    )
                )
    return out


def test_one_fetch_per_model_cycle_and_under_budget(tmp_path, monkeypatch):
    """R1+R2 antibody: ONE HTTP call per (endpoint, city, target_date) covering all models
    and both metrics. Budget: projected daily calls <= SANE_DAILY_CEILING."""
    calls: list[tuple] = []  # (endpoint, frozenset(models), city, target_date)

    def fake_single(*, models, city=None, latitude=None, longitude=None, timezone_name=None,
                    run=None, target_local_date=None, forecast_hours=None, **k):
        # city is not a param of _default_live_fetch_batched — derive from target_date via
        # the fact that we record (endpoint, frozenset(models), latitude, target_local_date).
        # The spec uses city; we record latitude as proxy (unique enough for the test).
        calls.append(("single_runs", frozenset(models), str(latitude), str(target_local_date)))
        return {m: (20.0, 10.0) for m in models}

    def fake_prev(*, models, city=None, latitude=None, longitude=None, timezone_name=None,
                  target_date=None, lead_days=None, **k):
        calls.append(("previous_runs", frozenset(models), str(latitude), str(target_date)))
        return {m: (19.0, 9.0) for m in models}

    monkeypatch.setattr(dl, "_default_live_fetch_batched", fake_single)
    monkeypatch.setattr(dl, "_default_previous_runs_fetch_batched", fake_prev)

    from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema
    import sqlite3

    db = tmp_path / "f.db"
    conn = sqlite3.connect(str(db))
    ensure_replacement_forecast_live_schema(conn)
    conn.commit()
    conn.close()

    # One cycle publish — 12Z (all models publish at 12Z)
    download_bayes_precision_fusion_extra_raw_inputs(
        forecast_db=db,
        cycle=datetime(2026, 6, 13, 12, tzinfo=timezone.utc),
        targets=_all_targets(),
    )

    # INVARIANT 1: metric NEVER doubles a fetch — no two calls share (endpoint, lat, date).
    keys = [(ep, lat, td) for (ep, _models, lat, td) in calls]
    assert len(keys) == len(set(keys)), (
        f"metric/duplicate re-fetch detected (R1 regressed): {len(keys) - len(set(keys))} duplicates"
    )

    # INVARIANT 2: models are BATCHED — at most 2 calls per (lat, date) [1 single + 1 prev].
    per_cd = Counter((lat, td) for (_ep, _m, lat, td) in calls)
    if per_cd:
        assert max(per_cd.values()) <= 2, (
            f"un-batched per-model fan-out detected (R2 regressed): "
            f"max calls per (city,date) = {max(per_cd.values())}"
        )

    # INVARIANT 3: total daily ceiling.
    # One publish over 50 cities x 2 target_dates x 2 endpoints = ~200 calls.
    # x4 cycles/day = ~800, well under 2000. A per-model revert would give ~9600.
    projected_daily = len(calls) * 4
    assert projected_daily <= SANE_DAILY_CEILING, (
        f"projected {projected_daily}/day exceeds {SANE_DAILY_CEILING} "
        "— API self-DoS collapse regressed"
    )


def test_jma_not_fetched_at_non_publishing_cycle():
    """R3: jma_seamless must be excluded from the model set at 06Z/18Z (2x/day only)."""
    from src.data.bayes_precision_fusion_download import _model_publishes_cycle

    # jma_seamless: publish only at 00Z and 12Z
    assert _model_publishes_cycle("jma_seamless", 0), "jma_seamless must publish at 00Z"
    assert _model_publishes_cycle("jma_seamless", 12), "jma_seamless must publish at 12Z"
    assert not _model_publishes_cycle("jma_seamless", 6), "jma_seamless must NOT publish at 06Z"
    assert not _model_publishes_cycle("jma_seamless", 18), "jma_seamless must NOT publish at 18Z"

    # gem_global: same cadence
    assert _model_publishes_cycle("gem_global", 0), "gem_global must publish at 00Z"
    assert _model_publishes_cycle("gem_global", 12), "gem_global must publish at 12Z"
    assert not _model_publishes_cycle("gem_global", 6), "gem_global must NOT publish at 06Z"
    assert not _model_publishes_cycle("gem_global", 18), "gem_global must NOT publish at 18Z"

    # 4x/day models: all cycles
    assert _model_publishes_cycle("gfs_global", 6), "gfs_global must publish at 06Z (4x/day model)"
    assert _model_publishes_cycle("ecmwf_ifs", 6), "ecmwf_ifs must publish at 06Z (4x/day model)"
    assert _model_publishes_cycle("icon_global", 18), "icon_global must publish at 18Z (4x/day model)"
