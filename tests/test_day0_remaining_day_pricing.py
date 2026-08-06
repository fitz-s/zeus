# Created: 2026-06-10
# Last reused or audited: 2026-08-06
# Lifecycle: created=2026-06-10; last_reviewed=2026-08-06; last_reused=2026-08-06
# Purpose: Protect causal Day0 remaining-window probability construction.
# Reuse: Run before changing Day0 hourly members, state diagnostics, or bootstrap pricing.
# Authority basis: operator green-light 2026-06-10 item B (remaining-day
#   pricing + persist-the-hourly-vector); day0 first-principles review §2.4
#   (full-day-masked q DEVIATES: overprices excursion bins post-peak) and
#   §6.1/§6.3 spec. Payload shape verified live against
#   api.open-meteo.com/v1/forecast (multi-model suffixed hourly keys).
"""Relationship tests for the day0 hourly-vector lane + remaining-day members.

Contracts:
  R9.  PERSISTENCE: hourly vectors round-trip (degC storage law), idempotent
       on (model, city, date, captured_at), retention prunes old rows, stale
       vectors (> max_age) are NOT served to the q path. When remaining-day
       mode is required by live Day0, unavailable vectors block the q seam.
  R10. REMAINING-DAY SELECTION: target-day hours not yet covered by the latest
       causal observation contribute; the just-elapsed hourly point may anchor
       its terminal sub-hour for at most one hour. A decision after local
       midnight keeps only an observation-uncovered tail, never the whole day.
  R11. POST-PEAK REPRICING: with all remaining-hours temps at/below the
       running max, the pooled members clamp to the floor — the floor bin
      gets ~all q mass and bins above get ~none (the exact category the
      full-day-masked q got wrong). Flag default OFF; flag OFF leaves the
      legacy path untouched; flag ON must not fall back to it.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from src.contracts.execution_price import ExecutionPrice as EP
from src.data.day0_hourly_vectors import (
    Day0HourlyVector,
    parse_openmeteo_hourly_payload,
    persist_day0_hourly_vectors,
    read_freshest_day0_hourly_vectors,
    remaining_day_extremes_c,
    select_ready_day0_hourly_vectors,
)
from src.types.market import Bin

UTC = timezone.utc

# Pin the retention-prune clock so this suite is HERMETIC. The persisted-vector
# fixtures use fixed captured_at timestamps on the 2026-06-10 target day; the
# prune cutoff is `now - retention_days`. Without a pinned `now`, the prune uses
# live wall-clock time, so once real time advances >3 days past 2026-06-10 every
# just-inserted fixture row is pruned immediately and the persistence/freshness
# assertions fail spuriously (the test is non-hermetic, not a code bug). Pinning
# `now` to the target day reproduces the intended same-day-write semantics; the
# retention test still pins a target-day `now` so its 9-day-old "ancient" row is
# correctly pruned and the fresh row is kept.
PRUNE_NOW = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)


def _paris():
    return SimpleNamespace(
        name="Paris", timezone="Europe/Paris", settlement_unit="C",
        settlement_source_type="wu_icao", wu_station="LFPG",
        lat=48.8566, lon=2.3522,
    )


def _hong_kong():
    return SimpleNamespace(
        name="Hong Kong",
        timezone="Asia/Hong_Kong",
        settlement_unit="C",
        settlement_source_type="hko",
        wu_station=None,
        lat=22.3022,
        lon=114.1742,
    )


def _wellington():
    return SimpleNamespace(
        name="Wellington", timezone="Pacific/Auckland", settlement_unit="C",
        lat=-41.2865, lon=174.7762,
    )


def _vector(model="icon_d2", captured_at=None, temps=None, start_hour=0):
    times = [f"2026-06-10T{h:02d}:00" for h in range(start_hour, 24)]
    temps = temps if temps is not None else [15.0 + 0.5 * h for h in range(start_hour, 24)]
    return Day0HourlyVector(
        model=model, city="Paris", target_date="2026-06-10",
        timezone_name="Europe/Paris",
        captured_at=(captured_at or datetime(2026, 6, 10, 9, 0, tzinfo=UTC)).isoformat(),
        times=tuple(times), temps_c=tuple(temps[: len(times)]),
    )


def _refresh_vector(city, model: str, decision_time: datetime) -> Day0HourlyVector:
    """Two complete local days, matching the production forecast_days=2 shape."""
    local_day = decision_time.astimezone(ZoneInfo(city.timezone)).date()
    times = tuple(
        f"{(local_day + timedelta(days=offset)).isoformat()}T{hour:02d}:00"
        for offset in (0, 1)
        for hour in range(24)
    )
    return Day0HourlyVector(
        model=model,
        city=city.name,
        target_date=local_day.isoformat(),
        timezone_name=city.timezone,
        captured_at=decision_time.isoformat(),
        times=times,
        temps_c=tuple(15.0 + 0.1 * index for index in range(len(times))),
    )


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_monitor_forecast_source_validations_include_hourly_bundle_provenance():
    """Monitor receipts must expose the complete Day0 hourly source bundle."""
    from src.engine import monitor_refresh

    validations = monitor_refresh._monitor_forecast_source_validations(
        {
            "source_id": "day0_hourly_vectors",
            "forecast_source_role": "day0_remaining_window_live",
            "source_models": ["icon_d2", "ecmwf_ifs"],
            "expected_models": ["icon_d2", "ecmwf_ifs"],
            "source_model_count": 2,
            "fetch_time": "2026-06-30T12:12:12+00:00",
        }
    )

    assert "forecast_source_id:day0_hourly_vectors" in validations
    assert "forecast_source_role:day0_remaining_window_live" in validations
    assert "forecast_source_models:icon_d2,ecmwf_ifs" in validations
    assert "forecast_expected_models:icon_d2,ecmwf_ifs" in validations
    assert "forecast_source_model_count:2" in validations
    assert "forecast_fetch_time:2026-06-30T12:12:12+00:00" in validations


def test_day0_high_signal_default_mc_stream_is_stable_for_same_support():
    """Repeated monitor refreshes with the same Day0 support must not resample seed noise."""
    from src.signal.day0_signal import Day0Signal
    from src.types.metric_identity import HIGH_LOCALDAY_MAX

    bins = [
        Bin(low=35, high=35, label="35C", unit="C"),
        Bin(low=36, high=36, label="36C", unit="C"),
        Bin(low=37, high=37, label="37C", unit="C"),
    ]
    signal = Day0Signal(
        observed_high_so_far=35.0,
        current_temp=34.0,
        hours_remaining=11.0,
        member_maxes_remaining=np.array([36.0]),
        unit="C",
        precision=1.0,
        temperature_metric=HIGH_LOCALDAY_MAX,
    )

    first = signal.p_vector(bins, n_mc=500)
    second = signal.p_vector(bins, n_mc=500)

    assert np.array_equal(first, second)


def test_day0_high_signal_seed_ignores_nonphysical_support_order_and_labels():
    """Equivalent Day0 support must not change MC stream because of ordering or display text."""
    from src.signal.day0_signal import Day0Signal
    from src.types.metric_identity import HIGH_LOCALDAY_MAX

    bins_a = [
        Bin(low=35, high=35, label="35C", unit="C"),
        Bin(low=36, high=36, label="36C", unit="C"),
        Bin(low=37, high=37, label="37C", unit="C"),
    ]
    bins_b = [
        Bin(low=35, high=35, label="Will high be 35C?", unit="C"),
        Bin(low=36, high=36, label="Will high be 36C?", unit="C"),
        Bin(low=37, high=37, label="Will high be 37C?", unit="C"),
    ]
    common = dict(
        observed_high_so_far=35.0,
        current_temp=34.0,
        hours_remaining=11.0,
        unit="C",
        precision=1.0,
        temperature_metric=HIGH_LOCALDAY_MAX,
    )
    signal_a = Day0Signal(
        member_maxes_remaining=np.array([36.0, 35.0, 37.0]),
        **common,
    )
    signal_b = Day0Signal(
        member_maxes_remaining=np.array([37.0, 36.0, 35.0]),
        **common,
    )

    assert np.array_equal(signal_a.p_vector(bins_a, n_mc=500), signal_b.p_vector(bins_b, n_mc=500))


def test_day0_high_signal_seed_is_prefix_stable_when_mc_count_changes():
    """Changing n_mc changes sample count, not the underlying common random stream seed."""
    from src.signal.day0_signal import _stable_day0_rng_seed

    bins = [
        Bin(low=35, high=35, label="35C", unit="C"),
        Bin(low=36, high=36, label="36C", unit="C"),
    ]

    assert _stable_day0_rng_seed(
        bins=bins,
        member_values=np.array([36.0, 35.0]),
        unit="C",
        precision=1.0,
    ) == _stable_day0_rng_seed(
        bins=bins,
        member_values=np.array([35.0, 36.0]),
        unit="C",
        precision=1.0,
    )


def test_day0_analysis_probability_content_is_stable_across_recapture_order(
    monkeypatch,
):
    """Production point-q and samples depend on content, not recapture metadata."""
    import src.engine.event_reactor_adapter as era

    family = SimpleNamespace(
        city="Paris",
        metric="high",
        target_date="2026-07-27",
        event_type="DAY0_EXTREME_UPDATED",
        family_id="Paris|2026-07-27|high",
    )
    content = {
        "metric": "high",
        "rounded_value": 30,
        "observation_time": "2026-07-27T01:00:00+00:00",
        "_edli_day0_remaining_model_names": [
            "ecmwf_ifs",
            "icon_global",
            "ukmo_global_deterministic_10km",
        ],
    }
    first = {
        **content,
        "_edli_day0_remaining_capture_times_utc": [
            "2026-07-27T01:50:06+00:00"
        ],
    }
    recaptured = {
        **content,
        "_edli_day0_remaining_capture_times_utc": [
            "2026-07-27T02:01:43+00:00"
        ],
    }
    members = np.array([32.1, 31.8, 31.6])
    changed_members = np.array([33.1, 31.8, 31.6])

    bins = [
        Bin(low=None, high=31, label="31C or below", unit="C"),
        Bin(low=32, high=32, label="32C", unit="C"),
        Bin(low=33, high=None, label="33C or above", unit="C"),
    ]
    family.bins = bins
    family.candidates = [
        SimpleNamespace(
            condition_id=f"condition-{index}",
            bin=bin_value,
            yes_token_id=f"yes-{index}",
            no_token_id=f"no-{index}",
        )
        for index, bin_value in enumerate(bins)
    ]
    native_costs = {
        (f"condition-{index}", side): (
            None,
            EP(price, "ask", fee_deducted=True, currency="probability_units"),
            price,
            None,
            None,
        )
        for index in range(len(bins))
        for side, price in (("buy_yes", 0.5), ("buy_no", 0.5))
    }
    snapshot = {
        "settlement_unit": "C",
        "temperature_metric": "high",
        "members_json": "[31.6, 31.8, 32.1]",
        "members_precision": 1.0,
        "source_id": "test",
        "issue_time": "2026-07-27T00:00:00+00:00",
        "dataset_id": "test_v1",
        "data_version": "test_v1",
    }
    served = {"members": members}
    monkeypatch.setattr(era, "_day0_remaining_day_q_enabled", lambda: True)
    monkeypatch.setattr(
        era,
        "_day0_remaining_day_members",
        lambda **_kwargs: np.asarray(served["members"], dtype=float),
    )

    def _analysis(payload, member_values):
        served["members"] = member_values
        threaded_payload = dict(payload)
        analysis = era._market_analysis_from_event_snapshot(
            calibration_conn=sqlite3.connect(":memory:"),
            snapshot=snapshot,
            family=family,
            native_costs=native_costs,
            payload=threaded_payload,
            decision_time=datetime(2026, 7, 27, 1, 5, tzinfo=UTC),
        )
        return (
            analysis,
            analysis.forecast_yes_probability_sample_matrix(64),
            threaded_payload,
        )

    first_analysis, first_samples, first_payload = _analysis(first, members)
    recaptured_analysis, recaptured_samples, recaptured_payload = _analysis(
        recaptured,
        members[::-1],
    )
    changed_analysis, changed_samples, changed_payload = _analysis(
        first,
        changed_members,
    )

    assert np.array_equal(first_analysis.p_posterior, recaptured_analysis.p_posterior)
    assert np.array_equal(first_samples, recaptured_samples)
    assert first_payload["_edli_spine_debiased_members_native"] == (
        recaptured_payload["_edli_spine_debiased_members_native"]
    ) == sorted(float(value) for value in members)
    assert not np.array_equal(first_analysis.p_posterior, changed_analysis.p_posterior)
    assert not np.array_equal(first_samples, changed_samples)
    assert changed_payload["_edli_spine_debiased_members_native"] == sorted(
        float(value) for value in changed_members
    )


def test_day0_hourly_bundle_authority_requires_expected_model_proof():
    """A Day0 hourly vector without complete model proof cannot refresh belief."""
    from src.engine import monitor_refresh

    assert monitor_refresh._day0_hourly_bundle_authority_rejection_reason(
        {
            "source_id": "day0_hourly_vectors",
            "source_models": ["icon_d2"],
            "source_model_count": 1,
            "fetch_time": "2026-06-30T02:44:32+00:00",
        }
    ) == "day0_hourly_bundle_expected_models_missing"

    assert monitor_refresh._day0_hourly_bundle_authority_rejection_reason(
        {
            "source_id": "day0_hourly_vectors",
            "expected_models": ["icon_d2", "ecmwf_ifs"],
            "source_models": ["icon_d2"],
            "source_model_count": 1,
            "fetch_time": "2026-06-30T02:44:32+00:00",
        }
    ) == "day0_hourly_bundle_missing_expected_models:ecmwf_ifs"


# ===========================================================================
# Parsing (live-verified payload shape)
# ===========================================================================

class TestParsePayload:
    def test_multi_model_suffixed_keys(self):
        payload = {
            "timezone": "Europe/Paris",
            "hourly": {
                "time": ["2026-06-10T00:00", "2026-06-10T01:00"],
                "temperature_2m_icon_d2": [15.1, 14.8],
                "temperature_2m_meteofrance_arome_france_hd": [15.4, None],
            },
        }
        vectors = parse_openmeteo_hourly_payload(
            payload, city=_paris(),
            models=["icon_d2", "meteofrance_arome_france_hd"],
            captured_at="2026-06-10T09:00:00+00:00",
        )
        assert {v.model for v in vectors} == {"icon_d2", "meteofrance_arome_france_hd"}
        arome = next(v for v in vectors if v.model.startswith("meteofrance"))
        assert len(arome.times) == 1  # null sample dropped, times stay aligned

    def test_single_model_plain_key_fallback(self):
        payload = {
            "hourly": {"time": ["2026-06-10T00:00"], "temperature_2m": [15.1]},
        }
        vectors = parse_openmeteo_hourly_payload(
            payload, city=_paris(), models=["icon_d2"],
            captured_at="2026-06-10T09:00:00+00:00",
        )
        assert len(vectors) == 1 and vectors[0].temps_c == (15.1,)

    def test_garbage_payload_is_empty(self):
        assert parse_openmeteo_hourly_payload(None, city=_paris(), models=["icon_d2"], captured_at="x") == []
        assert parse_openmeteo_hourly_payload({"hourly": "no"}, city=_paris(), models=["icon_d2"], captured_at="x") == []


# ===========================================================================
# R9 — persistence: roundtrip, idempotency, retention, freshness gate
# ===========================================================================

class TestPersistence:
    def test_read_error_is_distinct_when_producer_requires_proof(self):
        class BrokenConnection:
            def execute(self, *_args, **_kwargs):
                raise sqlite3.OperationalError("forecast store unavailable")

        conn = BrokenConnection()
        assert read_freshest_day0_hourly_vectors(
            city="Paris",
            target_date="2026-06-10",
            conn=conn,
        ) == []
        with pytest.raises(sqlite3.OperationalError, match="forecast store unavailable"):
            read_freshest_day0_hourly_vectors(
                city="Paris",
                target_date="2026-06-10",
                conn=conn,
                raise_on_db_error=True,
            )

    def test_roundtrip_and_idempotency(self):
        conn = _conn()
        v = _vector()
        assert persist_day0_hourly_vectors([v], target_date="2026-06-10", conn=conn, request_hash="sha256:test", now=PRUNE_NOW) == 1
        assert persist_day0_hourly_vectors([v], target_date="2026-06-10", conn=conn, request_hash="sha256:test", now=PRUNE_NOW) == 0  # idempotent
        out = read_freshest_day0_hourly_vectors(
            city="Paris", target_date="2026-06-10",
            now=datetime(2026, 6, 10, 10, 0, tzinfo=UTC), conn=conn,
        )
        assert len(out) == 1
        assert out[0].temps_c == v.temps_c and out[0].times == v.times

    def test_freshest_per_model_wins(self):
        conn = _conn()
        old = _vector(captured_at=datetime(2026, 6, 10, 7, 0, tzinfo=UTC), temps=[10.0] * 24)
        new = _vector(captured_at=datetime(2026, 6, 10, 9, 0, tzinfo=UTC), temps=[20.0] * 24)
        persist_day0_hourly_vectors([old, new], target_date="2026-06-10", conn=conn, request_hash="sha256:test", now=PRUNE_NOW)
        out = read_freshest_day0_hourly_vectors(
            city="Paris", target_date="2026-06-10",
            now=datetime(2026, 6, 10, 9, 30, tzinfo=UTC), conn=conn,
        )
        assert len(out) == 1 and out[0].temps_c[0] == 20.0

    def test_target_date_without_remaining_grid_is_not_persisted(self):
        conn = _conn()
        wrong_day = replace(
            _vector(
                captured_at=datetime(2026, 6, 10, 10, 0, tzinfo=UTC),
                temps=[20.0] * 24,
            ),
            times=tuple(f"2026-06-11T{hour:02d}:00" for hour in range(24)),
        )

        assert persist_day0_hourly_vectors(
            [wrong_day],
            target_date="2026-06-10",
            conn=conn,
            request_hash="sha256:wrong-day",
            now=PRUNE_NOW,
        ) == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM day0_hourly_vectors"
        ).fetchone()[0] == 0

    def test_reader_falls_back_past_newer_target_incomplete_vector(self):
        conn = _conn()
        old = _vector(
            captured_at=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            temps=[18.0] * 24,
        )
        persist_day0_hourly_vectors(
            [old],
            target_date="2026-06-10",
            conn=conn,
            request_hash="sha256:old-valid",
            now=PRUNE_NOW,
        )
        bad = replace(
            _vector(
                captured_at=datetime(2026, 6, 10, 10, 0, tzinfo=UTC),
                temps=[30.0] * 24,
            ),
            times=tuple(f"2026-06-11T{hour:02d}:00" for hour in range(24)),
        )
        conn.execute(
            """
            INSERT INTO day0_hourly_vectors (
                vector_id, model, city, target_date, timezone_name,
                captured_at, endpoint, request_hash, times_json, temps_c_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-bad-row", bad.model, bad.city, "2026-06-10",
                bad.timezone_name, bad.captured_at,
                "https://example.invalid", "sha256:legacy-bad",
                json.dumps(list(bad.times)), json.dumps(list(bad.temps_c)),
            ),
        )
        conn.commit()

        out = read_freshest_day0_hourly_vectors(
            city="Paris",
            target_date="2026-06-10",
            now=datetime(2026, 6, 10, 10, 30, tzinfo=UTC),
            conn=conn,
            remaining_window_start=datetime(
                2026, 6, 10, 10, 30, tzinfo=UTC
            ),
            require_complete_remaining_window=True,
        )

        assert len(out) == 1
        assert out[0].captured_at == old.captured_at
        assert out[0].temps_c[0] == 18.0

    def test_require_expected_rejects_partial_model_bundle(self):
        """Munich regression: one fresh regional vector is not a complete live bundle."""
        conn = _conn()
        icon_only = _vector(model="icon_d2")
        persist_day0_hourly_vectors(
            [icon_only],
            target_date="2026-06-10",
            conn=conn,
            request_hash="sha256:test",
            now=PRUNE_NOW,
        )

        out = read_freshest_day0_hourly_vectors(
            city="Paris",
            target_date="2026-06-10",
            now=datetime(2026, 6, 10, 10, 0, tzinfo=UTC),
            conn=conn,
            expected_models=["icon_d2", "ecmwf_ifs"],
            require_expected=True,
        )

        assert out == []

    def test_expected_bundle_reads_freshest_per_model_across_capture_times(self):
        conn = _conn()
        icon = _vector(
            model="icon_d2",
            captured_at=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            temps=[20.0] * 24,
        )
        ecmwf = _vector(
            model="ecmwf_ifs",
            captured_at=datetime(2026, 6, 10, 8, 55, tzinfo=UTC),
            temps=[18.0] * 24,
        )
        stale_ecmwf = _vector(
            model="ecmwf_ifs",
            captured_at=datetime(2026, 6, 10, 7, 0, tzinfo=UTC),
            temps=[10.0] * 24,
        )
        persist_day0_hourly_vectors(
            [icon, ecmwf, stale_ecmwf],
            target_date="2026-06-10",
            conn=conn,
            request_hash="sha256:test",
            now=PRUNE_NOW,
        )

        out = read_freshest_day0_hourly_vectors(
            city="Paris",
            target_date="2026-06-10",
            now=datetime(2026, 6, 10, 9, 30, tzinfo=UTC),
            conn=conn,
            expected_models=["icon_d2", "ecmwf_ifs"],
            require_expected=True,
        )

        assert [v.model for v in out] == ["icon_d2", "ecmwf_ifs"]
        assert [v.temps_c[0] for v in out] == [20.0, 18.0]

    def test_live_read_rejects_fresh_but_truncated_remaining_horizon(self):
        conn = _conn()
        truncated = _vector(temps=[20.0] * 20)
        truncated = Day0HourlyVector(
            model=truncated.model,
            city=truncated.city,
            target_date=truncated.target_date,
            timezone_name=truncated.timezone_name,
            captured_at=truncated.captured_at,
            times=truncated.times[:20],
            temps_c=truncated.temps_c[:20],
        )
        persist_day0_hourly_vectors(
            [truncated],
            target_date="2026-06-10",
            conn=conn,
            request_hash="sha256:test",
            now=PRUNE_NOW,
        )

        out = read_freshest_day0_hourly_vectors(
            city="Paris",
            target_date="2026-06-10",
            now=datetime(2026, 6, 10, 13, 0, tzinfo=UTC),
            conn=conn,
            expected_models=["icon_d2"],
            require_expected=True,
            remaining_window_start=datetime(2026, 6, 10, 13, 0, tzinfo=UTC),
            require_complete_remaining_window=True,
        )

        assert out == []

    def test_required_expected_bundle_rejects_excessive_model_capture_skew(self):
        conn = _conn()
        icon = _vector(
            model="icon_d2",
            captured_at=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            temps=[20.0] * 24,
        )
        stale_anchor = _vector(
            model="ecmwf_ifs",
            captured_at=datetime(2026, 6, 10, 7, 50, tzinfo=UTC),
            temps=[18.0] * 24,
        )
        persist_day0_hourly_vectors(
            [icon, stale_anchor],
            target_date="2026-06-10",
            conn=conn,
            request_hash="sha256:test",
            now=PRUNE_NOW,
        )

        out = read_freshest_day0_hourly_vectors(
            city="Paris",
            target_date="2026-06-10",
            now=datetime(2026, 6, 10, 9, 30, tzinfo=UTC),
            conn=conn,
            expected_models=["icon_d2", "ecmwf_ifs"],
            require_expected=True,
            max_bundle_skew_minutes=60.0,
        )

        assert out == []

    def test_stale_vectors_are_not_served(self):
        """R9 freshness gate: a 5h-old run must NOT masquerade as the current
        remaining-day distribution."""
        conn = _conn()
        v = _vector(captured_at=datetime(2026, 6, 10, 4, 0, tzinfo=UTC))
        persist_day0_hourly_vectors([v], target_date="2026-06-10", conn=conn, request_hash="sha256:test", now=PRUNE_NOW)
        out = read_freshest_day0_hourly_vectors(
            city="Paris", target_date="2026-06-10",
            now=datetime(2026, 6, 10, 9, 30, tzinfo=UTC), max_age_hours=3.0, conn=conn,
        )
        assert out == []

    def test_retention_prunes_old_rows(self):
        conn = _conn()
        ancient = _vector(captured_at=datetime(2026, 6, 1, 0, 0, tzinfo=UTC))
        persist_day0_hourly_vectors([ancient], target_date="2026-06-01", conn=conn, request_hash="sha256:test", now=PRUNE_NOW)
        fresh = _vector()
        persist_day0_hourly_vectors([fresh], target_date="2026-06-10", conn=conn, request_hash="sha256:test", now=PRUNE_NOW)
        n = conn.execute("SELECT COUNT(*) FROM day0_hourly_vectors").fetchone()[0]
        assert n == 1  # the 9-day-old row was pruned on the second write pass

    def test_missing_table_read_is_fail_soft_empty(self):
        conn = _conn()
        out = read_freshest_day0_hourly_vectors(
            city="Paris", target_date="2026-06-10",
            now=datetime(2026, 6, 10, 10, 0, tzinfo=UTC), conn=conn,
        )
        assert out == []


# ===========================================================================
# R10 — remaining-day hour selection
# ===========================================================================

class TestRemainingDaySelection:
    def test_only_hours_at_or_after_now_count(self):
        # Paris local: peak 30C at 14:00; evening cools to 22C.
        temps = [18, 17, 16, 16, 15, 15, 16, 18, 21, 24, 26, 28, 29, 30, 30, 29, 28, 27, 26, 25, 24, 23, 22, 22]
        v = _vector(temps=[float(t) for t in temps])
        # now = 16:00 local (14:00 UTC, CEST): remaining max is 28 (16:00 onward)
        now = datetime(2026, 6, 10, 14, 0, tzinfo=UTC)
        out = remaining_day_extremes_c([v], target_date="2026-06-10", now=now, metric="high")
        assert out == [28.0]

    def test_no_remaining_hours_contributes_nothing(self):
        v = _vector()
        now = datetime(2026, 6, 11, 1, 0, tzinfo=UTC)  # past the local day
        assert remaining_day_extremes_c([v], target_date="2026-06-10", now=now, metric="high") == []

    def test_post_midnight_decision_keeps_observation_uncovered_terminal_tail(self):
        v = Day0HourlyVector(
            model="ecmwf_ifs",
            city="Paris",
            target_date="2026-06-10",
            timezone_name="Europe/Paris",
            captured_at="2026-06-10T21:41:00+00:00",
            times=("2026-06-10T22:00", "2026-06-10T23:00"),
            temps_c=(22.4, 22.1),
        )
        observation_time = datetime(2026, 6, 10, 21, 20, tzinfo=UTC)  # 23:20 local
        decision_time = datetime(2026, 6, 10, 23, 15, tzinfo=UTC)  # 01:15 next day

        assert remaining_day_extremes_c(
            [v],
            target_date="2026-06-10",
            now=decision_time,
            metric="high",
            window_start=observation_time,
        ) == [22.1]

    @pytest.mark.parametrize("metric", ["high", "low"])
    def test_terminal_subhour_uses_same_hour_grid_anchor(self, metric):
        v = Day0HourlyVector(
            model="ecmwf_ifs",
            city="Paris",
            target_date="2026-06-10",
            timezone_name="Europe/Paris",
            captured_at="2026-06-10T21:17:00+00:00",
            times=("2026-06-10T22:00", "2026-06-10T23:00"),
            temps_c=(22.4, 22.1),
        )
        now = datetime(2026, 6, 10, 21, 17, tzinfo=UTC)  # 23:17 local

        assert remaining_day_extremes_c(
            [v], target_date="2026-06-10", now=now, metric=metric
        ) == [22.1]

    def test_terminal_anchor_older_than_one_hour_is_unavailable(self):
        v = Day0HourlyVector(
            model="ecmwf_ifs",
            city="Paris",
            target_date="2026-06-10",
            timezone_name="Europe/Paris",
            captured_at="2026-06-10T21:17:00+00:00",
            times=("2026-06-10T21:00",),
            temps_c=(22.4,),
        )
        now = datetime(2026, 6, 10, 21, 17, tzinfo=UTC)  # 23:17 local

        assert remaining_day_extremes_c(
            [v], target_date="2026-06-10", now=now, metric="high"
        ) == []

    def test_midday_truncated_vector_cannot_masquerade_as_terminal_hour(self):
        v = Day0HourlyVector(
            model="ecmwf_ifs",
            city="Paris",
            target_date="2026-06-10",
            timezone_name="Europe/Paris",
            captured_at="2026-06-10T10:30:00+00:00",
            times=("2026-06-10T12:00",),
            temps_c=(22.4,),
        )
        now = datetime(2026, 6, 10, 10, 30, tzinfo=UTC)  # 12:30 local

        assert remaining_day_extremes_c(
            [v], target_date="2026-06-10", now=now, metric="high"
        ) == []

    @pytest.mark.parametrize("metric", ["high", "low"])
    def test_missing_future_hour_fails_closed_for_both_metrics(self, metric):
        times = tuple(
            f"2026-06-10T{hour:02d}:00"
            for hour in range(24)
            if hour != 20
        )
        v = Day0HourlyVector(
            model="ecmwf_ifs",
            city="Paris",
            target_date="2026-06-10",
            timezone_name="Europe/Paris",
            captured_at="2026-06-10T13:00:00+00:00",
            times=times,
            temps_c=tuple(20.0 for _ in times),
        )
        now = datetime(2026, 6, 10, 13, 0, tzinfo=UTC)  # 15:00 local

        assert remaining_day_extremes_c(
            [v], target_date="2026-06-10", now=now, metric=metric
        ) == []

    def test_missing_hour_before_causal_boundary_does_not_block(self):
        times = tuple(
            f"2026-06-10T{hour:02d}:00"
            for hour in range(24)
            if hour != 10
        )
        v = Day0HourlyVector(
            model="ecmwf_ifs",
            city="Paris",
            target_date="2026-06-10",
            timezone_name="Europe/Paris",
            captured_at="2026-06-10T13:00:00+00:00",
            times=times,
            temps_c=tuple(float(hour) for hour in range(24) if hour != 10),
        )
        now = datetime(2026, 6, 10, 13, 30, tzinfo=UTC)  # 15:30 local

        assert remaining_day_extremes_c(
            [v], target_date="2026-06-10", now=now, metric="high"
        ) == [23.0]

    def test_spring_forward_uses_the_real_23_hour_local_day(self):
        times = tuple(
            ["2026-03-29T00:00"]
            + [f"2026-03-29T{hour:02d}:00" for hour in range(2, 24)]
        )
        v = Day0HourlyVector(
            model="ukmo_global_deterministic_10km",
            city="London",
            target_date="2026-03-29",
            timezone_name="Europe/London",
            captured_at="2026-03-28T23:30:00+00:00",
            times=times,
            temps_c=tuple(float(index) for index in range(len(times))),
        )

        assert remaining_day_extremes_c(
            [v],
            target_date="2026-03-29",
            now=datetime(2026, 3, 29, 0, 0, tzinfo=UTC),
            metric="high",
        ) == [22.0]

    def test_fall_back_requires_both_repeated_local_hours(self):
        complete_times = tuple(
            ["2026-10-25T00:00", "2026-10-25T01:00", "2026-10-25T01:00"]
            + [f"2026-10-25T{hour:02d}:00" for hour in range(2, 24)]
        )
        complete = Day0HourlyVector(
            model="ukmo_global_deterministic_10km",
            city="London",
            target_date="2026-10-25",
            timezone_name="Europe/London",
            captured_at="2026-10-24T22:30:00+00:00",
            times=complete_times,
            temps_c=tuple(float(index) for index in range(len(complete_times))),
        )
        incomplete = Day0HourlyVector(
            model=complete.model,
            city=complete.city,
            target_date=complete.target_date,
            timezone_name=complete.timezone_name,
            captured_at=complete.captured_at,
            times=tuple(item for index, item in enumerate(complete_times) if index != 2),
            temps_c=tuple(float(index) for index in range(len(complete_times) - 1)),
        )
        now = datetime(2026, 10, 24, 23, 0, tzinfo=UTC)  # local midnight

        assert remaining_day_extremes_c(
            [complete], target_date="2026-10-25", now=now, metric="high"
        ) == [24.0]
        assert remaining_day_extremes_c(
            [incomplete], target_date="2026-10-25", now=now, metric="high"
        ) == []

    def test_fall_back_boundary_distinguishes_the_two_repeated_hours(self):
        times = tuple(
            ["2026-10-25T00:00", "2026-10-25T01:00", "2026-10-25T01:00"]
            + [f"2026-10-25T{hour:02d}:00" for hour in range(2, 24)]
        )
        temps = [10.0, 99.0, 77.0] + [10.0] * 22
        v = Day0HourlyVector(
            model="ukmo_global_deterministic_10km",
            city="London",
            target_date="2026-10-25",
            timezone_name="Europe/London",
            captured_at="2026-10-24T22:30:00+00:00",
            times=times,
            temps_c=tuple(temps),
        )

        assert remaining_day_extremes_c(
            [v],
            target_date="2026-10-25",
            now=datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
            metric="high",
        ) == [77.0]

    def test_low_metric_takes_min(self):
        temps = [18.0, 12.0, 11.0] + [15.0] * 21
        v = _vector(temps=temps)
        now = datetime(2026, 6, 9, 22, 30, tzinfo=UTC)  # 00:30 local Jun 10
        out = remaining_day_extremes_c([v], target_date="2026-06-10", now=now, metric="low")
        assert out == [11.0]


# ===========================================================================
# R11 — post-peak repricing relationship (era consumption)
# ===========================================================================

class TestRemainingDayMembers:
    def _family(self):
        return SimpleNamespace(city="Paris", target_date="2026-06-10", metric="high")

    def test_current_state_conditioning_is_causal_and_decays(self):
        from src.signal.day0_window import (
            condition_day0_hourly_members_on_current_state,
        )

        members = np.asarray(
            [
                [10.0, 13.0, 14.0, 15.0],
                [20.0, 18.0, 17.0, 16.0],
            ]
        )
        times = [
            "2026-06-10T10:00:00+00:00",
            "2026-06-10T11:00:00+00:00",
            "2026-06-10T12:00:00+00:00",
            "2026-06-10T13:00:00+00:00",
        ]

        result = condition_day0_hourly_members_on_current_state(
            members,
            times,
            observation_time=datetime(2026, 6, 10, 11, 0, tzinfo=UTC),
            current_temp=12.0,
            e_fold_hours=4.2,
        )

        assert result is not None
        conditioned, innovations = result
        assert innovations.tolist() == [-1.0, -6.0]
        assert conditioned[:, 0].tolist() == [10.0, 20.0]
        assert conditioned[:, 1].tolist() == [12.0, 12.0]
        assert conditioned[:, 2].tolist() == pytest.approx(
            [
                14.0 - np.exp(-1.0 / 4.2),
                17.0 - 6.0 * np.exp(-1.0 / 4.2),
            ]
        )
        assert abs(conditioned[0, 3] - 15.0) < abs(conditioned[0, 2] - 14.0)

    def test_event_bound_market_analysis_constructor_matches_runtime_contract(self):
        """The live Day0 builder cannot pass retired MarketAnalysis kwargs."""
        import ast
        import inspect
        import textwrap

        import src.engine.event_reactor_adapter as era
        from src.strategy.market_analysis import MarketAnalysis

        tree = ast.parse(
            textwrap.dedent(
                inspect.getsource(era._market_analysis_from_event_snapshot)
            )
        )
        constructor = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "MarketAnalysis"
        )
        passed = {keyword.arg for keyword in constructor.keywords if keyword.arg}
        accepted = set(inspect.signature(MarketAnalysis).parameters)

        assert passed <= accepted

    def test_remaining_day_q_is_live_without_setting(self):
        """Remaining-day q is live Day0 law; missing settings cannot restore full-day masked q."""
        from src.engine.event_reactor_adapter import _day0_remaining_day_q_enabled

        assert _day0_remaining_day_q_enabled() is True

    def test_current_temperature_uses_newer_same_station_fast_print(
        self, monkeypatch
    ):
        """Trajectory time follows the freshest station print, not old WU cadence."""
        import src.engine.event_reactor_adapter as era
        from src.state.schema.observation_prints_schema import append_print, ensure_table

        conn = _conn()
        ensure_table(conn)
        append_print(
            conn,
            city="Paris",
            station_id="LFPG",
            source_channel="wu_icao_history",
            publish_ts_utc="2026-06-10T13:00:00+00:00",
            value_native=24.0,
            unit="C",
            fetched_at_utc="2026-06-10T13:05:00+00:00",
        )
        append_print(
            conn,
            city="Paris",
            station_id="LFPG",
            source_channel="aviationweather_metar",
            publish_ts_utc="2026-06-10T13:30:00+00:00",
            value_native=23.0,
            unit="C",
            fetched_at_utc="2026-06-10T13:34:00+00:00",
            raw_report="METAR LFPG 101330Z 23010KT 23/12",
        )
        append_print(
            conn,
            city="Paris",
            station_id="LFPG",
            source_channel="aviationweather_metar",
            publish_ts_utc="2026-06-10T13:35:00+00:00",
            value_native=22.0,
            unit="C",
            fetched_at_utc="2026-06-10T13:50:00+00:00",
            raw_report="METAR LFPG 101335Z 23010KT 22/12",
        )
        monkeypatch.setattr(era, "runtime_cities_by_name", lambda: {"Paris": _paris()})

        current = era._latest_day0_current_temperature_native(
            world_conn=conn,
            family=self._family(),
            decision_time=datetime(2026, 6, 10, 13, 40, tzinfo=UTC),
        )
        conn.close()

        assert current == (
            23.0,
            datetime(2026, 6, 10, 13, 30, tzinfo=UTC),
            "aviationweather_metar",
        )

    def test_ogimet_hourly_latest_report_reaches_current_temperature_authority(
        self, monkeypatch
    ):
        """The native hourly ingest bridge must publish the latest report, not
        only the bucket extrema, for NOAA-routed current-state conditioning."""
        import src.engine.event_reactor_adapter as era
        from scripts.obs_live_tick import (
            _append_hourly_prints_to_ledger,
            _hourly_observation_prints,
        )
        from src.data.wu_hourly_client import HourlyObservation
        from src.state.schema.observation_prints_schema import ensure_table

        city = SimpleNamespace(
            name="Tel Aviv",
            timezone="Asia/Jerusalem",
            settlement_unit="C",
            settlement_source_type="noaa",
            wu_station="LLBG",
        )
        obs = HourlyObservation(
            city="Tel Aviv",
            target_date="2026-07-27",
            local_hour=0.0,
            local_timestamp="2026-07-27T00:00:00+03:00",
            utc_timestamp="2026-07-26T21:00:00+00:00",
            utc_offset_minutes=180,
            dst_active=1,
            is_ambiguous_local_hour=0,
            is_missing_local_hour=0,
            time_basis="utc_hour_bucket_extremum",
            hour_max_temp=28.0,
            hour_min_temp=26.0,
            hour_max_raw_ts="2026-07-26T21:00:00+00:00",
            hour_min_raw_ts="2026-07-26T21:10:00+00:00",
            temp_unit="C",
            station_id="LLBG",
            observation_count=3,
            latest_raw_ts="2026-07-26T21:20:00+00:00",
            latest_temp=27.0,
        )
        conn = _conn()
        ensure_table(conn)
        _append_hourly_prints_to_ledger(
            conn,
            _hourly_observation_prints(
                obs,
                source_channel="ogimet_metar_llbg",
                fetched_at_utc="2026-07-26T21:25:00+00:00",
            ),
        )
        monkeypatch.setattr(
            era,
            "runtime_cities_by_name",
            lambda: {"Tel Aviv": city},
        )

        current = era._latest_day0_current_temperature_native(
            world_conn=conn,
            family=SimpleNamespace(
                city="Tel Aviv",
                target_date="2026-07-27",
                metric="high",
            ),
            decision_time=datetime(2026, 7, 26, 21, 30, tzinfo=UTC),
        )
        conn.close()

        assert current == (
            27.0,
            datetime(2026, 7, 26, 21, 20, tzinfo=UTC),
            "ogimet_metar_llbg",
        )

    def test_fahrenheit_fast_current_temperature_requires_precise_t_group(
        self, monkeypatch
    ):
        """Whole-C METAR fallback cannot silently become a Fahrenheit path state."""
        import src.engine.event_reactor_adapter as era
        from src.state.schema.observation_prints_schema import append_print, ensure_table

        city = SimpleNamespace(
            name="NYC",
            timezone="America/New_York",
            settlement_unit="F",
            settlement_source_type="wu_icao",
            wu_station="KLGA",
            lat=40.7,
            lon=-73.9,
        )
        conn = _conn()
        ensure_table(conn)
        for observed_at, raw_report in (
            (
                "2026-06-10T19:30:00+00:00",
                "METAR KLGA 101930Z 18008KT 10SM CLR 26/16 A2998 T02560161",
            ),
            (
                "2026-06-10T19:40:00+00:00",
                "METAR KLGA 101940Z 18008KT 10SM CLR 26/16 A2998",
            ),
        ):
            append_print(
                conn,
                city="NYC",
                station_id="KLGA",
                source_channel="aviationweather_metar",
                publish_ts_utc=observed_at,
                value_native=26.0,
                unit="C",
                fetched_at_utc=observed_at,
                raw_report=raw_report,
            )
        monkeypatch.setattr(era, "runtime_cities_by_name", lambda: {"NYC": city})

        current = era._latest_day0_current_temperature_native(
            world_conn=conn,
            family=SimpleNamespace(
                city="NYC", target_date="2026-06-10", metric="high"
            ),
            decision_time=datetime(2026, 6, 10, 19, 45, tzinfo=UTC),
        )
        conn.close()

        assert current == (
            pytest.approx(78.08),
            datetime(2026, 6, 10, 19, 30, tzinfo=UTC),
            "aviationweather_metar",
        )

    def test_post_peak_members_clamp_to_running_max_floor(self, monkeypatch):
        """All remaining-hours extremes BELOW the running max -> every pooled
        member clamps to the floor -> the floor bin owns ~all probability mass.
        This is precisely the post-peak overpricing the full-day q got wrong."""
        import src.engine.event_reactor_adapter as era

        vectors = [
            _vector(model="icon_d2", temps=[20.0] * 24),
            _vector(model="meteofrance_arome_france_hd", temps=[21.0] * 24),
        ]
        monkeypatch.setattr(
            "src.data.day0_hourly_vectors.read_freshest_day0_hourly_vectors",
            lambda **kw: vectors,
        )
        payload = {
            "metric": "high",
            "rounded_value": 25.0,
            "settlement_source": "wu_api",
        }
        members = era._day0_remaining_day_members(
            payload=payload, family=self._family(), unit="C",
            decision_time=datetime(2026, 6, 10, 15, 0, tzinfo=UTC),
        )
        assert members is not None
        # every member clamped UP to the running max (absorbing physical law)
        assert np.all(members == 25.0)
        assert payload["_edli_day0_unclamped_remaining_extrema_native"] == [
            20.0,
            21.0,
        ]
        assert payload["_edli_day0_remaining_models"] == 2

    @pytest.mark.parametrize(
        ("metric", "settlement_boundary", "physical_boundary", "future", "impossible_bin"),
        (
            ("high", 29.0, 30.0, 20.0, 0),
            ("low", 10.0, 9.0, 20.0, 2),
        ),
    )
    def test_statistical_physical_boundary_removes_impossible_q_mass(
        self,
        monkeypatch,
        metric,
        settlement_boundary,
        physical_boundary,
        future,
        impossible_bin,
    ):
        """Fast physical evidence constrains q without becoming settlement truth."""
        import src.engine.event_reactor_adapter as era
        from src.contracts.settlement_semantics import SettlementSemantics

        vectors = [
            _vector(model="icon_d2", temps=[future] * 24),
            _vector(
                model="meteofrance_arome_france_hd",
                temps=[future] * 24,
            ),
        ]
        monkeypatch.setattr(
            "src.data.day0_hourly_vectors.read_freshest_day0_hourly_vectors",
            lambda **kw: vectors,
        )
        monkeypatch.setattr(
            era,
            "_day0_process_sigma_native",
            lambda **kw: 1.0,
        )
        monkeypatch.setattr(
            era,
            "_day0_absorbing_mask",
            lambda **kw: np.ones(3, dtype=float),
        )
        payload = {
            "metric": metric,
            "rounded_value": settlement_boundary,
            "high_so_far": settlement_boundary if metric == "high" else None,
            "low_so_far": settlement_boundary if metric == "low" else None,
            "settlement_source": "wu_api",
            "_edli_day0_probability_boundary_native": physical_boundary,
        }
        family = SimpleNamespace(
            city="Paris",
            target_date="2026-06-10",
            metric=metric,
        )

        members = era._day0_remaining_day_members(
            payload=payload,
            family=family,
            unit="C",
            decision_time=datetime(2026, 6, 10, 15, 0, tzinfo=UTC),
        )
        assert members is not None
        assert np.all(members == physical_boundary)

        bins = (
            [
                Bin(None, 29, "C", "29C or below"),
                Bin(30, 30, "C", "30C"),
                Bin(31, None, "C", "31C or above"),
            ]
            if metric == "high"
            else [
                Bin(None, 8, "C", "8C or below"),
                Bin(9, 9, "C", "9C"),
                Bin(10, None, "C", "10C or above"),
            ]
        )
        q = era._day0_remaining_p_raw_vector(
            np.asarray([future, future], dtype=float),
            city=_paris(),
            settlement_semantics=SettlementSemantics.for_city(_paris()),
            bins=bins,
            payload=payload,
            extra_member_sigma=0.0,
        )
        assert q[impossible_bin] == 0.0

        sampler = era._make_day0_bootstrap_sampler(
            members_native=np.asarray([future, future], dtype=float),
            payload=payload,
            family=family,
            unit="C",
            decision_time=datetime(2026, 6, 10, 15, 0, tzinfo=UTC),
        )
        assert sampler is not None
        assert sampler.rounded == physical_boundary

    def test_remaining_members_keep_source_clock_exact_but_freeze_temporal_q_clock(
        self, monkeypatch
    ):
        """Sub-minute submit latency must not manufacture a new point-q world."""
        import src.engine.event_reactor_adapter as era

        vectors = [
            _vector(model="icon_d2", temps=[20.0] * 24),
            _vector(model="meteofrance_arome_france_hd", temps=[21.0] * 24),
        ]
        source_times: list[datetime] = []
        probability_times: list[datetime | None] = []

        def read_vectors(**kwargs):
            source_times.append(kwargs["now"])
            return vectors

        def record_authority(**kwargs):
            probability_times.append(kwargs["decision_time"])

        monkeypatch.setattr(
            "src.data.day0_hourly_vectors.read_freshest_day0_hourly_vectors",
            read_vectors,
        )
        monkeypatch.setattr(
            era,
            "_record_day0_remaining_day_exit_authority",
            record_authority,
        )
        exact = datetime(2026, 6, 10, 15, 0, 59, 900000, tzinfo=UTC)
        probability_cut = era._day0_probability_clock(exact)
        members = era._day0_remaining_day_members(
            payload={
                "metric": "high",
                "rounded_value": 25.0,
                "settlement_source": "wu_api",
            },
            family=self._family(),
            unit="C",
            decision_time=exact,
            probability_time=probability_cut,
        )

        assert members is not None
        assert source_times == [exact]
        assert probability_times == [probability_cut]

    @pytest.mark.parametrize(
        ("metric", "observed", "future", "winning_index"),
        (
            ("high", 26.0, [21.0, 22.0], 1),
            ("low", 26.0, [30.0, 31.0], 1),
        ),
    )
    def test_probability_noise_applies_before_absorbing_boundary(
        self, monkeypatch, metric, observed, future, winning_index
    ):
        """A completed plateau cannot be noised into a fictitious excursion."""
        import src.engine.event_reactor_adapter as era

        monkeypatch.setattr(era, "runtime_cities_by_name", lambda: {"Paris": _paris()})
        bins = [
            Bin(None, 25, "C", "25C or below"),
            Bin(26, 26, "C", "26C"),
            Bin(27, None, "C", "27C or above"),
        ]
        payload = {
            "metric": metric,
            "rounded_value": observed,
            "_edli_q_source": "day0_remaining_day",
        }
        q = era._snapshot_p_raw(
            {"settlement_unit": "C", "temperature_metric": metric},
            family=SimpleNamespace(city="Paris", metric=metric),
            bins=bins,
            members=np.asarray(future, dtype=float),
            payload=payload,
        )

        assert q[winning_index] == pytest.approx(1.0)
        assert payload["_edli_day0_probability_operator"] == (
            "extreme_observed_then_noisy_future_v1"
        )

    def test_probability_operator_preserves_real_future_excursion(self, monkeypatch):
        """Correct ordering removes fictitious tails without suppressing real upside."""
        import src.engine.event_reactor_adapter as era

        monkeypatch.setattr(era, "runtime_cities_by_name", lambda: {"Paris": _paris()})
        bins = [
            Bin(None, 25, "C", "25C or below"),
            Bin(26, 26, "C", "26C"),
            Bin(27, None, "C", "27C or above"),
        ]
        q = era._snapshot_p_raw(
            {"settlement_unit": "C", "temperature_metric": "high"},
            family=SimpleNamespace(city="Paris", metric="high"),
            bins=bins,
            members=np.asarray([28.0, 29.0], dtype=float),
            payload={
                "metric": "high",
                "rounded_value": 26.0,
                "_edli_q_source": "day0_remaining_day",
            },
        )

        assert q[2] > 0.99

    @pytest.mark.parametrize(
        ("metric", "future", "boundary_bin", "retracted_bin"),
        (
            ("high", [24.0, 25.0], 1, 0),
            ("low", [31.0, 32.0], 1, 2),
        ),
    )
    def test_provisional_boundary_is_revision_mixture_not_static_or_ignored(
        self,
        monkeypatch,
        metric,
        future,
        boundary_bin,
        retracted_bin,
    ):
        """7/24 HKO class: current extreme must dominate q without q=1."""
        import src.engine.event_reactor_adapter as era
        from src.contracts.settlement_semantics import SettlementSemantics

        survival = 0.90
        bins = [
            Bin(None, 27, "C", "27C or below"),
            Bin(28, 28, "C", "28C"),
            Bin(29, None, "C", "29C or above"),
        ]
        payload = {
            "metric": metric,
            "raw_value": 28.1,
            "rounded_value": 28,
            "high_so_far": 28.1 if metric == "high" else None,
            "low_so_far": 28.1 if metric == "low" else None,
            "settlement_source": "hko_hourly_accumulator",
            "evidence_finality": "PROVISIONAL_CURRENT_SNAPSHOT",
            "_edli_day0_probability_boundary_native": 28.1,
            "_edli_day0_provisional_boundary_survival_probability": survival,
        }

        q = era._day0_remaining_p_raw_vector(
            np.asarray(future, dtype=float),
            city=_hong_kong(),
            settlement_semantics=SettlementSemantics.for_city(_hong_kong()),
            bins=bins,
            payload=payload,
            extra_member_sigma=0.0,
        )

        assert q[boundary_bin] == pytest.approx(survival, abs=0.03)
        assert q[retracted_bin] == pytest.approx(1.0 - survival, abs=0.03)
        assert payload["_edli_day0_probability_operator"] == (
            "revision_mixture_extreme_observed_then_noisy_future_v2"
        )

        quoted_payload = {
            **payload,
            "best_bid": 0.001,
            "best_ask": 0.999,
            "entry_price": 0.338,
            "unrealized_pnl": -7.13,
        }
        quoted_q = era._day0_remaining_p_raw_vector(
            np.asarray(future, dtype=float),
            city=_hong_kong(),
            settlement_semantics=SettlementSemantics.for_city(_hong_kong()),
            bins=bins,
            payload=quoted_payload,
            extra_member_sigma=0.0,
        )
        assert quoted_q.tolist() == pytest.approx(q.tolist())

        monkeypatch.setattr(
            era,
            "_day0_process_sigma_native",
            lambda **_kwargs: 0.0,
        )
        monkeypatch.setattr(
            era,
            "_day0_absorbing_mask",
            lambda **_kwargs: np.ones(3, dtype=float),
        )
        sampler = era._make_day0_bootstrap_sampler(
            members_native=np.asarray(future, dtype=float),
            payload=payload,
            family=SimpleNamespace(
                city="Hong Kong",
                target_date="2026-07-24",
                metric=metric,
            ),
            unit="C",
            decision_time=datetime(2026, 7, 24, 15, 50, tzinfo=UTC),
        )
        assert sampler is not None
        assert sampler.rounded == pytest.approx(28.1)
        assert sampler.boundary_survival_probability == pytest.approx(
            survival
        )
        semantics = SettlementSemantics.for_city(_hong_kong())
        analysis = SimpleNamespace(
            _rng=np.random.default_rng(17),
            _settle=semantics.round_values,
            bins=bins,
            p_cal=q,
        )
        sampled = sampler.sample_matrix(
            analysis,
            n_samples=4000,
            n_members=1,
        )
        assert sampled.mean(axis=0).tolist() == pytest.approx(
            q.tolist(),
            abs=0.03,
        )

    def test_members_use_observation_time_after_local_midnight(self, monkeypatch):
        import src.engine.event_reactor_adapter as era

        vector = Day0HourlyVector(
            model="ecmwf_ifs",
            city="Paris",
            target_date="2026-06-10",
            timezone_name="Europe/Paris",
            captured_at="2026-06-10T21:41:00+00:00",
            times=("2026-06-10T22:00", "2026-06-10T23:00"),
            temps_c=(22.4, 22.1),
        )
        monkeypatch.setattr(era, "runtime_cities_by_name", lambda: {"Paris": _paris()})
        monkeypatch.setattr(
            "src.data.day0_hourly_vectors.read_freshest_day0_hourly_vectors",
            lambda **kw: [vector],
        )
        payload = {
            "metric": "high",
            "rounded_value": 25.0,
            "observation_time": "2026-06-10T21:20:00+00:00",
            "settlement_source": "wu_api",
        }

        members = era._day0_remaining_day_members(
            payload=payload,
            family=self._family(),
            unit="C",
            decision_time=datetime(2026, 6, 10, 23, 15, tzinfo=UTC),
        )

        assert members is not None
        assert members.tolist() == [25.0]
        assert payload["_edli_day0_remaining_window_start_utc"] == (
            "2026-06-10T21:20:00+00:00"
        )

    def test_entry_point_q_keeps_unseen_peak_tail_for_nonfinal_post_peak(self, monkeypatch):
        import src.engine.event_reactor_adapter as era

        monkeypatch.setattr(era, "runtime_cities_by_name", lambda: {"Paris": _paris()})
        bins = [
            Bin(None, 11, "C", "11C or below"),
            Bin(12, 12, "C", "12C"),
            Bin(13, None, "C", "13C or above"),
        ]
        payload = {
            "metric": "high",
            "rounded_value": 12.0,
            "observation_time": "2026-06-10T13:00:00+00:00",
            "_edli_day0_post_peak_confidence": 0.7301587,
        }
        family = SimpleNamespace(city="Paris", target_date="2026-06-10", metric="high")
        decision_time = datetime(2026, 6, 10, 13, 5, tzinfo=UTC)
        extra_sigma = era._day0_extra_member_sigma_native(
            payload=payload,
            family=family,
            unit="C",
            decision_time=decision_time,
        )
        p_raw = era._snapshot_p_raw(
            {
                "settlement_unit": "C",
                "temperature_metric": "high",
                "members_precision": 1.0,
            },
            family=family,
            bins=bins,
            members=np.array([12.0, 12.0, 12.0], dtype=float),
            payload=payload,
            extra_member_sigma=extra_sigma,
        )

        assert extra_sigma > 0.0
        assert payload["_edli_day0_unseen_peak_sigma_native"] > 0.0
        assert p_raw[1] < 0.86
        assert p_raw[2] > 0.12
        assert p_raw.sum() == pytest.approx(1.0)

    def test_excursion_still_possible_keeps_above_floor_members(self, monkeypatch):
        vectors = [
            _vector(model="icon_d2", temps=[27.5] * 24),
            _vector(model="meteofrance_arome_france_hd", temps=[24.0] * 24),
        ]
        monkeypatch.setattr(
            "src.data.day0_hourly_vectors.read_freshest_day0_hourly_vectors",
            lambda **kw: vectors,
        )
        import src.engine.event_reactor_adapter as era

        members = era._day0_remaining_day_members(
            payload={
                "metric": "high",
                "rounded_value": 25.0,
                "settlement_source": "wu_api",
            },
            family=self._family(),
            unit="C", decision_time=datetime(2026, 6, 10, 15, 0, tzinfo=UTC),
        )
        assert sorted(members.tolist()) == [25.0, 27.5]

    def test_live_members_transport_current_error_with_validated_decay(
        self, monkeypatch
    ):
        """Current error moves near hours strongly and distant hours weakly."""
        import src.engine.event_reactor_adapter as era

        vector = Day0HourlyVector(
            model="ecmwf_ifs",
            city="Paris",
            target_date="2026-06-10",
            timezone_name="Europe/Paris",
            captured_at="2026-06-10T12:30:00+00:00",
            times=tuple(f"2026-06-10T{hour:02d}:00" for hour in range(24)),
            temps_c=tuple(
                26.0 if hour == 16 else 25.0 if hour == 17 else 24.0
                for hour in range(24)
            ),
        )
        monkeypatch.setattr(era, "runtime_cities_by_name", lambda: {"Paris": _paris()})
        monkeypatch.setattr(
            "src.data.day0_hourly_vectors.read_freshest_day0_hourly_vectors",
            lambda **kw: [vector],
        )
        monkeypatch.setattr(
            era,
            "_latest_day0_current_temperature_native",
            lambda **kw: (
                23.0,
                datetime(2026, 6, 10, 14, 0, tzinfo=UTC),  # local 16:00
                "wu_icao_history",
            ),
        )
        payload = {
            "metric": "high",
            "rounded_value": 24.0,
            "observation_time": "2026-06-10T13:00:00+00:00",
            "settlement_source": "wu_api",
        }

        members = era._day0_remaining_day_members(
            payload=payload,
            family=self._family(),
            unit="C",
            decision_time=datetime(2026, 6, 10, 14, 20, tzinfo=UTC),
            world_conn=object(),
        )

        assert members is not None
        expected = 24.0 - 3.0 * np.exp(-7.0 / 4.2)
        assert payload["_edli_day0_unclamped_remaining_extrema_native"] == (
            pytest.approx([expected])
        )
        assert members.tolist() == [24.0]
        assert payload["_edli_day0_model_innovations_c"] == {"ecmwf_ifs": -3.0}
        assert payload["_edli_day0_trajectory_conditioning_basis"] == (
            "current_state_exponential_residual_decay_v1"
        )
        assert payload["_edli_day0_current_state_innovation_e_fold_hours"] == 4.2
        assert payload["_edli_day0_remaining_window_start_utc"] == (
            "2026-06-10T14:00:00+00:00"
        )

    def test_live_members_exclude_the_observed_model_grid_point(self, monkeypatch):
        """The grid point used as the state anchor is not future support."""
        import src.engine.event_reactor_adapter as era

        vector = Day0HourlyVector(
            model="ecmwf_ifs",
            city="Paris",
            target_date="2026-06-10",
            timezone_name="Europe/Paris",
            captured_at="2026-06-10T12:30:00+00:00",
            times=tuple(f"2026-06-10T{hour:02d}:00" for hour in range(24)),
            temps_c=tuple(30.0 if hour == 16 else 20.0 for hour in range(24)),
        )
        monkeypatch.setattr(era, "runtime_cities_by_name", lambda: {"Paris": _paris()})
        monkeypatch.setattr(
            "src.data.day0_hourly_vectors.read_freshest_day0_hourly_vectors",
            lambda **kw: [vector],
        )
        monkeypatch.setattr(
            era,
            "_latest_day0_current_temperature_native",
            lambda **kw: (
                20.0,
                datetime(2026, 6, 10, 14, 0, tzinfo=UTC),
                "wu_icao_history",
            ),
        )

        members = era._day0_remaining_day_members(
            payload={
                "metric": "high",
                "rounded_value": 25.0,
                "observation_time": "2026-06-10T13:00:00+00:00",
            },
            family=self._family(),
            unit="C",
            decision_time=datetime(2026, 6, 10, 14, 20, tzinfo=UTC),
            world_conn=object(),
        )

        assert members is not None
        # The elapsed 30C model anchor is excluded. Its -10C residual is carried
        # into unseen hours but decays, so it cannot become a permanent shift.
        assert members.tolist() == pytest.approx(
            [20.0 - 10.0 * np.exp(-7.0 / 4.2)]
        )

    def test_current_state_diagnostic_is_persisted_in_probability_authority(self):
        import src.engine.event_reactor_adapter as era

        authority = era._global_day0_probability_authority_payload(
            {
                "_edli_global_day0_binding": {
                    "probability_base_identity": "base-1",
                },
                "probability_authority": "day0_remaining_day_global_probability_v1",
                "q_source": "day0_remaining_day",
                "_edli_day0_q_mode": "remaining_day",
                "_edli_day0_current_temperature_native": 23.0,
                "_edli_day0_current_temperature_observed_at_utc": (
                    "2026-07-21T20:00:00+00:00"
                ),
                "_edli_day0_current_temperature_source": "wu_icao_history",
                "_edli_day0_trajectory_conditioning_basis": (
                    "current_state_exponential_residual_decay_v1"
                ),
                "_edli_day0_model_innovations_c": {
                    "ecmwf_ifs": -1.3,
                    "icon_global": -3.3,
                },
                "_edli_day0_current_state_innovation_e_fold_hours": 4.2,
            }
        )

        assert authority["current_temperature_native"] == 23.0
        assert authority["current_temperature_observed_at_utc"] == (
            "2026-07-21T20:00:00+00:00"
        )
        assert authority["current_temperature_source"] == "wu_icao_history"
        assert authority["trajectory_conditioning_basis"] == (
            "current_state_exponential_residual_decay_v1"
        )
        assert authority["model_innovations_c"] == {
            "ecmwf_ifs": -1.3,
            "icon_global": -3.3,
        }
        assert authority["current_state_innovation_e_fold_hours"] == 4.2

    def test_f_city_members_are_converted_at_the_seam(self, monkeypatch):
        vectors = [_vector(model="ncep_nbm_conus", temps=[25.0] * 24)]
        monkeypatch.setattr(
            "src.data.day0_hourly_vectors.read_freshest_day0_hourly_vectors",
            lambda **kw: vectors,
        )
        import src.engine.event_reactor_adapter as era

        members = era._day0_remaining_day_members(
            payload={"metric": "high", "rounded_value": 70.0}, family=self._family(),
            unit="F", decision_time=datetime(2026, 6, 10, 15, 0, tzinfo=UTC),
        )
        assert members is not None
        assert members[0] == pytest.approx(25.0 * 9 / 5 + 32)

    def test_no_vectors_returns_none_for_required_caller_to_block(self, monkeypatch):
        monkeypatch.setattr(
            "src.data.day0_hourly_vectors.read_freshest_day0_hourly_vectors",
            lambda **kw: [],
        )
        import src.engine.event_reactor_adapter as era

        assert era._day0_remaining_day_members(
            payload={"metric": "high", "rounded_value": 25.0}, family=self._family(),
            unit="C", decision_time=datetime(2026, 6, 10, 15, 0, tzinfo=UTC),
        ) is None

    def test_redecision_members_require_expected_hourly_bundle(self, monkeypatch):
        import src.engine.event_reactor_adapter as era
        import src.data.day0_hourly_vectors as hv

        captured = {}

        def fake_read(**kw):
            captured.update(kw)
            return []

        monkeypatch.setattr(era, "runtime_cities_by_name", lambda: {"Paris": _paris()})
        monkeypatch.setattr(hv, "day0_hourly_models_for_city", lambda city: ["icon_d2", "ecmwf_ifs"])
        monkeypatch.setattr(hv, "read_freshest_day0_hourly_vectors", fake_read)

        payload = {"metric": "high", "rounded_value": 25.0}
        forecast_conn = object()
        members = era._day0_remaining_day_members(
            payload=payload,
            family=self._family(),
            unit="C",
            decision_time=datetime(2026, 6, 10, 15, 0, tzinfo=UTC),
            forecast_conn=forecast_conn,
        )

        assert members is None
        assert captured["conn"] is forecast_conn
        assert captured["expected_models"] == ["icon_d2", "ecmwf_ifs"]
        assert captured["require_expected"] is True
        assert captured["max_bundle_skew_minutes"] == hv.DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES
        assert captured["remaining_window_start"] == datetime(
            2026, 6, 10, 15, 0, tzinfo=UTC
        )
        assert captured["require_complete_remaining_window"] is True
        assert payload["_edli_day0_remaining_unavailable_reason"] == "incomplete_hourly_model_bundle"

    def test_redecision_members_missing_city_config_blocks_before_vector_read(self, monkeypatch):
        import src.engine.event_reactor_adapter as era
        import src.data.day0_hourly_vectors as hv

        def fail_read(**kw):
            raise AssertionError("missing city config must not read an unscoped vector bundle")

        monkeypatch.setattr(era, "runtime_cities_by_name", lambda: {})
        monkeypatch.setattr(hv, "read_freshest_day0_hourly_vectors", fail_read)

        payload = {"metric": "high", "rounded_value": 25.0}
        members = era._day0_remaining_day_members(
            payload=payload,
            family=self._family(),
            unit="C",
            decision_time=datetime(2026, 6, 10, 15, 0, tzinfo=UTC),
        )

        assert members is None
        assert payload["_edli_day0_remaining_unavailable_reason"] == "city_config_missing_for_hourly_bundle"


    def test_hko_provisional_day0_event_uses_replacement_probability_path(
        self,
        monkeypatch,
    ):
        import src.engine.event_reactor_adapter as era

        payload = {
            "city": "Hong Kong",
            "target_date": "2026-07-20",
            "metric": "low",
            "rounded_value": 25,
            "observation_time": "2026-07-20T07:20:00+00:00",
            "settlement_source": "hko_hourly_accumulator",
            "evidence_finality": "PROVISIONAL_CURRENT_SNAPSHOT",
            "source_match_status": "MATCH",
            "local_date_status": "MATCH",
            "station_match_status": "MATCH",
            "dst_status": "UNAMBIGUOUS",
            "metric_match_status": "MATCH",
            "rounding_status": "MATCH",
            "source_authorized_status": "AUTHORIZED",
            "live_authority_status": "live",
        }

        replacement = (
            {"condition": 0.73},
            {},
            {},
            {},
            {"probability_authority": "replacement_0_1"},
        )
        calls = []
        bundle = SimpleNamespace(
            posterior_id=77,
            provenance_json={
                "day0_provisional_observation": {
                    "active": True,
                    "support_truncation": False,
                    "source": "hko_hourly_accumulator",
                    "observation_time": "2026-07-20T07:20:00+00:00",
                    "observed_extreme_c": 25.0,
                }
            },
        )

        def replacement_probability(**kwargs):
            calls.append(kwargs)
            kwargs["payload"]["_edli_spine_posterior_id"] = 77
            kwargs["payload"]["_edli_spine_posterior_identity_hash"] = (
                "posterior-77"
            )
            kwargs["provenance_capture"]["replacement_bundle"] = bundle
            return replacement

        monkeypatch.setattr(
            era,
            "_replacement_authority_probability_and_fdr_proof",
            replacement_probability,
        )

        def current_observation(**kwargs):
            binding = {
                "city": "Hong Kong",
                "target_date": "2026-07-20",
                "metric": "low",
                "observation_time": "2026-07-20T07:20:00+00:00",
                "observation_available_at": "2026-07-20T07:30:00+00:00",
                "observed_extreme_native": 25.0,
                "rounded_value": 25,
                "sample_count": 8,
                "station_id": "HKO",
                "settlement_source": "hko_hourly_accumulator",
                "settlement_unit": "C",
                "evidence_finality": "PROVISIONAL_CURRENT_SNAPSHOT",
            }
            if kwargs["posterior_id"] is not None:
                binding["posterior_id"] = kwargs["posterior_id"]
            binding["probability_base_identity"] = kwargs[
                "probability_base_identity"
            ]
            return {
                "city": "Hong Kong",
                "target_date": "2026-07-20",
                "metric": "low",
                "observation_time": binding["observation_time"],
                "observation_available_at": binding["observation_available_at"],
                "raw_value": 25.0,
                "rounded_value": 25,
                "low_so_far": 25.0,
                "sample_count": 8,
                "samples_count": 8,
                "station_id": "HKO",
                "settlement_source": "hko_hourly_accumulator",
                "settlement_unit": "C",
                "evidence_finality": "PROVISIONAL_CURRENT_SNAPSHOT",
                "source_match_status": "MATCH",
                "local_date_status": "MATCH",
                "station_match_status": "MATCH",
                "dst_status": "UNAMBIGUOUS",
                "metric_match_status": "MATCH",
                "rounding_status": "MATCH",
                "source_authorized_status": "AUTHORIZED",
                "live_authority_status": "live",
                "_edli_global_day0_binding": binding,
            }

        monkeypatch.setattr(
            era,
            "_global_day0_execution_payload",
            lambda *args, **kwargs: current_observation(**kwargs),
        )

        result = era._live_yes_probabilities(
            event=SimpleNamespace(event_type="DAY0_EXTREME_UPDATED"),
            payload=payload,
            family=SimpleNamespace(city="Hong Kong", target_date="2026-07-20"),
            conn=sqlite3.connect(":memory:"),
            calibration_conn=sqlite3.connect(":memory:"),
            native_costs={},
            decision_time=datetime(2026, 7, 20, 8, 0, tzinfo=UTC),
        )

        assert result is replacement
        assert len(calls) == 1
        assert payload["posterior_id"] == 77
        assert payload["day0_probability_authority"]["probability_authority"] == (
            "replacement_provisional_day0_global_probability_v1"
        )
        from src.events.day0_authority import (
            assert_live_day0_probability_authority,
        )

        assert_live_day0_probability_authority(
            payload,
            direction="buy_no",
            condition_id="condition",
            q_live=0.73,
            q_lcb=0.70,
        )

    def test_monitor_read_requires_expected_hourly_bundle(self, monkeypatch):
        import src.engine.monitor_refresh as monitor_refresh
        import src.data.day0_hourly_vectors as hv
        import src.state.db as db

        captured = {}

        def fake_read(**kw):
            captured.update(kw)
            return []

        monkeypatch.setattr(db, "get_forecasts_connection_read_only", lambda: sqlite3.connect(":memory:"))
        monkeypatch.setattr(hv, "day0_hourly_models_for_city", lambda city: ["icon_d2", "ecmwf_ifs"])
        monkeypatch.setattr(hv, "read_freshest_day0_hourly_vectors", fake_read)

        out = monitor_refresh._read_day0_hourly_vectors(
            city=_paris(),
            target_d=datetime(2026, 6, 10, tzinfo=UTC).date(),
            now=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            remaining_window_start=datetime(2026, 6, 10, 8, 0, tzinfo=UTC),
        )

        assert out is None
        assert captured["expected_models"] == ["icon_d2", "ecmwf_ifs"]
        assert captured["require_expected"] is True
        assert captured["max_bundle_skew_minutes"] == hv.DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES
        assert captured["remaining_window_start"] == datetime(
            2026, 6, 10, 8, 0, tzinfo=UTC
        )
        assert captured["require_complete_remaining_window"] is True

    def test_monitor_normalizes_local_hours_before_remaining_window_cut(
        self,
        monkeypatch,
    ):
        import src.data.day0_hourly_vectors as hv
        import src.engine.monitor_refresh as monitor_refresh
        import src.state.db as db
        from src.signal.day0_window import remaining_member_extrema_for_day0
        from src.types.metric_identity import HIGH_LOCALDAY_MAX

        temps = [10.0] * 24
        temps[13] = 99.0
        vector = _vector(model="ecmwf_ifs", temps=temps)
        monkeypatch.setattr(
            db,
            "get_forecasts_connection_read_only",
            lambda: sqlite3.connect(":memory:"),
        )
        monkeypatch.setattr(
            hv,
            "day0_hourly_models_for_city",
            lambda city: ["ecmwf_ifs"],
        )
        monkeypatch.setattr(
            hv,
            "read_freshest_day0_hourly_vectors",
            lambda **kwargs: [vector],
        )

        boundary = datetime(2026, 6, 10, 12, 30, tzinfo=UTC)  # 14:30 Paris
        out = monitor_refresh._read_day0_hourly_vectors(
            city=_paris(),
            target_d=date(2026, 6, 10),
            now=boundary,
            remaining_window_start=boundary,
        )

        assert out is not None
        assert out["times"][13] == "2026-06-10T11:00:00+00:00"
        extrema, hours = remaining_member_extrema_for_day0(
            out["members_hourly"],
            out["times"],
            "Europe/Paris",
            date(2026, 6, 10),
            now=boundary,
            temperature_metric=HIGH_LOCALDAY_MAX,
        )
        assert extrema is not None
        assert extrema.maxes.tolist() == [10.0]
        assert hours == 9.0

        stale_observation = datetime(2026, 6, 10, 10, 30, tzinfo=UTC)
        stale_out = monitor_refresh._read_day0_hourly_vectors(
            city=_paris(),
            target_d=date(2026, 6, 10),
            now=boundary,
            remaining_window_start=stale_observation,
        )
        assert stale_out is not None
        stale_extrema, stale_hours = remaining_member_extrema_for_day0(
            stale_out["members_hourly"],
            stale_out["times"],
            "Europe/Paris",
            date(2026, 6, 10),
            now=stale_observation,
            temperature_metric=HIGH_LOCALDAY_MAX,
        )
        assert stale_extrema is not None
        assert stale_extrema.maxes.tolist() == [99.0]
        assert stale_hours == 11.0

    def test_monitor_normalizes_both_fall_back_folds_to_distinct_utc_instants(
        self,
        monkeypatch,
    ):
        import src.data.day0_hourly_vectors as hv
        import src.engine.monitor_refresh as monitor_refresh
        import src.state.db as db

        times = tuple(
            ["2026-10-25T00:00", "2026-10-25T01:00"]
            + ["2026-10-25T02:00", "2026-10-25T02:00"]
            + [f"2026-10-25T{hour:02d}:00" for hour in range(3, 24)]
        )
        vector = Day0HourlyVector(
            model="ecmwf_ifs",
            city="Paris",
            target_date="2026-10-25",
            timezone_name="Europe/Paris",
            captured_at="2026-10-25T00:00:00+00:00",
            times=times,
            temps_c=tuple(float(i) for i in range(25)),
        )
        monkeypatch.setattr(
            db,
            "get_forecasts_connection_read_only",
            lambda: sqlite3.connect(":memory:"),
        )
        monkeypatch.setattr(
            hv,
            "day0_hourly_models_for_city",
            lambda city: ["ecmwf_ifs"],
        )
        monkeypatch.setattr(
            hv,
            "read_freshest_day0_hourly_vectors",
            lambda **kwargs: [vector],
        )

        out = monitor_refresh._read_day0_hourly_vectors(
            city=_paris(),
            target_d=date(2026, 10, 25),
            now=datetime(2026, 10, 25, 12, 0, tzinfo=UTC),
            remaining_window_start=datetime(2026, 10, 24, 22, 0, tzinfo=UTC),
        )

        assert out is not None
        assert out["times"][2:4] == [
            "2026-10-25T00:00:00+00:00",
            "2026-10-25T01:00:00+00:00",
        ]

    def test_live_remaining_day_unavailable_blocks_before_legacy_fallback(self, monkeypatch):
        """When live Day0 remaining-day mode is enabled, missing vectors are an
        input fault. The q seam must not continue into bias/Platt full-day q."""
        import src.engine.event_reactor_adapter as era

        bins = [Bin(25, 25, "C", "25°C"), Bin(26, None, "C", "26°C or higher")]
        candidates = [
            SimpleNamespace(
                condition_id=f"cond-{i}",
                bin=b,
                yes_token_id=f"yes-{i}",
                no_token_id=f"no-{i}",
            )
            for i, b in enumerate(bins)
        ]
        family = SimpleNamespace(
            city="Paris",
            metric="high",
            target_date="2026-06-10",
            event_type="DAY0_EXTREME_UPDATED",
            bins=bins,
            candidates=candidates,
            yes_token_ids=[f"yes-{i}" for i in range(len(bins))],
            no_token_ids=[f"no-{i}" for i in range(len(bins))],
            family_id="day0-test-fam",
        )
        native_costs = {
            (f"cond-{i}", side): (
                None,
                EP(price, "ask", fee_deducted=True, currency="probability_units"),
                price,
                None,
                None,
            )
            for i in range(len(bins))
            for side, price in (("buy_yes", 0.25), ("buy_no", 0.75))
        }
        payload = {"metric": "high", "rounded_value": 25.0}
        snapshot = {
            "settlement_unit": "C",
            "temperature_metric": "high",
            "members_json": "[24.0, 25.0, 26.0, 27.0]",
            "members_precision": 1.0,
            "source_id": "test",
            "issue_time": "2026-06-10T00:00:00+00:00",
            "dataset_id": "test_v1",
            "data_version": "test_v1",
        }

        monkeypatch.setattr(era, "_day0_remaining_day_q_enabled", lambda: True)
        monkeypatch.setattr(era, "_day0_remaining_day_members", lambda **kw: None)

        assert not hasattr(era, "_maybe_apply_edli_bias_correction")

        with pytest.raises(ValueError, match="DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE"):
            era._market_analysis_from_event_snapshot(
                calibration_conn=sqlite3.connect(":memory:"),
                snapshot=snapshot,
                family=family,
                native_costs=native_costs,
                payload=payload,
                decision_time=datetime(2026, 6, 10, 15, 0, tzinfo=UTC),
            )
        assert payload["_edli_day0_q_mode"] == "remaining_day_unavailable"
        assert payload["_edli_day0_q_block_reason"] == "DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE"

    def test_live_remaining_day_bootstrap_lcb_unavailable_blocks_static_fallback(self, monkeypatch):
        """A live Day0 q_lcb must not degrade to the static sampler.

        The static sampler makes q_lcb numerically equal to q_live, which later
        looks like a high-quality YES edge before submit-time authority rejects it.
        """
        import src.engine.event_reactor_adapter as era

        bins = [Bin(35, 35, "C", "35°C"), Bin(36, None, "C", "36°C or higher")]
        candidates = [
            SimpleNamespace(
                condition_id=f"cond-{i}",
                bin=b,
                yes_token_id=f"yes-{i}",
                no_token_id=f"no-{i}",
            )
            for i, b in enumerate(bins)
        ]
        family = SimpleNamespace(
            city="Wuhan",
            metric="high",
            target_date="2026-07-08",
            event_type="DAY0_EXTREME_UPDATED",
            bins=bins,
            candidates=candidates,
            yes_token_ids=[f"yes-{i}" for i in range(len(bins))],
            no_token_ids=[f"no-{i}" for i in range(len(bins))],
            family_id="day0-bootstrap-lcb-unavailable",
        )
        native_costs = {
            (f"cond-{i}", side): (
                None,
                EP(price, "ask", fee_deducted=True, currency="probability_units"),
                price,
                None,
                None,
            )
            for i in range(len(bins))
            for side, price in (("buy_yes", 0.80), ("buy_no", 0.20))
        }
        payload = {
            "metric": "high",
            "rounded_value": 35.0,
            "observation_time": "2026-07-08T09:00:00+00:00",
            "_edli_day0_post_peak_confidence": 0.75,
        }
        snapshot = {
            "settlement_unit": "C",
            "temperature_metric": "high",
            "members_json": "[34.0, 35.0, 36.0, 37.0]",
            "members_precision": 1.0,
            "source_id": "test",
            "issue_time": "2026-07-08T00:00:00+00:00",
            "dataset_id": "test_v1",
            "data_version": "test_v1",
        }

        monkeypatch.setattr(era, "_day0_remaining_day_q_enabled", lambda: True)
        monkeypatch.setattr(era, "_day0_remaining_day_members", lambda **kw: np.array([35.0, 35.0, 36.0]))
        monkeypatch.setattr(era, "_make_day0_bootstrap_sampler", lambda **kw: None)

        with pytest.raises(ValueError, match="DAY0_BOOTSTRAP_LCB_UNAVAILABLE"):
            era._market_analysis_from_event_snapshot(
                calibration_conn=sqlite3.connect(":memory:"),
                snapshot=snapshot,
                family=family,
                native_costs=native_costs,
                payload=payload,
                decision_time=datetime(2026, 7, 8, 9, 8, tzinfo=UTC),
            )
        assert payload["_edli_day0_q_block_reason"] == "DAY0_BOOTSTRAP_LCB_UNAVAILABLE"

    def test_live_day0_payload_blocks_without_family_event_type(self, monkeypatch):
        """The q seam must recognize Day0 from the live observation payload.

        Live market-family objects are rebuilt from market topology and may not
        carry event_type.  A live Day0 observation payload still has to require
        remaining-day vectors; otherwise the seam falls back to full-day masked
        q and overprices the observed boundary bin.
        """
        import src.engine.event_reactor_adapter as era

        bins = [Bin(25, 25, "C", "25°C"), Bin(26, None, "C", "26°C or higher")]
        candidates = [
            SimpleNamespace(
                condition_id=f"cond-{i}",
                bin=b,
                yes_token_id=f"yes-{i}",
                no_token_id=f"no-{i}",
            )
            for i, b in enumerate(bins)
        ]
        family = SimpleNamespace(
            city="Paris",
            metric="high",
            target_date="2026-06-10",
            bins=bins,
            candidates=candidates,
            yes_token_ids=[f"yes-{i}" for i in range(len(bins))],
            no_token_ids=[f"no-{i}" for i in range(len(bins))],
            family_id="day0-no-event-type-fam",
        )
        native_costs = {
            (f"cond-{i}", side): (
                None,
                EP(price, "ask", fee_deducted=True, currency="probability_units"),
                price,
                None,
                None,
            )
            for i in range(len(bins))
            for side, price in (("buy_yes", 0.25), ("buy_no", 0.75))
        }
        payload = {
            "metric": "high",
            "rounded_value": 25,
            "raw_value": 25.0,
            "observation_time": "2026-06-10T13:00:00+00:00",
            "live_authority_status": "live",
            "source_authorized_status": "AUTHORIZED",
        }
        snapshot = {
            "settlement_unit": "C",
            "temperature_metric": "high",
            "members_json": "[24.0, 25.0, 26.0, 27.0]",
            "members_precision": 1.0,
            "source_id": "test",
            "issue_time": "2026-06-10T00:00:00+00:00",
            "dataset_id": "test_v1",
            "data_version": "test_v1",
        }

        monkeypatch.setattr(era, "_day0_remaining_day_q_enabled", lambda: True)
        monkeypatch.setattr(era, "_day0_remaining_day_members", lambda **kw: None)

        assert not hasattr(era, "_maybe_apply_edli_bias_correction")

        with pytest.raises(ValueError, match="DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE"):
            era._market_analysis_from_event_snapshot(
                calibration_conn=sqlite3.connect(":memory:"),
                snapshot=snapshot,
                family=family,
                native_costs=native_costs,
                payload=payload,
                decision_time=datetime(2026, 6, 10, 13, 5, tzinfo=UTC),
            )

    def test_day0_probability_clock_is_stable_inside_one_current_truth_cut(self):
        import src.engine.event_reactor_adapter as era

        first = datetime(2026, 6, 10, 12, 0, 1, tzinfo=UTC)
        later = datetime(2026, 6, 10, 12, 0, 59, 999999, tzinfo=UTC)
        next_cut = datetime(2026, 6, 10, 12, 1, 0, tzinfo=UTC)

        assert era._day0_probability_clock(first) == era._day0_probability_clock(later)
        assert era._day0_probability_clock(next_cut) > era._day0_probability_clock(later)

        payload = {"metric": "high", "observation_time": "2026-06-10T10:00:00+00:00"}
        family = SimpleNamespace(city="unknown-test-city")
        first_sigma = era._day0_process_sigma_native(
            payload=dict(payload),
            family=family,
            unit="C",
            decision_time=era._day0_probability_clock(first),
        )
        later_sigma = era._day0_process_sigma_native(
            payload=dict(payload),
            family=family,
            unit="C",
            decision_time=era._day0_probability_clock(later),
        )
        next_sigma = era._day0_process_sigma_native(
            payload=dict(payload),
            family=family,
            unit="C",
            decision_time=era._day0_probability_clock(next_cut),
        )

        assert first_sigma == later_sigma
        assert next_sigma > later_sigma


# ===========================================================================
# R22 — replayable provenance identity on persisted vectors (PR#404 P1)
# ===========================================================================

class TestRequestHashProvenance:
    @pytest.fixture(autouse=True)
    def _isolate_refresh_state(self):
        import src.data.day0_hourly_vectors as hv

        state = (
            hv._LAST_REFRESH_MONOTONIC,
            hv._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC,
            hv._INCOMPLETE_RETRY_STREAK,
        )
        for values in state:
            values.clear()
        yield
        for values in state:
            values.clear()

    def test_persisted_rows_carry_non_empty_request_hash(self):
        conn = _conn()
        v = _vector()
        persist_day0_hourly_vectors(
            [v], target_date="2026-06-10", conn=conn, request_hash="sha256:abc123", now=PRUNE_NOW
        )
        rows = conn.execute("SELECT request_hash FROM day0_hourly_vectors").fetchall()
        assert rows and all(r[0] == "sha256:abc123" for r in rows)

    def test_empty_request_hash_is_rejected_in_code_and_schema(self):
        conn = _conn()
        v = _vector()
        with pytest.raises(ValueError, match="request_hash"):
            persist_day0_hourly_vectors(
                [v], target_date="2026-06-10", conn=conn, request_hash=""
            )
        # schema-level CHECK on fresh DBs (defense in depth)
        from src.data.day0_hourly_vectors import _ensure_schema

        _ensure_schema(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO day0_hourly_vectors (vector_id, model, city, target_date,"
                " timezone_name, captured_at, provider, endpoint, request_hash,"
                " times_json, temps_c_json, source_run_meta_json)"
                " VALUES ('x','m','c','d','tz','t','openmeteo','e','','[]','[]',NULL)"
            )

    def test_request_hash_is_replayable_and_idempotent(self):
        from src.data.day0_hourly_vectors import build_request_hash

        kwargs = dict(
            endpoint="https://api.open-meteo.com/v1/forecast",
            params={"latitude": 48.8566, "longitude": 2.3522, "models": "icon_d2"},
            models=["icon_d2"],
            captured_at="2026-06-10T09:00:12+00:00",
            payload={"hourly": {"time": ["2026-06-10T00:00"], "temperature_2m": [15.1]}},
        )
        h1 = build_request_hash(**kwargs)
        h2 = build_request_hash(**kwargs)
        assert h1 == h2 and h1.startswith("sha256:") and len(h1) > 20
        # any input change changes the identity
        changed = dict(kwargs, models=["meteofrance_arome_france_hd"])
        assert build_request_hash(**changed) != h1
        changed_payload = dict(kwargs, payload={"hourly": {"time": [], "temperature_2m": []}})
        assert build_request_hash(**changed_payload) != h1

    def test_refresh_pass_threads_real_hash(self, monkeypatch):
        """maybe_refresh persists with the fetch's request hash, never ''."""
        import src.data.day0_hourly_vectors as hv

        captured = {"target_dates": []}

        def fake_fetch(city, *, models=None, now=None):
            return [
                _refresh_vector(city, model, now) for model in models
            ], "sha256:realhash"

        def fake_persist(vectors, *, target_date, request_hash, **kw):
            captured["request_hash"] = request_hash
            captured["target_dates"].append(target_date)
            return len(vectors)

        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", fake_fetch)
        monkeypatch.setattr(hv, "persist_day0_hourly_vectors", fake_persist)
        monkeypatch.setattr(hv, "read_freshest_day0_hourly_vectors", lambda **_kw: [object()])
        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: ["icon_d2"])
        monkeypatch.setattr(hv.time, "monotonic", lambda: 60.0)
        hv._LAST_REFRESH_MONOTONIC.clear()
        n = hv.maybe_refresh_day0_hourly_vectors(
            [_paris()], decision_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
        )
        assert n == 8
        assert captured["request_hash"] == "sha256:realhash"
        assert captured["target_dates"] == ["2026-06-10", "2026-06-11"]

    def test_refresh_lock_contention_does_not_throttle_next_attempt(self, monkeypatch):
        """A contended forecasts writer lock must not stall the trading reactor lane."""
        import src.data.day0_hourly_vectors as hv

        attempts = {"fetch": 0, "persist": 0}

        def fake_fetch(city, *, models=None, now=None, timeout_s=None):
            attempts["fetch"] += 1
            return [
                _refresh_vector(city, model, now) for model in models
            ], "sha256:realhash"

        def fake_persist(vectors, *, target_date, request_hash, **kw):
            attempts["persist"] += 1
            assert kw["lock_blocking"] is False
            raise BlockingIOError("forecasts writer lock held")

        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", fake_fetch)
        monkeypatch.setattr(hv, "persist_day0_hourly_vectors", fake_persist)
        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: ["icon_d2"])
        hv._LAST_REFRESH_MONOTONIC.clear()

        decision_time = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
        n1 = hv.maybe_refresh_day0_hourly_vectors(
            [_paris()],
            decision_time=decision_time,
            persist_lock_blocking=False,
        )
        n2 = hv.maybe_refresh_day0_hourly_vectors(
            [_paris()],
            decision_time=decision_time + timedelta(seconds=1),
            persist_lock_blocking=False,
        )

        assert (n1, n2) == (0, 0)
        assert attempts == {"fetch": 2, "persist": 2}

    def test_empty_fetch_result_is_throttled_to_prevent_retry_storm(self, monkeypatch):
        """Transport/shape soft-failures must not spend quota every scheduler pass."""
        import src.data.day0_hourly_vectors as hv

        attempts = {"fetch": 0, "persist": 0}

        def fake_fetch(city, *, models=None, now=None, timeout_s=None):
            attempts["fetch"] += 1
            return [], ""

        def fake_persist(vectors, *, target_date, request_hash, **kw):
            attempts["persist"] += 1
            return len(vectors)

        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", fake_fetch)
        monkeypatch.setattr(hv, "persist_day0_hourly_vectors", fake_persist)
        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: ["icon_d2"])
        hv._LAST_REFRESH_MONOTONIC.clear()

        decision_time = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
        n1 = hv.maybe_refresh_day0_hourly_vectors(
            [_paris()],
            decision_time=decision_time,
            interval_s=1800.0,
        )
        n2 = hv.maybe_refresh_day0_hourly_vectors(
            [_paris()],
            decision_time=decision_time + timedelta(seconds=1),
            interval_s=1800.0,
        )

        assert (n1, n2) == (0, 0)
        assert attempts == {"fetch": 1, "persist": 0}

    @pytest.mark.parametrize("priority_cities", (0, 1))
    def test_transport_failure_retry_follows_capital_priority(
        self, monkeypatch, priority_cities
    ):
        """Current-authority gaps retry quickly; background failures stay throttled."""
        import src.data.day0_hourly_vectors as hv

        clock = {"now": 60.0}
        attempts = {"fetch": 0}

        def unavailable(*_args, **_kwargs):
            attempts["fetch"] += 1
            return [], ""

        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: [])
        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", unavailable)
        monkeypatch.setattr(hv.time, "monotonic", lambda: clock["now"])

        decision_time = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
        first = hv.maybe_refresh_day0_hourly_vectors(
            [_paris()],
            decision_time=decision_time,
            interval_s=1800.0,
            quota_priority_cities=priority_cities,
            return_stats=True,
        )

        refresh_key = "Paris|2026-06-10"
        assert first.unavailable_bundles[0].reason == (
            "DAY0_HOURLY_BUNDLE_FETCH_UNAVAILABLE"
        )
        assert first.incomplete_expected_bundles == priority_cities
        if priority_cities:
            assert refresh_key not in hv._LAST_REFRESH_MONOTONIC
            assert hv._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC[refresh_key] == (
                clock["now"] + hv.INCOMPLETE_BUNDLE_RETRY_INTERVAL_S
            )
        else:
            assert refresh_key in hv._LAST_REFRESH_MONOTONIC
            assert refresh_key not in hv._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC

        clock["now"] += hv.INCOMPLETE_BUNDLE_RETRY_INTERVAL_S
        second = hv.maybe_refresh_day0_hourly_vectors(
            [_paris()],
            decision_time=decision_time + timedelta(
                seconds=hv.INCOMPLETE_BUNDLE_RETRY_INTERVAL_S
            ),
            interval_s=1800.0,
            quota_priority_cities=priority_cities,
            return_stats=True,
        )
        assert second.cities_skipped_throttle == (0 if priority_cities else 1)
        assert attempts == {"fetch": 1 + priority_cities}

    @pytest.mark.parametrize(
        ("city_name", "timezone_name"),
        [
            ("Warsaw", "Europe/Warsaw"),
            ("Sao Paulo", "America/Sao_Paulo"),
        ],
    )
    @pytest.mark.parametrize(
        ("first_interval_s", "next_interval_s"),
        [(1800.0, 300.0), (300.0, 1800.0)],
        ids=["interval_1800_to_300", "interval_300_to_1800"],
    )
    def test_partial_expected_bundle_returns_typed_unavailable_and_retries_soon(
        self,
        monkeypatch,
        city_name,
        timezone_name,
        first_interval_s,
        next_interval_s,
    ):
        """Held-city partial bundles retry inside freshness law, never persist partials."""
        import src.data.day0_hourly_vectors as hv

        attempts = {"fetch": 0, "persist": 0}
        clock = {"now": 60.0}
        city = SimpleNamespace(
            name=city_name,
            timezone=timezone_name,
            lat=0.0,
            lon=0.0,
        )

        def fake_fetch(city, *, models=None, now=None, timeout_s=None):
            attempts["fetch"] += 1
            assert list(models or []) == [
                "ecmwf_ifs",
                "icon_global",
                "ukmo_global_deterministic_10km",
            ]
            if attempts["fetch"] == 1:
                return [_vector(model="ecmwf_ifs")], "sha256:partial"
            return [
                _refresh_vector(city, model, now) for model in models
            ], "sha256:complete"

        def fake_persist(vectors, *, target_date, request_hash, **kw):
            attempts["persist"] += 1
            return len(vectors)

        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: [])
        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", fake_fetch)
        monkeypatch.setattr(hv, "persist_day0_hourly_vectors", fake_persist)
        monkeypatch.setattr(hv, "read_freshest_day0_hourly_vectors", lambda **_kw: [object()])
        monkeypatch.setattr(hv.time, "monotonic", lambda: clock["now"])
        hv._LAST_REFRESH_MONOTONIC.clear()
        hv._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.clear()
        hv._INCOMPLETE_RETRY_STREAK.clear()

        decision_time = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
        first = hv.maybe_refresh_day0_hourly_vectors(
            [city],
            decision_time=decision_time,
            interval_s=first_interval_s,
            return_stats=True,
        )
        assert first.vectors_written == 0
        assert first.incomplete_expected_bundles == 1
        assert len(first.unavailable_bundles) == 1
        unavailable = first.unavailable_bundles[0]
        assert unavailable.city == city_name
        assert unavailable.reason == "DAY0_HOURLY_BUNDLE_INCOMPLETE"
        assert unavailable.available_models == ("ecmwf_ifs",)
        assert unavailable.missing_models == (
            "icon_global",
            "ukmo_global_deterministic_10km",
        )
        assert attempts == {"fetch": 1, "persist": 0}
        refresh_key = f"{city_name}|2026-06-10"
        assert refresh_key not in hv._LAST_REFRESH_MONOTONIC
        assert hv._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC[refresh_key] == (
            clock["now"] + hv.INCOMPLETE_BUNDLE_RETRY_INTERVAL_S
        )

        clock["now"] += 1.0
        too_soon = hv.maybe_refresh_day0_hourly_vectors(
            [city],
            decision_time=decision_time + timedelta(seconds=1.0),
            interval_s=next_interval_s,
            return_stats=True,
        )
        assert too_soon.vectors_written == 0
        assert too_soon.cities_skipped_throttle == 1
        assert attempts == {"fetch": 1, "persist": 0}

        clock["now"] += hv.INCOMPLETE_BUNDLE_RETRY_INTERVAL_S
        second = hv.maybe_refresh_day0_hourly_vectors(
            [city],
            decision_time=decision_time + timedelta(
                seconds=hv.INCOMPLETE_BUNDLE_RETRY_INTERVAL_S + 1.0
            ),
            interval_s=next_interval_s,
            return_stats=True,
        )

        assert second.vectors_written == 6
        assert second.unavailable_bundles == ()
        assert attempts == {"fetch": 2, "persist": 2}
        assert refresh_key not in hv._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC
        assert refresh_key not in hv._INCOMPLETE_RETRY_STREAK

    @pytest.mark.parametrize(
        ("critical_count", "priority_count", "expected_cap"),
        (
            (1, 0, 600.0),
            (0, 1, 600.0),
            (0, 0, 1800.0),
        ),
        ids=("held-capital", "missing-authority", "ordinary-authority"),
    )
    def test_repeated_incomplete_bundle_backs_off_without_quota_storm(
        self, monkeypatch, critical_count, priority_count, expected_cap
    ):
        """No-change partial bundles cannot consume the Day0 quota every 45 seconds."""
        import src.data.day0_hourly_vectors as hv

        city = SimpleNamespace(name="Tokyo", timezone="Asia/Tokyo", lat=0.0, lon=0.0)
        clock = {"now": 60.0}
        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: [])
        monkeypatch.setattr(
            hv,
            "fetch_day0_hourly_vectors",
            lambda city, *, models, now, **kw: ([_vector(model="ecmwf_ifs")], "partial"),
        )
        monkeypatch.setattr(hv.time, "monotonic", lambda: clock["now"])
        hv._LAST_REFRESH_MONOTONIC.clear()
        hv._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.clear()
        hv._INCOMPLETE_RETRY_STREAK.clear()
        decision_time = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)

        for expected_streak in range(1, 8):
            stats = hv.maybe_refresh_day0_hourly_vectors(
                [city],
                decision_time=decision_time,
                quota_critical_cities=critical_count,
                quota_priority_cities=priority_count,
                return_stats=True,
            )
            key = "Tokyo|2026-06-10"
            expected_delay = min(
                expected_cap,
                hv.INCOMPLETE_BUNDLE_RETRY_INTERVAL_S * (2 ** (expected_streak - 1)),
            )
            assert stats.incomplete_expected_bundles == 1
            assert hv._INCOMPLETE_RETRY_STREAK[key] == expected_streak
            assert hv._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC[key] == (
                clock["now"] + expected_delay
            )
            clock["now"] += expected_delay

        assert expected_delay == expected_cap

    def test_held_promotion_clamps_ordinary_incomplete_retry_debt(
        self, monkeypatch
    ):
        import src.data.day0_hourly_vectors as hv

        city = SimpleNamespace(name="Tokyo", timezone="Asia/Tokyo", lat=0.0, lon=0.0)
        clock = {"now": 100.0}
        fetches = {"count": 0}

        def fetch(city, *, models, now, **_kwargs):
            fetches["count"] += 1
            return [_refresh_vector(city, model, now) for model in models], "complete"

        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: [])
        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", fetch)
        monkeypatch.setattr(hv, "persist_day0_hourly_vectors", lambda vectors, **kw: len(vectors))
        monkeypatch.setattr(hv, "read_freshest_day0_hourly_vectors", lambda **kw: [object()])
        monkeypatch.setattr(hv.time, "monotonic", lambda: clock["now"])
        hv._LAST_REFRESH_MONOTONIC.clear()
        hv._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.clear()
        hv._INCOMPLETE_RETRY_STREAK.clear()
        key = "Tokyo|2026-06-10"
        hv._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC[key] = 1900.0
        hv._INCOMPLETE_RETRY_STREAK[key] = 7
        decision_time = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)

        deferred = hv.maybe_refresh_day0_hourly_vectors(
            [city],
            decision_time=decision_time,
            quota_critical_cities=1,
            return_stats=True,
        )
        assert deferred.cities_skipped_throttle == 1
        assert fetches["count"] == 0
        assert hv._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC[key] == 700.0

        clock["now"] = 700.0
        completed = hv.maybe_refresh_day0_hourly_vectors(
            [city],
            decision_time=decision_time,
            quota_critical_cities=1,
            return_stats=True,
        )
        assert completed.vectors_written == 6
        assert fetches["count"] == 1
        assert key not in hv._INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC
        assert key not in hv._INCOMPLETE_RETRY_STREAK

    def test_strict_bundle_predicate_rejects_every_incomplete_shape_then_resets_after_persist(self):
        """Producer priority and authority readers share the exact strict predicate."""
        now = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
        window_start = datetime(2026, 6, 10, 8, 0, tzinfo=UTC)
        expected = ("icon_d2", "ecmwf_ifs")
        strict = dict(
            target_date="2026-06-10",
            now=now,
            expected_models=expected,
            require_expected=True,
            max_bundle_skew_minutes=60.0,
            remaining_window_start=window_start,
            require_complete_remaining_window=True,
        )
        complete = [
            _vector(model="icon_d2", captured_at=now),
            _vector(model="ecmwf_ifs", captured_at=now),
        ]

        assert select_ready_day0_hourly_vectors(complete[:1], **strict) == []
        assert select_ready_day0_hourly_vectors(
            [_vector(model=model, captured_at=now - timedelta(hours=4)) for model in expected],
            **strict,
        ) == []
        assert select_ready_day0_hourly_vectors(
            [
                _vector(model="icon_d2", captured_at=now),
                _vector(model="ecmwf_ifs", captured_at=now - timedelta(hours=2)),
            ],
            **strict,
        ) == []
        assert select_ready_day0_hourly_vectors(
            [_vector(model="icon_d2", captured_at=now, start_hour=20), complete[1]],
            **strict,
        ) == []

        conn = _conn()
        assert read_freshest_day0_hourly_vectors(city="Paris", conn=conn, **strict) == []
        assert persist_day0_hourly_vectors(
            complete,
            target_date="2026-06-10",
            request_hash="sha256:strict-reset",
            conn=conn,
            now=now,
        ) == 2
        ready = read_freshest_day0_hourly_vectors(city="Paris", conn=conn, **strict)
        assert [vector.model for vector in ready] == list(expected)

    def test_quota_block_stops_batch_without_fetch_or_throttle(self, monkeypatch):
        import src.data.day0_hourly_vectors as hv

        attempts = {"fetch": 0}

        def fake_fetch(city, *, models=None, now=None, timeout_s=None):
            attempts["fetch"] += 1
            return [], ""

        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", fake_fetch)
        monkeypatch.setattr(hv.quota_tracker, "can_call", lambda: False)
        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: [])
        hv._LAST_REFRESH_MONOTONIC.clear()

        decision_time = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
        stats = hv.maybe_refresh_day0_hourly_vectors(
            [_paris(), _wellington()],
            decision_time=decision_time,
            return_stats=True,
        )

        assert stats.cities_attempted == 0
        assert stats.cities_skipped_quota == 1
        assert attempts == {"fetch": 0}
        assert hv._LAST_REFRESH_MONOTONIC == {}

    def test_held_prefix_can_use_critical_quota_before_batch_stops(self, monkeypatch):
        import src.data.day0_hourly_vectors as hv
        from src.data.openmeteo_quota import (
            MAINTENANCE_DAILY_LIMIT,
            OpenMeteoQuotaTracker,
        )

        tracker = OpenMeteoQuotaTracker()
        tracker._count = MAINTENANCE_DAILY_LIMIT
        attempts: list[str] = []

        def fake_fetch(city, *, models=None, now=None, timeout_s=None):
            attempts.append(city.name)
            return [], ""

        monkeypatch.setattr(hv, "quota_tracker", tracker)
        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", fake_fetch)
        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: [])
        hv._LAST_REFRESH_MONOTONIC.clear()

        stats = hv.maybe_refresh_day0_hourly_vectors(
            [_paris(), _wellington()],
            decision_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            quota_critical_cities=1,
            return_stats=True,
        )

        assert attempts == ["Paris"]
        assert stats.cities_attempted == 1
        assert stats.cities_skipped_quota == 1

    def test_priority_prefix_drains_strict_bundle_at_maintenance_cap(self, monkeypatch):
        import src.data.day0_hourly_vectors as hv
        from src.data.openmeteo_quota import (
            MAINTENANCE_DAILY_LIMIT,
            OpenMeteoQuotaTracker,
        )

        tracker = OpenMeteoQuotaTracker()
        tracker._count = MAINTENANCE_DAILY_LIMIT
        persisted: list[tuple[str, tuple[str, ...]]] = []

        monkeypatch.setattr(hv, "quota_tracker", tracker)
        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: [])
        monkeypatch.setattr(
            hv,
            "fetch_day0_hourly_vectors",
            lambda city, *, models=None, **_kw: (
                [
                    _refresh_vector(
                        city,
                        model,
                        datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
                    )
                    for model in models
                ],
                "sha256:priority-complete",
            ),
        )
        monkeypatch.setattr(
            hv,
            "persist_day0_hourly_vectors",
            lambda vectors, *, target_date, **_kw: persisted.append(
                (target_date, tuple(vector.model for vector in vectors))
            )
            or len(vectors),
        )
        monkeypatch.setattr(hv, "read_freshest_day0_hourly_vectors", lambda **_kw: [object()])
        hv._LAST_REFRESH_MONOTONIC.clear()

        stats = hv.maybe_refresh_day0_hourly_vectors(
            [_paris()],
            decision_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            quota_priority_cities=1,
            return_stats=True,
        )

        assert stats.vectors_written == 6
        assert stats.priority_reserve_exhausted is False
        assert persisted == [
            ("2026-06-10", ("ecmwf_ifs", "icon_global", "ukmo_global_deterministic_10km")),
            ("2026-06-11", ("ecmwf_ifs", "icon_global", "ukmo_global_deterministic_10km")),
        ]

    def test_priority_reserve_exhaustion_is_visible_and_never_borrows_critical(self, monkeypatch):
        import src.data.day0_hourly_vectors as hv
        from src.data.openmeteo_quota import (
            PRIORITY_DAILY_LIMIT,
            OpenMeteoQuotaTracker,
        )

        tracker = OpenMeteoQuotaTracker()
        tracker._count = PRIORITY_DAILY_LIMIT
        monkeypatch.setattr(hv, "quota_tracker", tracker)
        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: [])
        monkeypatch.setattr(
            hv,
            "fetch_day0_hourly_vectors",
            lambda *_args, **_kw: pytest.fail("priority exhaustion must fail closed"),
        )
        hv._LAST_REFRESH_MONOTONIC.clear()

        stats = hv.maybe_refresh_day0_hourly_vectors(
            [_paris()],
            decision_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            quota_priority_cities=1,
            return_stats=True,
        )

        assert stats.priority_reserve_exhausted is True
        assert stats.cities_skipped_quota == 1
        assert tracker._count == PRIORITY_DAILY_LIMIT

    def test_priority_recovery_uses_bounded_lane_only_when_explicit(self, monkeypatch):
        import src.data.day0_hourly_vectors as hv
        from src.data.openmeteo_quota import (
            PRIORITY_DAILY_LIMIT,
            OpenMeteoQuotaTracker,
        )

        tracker = OpenMeteoQuotaTracker()
        tracker._count = PRIORITY_DAILY_LIMIT
        observed_lanes: list[tuple[bool, bool]] = []
        monkeypatch.setattr(hv, "quota_tracker", tracker)
        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: [])

        def fetch(city, *, models=None, **_kw):
            observed_lanes.append((tracker._is_recovery(), tracker._is_critical()))
            return (
                [
                    _refresh_vector(
                        city,
                        model,
                        datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
                    )
                    for model in models
                ],
                "sha256:bounded-recovery",
            )

        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", fetch)
        monkeypatch.setattr(
            hv,
            "persist_day0_hourly_vectors",
            lambda vectors, **_kw: len(vectors),
        )
        monkeypatch.setattr(
            hv,
            "read_freshest_day0_hourly_vectors",
            lambda **_kw: [object()],
        )
        hv._LAST_REFRESH_MONOTONIC.clear()

        stats = hv.maybe_refresh_day0_hourly_vectors(
            [_paris()],
            decision_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            quota_priority_cities=1,
            allow_priority_recovery=True,
            return_stats=True,
        )

        assert stats.vectors_written == 6
        assert stats.priority_reserve_exhausted is False
        assert observed_lanes == [(True, False)]

    def test_priority_recovery_keeps_normal_priority_lane_when_available(self, monkeypatch):
        import src.data.day0_hourly_vectors as hv
        from src.data.openmeteo_quota import OpenMeteoQuotaTracker

        tracker = OpenMeteoQuotaTracker()
        observed_lanes: list[tuple[bool, bool]] = []
        monkeypatch.setattr(hv, "quota_tracker", tracker)
        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: [])

        def fetch(city, *, models=None, **_kw):
            observed_lanes.append((tracker._is_priority(), tracker._is_recovery()))
            return (
                [
                    _refresh_vector(
                        city,
                        model,
                        datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
                    )
                    for model in models
                ],
                "sha256:normal-priority",
            )

        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", fetch)
        monkeypatch.setattr(
            hv,
            "persist_day0_hourly_vectors",
            lambda vectors, **_kw: len(vectors),
        )
        monkeypatch.setattr(
            hv,
            "read_freshest_day0_hourly_vectors",
            lambda **_kw: [object()],
        )
        hv._LAST_REFRESH_MONOTONIC.clear()

        stats = hv.maybe_refresh_day0_hourly_vectors(
            [_paris()],
            decision_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            quota_priority_cities=1,
            allow_priority_recovery=True,
            return_stats=True,
        )

        assert stats.vectors_written == 6
        assert observed_lanes == [(True, False)]

    def test_no_regional_model_uses_global_multimodel_bundle(self, monkeypatch):
        import src.data.day0_hourly_vectors as hv

        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: [])

        assert "jma_msm" in hv.DAY0_HOURLY_MODELS
        assert "jma_msm" not in hv.GLOBAL_DAY0_HOURLY_MODELS
        assert hv.day0_hourly_models_for_city(_paris()) == [
            "ecmwf_ifs",
            "icon_global",
            "ukmo_global_deterministic_10km",
        ]

    def test_regional_model_keeps_global_multimodel_bundle(self, monkeypatch):
        import src.data.day0_hourly_vectors as hv

        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: ["icon_d2"])

        assert hv.day0_hourly_models_for_city(_paris()) == [
            "icon_d2",
            "ecmwf_ifs",
            "icon_global",
            "ukmo_global_deterministic_10km",
        ]

    def test_refresh_uses_global_multimodel_bundle_when_no_regional_model(self, monkeypatch):
        import src.data.day0_hourly_vectors as hv

        captured = {"target_dates": []}

        def fake_fetch(city, *, models=None, now=None):
            captured["models"] = list(models or [])
            return [
                _refresh_vector(city, model, now) for model in models
            ], "sha256:globalhash"

        def fake_persist(vectors, *, target_date, request_hash, **kw):
            captured["request_hash"] = request_hash
            captured["vector_models"] = [v.model for v in vectors]
            captured["target_dates"].append(target_date)
            return len(vectors)

        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: [])
        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", fake_fetch)
        monkeypatch.setattr(hv, "persist_day0_hourly_vectors", fake_persist)
        monkeypatch.setattr(hv, "read_freshest_day0_hourly_vectors", lambda **_kw: [object()])
        hv._LAST_REFRESH_MONOTONIC.clear()

        n = hv.maybe_refresh_day0_hourly_vectors(
            [_paris()], decision_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
        )

        assert n == 6
        assert captured["models"] == [
            "ecmwf_ifs",
            "icon_global",
            "ukmo_global_deterministic_10km",
        ]
        assert captured["request_hash"] == "sha256:globalhash"
        assert captured["vector_models"] == [
            "ecmwf_ifs",
            "icon_global",
            "ukmo_global_deterministic_10km",
        ]
        assert captured["target_dates"] == ["2026-06-10", "2026-06-11"]

    def test_refresh_throttle_is_target_date_scoped_at_local_midnight(self, monkeypatch):
        import src.data.day0_hourly_vectors as hv

        captured_dates = []

        def fake_fetch(city, *, models=None, now=None, timeout_s=None):
            return [
                _refresh_vector(city, model, now) for model in models
            ], "sha256:datehash"

        def fake_persist(vectors, *, target_date, request_hash, **kw):
            captured_dates.append(target_date)
            return len(vectors)

        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: [])
        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", fake_fetch)
        monkeypatch.setattr(hv, "persist_day0_hourly_vectors", fake_persist)
        monkeypatch.setattr(hv, "read_freshest_day0_hourly_vectors", lambda **_kw: [object()])
        hv._LAST_REFRESH_MONOTONIC.clear()

        before_midnight_utc = datetime(2026, 6, 25, 11, 59, tzinfo=UTC)
        after_midnight_utc = datetime(2026, 6, 25, 12, 1, tzinfo=UTC)

        n1 = hv.maybe_refresh_day0_hourly_vectors(
            [_wellington()],
            decision_time=before_midnight_utc,
            interval_s=1800.0,
        )
        n2 = hv.maybe_refresh_day0_hourly_vectors(
            [_wellington()],
            decision_time=after_midnight_utc,
            interval_s=1800.0,
        )

        assert (n1, n2) == (6, 6)
        assert captured_dates == [
            "2026-06-25",
            "2026-06-26",
            "2026-06-26",
            "2026-06-27",
        ]

    def test_scheduler_orders_same_local_day_money_path_cities_first(self):
        # R4-b2 (2026-07-08 main.py slimming): day0-hourly-refresh cluster body
        # (including this exclusive helper) moved to src.events.reactor.
        from src.events import reactor

        ordered, priority_count = reactor._edli_order_day0_hourly_refresh_cities(
            [_paris(), _wellington()],
            decision_time=datetime(2026, 6, 25, 12, 47, tzinfo=UTC),
            priority_families=[("Wellington", "2026-06-26", "high")],
        )

        assert priority_count == 1
        assert [c.name for c in ordered] == ["Wellington", "Paris"]

    def test_scheduler_excludes_completed_local_day_from_priority_lane(self):
        from src.events import reactor

        ordered, priority_count = reactor._edli_order_day0_hourly_refresh_cities(
            [_paris(), _wellington()],
            decision_time=datetime(2026, 6, 25, 12, 47, tzinfo=UTC),
            priority_families=[("Wellington", "2026-06-25", "high")],
        )

        assert priority_count == 0
        assert [city.name for city in ordered] == ["Paris", "Wellington"]

    def test_authorized_current_day_missing_bundle_enters_priority_then_clears_after_persist(
        self, monkeypatch
    ):
        """No health gate: the producer reuses the strict authority reader directly."""
        import src.config as config_module
        import src.data.day0_hourly_vectors as vectors_module
        import src.data.replacement_forecast_current_target_plan as target_plan
        import src.events.reactor as reactor
        import src.state.db as db_module

        city = _paris()
        now = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
        target_date = "2026-06-10"

        class _Conn:
            def __init__(self, role):
                self.role = role

            def close(self):
                pass

        world_conn = _Conn("world")
        forecast_conn = _Conn("forecasts")
        persisted = {"ready": False}
        monkeypatch.setattr(config_module, "runtime_cities_by_name", lambda: {"Paris": city})
        monkeypatch.setattr(
            db_module,
            "get_world_connection_read_only",
            lambda: world_conn,
        )
        monkeypatch.setattr(
            db_module,
            "get_forecasts_connection_read_only",
            lambda: forecast_conn,
        )

        def latest_fact(conn, *, temperature_metric, **_kw):
            assert conn is world_conn, "Day0 facts must come from the world DB"
            if temperature_metric == "high":
                return {"observation_time": (now - timedelta(minutes=5)).isoformat()}
            return None

        def read_vectors(**kwargs):
            assert kwargs.get("conn") is forecast_conn, (
                "Day0 vectors must come from the forecasts DB"
            )
            return [object()] if persisted["ready"] else []

        monkeypatch.setattr(
            target_plan,
            "_latest_authorized_day0_fact",
            latest_fact,
        )
        monkeypatch.setattr(vectors_module, "day0_hourly_models_for_city", lambda _city: ["ecmwf_ifs"])
        monkeypatch.setattr(
            vectors_module,
            "read_freshest_day0_hourly_vectors",
            read_vectors,
        )

        missing = reactor._edli_day0_hourly_missing_authority_families(
            cities=[city], decision_time=now
        )
        assert missing.proved is True
        assert missing.missing_families == frozenset({("Paris", target_date, "high")})

        persisted["ready"] = True
        ready = reactor._edli_day0_hourly_missing_authority_families(
            cities=[city], decision_time=now
        )
        assert ready.proved is True
        assert ready.missing_families == frozenset()

    @pytest.mark.parametrize("failure", ["read", "close"])
    def test_priority_probe_db_failure_is_unproved_and_closes_both(
        self, monkeypatch, failure
    ):
        import src.config as config_module
        import src.data.day0_hourly_vectors as vectors_module
        import src.data.replacement_forecast_current_target_plan as target_plan
        import src.events.reactor as reactor
        import src.state.db as db_module

        city = _paris()
        now = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
        closed = []

        class Connection:
            def __init__(self, role):
                self.role = role

            def close(self):
                closed.append(self.role)
                if failure == "close" and self.role == "world":
                    raise sqlite3.OperationalError("world close failed")

        world_conn = Connection("world")
        forecast_conn = Connection("forecasts")
        monkeypatch.setattr(config_module, "runtime_cities_by_name", lambda: {"Paris": city})
        monkeypatch.setattr(db_module, "get_world_connection_read_only", lambda: world_conn)
        monkeypatch.setattr(
            db_module,
            "get_forecasts_connection_read_only",
            lambda: forecast_conn,
        )
        monkeypatch.setattr(
            target_plan,
            "_latest_authorized_day0_fact",
            lambda *_args, **_kwargs: {
                "observation_time": (now - timedelta(minutes=5)).isoformat()
            },
        )
        monkeypatch.setattr(vectors_module, "day0_hourly_models_for_city", lambda _city: ["ecmwf_ifs"])

        def read_vectors(**kwargs):
            assert kwargs["raise_on_db_error"] is True
            if failure == "read":
                raise sqlite3.OperationalError("forecast read failed")
            return []

        monkeypatch.setattr(
            vectors_module,
            "read_freshest_day0_hourly_vectors",
            read_vectors,
        )

        probe = reactor._edli_day0_hourly_missing_authority_families(
            cities=[city], decision_time=now
        )

        assert probe.proved is False
        assert closed == ["world", "forecasts"]

    def test_scheduler_rotates_priority_segment_without_demoting_priority(self):
        # R4-b2: moved to src.events.reactor with the day0-hourly-refresh cluster.
        from src.events import reactor

        ordered = [_paris(), _wellington(), SimpleNamespace(name="London")]

        rotated = reactor._edli_rotate_day0_hourly_refresh_order(
            ordered,
            priority_city_count=2,
            cursor=1,
        )

        assert [c.name for c in rotated] == ["Wellington", "Paris", "London"]

    def test_scheduler_rotates_held_cities_without_demoting_them(self):
        from src.events import reactor

        ordered = [_paris(), _wellington(), SimpleNamespace(name="London")]
        rotated = reactor._edli_rotate_day0_hourly_refresh_order(
            ordered,
            priority_city_count=3,
            held_city_count=2,
            cursor=1,
        )

        assert [c.name for c in rotated] == ["Wellington", "Paris", "London"]

    def test_scheduler_day0_hourly_refresh_defaults_to_microbatch(self, monkeypatch):
        # R4-b2: the microbatch sizing helpers moved to src.events.reactor with the
        # day0-hourly-refresh cluster. R4-b3 (2026-07-08): the reactor-cluster
        # interval helper's sole caller (_edli_reactor_day0_hourly_refresher)
        # also moved to src.events.reactor with the reactor+prune cluster, so it
        # followed.
        from src.events import reactor

        monkeypatch.delenv("ZEUS_DAY0_HOURLY_REFRESH_MAX_CITIES", raising=False)
        monkeypatch.delenv("ZEUS_DAY0_HOURLY_REFRESH_PRIORITY_CITY_CAP", raising=False)
        monkeypatch.delenv("ZEUS_DAY0_HOURLY_REFRESH_BUDGET_SECONDS", raising=False)
        monkeypatch.delenv("ZEUS_DAY0_HOURLY_FETCH_TIMEOUT_SECONDS", raising=False)
        monkeypatch.delenv(
            "ZEUS_REACTOR_DAY0_HOURLY_FETCH_TIMEOUT_SECONDS", raising=False
        )

        assert reactor._day0_hourly_refresh_max_cities(priority_city_count=31) == 3
        assert reactor._day0_hourly_refresh_max_cities(priority_city_count=0) == 1
        assert reactor._day0_hourly_refresh_budget_seconds() == 6.0
        assert reactor._day0_hourly_fetch_timeout_seconds() == 4.0
        assert reactor._reactor_day0_hourly_fetch_timeout_seconds() == 4.0
        assert reactor._reactor_day0_hourly_refresh_interval_seconds() == 300.0

    def test_reactor_day0_hourly_refresher_preserves_city_date_throttle(
        self, monkeypatch
    ):
        # R4-b3 (2026-07-08): _edli_reactor_day0_hourly_refresher moved from
        # src/main.py to src.events.reactor with the reactor+prune cluster.
        import src.config as config
        import src.data.day0_hourly_vectors as hv
        from src.events import reactor

        captured = {}

        def fake_refresh(cities, **kwargs):
            captured.update(kwargs)
            assert [city.name for city in cities] == ["Paris"]
            return SimpleNamespace(
                vectors_written=2,
                cities_attempted=1,
                incomplete_expected_bundles=0,
            )

        monkeypatch.setattr(config, "runtime_cities_by_name", lambda: {"Paris": _paris()})
        monkeypatch.setattr(hv, "maybe_refresh_day0_hourly_vectors", fake_refresh)
        monkeypatch.delenv("ZEUS_REACTOR_DAY0_HOURLY_REFRESH_INTERVAL_SECONDS", raising=False)

        refresh = reactor._edli_reactor_day0_hourly_refresher(
            held_family_provider=lambda: (),
        )

        assert refresh(city="Paris", target_date="2026-06-25", metric="high") is True
        assert captured["interval_s"] == 300.0
        assert captured["max_cities"] == 1
        assert captured["quota_critical_cities"] == 0
        assert captured["persist_lock_blocking"] is False

    def test_reactor_day0_hourly_refresher_uses_critical_quota_for_held_family(
        self, monkeypatch
    ):
        """Targeted held redecision survives maintenance/source-clock exhaustion."""
        import src.config as config
        import src.data.day0_hourly_vectors as hv
        from src.events import reactor

        captured = {}

        def fake_refresh(cities, **kwargs):
            captured.update(kwargs)
            assert [city.name for city in cities] == ["Paris"]
            return SimpleNamespace(
                vectors_written=2,
                cities_attempted=1,
                incomplete_expected_bundles=0,
            )

        monkeypatch.setattr(config, "runtime_cities_by_name", lambda: {"Paris": _paris()})
        monkeypatch.setattr(hv, "maybe_refresh_day0_hourly_vectors", fake_refresh)
        refresh = reactor._edli_reactor_day0_hourly_refresher(
            held_family_provider=lambda: {
                ("Paris", "2026-06-25", "high"),
                ("Wellington", "2026-06-26", "low"),
            },
        )

        assert refresh(city="Paris", target_date="2026-06-25", metric="high") is True
        assert captured["quota_critical_cities"] == 1

    def test_day0_hourly_priority_source_puts_held_families_before_missing_authority(
        self, monkeypatch
    ):
        # R4-b2 (2026-07-08 main.py slimming): the priority-families builder
        # moved to src.events.reactor with the day0-hourly-refresh cluster.
        from src.events import reactor

        assert reactor._edli_day0_hourly_priority_families(
            held_families={("Paris", "2026-06-25", "low")},
            missing_authority_families={
                ("Wellington", "2026-06-26", "high"),
                ("London", "2026-06-25", "high"),
            },
        ) == [
            ("paris", "2026-06-25", "low"),
            ("london", "2026-06-25", "high"),
            ("wellington", "2026-06-26", "high"),
        ]


@pytest.mark.parametrize(
    ("metric", "settlement_value", "physical_value", "future_unavailable_value", "extreme_key"),
    (
        ("high", 35.0, 36.0, 37.0, "high_so_far"),
        ("low", 25.0, 24.0, 23.0, "low_so_far"),
    ),
)
def test_global_day0_fast_fact_is_statistical_and_causal(
    metric,
    settlement_value,
    physical_value,
    future_unavailable_value,
    extreme_key,
):
    """Fast physical facts move q symmetrically, never settlement authority."""
    import src.engine.event_reactor_adapter as era
    from src.events.opportunity_event import make_opportunity_event
    from src.state.schema.observation_prints_schema import append_print, ensure_table

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE observation_instants (
            city TEXT, target_date TEXT, source TEXT, station_id TEXT,
            local_timestamp TEXT, utc_timestamp TEXT, imported_at TEXT,
            temp_unit TEXT, running_max REAL, running_min REAL,
            authority TEXT, training_allowed INTEGER, causality_status TEXT,
            source_role TEXT, raw_response TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "Paris", "2026-07-14", "wu_icao_history", "LFPB",
            "2026-07-14T16:00:00+02:00", "2026-07-14T14:00:00+00:00",
            "2026-07-14T14:05:00+00:00", "C", 35.0, 25.0,
            "VERIFIED", 1, "OK", "historical_hourly", "METAR LFPB 141400Z 35/14",
        ),
    )
    ensure_table(conn)
    for published, fetched, value in (
        ("2026-07-14T14:30:00+00:00", "2026-07-14T14:34:00+00:00", settlement_value),
        ("2026-07-14T15:00:00+00:00", "2026-07-14T16:00:00+00:00", future_unavailable_value),
    ):
        append_print(
            conn,
            city="Paris",
            station_id="LFPB",
            source_channel="aviationweather_metar",
            publish_ts_utc=published,
            value_native=value,
            unit="C",
            fetched_at_utc=fetched,
            raw_report=f"METAR LFPB 141430Z {int(value):02d}/14",
        )
    carrier = make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key=f"Paris|2026-07-14|{metric}|LFPB",
        source="global_auction_winner_target:old-carrier",
        observed_at="2026-07-14T14:00:00+00:00",
        available_at="2026-07-14T14:05:00+00:00",
        received_at="2026-07-14T14:05:00+00:00",
        payload={
            "city": "Paris",
            "target_date": "2026-07-14",
            "metric": metric,
            "station_id": "LFPB",
            "settlement_source": "wu_icao_history",
            "settlement_unit": "C",
            "observation_time": "2026-07-14T14:00:00+00:00",
            "observation_available_at": "2026-07-14T14:05:00+00:00",
            "raw_value": settlement_value,
            "rounded_value": int(settlement_value),
            extreme_key: settlement_value,
            "source_match_status": "MATCH",
            "local_date_status": "MATCH",
            "station_match_status": "MATCH",
            "dst_status": "UNAMBIGUOUS",
            "metric_match_status": "MATCH",
            "rounding_status": "MATCH",
            "source_authorized_status": "AUTHORIZED",
            "live_authority_status": "live",
        },
        causal_snapshot_id="old-day0-carrier",
    )
    decision_time = datetime(2026, 7, 14, 14, 40, tzinfo=UTC)
    rebound = era._global_day0_execution_payload(
        carrier,
        family=SimpleNamespace(
            city="Paris",
            target_date="2026-07-14",
            metric=metric,
        ),
        resolution=SimpleNamespace(measurement_unit="C", station_id="LFPB"),
        conditioning=None,
        observation_conn=conn,
        decision_time=decision_time,
        posterior_id=29914,
    )

    assert rebound["observation_time"] == "2026-07-14T14:00:00+00:00"
    assert rebound["settlement_source"] == "wu_icao_history"
    assert rebound["rounded_value"] == int(settlement_value)
    assert rebound["_edli_day0_physical_frontier_observation_time"] == (
        "2026-07-14T14:30:00+00:00"
    )
    assert rebound["_edli_day0_physical_frontier_source"] == "aviationweather_metar"
    assert era._day0_observation_age_minutes(rebound, decision_time) == 10.0
    assert rebound["_edli_global_day0_binding"]["physical_frontier_clock"][
        "value_role"
    ] == "clock_only_equal_settlement_frontier"
    append_print(
        conn,
        city="Paris",
        station_id="LFPB",
        source_channel="aviationweather_metar",
        publish_ts_utc="2026-07-14T14:45:00+00:00",
        value_native=physical_value,
        unit="C",
        fetched_at_utc="2026-07-14T14:46:00+00:00",
        raw_report=f"METAR LFPB 141445Z {int(physical_value):02d}/14",
    )
    stronger_decision = datetime(2026, 7, 14, 15, 0, tzinfo=UTC)
    stronger = era._global_day0_execution_payload(
        carrier,
        family=SimpleNamespace(
            city="Paris",
            target_date="2026-07-14",
            metric=metric,
        ),
        resolution=SimpleNamespace(measurement_unit="C", station_id="LFPB"),
        conditioning=None,
        observation_conn=conn,
        decision_time=stronger_decision,
        posterior_id=29914,
    )
    conn.close()

    assert stronger["rounded_value"] == int(settlement_value)
    assert stronger[extreme_key] == settlement_value
    assert stronger["_edli_global_day0_binding"]["observed_extreme_native"] == settlement_value
    assert stronger["_edli_day0_probability_boundary_native"] == physical_value
    assert (
        stronger["_edli_global_day0_binding"]["statistical_physical_boundary"][
            "value_role"
        ]
        == "margin_adjusted_statistical_absorbing_boundary"
    )
    assert "_edli_day0_physical_frontier_observation_time" not in stronger
    assert era._day0_observation_age_minutes(stronger, stronger_decision) == 15.0
    assert stronger["observation_context_id"] != rebound["observation_context_id"]
