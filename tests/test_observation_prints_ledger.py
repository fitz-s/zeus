# Created: 2026-07-16
# Last reused/audited: 2026-07-16
# Authority basis: day0 defects 1-5 (Paris 2026-07-14 monotonicity regression,
#   WU-backfill-frozen hour buckets, climatology-band self-blinding, HKO
#   accumulator never folding its own spot read, Seoul binary exclusion where
#   margin-absorption already existed) — operator directive: observations are
#   a publication stream; observation_prints is the append-only ledger of
#   that stream. See src/state/schema/observation_prints_schema.py.
"""Tests for the observation_prints append-only ledger: table shape, the
third fact observation_prints adds to _latest_authorized_day0_fact's
absorbing-direction reduction, and the append-only (never-mutate) law that
makes the Paris 2026-07-14 type specimen solvable by append alone.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.data.replacement_forecast_current_target_plan import (
    _latest_authorized_day0_fact,
)
from src.state.schema.observation_prints_schema import append_print, ensure_table

UTC = timezone.utc


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    return conn


# ---------------------------------------------------------------------------
# (i) append + dedup + append-only
# ---------------------------------------------------------------------------


class TestAppendOnly:
    def test_append_dedup_is_free_noop(self):
        conn = _conn()
        first = append_print(
            conn, city="Paris", station_id="LFPB", source_channel="aviationweather_metar",
            publish_ts_utc="2026-07-14T14:00:00+00:00", value_native=34.0, unit="C",
            fetched_at_utc="2026-07-14T14:04:00+00:00", raw_report="METAR LFPB 141400Z 34/14",
        )
        second = append_print(
            conn, city="Paris", station_id="LFPB", source_channel="aviationweather_metar",
            publish_ts_utc="2026-07-14T14:00:00+00:00", value_native=34.0, unit="C",
            fetched_at_utc="2026-07-14T15:00:00+00:00", raw_report="METAR LFPB 141400Z 34/14",
        )
        assert first is True
        assert second is False  # already present -> free no-op, not an error
        (count,) = conn.execute("SELECT COUNT(*) FROM observation_prints").fetchone()
        assert count == 1

    def test_different_value_for_same_publish_ts_is_a_new_row_never_a_mutation(self):
        """A corrected/republished reading at the SAME nominal publish_ts_utc
        is a DIFFERENT row (uniqueness key includes value_native) — the
        ledger keeps BOTH; it never UPDATEs the old one."""
        conn = _conn()
        append_print(
            conn, city="Paris", station_id="LFPB", source_channel="wu_icao_history",
            publish_ts_utc="2026-07-14T14:00:00+00:00", value_native=34.0, unit="C",
            fetched_at_utc="2026-07-14T15:05:00+00:00",
        )
        append_print(
            conn, city="Paris", station_id="LFPB", source_channel="wu_icao_history",
            publish_ts_utc="2026-07-14T14:00:00+00:00", value_native=35.0, unit="C",
            fetched_at_utc="2026-07-16T02:00:00+00:00",
        )
        rows = conn.execute(
            "SELECT value_native FROM observation_prints ORDER BY value_native"
        ).fetchall()
        assert [r[0] for r in rows] == [34.0, 35.0]
        (count,) = conn.execute("SELECT COUNT(*) FROM observation_prints").fetchone()
        assert count == 2  # both rows present -- an append, never a 1-row overwrite

    def test_update_is_structurally_forbidden(self):
        conn = _conn()
        append_print(
            conn, city="Paris", station_id="LFPB", source_channel="wu_icao_history",
            publish_ts_utc="2026-07-14T14:00:00+00:00", value_native=34.0, unit="C",
            fetched_at_utc="2026-07-14T15:05:00+00:00",
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE observation_prints SET value_native = 99.0 WHERE city = 'Paris'")

    def test_delete_is_structurally_forbidden(self):
        conn = _conn()
        append_print(
            conn, city="Paris", station_id="LFPB", source_channel="wu_icao_history",
            publish_ts_utc="2026-07-14T14:00:00+00:00", value_native=34.0, unit="C",
            fetched_at_utc="2026-07-14T15:05:00+00:00",
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM observation_prints WHERE city = 'Paris'")


# ---------------------------------------------------------------------------
# (ii) Paris 2026-07-14 type specimen, replayed through the ledger
# ---------------------------------------------------------------------------


def _paris_temps_by_hhmm() -> list[tuple[str, float]]:
    # Real LFPB METAR temps, 2026-07-14 (half-hourly), including the 14:30Z
    # 35.0C reading that both observation_instants (frozen at 34.0, defect-2)
    # and the fast-lane opportunity_event (31.0, defect-1) missed.
    return [
        ("12:00", 32.0), ("12:30", 32.0), ("13:00", 33.0), ("13:30", 34.0),
        ("14:00", 34.0), ("14:30", 35.0), ("15:00", 34.0), ("15:30", 34.0),
        ("16:00", 34.0), ("16:30", 34.0), ("17:00", 34.0), ("17:30", 33.0),
        ("18:00", 33.0), ("18:30", 32.0), ("19:00", 32.0), ("19:30", 31.0),
    ]


class TestParisTypeSpecimenThroughLedger:
    def test_physical_ledger_clock_is_causal_and_uses_latest_equal_plateau(self):
        conn = _conn()
        for published, fetched in (
            ("2026-07-14T14:00:00+00:00", "2026-07-14T14:04:00+00:00"),
            ("2026-07-14T14:30:00+00:00", "2026-07-14T14:34:00+00:00"),
            # Published before the snapshot but not fetched until afterwards:
            # replay and live must both exclude it from the decision information set.
            ("2026-07-14T15:00:00+00:00", "2026-07-14T16:00:00+00:00"),
        ):
            append_print(
                conn,
                city="Paris",
                station_id="LFPB",
                source_channel="aviationweather_metar",
                publish_ts_utc=published,
                value_native=34.0,
                unit="C",
                fetched_at_utc=fetched,
                raw_report="METAR LFPB 141430Z 34/14",
            )

        fact = _latest_authorized_day0_fact(
            conn,
            city="Paris",
            target_date="2026-07-14",
            temperature_metric="high",
            decision_time=datetime(2026, 7, 14, 15, 30, tzinfo=UTC),
        )

        assert fact is not None
        assert fact["observation_time"] == "2026-07-14T14:30:00+00:00"
        assert fact["observation_available_at"] == "2026-07-14T14:34:00+00:00"

    def test_fahrenheit_fast_fact_uses_precise_t_group_value(self):
        conn = _conn()
        append_print(
            conn,
            city="NYC",
            station_id="KLGA",
            source_channel="aviationweather_metar",
            publish_ts_utc="2026-07-10T19:30:00+00:00",
            value_native=26.0,
            unit="C",
            fetched_at_utc="2026-07-10T19:34:00+00:00",
            raw_report=(
                "METAR KLGA 101930Z 18008KT 10SM CLR 26/16 A2998 T02560161"
            ),
        )

        fact = _latest_authorized_day0_fact(
            conn,
            city="NYC",
            target_date="2026-07-10",
            temperature_metric="high",
            decision_time=datetime(2026, 7, 10, 19, 45, tzinfo=UTC),
        )

        assert fact is not None
        assert fact["observed_extreme_native"] == pytest.approx(78.08)

    def test_ledger_fact_reaches_35_even_when_instants_says_34_and_events_says_31(self):
        conn = _conn()
        # observation_instants: frozen at 34.0 (defect-2 scenario) -- present
        # but NOT touched by the ledger fix.
        conn.execute(
            """
            CREATE TABLE observation_instants (
                city TEXT, target_date TEXT, source TEXT, station_id TEXT,
                temp_unit TEXT, imported_at TEXT, local_timestamp TEXT, utc_timestamp TEXT,
                running_max REAL, running_min REAL, authority TEXT,
                training_allowed INTEGER, causality_status TEXT, source_role TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "Paris", "2026-07-14", "wu_icao_history", "LFPB", "C",
                "2026-07-14T14:15:00+00:00", "2026-07-14T16:00:00+02:00",
                "2026-07-14T14:00:00+00:00", 34.0, 34.0,
                "VERIFIED", 1, "OK", "historical_hourly",
            ),
        )
        # opportunity_events: fresher-but-lower fast-lane event (defect-1
        # scenario) -- present but NOT the winner once the ledger fact exists.
        conn.execute(
            """
            CREATE TABLE opportunity_events (
                event_id TEXT, event_type TEXT, available_at TEXT,
                received_at TEXT, created_at TEXT, payload_json TEXT
            )
            """
        )
        event_payload = {
            "city": "Paris", "target_date": "2026-07-14", "metric": "high",
            "settlement_source": "aviationweather_metar", "station_id": "LFPB",
            "observation_time": "2026-07-14T19:30:00+00:00",
            "observation_available_at": "2026-07-14T19:32:20+00:00",
            "raw_value": 31.0, "rounded_value": 31, "high_so_far": 31.0,
            "source_match_status": "MATCH", "local_date_status": "MATCH",
            "station_match_status": "MATCH", "dst_status": "UNAMBIGUOUS",
            "metric_match_status": "MATCH", "rounding_status": "MATCH",
            "source_authorized_status": "AUTHORIZED", "live_authority_status": "live",
            "settlement_unit": "C",
        }
        conn.execute(
            "INSERT INTO opportunity_events VALUES (?, ?, ?, ?, ?, ?)",
            (
                "day0-paris-metar", "DAY0_EXTREME_UPDATED",
                "2026-07-14T19:32:20+00:00", "2026-07-14T19:32:20+00:00",
                "2026-07-14T19:32:20+00:00", json.dumps(event_payload),
            ),
        )
        # observation_prints: the FULL real METAR sequence, incl. 35.0@14:30Z.
        for hhmm, temp in _paris_temps_by_hhmm():
            published = datetime.fromisoformat(
                f"2026-07-14T{hhmm}:00+00:00"
            )
            append_print(
                conn, city="Paris", station_id="LFPB", source_channel="aviationweather_metar",
                publish_ts_utc=published.isoformat(), value_native=temp, unit="C",
                fetched_at_utc=(published + timedelta(minutes=4)).isoformat(),
                raw_report=f"METAR LFPB {temp}/15",
            )

        fact = _latest_authorized_day0_fact(
            conn, city="Paris", target_date="2026-07-14", temperature_metric="high",
            decision_time=datetime(2026, 7, 14, 19, 52, tzinfo=UTC),
        )
        assert fact is not None
        assert fact["observed_extreme_native"] == 35.0
        assert fact["source"] == "observation_prints:aviationweather_metar"

    def test_wu_backfill_as_append_no_mutation_needed(self):
        """The type specimen solved by append alone, no widening machinery:
        first-seen wu_icao_history print (14:00Z=34.0), then WU backfills the
        14:30Z=35.0 reading as a SECOND print (zero mutation of the first
        row) -- the ledger fact must reach 35.0."""
        conn = _conn()
        append_print(
            conn, city="Paris", station_id="LFPB", source_channel="wu_icao_history",
            publish_ts_utc="2026-07-14T14:00:00+00:00", value_native=34.0, unit="C",
            fetched_at_utc="2026-07-14T15:05:06+00:00",
        )
        (count_before,) = conn.execute("SELECT COUNT(*) FROM observation_prints").fetchone()

        append_print(
            conn, city="Paris", station_id="LFPB", source_channel="wu_icao_history",
            publish_ts_utc="2026-07-14T14:30:00+00:00", value_native=35.0, unit="C",
            fetched_at_utc="2026-07-16T02:00:01+00:00",
        )
        (count_after,) = conn.execute("SELECT COUNT(*) FROM observation_prints").fetchone()
        assert count_after == count_before + 1  # a new row, not a rewrite

        fact = _latest_authorized_day0_fact(
            conn, city="Paris", target_date="2026-07-14", temperature_metric="high",
            decision_time=datetime(2026, 7, 16, 3, 0, tzinfo=UTC),
            require_settlement_channel=True,
        )
        assert fact is not None
        assert fact["observed_extreme_native"] == 35.0
        assert fact["source"] == "observation_prints:wu_icao_history"


# ---------------------------------------------------------------------------
# (iii) Seoul margin absorption through the ledger
# ---------------------------------------------------------------------------


class TestSeoulMarginThroughLedger:
    def test_rksi_print_30_enters_ledger_fact_at_28(self):
        conn = _conn()
        append_print(
            conn, city="Seoul", station_id="RKSI", source_channel="aviationweather_metar",
            publish_ts_utc="2026-06-10T05:00:00+00:00", value_native=30.0, unit="C",
            fetched_at_utc="2026-06-10T05:04:00+00:00", raw_report="METAR RKSI 30/17",
        )
        fact = _latest_authorized_day0_fact(
            conn, city="Seoul", target_date="2026-06-10", temperature_metric="high",
            decision_time=datetime(2026, 6, 10, 6, 0, tzinfo=UTC),
        )
        assert fact is not None
        assert fact["observed_extreme_native"] == 28.0  # 30.0 - 2.0 measured margin
        assert fact["source"] == "observation_prints:aviationweather_metar"

    def test_low_metric_mirror_margin_direction_flips(self):
        conn = _conn()
        # 2026-06-09T20:00Z = 2026-06-10T05:00 KST (Seoul is UTC+9, no DST) --
        # inside the 2026-06-10 Seoul local day.
        append_print(
            conn, city="Seoul", station_id="RKSI", source_channel="aviationweather_metar",
            publish_ts_utc="2026-06-09T20:00:00+00:00", value_native=10.0, unit="C",
            fetched_at_utc="2026-06-09T20:04:00+00:00", raw_report="METAR RKSI 10/05",
        )
        fact = _latest_authorized_day0_fact(
            conn, city="Seoul", target_date="2026-06-10", temperature_metric="low",
            decision_time=datetime(2026, 6, 9, 21, 0, tzinfo=UTC),
        )
        assert fact is not None
        assert fact["observed_extreme_native"] == 12.0  # 10.0 + 2.0 measured margin


# ---------------------------------------------------------------------------
# (iv) HKO rhrread spot print appended and folded into the reduction
# ---------------------------------------------------------------------------


class TestHkoSpotPrintWriterAndFold:
    def test_rhrread_spot_reading_is_appended_with_hko_publish_clock(self):
        from src.data.daily_obs_append import _append_hko_rhrread_print_to_ledger

        conn = _conn()
        data = {
            "updateTime": "2026-07-15T02:20:00+08:00",
            "temperature": {"data": [{"place": "Hong Kong Observatory", "value": 29.0}]},
        }
        _append_hko_rhrread_print_to_ledger(
            conn, data=data, temp_c=29.0,
            now_utc=datetime(2026, 7, 15, 2, 25, tzinfo=UTC),
        )
        row = conn.execute(
            "SELECT city, station_id, source_channel, publish_ts_utc, value_native, unit "
            "FROM observation_prints"
        ).fetchone()
        assert row is not None
        assert row["city"] == "Hong Kong"
        assert row["station_id"] == "HKO"
        assert row["source_channel"] == "hko_rhrread_spot"
        # HKO's own publish clock (updateTime), NOT our fetch wall-clock (now_utc).
        assert row["publish_ts_utc"] == "2026-07-14T18:20:00+00:00"
        assert row["value_native"] == 29.0
        assert row["unit"] == "C"

    def test_rhrread_spot_reading_falls_back_to_fetch_clock_without_update_time(self):
        from src.data.daily_obs_append import _append_hko_rhrread_print_to_ledger

        conn = _conn()
        now_utc = datetime(2026, 7, 15, 2, 25, tzinfo=UTC)
        _append_hko_rhrread_print_to_ledger(
            conn, data={"temperature": {"data": []}}, temp_c=29.0, now_utc=now_utc,
        )
        (publish_ts,) = conn.execute(
            "SELECT publish_ts_utc FROM observation_prints"
        ).fetchone()
        assert publish_ts == now_utc.isoformat()

    def test_hko_type_specimen_spot_reading_enters_day0_fact_at_29(self):
        """The Hong Kong 2026-07-15 defect-4 type specimen, this time through
        the ledger: a spot print of 29.0C must be visible to the day0 fact
        reduction (HKO has no settlement-grade ledger channel yet -- only the
        physical rhrread_spot lane, matching require_settlement_channel=False,
        the same physical/settlement split every other city uses)."""
        conn = _conn()
        append_print(
            conn, city="Hong Kong", station_id="HKO", source_channel="hko_rhrread_spot",
            publish_ts_utc="2026-07-14T18:20:00+00:00", value_native=29.0, unit="C",
            fetched_at_utc="2026-07-15T02:25:00+00:00",
        )
        fact = _latest_authorized_day0_fact(
            conn, city="Hong Kong", target_date="2026-07-15", temperature_metric="high",
            decision_time=datetime(2026, 7, 14, 19, 0, tzinfo=UTC),
        )
        assert fact is not None
        assert fact["observed_extreme_native"] == 29.0
        assert fact["source"] == "observation_prints:hko_rhrread_spot"


# ---------------------------------------------------------------------------
# (vi) fail-soft: a ledger write exception must never break the caller
# ---------------------------------------------------------------------------


class TestLedgerWriteFailSoft:
    def test_metar_ledger_append_failure_does_not_raise(self):
        from src.data.day0_fast_obs import (
            FAST_OBS_SOURCE_ID,
            MetarReport,
            _append_metar_prints_to_ledger,
        )

        class ExplodingConn:
            def execute(self, *_a, **_k):
                raise sqlite3.OperationalError("simulated ledger write failure")

        report = MetarReport(
            station_id="LFPB",
            obs_time=datetime(2026, 7, 14, 14, 0, tzinfo=UTC),
            receipt_time=datetime(2026, 7, 14, 14, 4, tzinfo=UTC),
            temp_c=34.0, metar_type="METAR", raw="METAR LFPB 34/14",
        )
        source = type(
            "FakeSource", (), {"station_id": "LFPB", "source_id": FAST_OBS_SOURCE_ID}
        )()
        city = type("FakeCity", (), {"name": "Paris"})()
        eligible = ((city, source, "2026-07-14"),)
        # Must not raise -- fail-soft is the whole point.
        _append_metar_prints_to_ledger(ExplodingConn(), eligible, [report])

    def test_hko_ledger_append_failure_does_not_raise(self):
        from src.data.daily_obs_append import _append_hko_rhrread_print_to_ledger

        class ExplodingConn:
            def execute(self, *_a, **_k):
                raise sqlite3.OperationalError("simulated ledger write failure")

        _append_hko_rhrread_print_to_ledger(
            ExplodingConn(),
            data={"temperature": {"data": []}},
            temp_c=29.0,
            now_utc=datetime(2026, 7, 15, 2, 25, tzinfo=UTC),
        )  # must not raise

    def test_wu_ledger_append_failure_does_not_raise(self):
        import scripts.obs_live_tick as obs_tick

        # A real sqlite3 connection missing the observation_prints table
        # entirely -- append_print raises OperationalError inside the
        # ledger helper, which must swallow it without touching the caller.
        conn = sqlite3.connect(":memory:")
        obs_tick._append_wu_prints_to_ledger(
            conn,
            [{
                "city": "Paris", "station_id": "LFPB", "source_channel": "wu_icao_history",
                "publish_ts_utc": "2026-07-14T14:00:00+00:00", "value_native": 34.0,
                "unit": "C", "fetched_at_utc": "2026-07-14T15:05:00+00:00", "raw_report": None,
            }],
        )  # must not raise despite the missing table
