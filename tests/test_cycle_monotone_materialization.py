# Created: 2026-06-12
# Last reused or audited: 2026-10-02 (exact-request LOW/HIGH cert supersession); 2026-09-15 (causal baseline completion witness;
#   external review FINDING 2: per-family materializable-cycle
#   gate + typed leg-artifact-missing reason)
# Lifecycle: created=2026-06-12; last_reviewed=2026-10-02; last_reused=2026-10-02
#   (held re-heal: 30-min cooldown replaced by the input-identity fence;
#   worker ERROR is fenced (input verdict) or owner-retained (transient), never respawned)
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
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

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
from src.data.openmeteo_ecmwf_ifs9_anchor import (
    HIGH_DATA_VERSION as OPENMETEO_HIGH_DATA_VERSION,
    PRODUCT_ID as OPENMETEO_PRODUCT_ID,
    SOURCE_ID as OPENMETEO_SOURCE_ID,
)
from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest
from src.data.replacement_forecast_seed_discovery import _latest_manifest
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

UTC = timezone.utc



def _blocked_evidence(db, argv) -> dict:
    """What a real worker reports for an evidenced computation BLOCKED, as the
    materializer builds it: a supported reason (the stale anchor cycle) with the
    request's scope and its two source_run possession rows. The fixture requests
    declare an anchor cycle older than the cycle-age bound, so the stale-cycle
    predicate is true for them and stays true for every later prospective clock."""
    import sqlite3 as _sqlite3
    from types import SimpleNamespace

    from src.data.materialization_block_evidence import STALE_CYCLE, blocked_evidence

    request = json.loads(Path(argv[argv.index("--input-json") + 1]).read_text())
    conn = _sqlite3.connect(str(db))  # the queue's forecasts DB (created if absent)
    try:
        return blocked_evidence(conn, SimpleNamespace(**{
            key: request.get(key) for key in (
                "city", "target_date", "temperature_metric",
                "baseline_source_run_id", "openmeteo_source_run_id",
            )
        }), STALE_CYCLE)
    finally:
        conn.close()


def _consumed_witness(argv) -> dict:
    """What a real worker reports having read: at least its request file."""
    from scripts.materialize_replacement_forecast_live import _ConsumedInputs, _StageReceipt

    request = Path(argv[argv.index("--input-json") + 1])
    consumed = _ConsumedInputs(_StageReceipt(request, None).attempt_id)  # the parent's claim id
    consumed.read(request, role="request")
    return consumed.witness()

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


def _openmeteo_manifest_for_test(
    path: Path,
    *,
    cycle: datetime,
    city: str = "Buenos Aires",
    target_date: str = "2026-06-24",
) -> RawForecastArtifactManifest:
    precision = path.with_name(path.stem + ".precision.json")
    precision.write_text("{}", encoding="utf-8")
    return RawForecastArtifactManifest.from_file(
        path,
        source_id=OPENMETEO_SOURCE_ID,
        product_id=OPENMETEO_PRODUCT_ID,
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        source_cycle_time=cycle,
        source_available_at=cycle + timedelta(minutes=1),
        captured_at=cycle + timedelta(minutes=2),
        request_url="https://single-runs-api.open-meteo.com/v1/forecast",
        request_params={"run": cycle.isoformat()},
        product_metadata={
            "artifact_class": "openmeteo_ecmwf_ifs9_anchor_current_targets",
            "city": city,
            "target_date": target_date,
            "city_timezone": "America/Argentina/Buenos_Aires",
            "metric": "high",
            "forecast_hours": 120,
            "openmeteo_payload_json": str(path),
            "precision_metadata_json": str(precision),
        },
    )


def test_latest_manifest_rejects_corrupt_openmeteo_payload(tmp_path: Path) -> None:
    corrupt = tmp_path / "openmeteo_Buenos_Aires_2026-06-24_high_20260624T000000Z.json"
    corrupt.write_text('{"hourly": {}}\n}\n', encoding="utf-8")
    valid = tmp_path / "openmeteo_Buenos_Aires_2026-06-24_high_20260624T060000Z.json"
    valid.write_text(
        '{"hourly": {"time": ["2026-06-24T12:00"], "temperature_2m": [10.0]}}\n',
        encoding="utf-8",
    )

    corrupt_manifest = _openmeteo_manifest_for_test(
        corrupt,
        cycle=datetime(2026, 6, 24, 0, tzinfo=UTC),
    )
    valid_manifest = _openmeteo_manifest_for_test(
        valid,
        cycle=datetime(2026, 6, 24, 6, tzinfo=UTC),
    )

    selected = _latest_manifest(
        (corrupt_manifest, valid_manifest),
        source_id=OPENMETEO_SOURCE_ID,
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        city="Buenos Aires",
        target_date="2026-06-24",
        city_timezone="America/Argentina/Buenos_Aires",
    )

    assert selected is valid_manifest
    assert (
        _latest_manifest(
            (corrupt_manifest,),
            source_id=OPENMETEO_SOURCE_ID,
            data_version=OPENMETEO_HIGH_DATA_VERSION,
            city="Buenos Aires",
            target_date="2026-06-24",
            city_timezone="America/Argentina/Buenos_Aires",
        )
        is None
    )


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


def _fake_latest_manifest(
    manifests,
    *,
    source_id,
    data_version,
    city,
    target_date,
    city_timezone=None,
):
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


def test_family_materializable_cycle_uses_eligible_ens_carrier_with_newer_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OM availability admits the scope; the returned posterior carrier is ENS's own clock."""
    carrier = datetime(2026, 6, 12, 6, tzinfo=UTC)
    newest_anchor = datetime(2026, 6, 12, 12, tzinfo=UTC)
    manifests = _legs_for(
        "high", city="CityA", target_date="2026-06-13", cycle=newest_anchor
    )
    monkeypatch.setattr(
        "src.data.replacement_input_hwm.latest_eligible_ensemble_input_cycle",
        lambda *_args, **_kwargs: carrier,
    )
    got, missing = family_materializable_cycle(
        _conn(),
        manifests,
        city="CityA",
        target_date="2026-06-13",
        metric="high",
        decision_time=newest_anchor,
        expected_identity=expected_replacement_dependency_identity_by_role,
        latest_manifest=_fake_latest_manifest,
    )
    assert got == carrier
    assert missing == ()


def test_cycle_advance_bounds_baseline_selection_by_ens_carrier(
    tmp_path: Path,
) -> None:
    carrier = datetime(2026, 6, 12, 6, tzinfo=UTC)
    anchor_cycle = datetime(2026, 6, 12, 12, tzinfo=UTC)
    anchor_identity = expected_replacement_dependency_identity_by_role("high")[
        "openmeteo_ifs9_anchor"
    ]
    manifest = SimpleNamespace(
        source_id=anchor_identity.source_id,
        data_version=anchor_identity.data_version,
        source_cycle_time=anchor_cycle,
        artifact_path="openmeteo.json",
        product_metadata={},
    )
    selected: dict[str, object] = {}
    built: dict[str, object] = {}
    written: list[dict[str, object]] = []

    def latest_coverage(_conn, **kwargs):
        selected.update(kwargs)
        return {"source_run_id": "causal-baseline"}

    def build_seed(**kwargs):
        built.update(kwargs)
        return SimpleNamespace(
            ok=True,
            seed={
                "ready": True,
                "baseline_source_run_id": "causal-baseline",
            },
        )

    conn = sqlite3.connect(":memory:")
    result = cycle_advance._build_and_write_advance_seed(
        conn,
        city="Dallas",
        target_date="2026-06-12",
        metric="high",
        manifests=(manifest,),
        raw_dir=tmp_path,
        seed_path=tmp_path,
        computed_at=datetime(2026, 6, 12, 12, tzinfo=UTC),
        carrier_cycle_time=carrier,
        build_seed=build_seed,
        latest_baseline_coverage=latest_coverage,
        market_bins=lambda *_args, **_kwargs: ({"bin": "32C"},),
        write_seed=lambda _path, payload: written.append(dict(payload)),
        latest_manifest=lambda *_args, **_kwargs: manifest,
        manifest_path_value=lambda _manifest, key: (
            "precision.json" if key == "precision_metadata_json" else None
        ),
        manifest_base_dir=lambda *_args, **_kwargs: tmp_path,
        resolve_path=lambda path, **_kwargs: path,
        seed_name=lambda *_args, **_kwargs: "seed.json",
        expected_identity=expected_replacement_dependency_identity_by_role,
        required_baseline_source_run_id="causal-baseline",
    )

    assert result == tmp_path / "seed.json"
    assert selected["not_after_source_cycle_time"] == carrier
    assert selected["as_of_time"] == datetime(2026, 6, 12, 12, tzinfo=UTC)
    assert built["carrier_cycle_time"] == carrier
    assert written == [
        {
            "ready": True,
            "baseline_source_run_id": "causal-baseline",
            "upgrade_trigger": "newer_cycle_ingested",
        }
    ]
    conn.close()


def test_day0_carrier_filter_keeps_newer_independent_openmeteo_manifest() -> None:
    """ENS06 Day0 redecision may still use the later eligible OM12 provider input."""
    ens06 = datetime(2026, 6, 12, 6, tzinfo=UTC)
    om00 = _legs_for(
        "high",
        city="CityA",
        target_date="2026-06-13",
        cycle=datetime(2026, 6, 12, 0, tzinfo=UTC),
    )[0]
    om12 = _legs_for(
        "high",
        city="CityA",
        target_date="2026-06-13",
        cycle=datetime(2026, 6, 12, 12, tzinfo=UTC),
    )[0]

    assert cycle_advance._manifests_through_cycle((om00, om12), target_cycle=ens06) == (
        om00,
        om12,
    )


def test_family_materializable_cycle_missing_anchor_blocks_and_names_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE FINDING after AIFS removal: OM9 12Z exists only for CityA. The universe-wide freshest
    cycle says 12Z is materializable, but family_materializable_cycle for CityB MUST return None
    and name the missing OM9 leg."""
    cyc = datetime(2026, 6, 12, 12, tzinfo=UTC)
    monkeypatch.setattr(
        "src.data.replacement_input_hwm.latest_eligible_ensemble_input_cycle",
        lambda *_args, **_kwargs: cyc,
    )
    # Universe: CityA has the live OM9 leg at 12Z; CityB lacks it.
    manifests = (
        _legs_for("high", city="CityA", target_date="2026-06-13", cycle=cyc)
        + _legs_for("high", city="CityB", target_date="2026-06-13", cycle=cyc, include_anchor=False)
    )
    # CityA: fully materializable.
    got_a, missing_a = family_materializable_cycle(
        _conn(),
        manifests,
        city="CityA",
        target_date="2026-06-13",
        metric="high",
        decision_time=cyc,
        expected_identity=expected_replacement_dependency_identity_by_role,
        latest_manifest=_fake_latest_manifest,
    )
    assert got_a == cyc and missing_a == ()
    # CityB: NOT materializable — anchor leg absent for THIS family. No false advance.
    got_b, missing_b = family_materializable_cycle(
        _conn(),
        manifests,
        city="CityB",
        target_date="2026-06-13",
        metric="high",
        decision_time=cyc,
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


def test_batch_cycle_advance_enqueues_day0_with_observed_extreme(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batch redecision must not skip Day0 when canonical observed-extreme truth exists."""

    db_path = tmp_path / "forecast.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    conn.close()

    row = SimpleNamespace(
        city="Amsterdam",
        target_date="2026-07-04",
        temperature_metric="high",
        day0_observed_extreme_required=True,
    )
    plan = SimpleNamespace(status="OK", rows=(row,), reason_codes=())
    target_cycle = datetime(2026, 7, 3, 12, tzinfo=UTC)
    seed_dir = tmp_path / "seeds"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    built: dict[str, object] = {}

    monkeypatch.setattr(
        "src.data.replacement_forecast_current_target_plan.build_replacement_forecast_current_target_plan",
        lambda *args, **kwargs: plan,
    )
    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery._load_manifests",
        lambda *args, **kwargs: (),
    )
    monkeypatch.setattr(cycle_advance, "freshest_materializable_cycle", lambda _conn: target_cycle)
    monkeypatch.setattr(
        cycle_advance,
        "scope_needs_cycle_advance",
        lambda *args, **kwargs: {
            "needs_advance": True,
            "consumed_cycle": "2026-07-03T00:00:00+00:00",
            "target_cycle": target_cycle.isoformat(),
        },
    )
    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (target_cycle, ()),
    )
    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery._day0_observed_extreme_seed_payload",
        lambda **kwargs: {
            "day0_observed_extreme_c": 15.0,
            "day0_observed_extreme_source": "durable_observation_instants",
            "day0_observed_extreme_observation_time": "2026-07-03T22:00:00+00:00",
            "day0_observed_extreme_sample_count": 1,
            "day0_observed_extreme_unit": "C",
        },
    )

    def _fake_build_seed(*args, **kwargs):
        built.update(kwargs)
        seed_file = Path(
            kwargs.get("output_path") or seed_dir / "Amsterdam.2026-07-04.high.json"
        )
        seed_file.parent.mkdir(parents=True, exist_ok=True)
        seed_file.write_text("{}", encoding="utf-8")
        return seed_file

    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", _fake_build_seed)

    report = cycle_advance.enqueue_cycle_advance_reseeds(
        forecast_db=db_path,
        seed_dir=seed_dir,
        raw_manifest_dir=raw_dir,
        computed_at=datetime(2026, 7, 4, 1, tzinfo=UTC),
        limit=5,
    )

    assert report["seeds_enqueued"] == 1
    assert report["day0_skipped"] == 0
    assert built["day0_observed_extreme_c"] == 15.0
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        marker = conn.execute(
            """
            SELECT seed_file, day0_observed_extreme_observation_time
            FROM cycle_advance_enqueues
            WHERE city='Amsterdam' AND target_date='2026-07-04' AND metric='high'
            """
        ).fetchone()
    finally:
        conn.close()
    assert marker is not None
    assert marker["seed_file"]
    assert marker["day0_observed_extreme_observation_time"] == "2026-07-03T22:00:00+00:00"


def test_broad_triggers_share_one_plan_and_report_as_if_built_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One broad batch builds the current-target plan once for both triggers
    (about 105 s each on live 10-02); each trigger's report is identical to the
    report it gives with its own independently built plan."""
    import shutil

    import src.data.replacement_forecast_current_target_plan as plan_module
    import src.data.replacement_forecast_production as production

    row = SimpleNamespace(city="Amsterdam", target_date="2026-07-04",
                          temperature_metric="high", day0_observed_extreme_required=False)
    builds: list[object] = []

    def plan_builder(*_args, **kwargs):
        builds.append(kwargs.get("now_utc"))
        return SimpleNamespace(status="OK", rows=(row,), reason_codes=())

    monkeypatch.setattr(plan_module, "build_replacement_forecast_current_target_plan", plan_builder)
    monkeypatch.setattr("src.data.replacement_forecast_seed_discovery._load_manifests",
                        lambda *args, **kwargs: ())
    target_cycle = datetime(2026, 7, 3, 12, tzinfo=UTC)
    monkeypatch.setattr(cycle_advance, "freshest_materializable_cycle", lambda _conn: target_cycle)
    monkeypatch.setattr(cycle_advance, "scope_needs_cycle_advance", lambda *a, **k: {
        "needs_advance": True, "consumed_cycle": "2026-07-03T00:00:00+00:00",
        "target_cycle": target_cycle.isoformat()})
    monkeypatch.setattr(cycle_advance, "family_materializable_cycle",
                        lambda *a, **k: (target_cycle, ()))

    def fake_seed(*_args, **kwargs):
        seed_file = Path(kwargs["output_path"])
        seed_file.parent.mkdir(parents=True, exist_ok=True)
        seed_file.write_text("{}", encoding="utf-8")
        return seed_file

    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", fake_seed)
    template = tmp_path / "template.db"
    conn = sqlite3.connect(template)
    ensure_replacement_forecast_live_schema(conn)
    conn.close()
    computed_at = datetime(2026, 7, 4, 1, tzinfo=UTC)

    def run(name: str, shared: bool) -> tuple[object, object]:
        root = tmp_path / name / "state" / "queue"
        root.mkdir(parents=True)
        db = tmp_path / name / "forecast.db"
        shutil.copy(template, db)
        cfg = {"forecast_db": db, "seed_dir": root / "seeds", "raw_manifest_dir": tmp_path / "raw"}
        snapshot = {"computed_at": computed_at}
        fusion = production._enqueue_fusion_upgrade_reseeds_if_needed(
            cfg, manifest_snapshot=snapshot if shared else dict(snapshot))
        cycle = production._enqueue_cycle_advance_reseeds_if_needed(
            cfg, manifest_snapshot=snapshot if shared else dict(snapshot))
        strip = lambda report: {k: v for k, v in (report or {}).items()
                                if k not in ("enqueued", "staging_durable_ancestor")}
        return strip(fusion), strip(cycle)

    independent = run("independent", shared=False)
    independent_builds = list(builds)
    builds.clear()
    shared = run("shared", shared=True)

    assert shared == independent
    assert independent[1]["seeds_enqueued"] == 1 and independent[0]["scopes_checked"] == 1
    assert len(independent_builds) == 2
    assert builds == [computed_at], "one plan per batch, built at the batch's own cut"


def test_committed_ens_wake_is_not_complete_on_placeholder_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching string in a non-certified posterior cannot end held replay."""

    db_path = tmp_path / "forecast.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    target_cycle = datetime(2026, 8, 23, 0, tzinfo=UTC)
    committed_run = "ecmwf_open_data:mx2t6_high:2026-08-23T00Z"
    _insert_posterior(
        conn,
        city="Cape Town",
        target_date="2026-08-24",
        metric="high",
        cycle_iso=target_cycle.isoformat(),
        computed_at="2026-08-23T07:00:00+00:00",
    )
    conn.execute(
        """
        UPDATE forecast_posteriors
           SET dependency_source_run_ids_json = ?
         WHERE city = 'Cape Town'
           AND target_date = '2026-08-24'
           AND temperature_metric = 'high'
        """,
        (json.dumps({"baseline_b0": committed_run}),),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        cycle_advance,
        "freshest_materializable_cycle",
        lambda _conn: target_cycle,
    )
    monkeypatch.setattr(
        cycle_advance,
        "scope_needs_cycle_advance",
        lambda *_args, **_kwargs: {
            "needs_advance": False,
            "consumed_cycle": target_cycle.isoformat(),
            "target_cycle": target_cycle.isoformat(),
        },
    )
    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *_args, **_kwargs: (target_cycle, ()),
    )
    monkeypatch.setattr(
        cycle_advance,
        "_newer_eligible_ensemble_cycle",
        lambda *_args, **_kwargs: None,
    )
    inspected = []
    monkeypatch.setattr(
        cycle_advance,
        "_superseded_baseline_seed_file",
        lambda *_args, **_kwargs: inspected.append(True) or None,
    )
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    report = cycle_advance.enqueue_cycle_advance_reseeds(
        forecast_db=db_path,
        seed_dir=tmp_path / "seeds",
        raw_manifest_dir=raw_dir,
        computed_at=datetime(2026, 8, 23, 12, tzinfo=UTC),
        limit=1,
        scopes=(("Cape Town", "2026-08-24", "high"),),
        manifests=(),
        causal_baseline_source_run_id=committed_run,
    )

    assert report["seeds_enqueued"] == 0
    assert inspected
    assert report["causal_baseline_already_consumed"] == 0
    assert not (tmp_path / "seeds").exists()


def test_committed_ens_reset_requires_certified_held_grade_at_decision_clock(monkeypatch) -> None:
    from src.data import replacement_forecast_bundle_reader
    from src.engine import position_belief

    conn = _conn()
    decision = datetime(2026, 9, 27, 16, 25, tzinfo=UTC)
    required = "ecmwf_open_data:mn2t6_low:2026-09-26T12Z:current-v3"
    candidate = {
        "source_cycle_time": "2026-09-26T12:00:00+00:00",
        "dependency_source_run_ids_json": json.dumps({"baseline_b0": required}),
        "runtime_layer": "live",
        "q_json": '{"bin":0.5}', "q_lcb_json": '{"bin":0.2}',
        "q_ucb_json": '{"bin":0.8}',
        "provenance_json": "{}",
    }
    certified = []

    def certificate(_conn, **kwargs):
        assert kwargs["decision_time"] == decision
        certified.append(kwargs)
        return candidate

    monkeypatch.setattr(position_belief, "_certified_replacement_posterior_row", certificate)
    grade = []

    def held_grade(row, *, authority_purpose):
        assert authority_purpose is replacement_forecast_bundle_reader.ReplacementForecastAuthorityPurpose.HELD_REDECISION
        assert row["city"] == "Hong Kong" and row["target_date"] == "2026-09-27"
        return {"current_v5": True} if grade else None

    monkeypatch.setattr(replacement_forecast_bundle_reader, "_live_grade_provenance", held_grade)

    def consumed():
        return cycle_advance._latest_posterior_consumes_causal_baseline(
            conn, city="Hong Kong", target_date="2026-09-27", metric="low",
            target_cycle_iso="2026-09-26T12:00:00+00:00",
            required_baseline_source_run_id=required, decision_time=decision,
        )

    assert consumed() is False  # certified row without current held shape is not RESET
    grade.append(True)
    assert consumed() is True
    candidate["q_json"] = '{"bin":0.9}'
    assert consumed() is False  # impossible posterior/bounds never complete the wake
    candidate["q_json"] = '{"bin":0.5}'
    candidate["dependency_source_run_ids_json"] = json.dumps({"baseline_b0": "old-v2"})
    assert consumed() is False
    assert len(certified) == 4
    conn.close()


@pytest.mark.parametrize(
    ("owner_state", "expected_enqueued", "expected_status"),
    (
        (cycle_advance._Day0EnqueueOwnerRequestState.INACTIVE, 1, "CYCLE_ADVANCE_TRIGGER"),
        (
            cycle_advance._Day0EnqueueOwnerRequestState.ACTIVE,
            0,
            "CYCLE_ADVANCE_CAUSAL_BASELINE_INCOMPLETE",
        ),
    ),
)
@pytest.mark.parametrize("after_local_day_end", (False, True))
def test_committed_ens_run_replaces_same_cycle_seed_with_older_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner_state: cycle_advance._Day0EnqueueOwnerRequestState,
    expected_enqueued: int,
    expected_status: str,
    after_local_day_end: bool,
) -> None:
    """A late exact ENS shape must not be deduped by an anchor-first cycle marker."""

    db_path = tmp_path / "forecast.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    cycle_advance._ensure_day0_conditioning_identity_column(conn)
    target_cycle = datetime(2026, 8, 23, 0, tzinfo=UTC)
    committed_run = "ecmwf_open_data:mx2t6_high:2026-08-23T00Z"
    conn.execute(
        "CREATE TABLE source_run (source_run_id TEXT PRIMARY KEY, source_cycle_time TEXT)"
    )
    conn.execute(
        """
        CREATE TABLE ensemble_snapshots (
            source_run_id TEXT, city TEXT, target_date TEXT,
            temperature_metric TEXT, source_id TEXT, model_version TEXT,
            authority TEXT, causality_status TEXT, boundary_ambiguous INTEGER,
            forecast_window_attribution_status TEXT,
            contributes_to_target_extrema INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO source_run (source_run_id, source_cycle_time) VALUES (?, ?)
        """,
        (committed_run, target_cycle.isoformat()),
    )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots VALUES (
            ?, 'Cape Town', '2026-08-23', 'high', 'ecmwf_open_data',
            'ecmwf_ens', 'VERIFIED', 'OK', 0,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', 1
        )
        """,
        (committed_run,),
    )
    old_seed = tmp_path / "seeds" / "Cape_Town.anchor-first.json"
    old_seed.parent.mkdir()
    old_seed.write_text(
        json.dumps(
            {
                "baseline_source_run_id": (
                    "ecmwf_open_data:mx2t6_high:2026-08-22T18Z"
                )
            }
        ),
        encoding="utf-8",
    )
    day0_payload = {
        "day0_observed_extreme_c": 14.0,
        "day0_observed_extreme_source": "aviationweather_metar",
        "day0_observed_extreme_observation_time": "2026-08-23T07:29:21+00:00",
        "day0_observed_extreme_sample_count": 18,
        "day0_observed_extreme_unit": "C",
    }
    day0_identity = cycle_advance._day0_conditioning_identity(
        source=day0_payload["day0_observed_extreme_source"],
        observation_time=day0_payload[
            "day0_observed_extreme_observation_time"
        ],
        observed_extreme_c=day0_payload["day0_observed_extreme_c"],
        unit=day0_payload["day0_observed_extreme_unit"],
    )
    conn.execute(
        """
        INSERT INTO cycle_advance_enqueues (
            enqueued_at, city, target_date, metric, consumed_cycle_time,
            target_cycle_time, held_position, seed_file,
            day0_observed_extreme_observation_time,
            day0_conditioning_identity_json
        ) VALUES ('2026-08-23T06:36:45+00:00', 'Cape Town', '2026-08-23',
                  'high', '2026-08-22T18:00:00+00:00', ?, 1, ?, ?, ?)
        """,
        (
            target_cycle.isoformat(),
            str(old_seed),
            day0_payload["day0_observed_extreme_observation_time"],
            day0_identity,
        ),
    )
    conn.commit()
    conn.close()

    seed_dir = tmp_path / "seeds"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    built: dict[str, object] = {}
    monkeypatch.setattr(
        cycle_advance,
        "freshest_materializable_cycle",
        lambda _conn: target_cycle,
    )
    monkeypatch.setattr(
        cycle_advance,
        "scope_needs_cycle_advance",
        lambda *args, **kwargs: {
            "needs_advance": True,
            "consumed_cycle": "2026-08-22T18:00:00+00:00",
            "target_cycle": target_cycle.isoformat(),
        },
    )
    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (target_cycle, ()),
    )
    if after_local_day_end:
        # Cape Town's 08-23 local day ends at 22:00Z. A chain-held family
        # remains a reduce-only redecision after that boundary, but only for
        # an exact committed source and same-date Day0 observation witness.
        monkeypatch.setattr(
            cycle_advance, "_held_position_families",
            lambda _conn: {("Cape Town", "2026-08-23", "high")},
        )
    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery._day0_observed_extreme_seed_payload",
        lambda **kwargs: day0_payload,
    )
    monkeypatch.setattr(
        cycle_advance,
        "_day0_enqueue_owner_request_check",
        lambda **kwargs: cycle_advance._Day0EnqueueOwnerRequestCheck(
            owner_state,
            (
                "ABSENT"
                if owner_state is cycle_advance._Day0EnqueueOwnerRequestState.INACTIVE
                else "ACTIVE"
            ),
        ),
    )

    def _fake_build_seed(*args, **kwargs):
        built.update(kwargs)
        seed_file = Path(kwargs["output_path"])
        seed_file.parent.mkdir(parents=True, exist_ok=True)
        seed_file.write_text(
            json.dumps({"baseline_source_run_id": committed_run}),
            encoding="utf-8",
        )
        return seed_file

    monkeypatch.setattr(
        cycle_advance,
        "_build_and_write_advance_seed",
        _fake_build_seed,
    )

    report = cycle_advance.enqueue_cycle_advance_reseeds(
        forecast_db=db_path,
        seed_dir=seed_dir,
        raw_manifest_dir=raw_dir,
        trades_db=(db_path if after_local_day_end else None),
        computed_at=datetime(2026, 8, 23, 22, 3, tzinfo=UTC)
        if after_local_day_end else datetime(2026, 8, 23, 7, 53, tzinfo=UTC),
        limit=1,
        scopes=(("Cape Town", "2026-08-23", "high"),),
        manifests=(),
        causal_baseline_source_run_id=committed_run,
    )

    assert report["status"] == expected_status
    assert report["seeds_enqueued"] == expected_enqueued
    assert report["already_enqueued"] == 0
    if owner_state is cycle_advance._Day0EnqueueOwnerRequestState.INACTIVE:
        assert built["required_baseline_source_run_id"] == committed_run
    else:
        assert built == {}
        assert report["causal_baseline_scope_failed"] == 1
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    marker = conn.execute(
        """
        SELECT seed_file FROM cycle_advance_enqueues
        WHERE city='Cape Town' AND target_date='2026-08-23' AND metric='high'
          AND target_cycle_time=?
        """,
        (target_cycle.isoformat(),),
    ).fetchone()
    conn.close()
    assert marker is not None
    if owner_state is cycle_advance._Day0EnqueueOwnerRequestState.INACTIVE:
        assert marker["seed_file"] != str(old_seed)
        assert (
            json.loads(Path(marker["seed_file"]).read_text())["baseline_source_run_id"]
            == committed_run
        )
    else:
        assert marker["seed_file"] == str(old_seed)


def test_committed_ens_wake_record_enqueue_dedup_counts_as_already_enqueued(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_record_enqueue`` returning False means a concurrent/prior enqueue
    already recorded the row (per its own docstring, via the UNIQUE index
    INSERT OR IGNORE) -- a dedup, not a failure. A committed-ENS-wake call
    (``causal_baseline_source_run_id`` set) must count this as
    ``already_enqueued``, never ``causal_baseline_scope_failed``."""

    db_path = tmp_path / "forecast.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    cycle_advance._ensure_day0_conditioning_identity_column(conn)
    target_cycle = datetime(2026, 8, 23, 0, tzinfo=UTC)
    committed_run = "ecmwf_open_data:mx2t6_high:2026-08-23T00Z"
    conn.execute(
        "CREATE TABLE source_run (source_run_id TEXT PRIMARY KEY, source_cycle_time TEXT)"
    )
    conn.execute(
        """
        CREATE TABLE ensemble_snapshots (
            source_run_id TEXT, city TEXT, target_date TEXT,
            temperature_metric TEXT, source_id TEXT, model_version TEXT,
            authority TEXT, causality_status TEXT, boundary_ambiguous INTEGER,
            forecast_window_attribution_status TEXT,
            contributes_to_target_extrema INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO source_run (source_run_id, source_cycle_time) VALUES (?, ?)
        """,
        (committed_run, target_cycle.isoformat()),
    )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots VALUES (
            ?, 'Cape Town', '2026-08-23', 'high', 'ecmwf_open_data',
            'ecmwf_ens', 'VERIFIED', 'OK', 0,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', 1
        )
        """,
        (committed_run,),
    )
    conn.commit()
    conn.close()

    seed_dir = tmp_path / "seeds"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    day0_payload = {
        "day0_observed_extreme_c": 14.0,
        "day0_observed_extreme_source": "aviationweather_metar",
        "day0_observed_extreme_observation_time": "2026-08-23T07:29:21+00:00",
        "day0_observed_extreme_sample_count": 18,
        "day0_observed_extreme_unit": "C",
    }
    monkeypatch.setattr(
        cycle_advance, "freshest_materializable_cycle", lambda _conn: target_cycle
    )
    monkeypatch.setattr(
        cycle_advance,
        "scope_needs_cycle_advance",
        lambda *args, **kwargs: {
            "needs_advance": True,
            "consumed_cycle": "2026-08-22T18:00:00+00:00",
            "target_cycle": target_cycle.isoformat(),
        },
    )
    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (target_cycle, ()),
    )
    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery._day0_observed_extreme_seed_payload",
        lambda **kwargs: day0_payload,
    )
    monkeypatch.setattr(
        cycle_advance,
        "_day0_enqueue_owner_request_check",
        lambda **kwargs: cycle_advance._Day0EnqueueOwnerRequestCheck(
            cycle_advance._Day0EnqueueOwnerRequestState.INACTIVE, "ABSENT"
        ),
    )

    def _fake_build_seed(*args, **kwargs):
        seed_file = Path(kwargs["output_path"])
        seed_file.parent.mkdir(parents=True, exist_ok=True)
        seed_file.write_text(
            json.dumps({"baseline_source_run_id": committed_run}),
            encoding="utf-8",
        )
        return seed_file

    monkeypatch.setattr(
        cycle_advance, "_build_and_write_advance_seed", _fake_build_seed
    )
    # Simulate a concurrent/prior enqueue winning the UNIQUE-index INSERT for
    # this exact (scope, target_cycle): _record_enqueue's own docstring says
    # this False return means "already recorded", not a failure.
    monkeypatch.setattr(
        cycle_advance, "_record_enqueue", lambda *args, **kwargs: False
    )

    report = cycle_advance.enqueue_cycle_advance_reseeds(
        forecast_db=db_path,
        seed_dir=seed_dir,
        raw_manifest_dir=raw_dir,
        computed_at=datetime(2026, 8, 23, 7, 53, tzinfo=UTC),
        limit=1,
        scopes=(("Cape Town", "2026-08-23", "high"),),
        manifests=(),
        causal_baseline_source_run_id=committed_run,
    )

    assert report["already_enqueued"] == 1
    assert report["causal_baseline_scope_failed"] == 0
    assert report["seeds_enqueued"] == 0
    assert report["status"] == "CYCLE_ADVANCE_TRIGGER"


def test_cycle_advance_does_not_enqueue_anchor_behind_eligible_ensemble(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 06Z family seed cannot heal a scope whose eligible ENS HWM is 12Z."""

    db_path = tmp_path / "forecast.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    conn.close()
    family_cycle = datetime(2026, 8, 30, 6, tzinfo=UTC)
    eligible_cycle = datetime(2026, 8, 30, 12, tzinfo=UTC)
    built = False

    monkeypatch.setattr(
        cycle_advance,
        "freshest_materializable_cycle",
        lambda _conn: eligible_cycle,
    )
    monkeypatch.setattr(
        cycle_advance,
        "scope_needs_cycle_advance",
        lambda *args, **kwargs: {
            "needs_advance": True,
            "consumed_cycle": family_cycle.isoformat(),
            "target_cycle": eligible_cycle.isoformat(),
        },
    )
    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (family_cycle, ()),
    )
    monkeypatch.setattr(
        "src.data.replacement_input_hwm.latest_eligible_ensemble_input_cycle",
        lambda *args, **kwargs: eligible_cycle,
    )
    monkeypatch.setattr(
        "src.data.replacement_forecast_seed_discovery._day0_observed_extreme_seed_payload",
        lambda **kwargs: {
            "day0_observed_extreme_c": 22.0,
            "day0_observed_extreme_source": "aviationweather_metar",
            "day0_observed_extreme_observation_time": "2026-08-30T10:00:00+00:00",
            "day0_observed_extreme_sample_count": 4,
            "day0_observed_extreme_unit": "C",
        },
    )

    def _unexpected_build(*args, **kwargs):
        nonlocal built
        built = True
        return tmp_path / "unexpected.json"

    monkeypatch.setattr(
        cycle_advance,
        "_build_and_write_advance_seed",
        _unexpected_build,
    )

    report = cycle_advance.enqueue_cycle_advance_reseeds(
        forecast_db=db_path,
        seed_dir=tmp_path / "seeds",
        raw_manifest_dir=tmp_path / "raw",
        computed_at=datetime(2026, 8, 30, 12, 30, tzinfo=UTC),
        limit=1,
        scopes=(("Moscow", "2026-08-31", "high"),),
        manifests=(),
    )

    assert report["family_cycle_behind_eligible_ensemble"] == 1
    assert report["seeds_enqueued"] == 0
    assert built is False
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM cycle_advance_enqueues").fetchone()[0] == 0
    finally:
        conn.close()


def test_same_cycle_baseline_seed_replacement_uses_exact_marker_cas() -> None:
    """A concurrent marker owner cannot be overwritten by a stale ENS wake."""

    conn = _conn()
    cycle_advance._ensure_day0_conditioning_identity_column(conn)
    observation_time = "2026-08-23T07:29:21+00:00"
    identity = cycle_advance._day0_conditioning_identity(
        source="aviationweather_metar",
        observation_time=observation_time,
        observed_extreme_c=14.0,
        unit="C",
    )
    conn.execute(
        """
        INSERT INTO cycle_advance_enqueues (
            enqueued_at, city, target_date, metric, consumed_cycle_time,
            target_cycle_time, held_position, seed_file,
            day0_observed_extreme_observation_time,
            day0_conditioning_identity_json
        ) VALUES ('2026-08-23T07:53:00+00:00', 'Cape Town', '2026-08-23',
                  'high', '2026-08-22T18:00:00+00:00',
                  '2026-08-23T00:00:00+00:00', 1, 'new-owner.json', ?, ?)
        """,
        (observation_time, identity),
    )

    assert not cycle_advance._record_enqueue(
        conn,
        city="Cape Town",
        target_date="2026-08-23",
        metric="high",
        consumed_cycle_iso="2026-08-22T18:00:00+00:00",
        target_cycle_iso="2026-08-23T00:00:00+00:00",
        held_position=True,
        seed_file="stale-wake.json",
        day0_observed_extreme_observation_time=observation_time,
        day0_observed_extreme_source="aviationweather_metar",
        day0_observed_extreme_c=14.0,
        day0_observed_extreme_unit="C",
        superseded_seed_file="old-owner.json",
    )
    assert conn.execute(
        "SELECT seed_file FROM cycle_advance_enqueues WHERE city='Cape Town'"
    ).fetchone()[0] == "new-owner.json"


def test_causal_owner_check_waits_through_busy_queue_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short queue claim cannot turn a committed ENS wake into a retry lottery."""

    request_dir = tmp_path / "requests"
    inflight_dir = tmp_path / "inflight"
    request_dir.mkdir()
    inflight_dir.mkdir()
    attempts = 0

    @contextmanager
    def _busy_once(_path):
        nonlocal attempts
        attempts += 1
        yield attempts > 1

    monkeypatch.setattr(
        "src.data.replacement_forecast_production."
        "_replacement_forecast_live_materialization_queue_config",
        lambda: {"request_dir": request_dir, "inflight_dir": inflight_dir},
    )
    monkeypatch.setattr(
        "src.data.replacement_forecast_live_materialization_queue._queue_lock",
        _busy_once,
    )

    check = cycle_advance._day0_enqueue_owner_request_check(
        city="Cape Town",
        target_date="2026-08-23",
        metric="high",
        target_cycle_iso="2026-08-23T00:00:00+00:00",
        seed_file="old-owner.json",
        identity=None,
        queue_lock_wait_seconds=0.2,
    )

    assert attempts == 2
    assert check.state is cycle_advance._Day0EnqueueOwnerRequestState.INACTIVE
    assert check.reason == "DAY0_ENQUEUE_OWNER_REQUEST_ABSENT"


def test_scoped_source_commit_enqueues_missing_live_posterior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A committed raw-input family must not wait forever for a vanished discovery seed."""

    db_path = tmp_path / "forecast.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    conn.close()

    target_cycle = datetime(2026, 7, 27, 6, tzinfo=UTC)
    seed_dir = tmp_path / "seeds"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    built: dict[str, object] = {}

    monkeypatch.setattr(
        cycle_advance,
        "freshest_materializable_cycle",
        lambda _conn: target_cycle,
    )
    monkeypatch.setattr(
        cycle_advance,
        "scope_needs_cycle_advance",
        lambda *args, **kwargs: {
            "needs_advance": False,
            "consumed_cycle": None,
            "target_cycle": None,
        },
    )
    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (target_cycle, ()),
    )

    def _fake_build_seed(*args, **kwargs):
        built.update(kwargs)
        seed_file = Path(
            kwargs.get("output_path") or seed_dir / "Austin.2026-07-28.high.json"
        )
        seed_file.parent.mkdir(parents=True, exist_ok=True)
        seed_file.write_text("{}", encoding="utf-8")
        return seed_file

    monkeypatch.setattr(
        cycle_advance,
        "_build_and_write_advance_seed",
        _fake_build_seed,
    )

    global_semantics = cycle_advance.enqueue_cycle_advance_reseeds(
        forecast_db=db_path,
        seed_dir=seed_dir,
        raw_manifest_dir=raw_dir,
        computed_at=datetime(2026, 7, 27, 16, tzinfo=UTC),
        limit=5,
        scopes=(("Austin", "2026-07-28", "high"),),
        manifests=(),
    )
    assert global_semantics["seeds_enqueued"] == 0
    assert built == {}

    report = cycle_advance.enqueue_cycle_advance_reseeds(
        forecast_db=db_path,
        seed_dir=seed_dir,
        raw_manifest_dir=raw_dir,
        computed_at=datetime(2026, 7, 27, 16, tzinfo=UTC),
        limit=5,
        scopes=(("Austin", "2026-07-28", "high"),),
        manifests=(),
        include_missing_posterior=True,
    )

    assert report["advances_detected"] == 0
    assert report["first_materializations_detected"] == 1
    assert report["first_materialization_seeds_enqueued"] == 1
    assert report["seeds_enqueued"] == 1
    assert built["upgrade_trigger"] == "missing_live_posterior_reseed"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        marker = conn.execute(
            """
            SELECT consumed_cycle_time, target_cycle_time, reason, seed_file
            FROM cycle_advance_enqueues
            WHERE city='Austin' AND target_date='2026-07-28' AND metric='high'
            """
        ).fetchone()
    finally:
        conn.close()
    assert marker is not None
    assert marker["consumed_cycle_time"] == "NO_LIVE_POSTERIOR"
    assert marker["target_cycle_time"] == target_cycle.isoformat()
    assert marker["reason"] == "MISSING_LIVE_POSTERIOR"
    assert marker["seed_file"]

    # A pending seed still suppresses duplicate source-commit work. Once the
    # consumer moves that seed to processed/failed, a later source commit for
    # the same scope/cycle must replace the marker and retry first materialization.
    duplicate = cycle_advance.enqueue_cycle_advance_reseeds(
        forecast_db=db_path,
        seed_dir=seed_dir,
        raw_manifest_dir=raw_dir,
        computed_at=datetime(2026, 7, 27, 16, 1, tzinfo=UTC),
        limit=5,
        scopes=(("Austin", "2026-07-28", "high"),),
        manifests=(),
        include_missing_posterior=True,
    )
    assert duplicate["seeds_enqueued"] == 0
    assert duplicate["already_enqueued"] == 1

    Path(marker["seed_file"]).unlink()
    retry = cycle_advance.enqueue_cycle_advance_reseeds(
        forecast_db=db_path,
        seed_dir=seed_dir,
        raw_manifest_dir=raw_dir,
        computed_at=datetime(2026, 7, 27, 16, 2, tzinfo=UTC),
        limit=5,
        scopes=(("Austin", "2026-07-28", "high"),),
        manifests=(),
        include_missing_posterior=True,
    )
    assert retry["first_materialization_seeds_enqueued"] == 1
    assert retry["seeds_enqueued"] == 1
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        retried_marker = conn.execute(
            """
            SELECT seed_file FROM cycle_advance_enqueues
            WHERE city='Austin' AND target_date='2026-07-28' AND metric='high'
            """
        ).fetchone()
    finally:
        conn.close()
    assert retried_marker is not None
    assert Path(retried_marker["seed_file"]).exists()


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


def test_held_reheal_has_no_clock_cooldown() -> None:
    """Operator law: no timers. The held re-heal is bounded by input identity only."""
    assert not hasattr(cycle_advance, "_HELD_REHEAL_COOLDOWN")
    assert not hasattr(cycle_advance, "_fresh_enough_to_retry_held_reheal")


def _held_blocked_harness(
    tmp_path: Path, monkeypatch, *, target_date: str = "2026-06-22", owner: str = "",
):
    """A held Panama City marker whose consumed seed reached the REAL batch consumer and
    materialized BLOCKED (REQUIREMENTS_NOT_MET, no posterior). ``owner=""`` routes the
    decision through the HELD-POSITION RE-HEAL branch; ``".enqueue-ab"`` through the
    owned-stage branch. Both sit behind the same identity fence."""
    import src.data.replacement_forecast_live_materialization_queue as queue

    root = tmp_path / "replacement_forecast_live"
    seeds, requests = root / "seeds", root / "requests"
    requests.mkdir(parents=True)
    seeds.mkdir()
    db = tmp_path / "forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    seed_file = seeds / f"Panama_City.{target_date}.high.20260621T060500Z{owner}.json"
    request = {
        "city": "Panama City", "target_date": target_date, "temperature_metric": "high",
        "source_cycle_time": "2026-06-21T06:00:00+00:00",
        "computed_at": "2026-06-21T06:05:00+00:00",
        "baseline_source_run_id": "baseline-run", "openmeteo_source_run_id": "om-run",
        "baseline_source_available_at": "2026-06-21T06:00:00+00:00",
        "openmeteo_source_available_at": "2026-06-21T06:00:00+00:00",
        # An anchor cycle past the cycle-age bound: the evidenced BLOCKED is true.
        "openmeteo_source_cycle_time": "2026-05-01T00:00:00+00:00",
        "openmeteo_payload_json": "payload.json", "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "30C"}],
    }
    (requests / seed_file.name).write_text(json.dumps(request), encoding="utf-8")
    inputs = {"value": "a"}
    monkeypatch.setattr(
        queue, "_blocked_attempt_fingerprint",
        lambda **kwargs: f"fp-{inputs['value']}",  # stands in for every read input
    )
    monkeypatch.setattr(queue, "_seed_source_cycle_boundary", lambda **_k: None)
    monkeypatch.setattr(queue, "_day0_carrier_vector_preflight_reason", lambda **_k: None)
    spawned: list[str] = []

    def blocked_runner(argv):
        import subprocess

        spawned.append(argv[-1])
        return subprocess.CompletedProcess(
            list(argv), 2,
            stdout=json.dumps({"status": "BLOCKED",
                               "reason_codes": ["REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET"],
                               "consumed_inputs": _consumed_witness(argv),
                               "blocked_evidence": _blocked_evidence(db, argv)}),
            stderr="",
        )

    report = queue._process_claimed_materialization_batch(
        request_path=requests, processed_path=root / "processed", failed_path=root / "failed",
        forecast_db=db, limit=1, runner=blocked_runner,
        marker_dir=root / "blocked_attempts", seed_dir=seeds,
    )
    assert report.processed_count == 1 and len(spawned) == 1
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """INSERT INTO cycle_advance_enqueues
           (enqueued_at, city, target_date, metric, consumed_cycle_time, target_cycle_time,
            held_position, seed_file, reason)
           VALUES ('2026-06-21T06:05:00+00:00','Panama City',?,'high',
                   '2026-06-20T18:00:00+00:00','2026-06-21T06:00:00+00:00', 1, ?, NULL)""",
        (target_date, str(seed_file)),
    )
    conn.commit()

    def decide(now: datetime) -> bool:
        return cycle_advance._enqueue_decision(
            conn, city="Panama City", target_date=target_date, metric="high",
            target_cycle_iso="2026-06-21T06:00:00+00:00", as_of=now,
        ) is cycle_advance._CycleAdvanceEnqueueDecision.ADMIT

    return decide, inputs, conn


@pytest.mark.parametrize("owner", ("", ".enqueue-ab"))
def test_held_blocked_materialization_stays_fenced_across_ticks_and_restart(
    tmp_path, monkeypatch, owner,
) -> None:
    """G2 (Panama City 06-22 freeze class): a held family whose seed materialized BLOCKED
    must not re-heal on unchanged inputs, for any number of ticks or a restart, and must
    re-heal on the very next tick once any read input changes. No clock participates."""
    decide, inputs, conn = _held_blocked_harness(tmp_path, monkeypatch, owner=owner)
    start = datetime(2026, 6, 21, 6, 6, tzinfo=timezone.utc)
    assert not any(decide(start + timedelta(minutes=5 * n)) for n in range(200)), (
        "unchanged identity must stay fenced (no 30-minute cooldown reopen)"
    )
    conn.close()  # restart: the fence is on disk, nothing lives in process memory
    conn = sqlite3.connect(tmp_path / "forecasts.db")
    conn.row_factory = sqlite3.Row
    restarted = lambda now: cycle_advance._enqueue_decision(  # noqa: E731
        conn, city="Panama City", target_date="2026-06-22", metric="high",
        target_cycle_iso="2026-06-21T06:00:00+00:00", as_of=now,
    ) is cycle_advance._CycleAdvanceEnqueueDecision.ADMIT
    assert not restarted(start + timedelta(hours=17))

    inputs["value"] = "b"  # any read input changes
    assert restarted(start + timedelta(hours=17, seconds=1)), (
        "a held family must re-heal immediately on a read-input change"
    )


def test_held_family_whose_local_day_ended_is_never_readmitted(tmp_path, monkeypatch) -> None:
    """An ended-day held family can never be rebuilt: re-heal never re-admits it, whatever
    its inputs do, across many ticks (has_city_local_day_ended is the one predicate)."""
    decide, inputs, _conn = _held_blocked_harness(
        tmp_path, monkeypatch, target_date="2026-06-20",
    )
    start = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)  # Panama 06-20 has ended
    for n in range(100):
        inputs["value"] = f"changed-{n}"
        assert not decide(start + timedelta(minutes=5 * n))


@pytest.mark.parametrize(
    ("error_type", "category", "retained"),
    (
        ("ValueError", "INPUT_VERDICT", False),
        ("ValueError", None, True),  # an older worker's bare ValueError is UNCLASSIFIED
        ("OperationalError", "ENVIRONMENT_RETRY", True),
        ("RuntimeError", "BOGUS", True),
        (None, None, True),
    ),
)
def test_held_materialization_error_is_fenced_or_owned_never_respawned(
    tmp_path, monkeypatch, error_type, category, retained,
) -> None:
    """S3: a worker ERROR never makes held re-heal publish fresh producer work per tick.
    Only an emitted INPUT_VERDICT is fenced on the attempt identity; an environment or
    unclassified error (absent/unknown category included, whatever its exception type)
    keeps its one request as the family's owner, retried by the queue."""
    import subprocess

    import src.data.replacement_forecast_live_materialization_queue as queue

    root = tmp_path / "replacement_forecast_live"
    seeds, requests = root / "seeds", root / "requests"
    requests.mkdir(parents=True)
    seeds.mkdir()
    db = tmp_path / "forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    seed_file = seeds / "Panama_City.2026-06-22.high.20260621T060500Z.json"
    request = {
        "city": "Panama City", "target_date": "2026-06-22", "temperature_metric": "high",
        "source_cycle_time": "2026-06-21T06:00:00+00:00",
        "computed_at": "2026-06-21T06:05:00+00:00",
        "baseline_source_run_id": "baseline-run", "openmeteo_source_run_id": "om-run",
        "openmeteo_payload_json": "payload.json", "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "30C"}],
    }
    (requests / seed_file.name).write_text(json.dumps(request), encoding="utf-8")
    monkeypatch.setattr(queue, "_blocked_attempt_fingerprint", lambda **_k: "fp-a")
    monkeypatch.setattr(queue, "_seed_source_cycle_boundary", lambda **_k: None)
    monkeypatch.setattr(queue, "_day0_carrier_vector_preflight_reason", lambda **_k: None)
    def body(argv) -> str:
        return "" if error_type is None else json.dumps({
            "status": "ERROR", "error_type": error_type,
            **({} if category is None else {"failure_category": category}),
            "consumed_inputs": _consumed_witness(argv),
        })

    report = queue._process_claimed_materialization_batch(
        request_path=requests, processed_path=root / "processed", failed_path=root / "failed",
        forecast_db=db, limit=1,
        runner=lambda argv: subprocess.CompletedProcess(list(argv), 2, stdout="", stderr=body(argv)),
        marker_dir=root / "blocked_attempts", seed_dir=seeds,
    )
    assert (requests / seed_file.name).is_file() is retained
    assert not report.failed_count
    assert queue.consumed_seed_request_owned(seed_file) is retained
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """INSERT INTO cycle_advance_enqueues
           (enqueued_at, city, target_date, metric, consumed_cycle_time, target_cycle_time,
            held_position, seed_file, reason)
           VALUES ('2026-06-21T06:05:00+00:00','Panama City','2026-06-22','high',
                   '2026-06-20T18:00:00+00:00','2026-06-21T06:00:00+00:00', 1, ?, NULL)""",
        (str(seed_file),),
    )
    conn.commit()

    def admitted(n: int) -> bool:
        return cycle_advance._enqueue_decision(
            conn, city="Panama City", target_date="2026-06-22", metric="high",
            target_cycle_iso="2026-06-21T06:00:00+00:00",
            as_of=datetime(2026, 6, 21, 6, 6, tzinfo=timezone.utc) + timedelta(minutes=n),
        ) is cycle_advance._CycleAdvanceEnqueueDecision.ADMIT

    assert not any(admitted(n) for n in range(50))
    if retained:
        (requests / seed_file.name).unlink()  # the owner drains (e.g. it succeeded)
        assert admitted(51), "re-heal resumes once the owning request is gone"
    conn.close()


def test_held_reheal_unknown_city_timezone_is_not_ended() -> None:
    assert cycle_advance._held_target_local_day_ended(
        "Nowhere City", "2000-01-01", datetime(2026, 6, 21, tzinfo=timezone.utc),
    ) is False


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
        path = Path(
            kwargs.get("output_path")
            or Path(kwargs["seed_path"]) / "Shanghai.2026-06-19.high.seed.json"
        )
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


def test_single_family_reseed_skips_when_target_local_day_has_ended(
    tmp_path, monkeypatch
) -> None:
    """A single-family reseed for a city-local target day that already ended must be a
    fail-soft skip with no DB or file work: London's local day ends 23:00Z (BST), so a
    request at 03:07Z the next day is 4+ hours into a day the market has already closed."""
    db_path = tmp_path / "forecasts.db"  # deliberately never created
    monkeypatch.setattr(
        "src.data.replacement_forecast_current_target_plan._city_timezone_by_name",
        lambda: {"London": "Europe/London"},
    )

    def _fail_if_called(*_args, **_kwargs):
        pytest.fail("target-local-day-ended reseed must not reach family_materializable_cycle")

    monkeypatch.setattr(cycle_advance, "family_materializable_cycle", _fail_if_called)
    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", _fail_if_called)

    report = cycle_advance.enqueue_single_family_cycle_advance_reseed(
        forecast_db=db_path,
        seed_dir=tmp_path / "seeds",
        raw_manifest_dir=tmp_path / "raw",
        city="London",
        target_date="2026-09-13",
        metric="high",
        computed_at=datetime(2026, 9, 14, 3, 7, tzinfo=UTC),
    )

    assert report["status"] == cycle_advance.RESEED_SKIPPED_TARGET_LOCAL_DAY_ENDED
    assert report["enqueued"] is False
    assert not db_path.exists()
    assert not (tmp_path / "seeds").exists()


def test_single_family_reseed_enqueues_when_target_local_day_still_open(
    tmp_path, monkeypatch
) -> None:
    """Same family, 30 minutes before London's local-day rollover (23:00Z): the day is still
    open, so the reseed must proceed exactly as it did before the local-day-end guard existed."""
    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    cycle = datetime(2026, 9, 13, 12, tzinfo=UTC)
    _insert_artifact(
        conn,
        source_id="openmeteo_ecmwf_ifs_9km",
        cycle_iso=cycle.isoformat(),
    )
    conn.close()

    monkeypatch.setattr(
        "src.data.replacement_forecast_current_target_plan._city_timezone_by_name",
        lambda: {"London": "Europe/London"},
    )
    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (cycle, ()),
    )

    def _fake_build_seed(_conn_arg, **kwargs):
        path = Path(
            kwargs.get("output_path")
            or Path(kwargs["seed_path"]) / "London.2026-09-13.high.seed.json"
        )
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
        city="London",
        target_date="2026-09-13",
        metric="high",
        computed_at=datetime(2026, 9, 13, 22, 30, tzinfo=UTC),
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
        SELECT consumed_cycle_time, target_cycle_time, seed_file, reason
        FROM cycle_advance_enqueues
        WHERE city = 'London' AND target_date = '2026-09-13' AND metric = 'high'
        """
    ).fetchone()
    check.close()
    assert row["consumed_cycle_time"] == "NO_LIVE_POSTERIOR"
    assert row["target_cycle_time"] == cycle.isoformat()
    assert row["seed_file"] == str(seed_file)
    assert row["reason"] == "MISSING_LIVE_POSTERIOR"


def test_single_family_reseed_survives_invalid_timezone(tmp_path, monkeypatch) -> None:
    """ALWAYS-DECIDABLE / fail-soft contract: an unresolvable timezone in city config must
    degrade the local-day-end check to "not ended", never raise ZoneInfoNotFoundError into
    the reactor cycle. Same scenario as the still-open case above, but the config carries a
    bogus timezone string instead of a real one -- the family must still enqueue."""
    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    cycle = datetime(2026, 9, 13, 12, tzinfo=UTC)
    _insert_artifact(
        conn,
        source_id="openmeteo_ecmwf_ifs_9km",
        cycle_iso=cycle.isoformat(),
    )
    conn.close()

    monkeypatch.setattr(
        "src.data.replacement_forecast_current_target_plan._city_timezone_by_name",
        lambda: {"London": "Not/ARealZone"},
    )
    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (cycle, ()),
    )

    def _fake_build_seed(_conn_arg, **kwargs):
        path = Path(
            kwargs.get("output_path")
            or Path(kwargs["seed_path"]) / "London.2026-09-13.high.seed.json"
        )
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
        city="London",
        target_date="2026-09-13",
        metric="high",
        computed_at=datetime(2026, 9, 14, 3, 7, tzinfo=UTC),
    )

    assert report["status"] == "CYCLE_ADVANCE_FIRST_MATERIALIZATION_ENQUEUED"
    assert report["enqueued"] is True


def test_explicit_scopes_skip_target_local_day_ended(tmp_path, monkeypatch) -> None:
    """The ENS-wake / explicit-scopes lane (enqueue_cycle_advance_reseeds with scopes=[...])
    has no plan behind it, so it must apply the local-day-end predicate to its own scope
    directly: London 2026-09-13 at 03:07Z 09-14 (4h into an already-ended local day) must
    build zero seeds and count exactly one skip, never reaching family_materializable_cycle."""
    db_path = tmp_path / "forecast.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    conn.close()

    monkeypatch.setattr(
        cycle_advance,
        "freshest_materializable_cycle",
        lambda _conn: datetime(2026, 9, 13, 6, tzinfo=UTC),
    )

    def _fail_if_called(*_args, **_kwargs):
        pytest.fail("target-local-day-ended scope must not reach family_materializable_cycle")

    monkeypatch.setattr(cycle_advance, "family_materializable_cycle", _fail_if_called)
    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", _fail_if_called)

    report = cycle_advance.enqueue_cycle_advance_reseeds(
        forecast_db=db_path,
        seed_dir=tmp_path / "seeds",
        raw_manifest_dir=tmp_path / "raw",
        computed_at=datetime(2026, 9, 14, 3, 7, tzinfo=UTC),
        limit=5,
        scopes=(("London", "2026-09-13", "high"),),
        manifests=(),
        include_missing_posterior=True,
    )

    assert report["seeds_enqueued"] == 0
    assert report[cycle_advance.RESEED_SKIPPED_TARGET_LOCAL_DAY_ENDED] == 1
    assert not (tmp_path / "seeds").exists()

    check = sqlite3.connect(db_path)
    check.row_factory = sqlite3.Row
    row = check.execute(
        """
        SELECT 1 FROM cycle_advance_enqueues
        WHERE city = 'London' AND target_date = '2026-09-13' AND metric = 'high'
        """
    ).fetchone()
    check.close()
    assert row is None


def test_explicit_scopes_enqueues_when_target_local_day_still_open(tmp_path, monkeypatch) -> None:
    """Same lane, same family, 30 minutes before London's local-day rollover (23:00Z): the day
    is still open, so the scope must enqueue exactly as before this predicate existed."""
    db_path = tmp_path / "forecast.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    conn.close()

    target_cycle = datetime(2026, 9, 13, 6, tzinfo=UTC)
    seed_dir = tmp_path / "seeds"

    monkeypatch.setattr(
        cycle_advance,
        "freshest_materializable_cycle",
        lambda _conn: target_cycle,
    )
    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (target_cycle, ()),
    )
    # Target date is same-day as computed_at, so the (unrelated) Day0-observed-extreme
    # requirement would otherwise gate this candidate on evidence this test doesn't supply.
    monkeypatch.setattr(
        "src.data.replacement_forecast_current_target_plan._day0_observed_extreme_required",
        lambda **_kwargs: False,
    )

    def _fake_build_seed(*args, **kwargs):
        seed_file = Path(
            kwargs.get("output_path") or seed_dir / "London.2026-09-13.high.json"
        )
        seed_file.parent.mkdir(parents=True, exist_ok=True)
        seed_file.write_text("{}", encoding="utf-8")
        return seed_file

    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", _fake_build_seed)

    report = cycle_advance.enqueue_cycle_advance_reseeds(
        forecast_db=db_path,
        seed_dir=seed_dir,
        raw_manifest_dir=tmp_path / "raw",
        computed_at=datetime(2026, 9, 13, 22, 30, tzinfo=UTC),
        limit=5,
        scopes=(("London", "2026-09-13", "high"),),
        manifests=(),
        include_missing_posterior=True,
    )

    assert report[cycle_advance.RESEED_SKIPPED_TARGET_LOCAL_DAY_ENDED] == 0
    assert report["seeds_enqueued"] == 1


def test_explicit_scopes_survive_invalid_timezone(tmp_path, monkeypatch) -> None:
    """Fail-soft per this function's own contract ("any per-scope error is logged and
    skipped; the function never raises into the poll"): a bogus timezone string in city
    config must degrade the local-day-end check to "not ended" for that one scope, never
    raise ZoneInfoNotFoundError out of the batch. Same 03:07Z-09-14 scenario that would
    otherwise be excluded, but with an unresolvable timezone -- the scope must still enqueue."""
    db_path = tmp_path / "forecast.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    conn.close()

    target_cycle = datetime(2026, 9, 13, 6, tzinfo=UTC)
    seed_dir = tmp_path / "seeds"

    monkeypatch.setattr(
        "src.data.replacement_forecast_current_target_plan._city_timezone_by_name",
        lambda: {"London": "Not/ARealZone"},
    )
    monkeypatch.setattr(
        cycle_advance,
        "freshest_materializable_cycle",
        lambda _conn: target_cycle,
    )
    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (target_cycle, ()),
    )
    monkeypatch.setattr(
        "src.data.replacement_forecast_current_target_plan._day0_observed_extreme_required",
        lambda **_kwargs: False,
    )

    def _fake_build_seed(*args, **kwargs):
        seed_file = Path(
            kwargs.get("output_path") or seed_dir / "London.2026-09-13.json"
        )
        seed_file.parent.mkdir(parents=True, exist_ok=True)
        seed_file.write_text("{}", encoding="utf-8")
        return seed_file

    monkeypatch.setattr(cycle_advance, "_build_and_write_advance_seed", _fake_build_seed)

    report = cycle_advance.enqueue_cycle_advance_reseeds(
        forecast_db=db_path,
        seed_dir=seed_dir,
        raw_manifest_dir=tmp_path / "raw",
        computed_at=datetime(2026, 9, 14, 3, 7, tzinfo=UTC),
        limit=5,
        scopes=(("London", "2026-09-13", "high"),),
        manifests=(),
        include_missing_posterior=True,
    )

    assert report[cycle_advance.RESEED_SKIPPED_TARGET_LOCAL_DAY_ENDED] == 0
    assert report["seeds_enqueued"] == 1


@pytest.mark.parametrize("family_cycle_lag_hours", [0, 6])
@pytest.mark.parametrize("other_family_advanced", [False, True])
def test_single_family_monitor_recomputes_expired_posterior_on_same_cycle(
    tmp_path, monkeypatch, other_family_advanced, family_cycle_lag_hours
) -> None:
    """A held posterior's expired computation clock must not wait for a new source cycle."""
    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    cycle = datetime(2026, 8, 12, 6, tzinfo=UTC)
    _insert_artifact(
        conn,
        source_id="openmeteo_ecmwf_ifs_9km",
        cycle_iso=(cycle + timedelta(hours=6) if other_family_advanced else cycle).isoformat(),
    )
    _insert_posterior(
        conn,
        city="Tel Aviv",
        target_date="2026-08-13",
        metric="high",
        cycle_iso=cycle.isoformat(),
        computed_at="2026-08-12T09:00:00+00:00",
    )
    conn.close()

    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (cycle - timedelta(hours=family_cycle_lag_hours), ()),
    )

    def _fake_build_seed(_conn_arg, **kwargs):
        assert family_cycle_lag_hours == 0, "older carrier must not build a seed"
        path = Path(kwargs["output_path"])
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
        city="Tel Aviv",
        target_date="2026-08-13",
        metric="high",
        computed_at=datetime(2026, 8, 12, 13, tzinfo=UTC),
        held_position=True,
        minimum_posterior_computed_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
    )

    if family_cycle_lag_hours:
        assert report["status"] == "SAME_CYCLE_RECOMPUTE_MANIFEST_MISSING"
        assert report["enqueued"] is False
        assert not (tmp_path / "seeds").exists()
        return

    assert report["status"] == "SAME_CYCLE_RECOMPUTE_ENQUEUED"
    assert report["enqueued"] is True
    seed_file = Path(str(report["seed_file"]))
    assert json.loads(seed_file.read_text(encoding="utf-8")) == {
        "upgrade_trigger": "held_belief_computed_age_expired",
    }
    check = sqlite3.connect(db_path)
    check.row_factory = sqlite3.Row
    row = check.execute(
        """
        SELECT consumed_cycle_time, target_cycle_time, held_position, reason
        FROM cycle_advance_enqueues
        WHERE city = 'Tel Aviv' AND target_date = '2026-08-13' AND metric = 'high'
        """
    ).fetchone()
    check.close()
    assert row is not None
    assert row["consumed_cycle_time"] == cycle.isoformat()
    assert row["target_cycle_time"] == cycle.isoformat()
    assert row["held_position"] == 1
    assert row["reason"] == "HELD_BELIEF_COMPUTED_AGE_EXPIRED"


def test_single_family_day0_does_not_enqueue_anchor_behind_eligible_ensemble(
    tmp_path, monkeypatch
) -> None:
    """A Day0 wake must not churn an old carrier after ENS advanced past its anchor."""
    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    anchor_cycle = datetime(2026, 8, 30, 6, tzinfo=UTC)
    ensemble_cycle = datetime(2026, 8, 30, 12, tzinfo=UTC)
    _insert_artifact(
        conn,
        source_id="openmeteo_ecmwf_ifs_9km",
        cycle_iso=ensemble_cycle.isoformat(),
    )
    _insert_posterior(
        conn,
        city="Moscow",
        target_date="2026-08-31",
        metric="high",
        cycle_iso=anchor_cycle.isoformat(),
        computed_at="2026-08-30T08:00:00+00:00",
    )
    conn.close()

    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (anchor_cycle, ()),
    )
    monkeypatch.setattr(
        "src.data.replacement_input_hwm.latest_eligible_ensemble_input_cycle",
        lambda *args, **kwargs: ensemble_cycle,
    )
    monkeypatch.setattr(
        cycle_advance,
        "_build_and_write_advance_seed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("anchor-behind-ENS family must not build a seed")
        ),
    )

    report = cycle_advance.enqueue_single_family_cycle_advance_reseed(
        forecast_db=db_path,
        seed_dir=tmp_path / "seeds",
        raw_manifest_dir=tmp_path / "raw",
        city="Moscow",
        target_date="2026-08-31",
        metric="high",
        computed_at=datetime(2026, 8, 30, 19, tzinfo=UTC),
        day0_observed_extreme_c=20.0,
        day0_observed_extreme_source="aviationweather_metar",
        day0_observed_extreme_observation_time="2026-08-30T18:55:00+00:00",
        day0_observed_extreme_sample_count=24,
        day0_observed_extreme_unit="C",
        held_position=True,
    )

    assert report == {
        "status": "CYCLE_ADVANCE_FAMILY_ANCHOR_BEHIND_ENSEMBLE",
        "city": "Moscow",
        "target_date": "2026-08-31",
        "metric": "high",
        "held_position": True,
        "enqueued": False,
        "freshest_materializable_cycle": ensemble_cycle.isoformat(),
        "consumed_cycle": anchor_cycle.isoformat(),
        "family_cycle": anchor_cycle.isoformat(),
        "eligible_ensemble_cycle": ensemble_cycle.isoformat(),
    }
    assert not (tmp_path / "seeds").exists()


@pytest.mark.parametrize("other_family_advanced", [False, True])
def test_single_family_monitor_does_not_recompute_fresh_same_cycle_posterior(
    tmp_path, monkeypatch, other_family_advanced
) -> None:
    """A same-cycle posterior newer than the monitor cutoff remains completion proof."""
    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    cycle = datetime(2026, 8, 12, 6, tzinfo=UTC)
    _insert_artifact(
        conn,
        source_id="openmeteo_ecmwf_ifs_9km",
        cycle_iso=(cycle + timedelta(hours=6) if other_family_advanced else cycle).isoformat(),
    )
    _insert_posterior(
        conn,
        city="Tel Aviv",
        target_date="2026-08-13",
        metric="high",
        cycle_iso=cycle.isoformat(),
        computed_at="2026-08-12T12:00:00+00:00",
    )
    conn.close()

    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (cycle, ()),
    )
    monkeypatch.setattr(
        cycle_advance,
        "_build_and_write_advance_seed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fresh posterior must not enqueue same-cycle work")
        ),
    )

    report = cycle_advance.enqueue_single_family_cycle_advance_reseed(
        forecast_db=db_path,
        seed_dir=tmp_path / "seeds",
        raw_manifest_dir=tmp_path / "raw",
        city="Tel Aviv",
        target_date="2026-08-13",
        metric="high",
        computed_at=datetime(2026, 8, 12, 13, tzinfo=UTC),
        held_position=True,
        minimum_posterior_computed_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
    )

    assert report["status"] == "CYCLE_ADVANCE_NOT_NEEDED"
    assert report["enqueued"] is False


def test_single_family_day0_monitor_recomputes_matching_but_older_posterior(
    tmp_path, monkeypatch
) -> None:
    """New Day0 inputs must outrank an older posterior with the same observation identity."""
    db_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    cycle_advance._ensure_day0_conditioning_identity_column(conn)
    cycle = datetime(2026, 8, 12, 6, tzinfo=UTC)
    observation_time = "2026-08-12T09:30:00+00:00"
    conditioning = {
        "source": "aviationweather_metar",
        "observation_time": observation_time,
        "observed_extreme_c": 27.0,
        "unit": "C",
    }
    identity = cycle_advance._day0_conditioning_identity(**conditioning)
    _insert_artifact(
        conn,
        source_id="openmeteo_ecmwf_ifs_9km",
        cycle_iso=cycle.isoformat(),
    )
    _insert_posterior(
        conn,
        city="Jinan",
        target_date="2026-08-12",
        metric="high",
        cycle_iso=cycle.isoformat(),
        computed_at="2026-08-12T09:40:00+00:00",
    )
    conn.execute(
        """
        UPDATE forecast_posteriors
           SET provenance_json = ?
         WHERE city = 'Jinan' AND target_date = '2026-08-12' AND temperature_metric = 'high'
        """,
        (
            json.dumps(
                {
                    "openmeteo_anchor_artifact_id": 1,
                    "day0_conditioning": conditioning,
                }
            ),
        ),
    )
    old_seed = tmp_path / "seeds" / "drained-day0-seed.enqueue-owner.json"
    conn.execute(
        """
        INSERT INTO cycle_advance_enqueues (
            enqueued_at, city, target_date, metric, consumed_cycle_time,
            target_cycle_time, held_position, seed_file,
            day0_observed_extreme_observation_time,
            day0_conditioning_identity_json
        ) VALUES ('2026-08-12T09:40:00+00:00', 'Jinan', '2026-08-12',
                  'high', ?, ?, 1, ?, ?, ?)
        """,
        (
            cycle.isoformat(),
            cycle.isoformat(),
            str(old_seed),
            observation_time,
            identity,
        ),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        cycle_advance,
        "family_materializable_cycle",
        lambda *args, **kwargs: (cycle, ()),
    )
    monkeypatch.setattr(
        cycle_advance,
        "_day0_enqueue_owner_request_check",
        lambda **kwargs: cycle_advance._Day0EnqueueOwnerRequestCheck(
            cycle_advance._Day0EnqueueOwnerRequestState.INACTIVE,
            "ABSENT",
        ),
    )

    def _fake_build_seed(_conn_arg, **kwargs):
        path = Path(kwargs["output_path"])
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
        city="Jinan",
        target_date="2026-08-12",
        metric="high",
        computed_at=datetime(2026, 8, 12, 10, tzinfo=UTC),
        day0_observed_extreme_c=27.0,
        day0_observed_extreme_source="aviationweather_metar",
        day0_observed_extreme_observation_time=observation_time,
        day0_observed_extreme_sample_count=12,
        day0_observed_extreme_unit="C",
        held_position=True,
        minimum_posterior_computed_at=datetime(2026, 8, 12, 9, 55, tzinfo=UTC),
    )

    assert report["status"] == "DAY0_OBSERVATION_ADVANCE_ENQUEUED"
    assert report["enqueued"] is True
    assert json.loads(Path(str(report["seed_file"])).read_text()) == {
        "upgrade_trigger": "held_belief_computed_age_expired",
    }


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

    def _fake_persist(forecast_db, rows, cutoff_iso=None, **_kwargs):
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


def test_retired_low_revision_allows_only_the_proven_cycle_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The materializer retains same-version monotonicity but admits v1->v2 repair."""
    import src.data.replacement_forecast_materializer as materializer
    conn = _conn()
    _insert_posterior(conn, city='Seoul', target_date='2026-09-23', metric='low', cycle_iso='2026-09-22T18:00:00+00:00', computed_at='2026-09-23T05:05:10+00:00')
    req = _Req(city='Seoul', target_date=date(2026,9,23), metric='low', source_cycle_time=datetime(2026,9,22,12,tzinfo=UTC))
    monkeypatch.setattr(materializer, 'retired_low_uncertified_incumbent_yields_to_current_ensemble', lambda *_a, **_k: True)
    assert _cycle_monotone_block_reasons(conn, req, metric='low') == ()
    monkeypatch.setattr(materializer, 'retired_low_uncertified_incumbent_yields_to_current_ensemble', lambda *_a, **_k: False)
    assert _REGRESSION_REASON in _cycle_monotone_block_reasons(conn, req, metric='low')



def test_retained_failure_waits_its_turn_within_its_tier(tmp_path, monkeypatch) -> None:
    """Round-3 Q3: with one slot, a retained failing request rotates behind the
    other equal-priority family instead of monopolizing the slot. Order only:
    each request stays claimable at once (no delay), and the failing one keeps
    retrying (no cap)."""
    import subprocess

    import src.data.replacement_forecast_live_materialization_queue as queue

    root = tmp_path / "queue"
    requests = root / "requests"
    requests.mkdir(parents=True)
    db = tmp_path / "forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    for name, city in (("A.json", "Austin"), ("B.json", "Chicago")):
        (requests / name).write_text(json.dumps({
            "city": city, "target_date": "2026-10-01", "temperature_metric": "high",
            "source_cycle_time": "2026-10-01T06:00:00+00:00",
            "computed_at": "2026-10-01T06:05:00+00:00",
            "baseline_source_run_id": "baseline", "openmeteo_source_run_id": "om",
            "openmeteo_payload_json": "payload.json", "precision_metadata_json": "precision.json",
            "bins": [{"bin_id": "30C"}],
        }), encoding="utf-8")
    monkeypatch.setattr(queue, "_priority_map_with_names", lambda *_a, **_k: ({}, set()))
    monkeypatch.setattr(queue, "_blocked_attempt_fingerprint", lambda **k: "fp-" + k["payload"]["city"])
    monkeypatch.setattr(queue, "_seed_source_cycle_boundary", lambda **_k: None)
    monkeypatch.setattr(queue, "_day0_carrier_vector_preflight_reason", lambda **_k: None)
    calls: list[str] = []

    def runner(argv):
        file = next(Path(x) for x in argv if str(x).endswith(".json") and Path(x).is_file())
        calls.append(json.loads(file.read_text())["city"])
        return subprocess.CompletedProcess(argv, 2, stdout=json.dumps(
            {"status": "ERROR", "error_type": "RuntimeError"}), stderr="")

    for _ in range(4):
        report = queue.process_replacement_forecast_live_materialization_queue(
            request_dir=requests, processed_dir=root / "processed", failed_dir=root / "failed",
            forecast_db=db, limit=1, runner=runner, discover=False, seed_limit=0,
        )
        assert queue._UNCLASSIFIED_ERROR_REASON in report.reason_codes
    assert calls == ["Austin", "Chicago", "Austin", "Chicago"]
    assert sorted(p.name for p in requests.glob("*.json")) == ["A.json", "B.json"]



@pytest.mark.parametrize(("body", "category"), (
    ('{"temperature_metric": "high", "target_date": "not-a-date"}', "INPUT_VERDICT"),
    ("[1, 2]", "INPUT_VERDICT"),  # input JSON is not an object
    # A missing field is a KeyError, not a validation ValueError: no verdict, retained.
    ('{"city": "Panama City"}', "UNCLASSIFIED"),
))
def test_worker_input_verdict_is_emitted_and_fenced_by_the_queue(
    tmp_path, monkeypatch, body, category,
) -> None:
    """Round-3 Q4: the real worker classifies its own failure. A malformed input
    yields ERROR + INPUT_VERDICT, and the queue fences that exact attempt instead
    of retaining it for another full subprocess run."""
    import subprocess

    import scripts.materialize_replacement_forecast_live as worker
    import src.data.replacement_forecast_live_materialization_queue as queue

    root = tmp_path / "replacement_forecast_live"
    requests = root / "requests"
    requests.mkdir(parents=True)
    db = tmp_path / "forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    bad = tmp_path / "bad.json"
    bad.write_text(body, encoding="utf-8")
    with sqlite3.connect(":memory:") as worker_conn:
        returncode, stdout, stderr = worker._run_one(
            bad, commit=False, init_schema=False, conn=worker_conn,
        )
    emitted = json.loads(stderr.strip().splitlines()[-1])
    assert (returncode, emitted["status"], emitted["failure_category"]) == (2, "ERROR", category)

    # Replaying that output against a different claimed request is a crossed
    # witness, which must never fence. The queue half therefore has the real
    # worker judge the claimed request itself: its named payload holds the same
    # malformed body, so the verdict concerns only bytes this claim names.
    (root / "payload.json").write_text(body, encoding="utf-8")
    name = "Panama_City.2026-06-22.high.20260621T060500Z.json"
    fenced = category == "INPUT_VERDICT"
    claim = {
        "city": "Panama City", "city_timezone": "America/Panama",
        "target_date": "2026-06-22", "temperature_metric": "high",
        "source_cycle_time": "2026-06-21T06:00:00+00:00",
        "computed_at": "2026-06-21T06:05:00+00:00",
        "baseline_source_run_id": "baseline-run", "openmeteo_source_run_id": "om-run",
        "openmeteo_payload_json": "../payload.json", "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "30C"}],
    }
    if not fenced:
        # A field the queue's schema gate admits without but the worker reads:
        # its absence is a KeyError past a valid payload, no verdict.
        del claim["city_timezone"]
        (root / "payload.json").write_text("{}", encoding="utf-8")
    (requests / name).write_text(json.dumps(claim), encoding="utf-8")
    monkeypatch.setattr(queue, "_blocked_attempt_fingerprint", lambda **_k: "fp-a")
    monkeypatch.setattr(queue, "_seed_source_cycle_boundary", lambda **_k: None)
    monkeypatch.setattr(queue, "_day0_carrier_vector_preflight_reason", lambda **_k: None)
    monkeypatch.setattr(worker, "ROOT", tmp_path / "no-fallback-root")
    claimed: list[dict] = []

    def runner(argv):
        request = Path(argv[argv.index("--input-json") + 1])
        with sqlite3.connect(":memory:") as worker_conn:
            code, out, err = worker._run_one(request, commit=False, init_schema=False, conn=worker_conn)
        claimed.append(json.loads(err.strip().splitlines()[-1]))
        return subprocess.CompletedProcess(list(argv), code, out, err)

    report = queue._process_claimed_materialization_batch(
        request_path=requests, processed_path=root / "processed", failed_path=root / "failed",
        forecast_db=db, limit=1, runner=runner,
        marker_dir=root / "blocked_attempts", seed_dir=root / "seeds",
    )
    assert claimed[0]["failure_category"] == category
    assert (requests / name).exists() is not fenced, "a verdict is fenced; anything else is retained"
    assert (queue._UNCHANGED_BLOCKED_SKIP_REASON in report.reason_codes) is fenced
    assert (queue._ERROR_RETAINED_REASON in report.reason_codes) is not fenced


def test_worker_categories_name_what_each_failure_proves() -> None:
    import sqlite3 as _sqlite3

    import scripts.materialize_replacement_forecast_live as worker

    category = lambda exc: worker._error_response(exc)["failure_category"]  # noqa: E731
    assert category(worker.RequestInputInvalid("bins[] entries must be objects")) == "INPUT_VERDICT"
    assert category(ValueError("raised by the computation")) == "UNCLASSIFIED"
    assert category(_sqlite3.OperationalError("database is locked")) == "ENVIRONMENT_RETRY"
    assert category(PermissionError("unreadable")) == "ENVIRONMENT_RETRY"
    assert category(worker.ReplacementForecastWriteDeferred("busy")) == "ENVIRONMENT_RETRY"
    assert category(RuntimeError("unexpected")) == "UNCLASSIFIED"



def test_worker_fetched_payload_defect_is_never_an_input_verdict(tmp_path, monkeypatch) -> None:
    """Direct-fetch route: bytes from the network are not a named input, so a
    ValueError while validating them is UNCLASSIFIED, never fenced."""
    import scripts.materialize_replacement_forecast_live as worker

    request = tmp_path / "fetch.json"
    request.write_text(json.dumps({
        "city": "Panama City", "city_timezone": "America/Panama",
        "target_date": "2026-06-22", "temperature_metric": "high",
        "source_cycle_time": "2026-06-21T06:00:00+00:00",
        "computed_at": "2026-06-21T06:05:00+00:00",
        "latitude": 8.97, "longitude": -79.53, "bins": [{"bin_id": "30C"}],
    }), encoding="utf-8")
    monkeypatch.setattr(worker, "build_anchor_request", lambda **_k: object())
    monkeypatch.setattr(worker, "fetch_openmeteo_ecmwf_ifs9_anchor_payload", lambda _r: {"hourly": {}})

    def defective(*_a, **_k):
        raise ValueError("fetched payload lacks hourly samples")

    monkeypatch.setattr(worker, "extract_openmeteo_ecmwf_ifs9_localday_anchor", defective)
    with sqlite3.connect(":memory:") as worker_conn:
        returncode, _stdout, stderr = worker._run_one(
            request, commit=False, init_schema=False, conn=worker_conn,
        )
    emitted = json.loads(stderr.strip().splitlines()[-1])
    assert (returncode, emitted["error_type"], emitted["failure_category"]) == (
        2, "ValueError", "UNCLASSIFIED",
    )



@pytest.mark.parametrize("witness", ("absent", "rewritten"))
def test_unbound_blocked_verdict_is_retained_not_fenced(tmp_path, monkeypatch, witness) -> None:
    """Round-4 BLOCKER (ordinary BLOCKED shares the boundary): a verdict with no
    consumed-input witness, or whose consumed file changed after the worker read
    it, binds nothing. It is retained for fair retry with its diagnostic and the
    unbound reason; no marker or producer receipt is written."""
    import subprocess

    import src.data.replacement_forecast_live_materialization_queue as queue

    root = tmp_path / "replacement_forecast_live"
    requests, seeds = root / "requests", root / "seeds"
    requests.mkdir(parents=True)
    seeds.mkdir()
    db = tmp_path / "forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    name = "Panama_City.2026-06-22.high.20260621T060500Z.json"
    (requests / name).write_text(json.dumps({
        "city": "Panama City", "target_date": "2026-06-22", "temperature_metric": "high",
        "source_cycle_time": "2026-06-21T06:00:00+00:00",
        "computed_at": "2026-06-21T06:05:00+00:00",
        "baseline_source_run_id": "baseline-run", "openmeteo_source_run_id": "om-run",
        "openmeteo_payload_json": "payload.json", "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "30C"}],
    }), encoding="utf-8")
    monkeypatch.setattr(queue, "_blocked_attempt_fingerprint", lambda **_k: "fp-a")
    monkeypatch.setattr(queue, "_seed_source_cycle_boundary", lambda **_k: None)
    monkeypatch.setattr(queue, "_day0_carrier_vector_preflight_reason", lambda **_k: None)

    def runner(argv):
        body = {"status": "BLOCKED", "reason_codes": ["REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET"]}
        if witness == "rewritten":
            body["consumed_inputs"] = _consumed_witness(argv)
            request = Path(argv[argv.index("--input-json") + 1])
            request.write_bytes(request.read_bytes())  # same bytes, new version
        return subprocess.CompletedProcess(list(argv), 1, stdout=json.dumps(body), stderr="")

    report = queue._process_claimed_materialization_batch(
        request_path=requests, processed_path=root / "processed", failed_path=root / "failed",
        forecast_db=db, limit=1, runner=runner,
        marker_dir=root / "blocked_attempts", seed_dir=seeds,
    )
    assert (requests / name).is_file(), "unbound verdict keeps its single owner"
    assert queue._UNBOUND_VERDICT_REASON in report.reason_codes
    assert queue._UNCHANGED_BLOCKED_SKIP_REASON not in report.reason_codes
    assert not report.failed_count
    assert not list((root / "blocked_attempts").glob("*.json"))
    stage = json.loads((requests / f"{name}.stage").read_text())
    assert stage["last_failure"]["verdict_bound_to_inputs"] is False


def test_metadata_era_materialization_receipt_is_never_honored(tmp_path, monkeypatch) -> None:
    """A materialization-blocked receipt without the m2 identity version stays on
    disk as evidence and never fences the producer, even if its fingerprint matches."""
    import src.data.replacement_forecast_live_materialization_queue as queue

    root = tmp_path / "replacement_forecast_live"
    seeds = root / "seeds"
    seeds.mkdir(parents=True)
    db = tmp_path / "forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    seed_file = seeds / "Panama_City.2026-06-22.high.json"
    monkeypatch.setattr(queue, "_blocked_attempt_fingerprint", lambda **_k: "fp-a")
    request = {"city": "Panama City", "target_date": "2026-06-22", "temperature_metric": "high"}
    queue._write_seed_index_receipt(seed_file, {
        "status": "MATERIALIZATION_BLOCKED", "seed_file": str(seed_file),
        "materialization_blocked": {"request": request, "attempt_fingerprint": "fp-a"},
    })
    at = datetime(2026, 6, 21, 6, 6, tzinfo=timezone.utc)
    with sqlite3.connect(db) as conn:
        assert not queue.failed_seed_identity_fenced(seed_file, conn=conn, decision_at=at)
        queue._record_materialization_blocked_identity(
            root / "requests" / seed_file.name, seed_dir=seeds, request_payload=request,
            attempt_fingerprint="fp-a",
        )
        assert queue.failed_seed_identity_fenced(seed_file, conn=conn, decision_at=at)



def test_dependency_record_resolves_like_the_worker_and_follows_the_manifest(tmp_path) -> None:
    """Round-5: one resolver, one record. A fallback-resolved file is identified at
    the path the worker reads, the manifest's artifact is part of the record, and
    the fingerprint (which hashes the record) moves when either one's bytes move."""
    import hashlib as _hashlib

    import scripts.materialize_replacement_forecast_live as worker
    import src.data.replacement_forecast_live_materialization_queue as queue

    request_dir, fallback = tmp_path / "requests", tmp_path / "root"
    request_dir.mkdir()
    fallback.mkdir()
    artifact = fallback / "artifact.json"
    artifact.write_bytes(b'{"a": 1}')
    manifest = fallback / "manifest.json"
    manifest.write_text(json.dumps({"artifact_path": "artifact.json"}))
    (fallback / "precision.json").write_bytes(b"{}")
    payload = {"precision_metadata_json": "precision.json", "openmeteo_manifest_json": "manifest.json"}

    def record():
        return queue._materialization_dependency_record(payload, request_dir=request_dir, root=fallback)

    entries = {e["role"]: e for e in record() or []}
    assert entries["precision_metadata"]["path"] == str((fallback / "precision.json").resolve())
    assert entries["precision_metadata"]["path"] == str(
        worker.resolve_named_input("precision.json", base_dir=request_dir, root=fallback).resolve())
    # An unparseable manifest is still identified by its own bytes.
    assert entries["manifest"]["sha256"] == _hashlib.sha256(manifest.read_bytes()).hexdigest()

    from src.data.raw_forecast_artifact_manifest import RawForecastArtifactManifest

    real = RawForecastArtifactManifest.from_file(
        artifact, source_id=OPENMETEO_SOURCE_ID, product_id=OPENMETEO_PRODUCT_ID,
        data_version=OPENMETEO_HIGH_DATA_VERSION,
        source_cycle_time="2026-06-21T06:00:00+00:00", source_available_at="2026-06-21T06:00:00+00:00",
        captured_at="2026-06-21T06:00:00+00:00", request_url="https://example.invalid",
        request_params={"latitude": 8.97, "longitude": -79.53},
    )
    manifest.write_text(json.dumps({**real.to_dict(), "artifact_path": "artifact.json"}))
    before = {e["role"]: e for e in record() or []}
    assert before["manifest_artifact"]["path"] == str(artifact.resolve())
    artifact.write_bytes(b'{"a": 2}')
    after = {e["role"]: e for e in record() or []}
    assert after["manifest_artifact"]["sha256"] != before["manifest_artifact"]["sha256"]
    # A higher-priority base-relative file appearing changes the resolution.
    (request_dir / "precision.json").write_bytes(b"{}")
    moved = {e["role"]: e for e in record() or []}
    assert moved["precision_metadata"]["path"] == str((request_dir / "precision.json").resolve())


@pytest.mark.parametrize("crossed", ("other_attempt", "other_request_bytes", "foreign_file"))
def test_witness_must_name_the_parent_claim(tmp_path, monkeypatch, crossed) -> None:
    """Round-5: a witness for another invocation, for request bytes other than the
    ones the parent claimed, or naming a file outside the claim's dependency record
    never authorizes a fence. The request is retained, unbound."""
    import subprocess

    import scripts.materialize_replacement_forecast_live as worker
    import src.data.replacement_forecast_live_materialization_queue as queue

    root = tmp_path / "replacement_forecast_live"
    requests, seeds = root / "requests", root / "seeds"
    requests.mkdir(parents=True)
    seeds.mkdir()
    db = tmp_path / "forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    name = "Panama_City.2026-06-22.high.20260621T060500Z.json"
    (requests / name).write_text(json.dumps({
        "city": "Panama City", "target_date": "2026-06-22", "temperature_metric": "high",
        "source_cycle_time": "2026-06-21T06:00:00+00:00",
        "computed_at": "2026-06-21T06:05:00+00:00",
        "baseline_source_run_id": "baseline-run", "openmeteo_source_run_id": "om-run",
        "openmeteo_payload_json": "payload.json", "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "30C"}],
    }), encoding="utf-8")
    monkeypatch.setattr(queue, "_blocked_attempt_fingerprint", lambda **_k: "fp-a")
    monkeypatch.setattr(queue, "_seed_source_cycle_boundary", lambda **_k: None)
    monkeypatch.setattr(queue, "_day0_carrier_vector_preflight_reason", lambda **_k: None)
    foreign = tmp_path / "foreign.json"
    foreign.write_text("{}")

    def runner(argv):
        request = Path(argv[argv.index("--input-json") + 1])
        attempt = worker._StageReceipt(request, None).attempt_id
        consumed = worker._ConsumedInputs("someone-else" if crossed == "other_attempt" else attempt)
        if crossed == "other_request_bytes":
            original = request.read_bytes()
            request.write_text(json.dumps({**json.loads(original), "target_date": "not-a-date"}))
            consumed.read(request, role="request")
            request.write_bytes(original)  # the bytes the parent claimed are back
        else:
            consumed.read(request, role="request")
        if crossed == "foreign_file":
            consumed.read(foreign, role="precision_metadata")
        body = {"status": "BLOCKED", "reason_codes": ["REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET"],
                "consumed_inputs": consumed.witness()}
        return subprocess.CompletedProcess(list(argv), 1, stdout=json.dumps(body), stderr="")

    report = queue._process_claimed_materialization_batch(
        request_path=requests, processed_path=root / "processed", failed_path=root / "failed",
        forecast_db=db, limit=1, runner=runner,
        marker_dir=root / "blocked_attempts", seed_dir=seeds,
    )
    assert (requests / name).is_file()
    assert queue._UNBOUND_VERDICT_REASON in report.reason_codes
    assert not list((root / "blocked_attempts").glob("*.json"))


def test_m2_materialization_receipt_is_evidence_not_a_fence(tmp_path, monkeypatch) -> None:
    import src.data.replacement_forecast_live_materialization_queue as queue

    root = tmp_path / "replacement_forecast_live"
    seeds = root / "seeds"
    seeds.mkdir(parents=True)
    db = tmp_path / "forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    seed_file = seeds / "Panama_City.2026-06-22.high.json"
    monkeypatch.setattr(queue, "_blocked_attempt_fingerprint", lambda **_k: "fp-a")
    request = {"city": "Panama City", "target_date": "2026-06-22", "temperature_metric": "high"}
    queue._write_seed_index_receipt(seed_file, {
        "status": "MATERIALIZATION_BLOCKED", "seed_file": str(seed_file),
        "materialization_blocked": {"request": request, "attempt_fingerprint": "fp-a",
                                    "identity_version": "m2"},
    })
    with sqlite3.connect(db) as conn:
        assert not queue.failed_seed_identity_fenced(
            seed_file, conn=conn, decision_at=datetime(2026, 6, 21, 6, 6, tzinfo=timezone.utc))



def test_computation_blocked_without_typed_evidence_is_retained(tmp_path, monkeypatch) -> None:
    """A BLOCKED whose worker reports no (or unverifiable) typed evidence binds
    nothing, however complete its file witness: retained for fair retry."""
    import subprocess

    import src.data.replacement_forecast_live_materialization_queue as queue

    root = tmp_path / "replacement_forecast_live"
    requests, seeds = root / "requests", root / "seeds"
    requests.mkdir(parents=True)
    seeds.mkdir()
    db = tmp_path / "forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    name = "Panama_City.2026-06-22.high.20260621T060500Z.json"
    (requests / name).write_text(json.dumps({
        "city": "Panama City", "target_date": "2026-06-22", "temperature_metric": "high",
        "source_cycle_time": "2026-06-21T06:00:00+00:00",
        "computed_at": "2026-06-21T06:05:00+00:00",
        "baseline_source_run_id": "baseline-run", "openmeteo_source_run_id": "om-run",
        "openmeteo_payload_json": "payload.json", "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "30C"}],
    }), encoding="utf-8")
    monkeypatch.setattr(queue, "_blocked_attempt_fingerprint", lambda **_k: "fp-a")
    monkeypatch.setattr(queue, "_seed_source_cycle_boundary", lambda **_k: None)
    monkeypatch.setattr(queue, "_day0_carrier_vector_preflight_reason", lambda **_k: None)

    def runner(argv):
        body = {"status": "BLOCKED", "reason_codes": ["REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET"],
                "consumed_inputs": _consumed_witness(argv),
                "blocked_evidence": {"revision": "a-foreign-revision", "items": []}}
        return subprocess.CompletedProcess(list(argv), 1, stdout=json.dumps(body), stderr="")

    report = queue._process_claimed_materialization_batch(
        request_path=requests, processed_path=root / "processed", failed_path=root / "failed",
        forecast_db=db, limit=1, runner=runner,
        marker_dir=root / "blocked_attempts", seed_dir=seeds,
    )
    assert (requests / name).is_file()
    assert queue._UNBOUND_VERDICT_REASON in report.reason_codes
    assert not list((root / "blocked_attempts").glob("*.json"))


def test_fingerprint_keeps_the_dependency_record_when_a_source_is_missing(tmp_path, monkeypatch) -> None:
    """Round-6 BLOCKER 2: a missing configured source is one more identity component;
    the file record is still hashed, so a named-file repair moves the fingerprint."""
    import src.data.replacement_forecast_live_materialization_queue as queue

    db = tmp_path / "forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    precision = request_dir / "precision.json"
    precision.write_text("{}")
    payload = {"city": "Panama City", "target_date": "2026-06-22", "temperature_metric": "high",
               "source_cycle_time": "2026-06-21T06:00:00+00:00",
               "computed_at": "2026-06-21T06:05:00+00:00", "precision_metadata_json": "precision.json"}
    monkeypatch.setattr(queue, "_source_clock_missing_configured_sources", lambda *_a, **_k: ("gfs_hrrr",))
    before = queue._blocked_attempt_fingerprint(input_json=request_dir / "r.json", forecast_db=db, payload=payload)
    precision.write_text("[]")
    after = queue._blocked_attempt_fingerprint(input_json=request_dir / "r.json", forecast_db=db, payload=payload)
    assert before is not None and after is not None and before != after


def _licensed_worker_queue(tmp_path, monkeypatch, *, context_factory=None):
    """The licensed current fixture driven through the real worker and real parent:
    returns (conn, request, consume, fenced, worker) for one claimed request."""
    import dataclasses
    import subprocess
    from datetime import date as _date, datetime as _datetime

    from scripts import materialize_replacement_forecast_live as worker_mod
    import src.data.replacement_forecast_live_materialization_queue as queue
    from src.data.station_ground_evidence import forecast_db_from_connection
    from tests.test_replacement_forecast_materializer_cycle_policy import _licensed_current_context

    def serial(value):
        if isinstance(value, (_datetime, _date)):
            return value.isoformat()
        if dataclasses.is_dataclass(value):
            return {f.name: serial(getattr(value, f.name)) for f in dataclasses.fields(value)}
        if isinstance(value, dict):
            return {k: serial(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [serial(v) for v in value]
        return value

    gen = (context_factory(tmp_path, monkeypatch) if context_factory is not None
           else _licensed_current_context.__wrapped__(tmp_path, monkeypatch))
    conn, request, _provenance, _scope = next(gen)
    root = tmp_path / "queue"
    requests, seeds = root / "requests", root / "seeds"
    requests.mkdir(parents=True)
    seeds.mkdir()
    raw = tmp_path / "worker_payload.json"
    raw.write_bytes(request.openmeteo_raw_payload_bytes)
    precision = tmp_path / "worker_precision.json"
    precision.write_text(json.dumps(serial(request.openmeteo_precision_guard.metadata)))
    payload = {f.name: serial(getattr(request, f.name)) for f in dataclasses.fields(request)
               if f.name not in ("openmeteo_anchor", "openmeteo_precision_guard", "openmeteo_raw_payload_bytes")}
    payload.update(openmeteo_payload_json=str(raw), precision_metadata_json=str(precision),
                   openmeteo_source_cycle_time=request.openmeteo_anchor.source_cycle_time.isoformat(),
                   openmeteo_anchor_artifact_id=request.anchor_artifact_id)
    path = requests / "Shanghai.current.json"
    path.write_text(json.dumps(payload))
    db = forecast_db_from_connection(conn)
    responses: list[dict] = []

    def worker(f=path):
        if not f.exists():  # a fenced request left requests/; validate the same bytes
            f.write_text(json.dumps(payload))
        return worker_mod._run_one(f, commit=False, init_schema=False, conn=conn)

    def consume(during=None):
        def runner(argv):
            f = Path(argv[argv.index("--input-json") + 1])
            code, out, err = worker(f)
            responses.append(json.loads((out or err).strip().splitlines()[-1]))
            if during is not None:
                during()
            return subprocess.CompletedProcess(argv, code, out, err)

        path.write_text(json.dumps(payload))
        return queue._process_claimed_materialization_batch(
            request_path=requests, processed_path=root / "processed", failed_path=root / "failed",
            forecast_db=db, limit=1, runner=runner, marker_dir=root / "blocked_attempts", seed_dir=seeds,
        )

    def fenced():
        return queue.failed_seed_identity_fenced(seeds / path.name, conn=conn, decision_at=request.computed_at)

    worker.request = request
    return gen, conn, consume, fenced, worker, responses, queue


def _zero_extras_context(tmp_path, monkeypatch, metric):
    import src.data.replacement_forecast_materializer as mat
    from tests.test_replacement_forecast_materializer import (
        _hko_native_surfaces, _hko_source_surface, _shanghai_current_owner_request,
        _hko_current_provider_inputs,
    )
    real = mat._replacement_bayes_precision_fusion_override
    native = _hko_native_surfaces.__wrapped__(tmp_path, monkeypatch)
    next(native)
    source = _hko_source_surface.__wrapped__(tmp_path, monkeypatch, None)
    next(source)
    try:
        conn, request = _shanghai_current_owner_request(tmp_path, monkeypatch, metric=metric,
            computed_at=datetime(2026, 10, 1, 8, 15, tzinfo=timezone.utc))
        monkeypatch.setattr(mat, "_replacement_bayes_precision_fusion_override", real)
        body = json.loads(request.openmeteo_raw_payload_bytes)
        _hko_current_provider_inputs(request, {"ecmwf_ifs": 27. if metric == "high" else 18.5},
            conn=conn, selected_cells={"ecmwf_ifs": (body["latitude"], body["longitude"])})
        conn.execute("DELETE FROM raw_model_forecasts WHERE model != 'ecmwf_ifs'")
        conn.commit()
        yield conn, request, None, None
    finally:
        next(source, None)
        next(native, None)


@pytest.mark.parametrize("metric", ("high", "low"))
def test_zero_extras_actual_worker_proves_and_drains_unchanged_inputs(tmp_path, monkeypatch, metric):
    factory = lambda p, m: _zero_extras_context(p, m, metric)
    gen, conn, consume, fenced, worker, responses, queue = _licensed_worker_queue(
        tmp_path, monkeypatch, context_factory=factory)
    try:
        report = consume()
        assert len(responses) == 1 and responses[0]["status"] == "BLOCKED"
        assert responses[0]["blocked_evidence"]["reason"] == "ZERO_MULTI_MODEL_EXTRAS"
        assert queue._UNCHANGED_BLOCKED_SKIP_REASON in report.reason_codes
        assert fenced()
        second = consume()
        assert len(responses) == 1, "normal queue must not respawn the same proved zero-extras input"
        assert queue._UNCHANGED_BLOCKED_SKIP_REASON in second.reason_codes
        assert second.committed_posterior_count == second.reactor_wake_published_count == 0
    finally:
        next(gen, None)


@pytest.mark.parametrize("metric", ("high", "low"))
def test_zero_extras_future_arrival_keeps_old_cut_and_reopens_new_cut(tmp_path, monkeypatch, metric):
    from dataclasses import replace
    from src.data.materialization_block_evidence import evidence_holds
    from tests.test_replacement_forecast_materializer import _hko_current_provider_inputs
    from tests.test_openmeteo_cell_selection_and_elevation_are_product_identity import _selected_test_cell
    from src.config import runtime_cities_by_name

    factory = lambda p, m: _zero_extras_context(p, m, metric)
    gen, conn, consume, fenced, worker, responses, queue = _licensed_worker_queue(
        tmp_path, monkeypatch, context_factory=factory)
    try:
        consume()
        evidence = responses[0]["blocked_evidence"]
        path = tmp_path / "queue/requests/Shanghai.current.json"
        worker()  # Restore exactly the original request bytes for explicit cut checks.
        payload = json.loads(path.read_text())
        assert evidence_holds(conn, evidence, exact_request=payload)
        # Build a real physical current input possessed at 09Z, after the old
        # 08:15Z decision. The carrier itself stays at its old 00Z cycle.
        request = worker.request
        future = replace(request, source_cycle_time=datetime(2026, 10, 1, 6, tzinfo=timezone.utc),
            openmeteo_source_available_at=datetime(2026, 10, 1, 9, tzinfo=timezone.utc),
            computed_at=datetime(2026, 10, 1, 10, tzinfo=timezone.utc))
        city = runtime_cities_by_name()[request.city]
        _hko_current_provider_inputs(future, {"icon_global": 22.}, conn=conn,
            selected_cells={"icon_global": _selected_test_cell("icon_global", city.lat, city.lon)})
        assert evidence_holds(conn, evidence, exact_request=payload), "future arrival cannot rewrite the old cut"
        assert evidence_holds(conn, evidence, payload)
        prospective = {**payload, "computed_at": future.computed_at.isoformat()}
        assert not evidence_holds(conn, evidence, prospective), "a now-possessed lawful extra resets the predicate"
        path.write_text(json.dumps(prospective))
        attempts = []
        def reset_runner(argv):
            import subprocess
            attempts.append(argv)
            return subprocess.CompletedProcess(argv, 1,
                json.dumps({"status": "ERROR", "reason_codes": ["TEST_RESET_ATTEMPT"]}), "")
        queue._process_claimed_materialization_batch(
            request_path=path.parent, processed_path=tmp_path / "queue/processed",
            failed_path=tmp_path / "queue/failed", forecast_db=conn.execute("PRAGMA database_list").fetchone()[2],
            limit=1, runner=reset_runner,
            marker_dir=tmp_path / "queue/blocked_attempts")
        assert len(attempts) == 1
        # RESET authorizes a fresh computation, never READY or a probability.
        assert not queue._blocked_evidence_holds(evidence,
            forecast_db=conn.execute("PRAGMA database_list").fetchone()[2], prospective=prospective)
    finally:
        next(gen, None)


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("mutation", ("missing_item", "revision", "raw_id", "cut", "tau", "config", "empty_served", "read_error"))
def test_zero_extras_unbound_proof_never_fences(tmp_path, monkeypatch, metric, mutation):
    from src.data.materialization_block_evidence import evidence_holds
    import src.data.replacement_forecast_materializer as mat

    factory = lambda p, m: _zero_extras_context(p, m, metric)
    gen, conn, consume, fenced, worker, responses, queue = _licensed_worker_queue(
        tmp_path, monkeypatch, context_factory=factory)
    try:
        consume()
        evidence = json.loads(json.dumps(responses[0]["blocked_evidence"]))
        worker()
        payload = json.loads((tmp_path / "queue/requests/Shanghai.current.json").read_text())
        item = evidence["items"][-1]
        if mutation == "missing_item":
            evidence["items"].pop()
        elif mutation == "revision":
            item["selection_revision"] = "foreign"
        elif mutation == "raw_id":
            item["served"]["ecmwf_ifs"]["raw_model_forecast_id"] += 1
        elif mutation == "cut":
            item["decision_time_iso"] = "2026-10-01T08:16:00+00:00"
        elif mutation == "tau":
            item["day0_remaining_from_iso"] = "2026-10-01T08:00:00+00:00"
        elif mutation == "config":
            monkeypatch.setattr(mat, "_resolve_source_clock_scheme", lambda city, metric:
                SimpleNamespace(weights={"ecmwf_ifs": 1.}))
        elif mutation == "empty_served":
            conn.execute("DELETE FROM raw_model_forecasts")
            conn.commit()
        else:
            conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ else sqlite3.SQLITE_OK)
        assert not evidence_holds(conn, evidence, exact_request=payload)
        assert not evidence_holds(conn, evidence, payload)
    finally:
        conn.set_authorizer(None)
        next(gen, None)


def _split_provider_cohort(conn, cycle):
    conn.execute(
        "UPDATE raw_model_forecasts SET source_cycle_time = ? WHERE model = 'ukmo_global_deterministic_10km'",
        (cycle,),
    )
    conn.commit()


@pytest.mark.parametrize("change", ("in_place_repair", "selection_fills"))
def test_typed_evidence_binds_the_empty_cohort_and_reopens_when_it_heals(
    tmp_path, monkeypatch, change,
) -> None:
    """Round-7: the real worker's REQUIREMENTS_NOT_MET (no coherent current provider
    cohort) carries typed evidence; the real parent fences it; it reopens once a
    coherent pair exists, whether a row is repaired in place (its cycle moves back
    into the window) or the empty selection fills (the provider's row arrives)."""
    gen, conn, consume, fenced, worker, responses, queue = _licensed_worker_queue(tmp_path, monkeypatch)
    columns = [d[1] for d in conn.execute("PRAGMA table_info(raw_model_forecasts)")]
    ukmo = tuple(conn.execute(
        "SELECT * FROM raw_model_forecasts WHERE model = 'ukmo_global_deterministic_10km'"
    ).fetchone())
    original = ukmo[columns.index("source_cycle_time")]

    def restore():
        conn.execute("DELETE FROM raw_model_forecasts WHERE model = 'ukmo_global_deterministic_10km'")
        conn.execute(
            f"INSERT INTO raw_model_forecasts ({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
            ukmo,
        )
        conn.commit()

    try:
        assert worker()[0] == 0
        if change == "in_place_repair":
            _split_provider_cohort(conn, "2026-09-30T18:00:00+00:00")
        else:
            conn.execute("DELETE FROM raw_model_forecasts WHERE model = 'ukmo_global_deterministic_10km'")
            conn.commit()
        report = consume()
        assert responses[0]["status"] == "BLOCKED"
        assert responses[0]["blocked_evidence"]["reason"] == "NO_COHERENT_CURRENT_PROVIDER_COHORT"
        assert queue._UNCHANGED_BLOCKED_SKIP_REASON in report.reason_codes
        assert fenced(), "the evidenced verdict on an unchanged empty cohort binds"
        if change == "in_place_repair":
            _split_provider_cohort(conn, original)
        else:
            restore()
        assert worker()[0] == 0
        assert not fenced(), "the cohort healed; the fence must reopen"
    finally:
        restore()
        next(gen, None)


def test_typed_evidence_aba_never_fences_the_state_the_worker_did_not_judge(tmp_path, monkeypatch) -> None:
    """A->B->A: the worker judges B (cohort split) and the cohort is restored before
    the parent decides. Its evidence no longer holds, so nothing is fenced."""
    gen, conn, consume, fenced, worker, responses, queue = _licensed_worker_queue(tmp_path, monkeypatch)
    original = conn.execute(
        "SELECT source_cycle_time FROM raw_model_forecasts WHERE model = 'ukmo_global_deterministic_10km'"
    ).fetchone()[0]
    try:
        _split_provider_cohort(conn, "2026-09-30T18:00:00+00:00")
        report = consume(during=lambda: _split_provider_cohort(conn, original))
        assert responses[0]["status"] == "BLOCKED" and responses[0].get("blocked_evidence")
        assert queue._UNBOUND_VERDICT_REASON in report.reason_codes
        assert not fenced()
        assert worker()[0] == 0
    finally:
        _split_provider_cohort(conn, original)
        next(gen, None)


def test_unsupported_blocked_reason_stays_unbound(tmp_path, monkeypatch) -> None:
    """A computation BLOCKED with no typed evidence (here an ENS shape failure, which
    reads more than any covered record names) is retained, never fenced."""
    gen, conn, consume, fenced, worker, responses, queue = _licensed_worker_queue(tmp_path, monkeypatch)
    snapshot = conn.execute("SELECT MAX(snapshot_id) FROM ensemble_snapshots").fetchone()[0]
    original = conn.execute(
        "SELECT members_json FROM ensemble_snapshots WHERE snapshot_id = ?", (snapshot,)
    ).fetchone()[0]
    try:
        conn.execute("UPDATE ensemble_snapshots SET members_json = '[]' WHERE snapshot_id = ?", (snapshot,))
        conn.commit()
        report = consume()
        assert responses[0]["status"] == "BLOCKED" and "blocked_evidence" not in responses[0]
        assert queue._UNBOUND_VERDICT_REASON in report.reason_codes
        assert not fenced()
    finally:
        conn.execute("UPDATE ensemble_snapshots SET members_json = ? WHERE snapshot_id = ?", (original, snapshot))
        conn.commit()
        next(gen, None)


def test_m4_receipt_is_evidence_not_a_fence(tmp_path, monkeypatch) -> None:
    import src.data.replacement_forecast_live_materialization_queue as queue

    root = tmp_path / "replacement_forecast_live"
    seeds = root / "seeds"
    seeds.mkdir(parents=True)
    db = tmp_path / "forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    seed_file = seeds / "Panama_City.2026-06-22.high.json"
    monkeypatch.setattr(queue, "_blocked_attempt_fingerprint", lambda **_k: "fp-a")
    request = {"city": "Panama City", "target_date": "2026-06-22", "temperature_metric": "high"}
    queue._write_seed_index_receipt(seed_file, {
        "status": "MATERIALIZATION_BLOCKED", "seed_file": str(seed_file),
        "materialization_blocked": {"request": request, "attempt_fingerprint": "fp-a",
                                    "identity_version": "m4"},
    })
    with sqlite3.connect(db) as conn:
        assert not queue.failed_seed_identity_fenced(
            seed_file, conn=conn, decision_at=datetime(2026, 6, 21, 6, 6, tzinfo=timezone.utc))


def test_cert_regression_and_stale_cycle_evidence_reverify_their_own_rows(tmp_path) -> None:
    """The two other covered reasons hold exactly while their judged rows hold:
    the incumbent certificate and its posterior's serving key; the clock's
    source_run possession rows."""
    from datetime import datetime as _datetime, timezone as _tz
    from types import SimpleNamespace

    from src.data.materialization_block_evidence import (
        CERT_REGRESSION, STALE_CYCLE, blocked_evidence, cert_regression_item, evidence_holds,
    )

    db = tmp_path / "f.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE source_run (source_run_id TEXT PRIMARY KEY, fetch_finished_at TEXT);
        CREATE TABLE readiness_state (scope_key TEXT PRIMARY KEY, source_run_id TEXT);
        CREATE TABLE forecast_posteriors (posterior_id INTEGER PRIMARY KEY,
            source_cycle_time TEXT, computed_at TEXT);
        INSERT INTO source_run VALUES ('base', '2026-10-01T09:00:00+00:00');
        INSERT INTO readiness_state VALUES ('scope', 'posterior:7');
        INSERT INTO forecast_posteriors VALUES (7, '2026-10-01T12:00:00+00:00', '2026-10-01T13:00:00+00:00');
    """)
    conn.commit()
    request = SimpleNamespace(baseline_source_run_id="base", openmeteo_source_run_id="om")
    utc = lambda h: _datetime(2026, 10, 1, h, tzinfo=_tz.utc)  # noqa: E731
    cert = blocked_evidence(conn, request, CERT_REGRESSION, [cert_regression_item(
        scope_key="scope", incumbent_posterior_id=7,
        incumbent_key=(utc(12), utc(13)), incoming_key=(utc(6), utc(14)),
    )])
    stale = blocked_evidence(conn, request, STALE_CYCLE)
    assert evidence_holds(conn, cert) and evidence_holds(conn, stale)
    conn.execute("UPDATE readiness_state SET source_run_id = 'posterior:8'")
    conn.commit()
    assert not evidence_holds(conn, cert), "a newer certificate ends the regression"
    conn.execute("UPDATE readiness_state SET source_run_id = 'posterior:7'")
    conn.execute("UPDATE source_run SET fetch_finished_at = '2026-10-01T10:00:00+00:00'")
    conn.commit()
    assert not evidence_holds(conn, stale), "a moved possession clock moves computed_at"
    assert not evidence_holds(conn, cert)
    conn.execute("INSERT INTO source_run VALUES ('om', '2026-10-01T09:30:00+00:00')")
    conn.execute("UPDATE source_run SET fetch_finished_at = '2026-10-01T09:00:00+00:00' WHERE source_run_id='base'")
    conn.commit()
    assert not evidence_holds(conn, stale), "an absent possession row that appears is a change"
    conn.execute("DELETE FROM source_run WHERE source_run_id = 'om'")
    conn.commit()
    assert evidence_holds(conn, stale) and evidence_holds(conn, cert)
    for broken in ({}, {"revision": "x", "items": []}, {**stale, "items": stale["items"][1:]}):
        assert not evidence_holds(conn, broken)
    conn.close()


def _cert_payload(computed_at: str, **extra) -> dict:
    return {
        "city": "Shanghai", "target_date": "2026-10-02", "temperature_metric": "high",
        "source_cycle_time": "2026-10-01T00:00:00+00:00", "computed_at": computed_at,
        "baseline_source_run_id": "base", "openmeteo_source_run_id": "om",
        "baseline_source_available_at": "2026-10-01T06:00:00+00:00",
        "openmeteo_source_available_at": "2026-10-01T06:00:00+00:00",
        **extra,
    }


def _cert_db(tmp_path):
    db = tmp_path / "f.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE source_run (source_run_id TEXT PRIMARY KEY, fetch_finished_at TEXT);
        CREATE TABLE readiness_state (scope_key TEXT PRIMARY KEY, source_run_id TEXT);
        CREATE TABLE forecast_posteriors (posterior_id INTEGER PRIMARY KEY,
            source_cycle_time TEXT, computed_at TEXT);
        INSERT INTO readiness_state VALUES ('scope', 'posterior:7');
        INSERT INTO forecast_posteriors VALUES (7, '2026-10-01T00:00:00+00:00', '2026-10-01T08:15:00+00:00');
    """)
    conn.commit()
    return conn


@pytest.mark.parametrize(("prospective_at", "blocks"), (
    ("2026-10-01T08:14:00+00:00", True),   # still behind the incumbent
    ("2026-10-01T08:15:00+00:00", False),  # equal key: no strict regression
    ("2026-10-01T08:16:00+00:00", False),  # ahead of the incumbent
))
def test_cert_evidence_is_redecided_for_the_prospective_request(tmp_path, prospective_at, blocks) -> None:
    """Round-8 BLOCKER: the fence holds only while the request the producer would
    build now still regresses the certificate, at that request's effective clock."""
    from datetime import datetime as _datetime, timezone as _tz
    from types import SimpleNamespace

    from src.data.materialization_block_evidence import (
        CERT_REGRESSION, blocked_evidence, cert_regression_item, evidence_holds,
    )

    conn = _cert_db(tmp_path)
    utc = lambda h, m=0: _datetime(2026, 10, 1, h, m, tzinfo=_tz.utc)  # noqa: E731
    recorded = _cert_payload("2026-10-01T08:13:00+00:00")
    evidence = blocked_evidence(conn, SimpleNamespace(**recorded), CERT_REGRESSION, [cert_regression_item(
        scope_key="scope", incumbent_posterior_id=7,
        incumbent_key=(utc(0), utc(8, 15)), incoming_key=(utc(0), utc(8, 13)),
    )])
    assert evidence_holds(conn, evidence)  # admission: the judged facts hold
    assert evidence_holds(conn, evidence, _cert_payload(prospective_at)) is blocks
    # The possession clock lifts the prospective key: a role possessed after the
    # incumbent's computed_at ends the regression for any requested computed_at.
    conn.execute("INSERT INTO source_run VALUES ('base', '2026-10-01T08:20:00+00:00')")
    conn.commit()
    assert not evidence_holds(conn, evidence, _cert_payload("2026-10-01T08:14:00+00:00"))
    conn.close()


def test_evidence_binds_only_its_own_scope_and_roles(tmp_path) -> None:
    from types import SimpleNamespace

    from src.data.materialization_block_evidence import STALE_CYCLE, blocked_evidence, evidence_holds

    conn = _cert_db(tmp_path)
    stale = _cert_payload("2026-10-01T08:14:00+00:00", openmeteo_source_cycle_time="2026-08-01T00:00:00+00:00")
    evidence = blocked_evidence(conn, SimpleNamespace(**stale), STALE_CYCLE)
    assert evidence_holds(conn, evidence, stale)
    for change in ({"city": "Beijing"}, {"temperature_metric": "low"}, {"baseline_source_run_id": "other"}):
        assert not evidence_holds(conn, evidence, {**stale, **change}), change
    # A fresh anchor cycle at the prospective clock is no longer stale.
    assert not evidence_holds(conn, evidence, {**stale, "openmeteo_source_cycle_time": "2026-10-01T00:00:00+00:00"})
    conn.close()


def _cert_supersession_context(tmp_path, monkeypatch, metric):
    """Real final-write refusal and queue retention; upstream q is already prepared."""
    from dataclasses import replace
    import src.data.replacement_forecast_materializer as mat
    import src.data.replacement_forecast_live_materialization_queue as queue
    from src.state.readiness_repo import write_readiness_state
    from tests.test_replacement_forecast_materializer import _request

    db = tmp_path / "supersession.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ensure_replacement_forecast_live_schema(conn)
    from src.state.db import _create_readiness_state
    _create_readiness_state(conn)
    conn.execute("CREATE TABLE source_run (source_run_id TEXT PRIMARY KEY, source_id TEXT, dataset_id TEXT, fetch_finished_at TEXT)")
    at = lambda h, m=0: datetime(2026, 10, 2, h, m, tzinfo=UTC)
    req = replace(_request(), city="Hong Kong", city_id="Hong Kong",
        city_timezone="Asia/Hong_Kong", target_date=date(2026, 10, 3),
        temperature_metric=metric, source_cycle_time=at(6), computed_at=at(15, 19),
        baseline_source_run_id="base", openmeteo_source_run_id="om",
        baseline_source_available_at=at(7), openmeteo_source_available_at=at(7))
    version = expected_replacement_dependency_identity_by_role(metric)["baseline_b0"].data_version
    conn.execute("INSERT INTO source_run VALUES ('base', 'ecmwf_open_data', ?, ?)", (version, at(7).isoformat()))
    for computed in (at(16, 8), req.computed_at):
        _insert_posterior(conn, city=req.city, target_date=req.target_date.isoformat(),
            metric=metric, cycle_iso=at(6).isoformat(), computed_at=computed.isoformat())
    conn.execute("UPDATE forecast_posteriors SET dependency_source_run_ids_json=? WHERE posterior_id=1",
        (json.dumps({"baseline_b0": "base"}),))
    identity = expected_replacement_dependency_identity_by_role(metric)["soft_anchor_posterior"]
    write_readiness_state(conn, readiness_id="incumbent", scope_type="strategy", status="LIVE_ELIGIBLE",
        computed_at=at(16, 8), expires_at=at(23), city_id=req.city_id, city=req.city,
        city_timezone=req.city_timezone, target_local_date=req.target_date,
        temperature_metric=metric, physical_quantity=identity.physical_quantity,
        observation_field=identity.observation_field, data_version=mat._data_version(metric),
        strategy_key=mat.STRATEGY_KEY, source_id=mat.SOURCE_ID,
        track="soft_anchor_posterior", source_run_id="posterior:1")
    conn.commit()
    monkeypatch.setattr(mat, "_day0_ledger_frontier_identity", lambda *_a, **_k: None)
    monkeypatch.setattr(mat, "_write_posterior_row", lambda *_a, **_k: 2)
    monkeypatch.setattr(mat, "_build_readiness", lambda *_a, **_k: None)
    prepared = mat.PreparedReplacementForecastMaterialization(req, metric,
        SimpleNamespace(live_eligible=True), None, anchor_id=1)
    requests = tmp_path / "requests"
    requests.mkdir()
    payload = {"city": req.city, "target_date": req.target_date.isoformat(), "temperature_metric": metric,
        "source_cycle_time": at(6).isoformat(), "computed_at": req.computed_at.isoformat(),
        "baseline_source_run_id": "base", "openmeteo_source_run_id": "om",
        "baseline_source_available_at": at(7).isoformat(), "openmeteo_source_available_at": at(7).isoformat(),
        "bins": [{"bin_id": "20C"}]}
    old = requests / "old.json"
    old.write_text(json.dumps(payload))
    monkeypatch.setattr(queue, "_blocked_attempt_fingerprint", lambda **_k: "exact-inputs")
    monkeypatch.setattr(queue, "_seed_source_cycle_boundary", lambda **_k: None)
    monkeypatch.setattr(queue, "_day0_carrier_vector_preflight_reason", lambda **_k: None)
    monkeypatch.setattr(queue, "_priority_map_with_names", lambda *_a, **_k: ({"old.json": (0, ""), "new.json": (0, "")}, {"old.json", "new.json"}))
    return conn, prepared, requests, old, payload, db, queue


@pytest.mark.parametrize(("metric", "mutation"), [
    (metric, mutation)
    for metric in ("low", "high")
    for mutation in (None, "incumbent", "dependency", "dataset", "unreadable_basis", "missing_witness", "missing_evidence", "foreign_request", "request_file")
    if metric == "low" or mutation not in {"dataset", "unreadable_basis"}
])
def test_exact_cert_supersession_drains_only_proved_old_request(tmp_path, monkeypatch, metric, mutation):
    import subprocess
    import src.data.replacement_forecast_materializer as mat
    from src.data.materialization_block_evidence import evidence_holds

    conn, prepared, requests, old, payload, db, queue = _cert_supersession_context(tmp_path, monkeypatch, metric)
    def runner(argv):
        result = mat.write_prepared_replacement_forecast_live(conn, prepared)
        conn.commit()
        assert result.status == "BLOCKED" and result.reason_codes == ("READINESS_CERT_CYCLE_REGRESSION",)
        assert result.evidence is not None, "the actual refusal must prove exact-request supersession"
        assert result.evidence["reason"] == "READINESS_CERT_SUPERSEDED"
        assert not evidence_holds(conn, result.evidence, payload), "never a prospective family fence"
        body = {"status": result.status, "reason_codes": list(result.reason_codes),
            "blocked_evidence": result.evidence, "consumed_inputs": _consumed_witness(argv),
            "posterior_id": result.posterior_id, "committed": True, "reactor_wake_published": False}
        if mutation == "incumbent":
            conn.execute("UPDATE readiness_state SET source_run_id='posterior:2'")
        elif mutation == "dependency":
            conn.execute("UPDATE source_run SET fetch_finished_at='2026-10-02T18:00:00+00:00' WHERE source_run_id='base'")
        elif mutation == "dataset":
            conn.execute("UPDATE source_run SET dataset_id='changed' WHERE source_run_id='base'")
        elif mutation == "unreadable_basis":
            import src.data.replacement_forecast_source_run_identity as identities
            def unavailable(_metric):
                raise OSError("coordinate manifest unavailable")
            monkeypatch.setattr(identities, "expected_replacement_dependency_identity_by_role", unavailable)
        elif mutation == "missing_witness":
            body.pop("consumed_inputs")
        elif mutation == "missing_evidence":
            body.pop("blocked_evidence")
        elif mutation == "foreign_request":
            body["blocked_evidence"]["scope"]["target_date"] = "2026-10-04"
        elif mutation == "request_file":
            # The real consumed-input version/SHA guard, not the controlled
            # DB/fingerprint fixture, must reject a changed claimed body.
            claimed = Path(argv[argv.index("--input-json") + 1])
            changed = json.loads(claimed.read_text())
            changed["bins"] = [{"bin_id": "21C"}]
            claimed.write_text(json.dumps(changed))
            assert not queue._consumed_inputs_unchanged(body["consumed_inputs"])
        conn.commit()
        return subprocess.CompletedProcess(argv, 1, json.dumps(body), "")

    report = queue._process_claimed_materialization_batch(request_path=requests,
        processed_path=tmp_path / "processed", failed_path=tmp_path / "failed",
        forecast_db=db, limit=1, runner=runner, marker_dir=tmp_path / "blocked_attempts")
    retained = mutation is not None
    assert old.exists() is retained
    assert report.committed_posterior_count == report.reactor_wake_published_count == 0
    assert not list((tmp_path / "blocked_attempts").glob("*.json"))
    if retained:
        assert queue._UNBOUND_VERDICT_REASON in report.reason_codes
    else:
        receipt = json.loads((tmp_path / "superseded_latest" / f"Hong_Kong.2026-10-03.{metric}.json").read_text())
        assert receipt["status"] == "SKIPPED_READINESS_CERT_SUPERSEDED"
        newer = requests / "new.json"
        newer.write_text(json.dumps({**payload, "computed_at": "2026-10-02T18:30:52+00:00"}))
        plan = queue._build_request_claim_read_plan(request_path=requests,
            processed_path=tmp_path / "processed", failed_path=tmp_path / "failed",
            forecast_db=db, limit=1, lane=queue.MATERIALIZATION_LANE_PRIORITY)
        assert plan.claim.selected_files == (newer,)
    conn.close()


def test_retired_low_incumbent_never_gets_the_current_dataset_supersession_proof(tmp_path, monkeypatch):
    from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_LOW_DATA_VERSION_UNCERTIFIED, coordinate_bound_data_version
    import src.data.replacement_forecast_materializer as mat
    conn, prepared, *_ = _cert_supersession_context(tmp_path, monkeypatch, "low")
    current = conn.execute("SELECT dataset_id FROM source_run WHERE source_run_id='base'").fetchone()[0]
    retired = coordinate_bound_data_version(ECMWF_OPENDATA_LOW_DATA_VERSION_UNCERTIFIED, current.rsplit("__coordsha_", 1)[1])
    conn.execute("UPDATE source_run SET dataset_id=? WHERE source_run_id='base'", (retired,))
    result = mat.write_prepared_replacement_forecast_live(conn, prepared)
    assert result.reason_codes == ("READINESS_CERT_CYCLE_REGRESSION",)
    assert result.evidence is None
    conn.close()


def test_exact_cert_supersession_preserves_proven_retired_low_yield(monkeypatch):
    """The real retired-to-current ENS proof still overrides the cycle guard."""
    from dataclasses import replace
    import src.data.replacement_forecast_materializer as mat
    from src.data.replacement_input_hwm import retired_low_uncertified_incumbent_yields_to_current_ensemble
    from src.state.readiness_repo import write_readiness_state
    from tests.test_replacement_forecast_materializer import _low_revision_authority_conn, _request, _hko_dt

    conn = _low_revision_authority_conn()
    request = replace(_request(), city="Hong Kong", city_id="Hong Kong", city_timezone="Asia/Hong_Kong",
        target_date=date(2026, 10, 1), temperature_metric="low", source_cycle_time=_hko_dt(12),
        baseline_source_run_id="new12", computed_at=_hko_dt(20))
    identity = expected_replacement_dependency_identity_by_role("low")["soft_anchor_posterior"]
    write_readiness_state(conn, readiness_id="retired-incumbent", scope_type="strategy", status="LIVE_ELIGIBLE",
        computed_at=_hko_dt(20), expires_at=_hko_dt(23), city_id=request.city_id, city=request.city,
        city_timezone=request.city_timezone, target_local_date=request.target_date, temperature_metric="low",
        physical_quantity=identity.physical_quantity, observation_field=identity.observation_field,
        data_version=mat._data_version("low"), strategy_key=mat.STRATEGY_KEY, source_id=mat.SOURCE_ID,
        track="soft_anchor_posterior", source_run_id="posterior:1")
    _insert_posterior(conn, city=request.city, target_date=request.target_date.isoformat(), metric="low",
        cycle_iso=request.source_cycle_time.isoformat(), computed_at=request.computed_at.isoformat())
    incoming = conn.execute("SELECT max(posterior_id) FROM forecast_posteriors").fetchone()[0]
    assert retired_low_uncertified_incumbent_yields_to_current_ensemble(conn,
        city=request.city, target_date=request.target_date, metric="low", incoming_baseline_source_run_id="new12",
        decision_time=request.computed_at, incumbent_posterior_id=1)
    assert mat._readiness_cert_cycle_regression_reasons(conn, request, metric="low", incoming_posterior_id=incoming) == ()
    assert mat._cert_regression_evidence(conn, request, metric="low",
        incoming_posterior_id=incoming, exact_supersession=True) is None
    conn.close()


def test_unreadable_clock_row_is_unavailable_not_absent(tmp_path) -> None:
    """Round-8 HIGH: a failed source_run read never equals a recorded absence;
    a missing table is a proven absence."""
    from types import SimpleNamespace

    from src.data.materialization_block_evidence import STALE_CYCLE, blocked_evidence, evidence_holds

    class Failing(sqlite3.Connection):
        broken = False

        def execute(self, sql, parameters=(), /):
            if self.broken and sql.startswith("SELECT fetch_finished_at FROM source_run"):
                raise sqlite3.OperationalError("disk I/O error")
            return super().execute(sql, parameters)

    conn = sqlite3.connect(tmp_path / "f.db", factory=Failing)
    conn.execute("CREATE TABLE source_run (source_run_id TEXT PRIMARY KEY, fetch_finished_at TEXT)")
    conn.commit()
    stale = _cert_payload("2026-10-01T08:14:00+00:00", openmeteo_source_cycle_time="2026-08-01T00:00:00+00:00")
    evidence = blocked_evidence(conn, SimpleNamespace(**stale), STALE_CYCLE)
    assert evidence_holds(conn, evidence) and evidence_holds(conn, evidence, stale)
    conn.broken = True
    assert not evidence_holds(conn, evidence) and not evidence_holds(conn, evidence, stale)
    conn.broken = False
    conn.execute("DROP TABLE source_run")
    conn.commit()
    assert evidence_holds(conn, evidence, stale), "a missing source_run table is proven absence"
    conn.close()


@pytest.mark.parametrize("change", (
    "unknown_reason", "cert_without_its_item", "empty_clock", "foreign_clock_role",
    "low_cert", "extra_item",
))
def test_malformed_typed_evidence_binds_nothing(tmp_path, change) -> None:
    """Round-8 MEDIUM: a supported reason, exactly its items, both clock roles,
    the prospective scope; anything else is unbound."""
    import copy
    from types import SimpleNamespace

    from src.data.materialization_block_evidence import (
        CERT_REGRESSION, STALE_CYCLE, blocked_evidence, evidence_holds,
    )

    conn = _cert_db(tmp_path)
    stale = _cert_payload("2026-10-01T08:14:00+00:00", openmeteo_source_cycle_time="2026-08-01T00:00:00+00:00")
    evidence = blocked_evidence(conn, SimpleNamespace(**stale), STALE_CYCLE)
    assert evidence_holds(conn, evidence, stale)
    broken = copy.deepcopy(evidence)
    payload = stale
    if change == "unknown_reason":
        broken["reason"] = "SOMETHING_ELSE"
    elif change == "cert_without_its_item":
        broken["reason"] = CERT_REGRESSION
    elif change == "empty_clock":
        broken["items"][0]["source_runs"] = []
    elif change == "foreign_clock_role":
        broken["items"][0]["source_runs"][0]["role"] = "other"
    elif change == "low_cert":
        broken["reason"] = CERT_REGRESSION
        broken["items"].append({"kind": CERT_REGRESSION, "scope_key": "scope",
                                "incumbent_source_run_id": "posterior:7", "incumbent_posterior_id": 7,
                                "incumbent_key": [], "incoming_key": []})
        broken["scope"]["temperature_metric"] = "low"
        payload = {**stale, "temperature_metric": "low"}
    else:
        broken["items"].append(dict(broken["items"][0]))
    assert not evidence_holds(conn, broken, payload)
    assert not evidence_holds(conn, broken)
    conn.close()


def test_empty_cohort_is_redecided_at_the_prospective_cut(tmp_path, monkeypatch) -> None:
    """Round-8 BLOCKER: the negative selection is re-run at the prospective
    request's clock, so a provider possessed after the original cut reopens it."""
    from datetime import datetime as _datetime, timedelta as _timedelta

    gen, conn, consume, fenced, worker, responses, queue = _licensed_worker_queue(tmp_path, monkeypatch)
    columns = [d[1] for d in conn.execute("PRAGMA table_info(raw_model_forecasts)")]
    ukmo = [tuple(r) for r in conn.execute(
        "SELECT * FROM raw_model_forecasts WHERE model = 'ukmo_global_deterministic_10km'")]

    def put(rows):
        conn.execute("DELETE FROM raw_model_forecasts WHERE model = 'ukmo_global_deterministic_10km'")
        conn.executemany(
            f"INSERT INTO raw_model_forecasts ({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
            rows,
        )
        conn.commit()

    root = tmp_path / "queue"
    path = root / "requests" / "Shanghai.current.json"
    payload = json.loads(path.read_text())
    cut = _datetime.fromisoformat(payload["computed_at"])
    try:
        put([])
        consume()
        assert responses[-1]["blocked_evidence"]["reason"] == "NO_COHERENT_CURRENT_PROVIDER_COHORT"
        seed = root / "seeds" / path.name
        assert queue.failed_seed_identity_fenced(seed, conn=conn, decision_at=cut)
        captured = columns.index("captured_at")
        put([tuple((cut + _timedelta(minutes=1)).isoformat() if i == captured else v
                   for i, v in enumerate(row)) for row in ukmo])
        assert queue.failed_seed_identity_fenced(seed, conn=conn, decision_at=cut), \
            "not yet possessed at the original cut: still fenced"
        later = cut + _timedelta(minutes=2)
        assert not queue.failed_seed_identity_fenced(seed, conn=conn, decision_at=later), \
            "possessed by the prospective cut: the fence reopens"
    finally:
        put(ukmo)
        next(gen, None)
