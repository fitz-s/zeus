# Created: 2026-04-24
# Last reused/audited: 2026-04-30
# Authority basis: POST_AUDIT_HANDOFF_2026-04-24 §3.1 C6; task_2026-04-29 Phase 1B F08 learning guard; task_2026-04-29 Phase 5C.4 settlements_v2 producer; task_2026-04-29 Phase 5C.5 market_events_v2 outcome producer
# Purpose: INV-14 identity spine antibody for harvester settlement writes —
#          pins temperature_metric / physical_quantity / observation_field to
#          canonical HIGH_LOCALDAY_MAX.* so regression to the legacy literal
#          "daily_maximum_air_temperature" fails the test.
# Reuse: Covers src/execution/harvester.py::_write_settlement_truth VERIFIED
#        write path. Residual: 1,561 pre-fix settlement rows on the live DB
#        still carry legacy physical_quantity; historical-data migration is
#        owed but out of scope for this packet.
# Authority basis: POST_AUDIT_HANDOFF_2026-04-24 §3.1 C6 + INV-14 identity spine
#   defined at src/types/metric_identity.py
"""INV-14 identity spine antibody for harvester settlement writes.

Before this antibody, `src/execution/harvester.py::_write_settlement_truth`
hardcoded the settlement's INV-14 identity fields (`temperature_metric`,
`physical_quantity`, `observation_field`) to literal strings
`"high" / "daily_maximum_air_temperature" / "high_temp"`. The
`physical_quantity` literal diverged from canonical
`HIGH_LOCALDAY_MAX.physical_quantity = "mx2t6_local_calendar_day_max"`,
so any downstream JOIN filtering on canonical physical_quantity silently
dropped 100% of harvester-written settlement rows.

This test dry-runs the harvester write and asserts the row carries the
canonical `HIGH_LOCALDAY_MAX.*` identity values. If a future refactor
re-introduces a hardcoded divergent string, this test fires.

Residual: 1,561 pre-fix settlement rows on the live DB still carry
`physical_quantity="daily_maximum_air_temperature"`. A historical-data
migration is owed but is out of scope for this packet (would require
src/state/** changes and is NEEDS_OPERATOR_DECISION).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest.economics import check_economics_readiness
from src.config import City
from src.execution import harvester as harvester_mod
from src.state.db import init_schema, log_market_event_outcome_v2, log_settlement_v2
from src.state.schema.v2_schema import apply_v2_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX


def _make_city(name: str = "testville") -> City:
    return City(
        name=name,
        lat=41.8781,
        lon=-87.6298,
        timezone="America/Chicago",
        settlement_unit="F",
        cluster="north",
        wu_station="KORD",
        settlement_source="KORD",
        country_code="US",
        settlement_source_type="wu_icao",
    )


def _insert_market_event_v2(
    conn,
    *,
    market_slug: str,
    condition_id: str,
    token_id: str,
    city: str = "testville",
    target_date: str = "2026-04-24",
    temperature_metric: str = "high",
    range_label: str = "65-75°F",
    range_low: float | None = 65.0,
    range_high: float | None = 75.0,
    outcome: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO market_events_v2 (
            market_slug, city, target_date, temperature_metric,
            condition_id, token_id, range_label, range_low, range_high,
            outcome, created_at, recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market_slug,
            city,
            target_date,
            temperature_metric,
            condition_id,
            token_id,
            range_label,
            range_low,
            range_high,
            outcome,
            "2026-04-23T12:00:00Z",
            "2026-04-23T12:00:00Z",
        ),
    )


@pytest.fixture()
def harvester_conn():
    """In-memory settlements schema parity with the harvester live write path."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def test_fresh_schema_supplies_harvester_live_columns(harvester_conn):
    columns = {
        row["name"] for row in harvester_conn.execute("PRAGMA table_info(settlements)")
    }
    assert {"pm_bin_lo", "pm_bin_hi", "unit", "settlement_source_type"} <= columns


def test_harvester_settlement_uses_canonical_high_identity(harvester_conn):
    """C6: the VERIFIED settlement row carries HIGH_LOCALDAY_MAX identity."""
    city = _make_city()
    # Force SettlementSemantics to accept the proxy observation — the semantics
    # layer rounds/asserts via assert_settlement_value, and the observation
    # 88.0°F rounds to 88 which sits inside [85, 89].
    obs_row = {"high_temp": 88.0, "source": "wu_icao_history_v1", "id": 99, "fetched_at": "2026-04-24T12:00:00Z"}

    harvester_mod._write_settlement_truth(
        harvester_conn, city, "2026-04-24",
        pm_bin_lo=85.0, pm_bin_hi=89.0,
        event_slug="test-market",
        obs_row=obs_row,
    )

    row = harvester_conn.execute(
        "SELECT temperature_metric, physical_quantity, observation_field, authority "
        "FROM settlements WHERE city = ? AND target_date = ?",
        (city.name, "2026-04-24"),
    ).fetchone()

    assert row is not None, "harvester must write a settlements row"
    assert row["authority"] == "VERIFIED", row["authority"]
    assert row["temperature_metric"] == HIGH_LOCALDAY_MAX.temperature_metric
    assert row["physical_quantity"] == HIGH_LOCALDAY_MAX.physical_quantity
    assert row["observation_field"] == HIGH_LOCALDAY_MAX.observation_field


def test_physical_quantity_is_not_legacy_string(harvester_conn):
    """C6 regression-bar: catch re-introduction of the legacy literal."""
    city = _make_city("regression_city")
    obs_row = {"high_temp": 70.0, "source": "wu_icao_history_v1", "id": 100, "fetched_at": "2026-04-24T12:00:00Z"}

    harvester_mod._write_settlement_truth(
        harvester_conn, city, "2026-04-24",
        pm_bin_lo=65.0, pm_bin_hi=75.0,
        event_slug="regression-market",
        obs_row=obs_row,
    )

    row = harvester_conn.execute(
        "SELECT physical_quantity FROM settlements WHERE city = ?",
        (city.name,),
    ).fetchone()

    assert row["physical_quantity"] != "daily_maximum_air_temperature", (
        "harvester regressed to pre-C6 hardcoded physical_quantity"
    )
    assert row["physical_quantity"] == "mx2t6_local_calendar_day_max"


def test_harvester_imports_high_localday_max():
    """Structural guard: HIGH_LOCALDAY_MAX must be imported in harvester."""
    text = (PROJECT_ROOT / "src/execution/harvester.py").read_text()
    assert "HIGH_LOCALDAY_MAX" in text
    assert "from src.types.metric_identity" in text


def test_harvester_settlement_mirrors_verified_to_settlements_v2(harvester_conn):
    """Phase 5C.4: harvester writes v2 settlement substrate in the same transaction."""
    city = _make_city("v2_verified")
    result = harvester_mod._write_settlement_truth(
        harvester_conn,
        city,
        "2026-04-24",
        pm_bin_lo=85.0,
        pm_bin_hi=89.0,
        event_slug="highest-temperature-in-v2-verified-on-april-24-2026",
        obs_row={
            "high_temp": 88.0,
            "source": "wu_icao_history_v1",
            "id": 101,
            "fetched_at": "2026-04-24T12:00:00Z",
        },
    )

    assert result["authority"] == "VERIFIED"
    assert result["settlement_v2"]["status"] == "written"
    row = harvester_conn.execute(
        """
        SELECT city, target_date, temperature_metric, market_slug, winning_bin,
               settlement_value, settlement_source, settled_at, authority,
               provenance_json
        FROM settlements_v2
        WHERE city = ? AND target_date = ? AND temperature_metric = 'high'
        """,
        (city.name, "2026-04-24"),
    ).fetchone()

    assert row is not None
    assert row["market_slug"] == "highest-temperature-in-v2-verified-on-april-24-2026"
    assert row["winning_bin"] == "85-89°F"
    assert row["settlement_value"] == 88.0
    assert row["settlement_source"] == city.settlement_source
    assert row["settled_at"]
    assert row["authority"] == "VERIFIED"
    provenance = json.loads(row["provenance_json"])
    assert provenance["writer"] == "harvester_live_dr33"
    assert provenance["legacy_table"] == "settlements"
    assert provenance["pm_bin_lo"] == 85.0
    assert provenance["pm_bin_hi"] == 89.0
    assert provenance["unit"] == "F"
    assert provenance["settlement_source_type"] == "WU"
    assert provenance["temperature_metric"] == HIGH_LOCALDAY_MAX.temperature_metric
    assert provenance["physical_quantity"] == HIGH_LOCALDAY_MAX.physical_quantity
    assert provenance["observation_field"] == HIGH_LOCALDAY_MAX.observation_field
    assert provenance["data_version"] == "wu_icao_history_v1"

    readiness = check_economics_readiness(harvester_conn)
    assert readiness.ready is False
    assert "empty_table:settlements_v2" not in readiness.blockers
    assert "economics_engine_not_implemented" in readiness.blockers


def test_resolved_gamma_child_identity_is_preserved_for_winning_bin():
    """Phase 5C.5: resolved child parsing keeps condition_id and YES token."""
    event = {
        "markets": [
            {
                "umaResolutionStatus": "resolved",
                "conditionId": "cond-loser",
                "clobTokenIds": '["yes-loser", "no-loser"]',
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0", "1"]',
                "question": "65-69°F",
            },
            {
                "umaResolutionStatus": "resolved",
                "conditionId": "cond-winner",
                "clobTokenIds": '["yes-winner", "no-winner"]',
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["1", "0"]',
                "question": "70-75°F",
            },
        ]
    }

    resolved = harvester_mod._extract_resolved_market_outcomes(event)
    winning = harvester_mod._find_winning_market_outcome(event)

    assert len(resolved) == 2
    assert winning is not None
    assert winning.condition_id == "cond-winner"
    assert winning.yes_token_id == "yes-winner"
    assert winning.range_low == 70.0
    assert winning.range_high == 75.0
    assert harvester_mod._find_winning_bin(event) == (70.0, 75.0)


def test_resolved_gamma_child_identity_handles_reversed_outcome_labels():
    """Token mapping follows Gamma outcome labels instead of positional guesswork."""
    event = {
        "markets": [
            {
                "umaResolutionStatus": "resolved",
                "conditionId": "cond-reversed",
                "clobTokenIds": '["no-token", "yes-token"]',
                "outcomes": '["No", "Yes"]',
                "outcomePrices": '["0", "1"]',
                "question": "70°F or higher",
            },
        ]
    }

    winning = harvester_mod._find_winning_market_outcome(event)

    assert winning is not None
    assert winning.condition_id == "cond-reversed"
    assert winning.yes_token_id == "yes-token"
    assert winning.yes_won is True
    assert winning.range_low == 70.0
    assert winning.range_high is None


def test_resolved_gamma_child_identity_requires_exactly_one_winner():
    """Multiple YES-resolved children are malformed, not two settled winners."""
    event = {
        "markets": [
            {
                "umaResolutionStatus": "resolved",
                "conditionId": "cond-winner-a",
                "clobTokenIds": '["yes-a", "no-a"]',
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["1", "0"]',
                "question": "65-69°F",
            },
            {
                "umaResolutionStatus": "resolved",
                "conditionId": "cond-winner-b",
                "clobTokenIds": '["yes-b", "no-b"]',
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["1", "0"]',
                "question": "70-75°F",
            },
        ]
    }

    assert harvester_mod._find_winning_market_outcome(event) is None
    assert harvester_mod._find_winning_bin(event) == (None, None)


def test_harvester_verified_settlement_updates_market_events_v2_by_identity(harvester_conn):
    """Phase 5C.5: VERIFIED settlement writes YES/NO outcomes by exact child id."""
    city = _make_city("v2_outcome_city")
    market_slug = "highest-temperature-in-v2-outcome-city-on-april-24-2026"
    _insert_market_event_v2(
        harvester_conn,
        market_slug=market_slug,
        city=city.name,
        condition_id="cond-loser",
        token_id="yes-loser",
        range_label="65-69°F",
        range_low=65.0,
        range_high=69.0,
    )
    _insert_market_event_v2(
        harvester_conn,
        market_slug=market_slug,
        city=city.name,
        condition_id="cond-winner",
        token_id="yes-winner",
        range_label="70-75°F",
        range_low=70.0,
        range_high=75.0,
    )

    result = harvester_mod._write_settlement_truth(
        harvester_conn,
        city,
        "2026-04-24",
        pm_bin_lo=70.0,
        pm_bin_hi=75.0,
        event_slug=market_slug,
        obs_row={
            "high_temp": 72.0,
            "source": "wu_icao_history_v1",
            "id": 401,
            "fetched_at": "2026-04-24T12:00:00Z",
        },
        resolved_market_outcomes=[
            harvester_mod.ResolvedMarketOutcome(
                condition_id="cond-loser",
                yes_token_id="yes-loser",
                range_label="65-69°F",
                range_low=65.0,
                range_high=69.0,
                yes_won=False,
            ),
            harvester_mod.ResolvedMarketOutcome(
                condition_id="cond-winner",
                yes_token_id="yes-winner",
                range_label="70-75°F",
                range_low=70.0,
                range_high=75.0,
                yes_won=True,
            ),
        ],
    )

    assert result["authority"] == "VERIFIED"
    assert result["market_events_v2"]["status"] == "written"
    assert result["market_events_v2"]["written"] == 2
    rows = {
        row["condition_id"]: row["outcome"]
        for row in harvester_conn.execute(
            """
            SELECT condition_id, outcome
            FROM market_events_v2
            WHERE market_slug = ?
            """,
            (market_slug,),
        )
    }
    assert rows == {"cond-loser": "NO", "cond-winner": "YES"}
    readiness = check_economics_readiness(harvester_conn)
    assert readiness.ready is False
    assert "no_market_event_outcomes" not in readiness.blockers
    assert "economics_engine_not_implemented" in readiness.blockers


def test_harvester_market_events_v2_update_requires_existing_child_identity(harvester_conn):
    """The outcome producer does not insert missing child markets by label."""
    city = _make_city("v2_outcome_missing")
    market_slug = "highest-temperature-in-v2-outcome-missing-on-april-24-2026"

    result = harvester_mod._write_settlement_truth(
        harvester_conn,
        city,
        "2026-04-24",
        pm_bin_lo=70.0,
        pm_bin_hi=75.0,
        event_slug=market_slug,
        obs_row={
            "high_temp": 72.0,
            "source": "wu_icao_history_v1",
            "id": 402,
            "fetched_at": "2026-04-24T12:00:00Z",
        },
        resolved_market_outcomes=[
            harvester_mod.ResolvedMarketOutcome(
                condition_id="cond-missing",
                yes_token_id="yes-missing",
                range_label="70-75°F",
                range_low=70.0,
                range_high=75.0,
                yes_won=True,
            ),
        ],
    )

    assert result["authority"] == "VERIFIED"
    assert result["market_events_v2"]["status"] == "skipped_no_updates"
    assert result["market_events_v2"]["skipped_missing_market_event"] == 1
    assert harvester_conn.execute(
        "SELECT COUNT(*) FROM market_events_v2 WHERE market_slug = ?",
        (market_slug,),
    ).fetchone()[0] == 0


def test_harvester_market_events_v2_batch_is_all_or_nothing(harvester_conn):
    """One bad child identity must not leave a partially resolved market family."""
    city = _make_city("v2_outcome_atomic")
    market_slug = "highest-temperature-in-v2-outcome-atomic-on-april-24-2026"
    _insert_market_event_v2(
        harvester_conn,
        market_slug=market_slug,
        city=city.name,
        condition_id="cond-present",
        token_id="yes-present",
    )

    result = harvester_mod._write_settlement_truth(
        harvester_conn,
        city,
        "2026-04-24",
        pm_bin_lo=65.0,
        pm_bin_hi=75.0,
        event_slug=market_slug,
        obs_row={
            "high_temp": 70.0,
            "source": "wu_icao_history_v1",
            "id": 405,
            "fetched_at": "2026-04-24T12:00:00Z",
        },
        resolved_market_outcomes=[
            harvester_mod.ResolvedMarketOutcome(
                condition_id="cond-present",
                yes_token_id="yes-present",
                range_label="65-75°F",
                range_low=65.0,
                range_high=75.0,
                yes_won=True,
            ),
            harvester_mod.ResolvedMarketOutcome(
                condition_id="cond-missing",
                yes_token_id="yes-missing",
                range_label="76-80°F",
                range_low=76.0,
                range_high=80.0,
                yes_won=False,
            ),
        ],
    )

    assert result["authority"] == "VERIFIED"
    assert result["market_events_v2"]["status"] == "skipped_no_updates"
    assert result["market_events_v2"]["written"] == 0
    assert result["market_events_v2"]["skipped_missing_market_event"] == 1
    outcome = harvester_conn.execute(
        """
        SELECT outcome
        FROM market_events_v2
        WHERE market_slug = ? AND condition_id = ?
        """,
        (market_slug, "cond-present"),
    ).fetchone()["outcome"]
    assert outcome is None


def test_log_market_event_outcome_v2_skips_missing_table_without_creating_schema():
    """Capability-absent path is explicit and has no DDL side effect."""
    conn = sqlite3.connect(":memory:")

    result = log_market_event_outcome_v2(
        conn,
        market_slug="market-slug",
        city="NoSchema",
        target_date="2026-04-24",
        temperature_metric="high",
        condition_id="cond-1",
        token_id="yes-1",
        outcome="YES",
    )

    assert result == {"status": "skipped_missing_table", "table": "market_events_v2"}
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0] == 0


def test_harvester_market_events_v2_update_refuses_token_mismatch(harvester_conn):
    """condition_id alone is insufficient; YES token identity must also match."""
    city = _make_city("v2_outcome_mismatch")
    market_slug = "highest-temperature-in-v2-outcome-mismatch-on-april-24-2026"
    _insert_market_event_v2(
        harvester_conn,
        market_slug=market_slug,
        city=city.name,
        condition_id="cond-winner",
        token_id="yes-original",
    )

    result = harvester_mod._write_settlement_truth(
        harvester_conn,
        city,
        "2026-04-24",
        pm_bin_lo=65.0,
        pm_bin_hi=75.0,
        event_slug=market_slug,
        obs_row={
            "high_temp": 70.0,
            "source": "wu_icao_history_v1",
            "id": 403,
            "fetched_at": "2026-04-24T12:00:00Z",
        },
        resolved_market_outcomes=[
            harvester_mod.ResolvedMarketOutcome(
                condition_id="cond-winner",
                yes_token_id="yes-different",
                range_label="65-75°F",
                range_low=65.0,
                range_high=75.0,
                yes_won=True,
            ),
        ],
    )

    assert result["authority"] == "VERIFIED"
    assert result["market_events_v2"]["status"] == "conflicted"
    assert result["market_events_v2"]["refused_identity_mismatch"] == 1
    outcome = harvester_conn.execute(
        """
        SELECT outcome
        FROM market_events_v2
        WHERE market_slug = ? AND condition_id = ?
        """,
        (market_slug, "cond-winner"),
    ).fetchone()["outcome"]
    assert outcome is None


def test_harvester_quarantined_settlement_does_not_write_market_events_v2_outcome(harvester_conn):
    """market_events_v2 has no authority column, so quarantined settlement cannot resolve it."""
    city = _make_city("v2_outcome_quarantine")
    market_slug = "highest-temperature-in-v2-outcome-quarantine-on-april-24-2026"
    _insert_market_event_v2(
        harvester_conn,
        market_slug=market_slug,
        city=city.name,
        condition_id="cond-winner",
        token_id="yes-winner",
    )

    result = harvester_mod._write_settlement_truth(
        harvester_conn,
        city,
        "2026-04-24",
        pm_bin_lo=65.0,
        pm_bin_hi=75.0,
        event_slug=market_slug,
        obs_row={
            "high_temp": 82.0,
            "source": "wu_icao_history_v1",
            "id": 404,
            "fetched_at": "2026-04-24T12:00:00Z",
        },
        resolved_market_outcomes=[
            harvester_mod.ResolvedMarketOutcome(
                condition_id="cond-winner",
                yes_token_id="yes-winner",
                range_label="65-75°F",
                range_low=65.0,
                range_high=75.0,
                yes_won=True,
            ),
        ],
    )

    assert result["authority"] == "QUARANTINED"
    assert result["market_events_v2"]["status"] == "skipped_unverified_settlement"
    outcome = harvester_conn.execute(
        """
        SELECT outcome
        FROM market_events_v2
        WHERE market_slug = ? AND condition_id = ?
        """,
        (market_slug, "cond-winner"),
    ).fetchone()["outcome"]
    assert outcome is None


def test_harvester_settlement_without_market_slug_skips_settlements_v2(harvester_conn):
    """v2 settlement rows require market identity; legacy compatibility may still write."""
    city = _make_city("v2_missing_slug")

    result = harvester_mod._write_settlement_truth(
        harvester_conn,
        city,
        "2026-04-24",
        pm_bin_lo=65.0,
        pm_bin_hi=75.0,
        event_slug="",
        obs_row={
            "high_temp": 70.0,
            "source": "wu_icao_history_v1",
            "id": 102,
            "fetched_at": "2026-04-24T12:00:00Z",
        },
    )

    assert result["authority"] == "VERIFIED"
    assert result["settlement_v2"]["status"] == "refused_missing_identity"
    assert result["settlement_v2"]["missing_fields"] == ("market_slug",)
    legacy_count = harvester_conn.execute(
        "SELECT COUNT(*) FROM settlements WHERE city = ? AND target_date = ?",
        (city.name, "2026-04-24"),
    ).fetchone()[0]
    v2_count = harvester_conn.execute(
        "SELECT COUNT(*) FROM settlements_v2 WHERE city = ? AND target_date = ?",
        (city.name, "2026-04-24"),
    ).fetchone()[0]
    assert legacy_count == 1
    assert v2_count == 0


def test_harvester_settlement_mirrors_quarantine_to_settlements_v2(harvester_conn):
    """Quarantined legacy settlements do not get promoted to VERIFIED in v2."""
    city = _make_city("v2_quarantined")

    result = harvester_mod._write_settlement_truth(
        harvester_conn,
        city,
        "2026-04-24",
        pm_bin_lo=65.0,
        pm_bin_hi=75.0,
        event_slug="highest-temperature-in-v2-quarantined-on-april-24-2026",
        obs_row={
            "high_temp": 80.0,
            "source": "wu_icao_history_v1",
            "id": 103,
            "fetched_at": "2026-04-24T12:00:00Z",
        },
    )

    assert result["authority"] == "QUARANTINED"
    row = harvester_conn.execute(
        """
        SELECT authority, settlement_value, winning_bin, provenance_json
        FROM settlements_v2
        WHERE city = ? AND target_date = ?
        """,
        (city.name, "2026-04-24"),
    ).fetchone()

    assert row is not None
    assert row["authority"] == "QUARANTINED"
    assert row["settlement_value"] == 80.0
    assert row["winning_bin"] is None
    provenance = json.loads(row["provenance_json"])
    assert provenance["quarantine_reason"] == "harvester_live_obs_outside_bin"


def test_harvester_settlements_v2_mirror_is_idempotent(harvester_conn):
    """v2 uses ON CONFLICT update, not INSERT OR REPLACE duplicate rows."""
    city = _make_city("v2_idempotent")
    kwargs = {
        "target_date": "2026-04-24",
        "pm_bin_lo": 65.0,
        "pm_bin_hi": 75.0,
        "event_slug": "highest-temperature-in-v2-idempotent-on-april-24-2026",
    }
    harvester_mod._write_settlement_truth(
        harvester_conn,
        city,
        obs_row={
            "high_temp": 70.0,
            "source": "wu_icao_history_v1",
            "id": 201,
            "fetched_at": "2026-04-24T12:00:00Z",
        },
        **kwargs,
    )
    harvester_mod._write_settlement_truth(
        harvester_conn,
        city,
        obs_row={
            "high_temp": 70.0,
            "source": "wu_icao_history_v1",
            "id": 202,
            "fetched_at": "2026-04-24T18:00:00Z",
        },
        **kwargs,
    )

    count = harvester_conn.execute(
        "SELECT COUNT(*) FROM settlements_v2 WHERE city = ? AND target_date = ?",
        (city.name, "2026-04-24"),
    ).fetchone()[0]
    row = harvester_conn.execute(
        "SELECT provenance_json FROM settlements_v2 WHERE city = ? AND target_date = ?",
        (city.name, "2026-04-24"),
    ).fetchone()
    provenance = json.loads(row["provenance_json"])
    assert count == 1
    assert provenance["obs_id"] == 202
    assert provenance["decision_time_snapshot_id"] == "2026-04-24T18:00:00Z"


def test_log_settlement_v2_skips_missing_table_without_creating_schema():
    """Capability-absent path is explicit and has no DDL side effect."""
    conn = sqlite3.connect(":memory:")

    result = log_settlement_v2(
        conn,
        city="NoSchema",
        target_date="2026-04-24",
        temperature_metric="high",
        market_slug="market-slug",
        winning_bin="70°F",
        settlement_value=70.0,
        settlement_source="KORD",
        settled_at="2026-04-24T23:00:00Z",
        authority="VERIFIED",
        provenance={"writer": "test"},
        recorded_at="2026-04-24T23:00:00Z",
    )

    assert result == {"status": "skipped_missing_table", "table": "settlements_v2"}
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0] == 0


def test_harvester_settlement_v2_missing_unique_key_does_not_abort_legacy_write(harvester_conn):
    """Malformed v2 schema cannot block the legacy settlement truth write."""
    city = _make_city("v2_bad_unique")
    harvester_conn.execute("DROP TABLE settlements_v2")
    harvester_conn.execute(
        """
        CREATE TABLE settlements_v2 (
            settlement_id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            market_slug TEXT,
            winning_bin TEXT,
            settlement_value REAL,
            settlement_source TEXT,
            settled_at TEXT,
            authority TEXT,
            provenance_json TEXT,
            recorded_at TEXT
        )
        """
    )

    result = harvester_mod._write_settlement_truth(
        harvester_conn,
        city,
        "2026-04-24",
        pm_bin_lo=65.0,
        pm_bin_hi=75.0,
        event_slug="highest-temperature-in-v2-bad-unique-on-april-24-2026",
        obs_row={
            "high_temp": 70.0,
            "source": "wu_icao_history_v1",
            "id": 301,
            "fetched_at": "2026-04-24T12:00:00Z",
        },
    )

    assert result["authority"] == "VERIFIED"
    assert result["settlement_v2"]["status"] == "skipped_invalid_schema"
    assert result["settlement_v2"]["missing_unique_key"] == (
        "city",
        "target_date",
        "temperature_metric",
    )
    legacy_count = harvester_conn.execute(
        "SELECT COUNT(*) FROM settlements WHERE city = ? AND target_date = ?",
        (city.name, "2026-04-24"),
    ).fetchone()[0]
    v2_count = harvester_conn.execute(
        "SELECT COUNT(*) FROM settlements_v2 WHERE city = ? AND target_date = ?",
        (city.name, "2026-04-24"),
    ).fetchone()[0]
    assert legacy_count == 1
    assert v2_count == 0


def test_missing_forecast_issue_time_does_not_create_training_pairs(harvester_conn):
    """F08: runtime-only snapshots without issue time must not enter training."""
    apply_v2_schema(harvester_conn)
    city = _make_city("openmeteo_audit_only")

    count = harvester_mod.harvest_settlement(
        harvester_conn,
        city,
        target_date="2026-04-24",
        winning_bin_label="86-88°F",
        bin_labels=["85°F or below", "86-88°F", "89°F or higher"],
        p_raw_vector=[0.2, 0.5, 0.3],
        lead_days=1.0,
        forecast_issue_time=None,
        forecast_available_at="2026-04-23T00:00:00Z",
        source_model_version=HIGH_LOCALDAY_MAX.data_version,
        settlement_value=87.0,
        temperature_metric="high",
    )

    assert count == 0
    pair_count = harvester_conn.execute(
        "SELECT COUNT(*) FROM calibration_pairs_v2 WHERE city = ?",
        (city.name,),
    ).fetchone()[0]
    assert pair_count == 0


def test_snapshot_context_missing_issue_time_is_audit_only(harvester_conn):
    """F08: a persisted Open-Meteo snapshot id is auditable but not trainable."""
    cur = harvester_conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            city, target_date, issue_time, valid_time, available_at, fetch_time,
            lead_hours, members_json, p_raw_json, spread, is_bimodal,
            model_version, data_version, authority, temperature_metric
        )
        VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "NYC",
            "2026-04-24",
            "2026-04-23T01:00:00Z",
            "2026-04-23T00:00:00Z",
            "2026-04-23T00:05:00Z",
            24.0,
            "[70,71,72]",
            "[0.2,0.5,0.3]",
            2.0,
            0,
            "openmeteo_ecmwf_ifs025",
            "live_v1",
            "VERIFIED",
            "high",
        ),
    )
    snapshot_id = str(cur.lastrowid)

    context = harvester_mod.get_snapshot_context(harvester_conn, snapshot_id)

    assert context is not None
    assert context["issue_time"] is None
    assert context["snapshot_learning_ready"] is False
    assert context["learning_blocked_reason"] == "missing_forecast_issue_time"

    contexts, dropped_rows = harvester_mod._snapshot_contexts_from_rows(
        harvester_conn,
        harvester_conn,
        [{
            "decision_snapshot_id": snapshot_id,
            "source": "position_events",
            "authority_level": "durable_event",
            "learning_snapshot_ready": True,
        }],
    )

    assert dropped_rows == []
    assert len(contexts) == 1
    assert contexts[0]["learning_snapshot_ready"] is False
    assert contexts[0]["is_degraded"] is True
    assert contexts[0]["degraded_reason"] == "missing_forecast_issue_time"
