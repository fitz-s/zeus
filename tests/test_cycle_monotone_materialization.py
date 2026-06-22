# Created: 2026-06-12
# Last reused or audited: 2026-06-19 (external review FINDING 2: per-family materializable-cycle
#   gate + typed leg-artifact-missing reason)
# Lifecycle: created=2026-06-12; last_reviewed=2026-06-19; last_reused=2026-06-19
# Purpose: Relationship tests for consumed-cycle monotonicity and single-family BPF reseed repair.
# Reuse: Run when replacement cycle-advance, materialization reseed, or freshness gates change.
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
    family_materializable_cycle,
    freshest_materializable_cycle,
    scope_needs_cycle_advance,
)
import src.data.replacement_cycle_advance_trigger as cycle_advance
from src.data.replacement_forecast_source_run_identity import (
    expected_replacement_dependency_identity_by_role,
)
from src.data.replacement_forecast_materializer import (
    SOURCE_ID as MAT_SOURCE_ID,
    _cycle_monotone_block_reasons,
)
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

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
    ensure_replacement_forecast_live_schema(conn)
    return conn


def _insert_posterior(conn: sqlite3.Connection, *, city: str, target_date: str, metric: str,
                      cycle_iso: str, computed_at: str) -> None:
    conn.execute(
        """
        INSERT INTO forecast_posteriors
            (source_id, product_id, data_version, city, target_date, temperature_metric,
             source_cycle_time, source_available_at, computed_at, q_json, q_lcb_json,
             posterior_method, dependency_source_run_ids_json, provenance_json,
             runtime_layer, training_allowed)
        VALUES (?, 'pid', 'dv', ?, ?, ?, ?, ?, ?, '{}', '{}', 'm', '{}', '{}', 'live', 0)
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
            elif name == "runtime_layer":
                values[name] = "live"
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


def test_freshest_materializable_cycle_uses_live_anchor_leg() -> None:
    """After AIFS removal, the freshest materializable cycle follows the live OM9 anchor leg."""
    conn = _conn()
    _insert_artifact(conn, source_id="openmeteo_ecmwf_ifs_9km", cycle_iso="2026-06-12T12:00:00+00:00")
    _insert_artifact(conn, source_id="ecmwf_aifs_ens", cycle_iso="2026-06-12T06:00:00+00:00")
    got = freshest_materializable_cycle(conn)
    assert got == datetime(2026, 6, 12, 12, tzinfo=UTC)


def test_freshest_materializable_cycle_none_when_anchor_missing() -> None:
    conn = _conn()
    _insert_artifact(conn, source_id="ecmwf_aifs_ens", cycle_iso="2026-06-12T06:00:00+00:00")
    # Retired AIFS artifacts alone cannot make a cycle materializable.
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
# (C) PER-FAMILY MATERIALIZABLE CYCLE (external review FINDING 2) — the universe-wide high-water
# mark is NOT the per-family materializability authority. A cycle is materializable for a SPECIFIC
# (city, target_date, metric) only when BOTH legs' raw artifacts exist for THAT scope. When a leg
# is missing for the family, the trigger must NOT falsely advance and must NOT silently skip — it
# records a typed CYCLE_LEG_ARTIFACT_MISSING reason so the gap is visible (ALWAYS-DECIDABLE).
# ===========================================================================
class _FakeManifest:
    """Minimal stand-in for RawForecastArtifactManifest carrying the fields family_materializable_
    cycle reads (source_id, data_version, source_cycle_time) plus city/target_date for the fake
    latest_manifest filter. Mirrors the real manifest's scope-filtered lookup without disk I/O."""
    def __init__(self, *, source_id: str, data_version: str, cycle: datetime, city: str, target_date: str) -> None:
        self.source_id = source_id
        self.data_version = data_version
        self.source_cycle_time = cycle
        self._city = city
        self._target_date = target_date


def _fake_latest_manifest(manifests, *, source_id, data_version, city, target_date):
    """Mirror _latest_manifest's contract: newest manifest matching source_id+data_version that is
    allowed for (city, target_date), or None when no manifest matches the scope+leg. This is the
    SAME scope-filtered selection the seed builder uses — it returns None precisely when THIS
    family lacks THIS leg's artifact, which is the gap family_materializable_cycle must detect."""
    cands = [
        m for m in manifests
        if m.source_id == source_id and m.data_version == data_version
        and m._city == city and m._target_date == target_date
    ]
    if not cands:
        return None
    return max(cands, key=lambda m: m.source_cycle_time)


def _legs_for(metric: str, *, city: str, target_date: str, cycle: datetime,
              include_anchor: bool = True) -> list[_FakeManifest]:
    ident = expected_replacement_dependency_identity_by_role(metric)
    anchor = ident["openmeteo_ifs9_anchor"]
    out: list[_FakeManifest] = []
    if include_anchor:
        out.append(_FakeManifest(source_id=anchor.source_id, data_version=anchor.data_version,
                                 cycle=cycle, city=city, target_date=target_date))
    return out


def test_family_materializable_cycle_anchor_present_returns_cycle() -> None:
    """Current live leg present for the family -> the family-scoped cycle is materializable."""
    cyc = datetime(2026, 6, 12, 12, tzinfo=UTC)
    manifests = _legs_for("high", city="CityA", target_date="2026-06-13", cycle=cyc)
    got, missing = family_materializable_cycle(
        manifests, city="CityA", target_date="2026-06-13", metric="high",
        expected_identity=expected_replacement_dependency_identity_by_role,
        latest_manifest=_fake_latest_manifest,
    )
    assert got == cyc
    assert missing == ()


def test_family_materializable_cycle_missing_anchor_blocks_and_names_gap() -> None:
    """THE FINDING after AIFS removal: OM9 12Z exists only for CityA. The universe-wide freshest
    cycle says 12Z is materializable, but family_materializable_cycle for CityB MUST return None
    and name the missing OM9 leg."""
    cyc = datetime(2026, 6, 12, 12, tzinfo=UTC)
    # Universe: CityA has the live OM9 leg at 12Z; CityB lacks it.
    manifests = (
        _legs_for("high", city="CityA", target_date="2026-06-13", cycle=cyc)
        + _legs_for("high", city="CityB", target_date="2026-06-13", cycle=cyc, include_anchor=False)
    )
    # CityA: fully materializable.
    got_a, missing_a = family_materializable_cycle(
        manifests, city="CityA", target_date="2026-06-13", metric="high",
        expected_identity=expected_replacement_dependency_identity_by_role,
        latest_manifest=_fake_latest_manifest,
    )
    assert got_a == cyc and missing_a == ()
    # CityB: NOT materializable — anchor leg absent for THIS family. No false advance.
    got_b, missing_b = family_materializable_cycle(
        manifests, city="CityB", target_date="2026-06-13", metric="high",
        expected_identity=expected_replacement_dependency_identity_by_role,
        latest_manifest=_fake_latest_manifest,
    )
    assert got_b is None, "CityB must NOT advance: it lacks the OM9 anchor leg at 12Z"
    assert len(missing_b) == 1
    role, src = missing_b[0]
    assert role == "openmeteo_ifs9_anchor"
    anchor_src = expected_replacement_dependency_identity_by_role("high")["openmeteo_ifs9_anchor"].source_id
    assert src == anchor_src, "the typed gap must name the exact missing leg source"


def test_cycle_advance_marker_reason_column_persists() -> None:
    """The cycle_advance_enqueues table carries a `reason` column so a leg-artifact gap is recorded
    as a typed, idempotent row (CYCLE_LEG_ARTIFACT_MISSING:...) rather than a silent skip."""
    conn = _conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cycle_advance_enqueues)")}
    assert "reason" in cols, "cycle_advance_enqueues must have a reason column (FINDING 2)"
    reason = "CYCLE_LEG_ARTIFACT_MISSING:openmeteo_ecmwf_ifs_9km@2026-06-12T12:00:00+00:00"
    conn.execute(
        """
        INSERT INTO cycle_advance_enqueues
            (enqueued_at, city, target_date, metric, consumed_cycle_time, target_cycle_time,
             held_position, seed_file, reason)
        VALUES ('t', 'CityB', '2026-06-13', 'high', '2026-06-12T06:00:00+00:00',
                '2026-06-12T12:00:00+00:00', 0, NULL, ?)
        """,
        (reason,),
    )
    conn.commit()
    row = conn.execute(
        "SELECT seed_file, reason FROM cycle_advance_enqueues WHERE city = 'CityB'"
    ).fetchone()
    assert row["seed_file"] is None, "a gap row carries no seed_file (it never materialized)"
    assert row["reason"] == reason


def test_cycle_advance_gap_marker_heals_to_seed_when_artifact_arrives() -> None:
    """A typed missing-leg marker is not terminal. When the same target cycle becomes
    materializable, recording the seed updates the gap row in place under the UNIQUE scope key."""
    conn = _conn()
    target_cycle = "2026-06-12T12:00:00+00:00"
    reason = f"CYCLE_LEG_ARTIFACT_MISSING:openmeteo_ecmwf_ifs_9km@{target_cycle}"
    conn.execute(
        """
        INSERT INTO cycle_advance_enqueues
            (enqueued_at, city, target_date, metric, consumed_cycle_time, target_cycle_time,
             held_position, seed_file, reason)
        VALUES ('t', 'CityB', '2026-06-13', 'high', '2026-06-12T06:00:00+00:00',
                ?, 0, NULL, ?)
        """,
        (target_cycle, reason),
    )
    conn.commit()
    assert cycle_advance._already_enqueued(
        conn,
        city="CityB",
        target_date="2026-06-13",
        metric="high",
        target_cycle_iso=target_cycle,
    ) is False

    inserted = cycle_advance._record_enqueue(
        conn,
        city="CityB",
        target_date="2026-06-13",
        metric="high",
        consumed_cycle_iso="2026-06-12T06:00:00+00:00",
        target_cycle_iso=target_cycle,
        held_position=True,
        seed_file="CityB.seed.json",
        reason=None,
    )
    conn.commit()
    assert inserted is True
    row = conn.execute(
        "SELECT held_position, seed_file, reason FROM cycle_advance_enqueues WHERE city = 'CityB'"
    ).fetchone()
    assert row["held_position"] == 1
    assert row["seed_file"] == "CityB.seed.json"
    assert row["reason"] is None


def test_day0_observed_extreme_reseed_can_replace_moved_seed_file(tmp_path) -> None:
    """A prior seed moved out of the live queue is not terminal for Day0 repair.

    This is the automatic recovery path for a seed that reached the queue but
    later failed materialization with DAY0_OBSERVED_EXTREME_REQUIRED. Once the
    monitor has a real observed extreme, the same family/cycle may rewrite the
    idempotency row with a fresh seed instead of staying stuck at
    CYCLE_ADVANCE_ALREADY_ENQUEUED.
    """
    conn = _conn()
    target_cycle = "2026-06-12T12:00:00+00:00"
    moved_seed = tmp_path / "seed_failed" / "CityB.old.json"
    conn.execute(
        """
        INSERT INTO cycle_advance_enqueues
            (enqueued_at, city, target_date, metric, consumed_cycle_time, target_cycle_time,
             held_position, seed_file, reason)
        VALUES ('t', 'CityB', '2026-06-13', 'high', 'NO_LIVE_POSTERIOR',
                ?, 0, ?, 'MISSING_LIVE_POSTERIOR')
        """,
        (target_cycle, str(moved_seed)),
    )
    conn.commit()

    assert cycle_advance._already_enqueued(
        conn,
        city="CityB",
        target_date="2026-06-13",
        metric="high",
        target_cycle_iso=target_cycle,
        allow_missing_seed_file_reenqueue=True,
    ) is False

    new_seed = tmp_path / "seeds" / "CityB.new.json"
    new_seed.parent.mkdir()
    new_seed.write_text("{}", encoding="utf-8")
    replaced = cycle_advance._record_enqueue(
        conn,
        city="CityB",
        target_date="2026-06-13",
        metric="high",
        consumed_cycle_iso="NO_LIVE_POSTERIOR",
        target_cycle_iso=target_cycle,
        held_position=True,
        seed_file=str(new_seed),
        reason="MISSING_LIVE_POSTERIOR",
        replace_existing_seed_file=True,
    )
    conn.commit()

    assert replaced is True
    row = conn.execute(
        "SELECT held_position, seed_file, reason FROM cycle_advance_enqueues WHERE city = 'CityB'"
    ).fetchone()
    assert row["held_position"] == 1
    assert row["seed_file"] == str(new_seed)
    assert row["reason"] == "MISSING_LIVE_POSTERIOR"


def test_held_marker_with_moved_seed_reheals_without_day0_optin(tmp_path) -> None:
    """LIVE FREEZE FIX (2026-06-21): a HELD position whose materialization seed was built then
    processed/moved out of the live queue but produced NO posterior (the single_runs serving race
    -> BLOCKED on REQUIREMENTS_NOT_MET) must be re-enqueueable WITHOUT the caller opting in via a
    day0-observed-extreme. Otherwise the held belief freezes permanently (Panama City 2026-06-22
    stuck at the 18:00 cycle for 13h+ -> BELIEF_AUTHORITY_FAULT fail-closed HOLD -> reversal exit
    starved -> 'observe but not act'). Money-at-risk held rows re-heal a moved seed automatically."""
    conn = _conn()
    target_cycle = "2026-06-21T06:00:00+00:00"
    moved_seed = tmp_path / "seeds_processed" / "PanamaCity.moved.json"  # processed out of seeds/
    conn.execute(
        """INSERT INTO cycle_advance_enqueues
           (enqueued_at, city, target_date, metric, consumed_cycle_time, target_cycle_time,
            held_position, seed_file, reason)
           VALUES ('t','PanamaCity','2026-06-22','high','2026-06-20T18:00:00+00:00', ?, 1, ?, NULL)""",
        (target_cycle, str(moved_seed)),
    )
    conn.commit()
    assert cycle_advance._already_enqueued(
        conn, city="PanamaCity", target_date="2026-06-22", metric="high",
        target_cycle_iso=target_cycle,
    ) is False, "held marker with a moved/missing seed must re-heal (no permanent belief freeze)"


def test_held_marker_with_present_seed_still_suppresses(tmp_path) -> None:
    """CHURN GUARD: a held marker whose seed file is STILL PRESENT (validly pending in the queue,
    not yet processed) must NOT re-enqueue — only a moved/missing seed re-heals. This keeps the
    re-heal bounded so a pending or already-materialized cycle never rebuilds seeds each tick."""
    conn = _conn()
    target_cycle = "2026-06-21T06:00:00+00:00"
    present_seed = tmp_path / "PanamaCity.pending.json"
    present_seed.write_text("{}", encoding="utf-8")
    conn.execute(
        """INSERT INTO cycle_advance_enqueues
           (enqueued_at, city, target_date, metric, consumed_cycle_time, target_cycle_time,
            held_position, seed_file, reason)
           VALUES ('t','PanamaCity','2026-06-22','high','2026-06-20T18:00:00+00:00', ?, 1, ?, NULL)""",
        (target_cycle, str(present_seed)),
    )
    conn.commit()
    assert cycle_advance._already_enqueued(
        conn, city="PanamaCity", target_date="2026-06-22", metric="high",
        target_cycle_iso=target_cycle,
    ) is True, "a held marker with a present (pending) seed must suppress re-enqueue (no churn)"


def test_nonheld_marker_with_moved_seed_still_suppresses(tmp_path) -> None:
    """SCOPE GUARD: the auto re-heal is for MONEY-AT-RISK held rows only. A non-held marker with a
    moved seed keeps the prior behavior (suppress) unless the caller explicitly opts in via
    allow_missing_seed_file_reenqueue — the held auto-heal must not silently widen non-held churn."""
    conn = _conn()
    target_cycle = "2026-06-21T06:00:00+00:00"
    moved_seed = tmp_path / "gone" / "CityX.moved.json"
    conn.execute(
        """INSERT INTO cycle_advance_enqueues
           (enqueued_at, city, target_date, metric, consumed_cycle_time, target_cycle_time,
            held_position, seed_file, reason)
           VALUES ('t','CityX','2026-06-22','high','2026-06-20T18:00:00+00:00', ?, 0, ?, NULL)""",
        (target_cycle, str(moved_seed)),
    )
    conn.commit()
    assert cycle_advance._already_enqueued(
        conn, city="CityX", target_date="2026-06-22", metric="high",
        target_cycle_iso=target_cycle,
    ) is True, "non-held marker with a moved seed must keep prior suppress behavior"


def test_record_enqueue_replaces_moved_seed_for_held_position() -> None:
    """A held re-enqueue must REPLACE an existing seed-built marker (the moved/BLOCKED row), not be
    ignored as ALREADY_ENQUEUED. Without auto-replace for held rows the re-heal in _already_enqueued
    cannot complete (INSERT OR IGNORE no-ops and the default UPDATE only heals a NULL-seed gap)."""
    conn = _conn()
    target_cycle = "2026-06-21T06:00:00+00:00"
    conn.execute(
        """INSERT INTO cycle_advance_enqueues
           (enqueued_at, city, target_date, metric, consumed_cycle_time, target_cycle_time,
            held_position, seed_file, reason)
           VALUES ('t','PanamaCity','2026-06-22','high','2026-06-20T18:00:00+00:00', ?, 1,
                   'PanamaCity.old.json', NULL)""",
        (target_cycle,),
    )
    conn.commit()
    replaced = cycle_advance._record_enqueue(
        conn, city="PanamaCity", target_date="2026-06-22", metric="high",
        consumed_cycle_iso="2026-06-20T18:00:00+00:00", target_cycle_iso=target_cycle,
        held_position=True, seed_file="PanamaCity.new.json", reason=None,
    )
    conn.commit()
    assert replaced is True, "a held re-enqueue must replace the prior seed-built marker row"
    row = conn.execute(
        "SELECT seed_file FROM cycle_advance_enqueues WHERE city='PanamaCity'"
    ).fetchone()
    assert row["seed_file"] == "PanamaCity.new.json", "the marker must carry the fresh seed"


def test_single_family_reseed_materializes_missing_posterior(tmp_path, monkeypatch) -> None:
    """Always-decidable repair: a held family with no BPF posterior is a first materialization,
    not CYCLE_ADVANCE_NOT_NEEDED."""
    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    cycle = datetime(2026, 6, 18, 12, tzinfo=UTC)
    _insert_artifact(
        conn,
        source_id="openmeteo_ecmwf_ifs_9km",
        cycle_iso=cycle.isoformat(),
    )
    conn.close()

    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (cycle, ()),
    )

    def _fake_build_seed(_conn_arg, **kwargs):
        path = Path(kwargs["seed_path"]) / "Shanghai.2026-06-19.high.seed.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"upgrade_trigger": kwargs.get("upgrade_trigger")}),
            encoding="utf-8",
        )
        return path

    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", _fake_build_seed)

    report = cycle_advance.enqueue_single_family_cycle_advance_reseed(
        forecast_db=db_path,
        seed_dir=tmp_path / "seeds",
        raw_manifest_dir=tmp_path / "raw",
        city="Shanghai",
        target_date="2026-06-19",
        metric="high",
        computed_at=datetime(2026, 6, 19, 1, tzinfo=UTC),
    )

    assert report["status"] == "CYCLE_ADVANCE_FIRST_MATERIALIZATION_ENQUEUED"
    assert report["enqueued"] is True
    seed_file = Path(str(report["seed_file"]))
    assert json.loads(seed_file.read_text(encoding="utf-8")) == {
        "upgrade_trigger": "missing_live_posterior_reseed",
    }

    check = sqlite3.connect(db_path)
    check.row_factory = sqlite3.Row
    row = check.execute(
        """
        SELECT consumed_cycle_time, target_cycle_time, held_position, seed_file, reason
        FROM cycle_advance_enqueues
        WHERE city = 'Shanghai' AND target_date = '2026-06-19' AND metric = 'high'
        """
    ).fetchone()
    check.close()
    assert row["consumed_cycle_time"] == "NO_LIVE_POSTERIOR"
    assert row["target_cycle_time"] == cycle.isoformat()
    assert row["held_position"] == 0
    assert row["seed_file"] == str(seed_file)
    assert row["reason"] == "MISSING_LIVE_POSTERIOR"


def test_single_family_monitor_reseed_promotes_existing_enqueue_to_held_priority(
    tmp_path, monkeypatch
) -> None:
    """A monitor-owned stale-belief repair must not stay behind a non-held idempotency row."""
    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    consumed = datetime(2026, 6, 19, 6, tzinfo=UTC)
    target = datetime(2026, 6, 20, 0, tzinfo=UTC)
    _insert_artifact(
        conn,
        source_id="openmeteo_ecmwf_ifs_9km",
        cycle_iso=target.isoformat(),
    )
    _insert_posterior(
        conn,
        city="Kuala Lumpur",
        target_date="2026-06-21",
        metric="high",
        cycle_iso=consumed.isoformat(),
        computed_at="2026-06-20T00:03:09+00:00",
    )
    conn.execute(
        """
        INSERT INTO cycle_advance_enqueues
            (enqueued_at, city, target_date, metric, consumed_cycle_time,
             target_cycle_time, held_position, seed_file, reason)
        VALUES (?, 'Kuala Lumpur', '2026-06-21', 'high', ?, ?, 0, ?, NULL)
        """,
        (
            "2026-06-20T05:54:42+00:00",
            consumed.isoformat(),
            target.isoformat(),
            str(tmp_path / "seeds" / "Kuala_Lumpur.2026-06-21.high.seed.json"),
        ),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (target, ()),
    )

    report = cycle_advance.enqueue_single_family_cycle_advance_reseed(
        forecast_db=db_path,
        seed_dir=tmp_path / "seeds",
        raw_manifest_dir=tmp_path / "raw",
        city="Kuala Lumpur",
        target_date="2026-06-21",
        metric="high",
        computed_at=datetime(2026, 6, 20, 7, tzinfo=UTC),
        held_position=True,
    )

    assert report["status"] == "CYCLE_ADVANCE_ALREADY_ENQUEUED"
    assert report["held_position"] is True
    assert report["held_priority_promoted"] is True
    check = sqlite3.connect(db_path)
    check.row_factory = sqlite3.Row
    row = check.execute(
        """
        SELECT held_position
        FROM cycle_advance_enqueues
        WHERE city = 'Kuala Lumpur' AND target_date = '2026-06-21' AND metric = 'high'
        """
    ).fetchone()
    check.close()
    assert row["held_position"] == 1


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
