# Created: 2026-07-08
# Purpose: R3 (ingest contractualization) — SourceContract row validation, generic scheduler
#   cursor-diff idempotency, and the station-clock-law antibody (cwa/hko never gated behind the
#   gridded freshness ceiling).
# Authority basis: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R3;
#   docs/rebuild/whole_system_first_principles_2026-07-07.md §2.1.
"""Red-first tests for src/ingest/contract.py + src/ingest/scheduler.py (R3 packet)."""
from __future__ import annotations

from src.ingest.contract import SourceContract, clock_law_for, source_contracts
from src.ingest.scheduler import (
    InMemoryCursorStore,
    SourceRunTickResult,
    run_all_due,
    run_source_contract_tick,
)


# ---------------------------------------------------------------------------
# SourceContract row validation
# ---------------------------------------------------------------------------

def test_every_registry_row_has_a_source_id_and_a_clock_law():
    rows = source_contracts()
    assert rows, "SOURCE_CONTRACTS registry must not be empty"
    for source_id, row in rows.items():
        assert isinstance(row, SourceContract)
        assert row.source_id == source_id
        assert row.clock_law in ("gridded_ceiling", "own_clock")


def test_registry_covers_the_five_concern_anchor_row_with_its_dependents():
    """Concerns #1/#4/#5 of _replacement_cycle_availability_poll_if_needed land on the anchor row."""
    rows = source_contracts()
    anchor = rows["openmeteo_ecmwf_ifs_9km"]
    assert anchor.clock_law == "gridded_ceiling"
    assert set(anchor.dependents) == {"fusion_upgrade_reseed", "cycle_advance_reseed"}


def test_registry_covers_the_five_concern_extras_row_independently():
    """Concern #3 (bayes_precision_fusion extras) is its own row, NOT sharing the anchor's cursor."""
    rows = source_contracts()
    extras = rows["bayes_precision_fusion_extras"]
    assert extras.clock_law == "gridded_ceiling"
    assert extras.dependents == ()  # no downstream reseed triggers of its own


def test_live_authorization_and_backfill_only_are_properties_not_stored_fields():
    """Anti-duplication law (2026-05-24, preserved through the R3 relocation): authority facts
    must delegate to TemporalPolicy, never be re-declared as independent dataclass fields."""
    field_names = set(SourceContract.__dataclass_fields__)
    assert "live_authorization" not in field_names
    assert "backfill_only" not in field_names


# ---------------------------------------------------------------------------
# Station-clock law antibody (notepad law, verbatim: "station sources (cwa/hko) carry their own
# provider cycle clock — never gate them behind the gridded freshness ceiling")
# ---------------------------------------------------------------------------

def test_clock_law_for_station_prefixes_is_always_own_clock():
    assert clock_law_for("hko_fnd", family="forecast") == "own_clock"
    assert clock_law_for("cwa_township", family="forecast") == "own_clock"
    # Even if some future registry entry mislabels a station source's family as "forecast"
    # gridded — the prefix check wins; station identity is not overridable by a family typo.
    assert clock_law_for("hko_daily_api", family="forecast") == "own_clock"


def test_non_forecast_families_are_never_gridded_ceiling():
    assert clock_law_for("wu_icao_history", family="observation") == "own_clock"
    assert clock_law_for("openmeteo_archive", family="solar") == "own_clock"


def test_registry_rows_for_hko_and_cwa_are_own_clock():
    rows = source_contracts()
    assert rows["hko_fnd"].clock_law == "own_clock"
    assert rows["cwa_township"].clock_law == "own_clock"


def test_scheduler_never_gates_an_own_clock_row_behind_the_gridded_ceiling():
    """ANTIBODY: even when the gridded ceiling is CLOSED (not ready), an own_clock row's tick
    must still proceed to clock_check — the gate must be structurally unreachable for these rows,
    not merely unconsulted by convention."""
    station_row = source_contracts()["hko_fnd"]
    cursor_store = InMemoryCursorStore()

    result = run_source_contract_tick(
        station_row,
        clock_check=lambda: "2026-07-08T00:00:00+00:00",
        cursor_store=cursor_store,
        gridded_ceiling_ready=lambda: False,  # gridded basket is NOT ready this tick
    )

    assert result.status != "GATED_BY_GRIDDED_CEILING"
    assert result.status == "ARRIVED"


def test_scheduler_gates_a_gridded_ceiling_row_when_ceiling_not_ready():
    anchor_row = source_contracts()["openmeteo_ecmwf_ifs_9km"]
    cursor_store = InMemoryCursorStore()

    result = run_source_contract_tick(
        anchor_row,
        clock_check=lambda: "2026-07-08T00:00:00+00:00",
        cursor_store=cursor_store,
        gridded_ceiling_ready=lambda: False,
    )

    assert result.status == "GATED_BY_GRIDDED_CEILING"
    assert cursor_store.get("openmeteo_ecmwf_ifs_9km") is None  # no cursor advance while gated


# ---------------------------------------------------------------------------
# Cursor-diff idempotency (generalizing source_clock_update_probe.py's shape)
# ---------------------------------------------------------------------------

def test_same_cursor_value_twice_produces_exactly_one_arrival():
    row = source_contracts()["hko_fnd"]
    cursor_store = InMemoryCursorStore()
    call_count = {"n": 0}

    def clock_check() -> str:
        call_count["n"] += 1
        return "run-2026-07-08T00Z"  # provider metadata unchanged across both ticks

    first = run_source_contract_tick(row, clock_check=clock_check, cursor_store=cursor_store)
    second = run_source_contract_tick(row, clock_check=clock_check, cursor_store=cursor_store)

    assert first.status == "ARRIVED"
    assert second.status == "NO_CHANGE"
    assert call_count["n"] == 2  # clock_check runs every tick; only the FIRST commits an arrival


def test_a_changed_cursor_produces_a_second_arrival():
    row = source_contracts()["hko_fnd"]
    cursor_store = InMemoryCursorStore()
    values = iter(["run-A", "run-A", "run-B"])

    results = [
        run_source_contract_tick(row, clock_check=lambda: next(values), cursor_store=cursor_store)
        for _ in range(3)
    ]

    assert [r.status for r in results] == ["ARRIVED", "NO_CHANGE", "ARRIVED"]


def test_clock_check_returning_none_is_a_fail_soft_skip_not_a_crash():
    row = source_contracts()["hko_fnd"]
    cursor_store = InMemoryCursorStore()

    result = run_source_contract_tick(row, clock_check=lambda: None, cursor_store=cursor_store)

    assert result.status == "SKIPPED"
    assert cursor_store.get("hko_fnd") is None


def test_arrival_dispatches_the_row_dependents_exactly_once():
    row = source_contracts()["openmeteo_ecmwf_ifs_9km"]
    cursor_store = InMemoryCursorStore()
    dispatched: list[str] = []

    run_source_contract_tick(
        row,
        clock_check=lambda: "cycle-2026-07-08T00Z",
        cursor_store=cursor_store,
        dependents_dispatch=lambda name, contract: dispatched.append(name),
    )

    assert dispatched == ["fusion_upgrade_reseed", "cycle_advance_reseed"]


def test_run_all_due_drives_every_row_generically():
    """Goal 3: ONE generic scheduler loop drives ALL SourceContract rows, not a per-source poll
    function each."""
    rows = list(source_contracts().values())
    cursor_store = InMemoryCursorStore()

    results = run_all_due(
        rows,
        clock_check_for=lambda contract: (lambda: f"cursor-for-{contract.source_id}"),
        cursor_store=cursor_store,
        gridded_ceiling_ready=lambda: True,
    )

    assert {r.source_id for r in results} == {row.source_id for row in rows}
    assert all(isinstance(r, SourceRunTickResult) for r in results)
    assert all(r.status == "ARRIVED" for r in results)
