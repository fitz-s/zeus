# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/authority/replacement_final_form_2026_06_09.md §4a (staleness
#   degrade ladder). Boundaries DERIVED in docs/evidence/upstream_physical_2026_07_17/
#   staleness_ladder_derivation.md — these tests pin the band edges, the fail-open
#   contract, and the AMBER sigma-inflation admission seam.
"""Staleness DEGRADE LADDER: classification, fitted inflation loader, admission wiring.

(1) classify_posterior_staleness: GREEN/AMBER/RED/EXPIRED band edges (18h/24h/30h);
    newer-cycle-detected forces RED; unparseable/None cycle => UNKNOWN (caller keeps its
    binary law); the EXPIRED horizon is the SAME policy constant the fail-closed gate uses.
(2) posterior_age_inflation.v_for: fail-open zeros (artifact absent / sha mismatch /
    unknown metric / bad age); age->band mapping + monotone clamp.
(3) _amber_inflated_predictive_sigma_c: AMBER widens sigma by sqrt(sigma²+v); GREEN/RED/
    EXPIRED and a missing artifact leave the base sigma byte-identical (fail-open no-op).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import src.forecast.posterior_age_inflation as pai
from src.data.replacement_forecast_cycle_policy import (
    replacement_source_cycle_max_age_hours,
)
from src.data.staleness_degrade_ladder import (
    StalenessBand,
    classify_posterior_staleness,
)

UTC = timezone.utc
DECISION = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def _cyc(age_hours: float) -> datetime:
    return DECISION - timedelta(hours=age_hours)


def _band(age_hours: float, **kw) -> StalenessBand:
    return classify_posterior_staleness(DECISION, _cyc(age_hours), **kw).band


# ---------------------------------------------------------------------------
# (1) classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (0.0, StalenessBand.GREEN),
        (17.9, StalenessBand.GREEN),
        (18.0, StalenessBand.GREEN),     # boundary inclusive: <=18h is GREEN
        (18.01, StalenessBand.AMBER),
        (24.0, StalenessBand.AMBER),     # boundary inclusive: <=24h is AMBER
        (24.01, StalenessBand.RED),
        (29.99, StalenessBand.RED),
        (30.0, StalenessBand.EXPIRED),   # EXPIRED horizon (fail-closed) unchanged
        (48.0, StalenessBand.EXPIRED),
    ],
)
def test_band_edges(age: float, expected: StalenessBand) -> None:
    assert _band(age) == expected


def test_newer_cycle_detected_forces_red_even_when_fresh() -> None:
    # A fresh-age posterior is RED the moment a newer live-eligible cycle is detected
    # but not yet the served carrier (adverse-selection window).
    assert _band(5.0, newer_cycle_detected=True) == StalenessBand.RED
    assert _band(1.0, newer_cycle_detected=True) == StalenessBand.RED
    # ...but EXPIRED still wins over the newer-cycle RED (never weaken the hard wall).
    assert _band(31.0, newer_cycle_detected=True) == StalenessBand.EXPIRED


def test_unknown_cycle_keeps_binary_law() -> None:
    # None / unparseable source_cycle_time => UNKNOWN, which the caller treats as "no
    # ladder action; existing binary law decides" — never a NEW block, never GREEN.
    c = classify_posterior_staleness(DECISION, None)
    assert c.band is StalenessBand.UNKNOWN
    assert c.age_hours is None
    assert c.blocks_entry is False and c.inflates_sigma is False
    # A future-dated cycle (negative age) is also UNKNOWN (left to the future gate).
    assert classify_posterior_staleness(DECISION, DECISION + timedelta(hours=3)).band is (
        StalenessBand.UNKNOWN
    )


def test_expired_bound_tracks_policy_constant() -> None:
    # The ladder reads the EXPIRED horizon from the SAME policy the fail-closed gate uses,
    # so they can never drift on that one number.
    bound = replacement_source_cycle_max_age_hours()
    assert _band(bound - 0.01) != StalenessBand.EXPIRED
    assert _band(bound) == StalenessBand.EXPIRED
    assert _band(bound + 5.0) == StalenessBand.EXPIRED


def test_classification_semantic_flags() -> None:
    assert classify_posterior_staleness(DECISION, _cyc(10)).inflates_sigma is False   # GREEN
    assert classify_posterior_staleness(DECISION, _cyc(20)).inflates_sigma is True    # AMBER
    assert classify_posterior_staleness(DECISION, _cyc(20)).blocks_entry is False
    assert classify_posterior_staleness(DECISION, _cyc(26)).blocks_entry is True      # RED
    assert classify_posterior_staleness(DECISION, _cyc(26)).inflates_sigma is False
    assert classify_posterior_staleness(DECISION, _cyc(40)).blocks_entry is True      # EXPIRED


# ---------------------------------------------------------------------------
# (2) fitted inflation loader
# ---------------------------------------------------------------------------

def _write_artifact(tmp_path, metrics: dict, *, band_hours: int = 6) -> None:
    artifact = {
        "schema_version": 1,
        "as_of": "2026-07-17",
        "generated_at": "2026-07-17T00:00:00",
        "git_sha": "test",
        "unit": "degC2",
        "band_hours": band_hours,
        "fresh_serving_floor_hours": 7.2,
        "amber_band_hours": [18, 24],
        "min_cell_n": 100,
        "metrics": metrics,
    }
    payload = json.dumps(artifact, sort_keys=True, indent=2) + "\n"
    name = "posterior_age_inflation_20260717.json"
    (tmp_path / name).write_text(payload, encoding="utf-8")
    (tmp_path / "ACTIVE.json").write_text(
        json.dumps(
            {"artifact": name, "sha256": hashlib.sha256(payload.encode()).hexdigest(),
             "as_of": "2026-07-17"},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _fresh_artifact_cache():
    pai._load_active_artifact.cache_clear()
    yield
    pai._load_active_artifact.cache_clear()


def test_v_for_fails_open_to_zero(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(pai.ENV_POSTERIOR_AGE_INFLATION_DIR, str(tmp_path))
    assert pai.v_for("high", 20.0) == 0.0  # empty dir
    _write_artifact(tmp_path, {"high": {"v_by_age_band": {"18": 0.36}, "n_by_age_band": {"18": 900}}})
    pai._load_active_artifact.cache_clear()
    assert pai.v_for("zzz", 20.0) == 0.0            # unknown metric
    assert pai.v_for("high", 0.0) == 0.0            # non-positive age
    assert pai.v_for("high", float("nan")) == 0.0
    assert pai.v_for("high", -5.0) == 0.0


def test_v_for_sha_mismatch_fails_open(tmp_path, monkeypatch) -> None:
    _write_artifact(tmp_path, {"high": {"v_by_age_band": {"18": 0.36}, "n_by_age_band": {"18": 900}}})
    pointer = json.loads((tmp_path / "ACTIVE.json").read_text())
    pointer["sha256"] = "0" * 64
    (tmp_path / "ACTIVE.json").write_text(json.dumps(pointer))
    monkeypatch.setenv(pai.ENV_POSTERIOR_AGE_INFLATION_DIR, str(tmp_path))
    assert pai.v_for("high", 20.0) == 0.0


def test_v_for_band_mapping_and_clamp(tmp_path, monkeypatch) -> None:
    _write_artifact(tmp_path, {"high": {
        "v_by_age_band": {"6": 0.0, "12": 0.15, "18": 0.36, "24": 0.73},
        "n_by_age_band": {"6": 900, "12": 900, "18": 900, "24": 900},
    }})
    monkeypatch.setenv(pai.ENV_POSTERIOR_AGE_INFLATION_DIR, str(tmp_path))
    assert pai.v_for("high", 10.0) == 0.0     # band 6
    assert pai.v_for("high", 13.0) == 0.15    # band 12
    assert pai.v_for("high", 18.0) == 0.36    # band 18 (AMBER)
    assert pai.v_for("high", 23.9) == 0.36    # band 18 (floor)
    assert pai.v_for("high", 100.0) == 0.73   # clamp to largest fitted band (measured only)


# ---------------------------------------------------------------------------
# (3) AMBER sigma-inflation admission seam
# ---------------------------------------------------------------------------

def _bundle(sigma_c: float, age_hours: float):
    cyc = (DECISION - timedelta(hours=age_hours)).isoformat()
    return SimpleNamespace(
        provenance_json={"bayes_precision_fusion": {"predictive_sigma_c": sigma_c}},
        source_cycle_time=cyc,
    )


def test_amber_inflates_sigma_by_fitted_value(tmp_path, monkeypatch) -> None:
    import math

    import src.engine.event_reactor_adapter as era

    _write_artifact(tmp_path, {"high": {"v_by_age_band": {"18": 0.36}, "n_by_age_band": {"18": 900}}})
    monkeypatch.setenv(pai.ENV_POSTERIOR_AGE_INFLATION_DIR, str(tmp_path))
    pai._load_active_artifact.cache_clear()
    family = SimpleNamespace(metric="high", city="Shanghai", target_date="2026-07-18")

    # AMBER (age 20h): sigma widened to sqrt(sigma² + v).
    amber = era._amber_inflated_predictive_sigma_c(
        _bundle(0.84, 20.0), family=family, decision_time=DECISION
    )
    assert amber == pytest.approx(math.sqrt(0.84 * 0.84 + 0.36))
    assert amber > 0.84

    # GREEN (age 10h): base sigma untouched.
    green = era._amber_inflated_predictive_sigma_c(
        _bundle(0.84, 10.0), family=family, decision_time=DECISION
    )
    assert green == pytest.approx(0.84)

    # RED (age 26h): the AMBER inflation does not apply here (RED is entry-isolated at the
    # bundle read); base sigma returned unchanged if this seam is ever reached.
    red = era._amber_inflated_predictive_sigma_c(
        _bundle(0.84, 26.0), family=family, decision_time=DECISION
    )
    assert red == pytest.approx(0.84)


def test_amber_sigma_fails_open_without_artifact(tmp_path, monkeypatch) -> None:
    import src.engine.event_reactor_adapter as era

    monkeypatch.setenv(pai.ENV_POSTERIOR_AGE_INFLATION_DIR, str(tmp_path))  # empty
    pai._load_active_artifact.cache_clear()
    family = SimpleNamespace(metric="high")
    # AMBER band but no artifact => v=0.0 => base sigma byte-identical (fail-open).
    assert era._amber_inflated_predictive_sigma_c(
        _bundle(0.84, 20.0), family=family, decision_time=DECISION
    ) == pytest.approx(0.84)
    # Missing sigma provenance => None (degrade to the conservative 1-step threshold).
    assert era._amber_inflated_predictive_sigma_c(
        SimpleNamespace(provenance_json={}, source_cycle_time=_cyc(20).isoformat()),
        family=family, decision_time=DECISION,
    ) is None


def test_monitor_exit_lane_does_not_import_the_ladder() -> None:
    """RED isolates ENTRY only. The held-position monitor/exit read paths
    (position_belief, portfolio.Position) must NOT consult the entry ladder — proven
    structurally: neither module references the ladder classifier or the RED reason."""
    import inspect

    import src.engine.position_belief as pb
    import src.state.portfolio as pf

    for mod in (pb, pf):
        src = inspect.getsource(mod)
        assert "classify_posterior_staleness" not in src
        assert "REPLACEMENT_STALENESS_RED_ENTRY_ISOLATED" not in src
