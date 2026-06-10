# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator green-light 2026-06-10 item B (remaining-day
#   pricing + persist-the-hourly-vector); day0 first-principles review §2.4
#   (full-day-masked q DEVIATES: overprices excursion bins post-peak) and
#   §6.1/§6.3 spec. Payload shape verified live against
#   api.open-meteo.com/v1/forecast (multi-model suffixed hourly keys).
"""Relationship tests for the day0 hourly-vector lane + remaining-day members.

Contracts:
  R9.  PERSISTENCE: hourly vectors round-trip (degC storage law), idempotent
       on (model, city, date, captured_at), retention prunes old rows, stale
       vectors (> max_age) are NOT served to the q path (fail-closed to the
       legacy full-day path).
  R10. REMAINING-DAY SELECTION: only hours of the local target day AT/AFTER
       now contribute; a model whose remaining window is empty contributes
       nothing.
  R11. POST-PEAK REPRICING: with all remaining-hours temps at/below the
       running max, the pooled members clamp to the floor — the floor bin
       gets ~all q mass and bins above get ~none (the exact category the
       full-day-masked q got wrong). Flag default OFF; flag OFF leaves the
       legacy path untouched.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from src.data.day0_hourly_vectors import (
    Day0HourlyVector,
    parse_openmeteo_hourly_payload,
    persist_day0_hourly_vectors,
    read_freshest_day0_hourly_vectors,
    remaining_day_extremes_c,
)

UTC = timezone.utc


def _paris():
    return SimpleNamespace(
        name="Paris", timezone="Europe/Paris", settlement_unit="C",
        lat=48.8566, lon=2.3522,
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
        assert persist_day0_hourly_vectors([v], target_date="2026-06-10", conn=conn, request_hash="sha256:test") == 1
        assert persist_day0_hourly_vectors([v], target_date="2026-06-10", conn=conn, request_hash="sha256:test") == 0  # idempotent
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
        persist_day0_hourly_vectors([old, new], target_date="2026-06-10", conn=conn, request_hash="sha256:test")
        out = read_freshest_day0_hourly_vectors(
            city="Paris", target_date="2026-06-10",
            now=datetime(2026, 6, 10, 9, 30, tzinfo=UTC), conn=conn,
        )
        assert len(out) == 1 and out[0].temps_c[0] == 20.0

    def test_stale_vectors_are_not_served(self):
        """R9 freshness gate: a 5h-old run must NOT masquerade as the current
        remaining-day distribution (fail-closed to the legacy path)."""
        conn = _conn()
        v = _vector(captured_at=datetime(2026, 6, 10, 4, 0, tzinfo=UTC))
        persist_day0_hourly_vectors([v], target_date="2026-06-10", conn=conn, request_hash="sha256:test")
        out = read_freshest_day0_hourly_vectors(
            city="Paris", target_date="2026-06-10",
            now=datetime(2026, 6, 10, 9, 30, tzinfo=UTC), max_age_hours=3.0, conn=conn,
        )
        assert out == []

    def test_retention_prunes_old_rows(self):
        conn = _conn()
        ancient = _vector(captured_at=datetime(2026, 6, 1, 0, 0, tzinfo=UTC))
        persist_day0_hourly_vectors([ancient], target_date="2026-06-01", conn=conn, request_hash="sha256:test")
        fresh = _vector()
        persist_day0_hourly_vectors([fresh], target_date="2026-06-10", conn=conn, request_hash="sha256:test")
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

    def test_flag_default_off(self):
        from src.engine.event_reactor_adapter import _day0_remaining_day_q_enabled

        assert _day0_remaining_day_q_enabled() is False

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

    def test_no_vectors_returns_none_full_day_fallback(self, monkeypatch):
        monkeypatch.setattr(
            "src.data.day0_hourly_vectors.read_freshest_day0_hourly_vectors",
            lambda **kw: [],
        )
        import src.engine.event_reactor_adapter as era

        assert era._day0_remaining_day_members(
            payload={"metric": "high", "rounded_value": 25.0}, family=self._family(),
            unit="C", decision_time=datetime(2026, 6, 10, 15, 0, tzinfo=UTC),
        ) is None


# ===========================================================================
# R22 — replayable provenance identity on persisted vectors (PR#404 P1)
# ===========================================================================

class TestRequestHashProvenance:
    def test_persisted_rows_carry_non_empty_request_hash(self):
        conn = _conn()
        v = _vector()
        persist_day0_hourly_vectors(
            [v], target_date="2026-06-10", conn=conn, request_hash="sha256:abc123"
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

        captured = {}

        def fake_fetch(city, *, models=None, now=None):
            return [_vector()], "sha256:realhash"

        def fake_persist(vectors, *, target_date, request_hash, **kw):
            captured["request_hash"] = request_hash
            return len(vectors)

        monkeypatch.setattr(hv, "fetch_day0_hourly_vectors", fake_fetch)
        monkeypatch.setattr(hv, "persist_day0_hourly_vectors", fake_persist)
        monkeypatch.setattr(hv, "in_domain_models_for_city", lambda c, **kw: ["icon_d2"])
        hv._LAST_REFRESH_MONOTONIC.clear()
        n = hv.maybe_refresh_day0_hourly_vectors(
            [_paris()], decision_time=datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
        )
        assert n == 1 and captured["request_hash"] == "sha256:realhash"
