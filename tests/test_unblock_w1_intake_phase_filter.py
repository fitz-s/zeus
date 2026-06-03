# Created: 2026-06-03
# Last reused/audited: 2026-06-03
# Authority basis: docs/operations task WAVE-1 (unblock-W1) W1-T1 — shared
#   phase-admissibility predicate applied at FSR intake; reactor keeps the
#   identical predicate as a fail-closed backstop. Encodes the
#   EVENT_BOUND_MARKET_PHASE_CLOSED logic already at
#   src/engine/event_reactor_adapter.py (_edli_forecast_only_phase_evidence +
#   _forecast_only_phase_admits → _FORECAST_ONLY_ADMIT_PHASES = {PRE_SETTLEMENT_DAY}).
"""W1-T1 RED relationship tests: intake phase-filter ≡ reactor predicate.

Two relationships, both RED today:

RT-1 (behavioral): under ``edli_intake_phase_filter_enabled`` ON, a
phase-closed family (decision_time on/after the city-local target day →
SETTLEMENT_DAY/POST_TRADING) emits ZERO FORECAST_SNAPSHOT_READY events at
intake, while an OPEN sibling (decision_time well before the local target
day → PRE_SETTLEMENT_DAY) still emits within the budget. RED today because
the FSR intake path has NO phase filter — both families emit.

RT-2 (identity): ``market_phase_admits(...)`` (the shared pure predicate)
returns the SAME admit/reject verdict the reactor applies at bind time
(``_forecast_only_phase_admits(_edli_forecast_only_phase_evidence(...))``)
for a matrix of (phase) inputs. RED today because ``market_phase_admits``
does not exist (separate/no intake predicate).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

UTC = timezone.utc

# A city with a known IANA timezone present in the runtime city registry so
# both the intake predicate and the reactor predicate can resolve tz. London
# is UTC+1 in summer; any registry city with a tz works.
_TZ = "Europe/London"


def _decision(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UTC)


def _london_tz_city_name():
    import src.config as _config

    return next(
        (
            c.name
            for c in _config.runtime_cities_by_name().values()
            if getattr(c, "timezone", None) == _TZ
        ),
        None,
    )


# --------------------------------------------------------------------------- #
# RT-2 — identity between the intake predicate and the reactor predicate.
# --------------------------------------------------------------------------- #
class TestRT2PredicateIdentity:
    """market_phase_admits == reactor's _forecast_only_phase_admits verdict."""

    @pytest.mark.parametrize(
        "decision_iso,target_date,expect_admit",
        [
            # decision well before local target day → PRE_SETTLEMENT_DAY → admit
            ("2026-06-10T06:00:00Z", "2026-06-15", True),
            # decision on the local target day → SETTLEMENT_DAY → reject
            ("2026-06-15T06:00:00Z", "2026-06-15", False),
            # decision after F1 12:00 UTC end-of-target → POST_TRADING → reject
            ("2026-06-15T18:00:00Z", "2026-06-15", False),
            # decision the local day before still PRE_SETTLEMENT_DAY (pre local-midnight)
            ("2026-06-14T06:00:00Z", "2026-06-15", True),
        ],
    )
    def test_intake_predicate_matches_reactor(
        self, decision_iso: str, target_date: str, expect_admit: bool
    ) -> None:
        from src.strategy.market_phase import market_phase_admits
        from src.engine.event_reactor_adapter import (
            _edli_forecast_only_phase_evidence,
            _forecast_only_phase_admits,
        )

        city_name = _london_tz_city_name()
        if city_name is None:  # pragma: no cover - registry guarantees a London-tz city
            pytest.skip("no runtime city with Europe/London tz")

        decision_time = _decision(decision_iso)
        # Empty market_row → both predicates fall back to the F1 12:00-UTC
        # end-of-target anchor (identical inputs → identical verdict).
        market_row: dict = {}

        intake_verdict = market_phase_admits(
            city=city_name,
            target_date=target_date,
            metric="high",
            decision_time=decision_time,
            market_row=market_row,
        )

        reactor_evidence = _edli_forecast_only_phase_evidence(
            city=city_name,
            target_date=target_date,
            decision_time=decision_time,
            selected_market_row=market_row,
        )
        reactor_verdict = _forecast_only_phase_admits(reactor_evidence)

        assert intake_verdict == reactor_verdict, (
            f"intake predicate ({intake_verdict}) diverged from reactor "
            f"predicate ({reactor_verdict}) for decision={decision_iso} "
            f"target={target_date}; they MUST be identical."
        )
        assert intake_verdict is expect_admit, (
            f"expected admit={expect_admit} for decision={decision_iso} "
            f"target={target_date}, got {intake_verdict}"
        )

    def test_missing_timezone_rejects_fail_closed(self) -> None:
        """A city with no resolvable timezone → reject (fail-closed)."""
        from src.strategy.market_phase import market_phase_admits

        verdict = market_phase_admits(
            city="__city_with_no_timezone__",
            target_date="2026-06-15",
            metric="high",
            decision_time=_decision("2026-06-10T06:00:00Z"),
            market_row={},
        )
        assert verdict is False, "unknown-timezone family must fail-closed (reject)"


# --------------------------------------------------------------------------- #
# RT-1 — behavioral: phase-closed family emits 0 FSR; open sibling emits.
# --------------------------------------------------------------------------- #
class TestRT1IntakeEmissionFilter:
    """Under the intake filter flag ON, a closed family emits 0 FSR while an
    open sibling still emits. Mirrors the canonical scaffold in
    tests/events/test_forecast_snapshot_ready.py (in-memory init_schema_forecasts
    + the full source_run/coverage/ensemble_snapshots/market_events insert shape)
    so the only behavioral lever under test is the phase filter."""

    def _build(self, *, city: str, city_tz: str, target_date: str):
        """Build an in-memory forecasts DB with one LIVE_ELIGIBLE COMPLETE
        family + a market_events row for (city, target_date, high). Returns
        (trigger, forecasts_conn)."""
        import sqlite3
        from src.state.db import init_schema, init_schema_forecasts
        from src.events.triggers.forecast_snapshot_ready import (
            ForecastSnapshotReadyTrigger,
            executable_forecast_live_eligible_reader,
        )
        from src.events.event_writer import EventWriter

        fconn = sqlite3.connect(":memory:")
        fconn.row_factory = sqlite3.Row
        init_schema_forecasts(fconn)
        fconn.execute(
            """
            INSERT INTO source_run (
                source_run_id, source_id, track, release_calendar_key, ingest_mode, origin_mode,
                source_cycle_time, source_available_at, captured_at, target_local_date,
                city_id, city_timezone, temperature_metric, dataset_id,
                expected_members, observed_members, expected_steps_json, observed_steps_json,
                completeness_status, status
            ) VALUES (
                'run-1', 'ecmwf-open-data', 'ens', '2026-06-01T00', 'SCHEDULED_LIVE', 'SCHEDULED_LIVE',
                '2026-06-01T00:00:00+00:00', '2026-06-01T04:15:00+00:00', '2026-06-01T04:16:00+00:00',
                ?, ?, ?, 'high', 'v1',
                51, 51, '[0,3,6]', '[0,3,6]', 'COMPLETE', 'SUCCESS'
            )
            """,
            (target_date, city.lower(), city_tz),
        )
        fconn.execute(
            """
            INSERT INTO source_run_coverage (
                coverage_id, source_run_id, source_id, source_transport, release_calendar_key, track,
                city_id, city, city_timezone, target_local_date, temperature_metric, physical_quantity,
                observation_field, data_version, expected_members, observed_members, expected_steps_json,
                observed_steps_json, snapshot_ids_json, target_window_start_utc, target_window_end_utc,
                completeness_status, readiness_status, computed_at, expires_at
            ) VALUES (
                'cov-1', 'run-1', 'ecmwf-open-data', 'ensemble_snapshots_db_reader', '2026-06-01T00', 'ens',
                ?, ?, ?, ?, 'high', 'temperature',
                'high_temp', 'v1', 51, 51, '[0,3,6]', '[0,3,6]', '[1]',
                '2026-06-01T05:00:00+00:00', '2026-06-02T05:00:00+00:00',
                'COMPLETE', 'LIVE_ELIGIBLE', '2026-06-01T04:16:00+00:00', '2026-06-30T04:16:00+00:00'
            )
            """,
            (city.lower(), city, city_tz, target_date),
        )
        fconn.execute(
            """
            INSERT INTO ensemble_snapshots (
                snapshot_id, city, target_date, temperature_metric, physical_quantity, observation_field,
                issue_time, valid_time, available_at, fetch_time, lead_hours, members_json,
                model_version, dataset_id, source_id, source_transport, source_run_id,
                release_calendar_key, source_cycle_time, source_release_time, source_available_at,
                authority, causality_status, boundary_ambiguous, contributes_to_target_extrema,
                forecast_window_attribution_status, local_day_start_utc, step_horizon_hours,
                members_unit, raw_orderbook_hash_transition_delta_ms
            ) VALUES (
                1, ?, ?, 'high', 'temperature', 'high_temp',
                '2026-06-01T00:00:00+00:00', '2026-06-01T06:00:00+00:00',
                '2026-06-01T04:15:00+00:00', '2026-06-01T04:16:00+00:00', 6,
                '[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51]',
                'ecmwf', 'v1', 'ecmwf-open-data', 'ensemble_snapshots_db_reader', 'run-1',
                '2026-06-01T00', '2026-06-01T00:00:00+00:00', '2026-06-01T03:00:00+00:00',
                '2026-06-01T04:15:00+00:00', 'VERIFIED', 'OK', 0, 1,
                'FULLY_INSIDE_TARGET_LOCAL_DAY', '2026-06-01T05:00:00+00:00', 6, 'C', 0
            )
            """,
            (city, target_date),
        )
        fconn.execute(
            "INSERT INTO market_events (market_slug, city, target_date, temperature_metric) VALUES (?, ?, ?, 'high')",
            (f"slug-{target_date}", city, target_date),
        )
        fconn.commit()

        world_conn = sqlite3.connect(":memory:")
        init_schema(world_conn)
        trigger = ForecastSnapshotReadyTrigger(
            EventWriter(world_conn),
            live_eligibility_reader=executable_forecast_live_eligible_reader(fconn),
        )
        return trigger, fconn

    def test_closed_family_emits_zero_open_emits(self, monkeypatch):
        """Flag ON: a SETTLEMENT_DAY (closed) family emits 0 FSR; an open
        PRE_SETTLEMENT_DAY sibling emits ≥1 within budget."""
        import src.config as _config
        from src.config import settings as _settings

        city = _london_tz_city_name()
        if city is None:  # pragma: no cover
            pytest.skip("no runtime city with Europe/London tz")
        city_tz = _TZ
        target_date = "2026-06-15"

        # Turn the intake phase filter ON (default OFF in code). settings["edli_v1"]
        # is the live dict the trigger reads via .get(...); setitem auto-restores.
        monkeypatch.setitem(
            _settings["edli_v1"], "edli_intake_phase_filter_enabled", True
        )

        # CLOSED: decision on the local target day → SETTLEMENT_DAY → reject.
        dt_closed = _decision("2026-06-15T06:00:00Z")
        trig_c, fconn_c = self._build(city=city, city_tz=city_tz, target_date=target_date)
        results_c = trig_c.scan_committed_snapshots(
            forecasts_conn=fconn_c, decision_time=dt_closed,
            received_at=dt_closed.isoformat(), limit=100,
        )
        emitted_c = [r for r in results_c if getattr(r, "inserted", False)]
        assert len(emitted_c) == 0, (
            f"phase-CLOSED family emitted {len(emitted_c)} FSR under filter ON; "
            "expected ZERO."
        )

        # OPEN: decision well before the local target day → PRE_SETTLEMENT_DAY → admit.
        dt_open = _decision("2026-06-10T06:00:00Z")
        trig_o, fconn_o = self._build(city=city, city_tz=city_tz, target_date=target_date)
        results_o = trig_o.scan_committed_snapshots(
            forecasts_conn=fconn_o, decision_time=dt_open,
            received_at=dt_open.isoformat(), limit=100,
        )
        emitted_o = [r for r in results_o if getattr(r, "inserted", False)]
        assert len(emitted_o) >= 1, (
            "phase-OPEN sibling emitted 0 FSR under filter ON; expected ≥1."
        )
