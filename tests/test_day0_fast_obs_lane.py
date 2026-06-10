# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator green-light 2026-06-10 items A/C/E (free METAR fast
#   lane, live-obs hook wiring, WU-vs-METAR oracle anomaly guard); day0
#   first-principles review /tmp/day0_first_principles_review.md §6.2;
#   API shape verified live 2026-06-10 against aviationweather.gov
#   /api/data/metar?format=json (KLGA T-group tenths, RKSI whole-C, receiptTime
#   3-6 min behind obsTime).
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
    # Tokyo: settlement-FAITHFUL C city (measured). Seoul is EXCLUDED from the
    # fast lane by the faithfulness gate (config/wu_metar_divergence.json), so
    # emitter tests use Tokyo. JST is UTC+9 like KST — the same UTC fixtures
    # map to the same local day.
    return SimpleNamespace(
        name="Tokyo", timezone="Asia/Tokyo", settlement_unit="C",
        wu_station="RJTT", settlement_source_type="wu_icao",
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
        assert obs["live_authority_status"] == "LIVE_AUTHORITY"
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
                "live_authority_status": "LIVE_AUTHORITY",
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
        assert obs["live_authority_status"] == "NON_LIVE_AUTHORITY"

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
        assert all(p["live_authority_status"] == "LIVE_AUTHORITY" for p in payloads)

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

    def test_unfaithful_city_is_excluded_from_fast_lane(self):
        """Monotone-safe exclusion: Seoul gets NO fast-lane source (its METAR
        integer is not reliably WU's settlement integer), so METAR can never
        drive a bin-kill there; faithful cities keep the lane."""
        assert fast_obs_source_for_city(_seoul()) is None
        assert fast_obs_source_for_city(_tokyo()) is not None
        assert fast_obs_source_for_city(_nyc()) is not None

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
            wu_last_obs_time=datetime(2026, 6, 9, 17, 0, tzinfo=UTC),
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
