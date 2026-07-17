# Created: 2026-06-10
# Last reused/audited: 2026-07-17
# Authority basis: operator green-light 2026-06-10 items A/C/E (free METAR fast
#   lane, live-obs hook wiring, WU-vs-METAR oracle anomaly guard); day0
#   first-principles review /tmp/day0_first_principles_review.md §6.2;
#   API shape verified live 2026-06-10 against aviationweather.gov
#   /api/data/metar?format=json (KLGA T-group tenths, RKSI whole-C, receiptTime
#   3-6 min behind obsTime);
#   operator patch pr404_live_final_patch.diff 2026-06-10 (fast-lane duplicate
#   memo fix + inconclusive METAR window retry fix).
"""Relationship tests for the day0 fast METAR lane + oracle anomaly guard.

Contracts:
  R5. UNIT LAW: F-settled cities consume only T-group (tenths-C) reports;
      whole-C reports are skipped (understating the running extreme is
      monotone-safe; a 1F conversion error could falsely kill an alive bin).
      C-settled cities consume whole-C verbatim.
  R6. MONOTONE EMISSION: a (city,date,metric) emits only on first sight or
      when the rounded extreme moves in the absorbing direction; emitted
      events pass the reactor hard-fact gate; provenance carries the feed
      receiptTime as observation_available_at (the honest publication clock).
  R7. ORACLE ANOMALY: WU and METAR running extremes are compared over the
      SAME window (METAR truncated at WU's last obs time); divergence beyond
      conversion noise pauses the family's day0 q construction fail-closed;
      latency (METAR extreme moving after WU's last report) is NOT divergence.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.data.day0_fast_obs import (
    Day0FastObsEmitter,
    FastObsSource,
    MetarReport,
    fast_obs_source_for_city,
    fast_obs_to_day0_observation,
    parse_metar_api_payload,
    running_extremes_for_local_day,
    settlement_temp_for_report,
)
from src.data.day0_oracle_anomaly import (
    _reset_registry_for_tests,
    check_wu_metar_divergence,
    clear_day0_oracle_anomaly,
    flag_day0_oracle_anomaly,
    is_day0_family_paused,
)

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _clean_anomaly_registry():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _nyc():
    return SimpleNamespace(
        name="NYC", timezone="America/New_York", settlement_unit="F",
        wu_station="KLGA", settlement_source_type="wu_icao",
    )


def _seoul():
    return SimpleNamespace(
        name="Seoul", timezone="Asia/Seoul", settlement_unit="C",
        wu_station="RKSI", settlement_source_type="wu_icao",
    )


def _tokyo():
    # Tokyo: settlement-FAITHFUL C city (measured, margin 0). Seoul is
    # margin-absorbed rather than excluded as of 2026-07-16 (day0 defect-5,
    # see TestMetarMarginAbsorption) but most emitter tests still use Tokyo
    # for a margin-free baseline. JST is UTC+9 like KST — the same UTC
    # fixtures map to the same local day.
    return SimpleNamespace(
        name="Tokyo", timezone="Asia/Tokyo", settlement_unit="C",
        wu_station="RJTT", settlement_source_type="wu_icao",
    )


def _london():
    return SimpleNamespace(
        name="London", timezone="Europe/London", settlement_unit="C",
        wu_station="EGLC", settlement_source_type="wu_icao",
    )


def _report(station, obs_time, temp_c, *, t_group=True, receipt_offset_min=4.0):
    raw = f"METAR {station} 101200Z 16008KT 10SM 21/15 A3004"
    if t_group:
        raw += " RMK AO2 T02110150"
    return MetarReport(
        station_id=station,
        obs_time=obs_time,
        receipt_time=obs_time + timedelta(minutes=receipt_offset_min),
        temp_c=temp_c,
        metar_type="METAR",
        raw=raw,
    )


# ===========================================================================
# Parsing (real API shape, verified live 2026-06-10)
# ===========================================================================

class TestParsePayload:
    SAMPLE = [
        {
            "icaoId": "KLGA", "receiptTime": "2026-06-10T00:54:16.580Z",
            "obsTime": 1781052660, "reportTime": "2026-06-10T01:00:00.000Z",
            "temp": 21.1, "metarType": "METAR",
            "rawOb": "METAR KLGA 100051Z 16008KT 10SM FEW250 21/15 A3004 RMK AO2 SLP170 T02110150",
        },
        {
            "icaoId": "RKSI", "receiptTime": "2026-06-10T01:04:35.841Z",
            "obsTime": 1781053200, "reportTime": "2026-06-10T01:00:00.000Z",
            "temp": 21, "metarType": "METAR",
            "rawOb": "METAR RKSI 100100Z 23004KT 160V310 8000 BKN015 21/17 Q1009 NOSIG",
        },
        {"icaoId": "", "obsTime": 1781053200},      # malformed: no station
        {"icaoId": "KXXX"},                            # malformed: no obsTime
        "not-a-dict",
    ]

    def test_parses_valid_rows_and_skips_junk(self):
        reports = parse_metar_api_payload(self.SAMPLE)
        assert [r.station_id for r in reports] == ["KLGA", "RKSI"]
        klga = reports[0]
        assert klga.temp_c == pytest.approx(21.1)
        assert klga.has_t_group is True
        assert klga.obs_time == datetime.fromtimestamp(1781052660, tz=UTC)
        # receiptTime is the publication clock (provenance for available_at)
        assert klga.receipt_time is not None and klga.receipt_time > klga.obs_time
        rksi = reports[1]
        assert rksi.has_t_group is False

    def test_non_list_payload_returns_empty(self):
        assert parse_metar_api_payload({"error": "nope"}) == []
        assert parse_metar_api_payload(None) == []


# ===========================================================================
# R5 — unit law
# ===========================================================================

class TestUnitLaw:
    def test_f_city_with_t_group_converts_exactly(self):
        r = _report("KLGA", datetime(2026, 6, 10, 18, 51, tzinfo=UTC), 21.1, t_group=True)
        assert settlement_temp_for_report(r, "F") == pytest.approx(21.1 * 9 / 5 + 32)

    def test_f_city_without_t_group_is_skipped_fail_closed(self):
        r = _report("KLGA", datetime(2026, 6, 10, 18, 51, tzinfo=UTC), 21.0, t_group=False)
        assert settlement_temp_for_report(r, "F") is None

    def test_c_city_whole_degree_is_exact(self):
        r = _report("RKSI", datetime(2026, 6, 10, 5, 0, tzinfo=UTC), 21.0, t_group=False)
        assert settlement_temp_for_report(r, "C") == pytest.approx(21.0)

    def test_missing_temp_is_skipped(self):
        r = _report("KLGA", datetime(2026, 6, 10, 18, 51, tzinfo=UTC), None)
        assert settlement_temp_for_report(r, "F") is None


# ===========================================================================
# Running extremes: local-day membership, truncation, station filter
# ===========================================================================

class TestRunningExtremes:
    def test_local_day_membership_is_city_timezone(self):
        seoul = _seoul()
        # 2026-06-09T14:00Z = Jun 9 23:00 KST (prev local day);
        # 2026-06-09T16:00Z = Jun 10 01:00 KST (target day).
        reports = [
            _report("RKSI", datetime(2026, 6, 9, 14, 0, tzinfo=UTC), 28.0, t_group=False),
            _report("RKSI", datetime(2026, 6, 9, 16, 0, tzinfo=UTC), 21.0, t_group=False),
            _report("RKSI", datetime(2026, 6, 9, 18, 0, tzinfo=UTC), 19.0, t_group=False),
        ]
        ex = running_extremes_for_local_day(reports, city=seoul, target_date="2026-06-10")
        assert ex.sample_count == 2
        assert ex.high_so_far == pytest.approx(21.0)  # the 28C report belongs to Jun 9 local
        assert ex.low_so_far == pytest.approx(19.0)
        assert ex.current_temp == pytest.approx(19.0)

    def test_europe_low_boundary_excludes_tminus1_23_and_includes_target_00_01_23(self):
        london = _london()
        reports = [
            # 2026-06-17T22:00Z = Jun 17 23:00 BST, previous local day.
            _report("EGLC", datetime(2026, 6, 17, 22, 0, tzinfo=UTC), 10.0, t_group=False),
            # Target local day starts at 2026-06-17T23:00Z.
            _report("EGLC", datetime(2026, 6, 17, 23, 0, tzinfo=UTC), 16.0, t_group=False),
            _report("EGLC", datetime(2026, 6, 18, 0, 0, tzinfo=UTC), 14.0, t_group=False),
            _report("EGLC", datetime(2026, 6, 18, 22, 0, tzinfo=UTC), 12.0, t_group=False),
        ]

        before_midnight = running_extremes_for_local_day(
            reports,
            city=london,
            target_date="2026-06-18",
            as_of=datetime(2026, 6, 17, 22, 30, tzinfo=UTC),
        )
        at_00 = running_extremes_for_local_day(
            reports,
            city=london,
            target_date="2026-06-18",
            as_of=datetime(2026, 6, 17, 23, 30, tzinfo=UTC),
        )
        at_01 = running_extremes_for_local_day(
            reports,
            city=london,
            target_date="2026-06-18",
            as_of=datetime(2026, 6, 18, 0, 30, tzinfo=UTC),
        )
        late_day = running_extremes_for_local_day(
            reports,
            city=london,
            target_date="2026-06-18",
            as_of=datetime(2026, 6, 18, 22, 30, tzinfo=UTC),
        )

        assert before_midnight.sample_count == 0
        assert at_00.sample_count == 1
        assert at_00.low_so_far == pytest.approx(16.0)
        assert at_01.sample_count == 2
        assert at_01.low_so_far == pytest.approx(14.0)
        assert late_day.sample_count == 3
        assert late_day.low_so_far == pytest.approx(12.0)

    def test_as_of_truncation_excludes_later_reports(self):
        seoul = _seoul()
        reports = [
            _report("RKSI", datetime(2026, 6, 9, 16, 0, tzinfo=UTC), 21.0, t_group=False),
            _report("RKSI", datetime(2026, 6, 9, 20, 0, tzinfo=UTC), 26.0, t_group=False),
        ]
        ex = running_extremes_for_local_day(
            reports, city=seoul, target_date="2026-06-10",
            as_of=datetime(2026, 6, 9, 18, 0, tzinfo=UTC),
        )
        assert ex.sample_count == 1
        assert ex.high_so_far == pytest.approx(21.0)

    def test_other_station_reports_ignored_and_unit_law_skips_counted(self):
        nyc = _nyc()
        t = datetime(2026, 6, 10, 16, 51, tzinfo=UTC)
        reports = [
            _report("KJFK", t, 25.0, t_group=True),                 # wrong station
            _report("KLGA", t, 21.1, t_group=True),                 # used
            _report("KLGA", t + timedelta(hours=1), 23.0, t_group=False),  # unit-law skip
        ]
        ex = running_extremes_for_local_day(reports, city=nyc, target_date="2026-06-10")
        assert ex.sample_count == 1
        assert ex.skipped_unit_law == 1
        assert ex.high_so_far == pytest.approx(21.1 * 9 / 5 + 32)


# ===========================================================================
# Hard-fact statuses + provenance
# ===========================================================================

class TestObservationStatuses:
    def _extremes(self, city, **over):
        t = datetime(2026, 6, 10, 16, 51, tzinfo=UTC)
        reports = [_report(city.wu_station, t, 21.1, t_group=True)]
        return running_extremes_for_local_day(reports, city=city, target_date=over.pop("target_date", "2026-06-10"))

    def test_valid_observation_is_live_authority_and_passes_reactor_gate(self):
        nyc = _nyc()
        source = fast_obs_source_for_city(nyc)
        assert source is not None and source.source_id == "aviationweather_metar"
        obs = fast_obs_to_day0_observation(
            city=nyc, extremes=self._extremes(nyc), metric="high", source=source
        )
        assert obs["live_authority_status"] == "live"
        assert obs["source_authorized_status"] == "AUTHORIZED"
        assert obs["dst_status"] == "UNAMBIGUOUS"
        # available_at is the feed receiptTime, not our wall clock
        assert obs["observation_available_at"].startswith("2026-06-10T16:55")
        # Field-by-field equivalent of the reactor's 8-field hard-fact gate:
        assert all(
            obs[k] == v
            for k, v in {
                "source_match_status": "MATCH",
                "local_date_status": "MATCH",
                "station_match_status": "MATCH",
                "dst_status": "UNAMBIGUOUS",
                "metric_match_status": "MATCH",
                "rounding_status": "MATCH",
                "source_authorized_status": "AUTHORIZED",
                "live_authority_status": "live",
            }.items()
        )

    def test_wrong_local_date_is_not_live_authority(self):
        nyc = _nyc()
        source = fast_obs_source_for_city(nyc)
        ex = self._extremes(nyc)
        # claim the obs belongs to tomorrow -> local_date MISMATCH
        obs = fast_obs_to_day0_observation(
            city=nyc,
            extremes=ex.__class__(**{**ex.__dict__, "target_date": "2026-06-11"}),
            metric="high",
            source=source,
        )
        assert obs["local_date_status"] == "MISMATCH"
        assert obs["live_authority_status"] == "blocked"

    def test_non_wu_icao_city_has_no_fast_source(self):
        hko = SimpleNamespace(
            name="Hong Kong", timezone="Asia/Hong_Kong", settlement_unit="C",
            wu_station="VHHH", settlement_source_type="hko",
        )
        assert fast_obs_source_for_city(hko) is None


# ===========================================================================
# R6 — monotone emission through the real event store
# ===========================================================================

def _world_conn():
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


class TestEmitterMonotone:
    def _emit(self, emitter, conn, reports, when):
        return emitter.emit_events(
            world_conn=conn,
            cities=[_tokyo()],
            decision_time=when,
            received_at=when.isoformat(),
            limit=20,
        )

    def test_first_sight_emits_then_unchanged_is_silent_then_move_emits(self):
        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)  # Jun 10 01:00 JST
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: reports, min_fetch_interval_s=0.0)

        n1 = self._emit(emitter, conn, reports, t0 + timedelta(minutes=10))
        assert n1 == 2  # high + low first sight

        n2 = self._emit(emitter, conn, reports, t0 + timedelta(minutes=20))
        assert n2 == 0  # unchanged extreme -> monotone memo holds emission

        reports.append(_report("RJTT", t0 + timedelta(hours=1), 24.0, t_group=False))
        n3 = self._emit(emitter, conn, reports, t0 + timedelta(minutes=80))
        assert n3 == 1  # running max moved 21->24; low unchanged

        rows = conn.execute(
            "SELECT payload_json FROM opportunity_events WHERE event_type='DAY0_EXTREME_UPDATED'"
        ).fetchall()
        assert len(rows) == 3
        import json as _json

        payloads = [_json.loads(r["payload_json"]) for r in rows]
        assert all(p["settlement_source"] == "aviationweather_metar" for p in payloads)
        assert all(p["live_authority_status"] == "live" for p in payloads)

    def test_restart_short_window_cannot_emit_regressed_high(self):
        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)  # Jun 10 01:00 JST

        first_reports = [_report("RJTT", t0, 25.0, t_group=False)]
        first = Day0FastObsEmitter(
            fetcher=lambda stations, **kw: first_reports,
            min_fetch_interval_s=0.0,
        )
        assert self._emit(first, conn, first_reports, t0 + timedelta(minutes=10)) == 2

        short_window_reports = [_report("RJTT", t0 + timedelta(hours=1), 24.0, t_group=False)]
        restarted = Day0FastObsEmitter(
            fetcher=lambda stations, **kw: short_window_reports,
            min_fetch_interval_s=0.0,
        )
        restarted.emit_events(
            world_conn=conn,
            cities=[_tokyo()],
            decision_time=t0 + timedelta(hours=1, minutes=10),
            received_at=(t0 + timedelta(hours=1, minutes=10)).isoformat(),
            limit=20,
        )

        high_values = [
            row[0]
            for row in conn.execute(
                """
                SELECT CAST(json_extract(payload_json, '$.rounded_value') AS INTEGER)
                  FROM opportunity_events
                 WHERE event_type='DAY0_EXTREME_UPDATED'
                   AND json_extract(payload_json, '$.city') = 'Tokyo'
                   AND json_extract(payload_json, '$.metric') = 'high'
                 ORDER BY created_at
                """
            ).fetchall()
        ]
        assert high_values == [25], "restart recovery must suppress lower later high=24"

    def test_emitted_event_passes_reactor_hard_fact_gate(self):
        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: reports, min_fetch_interval_s=0.0)
        assert self._emit(emitter, conn, reports, t0 + timedelta(minutes=10)) == 2

        import json as _json
        from src.events.reactor import _day0_hard_fact_payload_live_eligible

        row = conn.execute(
            "SELECT payload_json FROM opportunity_events WHERE event_type='DAY0_EXTREME_UPDATED' LIMIT 1"
        ).fetchone()
        event = SimpleNamespace(payload_json=row["payload_json"], payload=_json.loads(row["payload_json"]))
        assert _day0_hard_fact_payload_live_eligible(event) is True

    def test_f_city_with_only_whole_c_reports_emits_nothing(self):
        conn = _world_conn()
        t0 = datetime(2026, 6, 10, 16, 51, tzinfo=UTC)
        reports = [_report("KLGA", t0, 21.0, t_group=False)]
        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: reports, min_fetch_interval_s=0.0)
        n = emitter.emit_events(
            world_conn=conn, cities=[_nyc()],
            decision_time=t0 + timedelta(minutes=10),
            received_at=(t0 + timedelta(minutes=10)).isoformat(), limit=20,
        )
        assert n == 0

    def test_fetch_failure_is_fail_soft_zero(self):
        conn = _world_conn()
        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: [], min_fetch_interval_s=0.0)
        n = emitter.emit_events(
            world_conn=conn, cities=[_tokyo()],
            decision_time=datetime(2026, 6, 10, 4, 0, tzinfo=UTC),
            received_at="2026-06-10T04:00:00+00:00", limit=20,
        )
        assert n == 0


# ===========================================================================
# R7 — oracle anomaly guard
# ===========================================================================

class TestOracleAnomaly:
    def _reports(self):
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)  # Jun 10 KST early
        return [
            _report("RKSI", t0, 21.0, t_group=False),
            _report("RKSI", t0 + timedelta(hours=1), 22.0, t_group=False),
            _report("RKSI", t0 + timedelta(hours=2), 26.0, t_group=False),  # after WU's last obs
        ]

    def test_matching_extremes_do_not_diverge(self):
        verdict = check_wu_metar_divergence(
            city=_seoul(), target_date="2026-06-10", metar_reports=self._reports(),
            wu_high_so_far=22.0, wu_low_so_far=21.0,
            wu_last_obs_time=datetime(2026, 6, 9, 17, 0, tzinfo=UTC),
        )
        assert verdict.compared is True and verdict.diverged is False

    def test_metar_rise_after_wu_last_obs_is_latency_not_divergence(self):
        """R7 truncation contract: the 26C report (after WU's last obs) must be
        excluded from the comparison — METAR freshness is not an anomaly."""
        verdict = check_wu_metar_divergence(
            city=_seoul(), target_date="2026-06-10", metar_reports=self._reports(),
            wu_high_so_far=22.0, wu_low_so_far=21.0,
            wu_last_obs_time=datetime(2026, 6, 9, 17, 0, tzinfo=UTC),
        )
        assert verdict.high_delta == pytest.approx(0.0)

    def test_true_divergence_flags_and_pauses(self):
        verdict = check_wu_metar_divergence(
            city=_seoul(), target_date="2026-06-10", metar_reports=self._reports(),
            wu_high_so_far=27.0,  # WU claims 5C above the same-window METAR max
            wu_low_so_far=21.0,
            wu_last_obs_time=datetime(2026, 6, 9, 17, 0, tzinfo=UTC),
        )
        assert verdict.compared and verdict.diverged
        flag_day0_oracle_anomaly("Seoul", "2026-06-10", detail=verdict.detail)
        assert is_day0_family_paused("Seoul", "2026-06-10") is True
        assert is_day0_family_paused("Seoul", "2026-06-11") is False
        assert clear_day0_oracle_anomaly("Seoul", "2026-06-10") is True
        assert is_day0_family_paused("Seoul", "2026-06-10") is False

    def test_pause_expires_after_ttl(self):
        flag_day0_oracle_anomaly(
            "Seoul", "2026-06-10", detail="t",
            now=datetime(2026, 6, 10, 0, 0, tzinfo=UTC),
        )
        assert is_day0_family_paused(
            "Seoul", "2026-06-10", now=datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
        ) is True
        assert is_day0_family_paused(
            "Seoul", "2026-06-10", now=datetime(2026, 6, 12, 1, 0, tzinfo=UTC)
        ) is False

    def test_missing_wu_side_is_not_compared_and_not_paused(self):
        verdict = check_wu_metar_divergence(
            city=_seoul(), target_date="2026-06-10", metar_reports=self._reports(),
            wu_high_so_far=None, wu_low_so_far=None, wu_last_obs_time=None,
        )
        assert verdict.compared is False and verdict.diverged is False

    def test_era_day0_q_path_raises_fail_closed_when_paused(self):
        """Enforcement relationship: a paused family's DAY0 q construction must
        raise (-> LIVE_INFERENCE_INPUTS_MISSING:DAY0_ORACLE_ANOMALY_PAUSED
        deterministic no-submit receipt at the proofs boundary)."""
        from src.engine.event_reactor_adapter import _live_yes_probabilities

        flag_day0_oracle_anomaly("Seoul", "2026-06-10", detail="test")
        event = SimpleNamespace(event_type="DAY0_EXTREME_UPDATED")
        family = SimpleNamespace(city="Seoul", target_date="2026-06-10", candidates=[])
        with pytest.raises(ValueError, match="DAY0_ORACLE_ANOMALY_PAUSED"):
            _live_yes_probabilities(
                event=event, payload={}, family=family,
                conn=None, calibration_conn=None, native_costs={},
                decision_time=datetime(2026, 6, 10, 4, 0, tzinfo=UTC),
            )

    def test_unpaused_family_does_not_raise_anomaly_error(self):
        """Counterfactual: same call without a flag must NOT raise the anomaly
        error (it will fail later/differently on the None conn — anything but
        DAY0_ORACLE_ANOMALY_PAUSED is acceptable here)."""
        from src.engine.event_reactor_adapter import _live_yes_probabilities

        event = SimpleNamespace(event_type="DAY0_EXTREME_UPDATED")
        family = SimpleNamespace(city="Seoul", target_date="2026-06-10", candidates=[])
        try:
            _live_yes_probabilities(
                event=event, payload={"rounded_value": 25.0, "metric": "high"},
                family=family, conn=None, calibration_conn=None, native_costs={},
                decision_time=datetime(2026, 6, 10, 4, 0, tzinfo=UTC),
            )
        except ValueError as exc:
            assert "DAY0_ORACLE_ANOMALY_PAUSED" not in str(exc)
        except Exception:
            pass  # any non-anomaly failure mode is out of scope here


# ===========================================================================
# R12 — empirical divergence thresholds (operator correction 2026-06-10:
# the 1.5F/1.0C guess replaced by measured per-city thresholds; provenance
# recorded; non-settlement-faithful cities excluded from the fast lane)
# ===========================================================================

class TestEmpiricalThresholds:
    def test_measured_city_uses_empirical_threshold(self):
        from src.data.day0_oracle_anomaly import divergence_threshold_for_city

        threshold, provenance = divergence_threshold_for_city("Tokyo", "C")
        assert provenance == "empirical"
        assert threshold == pytest.approx(1.0)  # feeds byte-identical post-rounding
        threshold, provenance = divergence_threshold_for_city("Seoul", "C")
        assert provenance == "empirical"
        assert threshold == pytest.approx(2.0)  # real +-1C spread measured

    def test_unmeasured_city_falls_back_to_conservative_default(self):
        from src.data.day0_oracle_anomaly import (
            DIVERGENCE_THRESHOLD,
            divergence_threshold_for_city,
        )

        threshold, provenance = divergence_threshold_for_city("Wellington", "C")
        assert provenance == "default_guess"
        assert threshold == pytest.approx(DIVERGENCE_THRESHOLD["C"])
        threshold_f, _ = divergence_threshold_for_city("NoSuchCity", "F")
        assert threshold_f == pytest.approx(DIVERGENCE_THRESHOLD["F"])

    def test_missing_model_file_degrades_to_defaults(self, tmp_path):
        from src.data.day0_oracle_anomaly import (
            DIVERGENCE_THRESHOLD,
            city_metar_settlement_faithful,
            divergence_threshold_for_city,
        )

        bogus = tmp_path / "nope.json"
        threshold, provenance = divergence_threshold_for_city("Tokyo", "C", path=bogus)
        assert provenance == "default_guess"
        assert threshold == pytest.approx(DIVERGENCE_THRESHOLD["C"])
        assert city_metar_settlement_faithful("Seoul", path=bogus) is True

    def test_settlement_faithfulness_verdicts(self):
        from src.data.day0_oracle_anomaly import city_metar_settlement_faithful

        assert city_metar_settlement_faithful("Seoul") is False   # measured divergence
        assert city_metar_settlement_faithful("Tokyo") is True
        assert city_metar_settlement_faithful("NYC") is True
        assert city_metar_settlement_faithful("UnmeasuredCity") is True

    def test_unfaithful_but_well_measured_city_gets_margin_absorbed_not_excluded(self):
        """2026-07-16 (day0 defect-5): Seoul's METAR integer is not reliably
        WU's settlement integer, but the divergence IS well-measured (990
        matched pairs, empirical_threshold=2.0C) — binary exclusion where
        margin-absorption machinery already existed one layer over
        (day0_hard_fact_exit._metar_kill_margin_units) was the same disease
        as the climatology-band defect. Seoul now gets a fast-lane source
        WITH the measured margin, not None; faithful cities keep margin 0."""
        seoul_source = fast_obs_source_for_city(_seoul())
        assert seoul_source is not None
        assert seoul_source.margin_units == pytest.approx(2.0)

        tokyo_source = fast_obs_source_for_city(_tokyo())
        assert tokyo_source is not None
        assert tokyo_source.margin_units == pytest.approx(0.0)

        nyc_source = fast_obs_source_for_city(_nyc())
        assert nyc_source is not None
        assert nyc_source.margin_units == pytest.approx(0.0)

    def test_guard_verdict_records_threshold_provenance(self):
        verdict = check_wu_metar_divergence(
            city=_tokyo(), target_date="2026-06-10",
            metar_reports=[
                MetarReport(
                    station_id="RJTT",
                    obs_time=datetime(2026, 6, 9, 16, 0, tzinfo=UTC),
                    receipt_time=datetime(2026, 6, 9, 16, 4, tzinfo=UTC),
                    temp_c=21.0, metar_type="METAR", raw="METAR RJTT 21/15",
                ),
            ],
            wu_high_so_far=21.0, wu_low_so_far=21.0,
            # within the round-2 coverage tolerance of the METAR window (the
            # detector now refuses to conclude when METAR lags WU's last obs)
            wu_last_obs_time=datetime(2026, 6, 9, 16, 4, tzinfo=UTC),
        )
        assert verdict.compared is True
        assert "threshold_provenance=empirical" in verdict.detail

    def test_empirical_tightening_one_unit_divergence_now_flags_for_clean_city(self):
        """For a measured-identical city the threshold tightened from the 1.5F
        guess to 1.0 — a 1.4F rounded-extreme divergence that the guess would
        have ignored now flags (sharper tamper detector). Use NYC (F)."""
        from src.data.day0_oracle_anomaly import divergence_threshold_for_city

        threshold, provenance = divergence_threshold_for_city("NYC", "F")
        assert provenance == "empirical" and threshold == pytest.approx(1.0)
        assert 1.4 > threshold  # would NOT have exceeded the old 1.5F guess


# ===========================================================================
# day0 defect-5 (2026-07-16) — margin absorption replaces binary exclusion
# for a measured-but-not-settlement-faithful METAR station. Seoul/RKSI type
# specimen: a raw 30.0C reading used to enter NOTHING (fast_obs_source_for_city
# returned None); it now enters the running belief at 28.0C (30.0 - the
# measured 2.0C margin), not at face value and not excluded.
# ===========================================================================

class TestMetarMarginAbsorption:
    def _reports(self, temps_with_minutes, station="RKSI"):
        base = datetime(2026, 6, 10, 0, 0, tzinfo=UTC)
        return [
            _report(station, base + timedelta(minutes=m), t, t_group=False)
            for m, t in temps_with_minutes
        ]

    def test_seoul_type_specimen_reading_enters_belief_shifted_by_margin(self):
        """The type specimen: METAR 30.0C at Seoul/RKSI, margin 2.0C ->
        high_so_far == 28.0C, not 30.0 (face value) and not None (excluded,
        the pre-fix behavior — fast_obs_source_for_city(_seoul()) used to
        return None, so this reading previously entered nothing at all)."""
        reports = self._reports([(0, 30.0)])
        ex = running_extremes_for_local_day(
            reports, city=_seoul(), target_date="2026-06-10", margin_units=2.0,
        )
        assert ex.high_so_far == pytest.approx(28.0)
        assert ex.current_temp == pytest.approx(30.0)  # diagnostic field stays raw

    def test_faithful_city_margin_zero_is_unchanged_face_value(self):
        reports = self._reports([(0, 30.0)], station="RJTT")
        ex = running_extremes_for_local_day(
            reports, city=_tokyo(), target_date="2026-06-10", margin_units=0.0,
        )
        assert ex.high_so_far == pytest.approx(30.0)

    def test_low_metric_mirror_margin_direction_flips(self):
        """LOW metric: a reading proves the true min is AT MOST reading +
        margin (margin adds, not subtracts, for the low side)."""
        reports = self._reports([(0, 10.0)])
        ex = running_extremes_for_local_day(
            reports, city=_seoul(), target_date="2026-06-10", margin_units=2.0,
        )
        assert ex.low_so_far == pytest.approx(12.0)

    def test_seoul_source_resolves_measured_margin_from_real_config(self):
        source = fast_obs_source_for_city(_seoul())
        assert source is not None
        assert source.margin_units == pytest.approx(2.0)

    def test_emitted_observation_records_margin_and_shifted_raw_value(self):
        """End-to-end through fast_obs_to_day0_observation: raw_value in the
        emitted payload is the ALREADY-shifted value (consistent with what
        gets rounded and stored), and metar_margin_units_applied records the
        margin so the pre-shift reading stays reconstructable."""
        source = fast_obs_source_for_city(_seoul())
        assert source is not None
        reports = self._reports([(0, 30.0)])
        extremes = running_extremes_for_local_day(
            reports, city=_seoul(), target_date="2026-06-10",
            margin_units=source.margin_units,
        )
        obs = fast_obs_to_day0_observation(
            city=_seoul(), extremes=extremes, metric="high", source=source,
        )
        assert obs["raw_value"] == pytest.approx(28.0)
        assert obs["metar_margin_units_applied"] == pytest.approx(2.0)
        # pre-shift reading is reconstructable: raw_value + margin for HIGH
        assert obs["raw_value"] + obs["metar_margin_units_applied"] == pytest.approx(30.0)

    def test_thin_sample_unfaithful_city_still_excluded(self, tmp_path):
        """A measured-but-not-faithful city whose divergence sample is too
        thin to trust (threshold_provenance != 'empirical') stays excluded —
        margin-absorption requires a well-sampled measurement, not just any
        unfaithful verdict."""
        import json

        from src.data.day0_oracle_anomaly import metar_margin_units_for_city

        path = tmp_path / "divergence.json"
        path.write_text(json.dumps({
            "cities": {
                "ThinCity": {
                    "matched_pairs": 12,
                    "empirical_threshold": 2.5,
                    "threshold_provenance": "thin_sample",
                    "settlement_faithful": False,
                },
            },
        }))
        assert metar_margin_units_for_city("ThinCity", "C", path=path) is None

    def test_never_measured_city_keeps_current_default_guess_margin(self):
        """A city with NO entry at all in wu_metar_divergence.json defaults
        to settlement_faithful=True (the guard threshold still covers it),
        so it is INCLUDED — not excluded — exactly as it is today. Its
        margin is the conservative DEFAULT_GUESS threshold (1.0C / 1.5F,
        provenance='default_guess'), not 0.0 — this is the pre-existing
        behavior this fix preserves unchanged (the OLD
        _metar_kill_margin_units gave unmeasured faithful cities this same
        non-zero margin; only an EMPIRICALLY byte-identical measured city
        gets margin 0.0)."""
        from src.data.day0_oracle_anomaly import metar_margin_units_for_city

        assert metar_margin_units_for_city("CityNeverMeasured", "C") == pytest.approx(1.0)
        assert metar_margin_units_for_city("CityNeverMeasured", "F") == pytest.approx(1.5)


# ===========================================================================
# day0 defect-ledger (2026-07-16) — boot hydration. A fresh process's
# _cached_reports is empty until the first successful HTTP fetch; hydration
# seeds it from observation_prints instead, so the belief isn't silently
# empty for the restart window. The kill-memo recovery path
# (_recover_kill_memo_from_events) is untouched and stays as defense in
# depth — these tests are scoped to the in-process cache path only.
# ===========================================================================

class TestLedgerPublicationDelta:
    def test_only_unconfirmed_publications_are_sent_to_sqlite(self):
        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        first = _report("RJTT", t0, 21.0, t_group=False)
        second = _report(
            "RJTT",
            t0 + timedelta(minutes=30),
            22.0,
            t_group=False,
        )
        source_reports = [first]
        emitter = Day0FastObsEmitter(
            fetcher=lambda _stations, **_kw: list(source_reports),
            min_fetch_interval_s=0.0,
        )

        pf1 = emitter.prefetch(
            cities=[_tokyo()],
            decision_time=t0 + timedelta(minutes=5),
        )
        assert pf1.ledger_reports == (first,)
        inserted_event_ids: list[str] = []
        inserted_families: list[tuple[str, str, str]] = []
        assert emitter.emit_prefetched(
            world_conn=conn,
            prefetch=pf1,
            received_at=(t0 + timedelta(minutes=5)).isoformat(),
            inserted_event_ids=inserted_event_ids,
            inserted_families=inserted_families,
        ) == 2
        assert len(inserted_event_ids) == 2
        assert len(set(inserted_event_ids)) == 2
        assert inserted_families == [
            ("Tokyo", "2026-06-10", "high"),
            ("Tokyo", "2026-06-10", "low"),
        ]

        pf2 = emitter.prefetch(
            cities=[_tokyo()],
            decision_time=t0 + timedelta(minutes=6),
        )
        assert pf2.ledger_reports == ()

        source_reports.append(second)
        pf3 = emitter.prefetch(
            cities=[_tokyo()],
            decision_time=t0 + timedelta(minutes=36),
        )
        assert pf3.ledger_reports == (second,)

    def test_failed_ledger_append_does_not_acknowledge_publication(self, monkeypatch):
        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        report = _report("RJTT", t0, 21.0, t_group=False)
        emitter = Day0FastObsEmitter(
            fetcher=lambda _stations, **_kw: [report],
            min_fetch_interval_s=0.0,
        )
        monkeypatch.setattr(
            "src.state.schema.observation_prints_schema.append_print",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                sqlite3.OperationalError("busy")
            ),
        )

        pf1 = emitter.prefetch(
            cities=[_tokyo()],
            decision_time=t0 + timedelta(minutes=5),
        )
        emitter.emit_prefetched(
            world_conn=conn,
            prefetch=pf1,
            received_at=(t0 + timedelta(minutes=5)).isoformat(),
        )
        pf2 = emitter.prefetch(
            cities=[_tokyo()],
            decision_time=t0 + timedelta(minutes=6),
        )

        assert pf2.ledger_reports == (report,)


class TestLedgerHydration:
    def test_cold_start_hydrates_cache_and_returns_todays_ledger_max(self):
        from src.data.day0_fast_obs import fast_obs_source_for_city
        from src.state.schema.observation_prints_schema import append_print

        conn = _world_conn()
        tokyo = _tokyo()
        append_print(
            conn, city="Tokyo", station_id="RJTT", source_channel="aviationweather_metar",
            publish_ts_utc="2026-06-09T16:00:00+00:00", value_native=21.0, unit="C",
            fetched_at_utc="2026-06-09T16:04:00+00:00", raw_report="METAR RJTT 21/15",
        )
        append_print(
            conn, city="Tokyo", station_id="RJTT", source_channel="aviationweather_metar",
            publish_ts_utc="2026-06-09T17:00:00+00:00", value_native=24.0, unit="C",
            fetched_at_utc="2026-06-09T17:04:00+00:00", raw_report="METAR RJTT 24/15",
        )
        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: [], min_fetch_interval_s=0.0)
        source = fast_obs_source_for_city(tokyo)
        eligible = ((tokyo, source, "2026-06-10"),)

        hydrated_count = emitter.hydrate_from_ledger(conn, eligible)
        assert hydrated_count == 2

        extremes = emitter.latest_extremes(
            tokyo, "2026-06-10", as_of=datetime(2026, 6, 9, 18, 0, tzinfo=UTC),
        )
        assert extremes is not None
        assert extremes.high_so_far == pytest.approx(24.0)

    def test_hydration_is_noop_once_the_cache_is_warm(self):
        from src.data.day0_fast_obs import fast_obs_source_for_city
        from src.state.schema.observation_prints_schema import append_print

        conn = _world_conn()
        tokyo = _tokyo()
        append_print(
            conn, city="Tokyo", station_id="RJTT", source_channel="aviationweather_metar",
            publish_ts_utc="2026-06-09T16:00:00+00:00", value_native=21.0, unit="C",
            fetched_at_utc="2026-06-09T16:04:00+00:00",
        )
        emitter = Day0FastObsEmitter(
            fetcher=lambda stations, **kw: [_report("RJTT", datetime(2026, 6, 9, 16, 0, tzinfo=UTC), 30.0, t_group=False)],
            min_fetch_interval_s=0.0,
        )
        emitter._reports_with_status(["RJTT"])  # a live fetch already warmed the cache
        source = fast_obs_source_for_city(tokyo)
        eligible = ((tokyo, source, "2026-06-10"),)

        hydrated_count = emitter.hydrate_from_ledger(conn, eligible)
        assert hydrated_count == 0  # no-op -- must not overwrite the live 30.0 with the ledger's 21.0

    def test_hydration_with_no_ledger_data_is_a_safe_noop(self):
        from src.data.day0_fast_obs import fast_obs_source_for_city

        conn = _world_conn()
        tokyo = _tokyo()
        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: [], min_fetch_interval_s=0.0)
        source = fast_obs_source_for_city(tokyo)
        eligible = ((tokyo, source, "2026-06-10"),)

        assert emitter.hydrate_from_ledger(conn, eligible) == 0
        assert emitter._cached_reports == []

    def test_emit_prefetched_hydrates_on_a_cold_start_with_no_fetch_this_cycle(self):
        """The real entry point: emit_prefetched calls hydration even when
        THIS cycle's own fetch produced nothing -- the exact scenario
        hydration exists for (an outage spanning multiple cycles)."""
        from src.data.day0_fast_obs import FastObsPrefetch, fast_obs_source_for_city
        from src.state.schema.observation_prints_schema import append_print

        conn = _world_conn()
        tokyo = _tokyo()
        append_print(
            conn, city="Tokyo", station_id="RJTT", source_channel="aviationweather_metar",
            publish_ts_utc="2026-06-09T16:00:00+00:00", value_native=21.0, unit="C",
            fetched_at_utc="2026-06-09T16:04:00+00:00",
        )
        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: [], min_fetch_interval_s=0.0)
        source = fast_obs_source_for_city(tokyo)
        decision_time = datetime(2026, 6, 9, 18, 0, tzinfo=UTC)
        prefetch = FastObsPrefetch(
            eligible=((tokyo, source, "2026-06-10"),),
            reports=(),  # this cycle's own fetch produced nothing
            freshness_status="no_data",
            cache_age_s=None,
            decision_time=decision_time,
        )

        emitter.emit_prefetched(world_conn=conn, prefetch=prefetch, received_at=decision_time.isoformat())

        assert len(emitter._cached_reports) == 1
        assert emitter._cached_reports[0].temp_c == pytest.approx(21.0)


# ===========================================================================
# R19 — source-failure discipline + mutex/no-HTTP split (PR#404 P0-2 / P0-3)
# ===========================================================================

class TestFetchFailureDiscipline:
    """PR#404 P0-3: a fetch failure after a populated cache must (a) arm the
    failure throttle (no tight retry storm), (b) serve the old cache ONLY with
    an explicit stale status, and (c) never emit live-authority events from a
    cache older than the city's staleness budget."""

    def _emitter_with_cache(self, reports, interval=300.0):
        from src.data.day0_fast_obs import Day0FastObsEmitter

        calls = {"n": 0}

        def fetcher(stations, **kw):
            calls["n"] += 1
            return list(reports) if calls["n"] == 1 else []

        emitter = Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=interval)
        return emitter, calls

    def test_failure_serves_stale_with_explicit_status_and_throttles(self):
        from src.data.day0_fast_obs import (
            FETCH_CACHE_HIT,
            FETCH_FRESH,
            FETCH_STALE_AFTER_FAILURE,
        )

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        emitter, calls = self._emitter_with_cache(reports, interval=0.0)

        out, status, _age = emitter._reports_with_status(["RJTT"])
        assert status == FETCH_FRESH and out and calls["n"] == 1

        # cache now exists; interval 0 -> next call attempts again and FAILS
        out, status, age = emitter._reports_with_status(["RJTT"])
        assert calls["n"] == 2
        assert status == FETCH_STALE_AFTER_FAILURE
        assert out, "old cache is served, but never silently as fresh"

        # failure-throttle: with a real interval, the next pass must NOT
        # re-invoke the fetcher (no retry storm during an outage)
        emitter.min_fetch_interval_s = 3600.0
        out, status, _age = emitter._reports_with_status(["RJTT"])
        assert calls["n"] == 2, "failed attempt must arm the throttle"
        assert status in (FETCH_STALE_AFTER_FAILURE, FETCH_CACHE_HIT)

    def test_slow_success_does_not_double_the_start_to_start_poll_interval(self, monkeypatch):
        import src.data.day0_fast_obs as fast_obs

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        calls = {"n": 0}
        clock = iter((100.0, 100.7, 105.1, 105.3))

        def fetcher(_stations, **_kwargs):
            calls["n"] += 1
            return reports

        monkeypatch.setattr(fast_obs.time, "monotonic", lambda: next(clock))
        emitter = Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=5.0)

        assert emitter._reports_with_status(["RJTT"])[1] == fast_obs.FETCH_FRESH
        assert emitter._reports_with_status(["RJTT"])[1] == fast_obs.FETCH_FRESH
        assert calls["n"] == 2

    def test_stale_cache_beyond_budget_emits_no_live_event_but_updates_kill_memo(self):
        from src.data.day0_fast_obs import Day0FastObsEmitter

        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        calls = {"n": 0}

        def fetcher(stations, **kw):
            calls["n"] += 1
            return list(reports) if calls["n"] == 1 else []

        emitter = Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=0.0)
        # pass 1: fresh fetch fills cache
        pf = emitter.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=5))
        assert pf.freshness_status == "fresh_fetch"
        # pass 2: fetch fails -> stale-after-failure; age the cache far beyond
        # Tokyo's staleness budget (60 min) by rewinding the cache clock.
        import time as _time

        emitter._cache_fetched_monotonic = _time.monotonic() - 7200.0
        pf2 = emitter.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=10))
        assert pf2.freshness_status == "stale_cache_after_failure"
        assert pf2.cache_age_s is not None and pf2.cache_age_s > 3600.0

        n = emitter.emit_prefetched(
            world_conn=conn, prefetch=pf2,
            received_at=(t0 + timedelta(minutes=10)).isoformat(), limit=20,
        )
        assert n == 0, "stale-beyond-budget cache must NOT emit live-authority events"
        rows = conn.execute(
            "SELECT COUNT(*) FROM opportunity_events WHERE event_type='DAY0_EXTREME_UPDATED'"
        ).fetchone()[0]
        assert rows == 0
        # the monotone hard-fact KILL memo still advances (staleness-safe direction)
        assert emitter.latest_rounded_extreme("Tokyo", "2026-06-10", "high") == 21

    def test_no_cache_failure_is_no_data(self):
        from src.data.day0_fast_obs import Day0FastObsEmitter, FETCH_NO_DATA

        emitter = Day0FastObsEmitter(fetcher=lambda s, **kw: [], min_fetch_interval_s=0.0)
        out, status, age = emitter._reports_with_status(["RJTT"])
        assert out == [] and status == FETCH_NO_DATA and age is None


class TestIncrementalFetchWindow:
    def test_cold_fetch_is_full_then_warm_fetch_merges_recent_delta(self):
        from src.data.day0_fast_obs import (
            Day0FastObsEmitter,
            METAR_FULL_FETCH_HOURS,
            METAR_INCREMENTAL_FETCH_HOURS,
        )

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        first = _report("RJTT", t0, 21.0, t_group=False)
        second = _report("RJTT", t0 + timedelta(hours=1), 24.0, t_group=False)
        hours = []
        payloads = [[first], [second]]

        def fetcher(_stations, **kwargs):
            hours.append(kwargs["hours"])
            return payloads.pop(0)

        emitter = Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=0.0)
        first_window, _, _ = emitter._reports_with_status(["RJTT"])
        second_window, _, _ = emitter._reports_with_status(["RJTT"])

        assert hours == [METAR_FULL_FETCH_HOURS, METAR_INCREMENTAL_FETCH_HOURS]
        assert first_window == [first]
        assert second_window == [first, second]

    def test_warm_fetch_periodically_backfills_late_publications(self, monkeypatch):
        import src.data.day0_fast_obs as fast_obs

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        report = _report("RJTT", t0, 21.0, t_group=False)
        hours = []
        clock = iter((100.0, 100.1, 1000.2, 1000.3))

        def fetcher(_stations, **kwargs):
            hours.append(kwargs["hours"])
            return [report]

        monkeypatch.setattr(fast_obs.time, "monotonic", lambda: next(clock))
        emitter = fast_obs.Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=0.0)
        emitter._reports_with_status(["RJTT"])
        emitter._reports_with_status(["RJTT"])

        assert hours == [
            fast_obs.METAR_FULL_FETCH_HOURS,
            fast_obs.METAR_BACKFILL_FETCH_HOURS,
        ]

    def test_identical_warm_payload_skips_full_window_merge(self, monkeypatch):
        import src.data.day0_fast_obs as fast_obs

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        report = _report("RJTT", t0, 21.0, t_group=False)
        emitter = fast_obs.Day0FastObsEmitter(
            fetcher=lambda _stations, **_kwargs: [report],
            min_fetch_interval_s=0.0,
        )

        first_window, _, _ = emitter._reports_with_status(["RJTT"])
        monkeypatch.setattr(
            fast_obs,
            "_merge_report_windows",
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("identical payload rebuilt the retained window")
            ),
        )
        second_window, status, _ = emitter._reports_with_status(["RJTT"])

        assert first_window == [report]
        assert second_window == [report]
        assert status == fast_obs.FETCH_FRESH

    def test_fetch_window_expands_across_an_outage(self):
        import time as _time

        from src.data.day0_fast_obs import (
            Day0FastObsEmitter,
            METAR_FULL_FETCH_HOURS,
            METAR_RECOVERY_OVERLAP_HOURS,
        )

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        report = _report("RJTT", t0, 21.0, t_group=False)
        hours = []

        def fetcher(_stations, **kwargs):
            hours.append(kwargs["hours"])
            return [report]

        emitter = Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=0.0)
        emitter._reports_with_status(["RJTT"])
        emitter._cache_fetched_monotonic = _time.monotonic() - 3 * 3600.0
        emitter._reports_with_status(["RJTT"])

        assert hours == [
            METAR_FULL_FETCH_HOURS,
            pytest.approx(3.0 + METAR_RECOVERY_OVERLAP_HOURS, abs=0.01),
        ]

    def test_ledger_hydration_does_not_skip_full_network_recovery(self):
        import time as _time

        from src.data.day0_fast_obs import Day0FastObsEmitter, METAR_FULL_FETCH_HOURS

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        hours = []

        def fetcher(_stations, **kwargs):
            hours.append(kwargs["hours"])
            return [_report("RJTT", t0, 22.0, t_group=False)]

        emitter = Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=0.0)
        emitter._cached_reports = [_report("RJTT", t0, 21.0, t_group=False)]
        emitter._cache_fetched_monotonic = _time.monotonic()
        emitter._reports_with_status(["RJTT"])

        assert hours == [METAR_FULL_FETCH_HOURS]
        assert emitter._cached_reports[0].temp_c == pytest.approx(22.0)


class TestMetarConnectionReuse:
    def test_fetch_uses_injected_http_client(self):
        from src.data.day0_fast_obs import fetch_metar_reports

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return []

        class Client:
            def __init__(self):
                self.calls = []

            def get(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return Response()

        client = Client()
        assert fetch_metar_reports(["RJTT"], hours=2.0, client=client) == []
        assert len(client.calls) == 1
        assert client.calls[0][1]["params"]["hours"] == 2.0

    def test_emitter_reuses_one_client_across_polls(self, monkeypatch):
        import src.data.day0_fast_obs as fast_obs

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        report = _report("RJTT", t0, 21.0, t_group=False)
        clients = []

        class Client:
            def __init__(self, **_kwargs):
                self.calls = 0
                clients.append(self)

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: [
                        {
                            "icaoId": report.station_id,
                            "obsTime": report.obs_time.timestamp(),
                            "receiptTime": report.receipt_time.isoformat(),
                            "temp": report.temp_c,
                            "metarType": report.metar_type,
                            "rawOb": report.raw,
                        }
                    ],
                )

        monkeypatch.setattr(fast_obs.httpx, "Client", Client)
        emitter = fast_obs.Day0FastObsEmitter(min_fetch_interval_s=0.0)
        emitter._reports_with_status(["RJTT"])
        emitter._reports_with_status(["RJTT"])

        assert len(clients) == 1
        assert clients[0].calls == 2


class TestMutexNoHttpSplit:
    """PR#404 P0-2: the world-write mutex must never span HTTP. The write phase
    (emit_prefetched) performs zero network IO; main.py prefetches BEFORE
    acquiring the mutex."""

    def test_emit_prefetched_never_invokes_the_fetcher(self):
        from src.data.day0_fast_obs import Day0FastObsEmitter, FastObsPrefetch

        def forbidden_fetcher(stations, **kw):
            raise AssertionError("HTTP fetch invoked inside the write phase")

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = (_report("RJTT", t0, 21.0, t_group=False),)
        emitter = Day0FastObsEmitter(fetcher=forbidden_fetcher, min_fetch_interval_s=0.0)
        from src.data.day0_fast_obs import fast_obs_source_for_city

        city = _tokyo()
        prefetch = FastObsPrefetch(
            eligible=((city, fast_obs_source_for_city(city), "2026-06-10"),),
            reports=reports,
            freshness_status="fresh_fetch",
            cache_age_s=0.0,
            decision_time=t0 + timedelta(minutes=5),
        )
        conn = _world_conn()
        n = emitter.emit_prefetched(
            world_conn=conn, prefetch=prefetch,
            received_at=(t0 + timedelta(minutes=5)).isoformat(), limit=20,
        )
        assert n == 2  # high + low emitted with ZERO fetcher invocations

    def test_emit_prefetched_only_recomputes_changed_stations(self, monkeypatch):
        import src.data.day0_fast_obs as fast_obs

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        tokyo = _tokyo()
        osaka = SimpleNamespace(
            name="Osaka",
            timezone="Asia/Tokyo",
            settlement_unit="C",
            wu_station="RJOO",
            settlement_source_type="wu_icao",
        )
        tokyo_report = _report("RJTT", t0, 21.0, t_group=False)
        osaka_report = _report("RJOO", t0, 22.0, t_group=False)
        prefetch = fast_obs.FastObsPrefetch(
            eligible=(
                (
                    tokyo,
                    fast_obs.FastObsSource(
                        source_id=fast_obs.FAST_OBS_SOURCE_ID,
                        station_id="RJTT",
                        authority="ICAO_STATION_NATIVE",
                    ),
                    "2026-06-10",
                ),
                (
                    osaka,
                    fast_obs.FastObsSource(
                        source_id=fast_obs.FAST_OBS_SOURCE_ID,
                        station_id="RJOO",
                        authority="ICAO_STATION_NATIVE",
                    ),
                    "2026-06-10",
                ),
            ),
            reports=(tokyo_report, osaka_report),
            freshness_status=fast_obs.FETCH_FRESH,
            cache_age_s=0.0,
            decision_time=t0 + timedelta(minutes=5),
            ledger_reports=(tokyo_report,),
        )
        original = fast_obs.running_extremes_for_local_day
        seen: list[str] = []

        def _running_extremes(*args, **kwargs):
            seen.append(kwargs["city"].name)
            return original(*args, **kwargs)

        monkeypatch.setattr(fast_obs, "running_extremes_for_local_day", _running_extremes)

        emitted = fast_obs.Day0FastObsEmitter().emit_prefetched(
            world_conn=_world_conn(),
            prefetch=prefetch,
            received_at=(t0 + timedelta(minutes=5)).isoformat(),
            limit=20,
        )

        assert emitted == 2
        assert seen == ["Tokyo"]

    def test_committed_event_evaluation_skips_only_ledgered_publications(self):
        import src.data.day0_fast_obs as fast_obs

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        city = _tokyo()
        report = _report("RJTT", t0, 21.0, t_group=False)
        prefetch = fast_obs.FastObsPrefetch(
            eligible=((city, fast_obs.fast_obs_source_for_city(city), "2026-06-10"),),
            reports=(report,),
            freshness_status=fast_obs.FETCH_FRESH,
            cache_age_s=0.0,
            decision_time=t0 + timedelta(minutes=5),
            ledger_reports=(report,),
        )
        emitter = fast_obs.Day0FastObsEmitter()
        conn = _world_conn()
        evaluated: list[tuple[str, str, float]] = []

        assert emitter.emit_prefetched(
            world_conn=conn,
            prefetch=prefetch,
            received_at=(t0 + timedelta(minutes=5)).isoformat(),
            evaluated_report_keys=evaluated,
            persist_ledger=False,
        ) == 2
        assert evaluated
        assert emitter.prefetched_events_evaluated(prefetch) is False

        conn.commit()
        emitter.mark_prefetched_events_evaluated(evaluated)
        assert emitter.prefetched_events_evaluated(prefetch) is True

        assert emitter.persist_prefetched_ledger(
            world_conn=conn,
            prefetch=prefetch,
        ) is True
        assert emitter.prefetched_events_evaluated(prefetch) is False

    def test_emit_prefetched_persists_anomaly_actions_with_world_conn(self, monkeypatch):
        from src.data import day0_oracle_anomaly as oa
        from src.data.day0_fast_obs import Day0FastObsEmitter, FastObsPrefetch
        from src.state import db_writer_lock

        def forbidden_lock(*_args, **_kwargs):
            raise AssertionError("emit-phase anomaly persistence must use world_conn")

        monkeypatch.setattr(db_writer_lock, "db_writer_lock", forbidden_lock)
        conn = _world_conn()
        action = oa.Day0OracleAnomalyAction(
            action="flag",
            city="Tokyo",
            target_date="2026-06-10",
            detail="paris-class",
        )
        prefetch = FastObsPrefetch(
            eligible=(),
            reports=(),
            freshness_status="fresh_fetch",
            cache_age_s=0.0,
            decision_time=datetime(2026, 6, 10, 4, 0, tzinfo=UTC),
            anomaly_actions=(action,),
        )

        emitted = Day0FastObsEmitter().emit_prefetched(
            world_conn=conn,
            prefetch=prefetch,
            received_at="2026-06-10T04:00:00+00:00",
        )

        assert emitted == 0
        oa._reset_registry_for_tests()
        assert oa.is_day0_family_paused(
            "Tokyo",
            "2026-06-10",
            now=datetime(2026, 6, 10, 5, 0, tzinfo=UTC),
            conn=conn,
        ) is True

    def test_prefetch_anomaly_check_returns_action_without_writer_lock(self, monkeypatch):
        from src.data import day0_oracle_anomaly as oa
        from src.data.day0_fast_obs import Day0FastObsEmitter
        from src.state import db_writer_lock

        def forbidden_lock(*_args, **_kwargs):
            raise AssertionError("prefetch-phase anomaly check must not acquire a writer lock")

        def wu_obs(city, target_date=None, **kw):
            return SimpleNamespace(
                source="wu_api",
                coverage_status="OK",
                observation_time="2026-06-09T15:05:00+00:00",
                high_so_far=26.0,
                low_so_far=21.0,
            )

        monkeypatch.setattr(db_writer_lock, "db_writer_lock", forbidden_lock)
        monkeypatch.setattr("src.data.observation_client.get_current_observation", wu_obs)

        t0 = datetime(2026, 6, 9, 15, 0, tzinfo=UTC)  # Jun 10 00:00 JST
        reports = [
            _report("RJTT", t0, 21.0, t_group=False),
            _report("RJTT", t0 + timedelta(minutes=5), 21.0, t_group=False),
        ]
        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: reports, min_fetch_interval_s=0.0)

        prefetch = emitter.prefetch(
            cities=[_tokyo()],
            decision_time=t0 + timedelta(minutes=10),
            anomaly_check=oa.wu_metar_anomaly_check,
        )

        assert len(prefetch.anomaly_actions) == 1
        action = prefetch.anomaly_actions[0]
        assert action.action == "flag"
        assert action.city == "Tokyo"
        assert action.target_date == "2026-06-10"
        assert oa.is_day0_family_paused(
            "Tokyo",
            "2026-06-10",
            now=datetime(2026, 6, 9, 15, 15, tzinfo=UTC),
            conn=sqlite3.connect(":memory:"),
        ) is True

        oa._reset_registry_for_tests()
        assert oa.is_day0_family_paused(
            "Tokyo",
            "2026-06-10",
            now=datetime(2026, 6, 9, 15, 15, tzinfo=UTC),
            conn=sqlite3.connect(":memory:"),
        ) is False, "prefetch must not make restart durability depend on a standalone write"

    def test_reactor_does_not_duplicate_source_clock_metar_fetch(self):
        """The data-ingest source clock exclusively owns fast METAR HTTP.

        Reactor Day0 emission is durable-state catch-up only. Open-Meteo
        hourly-vector refresh remains an independent scheduler job.

        Pin home: the EDLI reactor body moved from src/main.py to
        src/events/reactor.py (R4-b2 slimming + 57c426dc3); the scheduler
        job id stays in src/main.py."""
        source = open("src/events/reactor.py", encoding="utf-8").read()
        main_source = open("src/main.py", encoding="utf-8").read()
        assert "_edli_prefetch_day0_fast_obs" not in source

        start = source.index("def _edli_emit_day0_extreme_events(")
        end = source.index("def _edli_day0_settlement_semantics(")
        emit_body = source[start:end]
        for forbidden in (
            "emit_events(",
            "emit_prefetched(",
            "get_fast_obs_emitter",
            "httpx",
            "maybe_refresh_day0_hourly_vectors",
            ".prefetch(",
        ):
            assert forbidden not in emit_body, f"write phase must not contain {forbidden!r}"

        import inspect

        from src.events import reactor as reactor_module

        hourly_src = inspect.getsource(reactor_module.run_edli_day0_hourly_refresh_cycle)
        assert "maybe_refresh_day0_hourly_vectors" in hourly_src
        assert 'id="edli_day0_hourly_refresh"' in main_source

    def test_hourly_refresh_yields_before_priority_db_reads(self, monkeypatch):
        import src.config as config_module
        from src.events import reactor as reactor_module

        monkeypatch.setattr(
            config_module,
            "settings",
            SimpleNamespace(_data={"edli": {"enabled": True}}),
        )
        monkeypatch.setattr(
            reactor_module,
            "_edli_day0_hourly_priority_families",
            lambda: pytest.fail("active trading lane must defer before DB priority reads"),
        )

        reactor_module.run_edli_day0_hourly_refresh_cycle(
            trading_lane_active=True,
        )

    def test_hourly_refresh_admission_excludes_monitor_and_reactor(self):
        source = open("src/main.py", encoding="utf-8").read()
        hook_start = source.index('@_scheduler_job("edli_day0_hourly_refresh")')
        hook_end = source.index("def _edli_is_sqlite_lock_error", hook_start)
        hook = source[hook_start:hook_end]
        assert "_held_position_monitor_active.is_set()" in hook
        assert "_edli_reactor_active_lock.acquire(blocking=False)" in hook
        assert "_edli_reactor_active_lock.release()" in hook

        schedule_at = source.index('id="edli_day0_hourly_refresh"')
        schedule = source[schedule_at - 500 : schedule_at + 500]
        assert "OPENING_HUNT_FIRST_DELAY_SECONDS + 36.0" in schedule

    def test_live_family_admission_scopes_market_seek_to_runtime_cities(self, monkeypatch):
        from src.events import reactor as reactor_module

        forecasts = sqlite3.connect(":memory:")
        forecasts.execute(
            """
            CREATE TABLE market_events (
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL
            )
            """
        )
        forecasts.execute(
            "CREATE INDEX idx_market_events_city_date_metric "
            "ON market_events(city, target_date, temperature_metric)"
        )
        forecasts.executemany(
            "INSERT INTO market_events VALUES (?, ?, ?)",
            (
                ("Paris", "2026-07-16", "high"),
                ("Paris", "2026-07-16", "high"),
                ("Unconfigured City", "2026-07-16", "high"),
            ),
        )
        trade = sqlite3.connect(":memory:")
        monkeypatch.setattr(reactor_module, "_open_rest_family_rows_for_refresh", lambda _: ())
        monkeypatch.setattr(
            "src.data.replacement_cycle_advance_trigger._held_position_families",
            lambda _: (),
        )
        traced: list[str] = []
        forecasts.set_trace_callback(traced.append)

        admission = reactor_module._edli_day0_live_family_admission(
            forecasts,
            trade,
            decision_time=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
        )

        assert ("paris", "2026-07-16", "high") in admission.admitted_families
        assert admission.scan_cities == frozenset({"Paris"})
        assert all(family[0] != "unconfigured city" for family in admission.admitted_families)
        assert any("city IN (" in statement for statement in traced)

    def test_publication_clock_missing_denies_live_authority(self):
        """PR#404 P2: receiptTime absent -> available_at falls back to the obs
        valid time (never our wall clock) AND live status is blocked."""
        from src.data.day0_fast_obs import (
            fast_obs_source_for_city,
            fast_obs_to_day0_observation,
            running_extremes_for_local_day,
        )
        from src.data.day0_fast_obs import MetarReport

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        report = MetarReport(
            station_id="RJTT", obs_time=t0, receipt_time=None,
            temp_c=21.0, metar_type="METAR", raw="METAR RJTT 21/15",
        )
        city = _tokyo()
        ex = running_extremes_for_local_day([report], city=city, target_date="2026-06-10")
        obs = fast_obs_to_day0_observation(
            city=city, extremes=ex, metric="high", source=fast_obs_source_for_city(city),
        )
        assert obs["observation_available_at"] == obs["observation_time"]
        assert obs["live_authority_status"] == "blocked"


# ===========================================================================
# R21 — anomaly pause persistence + WU-check memo discipline (PR#404 P1)
# ===========================================================================

class TestAnomalyPausePersistence:
    """PR#404 P1: a Paris-CDG-class anomaly is a settlement-authority integrity
    event — the pause must survive a daemon restart, and a WU outage must not
    consume the success-check memo."""

    def _flags_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn

    def test_pause_survives_process_restart(self):
        from src.data import day0_oracle_anomaly as oa

        conn = self._flags_conn()
        oa.flag_day0_oracle_anomaly(
            "Tokyo", "2026-06-10", detail="paris-class",
            now=datetime(2026, 6, 10, 4, 0, tzinfo=UTC), conn=conn,
        )
        assert oa.is_day0_family_paused(
            "Tokyo", "2026-06-10",
            now=datetime(2026, 6, 10, 4, 30, tzinfo=UTC), conn=conn,
        ) is True

        # SIMULATED RESTART: in-process registry wiped; durable flags remain.
        oa._reset_registry_for_tests()
        assert oa.is_day0_family_paused(
            "Tokyo", "2026-06-10",
            now=datetime(2026, 6, 10, 12, 0, tzinfo=UTC), conn=conn,
        ) is True, "pause must be re-hydrated from the durable world-DB flags"

        # TTL is enforced from the DURABLE flagged_at, even post-restart.
        oa._reset_registry_for_tests()
        assert oa.is_day0_family_paused(
            "Tokyo", "2026-06-10",
            now=datetime(2026, 6, 12, 5, 0, tzinfo=UTC), conn=conn,
        ) is False

    def test_clear_removes_durable_flag_too(self):
        from src.data import day0_oracle_anomaly as oa

        conn = self._flags_conn()
        oa.flag_day0_oracle_anomaly("Tokyo", "2026-06-10", detail="t", conn=conn)
        assert oa.clear_day0_oracle_anomaly("Tokyo", "2026-06-10", conn=conn) is True
        oa._reset_registry_for_tests()
        assert oa.is_day0_family_paused("Tokyo", "2026-06-10", conn=conn) is False

    def test_persist_failure_is_loud_but_pause_holds_in_process(self):
        from src.data import day0_oracle_anomaly as oa

        class _BrokenConn:
            def execute(self, *a, **kw):
                raise sqlite3.OperationalError("disk full")

        oa.flag_day0_oracle_anomaly("Tokyo", "2026-06-10", detail="t", conn=_BrokenConn())
        assert oa.is_day0_family_paused("Tokyo", "2026-06-10", conn=_BrokenConn()) is True

    def test_anomaly_flag_uses_blocking_live_writer_lock(self, monkeypatch):
        from contextlib import contextmanager
        from src.data import day0_oracle_anomaly as oa
        from src.state import db as state_db
        from src.state import db_writer_lock

        raw_conn = self._flags_conn()

        class _Conn:
            def execute(self, *args, **kwargs):
                return raw_conn.execute(*args, **kwargs)

            def commit(self):
                return raw_conn.commit()

            def close(self):
                return None

        conn = _Conn()
        calls = []

        @contextmanager
        def _lock(db_path, write_class, *, blocking=True):
            calls.append((db_path, write_class, blocking))
            yield

        monkeypatch.setattr(state_db, "get_world_connection", lambda **_kwargs: conn)
        monkeypatch.setattr(db_writer_lock, "db_writer_lock", _lock)

        oa.flag_day0_oracle_anomaly("Tokyo", "2026-06-10", detail="t")

        assert calls, "production anomaly flag path must acquire the world writer lock"
        assert calls[-1][1] == db_writer_lock.WriteClass.LIVE
        assert calls[-1][2] is True
        rows = raw_conn.execute(
            "SELECT COUNT(*) FROM day0_oracle_anomaly_flags WHERE city='Tokyo'"
        ).fetchone()[0]
        assert rows == 1

    def test_wu_outage_does_not_consume_success_memo(self, monkeypatch):
        """The old code armed the 10-min memo BEFORE calling WU — an outage
        silenced the cross-check for the full window. Now: failure arms only a
        short retry throttle; the next eligible pass retries WU."""
        from src.data import day0_oracle_anomaly as oa

        calls = {"n": 0}

        def failing_wu(city, target_date=None, **kw):
            calls["n"] += 1
            raise RuntimeError("WU outage")

        monkeypatch.setattr(
            "src.data.observation_client.get_current_observation", failing_wu
        )
        city = _tokyo()
        extremes = SimpleNamespace(target_date="2026-06-10")
        oa.wu_metar_anomaly_check(city, extremes, [])
        assert calls["n"] == 1
        # within the FAILURE retry throttle: no call
        oa.wu_metar_anomaly_check(city, extremes, [])
        assert calls["n"] == 1
        # past the failure throttle (rewind the failure memo), well within what
        # the OLD code would have treated as the consumed 10-min success memo:
        import time as _time

        with oa._WU_CHECK_MEMO_LOCK:
            oa._WU_CHECK_FAILURE_MEMO["Tokyo"] = _time.monotonic() - 200.0
        oa.wu_metar_anomaly_check(city, extremes, [])
        assert calls["n"] == 2, "WU must be retried after the short failure throttle"

    def test_inconclusive_metar_window_does_not_consume_success_memo(self, monkeypatch):
        """WU fetch success is not enough to arm the 10-min success memo. If
        the METAR side cannot cover WU's last observation window, the comparison
        is inconclusive and must retry on the short failure throttle."""
        from src.data import day0_oracle_anomaly as oa

        calls = {"n": 0}

        def wu_obs(city, target_date=None, **kw):
            calls["n"] += 1
            return SimpleNamespace(
                observation_time="2026-06-10T12:00:00+00:00",
                high_so_far=26.0,
                low_so_far=21.0,
            )

        monkeypatch.setattr(
            "src.data.observation_client.get_current_observation", wu_obs
        )
        city = _tokyo()
        extremes = SimpleNamespace(target_date="2026-06-10")
        # METAR window is stale relative to WU's 12:00 observation.
        stale_reports = [
            _report("RJTT", datetime(2026, 6, 10, 9, 0, tzinfo=UTC), 21.0, t_group=False),
            _report("RJTT", datetime(2026, 6, 10, 10, 0, tzinfo=UTC), 22.0, t_group=False),
        ]

        oa.wu_metar_anomaly_check(city, extremes, stale_reports)
        assert calls["n"] == 1
        with oa._WU_CHECK_MEMO_LOCK:
            assert "Tokyo" not in oa._WU_CHECK_MEMO
            assert "Tokyo" in oa._WU_CHECK_FAILURE_MEMO

        # Within the short retry throttle: no call.
        oa.wu_metar_anomaly_check(city, extremes, stale_reports)
        assert calls["n"] == 1

        # After short retry throttle: WU is called again; the old implementation
        # would have consumed the 10-min success memo and skipped this.
        import time as _time
        with oa._WU_CHECK_MEMO_LOCK:
            oa._WU_CHECK_FAILURE_MEMO["Tokyo"] = _time.monotonic() - 200.0
        oa.wu_metar_anomaly_check(city, extremes, stale_reports)
        assert calls["n"] == 2


# ===========================================================================
# R24 — PR#404 ROUND-2: split memos, anomaly freshness gates, TTL'd miss cache
# ===========================================================================

class TestSplitMemos:
    """Round-2 P0-1: the kill memo (hard-fact exits) and the live-emission memo
    are SEPARATE state with separate update rules — a stale-withheld kill-memo
    advance must never suppress the later fresh live event."""

    def _flaky_emitter(self, reports):
        from src.data.day0_fast_obs import Day0FastObsEmitter

        plan = {"fail": False, "calls": 0}

        def fetcher(stations, **kw):
            plan["calls"] += 1
            return [] if plan["fail"] else list(reports)

        return Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=0.0), plan

    def test_operator_scenario_stale_withholding_does_not_suppress_fresh_live_emit(self):
        """THE mandated scenario: (1) fresh prefetch fills the cache but the
        write phase never runs; (2) fetch fails, cache aged beyond budget ->
        emit pass updates the KILL memo only (no live event); (3) a later
        FRESH fetch confirms the SAME rounded extreme -> the live event MUST
        still emit (the old coupled memo saw moved=False and never emitted)."""
        import time as _time

        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        emitter, plan = self._flaky_emitter(reports)

        # (1) fresh prefetch fills the cache; write phase intentionally not run
        pf1 = emitter.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=5))
        assert pf1.freshness_status == "fresh_fetch"

        # (2) outage + cache aged beyond Tokyo's 60-min budget -> kill memo only
        plan["fail"] = True
        emitter._cache_fetched_monotonic = _time.monotonic() - 7200.0
        pf2 = emitter.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=10))
        assert pf2.freshness_status == "stale_cache_after_failure"
        n2 = emitter.emit_prefetched(
            world_conn=conn, prefetch=pf2,
            received_at=(t0 + timedelta(minutes=10)).isoformat(), limit=20,
        )
        assert n2 == 0
        assert emitter.latest_rounded_extreme("Tokyo", "2026-06-10", "high") == 21

        # (3) recovery: fresh fetch, SAME rounded extreme -> live event STILL emits
        plan["fail"] = False
        pf3 = emitter.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=15))
        assert pf3.freshness_status == "fresh_fetch"
        n3 = emitter.emit_prefetched(
            world_conn=conn, prefetch=pf3,
            received_at=(t0 + timedelta(minutes=15)).isoformat(), limit=20,
        )
        assert n3 == 2, (
            "fresh confirmation of a kill-memo-only extreme must STILL emit the "
            f"live events (entry/exit lane state divergence) — emitted {n3}"
        )
        rows = conn.execute(
            "SELECT COUNT(*) FROM opportunity_events WHERE event_type='DAY0_EXTREME_UPDATED'"
        ).fetchone()[0]
        assert rows == 2

    def test_only_inserted_live_events_advance_the_live_memo(self):
        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        emitter, _plan = self._flaky_emitter(reports)
        pf = emitter.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=5))
        assert emitter.emit_prefetched(
            world_conn=conn, prefetch=pf, received_at=t0.isoformat(), limit=20,
        ) == 2
        key = ("Tokyo", "2026-06-10", "high")
        assert emitter._last_live_emitted_rounded[key] == 21
        assert emitter._last_kill_memo_rounded[key] == 21
        # unchanged extreme: neither memo moves, nothing emits
        pf2 = emitter.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=8))
        assert emitter.emit_prefetched(
            world_conn=conn, prefetch=pf2, received_at=t0.isoformat(), limit=20,
        ) == 0

    def test_duplicate_live_event_after_restart_advances_live_memo(self):
        """A persisted duplicate is already a live event. After a daemon restart
        the in-process live memo is empty, so the first write attempt may return
        duplicate. That duplicate must advance the live memo, or the daemon will
        retry the same INSERT OR IGNORE forever until the rounded extreme moves."""
        conn = _world_conn()
        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = [_report("RJTT", t0, 21.0, t_group=False)]

        emitter1, _ = self._flaky_emitter(reports)
        pf1 = emitter1.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=5))
        assert emitter1.emit_prefetched(
            world_conn=conn, prefetch=pf1,
            received_at=(t0 + timedelta(minutes=5)).isoformat(), limit=20,
        ) == 2

        # Simulated restart: new emitter has empty in-process memos but the
        # immutable events already exist in the world DB.
        emitter2, _ = self._flaky_emitter(reports)
        pf2 = emitter2.prefetch(cities=[_tokyo()], decision_time=t0 + timedelta(minutes=6))
        assert emitter2.emit_prefetched(
            world_conn=conn, prefetch=pf2,
            received_at=(t0 + timedelta(minutes=6)).isoformat(), limit=20,
        ) == 0
        assert emitter2._last_live_emitted_rounded[("Tokyo", "2026-06-10", "high")] == 21
        assert emitter2._last_live_emitted_rounded[("Tokyo", "2026-06-10", "low")] == 21


class TestAnomalyFreshnessGates:
    """Round-2 P0-2: the WU-vs-METAR detector must never CONCLUDE from a stale
    METAR window — at the prefetch layer (A) and inside the detector (B)."""

    def test_prefetch_skips_anomaly_check_on_stale_cache(self):
        import time as _time

        from src.data.day0_fast_obs import Day0FastObsEmitter

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        plan = {"fail": False}

        def fetcher(stations, **kw):
            return [] if plan["fail"] else list(reports)

        calls = {"n": 0}

        def check(city, extremes, rpts):
            calls["n"] += 1

        emitter = Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=0.0)
        pf = emitter.prefetch(
            cities=[_tokyo()], decision_time=t0 + timedelta(minutes=5), anomaly_check=check,
        )
        assert pf.freshness_status == "fresh_fetch" and calls["n"] == 1

        plan["fail"] = True
        emitter._cache_fetched_monotonic = _time.monotonic() - 600.0
        pf2 = emitter.prefetch(
            cities=[_tokyo()], decision_time=t0 + timedelta(minutes=10), anomaly_check=check,
        )
        assert pf2.freshness_status == "stale_cache_after_failure"
        assert calls["n"] == 1, "a stale METAR cache must not feed the divergence detector"

    def test_prefetch_bounds_anomaly_checks_before_scanning_all_cities(self):
        from src.data.day0_fast_obs import Day0FastObsEmitter

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        tokyo = _tokyo()
        tokyo_b = SimpleNamespace(
            name="Tokyo-B",
            timezone=tokyo.timezone,
            settlement_unit=tokyo.settlement_unit,
            wu_station="RJTT",
            settlement_source_type=tokyo.settlement_source_type,
        )
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        calls: list[str] = []

        def check(city, extremes, rpts):
            calls.append(city.name)

        emitter = Day0FastObsEmitter(fetcher=lambda stations, **kw: reports, min_fetch_interval_s=0.0)
        prefetch = emitter.prefetch(
            cities=[tokyo, tokyo_b],
            decision_time=t0 + timedelta(minutes=5),
            anomaly_check=check,
            anomaly_check_budget_s=60.0,
            anomaly_check_max_cities=1,
        )

        assert prefetch.freshness_status == "fresh_fetch"
        assert calls == ["Tokyo"]

    def test_cached_anomaly_checks_rotate_without_another_metar_fetch(self):
        from src.data.day0_fast_obs import Day0FastObsEmitter

        t0 = datetime(2026, 6, 9, 16, 0, tzinfo=UTC)
        tokyo = _tokyo()
        tokyo_b = SimpleNamespace(
            name="Tokyo-B",
            timezone=tokyo.timezone,
            settlement_unit=tokyo.settlement_unit,
            wu_station="RJTT",
            settlement_source_type=tokyo.settlement_source_type,
        )
        reports = [_report("RJTT", t0, 21.0, t_group=False)]
        fetches = {"n": 0}

        def fetcher(stations, **kw):
            fetches["n"] += 1
            return reports

        checked: list[str] = []
        emitter = Day0FastObsEmitter(fetcher=fetcher, min_fetch_interval_s=0.0)
        emitter.prefetch(cities=[tokyo, tokyo_b], decision_time=t0)

        for offset in (5, 10):
            emitter.cached_anomaly_actions(
                cities=[tokyo, tokyo_b],
                decision_time=t0 + timedelta(seconds=offset),
                anomaly_check=lambda city, *_args: checked.append(city.name),
                max_cities=1,
            )

        assert fetches["n"] == 1
        assert checked == ["Tokyo", "Tokyo-B"]

    def test_ledger_projection_cold_load_then_primary_key_delta(self):
        from src.data.day0_fast_obs import (
            FAST_OBS_SOURCE_ID,
            Day0FastObsEmitter,
        )
        from src.state.schema.observation_prints_schema import (
            append_print,
            ensure_table,
        )

        conn = sqlite3.connect(":memory:")
        ensure_table(conn)
        first = datetime(2026, 6, 9, 15, 0, tzinfo=UTC)
        append_print(
            conn,
            city="Tokyo",
            station_id="RJTT",
            source_channel=FAST_OBS_SOURCE_ID,
            publish_ts_utc=first.isoformat(),
            value_native=21.0,
            unit="C",
            fetched_at_utc=first.isoformat(),
            raw_report="METAR RJTT 091500Z T0210",
        )
        conn.commit()

        emitter = Day0FastObsEmitter(fetcher=lambda *_args, **_kw: [])
        assert emitter.sync_from_ledger(
            conn,
            [_tokyo()],
            as_of=first + timedelta(minutes=1),
        ) == 1

        second = first + timedelta(minutes=5)
        append_print(
            conn,
            city="Tokyo",
            station_id="RJTT",
            source_channel=FAST_OBS_SOURCE_ID,
            publish_ts_utc=second.isoformat(),
            value_native=22.0,
            unit="C",
            fetched_at_utc=second.isoformat(),
            raw_report="METAR RJTT 091505Z T0220",
        )
        conn.commit()
        traced: list[str] = []
        conn.set_trace_callback(traced.append)
        assert emitter.sync_from_ledger(
            conn,
            [_tokyo()],
            as_of=second + timedelta(minutes=1),
        ) == 1
        conn.set_trace_callback(None)

        assert any("WHERE id >" in sql for sql in traced)
        extremes = emitter.latest_extremes(
            _tokyo(),
            "2026-06-10",
            as_of=second + timedelta(minutes=1),
        )
        assert extremes is not None
        assert extremes.high_so_far == pytest.approx(22.0)
        assert extremes.sample_count == 2

    def test_ledger_identity_seed_removes_cold_fetch_history_from_write_delta(self):
        from src.data.day0_fast_obs import FAST_OBS_SOURCE_ID, Day0FastObsEmitter
        from src.state.schema.observation_prints_schema import append_print, ensure_table

        conn = sqlite3.connect(":memory:")
        ensure_table(conn)
        observed = datetime(2026, 6, 9, 15, 0, tzinfo=UTC)
        report = _report("RJTT", observed, 21.0)
        append_print(
            conn,
            city="Tokyo",
            station_id="RJTT",
            source_channel=FAST_OBS_SOURCE_ID,
            publish_ts_utc=report.receipt_time.isoformat(),
            value_native=21.0,
            unit="C",
            fetched_at_utc=report.receipt_time.isoformat(),
            raw_report=report.raw,
        )
        conn.commit()
        emitter = Day0FastObsEmitter(
            fetcher=lambda *_args, **_kwargs: [report],
            min_fetch_interval_s=0.0,
        )

        assert emitter.sync_ledger_report_keys(
            conn,
            [_tokyo()],
            as_of=report.receipt_time + timedelta(minutes=1),
        ) == 1
        assert emitter.ledger_report_keys_loaded()
        prefetch = emitter.prefetch(
            cities=[_tokyo()],
            decision_time=report.receipt_time + timedelta(minutes=1),
        )

        assert prefetch.reports == (report,)
        assert prefetch.ledger_reports == ()

        traced: list[str] = []
        conn.set_trace_callback(traced.append)
        assert emitter.sync_ledger_report_keys(conn, [_tokyo()]) == 0
        conn.set_trace_callback(None)
        assert not traced

    def test_detector_refuses_conclusion_when_metar_window_lags_wu(self):
        """Operator scenario: METAR outage since 10:00, WU moved at 12:00 —
        comparing a 2h-stale METAR window vs current WU is NOT divergence."""
        from src.data import day0_oracle_anomaly as oa

        # METAR reports through 10:00 UTC only
        reports = [
            _report("RJTT", datetime(2026, 6, 10, 9, 0, tzinfo=UTC), 21.0, t_group=False),
            _report("RJTT", datetime(2026, 6, 10, 10, 0, tzinfo=UTC), 22.0, t_group=False),
        ]
        verdict = oa.check_wu_metar_divergence(
            city=_tokyo(), target_date="2026-06-10", metar_reports=reports,
            wu_high_so_far=26.0,  # WU moved 4C since the METAR outage began
            wu_low_so_far=21.0,
            wu_last_obs_time=datetime(2026, 6, 10, 12, 0, tzinfo=UTC),
        )
        assert verdict.compared is False and verdict.diverged is False
        assert "metar_side_stale_for_wu_window" in verdict.detail
        assert oa.is_day0_family_paused("Tokyo", "2026-06-10",
                                        conn=sqlite3.connect(":memory:")) is False

    def test_detector_still_fires_on_real_mismatch_with_coverage(self):
        from src.data import day0_oracle_anomaly as oa

        reports = [
            _report("RJTT", datetime(2026, 6, 10, 11, 30, tzinfo=UTC), 22.0, t_group=False),
            _report("RJTT", datetime(2026, 6, 10, 12, 0, tzinfo=UTC), 22.0, t_group=False),
        ]
        verdict = oa.check_wu_metar_divergence(
            city=_tokyo(), target_date="2026-06-10", metar_reports=reports,
            wu_high_so_far=26.0,  # real same-window mismatch (4C > threshold)
            wu_low_so_far=21.0,
            wu_last_obs_time=datetime(2026, 6, 10, 12, 0, tzinfo=UTC),
        )
        assert verdict.compared is True and verdict.diverged is True

    def test_coverage_within_tolerance_still_compares(self):
        from src.data import day0_oracle_anomaly as oa

        reports = [
            _report("RJTT", datetime(2026, 6, 10, 11, 57, tzinfo=UTC), 22.0, t_group=False),
        ]
        verdict = oa.check_wu_metar_divergence(
            city=_tokyo(), target_date="2026-06-10", metar_reports=reports,
            wu_high_so_far=22.0, wu_low_so_far=22.0,
            wu_last_obs_time=datetime(2026, 6, 10, 12, 0, tzinfo=UTC),  # 3 min ahead
        )
        assert verdict.compared is True and verdict.diverged is False


class TestTtlMissCacheAndPersistedTtl:
    """Round-2 P1-A: the negative-miss cache is TTL'd (cross-process flags
    become visible without restart) and the persisted TTL is the authority."""

    def test_flag_from_another_process_visible_after_miss_cache_ttl(self, monkeypatch):
        from src.data import day0_oracle_anomaly as oa

        conn = sqlite3.connect(":memory:")
        # process A reads -> negative miss cached
        assert oa.is_day0_family_paused("Tokyo", "2026-06-10", conn=conn) is False
        # external process/operator writes the durable flag directly
        conn.execute(oa._FLAGS_TABLE_DDL)
        conn.execute(
            "INSERT OR REPLACE INTO day0_oracle_anomaly_flags VALUES (?,?,?,?,?)",
            ("Tokyo", "2026-06-10", datetime(2026, 6, 10, 4, 0, tzinfo=UTC).isoformat(),
             24.0, "external"),
        )
        conn.commit()
        # within the miss-cache TTL the stale negative may persist…
        # …but once the TTL lapses the flag MUST become visible (no restart).
        monkeypatch.setattr(oa, "_DB_MISS_TTL_S", 0.0)
        assert oa.is_day0_family_paused(
            "Tokyo", "2026-06-10",
            now=datetime(2026, 6, 10, 12, 0, tzinfo=UTC), conn=conn,
        ) is True

    def test_persisted_custom_ttl_survives_restart_and_governs_expiry(self):
        from src.data import day0_oracle_anomaly as oa

        conn = sqlite3.connect(":memory:")
        oa.flag_day0_oracle_anomaly(
            "Tokyo", "2026-06-10", detail="short-lived",
            now=datetime(2026, 6, 10, 4, 0, tzinfo=UTC),
            ttl_hours=2.0, conn=conn,
        )
        oa._reset_registry_for_tests()  # simulated restart
        # +1h: paused (within the persisted 2h TTL)
        assert oa.is_day0_family_paused(
            "Tokyo", "2026-06-10", now=datetime(2026, 6, 10, 5, 0, tzinfo=UTC), conn=conn,
        ) is True
        oa._reset_registry_for_tests()
        # +3h: the PERSISTED 2h TTL governs — NOT the 24h call-site default
        assert oa.is_day0_family_paused(
            "Tokyo", "2026-06-10", now=datetime(2026, 6, 10, 7, 0, tzinfo=UTC), conn=conn,
        ) is False
        # expired durable row was best-effort deleted (no restart re-hydration)
        rows = conn.execute("SELECT COUNT(*) FROM day0_oracle_anomaly_flags").fetchone()[0]
        assert rows == 0
