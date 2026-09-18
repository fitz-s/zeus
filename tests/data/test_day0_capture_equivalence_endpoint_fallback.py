"""Endpoint fallback must not read as a different observation.

``_select_day0_run_endpoint`` (35ff9a3dc) deliberately falls back from the
run-pinned single-runs endpoint to the standard meta-stamped one whenever the
freshest run fails its clock precheck or its response starts after the causal
observation boundary. That fallback proves the SAME provider run through a
different URL, so the capture-equivalence comparison must tolerate the
transport fields it changes while still rejecting a genuinely different run.

Measured on live captures 2026-09-18: 28 of that day's endpoint-mode flips
carried an identical ``provider_run_id`` AND byte-identical values on every
shared target-day timestamp, yet the semantic hash rejected the entry as
``DAY0_CAUSAL_CAPTURE_EQUIVALENCE_SEMANTIC_META_MISMATCH`` ->
``GLOBAL_ACTUATION_PROBABILITY_USE_DIVERGED`` -- the largest single
winner-preflight rejection class (39 of 106).
"""
import json

import src.data.day0_hourly_vectors as hourly


def _semantic_meta(meta: dict, *, model: str) -> dict:
    """Mirror the projection `_day0_canonical_vector_row_snapshot` applies."""
    return {
        str(key): hourly._day0_normalize_vector_request_semantics(
            str(key), value, model=model
        )
        for key, value in meta.items()
        if str(key) not in hourly._DAY0_CAPTURE_EQUIVALENCE_ONLY_META
    }


def _meta(*, endpoint_mode: str, run: str, model: str = "icon_global") -> dict:
    single = endpoint_mode == "single_runs"
    return {
        "provider": "openmeteo",
        "model_api_id": model,
        "provider_run_id": f"openmeteo:{model}:{run}",
        "provider_source_cycle_time_utc": run,
        "provider_source_available_at_utc": "2026-09-18T05:40:00+00:00",
        "provider_source_modified_at_utc": "2026-09-18T05:41:00+00:00",
        "endpoint": (
            "https://single-runs-api.open-meteo.com/v1/forecast"
            if single
            else "https://api.open-meteo.com/v1/forecast"
        ),
        "endpoint_mode": endpoint_mode,
        "source_run_authority": (
            "run_pinned_single_runs" if single else "provider_meta_declared"
        ),
        # Capture-only identity: already exempt before this change.
        "fetch_started_at": "2026-09-18T06:04:13.846742+00:00",
        "fetch_finished_at": "2026-09-18T06:04:13.856036+00:00",
        "request_hash": f"sha256:{endpoint_mode}",
        "source_run_id": f"day0_hourly:sha256:{endpoint_mode}",
        "request_params_json": json.dumps(
            {
                "city": "Taipei",
                "model": model,
                "runs": {model: run, "ecmwf_ifs": "2026-09-18T00:00:00+00:00"},
                "endpoint_modes": {model: endpoint_mode, "ecmwf_ifs": "single_runs"},
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    }


RUN = "2026-09-18T06:00:00+00:00"
OTHER_RUN = "2026-09-18T00:00:00+00:00"


def test_endpoint_fallback_on_the_same_run_is_semantically_equivalent():
    single = _semantic_meta(_meta(endpoint_mode="single_runs", run=RUN), model="icon_global")
    standard = _semantic_meta(
        _meta(endpoint_mode="standard_meta_stamped", run=RUN), model="icon_global"
    )
    assert hourly._day0_json_hash(single) == hourly._day0_json_hash(standard)


def test_a_different_run_still_diverges_through_either_endpoint():
    single = _semantic_meta(_meta(endpoint_mode="single_runs", run=RUN), model="icon_global")
    other_single = _semantic_meta(
        _meta(endpoint_mode="single_runs", run=OTHER_RUN), model="icon_global"
    )
    other_standard = _semantic_meta(
        _meta(endpoint_mode="standard_meta_stamped", run=OTHER_RUN), model="icon_global"
    )
    assert hourly._day0_json_hash(single) != hourly._day0_json_hash(other_single)
    # The exemption must not let a run advance hide behind a transport flip.
    assert hourly._day0_json_hash(single) != hourly._day0_json_hash(other_standard)


def test_a_differing_provider_availability_clock_still_diverges():
    single = _meta(endpoint_mode="single_runs", run=RUN)
    standard = _meta(endpoint_mode="standard_meta_stamped", run=RUN)
    # Live data carries 4 same-run flips whose two endpoints reported different
    # availability clocks; that is real provenance, not transport.
    standard["provider_source_available_at_utc"] = "2026-09-18T05:55:00+00:00"
    assert hourly._day0_json_hash(
        _semantic_meta(single, model="icon_global")
    ) != hourly._day0_json_hash(_semantic_meta(standard, model="icon_global"))


def test_transport_fields_are_the_only_widening():
    # Guards against a future blanket widening: run identity and every provider
    # clock must stay OUTSIDE the capture-only allow-list.
    assert hourly._DAY0_CAPTURE_EQUIVALENCE_ONLY_META == frozenset(
        {
            "fetch_started_at",
            "fetch_finished_at",
            "request_hash",
            "source_run_id",
            "endpoint",
            "endpoint_mode",
            "source_run_authority",
        }
    )
    for field in (
        "provider_run_id",
        "provider_source_cycle_time_utc",
        "provider_source_available_at_utc",
        "provider_source_modified_at_utc",
        "model_api_id",
        "provider",
    ):
        assert field not in hourly._DAY0_CAPTURE_EQUIVALENCE_ONLY_META


def test_sibling_endpoint_mode_flip_does_not_change_this_rows_identity():
    own = _meta(endpoint_mode="single_runs", run=RUN)
    sibling_flipped = _meta(endpoint_mode="single_runs", run=RUN)
    params = json.loads(sibling_flipped["request_params_json"])
    params["endpoint_modes"]["ecmwf_ifs"] = "standard_meta_stamped"
    sibling_flipped["request_params_json"] = json.dumps(
        params, sort_keys=True, separators=(",", ":")
    )
    assert hourly._day0_json_hash(
        _semantic_meta(own, model="icon_global")
    ) == hourly._day0_json_hash(_semantic_meta(sibling_flipped, model="icon_global"))
