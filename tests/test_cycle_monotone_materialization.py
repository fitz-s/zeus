# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: U5 step 2a (operator regime-unification + freshness investigation 2026-06-12,
#   docs/authority/regime_unification_2026-06-12.md §U2; docs/evidence/freshness/
#   2026-06-12_forecast_freshness_truth.md §Q3/§Q4). Relationship-first pins for:
#     (A) monotone consumed-cycle advance — a materialize request whose cycle is OLDER than the
#         family's current posterior cycle is UNCONSTRUCTABLE (typed BLOCKED, no row written);
#     (B) newer-cycle re-materialization trigger — fires EXACTLY when a fresher cycle is ingested
#         and NOT on a wall clock with no new cycle; held-position families prioritized;
#     (D) the synthetic +14h availability stamp is GONE (literal scan) and consumers audited.
#   These are CROSS-MODULE invariants (posterior DB row ⇄ materialize request; raw-artifact legs ⇄
#   re-mat enqueue; download row ⇄ availability provenance), so they are written as relationship
#   assertions, not function tests of one side alone.
"""Antibody tests for cycle-monotone materialization + newer-cycle re-mat + honest availability."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.data.replacement_cycle_advance_trigger import (
    SOURCE_ID as ADV_SOURCE_ID,
    freshest_materializable_cycle,
    scope_needs_cycle_advance,
)
from src.data.replacement_forecast_materializer import (
    SOURCE_ID as MAT_SOURCE_ID,
    _cycle_monotone_block_reasons,
)
from src.state.schema.v2_schema import ensure_replacement_forecast_shadow_schema

UTC = timezone.utc

_REGRESSION_REASON = "REPLACEMENT_MATERIALIZATION_SOURCE_CYCLE_REGRESSION"


# ---------------------------------------------------------------------------
# Minimal in-memory request stub for the conn-aware monotone guard. The guard reads only
# request.source_cycle_time / city / target_date / temperature_metric from the request, so a stub
# carrying exactly those fields exercises the real cross-module SQL without the 51-member AIFS
# fixture debt of the full materialize path.
# ---------------------------------------------------------------------------
class _Req:
    def __init__(self, *, city: str, target_date, metric: str, source_cycle_time: datetime) -> None:
        self.city = city
        self.target_date = target_date
        self.temperature_metric = metric
        self.source_cycle_time = source_cycle_time


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_shadow_schema(conn)
    return conn


def _insert_posterior(conn: sqlite3.Connection, *, city: str, target_date: str, metric: str,
                      cycle_iso: str, computed_at: str) -> None:
    conn.execute(
        """
        INSERT INTO forecast_posteriors
            (source_id, product_id, data_version, city, target_date, temperature_metric,
             source_cycle_time, source_available_at, computed_at, q_json, q_lcb_json,
             posterior_method, dependency_source_run_ids_json, provenance_json,
             trade_authority_status, training_allowed)
        VALUES (?, 'pid', 'dv', ?, ?, ?, ?, ?, ?, '{}', '{}', 'm', '{}', '{}', 'SHADOW_ONLY', 0)
        """,
        (MAT_SOURCE_ID, city, target_date, metric, cycle_iso, cycle_iso, computed_at),
    )
    conn.commit()


def _insert_artifact(conn: sqlite3.Connection, *, source_id: str, cycle_iso: str) -> None:
    """Insert a minimal raw_forecast_artifacts row for the freshest-materializable-cycle high-water
    mark. Fills EVERY NOT-NULL non-PK column generically from PRAGMA so the test survives schema
    evolution; only source_id + source_cycle_time carry meaning for the query under test."""
    meaningful = {"source_id": source_id, "source_cycle_time": cycle_iso}
    values: dict[str, object] = {}
    for r in conn.execute("PRAGMA table_info(raw_forecast_artifacts)"):
        name, notnull, pk = r[1], r[3], r[5]
        if pk:
            continue  # autoincrement
        if name in meaningful:
            values[name] = meaningful[name]
        elif notnull:
            # JSON columns need valid JSON; numeric columns need a number; everything else a string.
            if name.endswith("_json"):
                values[name] = "{}"
            elif name in ("byte_size", "training_allowed"):
                values[name] = 0
            elif name == "trade_authority_status":
                values[name] = "SHADOW_ONLY"  # CHECK-constrained enum
            elif name.endswith("_at") or name.endswith("_time"):
                values[name] = cycle_iso
            else:
                values[name] = f"{source_id}-x"
    names = ", ".join(values)
    qs = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO raw_forecast_artifacts ({names}) VALUES ({qs})", tuple(values.values()))
    conn.commit()


# ===========================================================================
# (A) MONOTONE CONSUMED-CYCLE ADVANCE — backward materialization is unconstructable.
# ===========================================================================
def test_backward_cycle_request_is_refused() -> None:
    """RELATIONSHIP: family posterior consumed 06Z; a request for the OLDER 00Z cycle must be
    REFUSED with the typed regression reason (the thrashing disease — a backward consumed-cycle
    step — becomes unconstructable, not a silent ±2.5°C swing)."""
    conn = _conn()
    _insert_posterior(conn, city="Shanghai", target_date="2026-06-13", metric="high",
                      cycle_iso="2026-06-12T06:00:00+00:00", computed_at="2026-06-12T16:00:00+00:00")
    req = _Req(city="Shanghai", target_date=date(2026, 6, 13), metric="high",
               source_cycle_time=datetime(2026, 6, 12, 0, tzinfo=UTC))  # OLDER 00Z
    reasons = _cycle_monotone_block_reasons(conn, req, metric="high")
    assert _REGRESSION_REASON in reasons


def test_forward_cycle_request_is_allowed() -> None:
    """A request for a NEWER cycle than the current posterior is admitted (the whole point of
    re-materialization — advance the belief onto fresher information)."""
    conn = _conn()
    _insert_posterior(conn, city="Shanghai", target_date="2026-06-13", metric="high",
                      cycle_iso="2026-06-12T00:00:00+00:00", computed_at="2026-06-12T10:00:00+00:00")
    req = _Req(city="Shanghai", target_date=date(2026, 6, 13), metric="high",
               source_cycle_time=datetime(2026, 6, 12, 6, tzinfo=UTC))  # NEWER 06Z
    assert _cycle_monotone_block_reasons(conn, req, metric="high") == ()


def test_same_cycle_request_is_allowed() -> None:
    """EQUAL cycle is allowed: a same-cycle re-materialization is the legitimate fusion-upgrade /
    instrument-set-expansion path (Task #32). The monotone law refuses only a STRICTLY older cycle."""
    conn = _conn()
    _insert_posterior(conn, city="Shanghai", target_date="2026-06-13", metric="high",
                      cycle_iso="2026-06-12T06:00:00+00:00", computed_at="2026-06-12T10:00:00+00:00")
    req = _Req(city="Shanghai", target_date=date(2026, 6, 13), metric="high",
               source_cycle_time=datetime(2026, 6, 12, 6, tzinfo=UTC))  # SAME 06Z
    assert _cycle_monotone_block_reasons(conn, req, metric="high") == ()


def test_no_prior_posterior_is_allowed() -> None:
    """A first materialization (no prior posterior for the family) is never a regression."""
    conn = _conn()
    req = _Req(city="Ghostville", target_date=date(2026, 6, 13), metric="high",
               source_cycle_time=datetime(2026, 6, 12, 0, tzinfo=UTC))
    assert _cycle_monotone_block_reasons(conn, req, metric="high") == ()


def test_monotone_guard_is_family_scoped() -> None:
    """A backward step in ANOTHER family/metric must not block this one (family identity =
    source_id+city+target_date+temperature_metric, the same key the trigger + serving authority use)."""
    conn = _conn()
    # Different metric (low) at a newer cycle must not constrain the high family's request.
    _insert_posterior(conn, city="Shanghai", target_date="2026-06-13", metric="low",
                      cycle_iso="2026-06-12T12:00:00+00:00", computed_at="2026-06-12T20:00:00+00:00")
    req = _Req(city="Shanghai", target_date=date(2026, 6, 13), metric="high",
               source_cycle_time=datetime(2026, 6, 12, 0, tzinfo=UTC))
    assert _cycle_monotone_block_reasons(conn, req, metric="high") == ()


# ===========================================================================
# (B) NEWER-CYCLE RE-MATERIALIZATION TRIGGER — fires on new cycle, NOT on the clock.
# ===========================================================================
def test_trigger_fires_when_fresher_cycle_ingested() -> None:
    """BORN-STALE/RE-MAT pin: posterior consumed 00Z; a fresher 06Z cycle is materializable (both
    legs ingested) => the scope needs a cycle advance onto 06Z."""
    conn = _conn()
    _insert_posterior(conn, city="Shanghai", target_date="2026-06-13", metric="high",
                      cycle_iso="2026-06-12T00:00:00+00:00", computed_at="2026-06-12T10:00:00+00:00")
    target = datetime(2026, 6, 12, 6, tzinfo=UTC)
    verdict = scope_needs_cycle_advance(conn, city="Shanghai", target_date="2026-06-13",
                                        metric="high", freshest_cycle=target)
    assert verdict["needs_advance"] is True
    assert verdict["consumed_cycle"] == "2026-06-12T00:00:00+00:00"
    assert verdict["target_cycle"] == "2026-06-12T06:00:00+00:00"


def test_trigger_does_not_fire_on_wall_clock_without_new_cycle() -> None:
    """THE physics pin (freshness investigation §Q3): belief decay is a STEP on missed CYCLES, not
    a smooth function of hours. If the freshest materializable cycle EQUALS the consumed cycle, NO
    advance fires — even after arbitrary wall-clock time. Re-materialization is worthless on a
    clock and worthwhile only when a newer cycle exists."""
    conn = _conn()
    _insert_posterior(conn, city="Shanghai", target_date="2026-06-13", metric="high",
                      cycle_iso="2026-06-12T06:00:00+00:00", computed_at="2026-06-12T10:00:00+00:00")
    same = datetime(2026, 6, 12, 6, tzinfo=UTC)  # no newer cycle ingested
    verdict = scope_needs_cycle_advance(conn, city="Shanghai", target_date="2026-06-13",
                                        metric="high", freshest_cycle=same)
    assert verdict["needs_advance"] is False


def test_trigger_does_not_fire_on_older_freshest_than_consumed() -> None:
    """Defensive: if the universe high-water mark is somehow OLDER than the consumed cycle (a leg
    regressed), the advance must NOT fire (no backward re-seed — that is the monotone law's job to
    refuse, and the trigger never proposes it)."""
    conn = _conn()
    _insert_posterior(conn, city="Shanghai", target_date="2026-06-13", metric="high",
                      cycle_iso="2026-06-12T12:00:00+00:00", computed_at="2026-06-12T20:00:00+00:00")
    older = datetime(2026, 6, 12, 6, tzinfo=UTC)
    verdict = scope_needs_cycle_advance(conn, city="Shanghai", target_date="2026-06-13",
                                        metric="high", freshest_cycle=older)
    assert verdict["needs_advance"] is False


def test_freshest_materializable_cycle_is_min_over_both_legs() -> None:
    """A cycle is materializable only when BOTH legs (AIFS + OM9 anchor) are ingested: the freshest
    materializable cycle = MIN(MAX(aifs), MAX(anchor)). A half-published newer cycle (only one leg)
    is NOT yet a re-mat opportunity (mirrors the downloader high-water mark)."""
    conn = _conn()
    # Anchor reached 12Z; AIFS only reached 06Z => freshest materializable = 06Z (the lagging leg).
    _insert_artifact(conn, source_id="openmeteo_ecmwf_ifs_9km", cycle_iso="2026-06-12T12:00:00+00:00")
    _insert_artifact(conn, source_id="ecmwf_aifs_ens", cycle_iso="2026-06-12T06:00:00+00:00")
    got = freshest_materializable_cycle(conn)
    assert got == datetime(2026, 6, 12, 6, tzinfo=UTC)


def test_freshest_materializable_cycle_none_when_a_leg_missing() -> None:
    conn = _conn()
    _insert_artifact(conn, source_id="ecmwf_aifs_ens", cycle_iso="2026-06-12T06:00:00+00:00")
    # No anchor leg at all => nothing is materializable.
    assert freshest_materializable_cycle(conn) is None


def test_cycle_advance_marker_unique_bounds_enqueue_to_once_per_target_cycle() -> None:
    """IDEMPOTENCY: the marker UNIQUE(city,target_date,metric,target_cycle_time) makes a second
    enqueue for the SAME target cycle a no-op (at most one re-mat per cycle advance); the NEXT
    fresher cycle is a distinct marker that enqueues again."""
    conn = _conn()
    base = ("2026-06-12T16:00:00+00:00", "Shanghai", "2026-06-13", "high",
            "2026-06-12T00:00:00+00:00")

    def _insert(target_cycle: str, seed: str) -> int:
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO cycle_advance_enqueues
                (enqueued_at, city, target_date, metric, consumed_cycle_time, target_cycle_time,
                 held_position, seed_file)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (*base, target_cycle, seed),
        )
        conn.commit()
        return conn.total_changes - before

    assert _insert("2026-06-12T06:00:00+00:00", "s1.json") == 1, "first enqueue inserts"
    assert _insert("2026-06-12T06:00:00+00:00", "s2.json") == 0, "same target cycle is a no-op"
    assert _insert("2026-06-12T12:00:00+00:00", "s3.json") == 1, "a fresher target cycle re-enqueues"


# ===========================================================================
# (D) HONEST AVAILABILITY — the synthetic +14h stamp is gone; the row stamp is proof-of-possession.
# ===========================================================================
def test_synthetic_14h_availability_literal_is_gone() -> None:
    """LITERAL SCAN: the bayes_precision_fusion download must no longer stamp a standalone synthetic
    source_available_at = cycle + 14h. The honest value is min(captured_at, nominal)
    (proof-of-possession), so the only remaining use of the lag offset is as the nominal ceiling
    INSIDE that min()."""
    src = Path("src/data/bayes_precision_fusion_download.py").read_text(encoding="utf-8")
    # The honest stamp must be present...
    assert "min(captured_at, nominal_available)" in src, "row stamp must be the proof-of-possession bound"
    # ...and the standalone synthetic assignment must be gone.
    assert "source_available_iso = (cycle_utc + timedelta(hours=release_lag_hours)).isoformat()" not in src


def test_availability_stamp_is_proof_of_possession_bound() -> None:
    """RELATIONSHIP (download row ⇄ availability provenance): a row is only written when the value is
    POSSESSED, so source_available_at must never exceed captured_at. We assert the bound directly on
    the production code path via a fake fetcher capturing the persisted rows."""
    import src.data.bayes_precision_fusion_download as mod

    captured: list[dict] = []

    def _fake_persist(forecast_db, rows, cutoff_iso=None):
        for r in rows:
            captured.append(dict(r))
        return len(list(rows)), 0

    # Patch the chunk persister so no real DB is touched; capture the rows it would write.
    orig = mod._persist_chunk_with_lock_retry
    mod._persist_chunk_with_lock_retry = _fake_persist  # type: ignore[assignment]
    try:
        from src.data.bayes_precision_fusion_download import (
            download_bayes_precision_fusion_extra_raw_inputs,
        )
        from datetime import datetime as _dt

        # A cycle whose nominal (cycle+14h) is in the FUTURE relative to capture: the honest stamp
        # must clamp to captured_at, never the future nominal.
        cycle = _dt.now(tz=UTC).replace(microsecond=0)

        class _T:
            city = "Shanghai"; target_date = (cycle.date()).isoformat(); metric = "high"
            latitude = 31.23; longitude = 121.47; timezone_name = "Asia/Shanghai"
            lead_days = 1

        def _single(**_kw):
            return 25.0

        def _prev(**_kw):
            return 24.0

        report = download_bayes_precision_fusion_extra_raw_inputs(
            forecast_db=Path(":memory:"),
            cycle=cycle,
            targets=[_T()],
            single_runs_fetch=_single,
            previous_runs_fetch=_prev,
        )
        assert report["status"].startswith("BAYES_PRECISION_FUSION_EXTRA")
        assert captured, "at least one row should have been staged"
        for r in captured:
            avail = _dt.fromisoformat(str(r["source_available_at"]).replace("Z", "+00:00"))
            cap = _dt.fromisoformat(str(r["captured_at"]).replace("Z", "+00:00"))
            assert avail <= cap + timedelta(seconds=1), (
                "source_available_at must be proof-of-possession bound (<= captured_at), not a "
                "synthetic future cycle+14h"
            )
    finally:
        mod._persist_chunk_with_lock_retry = orig  # type: ignore[assignment]
