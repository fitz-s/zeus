# Created: 2026-06-10
# Last reused or audited: 2026-06-30
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
  R10. REMAINING-DAY SELECTION: only hours of the local target day AT/AFTER
       now contribute; a model whose remaining window is empty contributes
       nothing.
  R11. POST-PEAK REPRICING: with all remaining-hours temps at/below the
       running max, the pooled members clamp to the floor — the floor bin
      gets ~all q mass and bins above get ~none (the exact category the
      full-day-masked q got wrong). Flag default OFF; flag OFF leaves the
      legacy path untouched; flag ON must not fall back to it.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from src.contracts.execution_price import ExecutionPrice as EP
from src.data.day0_hourly_vectors import (
    Day0HourlyVector,
    parse_openmeteo_hourly_payload,
    persist_day0_hourly_vectors,
    read_freshest_day0_hourly_vectors,
    remaining_day_extremes_c,
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
        lat=48.8566, lon=2.3522,
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

    def test_remaining_day_q_is_live_without_setting(self):
        """Remaining-day q is live Day0 law; missing settings cannot restore full-day masked q."""
        from src.engine.event_reactor_adapter import _day0_remaining_day_q_enabled

        assert _day0_remaining_day_q_enabled() is True

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
        payload = {"metric": "high", "rounded_value": 25.0}
        members = era._day0_remaining_day_members(
            payload=payload, family=self._family(), unit="C",
            decision_time=datetime(2026, 6, 10, 15, 0, tzinfo=UTC),
        )
        assert members is not None
        # every member clamped UP to the running max (absorbing physical law)
        assert np.all(members == 25.0)
        assert payload["_edli_day0_remaining_models"] == 2

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
            payload={"metric": "high", "rounded_value": 25.0}, family=self._family(),
            unit="C", decision_time=datetime(2026, 6, 10, 15, 0, tzinfo=UTC),
        )
        assert sorted(members.tolist()) == [25.0, 27.5]

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
        members = era._day0_remaining_day_members(
            payload=payload,
            family=self._family(),
            unit="C",
            decision_time=datetime(2026, 6, 10, 15, 0, tzinfo=UTC),
        )

        assert members is None
        assert captured["expected_models"] == ["icon_d2", "ecmwf_ifs"]
        assert captured["require_expected"] is True
        assert captured["max_bundle_skew_minutes"] == hv.DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES
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
        )

        assert out is None
        assert captured["expected_models"] == ["icon_d2", "ecmwf_ifs"]
        assert captured["require_expected"] is True
        assert captured["max_bundle_skew_minutes"] == hv.DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES

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

        def _legacy_fallback_called(*args, **kwargs):
            raise AssertionError("legacy Day0 full-day fallback was called")

        monkeypatch.setattr(era, "_maybe_apply_edli_bias_correction", _legacy_fallback_called)

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

        def _legacy_fallback_called(*args, **kwargs):
            raise AssertionError("legacy Day0 full-day fallback was called")

        monkeypatch.setattr(era, "_maybe_apply_edli_bias_correction", _legacy_fallback_called)

        with pytest.raises(ValueError, match="DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE"):
            era._market_analysis_from_event_snapshot(
                calibration_conn=sqlite3.connect(":memory:"),
                snapshot=snapshot,
                family=family,
                native_costs=native_costs,
                payload=payload,
                decision_time=datetime(2026, 6, 10, 13, 5, tzinfo=UTC),
            )


# ===========================================================================
# R22 — replayable provenance identity on persisted vectors (PR#404 P1)
# ===========================================================================

class TestRequestHashProvenance:
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
            return [_vector()], "sha256:realhash"

        def fake_persist(vectors, *, target_date, request_hash, **kw):
            captured["request_hash"] = request_hash
            captured["target_dates"].append(target_date)
            return len(vectors)

        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", fake_fetch)
        monkeypatch.setattr(hv, "persist_day0_hourly_vectors", fake_persist)
        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: ["icon_d2"])
        hv._LAST_REFRESH_MONOTONIC.clear()
        n = hv.maybe_refresh_day0_hourly_vectors(
            [_paris()], decision_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
        )
        assert n == 2
        assert captured["request_hash"] == "sha256:realhash"
        assert captured["target_dates"] == ["2026-06-10", "2026-06-11"]

    def test_refresh_lock_contention_does_not_throttle_next_attempt(self, monkeypatch):
        """A contended forecasts writer lock must not stall the trading reactor lane."""
        import src.data.day0_hourly_vectors as hv

        attempts = {"fetch": 0, "persist": 0}

        def fake_fetch(city, *, models=None, now=None, timeout_s=None):
            attempts["fetch"] += 1
            return [_vector()], "sha256:realhash"

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

    def test_empty_fetch_result_does_not_throttle_next_attempt(self, monkeypatch):
        """Transport/shape soft-failures return empty vectors; they must retry next pass."""
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
        assert attempts == {"fetch": 2, "persist": 0}

    def test_partial_expected_bundle_does_not_throttle_next_attempt(self, monkeypatch):
        """A partial regional+ECMWF bundle is useful data, but not a complete live authority."""
        import src.data.day0_hourly_vectors as hv

        attempts = {"fetch": 0, "persist": 0}

        def fake_fetch(city, *, models=None, now=None, timeout_s=None):
            attempts["fetch"] += 1
            assert list(models or []) == ["icon_d2", "ecmwf_ifs"]
            return [_vector(model="icon_d2")], "sha256:partial"

        def fake_persist(vectors, *, target_date, request_hash, **kw):
            attempts["persist"] += 1
            return len(vectors)

        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: ["icon_d2"])
        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", fake_fetch)
        monkeypatch.setattr(hv, "persist_day0_hourly_vectors", fake_persist)
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

        assert (n1, n2) == (2, 2)
        assert attempts == {"fetch": 2, "persist": 4}

    def test_no_regional_model_uses_global_ecmwf_fallback(self, monkeypatch):
        import src.data.day0_hourly_vectors as hv

        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: [])

        assert hv.day0_hourly_models_for_city(_paris()) == ["ecmwf_ifs"]

    def test_regional_model_keeps_global_ecmwf_anchor(self, monkeypatch):
        import src.data.day0_hourly_vectors as hv

        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: ["icon_d2"])

        assert hv.day0_hourly_models_for_city(_paris()) == ["icon_d2", "ecmwf_ifs"]

    def test_refresh_uses_global_ecmwf_fallback_when_no_regional_model(self, monkeypatch):
        import src.data.day0_hourly_vectors as hv

        captured = {"target_dates": []}

        def fake_fetch(city, *, models=None, now=None):
            captured["models"] = list(models or [])
            return [_vector(model="ecmwf_ifs")], "sha256:globalhash"

        def fake_persist(vectors, *, target_date, request_hash, **kw):
            captured["request_hash"] = request_hash
            captured["vector_models"] = [v.model for v in vectors]
            captured["target_dates"].append(target_date)
            return len(vectors)

        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: [])
        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", fake_fetch)
        monkeypatch.setattr(hv, "persist_day0_hourly_vectors", fake_persist)
        hv._LAST_REFRESH_MONOTONIC.clear()

        n = hv.maybe_refresh_day0_hourly_vectors(
            [_paris()], decision_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
        )

        assert n == 2
        assert captured["models"] == ["ecmwf_ifs"]
        assert captured["request_hash"] == "sha256:globalhash"
        assert captured["vector_models"] == ["ecmwf_ifs"]
        assert captured["target_dates"] == ["2026-06-10", "2026-06-11"]

    def test_refresh_throttle_is_target_date_scoped_at_local_midnight(self, monkeypatch):
        import src.data.day0_hourly_vectors as hv

        captured_dates = []

        def fake_fetch(city, *, models=None, now=None, timeout_s=None):
            return [_vector(model="ecmwf_ifs")], "sha256:datehash"

        def fake_persist(vectors, *, target_date, request_hash, **kw):
            captured_dates.append(target_date)
            return len(vectors)

        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: [])
        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", fake_fetch)
        monkeypatch.setattr(hv, "persist_day0_hourly_vectors", fake_persist)
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

        assert (n1, n2) == (2, 2)
        assert captured_dates == [
            "2026-06-25",
            "2026-06-26",
            "2026-06-26",
            "2026-06-27",
        ]

    def test_scheduler_orders_same_local_day_money_path_cities_first(self):
        import src.main as main

        ordered, priority_count = main._edli_order_day0_hourly_refresh_cities(
            [_paris(), _wellington()],
            decision_time=datetime(2026, 6, 25, 12, 47, tzinfo=UTC),
            priority_families=[("Wellington", "2026-06-26", "high")],
        )

        assert priority_count == 1
        assert [c.name for c in ordered] == ["Wellington", "Paris"]

    def test_scheduler_rotates_priority_segment_without_demoting_priority(self):
        import src.main as main

        ordered = [_paris(), _wellington(), SimpleNamespace(name="London")]

        rotated = main._edli_rotate_day0_hourly_refresh_order(
            ordered,
            priority_city_count=2,
            cursor=1,
        )

        assert [c.name for c in rotated] == ["Wellington", "Paris", "London"]
