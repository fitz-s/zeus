# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: Operator pre-MC re-audit Blocker B2 (2026-05-28). The MC rebuild's
#   per-snapshot error-params cache key is (city.name, season_label, metric). Two
#   snapshots in the same season but different target_month resolve to the same key,
#   so a row that covers ONE month but not the other is misapplied:
#     - first snapshot's month not covered  -> read_bias_model returns None
#       -> cache[(city,MAM,high)] = None
#       -> SECOND snapshot in the SAME season with a COVERED month silently reuses
#          the cached None and fails open even though a serving row exists.
#     - first snapshot's month covered       -> cache stores params
#       -> a later snapshot with an OFF-coverage month silently reuses the cached
#          params, bypassing the month-scope guard in read_bias_model.
#   Both paths break the canonical-row month-scope antibody (`require_coverage_months`
#   + target_month). Cache key must include target_month so each calendar month is
#   probed independently.
"""B2 — cache_key must include target_month so distinct months are probed independently."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.config import cities_by_name


@pytest.fixture()
def fake_city():
    # Use a real city object so c.lat / c.name / c.settlement_unit are realistic.
    return cities_by_name["Atlanta"]


@pytest.fixture()
def fake_spec():
    # _native_error_params_for_snapshot only reads spec.identity.temperature_metric.
    return SimpleNamespace(identity=SimpleNamespace(temperature_metric="high"))


def test_cache_separates_distinct_target_months(monkeypatch, fake_city, fake_spec):
    """Two snapshots in the same (city, season, metric) but different target_month
    must each trigger an independent read_bias_model probe.

    Pre-fix: cache_key omits target_month so the second snapshot reuses the first's
    cached entry (None or params) → off-month rows misapplied OR covered months
    rejected. Test asserts read_bias_model is called once per distinct target_month.
    """
    from scripts import rebuild_calibration_pairs as mod

    # Spy the DB read so we can inspect target_month per call.
    calls: list[int | None] = []

    def fake_read(conn, **kw):
        calls.append(kw.get("target_month"))
        # Return a usable row only for month==5 so cache-poisoning is visible.
        if kw.get("target_month") == 5:
            return {
                "effective_bias_c": 0.5,
                "total_residual_sd_c": 1.0,
            }
        return None

    monkeypatch.setattr("src.calibration.ens_bias_repo.read_bias_model", fake_read)
    monkeypatch.setattr(mod, "current_gate_set_hash", lambda: "deadbeef", raising=False)

    cache: dict = {}
    conn = MagicMock(spec=sqlite3.Connection)

    # Snapshot A: month 3 in MAM. Row covers only month 5 → fake returns None.
    params_a = mod._native_error_params_for_snapshot(
        conn=conn,
        city=fake_city,
        target_date="2026-03-15",
        spec=fake_spec,
        error_model_family="full_transport_v1",
        cache=cache,
    )
    # Snapshot B: month 5, same MAM/high. Row covers month 5 → fake returns params.
    params_b = mod._native_error_params_for_snapshot(
        conn=conn,
        city=fake_city,
        target_date="2026-05-15",
        spec=fake_spec,
        error_model_family="full_transport_v1",
        cache=cache,
    )

    # Snapshot A's bucket has no row covering its month -> None (fail-open).
    assert params_a is None, "snapshot A's month-3 read must return None"

    # CRITICAL: snapshot B must NOT have been answered from the cache. The cache key
    # is supposed to be month-specific so B re-probes and finds the month-5 row.
    assert params_b is not None, (
        "snapshot B (month 5) must not reuse the cached None from snapshot A's "
        "month-3 probe — cache_key must include target_month"
    )
    assert calls == [3, 5], (
        f"read_bias_model must be invoked once per distinct target_month; got {calls}"
    )


def test_cache_separates_off_coverage_month_from_on_coverage(monkeypatch, fake_city, fake_spec):
    """Inverse: a CACHED row from a covered month must NOT be served to a snapshot
    whose target_month is outside coverage (would bypass read_bias_model's
    month-scope guard).
    """
    from scripts import rebuild_calibration_pairs as mod

    calls: list[int | None] = []

    def fake_read(conn, **kw):
        calls.append(kw.get("target_month"))
        # Only month=5 is covered. The month-scope guard in real read_bias_model
        # rejects any other month -> None.
        if kw.get("target_month") == 5:
            return {"effective_bias_c": 0.2, "total_residual_sd_c": 0.9}
        return None

    monkeypatch.setattr("src.calibration.ens_bias_repo.read_bias_model", fake_read)
    monkeypatch.setattr(mod, "current_gate_set_hash", lambda: "deadbeef", raising=False)

    cache: dict = {}
    conn = MagicMock(spec=sqlite3.Connection)

    params_first = mod._native_error_params_for_snapshot(
        conn=conn, city=fake_city, target_date="2026-05-10",
        spec=fake_spec, error_model_family="full_transport_v1", cache=cache,
    )
    params_off = mod._native_error_params_for_snapshot(
        conn=conn, city=fake_city, target_date="2026-03-10",
        spec=fake_spec, error_model_family="full_transport_v1", cache=cache,
    )

    assert params_first is not None
    assert params_off is None, (
        "off-coverage month must re-probe read_bias_model (which rejects) — "
        "not reuse the cached month-5 params"
    )
    assert calls == [5, 3], (
        f"distinct months must each trigger a probe; got {calls}"
    )
