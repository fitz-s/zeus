# Created: 2026-06-21
# Last audited: 2026-06-21
# Authority basis: docs/evidence/live_order_pathology/2026-06-21_forward_chain_diagnosis.md
#   "CHOSEN FIX (consult-validated, two layers)" — LAYER 2 monitor read-through.
"""Layer 2 — held-belief read-through recompute (consult Stage 1+2).

These tests pin the PURE READ-ONLY fusion compute entrypoint extracted from the
materializer's write path, and the byte-identical preservation of that write
path. The monitor wiring (return-fresh-on-recompute / fail-close-with-belief-debt)
is covered in tests/test_monitor_held_belief_readthrough.py.

TDD: written RED first (the read-only entrypoint does not yet exist).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

import tests.test_replacement_forecast_materializer as base

from src.data.replacement_forecast_materializer import (
    compute_replacement_posterior_readonly,
    materialize_replacement_forecast_live,
)

UTC = timezone.utc


def _dt(hour: int) -> datetime:
    # 2026-06-06 to match the base materializer fixtures' clock: with
    # target_date=2026-06-07 the request is a NON-day0 (lead>=1) family — exactly
    # the held-position scenario LAYER 2's read-through targets.
    return datetime(2026, 6, 6, hour, tzinfo=UTC)


def test_readonly_entrypoint_returns_finite_posterior_and_ci_without_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live-eligible fusion → read-only compute yields finite q + q_lcb + q_ucb
    AND writes NO forecast_posteriors row (INV-37 no-write on the read path)."""
    conn = base._conn()
    base._install_live_fusion(monkeypatch)
    request = base._request(source_cycle_time=_dt(6), computed_at=_dt(10), expires_at=_dt(12))

    result = compute_replacement_posterior_readonly(conn, request)

    assert result is not None
    assert result.live_eligible is True
    # Point distribution over the same bins, finite and summing to ~1.
    assert set(result.q) == {"cool", "warm", "hot"}
    assert abs(sum(result.q.values()) - 1.0) < 1e-6
    for v in result.q.values():
        assert 0.0 <= float(v) <= 1.0
    # A real conservative band: q_lcb <= q_point <= q_ucb per bin.
    assert result.q_lcb_map is not None and result.q_ucb_map is not None
    for b in result.q:
        assert result.q_lcb_map[b] <= result.q[b] + 1e-9
        assert result.q_ucb_map[b] >= result.q[b] - 1e-9
    # The fused center + spread are carried so a caller can audit the belief width.
    assert result.mu_star is not None
    assert result.predictive_sigma_c is not None and result.predictive_sigma_c > 0.0
    # Provenance: provider counts let the caller see when the CI is honestly wider.
    assert result.decorrelated_providers_expected >= result.decorrelated_providers_served >= 0
    # NO posterior row written by the read-only path.
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM readiness_state").fetchone()[0] == 0


def test_readonly_compute_runs_under_query_only_enforcement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense-in-depth (critic 2026-06-21, MEDIUM-1): the read-only compute must issue
    ZERO writes to ANY forecasts table — not just forecast_posteriors / readiness_state.
    Run it under a SQLite query_only=ON connection: any inadvertent write through the read
    path (now, or a future edit to a reader in the deep fusion call tree) raises instead of
    silently corrupting forecast truth during the live monitor loop. This mirrors the
    enforcement the production read-through sets in _attempt_held_belief_readthrough; it is
    strictly stronger than counting rows on two named tables."""
    conn = base._conn()
    base._install_live_fusion(monkeypatch)
    request = base._request(source_cycle_time=_dt(6), computed_at=_dt(10), expires_at=_dt(12))
    conn.execute("PRAGMA query_only=ON")

    # If the compute attempted ANY write through this conn, query_only raises here.
    result = compute_replacement_posterior_readonly(conn, request)

    assert result is not None and result.live_eligible is True
    assert set(result.q) == {"cool", "warm", "hot"}


def test_readonly_entrypoint_reports_not_eligible_when_inputs_insufficient() -> None:
    """No fusion override (missing current single_runs capture) → not live-eligible
    and still no row written. Honest insufficiency, not a fabricated belief."""
    conn = base._conn()
    # No _install_live_fusion: the real override runs and returns None (no persisted
    # current single_runs in this in-memory DB) → single-anchor fallback → not live.
    request = base._request(source_cycle_time=_dt(6), computed_at=_dt(10), expires_at=_dt(12))

    result = compute_replacement_posterior_readonly(conn, request)

    assert result is not None
    assert result.live_eligible is False
    assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0


def test_write_path_unchanged_after_compute_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: the read-only compute and the write path agree on q, and
    the write path still inserts exactly the same posterior it always did."""
    conn_w = base._conn()
    base._install_live_fusion(monkeypatch)
    request = base._request(source_cycle_time=_dt(6), computed_at=_dt(10), expires_at=_dt(12))

    write_result = materialize_replacement_forecast_live(conn_w, request)
    assert write_result.ok is True
    assert write_result.posterior_id is not None
    import json as _json

    written = conn_w.execute(
        "SELECT q_json, q_lcb_json, q_ucb_json FROM forecast_posteriors"
    ).fetchone()
    written_q = _json.loads(written["q_json"])

    # The read-only compute on a clean conn must produce the identical q vector.
    conn_r = base._conn()
    base._install_live_fusion(monkeypatch)
    ro = compute_replacement_posterior_readonly(conn_r, request)
    assert ro is not None and ro.live_eligible is True
    assert set(ro.q) == set(written_q)
    for b in written_q:
        assert abs(float(ro.q[b]) - float(written_q[b])) < 1e-9


def test_arrival_guard_uses_decision_now_not_seed_computed_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGRESSION (real-chain verified 2026-06-21): the read-through MUST pass the
    LIVE decision instant — not the seed's stale ``computed_at`` — as the arrival
    guard's cutoff.  Otherwise, single_runs that arrived AFTER the seed was built
    (the common held-position freeze scenario) are excluded → STALE_HISTORY_ONLY →
    live_eligible=False → read-through returns None → the freeze is reproduced.

    Scenario:
      seed computed_at = _dt(4)  (04:00 UTC — the stale seed decision time)
      single_runs arrive at   _dt(5)  (05:00 UTC — AFTER the seed was built)
      monitor decision instant = _dt(6)  (06:00 UTC — the live cycle, > arrivals)

    With decision_now=_dt(6): arrival guard admits the 05:00 run → live_eligible=True.
    With decision_now=_dt(4): arrival guard EXCLUDES the 05:00 run  → live_eligible=False.

    This directly antibodies the arrival-guard / decision-time interaction that the
    real-chain probe confirmed on Panama City 2026-06-22 high.
    """
    # Single-run arrivals AFTER the seed's build time but BEFORE the live monitor cycle.
    late_available_at = _dt(5)   # 05:00 UTC: arrives after seed (04:00) but before monitor (06:00)
    stale_seed_time   = _dt(4)   # 04:00 UTC: the on-disk seed's original computed_at
    monitor_now       = _dt(6)   # 06:00 UTC: the live monitor cycle instant

    base._install_live_fusion(monkeypatch)

    # Build request with the late-arriving availability timestamps and the stale seed time.
    request = base._request(
        baseline_source_available_at=late_available_at,
        openmeteo_source_available_at=late_available_at,
        source_cycle_time=_dt(0),       # forecast cycle = 00Z
        computed_at=stale_seed_time,    # seed's original decision instant
        expires_at=_dt(12),
    )

    conn_fresh = base._conn()
    conn_stale = base._conn()

    # 1. decision_now = live monitor cycle (AFTER single_runs arrived): must admit them.
    result_fresh = compute_replacement_posterior_readonly(
        conn_fresh, replace(request, computed_at=monitor_now)
    )
    assert result_fresh is not None, "expected a result with monitor_now"
    assert result_fresh.live_eligible is True, (
        f"expected live_eligible=True with decision_now=monitor_now; "
        f"capture_status={result_fresh.capture_status}, "
        f"providers={result_fresh.decorrelated_providers_served}/{result_fresh.decorrelated_providers_expected}"
    )

    # 2. decision_now = stale seed time (BEFORE single_runs arrived): must NOT yield a
    # live-eligible posterior.  The prewrite guard fires
    # REPLACEMENT_MATERIALIZATION_DEPENDENCY_AFTER_COMPUTED_AT (source_available_at >
    # computed_at) and compute_replacement_posterior_readonly returns None — that is the
    # correct "not computable" signal, equivalent to live_eligible=False.
    result_stale = compute_replacement_posterior_readonly(
        conn_stale, replace(request, computed_at=stale_seed_time)
    )
    not_live_eligible = result_stale is None or not result_stale.live_eligible
    assert not_live_eligible, (
        f"expected None or live_eligible=False with stale decision time; "
        f"got capture_status={getattr(result_stale, 'capture_status', 'N/A')}, "
        f"providers={getattr(result_stale, 'decorrelated_providers_served', '?')}"
        f"/{getattr(result_stale, 'decorrelated_providers_expected', '?')}"
    )


def test_request_dataclass_builder_assembles_from_on_disk_seed(tmp_path) -> None:
    """The read-through's request assembly (build_materialize_request_dataclass)
    constructs a valid ReplacementForecastMaterializeRequest from a real on-disk
    seed's anchor payload + precision metadata — the SAME inputs the live queue
    worker uses. Antibody against drift between the read-through and the live
    write path's request shape.
    """
    import tests.test_replacement_forecast_materialization_request_builder as rb_test
    from src.data.replacement_forecast_materialization_request_builder import (
        build_materialize_request_dataclass,
        build_replacement_forecast_materialization_request,
    )
    from src.data.replacement_forecast_materializer import (
        ReplacementForecastMaterializeRequest,
    )

    seed = rb_test._write_inputs(tmp_path)
    built = build_replacement_forecast_materialization_request(seed, base_dir=tmp_path)
    assert built.ok is True and built.request is not None

    request = build_materialize_request_dataclass(built.request, base_dir=tmp_path)

    assert isinstance(request, ReplacementForecastMaterializeRequest)
    assert request.city == "Shanghai"
    assert request.temperature_metric == "high"
    assert str(request.target_date) == "2026-06-07"
    # Anchor was extracted from the on-disk Open-Meteo payload (a real center, not a guess).
    assert request.openmeteo_anchor is not None
    assert request.openmeteo_anchor.high_c is not None
    assert len(request.bins) == 3
    # The precision guard was evaluated from the on-disk metadata.
    assert request.openmeteo_precision_guard is not None
