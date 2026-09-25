# Created: 2026-09-24
# Last reused or audited: 2026-09-24
# Authority basis: Day0 remaining-center settlement residual study 2026-09-24;
#   docs/authority/replacement_final_form_2026_06_09.md "Day0 conditional
#   remaining-path operator" (settlement-graded remaining-member center shift).
"""Contracts for the Day0 remaining-carrier center shift.

(a) an active cell's shift moves q the fitted direction, in the point q AND the draws;
(b) artifact absent -> q, samples and identity are byte-identical to the pre-change
    carrier (golden values captured from the unmodified builder);
(c) a degF carrier receives the shift scaled by 9/5;
(d) an inactive or unfitted cell serves no shift and says so;
(e) the Day0 semantics revision is bumped and stamped into q_version;
plus: the boundary and the typed final centers never move, and the entry and held
adapter rebuilds apply the same lookup and stamp the same provenance.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest

from src.calibration import day0_remaining_bias as mod
from src.calibration.day0_remaining_bias import (
    APPLIED,
    ARTIFACT_UNAVAILABLE,
    INACTIVE_CELL,
    SCHEMA_VERSION,
    cell_key,
    day0_remaining_bias,
)
from src.config import runtime_cities_by_name
from src.contracts.settlement_semantics import SettlementSemantics
from src.data.day0_hourly_vectors import (
    build_day0_remaining_probability_carrier,
    day0_remaining_carrier_identity_inputs,
)

# Atlanta (America/New_York, EDT = UTC-4): 13:00Z is local 09:00 -> cell high|8.
DECISION = datetime(2026, 9, 24, 13, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clear_cache():
    mod.reset_cache()
    yield
    mod.reset_cache()


def _install(tmp_path, monkeypatch, artifact: object) -> None:
    path = tmp_path / mod.ARTIFACT_FILENAME
    path.write_text(
        artifact if isinstance(artifact, str) else json.dumps(artifact), encoding="utf-8"
    )
    monkeypatch.setattr(mod, "artifact_path", lambda: path)


def _artifact(*, fit_date: str = "2026-09-24", **cells: dict) -> dict:
    return {"schema_version": SCHEMA_VERSION, "fit_date": fit_date, "cells": cells}


def _carrier(city: str, unit: str, metric: str, fut, fin, boundary, bounds, bias=0.0):
    semantics = SettlementSemantics.for_city(runtime_cities_by_name()[city])
    return build_day0_remaining_probability_carrier(
        future_extremes_c=fut,
        final_extreme_centers_c=fin,
        boundary_scenarios=((boundary, 0.95), (None, 0.05)),
        metric=metric,
        path_error_sigma_c=0.8,
        instrument_sigma_c=0.3,
        bin_bounds_c=bounds,
        n_point=1000,
        n_samples=500,
        identity_inputs=day0_remaining_carrier_identity_inputs(
            city=city,
            unit=unit,
            decision_time_utc="2026-09-24T03:00:00+00:00",
            station_id="VHHH" if city == "Hong Kong" else "KATL",
            preliminary_survival_identity="abc123",
        ),
        settlement_semantics=semantics,
        remaining_center_bias_native=bias,
    )


ATL_HIGH = ("Atlanta", "F", "high", (84.1, 85.3, 83.8), (), 82.0,
            [(None, 81), (82, 83), (84, 85), (86, 87), (88, None)])
HK_HIGH = ("Hong Kong", "C", "high", (31.8, 30.6, 31.0), (32.0,), 28.3,
           [(None, 28), (29, 29), (30, 30), (31, 31), (32, 32), (33, None)])
ATL_LOW = ("Atlanta", "F", "low", (66.2, 65.1, 67.0), (), 68.0,
           [(None, 63), (64, 65), (66, 67), (68, None)])

# Captured from the builder BEFORE remaining_center_bias_native existed
# (worktree base 472bd6757): (operator, content_identity, sha256(samples json), q).
GOLDEN = {
    "atl_high": (
        "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
        "b794ed2246cdad22bb601cdd2c634c94df1046b5f154d57155a2142adef1d203",
        "18c7f728bc34fcb42cdc38182a49553dcdaa5e559140269df6044acb4e9fd142",
        [7.878417705639014e-05, 0.20711471936196632, 0.6323320431119992,
         0.15878933324604055, 0.0016851201029375992],
    ),
    "hk_high": (
        "typed_remaining_and_final_extreme_gaussian_v3",
        "764dcc7ffcef77526ffb66e1eb530bb7f41862ea37f481f56221d518fe1a349a",
        "c7d22c9289d106dd65c3c66b29afc8d7da34a9c12b81ec4ca9750b71b6e1b959",
        [0.010229670978899038, 0.0871113282409378, 0.27156648011837825,
         0.36133437569554105, 0.21648063954358293, 0.05327750542266089],
    ),
    "atl_low": (
        "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2",
        "348a92445a781588d4eacff802a7c136d69024d7fe7ca6bdacf7e821f3362c7b",
        "b4be00125e68cb8a6478be8c6872503a324cc8766d3d3aeca4d19b63f0fc604c",
        [0.010455770549529593, 0.2982290625756278, 0.5760643664840178,
         0.11525080039082476],
    ),
}


def _mean_bin(values) -> float:
    return float(np.dot(np.asarray(values, dtype=float), np.arange(len(values))))


# (b) ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "case"), (("atl_high", ATL_HIGH), ("hk_high", HK_HIGH), ("atl_low", ATL_LOW))
)
def test_zero_shift_is_byte_identical_to_the_pre_change_carrier(name, case) -> None:
    operator, identity, samples_hash, q = GOLDEN[name]
    carrier = _carrier(*case)

    assert carrier["operator"] == operator
    assert carrier["content_identity"] == identity
    assert hashlib.sha256(json.dumps(carrier["samples"]).encode()).hexdigest() == samples_hash
    assert carrier["q"] == q


def test_artifact_absent_serves_zero_with_visible_status(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "artifact_path", lambda: tmp_path / "absent.json")

    bias = day0_remaining_bias(
        city="Atlanta", metric="high", decision_time=DECISION, timezone_name="America/New_York"
    )

    assert (bias.shift_c, bias.status, bias.artifact) == (0.0, ARTIFACT_UNAVAILABLE, None)
    assert bias.provenance() == {
        "day0_remaining_center_bias_c": 0.0,
        "day0_remaining_bias_status": "artifact_unavailable",
        "day0_remaining_bias_artifact": None,
    }
    # Zero shift is exactly the golden pre-change carrier.
    assert _carrier(*ATL_HIGH, bias=bias.shift_c)["q"] == GOLDEN["atl_high"][3]


@pytest.mark.parametrize(
    "artifact",
    (
        "{not json",
        _artifact(**{"high|8": {"b_c": 0.5, "active": True}}) | {"schema_version": 99},
        _artifact(**{"high|8": {"b_c": 9.0, "active": True}}),  # past the sanity rail
        _artifact(fit_date="2026-09-10", **{"high|8": {"b_c": 0.5, "active": True}}),  # stale
        _artifact(fit_date="2026-09-25", **{"high|8": {"b_c": 0.5, "active": True}}),  # future
    ),
)
def test_unusable_artifact_serves_zero(tmp_path, monkeypatch, artifact) -> None:
    _install(tmp_path, monkeypatch, artifact)

    bias = day0_remaining_bias(
        city="Atlanta", metric="high", decision_time=DECISION, timezone_name="America/New_York"
    )

    assert (bias.shift_c, bias.status) == (0.0, ARTIFACT_UNAVAILABLE)


# (d) ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cells",
    (
        {"high|8": {"b_c": 0.5, "active": False, "n": 150}},  # low-n / failed gate
        {"high|10": {"b_c": 0.5, "active": True}},  # a different band only
        {"low|8": {"b_c": 0.5, "active": True}},  # the other metric only
    ),
)
def test_inactive_or_unfitted_cell_serves_no_shift(tmp_path, monkeypatch, cells) -> None:
    _install(tmp_path, monkeypatch, _artifact(**cells))

    bias = day0_remaining_bias(
        city="Atlanta", metric="high", decision_time=DECISION, timezone_name="America/New_York"
    )

    assert bias.shift_c == 0.0
    assert bias.status == INACTIVE_CELL
    assert bias.artifact is not None and bias.artifact.startswith("2026-09-24:")


def test_active_cell_uses_station_value_then_pool(tmp_path, monkeypatch) -> None:
    _install(
        tmp_path,
        monkeypatch,
        _artifact(**{"high|8": {"b_c": 0.5, "active": True, "stations": {"Atlanta": 0.3}}}),
    )

    atlanta = day0_remaining_bias(
        city="Atlanta", metric="HIGH", decision_time=DECISION, timezone_name="America/New_York"
    )
    pooled = day0_remaining_bias(
        city="Miami", metric="high", decision_time=DECISION, timezone_name="America/New_York"
    )

    assert (atlanta.shift_c, atlanta.status) == (0.3, APPLIED)
    assert (pooled.shift_c, pooled.status) == (0.5, APPLIED)
    assert cell_key("high", 9.99) == "high|8" and cell_key("low", 23.5) == "low|22"


# (a) and (c) -------------------------------------------------------------------


def test_warm_shift_moves_point_q_and_draws_up_and_rebinds_identity() -> None:
    base = _carrier(*HK_HIGH)
    shifted = _carrier(*HK_HIGH, bias=0.5)

    assert _mean_bin(shifted["q"]) > _mean_bin(base["q"])
    assert _mean_bin(np.mean(shifted["samples"], axis=0)) > _mean_bin(
        np.mean(base["samples"], axis=0)
    )
    assert shifted["content_identity"] != base["content_identity"]


def test_shift_equals_moving_only_the_remaining_members() -> None:
    city, unit, metric, fut, fin, boundary, bounds = HK_HIGH
    shifted = _carrier(*HK_HIGH, bias=0.5)
    moved = _carrier(city, unit, metric, tuple(v + 0.5 for v in fut), fin, boundary, bounds)

    # Same distribution: the boundary and the typed final center did not move.
    assert shifted["q"] == moved["q"]
    moved_final = _carrier(
        city, unit, metric, tuple(v + 0.5 for v in fut), tuple(v + 0.5 for v in fin),
        boundary, bounds,
    )
    assert shifted["q"] != moved_final["q"]


def test_fahrenheit_adapter_rebuild_scales_the_shift_by_nine_fifths(
    tmp_path, monkeypatch
) -> None:
    calls = _run_adapter_rebuild(tmp_path, monkeypatch, "entry_current_remaining_path", 0.5)

    assert calls[0]["remaining_center_bias_native"] == pytest.approx(0.5 * 9.0 / 5.0)


# Adapter wiring: one lookup, both callers, replay binds the persisted shift -------


def _noaa_likelihood(station: str, cutoff: str) -> dict:
    likelihood: dict[str, object] = {
        "semantics": "same_station_preliminary_report_survival_likelihood_v1",
        "cutoff": cutoff,
        "successes": 19,
        "failures": 1,
        "unconfirmed_awc_ids": [],
        "alpha": 19.5,
        "beta": 1.5,
        "station_id": station,
        "source_channel_pair": {
            "awc": "aviationweather_metar",
            "ogimet": f"ogimet_metar_{station.lower()}",
        },
        "boundary_survival_probability": 0.95,
    }
    likelihood["identity_hash"] = hashlib.sha256(
        json.dumps(
            {k: likelihood[k] for k in likelihood if k != "boundary_survival_probability"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return likelihood


FUTURE_C = (28.5, 29.0, 30.5, 31.25)
BOUNDS_F = [(None, 77)] + [(v, v + 1) for v in range(78, 96, 2)] + [(96, None)]


def _atlanta_payload(authority_kind: str) -> tuple[dict, SimpleNamespace]:
    from src.types.market import Bin

    cutoff = DECISION.isoformat()
    family = SimpleNamespace(
        city="Atlanta",
        target_date="2026-09-24",
        metric="high",
        candidates=[
            SimpleNamespace(bin=Bin(low, high, "F", f"bin-{i}"))
            for i, (low, high) in enumerate(BOUNDS_F)
        ],
    )
    payload = {
        "metric": "high",
        "target_date": "2026-09-24",
        "rounded_value": 84.0,
        "settlement_source": "aviationweather_metar",
        "evidence_finality": "PROVISIONAL_CURRENT_SNAPSHOT",
        "_edli_day0_probability_boundary_native": 84.0,
        "_edli_day0_source_clock_predictive_sigma_native": 1.2,
        "_edli_day0_provisional_boundary_survival_probability": 0.95,
        "_edli_day0_provisional_revision_likelihood": _noaa_likelihood("KATL", cutoff),
        "_edli_day0_remaining_vector_witness": {
            "vector_id": "same-vector",
            "expected_models": ["ecmwf_ifs"],
            "actual_models": ["ecmwf_ifs"],
            "capture_times_by_model_utc": {"ecmwf_ifs": cutoff},
            "provider_source_cycle_time_by_model_utc": {"ecmwf_ifs": cutoff},
            "provider_source_available_at_by_model_utc": {"ecmwf_ifs": cutoff},
            "source_run_id_by_model": {"ecmwf_ifs": "source-run"},
            "provider_run_id_by_model": {"ecmwf_ifs": "provider-run"},
            "request_hash_by_model": {"ecmwf_ifs": "request-hash"},
        },
    }
    if authority_kind == "held_current_remaining_path":
        payload["_edli_day0_redecision_authority_scope"] = (
            "held_exposure_current_bundle_day0_only_v1"
        )
    elif authority_kind == "held_a_prime":
        payload["_edli_day0_redecision_authority_scope"] = "held_exposure_current_day0_only_v1"
    return payload, family


def _run_adapter_rebuild(tmp_path, monkeypatch, authority_kind: str, b_c: float | None):
    import src.data.day0_hourly_vectors as hourly
    import src.engine.event_reactor_adapter as era

    if b_c is None:
        monkeypatch.setattr(mod, "artifact_path", lambda: tmp_path / "absent.json")
    else:
        _install(tmp_path, monkeypatch, _artifact(**{"high|8": {"b_c": b_c, "active": True}}))
    monkeypatch.setattr(era, "_day0_extra_member_sigma_native", lambda **_kwargs: 0.7)
    original = hourly.build_day0_remaining_probability_carrier
    calls: list[dict] = []

    def recording(**kwargs):
        calls.append(dict(kwargs))
        return original(**kwargs)

    monkeypatch.setattr(hourly, "build_day0_remaining_probability_carrier", recording)
    payload, family = _atlanta_payload(authority_kind)
    era._rebuild_decision_time_day0_carrier(
        payload=payload,
        family=family,
        unit="F",
        decision_time=DECISION,
        future_extremes_c=FUTURE_C,
        authority_kind=authority_kind,
        entry_authority=authority_kind == "entry_current_remaining_path",
        held_shared_current_remaining_path=(
            authority_kind == "held_shared_current_remaining_path"
        ),
    )
    calls.append(payload)
    return calls


@pytest.mark.parametrize(
    "authority_kind",
    (
        "entry_current_remaining_path",
        "held_current_remaining_path",
        "held_shared_current_remaining_path",
        "held_a_prime",
    ),
)
def test_entry_and_held_rebuilds_apply_and_stamp_the_same_shift(
    tmp_path, monkeypatch, authority_kind
) -> None:
    calls = _run_adapter_rebuild(tmp_path, monkeypatch, authority_kind, 0.5)
    builder_kwargs, payload = calls[0], calls[-1]

    assert builder_kwargs["remaining_center_bias_native"] == pytest.approx(0.9)
    assert payload["_edli_day0_remaining_center_bias_c"] == 0.5
    assert payload["_edli_day0_remaining_bias_status"] == APPLIED
    assert str(payload["_edli_day0_remaining_bias_artifact"]).startswith("2026-09-24:")
    # The persisted member vector stays unshifted (the fitter's residual basis).
    assert payload["_edli_day0_remaining_carrier_future_extremes_c"] == list(FUTURE_C)


def test_adapter_rebuild_without_artifact_is_unshifted_and_says_so(
    tmp_path, monkeypatch
) -> None:
    calls = _run_adapter_rebuild(
        tmp_path, monkeypatch, "entry_current_remaining_path", None
    )

    assert calls[0]["remaining_center_bias_native"] == 0.0
    assert calls[-1]["_edli_day0_remaining_bias_status"] == ARTIFACT_UNAVAILABLE
    assert calls[-1]["_edli_day0_remaining_center_bias_c"] == 0.0


def test_replay_reproduces_the_persisted_shift_not_a_fresh_lookup(
    tmp_path, monkeypatch
) -> None:
    import src.engine.event_reactor_adapter as era

    calls = _run_adapter_rebuild(tmp_path, monkeypatch, "entry_current_remaining_path", 0.5)
    payload = calls[-1]
    # A later artifact change must not alter the replay of the certificate that was built.
    mod.reset_cache()
    monkeypatch.setattr(mod, "artifact_path", lambda: tmp_path / "gone.json")
    payload.pop("_edli_day0_redecision_authority_scope", None)
    city = runtime_cities_by_name()["Atlanta"]
    from src.types.market import Bin

    replay = era._day0_remaining_p_raw_vector(
        np.asarray(FUTURE_C, dtype=float) * 9.0 / 5.0 + 32.0,
        city=city,
        settlement_semantics=SettlementSemantics.for_city(city),
        bins=[Bin(low, high, "F", f"bin-{i}") for i, (low, high) in enumerate(BOUNDS_F)],
        payload=payload,
        extra_member_sigma=0.0,
        decision_time=DECISION,
    )

    assert replay.tolist() == pytest.approx(payload["_edli_day0_remaining_carrier_q"])


# (e) ---------------------------------------------------------------------------


def test_semantics_revision_is_bumped_and_stamped() -> None:
    from src.events.day0_authority import (
        DAY0_PROBABILITY_SEMANTICS_REVISION,
        bind_day0_probability_semantics,
        day0_probability_semantics_revision,
    )

    assert DAY0_PROBABILITY_SEMANTICS_REVISION == (
        "day0_settlement_channel_revision_model_v22"
    )
    stamped = bind_day0_probability_semantics("q-hash")
    assert day0_probability_semantics_revision(stamped) == (
        "day0_settlement_channel_revision_model_v22"
    )
    assert day0_probability_semantics_revision(
        "day0-semrev:day0_remaining_center_bias_v20:q-hash"
    ) != DAY0_PROBABILITY_SEMANTICS_REVISION
