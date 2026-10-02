# Created: 2026-10-02
# Last reused/audited: 2026-10-02
# Authority basis: Day0 law H = max(H_confirmed, H_remaining) (60f1f591b, c27684d0c); the
#   remaining-window read is keyed on the LAST OBSERVATION, never the decision clock
#   (day0 causal cut replay 2026-09-06). Live 2026-10-02: held Chongqing 10-02 lost every
#   post-midnight run at 16:00Z (local day end) while its last observation was 15:05Z.
#   Current slice also defends immutable source-window parity at ordinary/held q reads.
"""A stored body's local-day coverage proof is re-parsed at the family's last observation."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from src.data import replacement_current_value_serving as serving
from src.data.bayes_precision_fusion_download import _parse_batched_single_runs_payload

TZ = "Asia/Shanghai"  # UTC+8: 2026-10-02 local day is [10-01T16Z, 10-02T16Z)
DAY = date(2026, 10, 2)
TAU = "2026-10-02T15:05:56+00:00"


def _payload(first_utc_hour: int) -> dict:
    """An hourly body whose first sample is ``first_utc_hour`` UTC on 10-02 (a 06Z run sees 14:00 local)."""
    start = datetime(2026, 10, 2, first_utc_hour, tzinfo=timezone.utc)
    times = [(start + timedelta(hours=h)).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M")
             for h in range(0, 30)]
    return {"utc_offset_seconds": 28800, "timezone": TZ, "hourly_units": {"temperature_2m": "°C"},
            "hourly": {"time": times, "temperature_2m": [20.0 + (h % 5) for h in range(30)]}}


def _row(cut: str, tau: str | None) -> dict:
    row = {"physical_proof_cutoff": cut, "captured_at": "2026-10-02T12:32:04+00:00",
           "timezone_requested": TZ, "target_date": DAY.isoformat()}
    if tau is not None:
        row["day0_remaining_from"] = tau
    return row


def _covers(row: dict, payload: dict) -> bool:
    values = _parse_batched_single_runs_payload(payload, ["ecmwf_ifs"], DAY, TZ,
                                                decision_at=serving._remaining_window_boundary(row))
    return "ecmwf_ifs" in values


def test_post_day_run_issued_after_midnight_stays_proven_at_its_last_observation():
    # 06Z ecmwf/ukmo body: first slot 06Z = 14:00 local, so it owns [15Z, 16Z) at tau=15:05Z.
    body = _payload(6)
    assert _covers(_row("2026-10-02T16:30:00+00:00", TAU), body)
    assert serving._remaining_window_boundary(_row("2026-10-02T16:30:00+00:00", TAU)) == TAU


@pytest.mark.parametrize("tau", [None, "2026-10-02T16:00:00+00:00", "2026-10-02T16:20:00+00:00"])
def test_without_an_in_day_tau_the_whole_day_law_still_rejects_the_partial_run(tau):
    body = _payload(6)
    row = _row("2026-10-02T16:30:00+00:00", tau)
    assert serving._remaining_window_boundary(row) == "2026-10-02T16:30:00+00:00"
    assert not _covers(row, body)


def test_tau_after_the_cut_or_unparseable_is_never_admitted():
    assert serving._remaining_window_boundary(_row("2026-10-02T16:30:00+00:00", "2026-10-02T17:00:00+00:00")) \
        == "2026-10-02T16:30:00+00:00"
    assert serving._remaining_window_boundary(_row("2026-10-02T16:30:00+00:00", "not-a-time")) \
        == "2026-10-02T16:30:00+00:00"
    assert serving._remaining_window_boundary(_row("2026-10-02T16:30:00+00:00", "2026-10-02T15:05:56")) \
        == "2026-10-02T16:30:00+00:00"


@pytest.mark.parametrize("cut", ["2026-10-02T15:30:00+00:00", "2026-10-01T10:00:00+00:00"])
def test_pre_day_end_boundary_is_the_decision_cut_whatever_tau_is(cut):
    for tau in (None, TAU, "2026-10-02T09:00:00+00:00"):
        assert serving._remaining_window_boundary(_row(cut, tau)) == cut


def test_identity_text_without_tau_is_byte_for_byte_unchanged():
    schema = serving.CurrentValueServingSchema(
        has_captured_at=True, has_source_available_at=True, has_recorded_at=True, has_coverage_status=True,
        product_identity_columns=serving._PRODUCT_IDENTITY_COLUMNS, has_artifacts=True)
    base = serving._product_identity_select(schema, decision_iso="2026-10-02T16:30:00+00:00")
    assert serving._product_identity_select(schema, decision_iso="2026-10-02T16:30:00+00:00",
                                            day0_remaining_from_iso=None) == base
    assert "day0_remaining_from" not in base
    with_tau = serving._product_identity_select(schema, decision_iso="2026-10-02T16:30:00+00:00",
                                                day0_remaining_from_iso=TAU)
    assert f"'day0_remaining_from', '{TAU}'" in with_tau


def test_the_proof_reparse_reads_the_boundary_helper():
    """Wiring: the coverage re-parse consumes the boundary, not the raw cut."""
    import inspect
    source = inspect.getsource(serving._physical_response_has_authority)
    assert "decision_at=_remaining_window_boundary(row)" in source
    assert 'decision_at=str(row.get("physical_proof_cutoff")' not in source


def test_frozen_identity_carries_tau_only_when_supplied():
    """The commit/held replay re-proves the same remaining window the selector proved."""
    import inspect
    source = inspect.getsource(serving._physical_response_provenance)
    assert 'original_identity["day0_remaining_from"] = row["day0_remaining_from"]' in source
    assert 'if row.get("day0_remaining_from") is not None' in source


def test_every_bpf_current_read_in_the_materializer_names_the_family_tau(monkeypatch):
    import inspect
    from src.data import replacement_forecast_materializer as m
    source = inspect.getsource(m._replacement_bayes_precision_fusion_override)
    import re
    reads = re.findall(r"read_(?:current_instrument_values|freshest_coherent_instrument_values)\((.*?)\n\s*\)",
                       source, flags=re.S)
    assert len(reads) == 2
    assert all("day0_remaining_from_iso=_day0_remaining_from_iso(request)" in call for call in reads)
    shared_call = re.search(r"_read_current_capture_serving\((.*?)\n\s*\)", source, flags=re.S)
    assert shared_call is not None
    assert "day0_remaining_from_iso=_day0_remaining_from_iso(request)" in shared_call.group(1)
    seen = []
    def read_current(_conn, **kwargs):
        seen.append((kwargs["day0_remaining_from_iso"], kwargs["decision_time_iso"]))
        return {}
    monkeypatch.setattr(serving, "read_current_instrument_values", read_current)
    from types import SimpleNamespace
    producer_tau = m._day0_remaining_from_iso(SimpleNamespace(day0_observed_extreme_observation_time=TAU))
    assert m._read_current_capture_serving(None, city="Shanghai", metric="high",
        target_date=DAY.isoformat(), source_cycle_time_iso="2026-10-02T12:00:00+00:00",
        decision_time_iso="2026-10-02T16:30:00+00:00", day0_remaining_from_iso=producer_tau,
        lat=31.2, lon=121.5, lead_days=0, configured=()) == {}
    assert seen == [(TAU, "2026-10-02T16:30:00+00:00")]


def _remaining_serving_certificate(tmp_path, monkeypatch, metric):
    """Inert transport; real body binding, persistence, surface and serving replay."""
    import sqlite3
    from src.data import bayes_precision_fusion_download as dl
    from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema
    from tests.test_openmeteo_cell_selection_and_elevation_are_product_identity import (
        _controlled_model_static_transport, _persist_exact_provider_body,
    )
    _controlled_model_static_transport.__wrapped__(tmp_path, monkeypatch)
    conn = sqlite3.connect(":memory:")
    ensure_replacement_forecast_live_schema(conn)
    day = "2026-10-02"
    cycle = "2026-10-02T12:00:00+00:00"
    cut = "2026-10-02T22:18:20+00:00"
    tau = "2026-10-02T21:32:17+00:00"
    original_bind = dl._bind_physical_response

    def bind_partial(payload, **kwargs):
        payload = {**payload, "hourly": {key: values[14:] for key, values in payload["hourly"].items()}}
        body = json.dumps(payload).encode()
        stamp = kwargs["captures"][-1][1]
        kwargs.update(captures=[(body, stamp)], network_captures=[(body, stamp, {"content-type": "application/json"})])
        return original_bind(payload, **kwargs)

    persist = lambda run, stamp, **kwargs: _persist_exact_provider_body(
        conn, tmp_path, city="Paris", metric=metric, target_date=day,
        model="icon_global", cycle=run, captured=stamp, value=20.0, network=True, **kwargs,
    )
    persist("2026-10-02T00:00:00+00:00", "2026-10-02T10:00:00+00:00")
    monkeypatch.setattr(dl, "_bind_physical_response", bind_partial)
    persist(cycle, "2026-10-02T20:00:00+00:00")

    def read(tau_value):
        return serving.read_current_instrument_values(conn, city="Paris", metric=metric,
            target_date=day, source_cycle_time_iso=cycle, decision_time_iso=cut,
            day0_remaining_from_iso=tau_value)["icon_global"]

    consumed, full = read(tau), read(None)
    assert consumed.raw_model_forecast_id != full.raw_model_forecast_id
    provenance = {"day0_provisional_observation": {
        "active": True, "metric": metric, "source": "noaa_wrh_LFPG",
        "observation_time": tau, "observed_extreme_c": 20.0, "unit": "C",
    }, "openmeteo_anchor_artifact_id": _owned_anchor_artifact(conn, tmp_path, metric=metric,
        day=day, cycle=cycle, captured="2026-10-02T20:00:00+00:00"),
        "bayes_precision_fusion": {"used_models": ["icon_global"],
        "current_evidence_shape": {"source_cycle_time": cycle},
        "current_value_serving": {"icon_global": consumed.as_provenance()}}}
    return conn, provenance, full, cycle, cut, tau, persist


def _owned_anchor_artifact(conn, tmp_path, *, metric, day, cycle, captured):
    """A real Open-Meteo IFS9 anchor body through the production manifest writer."""
    from zoneinfo import ZoneInfo
    from src.config import runtime_cities_by_name
    from src.data.openmeteo_ecmwf_ifs9_anchor import (
        OpenMeteoEcmwfIfs9AnchorRequest, build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest,
    )
    from src.data.raw_forecast_artifact_manifest import write_manifest_to_db
    city = runtime_cities_by_name()["Paris"]
    target = datetime.fromisoformat(day)
    payload = {"latitude": city.lat, "longitude": city.lon, "elevation": 45.0, "timezone": city.timezone,
        "utc_offset_seconds": int(target.replace(tzinfo=ZoneInfo(city.timezone)).utcoffset().total_seconds()),
        "hourly_units": {"temperature_2m": "°C"},
        "hourly": {"time": [(target + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(24)],
                   "temperature_2m": [20.0] * 24}}
    path = tmp_path / "anchor.json"
    path.write_bytes((json.dumps(payload, indent=2) + "\n").encode())
    manifest = build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest(path,
        request=OpenMeteoEcmwfIfs9AnchorRequest(city.lat, city.lon, datetime.fromisoformat(cycle), city.timezone),
        metric=metric, source_available_at=captured, captured_at=captured,
        product_metadata={"city": city.name, "target_date": day})
    return write_manifest_to_db(conn, manifest)


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("held", (False, True))
def test_actual_ordinary_and_held_lag_replays_immutable_day0_window(
    tmp_path, monkeypatch, metric, held
):
    from src.data.replacement_input_hwm import replacement_live_input_lag_reason
    conn, provenance, _full, cycle, cut, _tau, _persist = _remaining_serving_certificate(tmp_path, monkeypatch, metric)
    try:
        assert replacement_live_input_lag_reason(conn, city="Paris", target_date="2026-10-02",
            metric=metric, decision_time=datetime.fromisoformat("2026-10-02T22:30:00+00:00"),
            posterior_source_cycle_time=cycle, posterior_computed_at=cut,
            posterior_provenance=provenance, held_redecision=held) is None
    finally:
        conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
def test_actual_lag_without_day0_witness_preserves_fulltarget(tmp_path, monkeypatch, metric):
    from src.data.replacement_input_hwm import replacement_live_input_lag_reason
    conn, provenance, full, cycle, cut, _tau, _persist = _remaining_serving_certificate(tmp_path, monkeypatch, metric)
    provenance.pop("day0_provisional_observation")
    provenance["bayes_precision_fusion"]["current_value_serving"]["icon_global"] = full.as_provenance()
    try:
        assert replacement_live_input_lag_reason(conn, city="Paris", target_date="2026-10-02",
            metric=metric, decision_time=datetime.fromisoformat("2026-10-02T22:30:00+00:00"),
            posterior_source_cycle_time=cycle, posterior_computed_at=cut,
            posterior_provenance=provenance) is None
    finally:
        conn.close()


def _consumed_body(conn, provenance):
    """The consumed icon row's body artifact as the posterior recorded it, and on disk."""
    proof = provenance["bayes_precision_fusion"]["current_value_serving"]["icon_global"]["physical_response"]
    artifact_id = int(proof["artifact_id"])
    path, sha = conn.execute(
        "SELECT artifact_path, sha256 FROM raw_forecast_artifacts WHERE artifact_id=?", (artifact_id,),
    ).fetchone()
    return artifact_id, path, sha, proof


@pytest.mark.parametrize("metric", ("high", "low"))
def test_same_raw_real_body_change_is_refresh_debt_after_window_alignment(tmp_path, monkeypatch, metric):
    """A newer body BESIDE the consumed one never revokes the proof it was served on.

    The second capture binds a different body for the same raw row (no new raw
    row is written); the consumed body artifact, its sha and its receipt stay as
    the posterior recorded them, so the consumed proof re-verifies.
    """
    import hashlib
    from src.data import bayes_precision_fusion_download as dl
    from src.data.replacement_input_hwm import (
        replacement_input_refresh_reason, replacement_live_input_lag_reason,
    )
    conn, provenance, _full, cycle, cut, _tau, persist = _remaining_serving_certificate(tmp_path, monkeypatch, metric)
    artifact_id, path, sha, proof = _consumed_body(conn, provenance)
    old_bind = dl._bind_physical_response
    def changed_body(payload, **kwargs):
        return old_bind({**payload, "generationtime_ms": 1.0}, **kwargs)
    monkeypatch.setattr(dl, "_bind_physical_response", changed_body)
    persist(cycle, "2026-10-02T22:20:00+00:00", expected_written=0)
    try:
        # Beside, not over: the consumed artifact still names the same bytes.
        assert _consumed_body(conn, provenance)[1:3] == (path, sha)
        assert hashlib.sha256(open(path, "rb").read()).hexdigest() == sha == proof["entity_body_sha256"]
        assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts WHERE artifact_id > ?",
            (artifact_id,)).fetchone()[0] > 0
        kwargs = dict(city="Paris", target_date="2026-10-02",
            metric=metric, decision_time=datetime.fromisoformat("2026-10-02T22:30:00+00:00"),
            posterior_source_cycle_time=cycle, posterior_computed_at=cut,
            posterior_provenance=provenance, held_redecision=True)
        assert replacement_live_input_lag_reason(conn, **kwargs, use_memo=False) is None
        assert "physical_proof_dependency_changed" in replacement_input_refresh_reason(conn, **kwargs)
    finally:
        conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
def test_certificate_fixture_anchor_is_checked_not_assumed(tmp_path, monkeypatch, metric):
    """The fixture's anchor passes the real check, so a changed anchor body is refused."""
    from src.data.replacement_input_hwm import replacement_live_input_lag_reason
    conn, provenance, _full, cycle, cut, _tau, _persist = _remaining_serving_certificate(tmp_path, monkeypatch, metric)
    kwargs = dict(city="Paris", target_date="2026-10-02", metric=metric,
        decision_time=datetime.fromisoformat("2026-10-02T22:30:00+00:00"),
        posterior_source_cycle_time=cycle, posterior_computed_at=cut,
        posterior_provenance=provenance, use_memo=False)
    try:
        assert replacement_live_input_lag_reason(conn, **kwargs) is None
        (tmp_path / "anchor.json").write_bytes(b"{}\n")
        assert "openmeteo_anchor_artifact_payload_identity_mismatch" in replacement_live_input_lag_reason(conn, **kwargs)
    finally:
        conn.close()


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("damage", ("bytes_changed_in_place", "artifact_removed"))
def test_consumed_body_damaged_blocks_on_both_projections(tmp_path, monkeypatch, metric, damage):
    """The consumed body itself changed or gone is intrinsic invalidity: BLOCKED everywhere."""
    from src.data.replacement_input_hwm import (
        replacement_input_refresh_reason, replacement_live_input_lag_reason,
    )
    conn, provenance, _full, cycle, cut, _tau, _persist = _remaining_serving_certificate(tmp_path, monkeypatch, metric)
    artifact_id, path, _sha, _proof = _consumed_body(conn, provenance)
    if damage == "bytes_changed_in_place":
        body = bytearray(open(path, "rb").read())
        body[-2] = ord(" ") if body[-2] != ord(" ") else ord("\t")
        open(path, "wb").write(bytes(body))
    else:
        conn.execute("DELETE FROM raw_forecast_artifacts WHERE artifact_id=?", (artifact_id,))
    try:
        kwargs = dict(city="Paris", target_date="2026-10-02",
            metric=metric, decision_time=datetime.fromisoformat("2026-10-02T22:30:00+00:00"),
            posterior_source_cycle_time=cycle, posterior_computed_at=cut,
            posterior_provenance=provenance, held_redecision=True)
        serving = replacement_live_input_lag_reason(conn, **kwargs, use_memo=False)
        refresh = replacement_input_refresh_reason(conn, **kwargs)
        assert serving is not None and "current_value_serving_consumed" in serving, serving
        assert refresh == serving
    finally:
        conn.close()


@pytest.mark.parametrize("context_key", ("day0_conditioning", "day0_provisional_observation"))
@pytest.mark.parametrize("ifs_frozen", (False, True))
def test_shared_window_decoder_uses_context_and_checks_frozen_ifs(context_key, ifs_frozen):
    tau = "2026-10-02T21:32:17+00:00"
    provenance = {context_key: {"active": True, "metric": "high", "observation_time": tau}}
    if ifs_frozen:
        provenance["bayes_precision_fusion"] = {"current_value_serving": {"ecmwf_ifs": {
            "physical_response": {"frozen_product_identity": {"day0_remaining_from": tau}},
        }}}
    assert serving.day0_remaining_from_provenance(provenance, city="Paris", target_date="2026-10-02",
        metric="high", posterior_computed_at="2026-10-02T22:18:20+00:00") == (tau, None)


@pytest.mark.parametrize("invalid", (
    "missing_time", "naive", "malformed", "context_not_mapping", "wrong_metric",
    "wrong_day", "day_end", "after_cert_cut", "contexts_conflict", "frozen_none",
    "frozen_malformed", "frozen_conflict", "inactive_with_time",
))
def test_declared_invalid_window_never_silently_becomes_fulltarget(invalid):
    tau = "2026-10-02T21:32:17+00:00"
    context = {"active": True, "metric": "high", "observation_time": tau}
    provenance = {"day0_provisional_observation": context}
    cut = "2026-10-02T22:18:20+00:00"
    replacements = {
        "missing_time": None, "naive": "2026-10-02T21:32:17", "malformed": "unknown",
        "wrong_day": "2026-10-01T20:00:00+00:00", "day_end": "2026-10-02T22:00:00+00:00",
    }
    if invalid in replacements:
        context["observation_time"] = replacements[invalid]
    elif invalid == "context_not_mapping":
        provenance["day0_provisional_observation"] = None
    elif invalid == "wrong_metric":
        context["metric"] = "low"
    elif invalid == "after_cert_cut":
        cut = "2026-10-02T21:20:00+00:00"
    elif invalid == "contexts_conflict":
        provenance["day0_conditioning"] = {**context, "observation_time": "2026-10-02T21:30:00+00:00"}
    elif invalid == "inactive_with_time":
        context["active"] = False
    else:
        frozen = {"day0_remaining_from": None if invalid == "frozen_none"
            else "unknown" if invalid == "frozen_malformed" else "2026-10-02T21:30:00+00:00"}
        provenance["bayes_precision_fusion"] = {"current_value_serving": {"ecmwf_ifs": {
            "physical_response": {"frozen_product_identity": frozen},
        }}}
    assert serving.day0_remaining_from_provenance(provenance, city="Paris", target_date="2026-10-02",
        metric="high", posterior_computed_at=cut) == (
            None, "basis=current_value_serving_day0_window_unverifiable",
        )


def test_pre_day0_fulltarget_frozen_body_declares_no_window_and_is_no_conflict():
    # A full-target body captured before the local day began never had a remaining
    # window; absence of the key is "no claim", not a conflict with the Day0 tau.
    tau = "2026-10-02T21:32:17+00:00"
    provenance = {"day0_provisional_observation": {"active": True, "metric": "high", "observation_time": tau},
        "bayes_precision_fusion": {"current_value_serving": {"ecmwf_ifs": {
            "physical_response": {"frozen_product_identity": {"model": "ecmwf_ifs"}}}}}}
    assert serving.day0_remaining_from_provenance(provenance, city="Paris", target_date="2026-10-02",
        metric="high", posterior_computed_at="2026-10-02T22:18:20+00:00") == (tau, None)


def test_frozen_tau_without_posterior_tau_is_invalid():
    provenance = {"bayes_precision_fusion": {"current_value_serving": {"ecmwf_ifs": {
        "physical_response": {"frozen_product_identity": {"day0_remaining_from": "2026-10-02T21:30:00+00:00"}}}}}}
    assert serving.day0_remaining_from_provenance(provenance, city="Paris", target_date="2026-10-02",
        metric="high", posterior_computed_at="2026-10-02T22:18:20+00:00") == (
            None, "basis=current_value_serving_day0_window_unverifiable")


def test_no_day0_or_inactive_without_window_keeps_fulltarget():
    for provenance in ({}, {"day0_conditioning": {"active": False}}):
        assert serving.day0_remaining_from_provenance(provenance, city="Paris", target_date="2026-10-03",
            metric="low", posterior_computed_at=None) == (None, None)


@pytest.mark.parametrize("metric", ("high", "low"))
def test_actual_invalid_tau_blocks_lag_and_held_continuity_before_any_fallback(
    tmp_path, monkeypatch, metric
):
    from src.data import replacement_forecast_bundle_reader as reader
    from src.data.replacement_input_hwm import replacement_live_input_lag_reason
    conn, provenance, full, cycle, cut, _tau, _persist = _remaining_serving_certificate(tmp_path, monkeypatch, metric)
    # Fulltarget would otherwise look valid: prove an invalid declared window
    # cannot fall through and promote this different, physically legal product.
    provenance["bayes_precision_fusion"]["current_value_serving"]["icon_global"] = full.as_provenance()
    provenance["day0_provisional_observation"]["observation_time"] = "2026-10-02T21:32:17"
    def no_fallback(*_args, **_kwargs):
        pytest.fail("invalid source-window witness cannot fall back to a selector")
    monkeypatch.setattr(serving, "read_current_instrument_values", no_fallback)
    try:
        reason = replacement_live_input_lag_reason(conn, city="Paris", target_date="2026-10-02",
            metric=metric, decision_time=datetime.fromisoformat("2026-10-02T22:30:00+00:00"),
            posterior_source_cycle_time=cycle, posterior_computed_at=cut,
            posterior_provenance=provenance, held_redecision=True)
        assert reason == "basis=current_value_serving_day0_window_unverifiable"
        status, held_reason = reader._latest_complete_held_continuity(conn,
            row={"computed_at": cut, "source_cycle_time": cycle}, provenance=provenance,
            city="Paris", target_date="2026-10-02", metric=metric,
            decision_time=datetime.fromisoformat("2026-10-02T22:30:00+00:00"))
        assert status is reader._HeldContinuityStatus.BLOCKED
        assert held_reason == f"REPLACEMENT_PINNED_RAW_INPUT_HWM:{reason}"
    finally:
        conn.close()
