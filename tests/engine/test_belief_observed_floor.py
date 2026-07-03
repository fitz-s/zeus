# Created: 2026-06-28
# Last audited: 2026-06-28
# Authority basis: midstream belief-freeze incident 2026-06-28 — the served daily-HIGH
#   belief (load_replacement_belief, the K1 single held-position belief authority) reads
#   forecast_posteriors.q_json computed the DAY BEFORE the target day and applies NO
#   observed-floor, so the served belief sits BELOW the day's already-measured running
#   high (Beijing served 28.6C while observed running-high was 33.0C; ~83% of served mass
#   on bins the realized 33 made physically impossible). The day0 absorbing-fact lane that
#   WOULD inject the observed extreme depends on a live-provider obs fetch that fails on
#   settlement day, then falls back to this same stale posterior. The realized running-high
#   IS persisted/current in world.observation_instants (running_max). This pins the
#   MEASURED-FACT invariant: the served held-position belief can NEVER place mass on a
#   settlement bin entirely below the observed running extreme, and the remaining bins
#   keep the FORECAST's relative odds (truncate-and-renormalize the full-day-max forecast
#   posterior conditioned on M >= observed_high — a pure Bayesian conditioning on a hard
#   fact, NOT a fitted de-bias/MOS).
"""Antibody: the served held-position belief is floored by the observed running extreme.

A daily MAX can only be >= the observed-so-far max (and a daily MIN only <= observed-so-far
min). A served belief that places probability on a settlement bin the realized extreme has
already excluded is a hard consistency violation and blinds exit decisions (a NO on a bin the
high already exceeded is a near-certain win; a YES on it is dead).

These tests pin two layers:

1. ``apply_observed_floor_to_q_vector`` — the PURE settlement transport Y=max(X,O) (HIGH) /
   min(X,O) (LOW): zero every bin whose entire preimage lies at/below the observed high (for
   HIGH; symmetric for LOW) and collapse its mass onto the bin CONTAINING O (the lowest
   surviving bin); surviving bins keep their forecast mass EXACTLY (no renormalization). This
   is measure-preserving transport, NOT Bayesian conditioning X|X>=O (which would renormalize
   and wrongly inflate the upper tail). It is the SAME operator the continuous day0 law uses,
   so the floor is idempotent — T(T(q)) == T(q).

2. ``load_replacement_belief`` — the single belief authority reads the canonical observed extreme
   from world.observation_instants and serves the FLOORED belief, so a held NO on an
   already-exceeded bin reads ~1.0 and a held YES on it reads ~0.0, regardless of whether the
   upstream day0 live-obs lane fired.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.engine.position_belief import (
    LIVE_REPLACEMENT_POSTERIOR_SOURCE_ID,
    apply_observed_floor_to_q_vector,
    load_replacement_belief,
)

NOW = datetime(2026, 6, 28, 8, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Layer 1: the pure settlement-conditioned transform
# ---------------------------------------------------------------------------

# The live Beijing 2026-06-28 family (metric=high), q copied from the served posterior.
BEIJING_Q = {
    "Will the highest temperature in Beijing be 27°C or below on June 28?": 0.2032,
    "Will the highest temperature in Beijing be 28°C on June 28?": 0.2086,
    "Will the highest temperature in Beijing be 29°C on June 28?": 0.2154,
    "Will the highest temperature in Beijing be 30°C on June 28?": 0.1649,
    "Will the highest temperature in Beijing be 31°C on June 28?": 0.0957,
    "Will the highest temperature in Beijing be 32°C on June 28?": 0.0459,
    "Will the highest temperature in Beijing be 33°C on June 28?": 0.0229,
    "Will the highest temperature in Beijing be 34°C on June 28?": 0.0156,
    "Will the highest temperature in Beijing be 35°C on June 28?": 0.0140,
    "Will the highest temperature in Beijing be 36°C on June 28?": 0.0138,
    "Will the highest temperature in Beijing be 37°C or higher on June 28?": 0.0000,
}


def _below_or_at(q: dict[str, float], threshold_label_value: int) -> float:
    """Sum q over point/shoulder bins whose label value <= threshold (high family)."""
    total = 0.0
    for label, val in q.items():
        # crude label value extractor for the test's own bookkeeping
        import re

        m = re.search(r"(-?\d+)\s*°C", label)
        if m and int(m.group(1)) <= threshold_label_value and "higher" not in label:
            total += val
    return total


class TestApplyObservedFloorHigh:
    def test_beijing_observed_33_zeros_all_bins_below_33(self):
        """Observed running-high 33 makes every bin whose preimage upper <= 33-0.5
        impossible: 27-or-below..32 all go to 0.0."""
        floored = apply_observed_floor_to_q_vector(
            BEIJING_Q,
            observed_extreme_native=33.0,
            metric="high",
            rounding_rule="wmo_half_up",
        )
        for label_val in (27, 28, 29, 30, 31, 32):
            key = next(k for k in BEIJING_Q if f"{label_val}°C" in k and "higher" not in k)
            assert floored[key] == 0.0, f"bin {label_val} must be impossible under obs 33"

    def test_beijing_transport_is_mass_preserving(self):
        """Transport Y=max(X,O) moves mass, never destroys it: the floored vector still
        sums to the forecast total (1.0)."""
        floored = apply_observed_floor_to_q_vector(
            BEIJING_Q, observed_extreme_native=33.0, metric="high",
            rounding_rule="wmo_half_up",
        )
        assert sum(floored.values()) == pytest.approx(sum(BEIJING_Q.values()), abs=1e-9)

    def test_beijing_impossible_mass_collapses_to_O_bin_upper_bins_unchanged(self):
        """The O-containing bin (33) absorbs ALL the below-33 mass; the upper bins
        (34,35,36, 37-or-higher) keep their FORECAST mass verbatim — they are trajectories
        with X>=O that settle at X, NOT renormalized."""
        floored = apply_observed_floor_to_q_vector(
            BEIJING_Q, observed_extreme_native=33.0, metric="high",
            rounding_rule="wmo_half_up",
        )
        k33 = next(k for k in BEIJING_Q if "33°C" in k)
        below_mass = sum(BEIJING_Q[k] for k in BEIJING_Q
                         if (mm := __import__("re").search(r"(-?\d+)\s*°C", k))
                         and int(mm.group(1)) <= 32 and "higher" not in k)
        # 33 bin = its own forecast mass + ALL collapsed below-33 mass
        assert floored[k33] == pytest.approx(BEIJING_Q[k33] + below_mass, abs=1e-9)
        # upper bins UNCHANGED (verbatim forecast mass — the conditioning-renormalize bug
        # would have inflated these)
        for label_val in (34, 35, 36):
            k = next(kk for kk in BEIJING_Q if f"{label_val}°C" in kk and "higher" not in kk)
            assert floored[k] == pytest.approx(BEIJING_Q[k], abs=1e-12)
        ktop = next(k for k in BEIJING_Q if "higher" in k)
        assert floored[ktop] == pytest.approx(BEIJING_Q[ktop], abs=1e-12)

    def test_observed_below_whole_family_is_noop(self):
        """obs 20 is below every bin's preimage -> nothing impossible -> unchanged."""
        floored = apply_observed_floor_to_q_vector(
            BEIJING_Q, observed_extreme_native=20.0, metric="high",
            rounding_rule="wmo_half_up",
        )
        for k in BEIJING_Q:
            assert floored[k] == pytest.approx(BEIJING_Q[k], abs=1e-12)

    def test_observed_on_bin_boundary_keeps_containing_bin(self):
        """obs exactly 32.5 (the 32|33 preimage boundary): the 32 bin (preimage [31.5,32.5))
        is entirely at/below 32.5 -> impossible; the 33 bin (preimage [32.5,33.5)) survives."""
        floored = apply_observed_floor_to_q_vector(
            BEIJING_Q, observed_extreme_native=32.5, metric="high",
            rounding_rule="wmo_half_up",
        )
        k32 = next(k for k in BEIJING_Q if "32°C" in k)
        k33 = next(k for k in BEIJING_Q if "33°C" in k)
        assert floored[k32] == 0.0
        assert floored[k33] > 0.0

    def test_open_top_bin_certain_when_obs_at_or_above(self):
        """If observed high reaches the open-top '37 or higher' edge, only that bin survives
        and it carries all the mass (-> a held YES on it is certain)."""
        q = dict(BEIJING_Q)
        q["Will the highest temperature in Beijing be 37°C or higher on June 28?"] = 0.05
        # renormalize the toy q so it sums to 1 for a clean assertion
        s = sum(q.values())
        q = {k: v / s for k, v in q.items()}
        floored = apply_observed_floor_to_q_vector(
            q, observed_extreme_native=37.0, metric="high", rounding_rule="wmo_half_up",
        )
        ktop = next(k for k in q if "higher" in k)
        assert floored[ktop] == pytest.approx(1.0, abs=1e-9)
        for k in q:
            if k != ktop:
                assert floored[k] == 0.0

    def test_extreme_obs_collapses_all_mass_to_open_top_bin(self):
        """obs=99 excludes every CLOSED bin; the open-top 'X or higher' shoulder
        (preimage hi=+inf) is never impossible, so ALL mass transports there (settlement is
        definitely 'X or higher' when the high reached 99). A valid MECE high family always
        has this open-top shoulder, so the all-impossible degenerate branch is unreachable in
        practice — the floor stays well-defined."""
        floored = apply_observed_floor_to_q_vector(
            BEIJING_Q, observed_extreme_native=99.0, metric="high",
            rounding_rule="wmo_half_up",
        )
        ktop = next(k for k in BEIJING_Q if "higher" in k)
        assert floored[ktop] == pytest.approx(sum(BEIJING_Q.values()), abs=1e-9)
        for k in BEIJING_Q:
            if k != ktop:
                assert floored[k] == 0.0

    def test_degenerate_no_open_shoulder_returns_unchanged(self):
        """Genuinely degenerate (a MALFORMED family with NO open-top shoulder): if every
        parsed bin is impossible there is no transport target. The floor must not
        divide-by-zero or fabricate a distribution — it returns the input unchanged and lets
        the caller's gates own the contradiction."""
        closed_only = {
            "Will the highest temperature in Beijing be 28°C on June 28?": 0.5,
            "Will the highest temperature in Beijing be 29°C on June 28?": 0.5,
        }
        floored = apply_observed_floor_to_q_vector(
            closed_only, observed_extreme_native=99.0, metric="high",
            rounding_rule="wmo_half_up",
        )
        assert floored == closed_only


class TestApplyObservedFloorLow:
    LOW_Q = {
        "Will the lowest temperature in Moscow be 10°C or below on June 28?": 0.10,
        "Will the lowest temperature in Moscow be 11°C on June 28?": 0.20,
        "Will the lowest temperature in Moscow be 12°C on June 28?": 0.30,
        "Will the lowest temperature in Moscow be 13°C on June 28?": 0.25,
        "Will the lowest temperature in Moscow be 14°C or higher on June 28?": 0.15,
    }

    def test_observed_low_zeros_bins_above(self):
        """LOW market: a daily MIN can only be <= observed-so-far min. Observed low 11 makes
        every bin entirely ABOVE 11 impossible (12,13,14-or-higher)."""
        floored = apply_observed_floor_to_q_vector(
            self.LOW_Q, observed_extreme_native=11.0, metric="low",
            rounding_rule="wmo_half_up",
        )
        for label_val in (12, 13):
            key = next(k for k in self.LOW_Q if f"{label_val}°C" in k and "higher" not in k)
            assert floored[key] == 0.0
        ktop = next(k for k in self.LOW_Q if "higher" in k)
        assert floored[ktop] == 0.0

    def test_low_transport_mass_preserving_collapses_to_O_bin(self):
        """LOW transport Y=min(X,O): impossible (above-O) mass collapses onto the
        O-containing bin (the HIGHEST surviving bin = 11); mass is preserved; lower bins
        (10-or-below) keep forecast mass verbatim."""
        floored = apply_observed_floor_to_q_vector(
            self.LOW_Q, observed_extreme_native=11.0, metric="low",
            rounding_rule="wmo_half_up",
        )
        assert sum(floored.values()) == pytest.approx(sum(self.LOW_Q.values()), abs=1e-9)
        k11 = next(k for k in self.LOW_Q if "11°C" in k)
        above_mass = self.LOW_Q[next(k for k in self.LOW_Q if "12°C" in k)] \
            + self.LOW_Q[next(k for k in self.LOW_Q if "13°C" in k)] \
            + self.LOW_Q[next(k for k in self.LOW_Q if "higher" in k)]
        assert floored[k11] == pytest.approx(self.LOW_Q[k11] + above_mass, abs=1e-9)
        kbelow = next(k for k in self.LOW_Q if "below" in k)
        assert floored[kbelow] == pytest.approx(self.LOW_Q[kbelow], abs=1e-12)


class TestObservedFloorIdempotent:
    def test_double_application_is_noop(self):
        """The transport is idempotent: T(T(q)) == T(q). Coexistence with the day0 lane's
        floor can never double-shift mass (consult-verified, same operator both layers)."""
        once = apply_observed_floor_to_q_vector(
            BEIJING_Q, observed_extreme_native=33.0, metric="high", rounding_rule="wmo_half_up",
        )
        twice = apply_observed_floor_to_q_vector(
            once, observed_extreme_native=33.0, metric="high", rounding_rule="wmo_half_up",
        )
        for k in once:
            assert twice[k] == pytest.approx(once[k], abs=1e-12)


# ---------------------------------------------------------------------------
# Layer 2: load_replacement_belief serves the floored belief from the canonical surface
# ---------------------------------------------------------------------------

BIN_28 = "Will the highest temperature in Beijing be 28°C on June 28?"


@pytest.fixture
def forecasts_db(tmp_path):
    path = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id TEXT, city TEXT, target_date TEXT,
            temperature_metric TEXT, computed_at TEXT, q_json TEXT,
            source_cycle_time TEXT, runtime_layer TEXT, source_id TEXT, posterior_method TEXT
        )
        """
    )
    conn.execute("CREATE TABLE raw_model_forecasts (city TEXT, target_date TEXT, metric TEXT, source_cycle_time TEXT, endpoint TEXT, coverage_status TEXT, captured_at TEXT, source_available_at TEXT)")
    conn.execute("CREATE TABLE raw_forecast_artifacts (source_id TEXT, source_cycle_time TEXT, captured_at TEXT, source_available_at TEXT, artifact_metadata_json TEXT)")
    # Posterior computed the DAY BEFORE the target day (the freeze) — fresh by clock,
    # stale by fact. source_cycle_time recent so the freshness gate passes.
    conn.execute(
        "INSERT INTO forecast_posteriors VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "p_beijing", "Beijing", "2026-06-28", "high",
            "2026-06-27T14:17:17+00:00", json.dumps(BEIJING_Q),
            "2026-06-28T06:00:00+00:00", "live",
            LIVE_REPLACEMENT_POSTERIOR_SOURCE_ID,
            "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        ),
    )
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture
def world_db(tmp_path):
    path = tmp_path / "zeus-world.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE observation_instants (
            city TEXT, target_date TEXT, source TEXT, local_timestamp TEXT, utc_timestamp TEXT,
            running_max REAL, running_min REAL, temp_unit TEXT, authority TEXT,
            source_role TEXT, training_allowed INTEGER, causality_status TEXT
        )
        """
    )
    # The realized running-high IS present and current: 33.0C, VERIFIED/historical_hourly/wu.
    for hour, rmax in ((0, 28.0), (3, 31.0), (6, 33.0)):
        conn.execute(
            "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "Beijing", "2026-06-28", "wu_icao_history",
                f"2026-06-28T{hour:02d}:00:00", f"2026-06-28T{hour:02d}:00:00+00:00",
                rmax, 24.0, "C", "VERIFIED", "historical_hourly", 1, "OK",
            ),
        )
    conn.commit()
    conn.close()
    return str(path)


def _patch_world_path(monkeypatch, world_db):
    """Point the belief authority's world read at the test world DB."""
    import src.state.db as db_mod

    monkeypatch.setattr(db_mod, "ZEUS_WORLD_DB_PATH", type(db_mod.ZEUS_WORLD_DB_PATH)(world_db))


def test_served_belief_never_below_observed_high_buy_no(forecasts_db, world_db, monkeypatch):
    """THE LIVE CONTRADICTION: held buy_no on the Beijing 28C bin while observed high is 33.0.
    Without the floor the served held-side (buy_no) prob = 1 - 0.2086 = 0.791 (the bin still
    'might lose'). With the floor the 28 bin is impossible -> q_yes=0 -> the NO is a near-certain
    win (held_side_prob ~ 1.0)."""
    _patch_world_path(monkeypatch, world_db)
    belief = load_replacement_belief(
        city="Beijing", target_date="2026-06-28", temperature_metric="high",
        bin_label=BIN_28, direction="buy_no", now=NOW, db_path=forecasts_db,
    )
    assert belief is not None
    assert belief.q_yes_bin == pytest.approx(0.0, abs=1e-9)
    assert belief.held_side_prob == pytest.approx(1.0, abs=1e-9)


def test_served_belief_buy_yes_on_impossible_bin_is_dead(forecasts_db, world_db, monkeypatch):
    _patch_world_path(monkeypatch, world_db)
    belief = load_replacement_belief(
        city="Beijing", target_date="2026-06-28", temperature_metric="high",
        bin_label=BIN_28, direction="buy_yes", now=NOW, db_path=forecasts_db,
    )
    assert belief is not None
    assert belief.q_yes_bin == pytest.approx(0.0, abs=1e-9)
    assert belief.held_side_prob == pytest.approx(0.0, abs=1e-9)


def test_no_observed_surface_serves_unfloored_belief(forecasts_db, tmp_path, monkeypatch):
    """When the canonical surface has NO row (the floor is unavailable), the belief is served
    UNFLOORED (fail-open to the existing behavior — the floor only ever ADDS the measured fact,
    it never blocks serving)."""
    empty_world = tmp_path / "empty-world.db"
    conn = sqlite3.connect(empty_world)
    conn.execute(
        "CREATE TABLE observation_instants (city TEXT, target_date TEXT, source TEXT, local_timestamp TEXT, utc_timestamp TEXT, running_max REAL, running_min REAL, temp_unit TEXT, authority TEXT, source_role TEXT, training_allowed INTEGER, causality_status TEXT)"
    )
    conn.commit()
    conn.close()
    _patch_world_path(monkeypatch, empty_world)
    belief = load_replacement_belief(
        city="Beijing", target_date="2026-06-28", temperature_metric="high",
        bin_label=BIN_28, direction="buy_no", now=NOW, db_path=forecasts_db,
    )
    assert belief is not None
    # unfloored: q_yes is the raw forecast value, held NO = 1 - 0.2086
    assert belief.q_yes_bin == pytest.approx(0.2086, abs=1e-6)
    assert belief.held_side_prob == pytest.approx(1.0 - 0.2086, abs=1e-6)
