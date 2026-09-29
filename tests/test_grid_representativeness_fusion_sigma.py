# Created: 2026-06-17
# Last audited: 2026-09-29
# Authority basis: operator "finish v3" 2026-06-17 — live wiring of zeus_grid_coordinate
#   _precision_upgrade_v3.md rule 5 (sigma_repr^2 added to the fusion Sigma diagonal). RED-on
#   -revert antibody for src/forecast/bayes_precision_fusion.py (Sigma = Sigma + diag(repr_sq)
#   + tau0_eff augment) and src/forecast/grid_representativeness_loader.py.
"""v3 rule 5: ModelInstrument.sigma_repr^2 down-weights an instrument inside the ONE fusion.

Contract (RED on reverting the Sigma-diagonal / tau0 augment):
  1. sigma_repr_sq=0 on every instrument + anchor_sigma_repr_sq=0 -> byte-identical mu*/sd
     (backward compatible; the default / flag-OFF path).
  2. A large sigma_repr_sq on the instrument that PULLS the fused center away from the anchor
     widens that instrument's Sigma diagonal -> the T2 Sigma^-1 gives it LESS weight ->
     mu* moves back TOWARD the anchor. The down-weight is DERIVED by the fusion, never applied
     by hand.
  3. anchor_sigma_repr_sq>0 widens the prior tau0 -> the posterior leans MORE on the
     likelihood instruments (mu* moves away from the anchor center).
  4. The typed loader distinguishes native proof, downscaled not-applicable and
     unproven geometry; the scalar API never maps missing proof to verified zero.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from src.forecast.bayes_precision_fusion import (
    ModelInstrument,
    fuse_bayes_precision_posterior,
)
from src.forecast.grid_representativeness_loader import (
    GridRepresentativenessUnavailable,
    read_grid_representativeness,
    sigma_repr_sq_for,
)


def _instruments(repr_sq_high: float) -> list[ModelInstrument]:
    # Two instruments with IDENTICAL residual structure (equal model-residual variance), so the
    # ONLY thing that differentiates their Sigma diagonal is sigma_repr_sq. gfs pulls HIGH
    # (away from the anchor at 10); icon agrees with the anchor.
    resid = (-0.5, 0.0, 0.5)
    return [
        ModelInstrument(model="gfs_global", z=14.0, train_residuals=resid, n_train=40,
                        sigma_repr_sq=repr_sq_high),
        ModelInstrument(model="icon_global", z=10.0, train_residuals=resid, n_train=40,
                        sigma_repr_sq=0.0),
    ]


def test_zero_sigma_repr_is_byte_identical():
    base = fuse_bayes_precision_posterior(
        anchor_z=10.0, anchor_tau0=1.0, likelihood=_instruments(0.0),
    )
    # An instrument list built with the explicit 0.0 must equal the default-field path.
    default = fuse_bayes_precision_posterior(
        anchor_z=10.0, anchor_tau0=1.0,
        likelihood=[
            ModelInstrument(model="gfs_global", z=14.0, train_residuals=(-0.5, 0.0, 0.5), n_train=40),
            ModelInstrument(model="icon_global", z=10.0, train_residuals=(-0.5, 0.0, 0.5), n_train=40),
        ],
    )
    assert base.mu == default.mu
    assert base.sd == default.sd


def test_sigma_repr_downweights_the_pulling_instrument():
    no_repr = fuse_bayes_precision_posterior(
        anchor_z=10.0, anchor_tau0=1.0, likelihood=_instruments(0.0),
    )
    with_repr = fuse_bayes_precision_posterior(
        anchor_z=10.0, anchor_tau0=1.0, likelihood=_instruments(9.0),  # large repr var on gfs
    )
    # gfs (z=14) is down-weighted -> fused center moves DOWN toward the anchor (10) / icon (10).
    assert with_repr.mu < no_repr.mu, (
        f"sigma_repr did not down-weight the high-pulling instrument: "
        f"with_repr.mu={with_repr.mu:.4f} >= no_repr.mu={no_repr.mu:.4f}"
    )


def test_anchor_sigma_repr_widens_prior_leans_on_instruments():
    # Anchor at 10, instruments both pull to 14. Widening the anchor prior (anchor_sigma_repr_sq)
    # makes the posterior lean MORE on the instruments -> mu* moves UP, away from the anchor.
    lik = [
        ModelInstrument(model="gfs_global", z=14.0, train_residuals=(-0.5, 0.0, 0.5), n_train=40),
        ModelInstrument(model="icon_global", z=14.0, train_residuals=(-0.5, 0.0, 0.5), n_train=40),
    ]
    tight = fuse_bayes_precision_posterior(
        anchor_z=10.0, anchor_tau0=1.0, likelihood=lik, anchor_sigma_repr_sq=0.0,
    )
    wide = fuse_bayes_precision_posterior(
        anchor_z=10.0, anchor_tau0=1.0, likelihood=lik, anchor_sigma_repr_sq=9.0,
    )
    assert wide.mu > tight.mu, (
        f"anchor sigma_repr did not weaken the prior: wide.mu={wide.mu:.4f} <= tight.mu={tight.mu:.4f}"
    )


def _geometry(*, role="native_model_cell", lat=35.1, height=100.0, model="ecmwf_ifs"):
    response = json.dumps({"latitude": lat, "longitude": 139.0, "elevation": height, "daily": {"temperature_2m_max": [20.]}, "daily_units": {"temperature_2m_max": "°C"}}).encode()
    binding = {
        "station": "RJTT", "model": model, "product": "openmeteo/v1/forecast",
        "request_policy": {"models": model, "latitude": 35.0, "longitude": 139.0, "daily": "temperature_2m_max", **({"elevation": "nan"} if role == "native_model_cell" else {})},
        "raw_response_sha256": hashlib.sha256(response).hexdigest(),
        "requested_lat": 35.0, "requested_lon": 139.0,
    }
    cell = {
        "binding": binding, "height_role": role,
        "native_cell_lat": lat, "native_cell_lon": 139.0, "native_cell_elevation_m": height,
        "station_ground_height_m": 20.0,
        "native_height_basis": {"kind": "native_model_surface", "model": model, "source": "synthetic native response fixture"},
        "station_ground_height_basis": {"kind": "settlement_station_ground", "station": "RJTT", "lat": 35., "lon": 139., "elevation_m": 20., "source": "synthetic settlement sensor ground fixture"},
        "response_lat": lat, "response_lon": 139.0, "target_dem_elevation_m": height,
    }
    binding["native_height_basis"] = deepcopy(cell["native_height_basis"])
    binding["station_ground_height_basis"] = deepcopy(cell["station_ground_height_basis"])
    return {"Tokyo": {"station": "RJTT", "models": {model: cell}}}, binding, response


@pytest.mark.parametrize("city,model", [("Atlantis", "ecmwf_ifs"), ("Tokyo", "no_such_model"), ("Tokyo", "ecmwf_ifs")])
def test_legacy_or_missing_geometry_is_unproven_not_zero(city, model):
    result = read_grid_representativeness(city, model)
    assert result.status == "UNPROVEN"
    assert result.variance_c2 is None
    with pytest.raises(GridRepresentativenessUnavailable) as exc:
        sigma_repr_sq_for(city, model)
    assert exc.value.result == result


def test_exact_native_proof_retains_old_formula_and_distance_monotonicity():
    near, binding, raw = _geometry(lat=35.001)
    result = read_grid_representativeness("Tokyo", "ecmwf_ifs", grid_table=near, expected_binding=binding, raw_payload_bytes=raw)
    assert result.status == "APPLICABLE_NATIVE" and result.delta_z_m == -80.0
    assert result.variance_c2 > 0
    far, far_binding, far_raw = _geometry(lat=35.1)
    assert sigma_repr_sq_for("Tokyo", "ecmwf_ifs", grid_table=far, expected_binding=far_binding, raw_payload_bytes=far_raw) > result.variance_c2


def test_default_downscaled_is_not_applicable_not_verified_zero():
    table, binding, raw = _geometry(role="default_downscaled_target_dem")
    result = read_grid_representativeness("Tokyo", "ecmwf_ifs", grid_table=table, expected_binding=binding, raw_payload_bytes=raw)
    assert result.status == "NOT_APPLICABLE_DOWNSCALED"
    assert result.variance_c2 is None and result.delta_z_m is None
    with pytest.raises(GridRepresentativenessUnavailable):
        sigma_repr_sq_for("Tokyo", "ecmwf_ifs", grid_table=table, expected_binding=binding, raw_payload_bytes=raw)


@pytest.mark.parametrize("key", ["station", "model", "product", "request_policy", "raw_response_sha256", "requested_lat", "requested_lon"])
def test_each_foreign_binding_dimension_rejects_native_penalty(key):
    table, binding, raw = _geometry()
    foreign = deepcopy(binding)
    foreign[key] = {"elevation": "default"} if key == "request_policy" else "foreign"
    result = read_grid_representativeness("Tokyo", "ecmwf_ifs", grid_table=table, expected_binding=foreign, raw_payload_bytes=raw)
    assert result.status == "UNPROVEN" and result.variance_c2 is None


@pytest.mark.parametrize("field,value", [("native_height_basis", None), ("native_height_basis", "a label is not proof"), ("station_ground_height_basis", None), ("station_ground_height_basis", {"kind": "airport_reference", "source": "OurAirports"}), ("native_cell_elevation_m", float("nan")), ("station_ground_height_m", None), ("native_cell_lat", 35.8)])
def test_incomplete_or_rebound_native_ground_cannot_become_zero(field, value):
    table, binding, raw = _geometry()
    table["Tokyo"]["models"]["ecmwf_ifs"][field] = value
    result = read_grid_representativeness("Tokyo", "ecmwf_ifs", grid_table=table, expected_binding=binding, raw_payload_bytes=raw)
    assert result.status == "UNPROVEN" and result.variance_c2 is None


@pytest.mark.parametrize("raw", [None, b"{}", b"different entity bytes"])
def test_response_hash_alone_is_not_authenticity_proof(raw):
    table, binding, _ = _geometry()
    assert read_grid_representativeness("Tokyo", "ecmwf_ifs", grid_table=table, expected_binding=binding, raw_payload_bytes=raw).status == "UNPROVEN"


def test_wrong_native_response_resets_on_next_exact_proof():
    table, binding, raw = _geometry()
    assert read_grid_representativeness("Tokyo", "ecmwf_ifs", grid_table=table, expected_binding=binding, raw_payload_bytes=b"foreign response").status == "UNPROVEN"
    result = read_grid_representativeness("Tokyo", "ecmwf_ifs", grid_table=table, expected_binding=binding, raw_payload_bytes=raw)
    assert result.status == "APPLICABLE_NATIVE" and result.variance_c2 > 0.0


def test_builder_preserves_entity_bytes_and_honest_default_role(monkeypatch):
    from scripts import build_grid_representativeness as builder

    raw = b'{ "latitude":35.1, "longitude":139, "elevation":100, "daily":{"temperature_2m_max":[20]}, "daily_units":{"temperature_2m_max":"\\u00b0C"} }'
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return raw
    monkeypatch.setattr(builder.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    cell = builder.cell_meta(35.0, 139.0, "ecmwf_ifs", station="RJTT")
    assert cell["binding"]["raw_response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert cell["binding"]["raw_response_sha256"] != hashlib.sha256(json.dumps(json.loads(raw)).encode()).hexdigest()
    assert cell["target_dem_elevation_m"] == 100 and "delta_z_m" not in cell
    assert cell["native_cell_elevation_m"] is None and cell["station_ground_height_m"] is None
    result = read_grid_representativeness("Tokyo", "ecmwf_ifs", grid_table={"Tokyo": {"station": "RJTT", "models": {"ecmwf_ifs": cell}}}, expected_binding=cell["binding"], raw_payload_bytes=raw)
    assert result.status == "NOT_APPLICABLE_DOWNSCALED"


@pytest.mark.parametrize("role", ["native_model_cell", "default_downscaled_target_dem"])
@pytest.mark.parametrize("unit,settled", [("C", 25.0), ("F", 77.0)])
def test_offline_fitter_uses_only_product_bound_native_geometry(monkeypatch, role, unit, settled):
    import sqlite3
    from datetime import date, timedelta
    from scripts import fit_grid_representativeness as fitter

    table, binding, raw = _geometry(role=role)
    real_read = read_grid_representativeness
    monkeypatch.setattr(fitter, "read_grid_representativeness", lambda *args, **kwargs: real_read(*args, grid_table=table, **kwargs))
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE raw_model_forecasts(city,model,target_date,forecast_value_c,raw_payload_hash,metric,endpoint,lead_days);
        CREATE TABLE settlement_outcomes(city,target_date,temperature_metric,settlement_value,settlement_unit,authority);
    """)
    td = (date.today() - timedelta(days=15)).isoformat()
    con.execute("INSERT INTO raw_model_forecasts VALUES(?,?,?,?,?,?,?,?)", ("Tokyo", "ecmwf_ifs", td, 24., binding["raw_response_sha256"], "HIGH", "previous_runs", 1))
    con.execute("INSERT INTO settlement_outcomes VALUES(?,?,?,?,?,?)", ("Tokyo", td, "HIGH", settled, unit, "VERIFIED"))
    key = ("Tokyo", "ecmwf_ifs", binding["raw_response_sha256"])
    rows, shifts, diag = fitter.build_residual_rows(con, fit_lo=60, holdout=7, lead=1, geometry_bindings={key: binding}, raw_responses={binding["raw_response_sha256"]: raw})
    if role == "native_model_cell":
        assert len(rows) == len(shifts) == 1
        assert rows[0].settlement_residual == pytest.approx(1.0)
        assert rows[0].dz_m == -80.0
    else:
        assert rows == shifts == []
        assert any("NOT_APPLICABLE_DOWNSCALED" in reason for reason in diag["geometry_dispositions"])
    # A current city/model mapping cannot self-certify a historical response.
    rows, shifts, diag = fitter.build_residual_rows(con, fit_lo=60, holdout=7, lead=1)
    assert rows == shifts == [] and any("UNPROVEN" in reason for reason in diag["geometry_dispositions"])
    con.close()


def test_offline_validator_does_not_claim_dem_or_missing_proof_is_an_on_pass(monkeypatch):
    from scripts import validate_grid_representativeness_fusion as validator
    from src.forecast.bayes_precision_fusion import ANCHOR_MODEL

    table, binding, raw = _geometry(role="default_downscaled_target_dem", model=ANCHOR_MODEL)
    real_read = read_grid_representativeness
    monkeypatch.setattr(validator, "read_grid_representativeness", lambda *args, **kwargs: real_read(*args, grid_table=table, **kwargs))
    for bindings, responses, status in [({}, {}, "UNPROVEN"), ({ANCHOR_MODEL: binding}, {binding["raw_response_sha256"]: raw}, "NOT_APPLICABLE_DOWNSCALED")]:
        mu_off, mu_cold, mu_fit, dispositions = validator.fuse_variant(10., 1., [], 0., city="Tokyo", repr_fit=None, geometry_bindings=bindings, raw_responses=responses)
        assert mu_off == 10.0 and mu_cold is None and mu_fit is None
        assert dispositions[ANCHOR_MODEL]["status"] == status


def test_unproven_fitter_main_writes_no_fit_assets(monkeypatch, capsys):
    from types import SimpleNamespace
    from scripts import fit_grid_representativeness as fitter

    monkeypatch.setattr(fitter.sys, "argv", ["fit_grid_representativeness.py"])
    monkeypatch.setattr(fitter.sqlite3, "connect", lambda *args, **kwargs: SimpleNamespace(row_factory=None, close=lambda: None))
    monkeypatch.setattr(fitter, "build_residual_rows", lambda *args, **kwargs: ([], [], {"geometry_dispositions": {"UNPROVEN: no exact bytes": 1}}))
    class ForbiddenWrite:
        def write_text(self, *args, **kwargs): pytest.fail("unproven geometry cannot write fitted assets")
    monkeypatch.setattr(fitter, "REPR_FIT_OUT", ForbiddenWrite())
    monkeypatch.setattr(fitter, "SHIFT_FIT_OUT", ForbiddenWrite())
    assert fitter.main() == 2
    assert "NOT_FITTED" in capsys.readouterr().out


@pytest.mark.parametrize("unit,scale", [("C", 1.0), ("F", 3.24)])
def test_era_legacy_members_keep_unapplied_penalty_status_per_analysis(monkeypatch, unit, scale):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from src.engine import event_reactor_adapter as era
    from src.data import bayes_precision_fusion_history_provider as history

    monkeypatch.setattr(era, "runtime_cities_by_name", lambda: {"Tokyo": SimpleNamespace(settlement_unit=unit)})
    monkeypatch.setattr(era, "_raw_model_members_for_cycle", lambda *args, **kwargs: {"ecmwf_ifs": 20., "gfs_global": 22., "icon_global": 24.})
    monkeypatch.setattr(history, "raw_second_moment_by_model", lambda *args, **kwargs: {"ecmwf_ifs": (2., 40)})
    event = SimpleNamespace(event_type=next(iter(era._FORECAST_DECISION_EVENT_TYPES)), causal_snapshot_id="rmf-Tokyo|2026-09-30|HIGH|2026-09-29")
    family = SimpleNamespace(city="Tokyo", metric="HIGH", target_date="2026-09-30")
    first, second = {}, {}
    args = dict(event=event, family=family, decision_time=datetime(2026, 9, 29, tzinfo=timezone.utc))
    a = era._spine_multimodel_members_for_event(None, **args, geometry_out=first)
    b = era._spine_multimodel_members_for_event(None, **args, geometry_out=second)
    assert a == b and first == second and first is not second
    assert a[2][0][1] == pytest.approx(2.0 * scale)
    assert all(row[3] == 0.0 for row in a[2])
    assert set(first) == {row[0] for row in a[2]}
    assert all(p["status"] == "UNPROVEN" and p["native_penalty_not_applied"] for p in first.values())
    first["ecmwf_ifs"]["status"] = "mutated"
    assert second["ecmwf_ifs"]["status"] == "UNPROVEN"


def test_era_legacy_geometry_status_reaches_forecast_authority_carrier(monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from src.engine import event_reactor_adapter as era
    from src.data import replacement_forecast_bundle_reader as reader

    diagnostic = {"ecmwf_ifs": {"status": "UNPROVEN", "reason": "response binding absent", "native_penalty_not_applied": True}}
    def members(*args, geometry_out, **kwargs):
        geometry_out.update(deepcopy(diagnostic))
        return [20., 22., 24.], "2026-09-28", []
    monkeypatch.setattr(era, "_spine_multimodel_members_for_event", members)
    monkeypatch.setattr(era, "runtime_cities_by_name", lambda: {"Tokyo": SimpleNamespace(settlement_unit="C", timezone="Asia/Tokyo")})
    monkeypatch.setattr(era, "_authority_table_ref", lambda *args: "forecast_posteriors")
    monkeypatch.setattr(reader, "_current_ensemble_snapshot_identity_reason", lambda *args, **kwargs: None)
    monkeypatch.setattr(era, "_replacement_live_input_lag_reason", lambda *args, **kwargs: None)
    monkeypatch.setattr(era, "current_evidence_shape_semantics_mismatch", lambda *args: False)
    monkeypatch.setattr(era, "replacement_probability_bundle_hash", lambda **kwargs: "test-lawful-bundle-hash")
    monkeypatch.setattr(era, "_source_clock_model_count_certificate", lambda *args, **kwargs: (False, None))
    bins = [{"bin_id": "test-bin"}]
    provenance = {"bin_topology": bins, "q_lcb_bootstrap_draws": 10}
    prow = ("openmeteo", "2026-09-28T00:00:00+00:00", "2026-09-28T01:00:00+00:00", "2026-09-28T02:00:00+00:00", "posterior-test-hash", "test-version", 1, "test-family", era.stable_hash(bins), '{"test-bin":0.5}', '{"test-bin":0.4}', '{"test-bin":0.6}', json.dumps(provenance), "{}")
    class Conn:
        def execute(self, sql, params):
            return SimpleNamespace(fetchone=lambda: prow if "FROM forecast_posteriors" in sql else None)
    payload = {}
    out = era._forecast_authority_payload_from_posterior(
        Conn(), event=SimpleNamespace(event_type=next(iter(era._FORECAST_DECISION_EVENT_TYPES)), causal_snapshot_id="rmf-Tokyo|2026-09-30|HIGH|2026-09-28"),
        family=SimpleNamespace(city="Tokyo", target_date="2026-09-30", metric="high"), payload=payload,
        decision_time=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )
    assert out is not None
    assert payload["_edli_grid_representativeness"] == diagnostic
    assert out[0]["grid_representativeness"] == diagnostic
    assert out[0]["grid_representativeness"] is not payload["_edli_grid_representativeness"]


def test_era_geometry_status_passes_candidate_receipt_whitelist_without_family_aliasing(monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from src.engine import event_reactor_adapter as era

    monkeypatch.setattr(era, "_live_yes_probabilities", lambda **kwargs: ({}, {}, {}, {}, {}))
    monkeypatch.setattr(era, "_direction_law_family_center", lambda **kwargs: (None, None))
    monkeypatch.setattr(era, "_direction_law_mu_settled_for_family", lambda **kwargs: None)
    monkeypatch.setattr(era, "_direction_law_settle_value_for_family", lambda **kwargs: None)
    monkeypatch.setattr(era, "_apply_pre_day0_low_carryover_to_spine_members", lambda **kwargs: (kwargs["members_native"], None, None))
    def members(*args, family, geometry_out, **kwargs):
        status = "UNPROVEN" if family.city == "Tokyo" else "NOT_APPLICABLE_DOWNSCALED"
        geometry_out["ecmwf_ifs"] = {"status": status, "reason": "fixture product role", "native_penalty_not_applied": True}
        return [20., 22., 24.], "2026-09-28", [("ecmwf_ifs", 2., 40, 0.), ("gfs_global", None, 0, 0.), ("icon_global", None, 0, 0.)]
    monkeypatch.setattr(era, "_spine_multimodel_members_for_event", members)
    captures, payloads = [], []
    for city in ("Tokyo", "Seoul"):
        capture, payload = {}, {"_edli_grid_representativeness": {"foreign_model": {"status": "APPLICABLE_NATIVE"}}}
        out = era._generate_candidate_proofs(
            event=SimpleNamespace(event_type=next(iter(era._FORECAST_DECISION_EVENT_TYPES)), causal_snapshot_id="fixture-cycle"),
            payload=payload, family=SimpleNamespace(city=city, target_date="2026-09-30", metric="high", family_id=city, candidates=[]),
            snapshot_rows=[], trade_conn=None, forecast_conn=None, calibration_conn=None,
            decision_time=datetime(2026, 9, 29, tzinfo=timezone.utc), provenance_capture=capture,
        )
        assert out == ()
        captures.append(capture["decision_receipt_spine_inputs"]["_edli_grid_representativeness"])
        payloads.append(payload)
    assert captures[0]["ecmwf_ifs"]["status"] == "UNPROVEN"
    assert captures[1]["ecmwf_ifs"]["status"] == "NOT_APPLICABLE_DOWNSCALED"
    assert all("foreign_model" not in c for c in captures)
    payloads[0]["_edli_grid_representativeness"]["ecmwf_ifs"]["status"] = "mutated"
    assert captures[0]["ecmwf_ifs"]["status"] == "UNPROVEN"
    assert captures[1]["ecmwf_ifs"]["status"] == "NOT_APPLICABLE_DOWNSCALED"
