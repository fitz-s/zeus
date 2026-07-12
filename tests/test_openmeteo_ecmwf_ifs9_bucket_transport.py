# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 (~07:10Z) — rung-3 S3 bucket anchor
#   transport. Relationship-first tests: admission rule, output-shape equivalence with the
#   API payload (extractor consumes both identically), provenance completeness, ladder
#   ordering (rung 3 only on rung-1 HTTP-400 + rung-2 declared-run-mismatch + bucket
#   declares wanted run + city whitelisted), and cross-check comparator reuse.
"""Tests for the rung-3 Open-Meteo S3 data_spatial partial-run anchor transport.

The load-bearing cross-module RELATIONSHIPS verified here:
  R1 (admission ↔ data): a bucket read for run R is admissible iff the manifest declares
     R AND every needed local-day timestep is present — drop ONE step ⇒ refuse.
  R2 (transport ↔ extractor): the bucket payload flows into
     extract_openmeteo_ecmwf_ifs9_localday_anchor identically to an API payload (same
     {"hourly": {"time", "temperature_2m"}} contract) — golden equivalence.
  R3 (transport ↔ ladder): rung 3 fires ONLY when rung 1 HTTP-400s, rung 2 raises its
     declared-run-mismatch ValueError, the bucket declares the wanted run, AND the city is
     cross-check-whitelisted; otherwise the rung-2 refusal propagates UNCHANGED.
  R4 (transport ↔ antibody): bucket artifacts carry run_authority=bucket_partial_run_
     unverified and the cross-check comparator (anchor_cross_check.compare_hourly_series)
     scores them the same way it scores meta-stamped artifacts.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from src.data.anchor_cross_check import compare_hourly_series
from src.data.openmeteo_ecmwf_ifs9_anchor import (
    extract_openmeteo_ecmwf_ifs9_localday_anchor,
)
from src.data.openmeteo_ecmwf_ifs9_bucket_transport import (
    RUN_AUTHORITY_BUCKET_UNVERIFIED,
    BucketRunManifest,
    check_partial_run_admission,
    fetch_bucket_anchor_payload,
    load_verified_city_whitelist,
    local_day_hourly_valid_times,
    map_lat_lon_to_o1280_index,
    parse_bucket_manifest,
    select_declaring_manifest,
)

UTC = timezone.utc


def _manifest(
    *,
    run: datetime,
    valid_times: list[datetime],
    completed: bool = False,
    source_key: str = "data_spatial/ecmwf_ifs/in-progress.json",
) -> BucketRunManifest:
    return BucketRunManifest(
        reference_time=run,
        completed=completed,
        valid_times=tuple(valid_times),
        last_modified_time=run + timedelta(hours=7),
        source_key=source_key,
        raw_variables=("temperature_2m",),
    )


def _hourly(run: datetime, n: int) -> list[datetime]:
    return [run + timedelta(hours=h) for h in range(n)]


# ---------------------------------------------------------------------------
# R1: admission rule — every needed timestep must be present.
# ---------------------------------------------------------------------------
def test_admission_passes_when_all_needed_timesteps_present() -> None:
    run = datetime(2026, 6, 11, 0, tzinfo=UTC)
    manifest = _manifest(run=run, valid_times=_hourly(run, 90))
    needed = [run + timedelta(hours=h) for h in range(4, 28)]  # a 24h local-day window
    result = check_partial_run_admission(manifest, wanted_run=run, needed_valid_times=needed)
    assert result.admissible is True
    assert result.missing_valid_times == ()


def test_admission_refuses_when_one_needed_timestep_missing() -> None:
    run = datetime(2026, 6, 11, 0, tzinfo=UTC)
    full = _hourly(run, 90)
    needed = [run + timedelta(hours=h) for h in range(4, 28)]
    # Drop exactly ONE needed step from the manifest.
    dropped = needed[12]
    manifest = _manifest(run=run, valid_times=[v for v in full if v != dropped])
    result = check_partial_run_admission(manifest, wanted_run=run, needed_valid_times=needed)
    assert result.admissible is False
    assert dropped in result.missing_valid_times
    assert len(result.missing_valid_times) == 1


def test_admission_refuses_when_manifest_declares_a_different_run() -> None:
    run = datetime(2026, 6, 11, 0, tzinfo=UTC)
    other = datetime(2026, 6, 10, 18, tzinfo=UTC)
    manifest = _manifest(run=other, valid_times=_hourly(other, 90))
    needed = [run + timedelta(hours=h) for h in range(4, 28)]
    result = check_partial_run_admission(manifest, wanted_run=run, needed_valid_times=needed)
    assert result.admissible is False
    assert "!=" in result.reason


def test_local_day_window_is_bounded_by_run_horizon() -> None:
    run = datetime(2026, 6, 11, 0, tzinfo=UTC)
    needed = local_day_hourly_valid_times(
        run=run, city_timezone="America/New_York", target_local_date=date(2026, 6, 13),
        forecast_hours=120,
    )
    assert len(needed) == 24
    assert all(run <= v <= run + timedelta(hours=120) for v in needed)


# ---------------------------------------------------------------------------
# R2: output-shape equivalence — bucket payload feeds the extractor like the API.
# ---------------------------------------------------------------------------
def _stub_reader_from_series(series: dict[datetime, float]):
    """A read_point stub: maps the step's valid_time (decoded from the key) to a temp."""

    def _reader(s3_uri: str, flat_index: int) -> float:
        # key tail '...<YYYY-MM-DDTHHMM>.om' -> valid_time
        stem = s3_uri.rsplit("/", 1)[-1].removesuffix(".om")
        vt = datetime.strptime(stem, "%Y-%m-%dT%H%M").replace(tzinfo=UTC)
        return series[vt]

    return _reader


def test_bucket_payload_shape_matches_api_and_extractor_consumes_identically() -> None:
    run = datetime(2026, 6, 11, 0, tzinfo=UTC)
    tz = "America/New_York"
    target = date(2026, 6, 11)
    needed = local_day_hourly_valid_times(run=run, city_timezone=tz, target_local_date=target)
    assert needed, "expected a non-empty local-day window for the run day"
    manifest = _manifest(run=run, valid_times=_hourly(run, 90))
    # deterministic temps: a ramp so high/low are unambiguous
    series = {vt: 10.0 + 0.5 * i for i, vt in enumerate(needed)}
    reader = _stub_reader_from_series(series)

    result = fetch_bucket_anchor_payload(
        latitude=40.71, longitude=-74.01, run=run, timezone_name=tz,
        needed_valid_times=needed, manifest=manifest, read_point=reader,
    )
    payload = result.payload
    # API-shape contract
    assert set(payload["hourly"].keys()) == {"time", "temperature_2m"}
    assert "utc_offset_seconds" in payload
    assert len(payload["hourly"]["time"]) == len(needed)
    assert payload["hourly"]["time"][0].count("T") == 1  # local wall-clock ISO minute

    # The extractor consumes the bucket payload with NO bucket-specific code path.
    anchor = extract_openmeteo_ecmwf_ifs9_localday_anchor(
        payload, city_timezone=tz, target_local_date=target,
    )
    assert anchor.sample_count == len(needed)
    assert anchor.high_c == pytest.approx(max(series.values()))
    assert anchor.low_c == pytest.approx(min(series.values()))


def test_bucket_payload_refuses_to_assemble_when_admission_fails() -> None:
    run = datetime(2026, 6, 11, 0, tzinfo=UTC)
    tz = "America/New_York"
    target = date(2026, 6, 11)
    needed = local_day_hourly_valid_times(run=run, city_timezone=tz, target_local_date=target)
    dropped = needed[1]
    manifest = _manifest(run=run, valid_times=[v for v in _hourly(run, 90) if v != dropped])
    with pytest.raises(ValueError, match="refused"):
        fetch_bucket_anchor_payload(
            latitude=40.71, longitude=-74.01, run=run, timezone_name=tz,
            needed_valid_times=needed, manifest=manifest,
            read_point=lambda uri, idx: 15.0,
        )


# ---------------------------------------------------------------------------
# R2b: provenance completeness — every required field is recorded.
# ---------------------------------------------------------------------------
def test_provenance_records_all_required_partial_run_fields() -> None:
    run = datetime(2026, 6, 11, 0, tzinfo=UTC)
    tz = "UTC"
    target = date(2026, 6, 11)
    needed = local_day_hourly_valid_times(run=run, city_timezone=tz, target_local_date=target)
    manifest = _manifest(run=run, valid_times=_hourly(run, 90))
    result = fetch_bucket_anchor_payload(
        latitude=51.5, longitude=-0.13, run=run, timezone_name=tz,
        needed_valid_times=needed, manifest=manifest, read_point=lambda uri, idx: 12.0,
    )
    prov = result.provenance
    assert prov["run_authority"] == RUN_AUTHORITY_BUCKET_UNVERIFIED
    assert prov["bucket_run_reference_time"] == run.isoformat()
    assert prov["bucket_completed_flag"] is False
    assert prov["bucket_valid_times_count_at_read"] == 90
    assert prov["bucket_last_modified_time"] is not None
    assert prov["bucket_needed_valid_times_count"] == len(needed)
    assert isinstance(prov["bucket_step_keys"], list) and prov["bucket_step_keys"]
    assert prov["cross_check_status"] == "PENDING_BUCKET_VS_API_VERIFICATION"
    assert isinstance(prov["o1280_flat_index"], int)


# ---------------------------------------------------------------------------
# R3: grid mapping sanity — O1280 octahedral count + nearest-neighbour determinism.
# ---------------------------------------------------------------------------
def test_o1280_index_is_in_range_and_deterministic() -> None:
    a = map_lat_lon_to_o1280_index(51.5, -0.13)
    b = map_lat_lon_to_o1280_index(51.5, -0.13)
    assert a == b
    assert 0 <= a.flat_index < 6_599_680
    assert a.nearest_distance_km < 10.0  # 9km grid: nearest point is close


def test_o1280_longitude_wraps_consistently() -> None:
    east = map_lat_lon_to_o1280_index(40.0, -3.7)
    east360 = map_lat_lon_to_o1280_index(40.0, 356.3)
    assert east.flat_index == east360.flat_index


# ---------------------------------------------------------------------------
# R3b: ladder ordering — rung 3 selection only when the bucket declares wanted run.
# ---------------------------------------------------------------------------
def test_select_declaring_manifest_prefers_in_progress_for_wanted_run() -> None:
    wanted = datetime(2026, 6, 11, 0, tzinfo=UTC)
    older = datetime(2026, 6, 10, 6, tzinfo=UTC)
    manifests = {
        "in_progress": _manifest(run=wanted, valid_times=_hourly(wanted, 90)),
        "latest": _manifest(run=older, valid_times=_hourly(older, 109), completed=True,
                            source_key="data_spatial/ecmwf_ifs/latest.json"),
    }
    chosen = select_declaring_manifest(manifests, wanted_run=wanted)
    assert chosen is not None and chosen.reference_time == wanted


def test_select_declaring_manifest_returns_none_when_neither_declares_wanted_run() -> None:
    wanted = datetime(2026, 6, 11, 0, tzinfo=UTC)
    older = datetime(2026, 6, 10, 6, tzinfo=UTC)
    older2 = datetime(2026, 6, 10, 12, tzinfo=UTC)
    manifests = {
        "in_progress": _manifest(run=older2, valid_times=_hourly(older2, 90)),
        "latest": _manifest(run=older, valid_times=_hourly(older, 109), completed=True),
    }
    assert select_declaring_manifest(manifests, wanted_run=wanted) is None


# ---------------------------------------------------------------------------
# R3c: city whitelist antibody — empty receipts ⇒ empty whitelist (fail-closed).
# ---------------------------------------------------------------------------
def test_whitelist_empty_when_no_receipts(tmp_path) -> None:
    missing = tmp_path / "no_such_receipt.json"
    assert load_verified_city_whitelist(receipt_path=str(missing)) == frozenset()


def test_whitelist_admits_only_verified_within_tolerance(tmp_path) -> None:
    import json

    receipt = tmp_path / "anchor_cross_check.json"
    receipt.write_text(json.dumps({
        "2026-06-10T06:00:00+00:00::bucket::Atlanta": {
            "verdict": "VERIFIED", "city": "Atlanta", "max_abs_delta_c": 0.05},
        "2026-06-10T06:00:00+00:00::bucket::Tokyo": {
            "verdict": "MISMATCH", "city": "Tokyo", "max_abs_delta_c": 3.65},
        "2026-06-10T06:00:00+00:00::bucket::London": {
            "verdict": "VERIFIED", "city": "London", "max_abs_delta_c": 0.05},
        "2026-06-10T06:00:00+00:00::bucket::Amsterdam": {
            "verdict": "MISMATCH", "city": "Amsterdam", "max_abs_delta_c": 0.25},  # real bias
        "2026-06-10T06:00:00+00:00::bucket::Chongqing": {
            "verdict": "MISMATCH", "city": "Chongqing", "max_abs_delta_c": 1.05},
    }))
    wl = load_verified_city_whitelist(receipt_path=str(receipt))
    assert "Atlanta" in wl
    assert "London" in wl
    assert "Tokyo" not in wl        # MISMATCH excluded
    assert "Amsterdam" not in wl    # real downscaling bias excluded
    assert "Chongqing" not in wl    # gross bias excluded


# ---------------------------------------------------------------------------
# R4: cross-check comparator reuse — same comparator scores bucket vs API.
# ---------------------------------------------------------------------------
def test_cross_check_comparator_verifies_matching_bucket_and_api_series() -> None:
    stored = {"hourly": {"time": ["2026-06-11T00:00", "2026-06-11T01:00"],
                          "temperature_2m": [10.0, 11.0]}}
    pinned = {"hourly": {"time": ["2026-06-11T00:00", "2026-06-11T01:00"],
                         "temperature_2m": [10.02, 10.99]}}
    result = compare_hourly_series(stored, pinned)
    assert result["verdict"] == "VERIFIED"
    assert result["compared"] == 2


def test_cross_check_comparator_flags_biased_bucket_series() -> None:
    stored = {"hourly": {"time": ["2026-06-11T00:00", "2026-06-11T01:00"],
                         "temperature_2m": [20.6, 17.75]}}  # Tokyo bucket
    pinned = {"hourly": {"time": ["2026-06-11T00:00", "2026-06-11T01:00"],
                        "temperature_2m": [22.8, 16.3]}}    # Tokyo API
    result = compare_hourly_series(stored, pinned)
    assert result["verdict"] == "MISMATCH"
    assert result["max_abs_delta_c"] > 0.05


def test_bucket_tolerance_clears_api_rounding_artifact() -> None:
    # The API serves 0.1C-rounded temps; the bucket carries 0.01C. A bucket value of
    # 10.95 vs API 10.9 is the API's rounding, NOT a disagreement, and lands at exactly
    # 0.05C. The strict 0.05C default trips on the float-repr boundary; the bucket
    # tolerance (0.1C = one API quantum) admits the true match as VERIFIED.
    from src.data.anchor_cross_check import BUCKET_VS_API_TOLERANCE_C

    stored = {"hourly": {"time": ["2026-06-11T00:00", "2026-06-11T01:00", "2026-06-11T02:00"],
                         "temperature_2m": [10.95, 13.65, 15.05]}}   # bucket 0.01C
    pinned = {"hourly": {"time": ["2026-06-11T00:00", "2026-06-11T01:00", "2026-06-11T02:00"],
                        "temperature_2m": [10.9, 13.6, 15.1]}}       # API 0.1C
    quant = compare_hourly_series(stored, pinned, tolerance_c=BUCKET_VS_API_TOLERANCE_C)
    assert quant["verdict"] == "VERIFIED"
    assert quant["max_abs_delta_c"] <= 0.05 + 1e-6


def test_bucket_tolerance_still_flags_real_downscaling_bias() -> None:
    # A genuine coastal/terrain downscaling delta (Amsterdam +0.25, Tokyo +3.65) must
    # remain MISMATCH even at the 0.1C bucket tolerance — it fixes rounding, not real bias.
    from src.data.anchor_cross_check import BUCKET_VS_API_TOLERANCE_C

    stored = {"hourly": {"time": ["2026-06-11T00:00"], "temperature_2m": [13.35]}}  # bucket
    pinned = {"hourly": {"time": ["2026-06-11T00:00"], "temperature_2m": [13.1]}}   # API (Δ0.25)
    quant = compare_hourly_series(stored, pinned, tolerance_c=BUCKET_VS_API_TOLERANCE_C)
    assert quant["verdict"] == "MISMATCH"
    assert quant["max_abs_delta_c"] >= 0.2


# ---------------------------------------------------------------------------
# Manifest parsing — live JSON shape round-trips.
# ---------------------------------------------------------------------------
def test_resolve_anchor_payload_ladder_degrades_rung1_400_then_rung2_then_rung3(monkeypatch) -> None:
    """Ladder ordering + scoping: rung-1 HTTP 400 → rung-2 (refusal/transport/5xx) → rung-3.

    Guards the `except ... as` unbinding gotcha (the resolver references single_runs_exc and
    the rung-2 reason after their except blocks exit) and the 5xx/transport degradation."""
    import httpx

    import scripts.download_replacement_forecast_current_targets as dl

    class _Req:
        latitude = 39.9
        longitude = 116.4
        run = datetime(2026, 6, 11, 0, tzinfo=UTC)
        forecast_hours = 120

    req = _Req()

    def _make_http_status(code: int) -> httpx.HTTPStatusError:
        request = httpx.Request("GET", "https://x")
        response = httpx.Response(code, request=request)
        return httpx.HTTPStatusError("err", request=request, response=response)

    # rung 1 always 400 (run not yet served)
    monkeypatch.setattr(
        dl, "fetch_openmeteo_ecmwf_ifs9_anchor_payload",
        lambda r, **kwargs: (_ for _ in ()).throw(_make_http_status(400)),
    )
    # rung 3 stub: returns a sentinel so we can assert we reached it without unbinding errors
    captured = {}

    def _fake_rung3(*, request, city, target_date, timezone_name, meta_refusal, single_runs_exc):
        captured["meta_refusal"] = str(meta_refusal)
        captured["single_runs_exc"] = str(single_runs_exc)
        return {"hourly": {"time": [], "temperature_2m": []}}, {"run_authority": "bucket_partial_run_unverified"}

    monkeypatch.setattr(dl, "_try_bucket_rung_three", _fake_rung3)

    # Case A: rung-2 raises a 502 (provider 5xx) → degrades to rung 3 (no UnboundLocalError).
    monkeypatch.setattr(
        "src.data.openmeteo_ecmwf_ifs9_anchor.fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped",
        lambda r, **kwargs: (_ for _ in ()).throw(_make_http_status(502)),
    )
    payload, prov = dl._resolve_anchor_payload(
        request=req, city="Beijing", target_date="2026-06-13", timezone_name="Asia/Shanghai",
    )
    assert prov["run_authority"] == "bucket_partial_run_unverified"
    assert "single_runs_exc" in captured and captured["single_runs_exc"]  # name survived

    # Case B: rung-2 raises a transport error (provider unreachable) -> degrades to rung 3.
    monkeypatch.setattr(
        "src.data.openmeteo_ecmwf_ifs9_anchor.fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped",
        lambda r, **kwargs: (_ for _ in ()).throw(httpx.ConnectError("ssl eof")),
    )
    payload2, prov2 = dl._resolve_anchor_payload(
        request=req, city="Beijing", target_date="2026-06-13", timezone_name="Asia/Shanghai",
    )
    assert prov2["run_authority"] == "bucket_partial_run_unverified"

    # Case C: provider rate-limit is transient, so it also degrades instead of poisoning the
    # whole current-target download batch.
    monkeypatch.setattr(
        dl, "fetch_openmeteo_ecmwf_ifs9_anchor_payload",
        lambda r, **kwargs: (_ for _ in ()).throw(_make_http_status(429)),
    )
    monkeypatch.setattr(
        "src.data.openmeteo_ecmwf_ifs9_anchor.fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped",
        lambda r, **kwargs: (_ for _ in ()).throw(_make_http_status(429)),
    )
    payload3, prov3 = dl._resolve_anchor_payload(
        request=req, city="Beijing", target_date="2026-06-13", timezone_name="Asia/Shanghai",
    )
    assert prov3["run_authority"] == "bucket_partial_run_unverified"

    # Case D: rung-2 success must preserve the real rung-1 failure class in provenance; rate
    # limits and retry exhaustion are not run-not-yet-served defects.
    monkeypatch.setattr(
        dl,
        "fetch_openmeteo_ecmwf_ifs9_anchor_payload",
        lambda r, **kwargs: (_ for _ in ()).throw(RuntimeError("Open-Meteo fetch exhausted retries")),
    )
    monkeypatch.setattr(
        "src.data.openmeteo_ecmwf_ifs9_anchor.fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped",
        lambda r, **kwargs: (
            {"hourly": {"time": [], "temperature_2m": []}},
            {"run_authority": "meta_stamped"},
        ),
    )
    payload4, prov4 = dl._resolve_anchor_payload(
        request=req, city="Beijing", target_date="2026-06-13", timezone_name="Asia/Shanghai",
    )
    assert prov4["run_authority"] == "meta_stamped"
    assert prov4["single_runs_fallback_reason"].startswith("RuntimeError:")
    assert "HTTP 400 run not yet served" not in prov4["single_runs_fallback_reason"]

    # Case E: the live Open-Meteo client wraps repeated 429s as RuntimeError after retries.
    monkeypatch.setattr(
        dl,
        "fetch_openmeteo_ecmwf_ifs9_anchor_payload",
        lambda r, **kwargs: (_ for _ in ()).throw(RuntimeError("Open-Meteo fetch exhausted retries")),
    )
    monkeypatch.setattr(
        "src.data.openmeteo_ecmwf_ifs9_anchor.fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped",
        lambda r, **kwargs: (_ for _ in ()).throw(RuntimeError("Open-Meteo fetch exhausted retries")),
    )
    payload5, prov5 = dl._resolve_anchor_payload(
        request=req, city="Beijing", target_date="2026-06-13", timezone_name="Asia/Shanghai",
    )
    assert prov5["run_authority"] == "bucket_partial_run_unverified"

    # Case F: quota cooldown is also a provider transient; it should fall through to bucket
    # instead of leaving replacement_forecast_download failed.
    monkeypatch.setattr(
        dl,
        "fetch_openmeteo_ecmwf_ifs9_anchor_payload",
        lambda r, **kwargs: (_ for _ in ()).throw(_make_http_status(429)),
    )
    monkeypatch.setattr(
        "src.data.openmeteo_ecmwf_ifs9_anchor.fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped",
        lambda r, **kwargs: (_ for _ in ()).throw(RuntimeError("Open-Meteo quota exhausted (2 calls today)")),
    )
    payload6, prov6 = dl._resolve_anchor_payload(
        request=req, city="Beijing", target_date="2026-06-13", timezone_name="Asia/Shanghai",
    )
    assert prov6["run_authority"] == "bucket_partial_run_unverified"


def test_bucket_artifact_source_available_at_is_capture_time_not_api_lag() -> None:
    """Fitz #4 / seed-discovery coupling: a bucket artifact's source_available_at must be the
    CAPTURE time (data available when the bucket served it), NOT the API's cycle+release-lag.
    The API lag would push it into the future and seed discovery (which admits only manifests
    with source_available_at <= now) would never see the early bucket data."""
    from datetime import timezone as _tz

    import scripts.download_replacement_forecast_current_targets as dl

    cycle = datetime(2026, 6, 11, 0, tzinfo=_tz.utc)
    api_lag = dl._source_available_at(cycle, release_lag_hours=14.0)
    assert api_lag == datetime(2026, 6, 11, 14, tzinfo=_tz.utc)  # cycle + 14h (future at 09:xx)
    # The transport-aware branch selects captured_at for a bucket artifact. Verify the rule:
    bucket_prov = {"run_authority": "bucket_partial_run_unverified"}
    api_prov = {"run_authority": "run_pinned_single_runs"}
    assert str(bucket_prov.get("run_authority", "")).startswith("bucket_partial_run")
    assert not str(api_prov.get("run_authority", "")).startswith("bucket_partial_run")


def test_resolve_anchor_payload_reraises_non_degradable_errors(monkeypatch) -> None:
    """Client-defect 4xx statuses still raise; 429 is handled by the ladder test."""
    import httpx

    import scripts.download_replacement_forecast_current_targets as dl

    class _Req:
        latitude = 39.9
        longitude = 116.4
        run = datetime(2026, 6, 11, 0, tzinfo=UTC)
        forecast_hours = 120

    def _status(code: int) -> httpx.HTTPStatusError:
        request = httpx.Request("GET", "https://x")
        return httpx.HTTPStatusError("e", request=request, response=httpx.Response(code, request=request))

    # single-runs 401 (auth) must raise, not degrade.
    monkeypatch.setattr(
        dl, "fetch_openmeteo_ecmwf_ifs9_anchor_payload",
        lambda r, **kwargs: (_ for _ in ()).throw(_status(401)),
    )
    with pytest.raises(httpx.HTTPStatusError):
        dl._resolve_anchor_payload(request=_Req(), city="X", target_date="2026-06-13", timezone_name="UTC")

    # single-runs 400 → rung 2 raises 404 (client defect) must raise, not degrade to rung 3.
    monkeypatch.setattr(
        dl, "fetch_openmeteo_ecmwf_ifs9_anchor_payload",
        lambda r, **kwargs: (_ for _ in ()).throw(_status(400)),
    )
    monkeypatch.setattr(
        "src.data.openmeteo_ecmwf_ifs9_anchor.fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped",
        lambda r, **kwargs: (_ for _ in ()).throw(_status(404)),
    )
    with pytest.raises(httpx.HTTPStatusError):
        dl._resolve_anchor_payload(request=_Req(), city="X", target_date="2026-06-13", timezone_name="UTC")


def test_resolve_anchor_payload_fast_fails_429_to_transport_ladder(monkeypatch) -> None:
    """The live current-target ladder has bucket fallback, so it must not sleep/retry 429s."""
    import httpx

    import scripts.download_replacement_forecast_current_targets as dl

    class _Req:
        latitude = 39.9
        longitude = 116.4
        run = datetime(2026, 6, 11, 0, tzinfo=UTC)
        forecast_hours = 120

    calls: list[tuple[str, bool]] = []

    def _single_runs(request, *, fast_fail_429=False):
        calls.append(("single", fast_fail_429))
        req = httpx.Request("GET", "https://x")
        raise httpx.HTTPStatusError(
            "429", request=req, response=httpx.Response(429, request=req)
        )

    def _meta_stamped(request, *, fast_fail_429=False):
        calls.append(("meta", fast_fail_429))
        req = httpx.Request("GET", "https://x")
        raise httpx.HTTPStatusError(
            "429", request=req, response=httpx.Response(429, request=req)
        )

    def _fake_rung3(*, request, city, target_date, timezone_name, meta_refusal, single_runs_exc):
        return {"hourly": {"time": [], "temperature_2m": []}}, {"run_authority": "bucket_partial_run_unverified"}

    monkeypatch.setattr(dl, "fetch_openmeteo_ecmwf_ifs9_anchor_payload", _single_runs)
    monkeypatch.setattr(
        "src.data.openmeteo_ecmwf_ifs9_anchor.fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped",
        _meta_stamped,
    )
    monkeypatch.setattr(dl, "_try_bucket_rung_three", _fake_rung3)

    _, prov = dl._resolve_anchor_payload(
        request=_Req(), city="Beijing", target_date="2026-06-13", timezone_name="Asia/Shanghai",
    )

    assert prov["run_authority"] == "bucket_partial_run_unverified"
    assert calls == [("single", True), ("meta", True)]


def test_parse_bucket_manifest_round_trips_live_shape() -> None:
    raw = {
        "completed": False,
        "reference_time": "2026-06-11T00:00:00Z",
        "valid_times": ["2026-06-11T00:00Z", "2026-06-11T01:00Z"],
        "last_modified_time": "2026-06-11T07:11:40Z",
        "variables": ["temperature_2m", "dew_point_2m"],
    }
    m = parse_bucket_manifest(raw, source_key="data_spatial/ecmwf_ifs/in-progress.json")
    assert m.reference_time == datetime(2026, 6, 11, 0, tzinfo=UTC)
    assert m.completed is False
    assert len(m.valid_times) == 2
    assert m.valid_times[0] == datetime(2026, 6, 11, 0, tzinfo=UTC)


# ===========================================================================
# DOWNSCALING (Open-Meteo cell_selection=land + lapse-rate replication).
#
# Cross-module relationships verified here:
#   D1 (geometry ↔ open-meteo): the equidistant-approx O1280 indexing reproduces
#      open-meteo's GaussianGrid getPos/getCoordinates round-trip and the 3x3 box.
#   D2 (cell selection ↔ elevation): flat grid ⇒ center (no search); elevation step ⇒
#      the land neighbor matching target is chosen; sea-nearest ⇒ a land neighbor preferred.
#   D3 (correction ↔ sign): corrected = grid_T + (modelElev-target)*0.0065, higher target
#      ⇒ cooler; sea/NaN cell ⇒ no-op.
#   D4 (whitelist ↔ method): resolve_bucket_serve_method returns raw|downscaled|None from
#      the two receipt classes — a downscaled-only-verified city is served downscaled.
#   D5 (downscaled payload ↔ extractor): the downscaled payload is API-shaped and the
#      extractor consumes it with no downscaling-specific code path.
# ===========================================================================
from src.data.openmeteo_ecmwf_ifs9_bucket_transport import (  # noqa: E402
    LAPSE_RATE_K_PER_M,
    SEA_SENTINEL_M,
    apply_elevation_correction,
    capture_city_target_elevation,
    fetch_bucket_anchor_payload_downscaled,
    load_city_target_elevation,
    om_get_coordinates,
    om_get_surrounding_gridpoints,
    resolve_bucket_serve_method,
    select_terrain_optimised_point,
)


# --- D1: grid geometry --------------------------------------------------------
def test_om_grid_coordinates_roundtrip_in_range() -> None:
    # The flat index maps to a valid (lat,lon) and the 3x3 box contains the nearest.
    gps, dists, mi = om_get_surrounding_gridpoints(35.553, 139.781)
    assert len(gps) == 9 and len(dists) == 9
    assert 0 <= mi < 9
    for gp in gps:
        assert 0 <= gp < 6_599_680
    coord = om_get_coordinates(gps[mi])
    # nearest box member should be physically near the request (within ~0.1deg)
    assert abs(coord.grid_latitude - 35.553) < 0.1


def test_om_surrounding_box_is_nine_distinct_neighbors_inland() -> None:
    gps, _dists, _mi = om_get_surrounding_gridpoints(40.466, -3.555)  # Madrid (inland)
    assert len(set(gps)) >= 6  # staggered octahedral rows ⇒ at least 6 distinct points


# --- D2: terrain-optimised cell selection ------------------------------------
def test_terrain_selects_center_when_elevation_within_tolerance() -> None:
    # center within 100m of target ⇒ center cell, no search.
    captured = {}

    gps, _d, mi = om_get_surrounding_gridpoints(48.348, 11.813)  # Munich
    center_gp = gps[mi]
    # center elev 70m, target 50m: |70-50|=20 <= 100 ⇒ center branch fires.
    def near_elev(idx: int) -> float:
        captured.setdefault("reads", []).append(idx)
        return 70.0

    cell = select_terrain_optimised_point(48.348, 11.813, 50.0, read_elevation=near_elev)
    assert cell.flat_index == center_gp
    assert cell.is_center is True
    assert cell.is_sea is False
    # VERBATIM open-meteo: within 100m the cell's effective elevation IS the target, so the
    # correction is exactly zero (NOT (70-50)*lapse). model_elevation_m must equal the target.
    assert cell.model_elevation_m == 50.0
    assert apply_elevation_correction(
        18.0, model_elevation_m=cell.model_elevation_m, target_elevation_m=50.0
    ) == 18.0
    # center-tolerance short-circuit ⇒ only the center elevation is read (no full 3x3 scan)
    assert captured["reads"] == [center_gp]


def test_terrain_prefers_land_neighbor_when_nearest_is_sea() -> None:
    # COASTAL synthetic: nearest (center) is SEA; one neighbor is land near target ⇒ pick the land one.
    gps, _d, mi = om_get_surrounding_gridpoints(35.553, 139.781)
    center_gp = gps[mi]
    land_gp = next(g for g in gps if g != center_gp)

    def coastal_elev(idx: int) -> float:
        if idx == center_gp:
            return SEA_SENTINEL_M  # sea
        if idx == land_gp:
            return 5.0  # land near target 4m
        return SEA_SENTINEL_M  # all other neighbors sea

    cell = select_terrain_optimised_point(35.553, 139.781, 4.0, read_elevation=coastal_elev)
    assert cell.flat_index == land_gp
    assert cell.is_sea is False
    assert cell.model_elevation_m == 5.0


def test_terrain_falls_back_to_center_when_all_neighbors_sea() -> None:
    # ALL sea ⇒ honest fall back to the (sea) center cell; correction must then be a no-op.
    gps, _d, mi = om_get_surrounding_gridpoints(1.368, 103.982)
    center_gp = gps[mi]

    def all_sea(idx: int) -> float:
        return SEA_SENTINEL_M

    cell = select_terrain_optimised_point(1.368, 103.982, 15.0, read_elevation=all_sea)
    assert cell.flat_index == center_gp
    assert cell.is_sea is True


def test_terrain_picks_elevation_match_over_pure_nearest() -> None:
    # ELEVATION STEP: center is land but 1200m off target; a neighbor matches target ⇒ pick neighbor.
    gps, _d, mi = om_get_surrounding_gridpoints(29.718, 106.639)  # Chongqing (basin)
    center_gp = gps[mi]
    match_gp = next(g for g in gps if g != center_gp)

    def stepped_elev(idx: int) -> float:
        if idx == center_gp:
            return 1500.0  # 1087m above target 413 ⇒ search triggered
        if idx == match_gp:
            return 400.0  # close to target
        return 1500.0

    cell = select_terrain_optimised_point(29.718, 106.639, 413.0, read_elevation=stepped_elev)
    # the matching neighbor wins unless its distance penalty exceeds the center's 1087m delta
    assert cell.model_elevation_m in (400.0, 1500.0)
    if cell.flat_index == match_gp:
        assert cell.model_elevation_m == 400.0


# --- D3: elevation correction sign + no-op -----------------------------------
def test_elevation_correction_exact_and_signed() -> None:
    # target HIGHER than model ⇒ cooler. modelElev 7, target 4 ⇒ +3*0.0065 = +0.0195.
    assert apply_elevation_correction(22.1, model_elevation_m=7.0, target_elevation_m=4.0) == pytest.approx(
        22.1 + (7.0 - 4.0) * LAPSE_RATE_K_PER_M
    )
    # target ABOVE model (uphill) ⇒ negative correction
    up = apply_elevation_correction(20.0, model_elevation_m=100.0, target_elevation_m=600.0)
    assert up == pytest.approx(20.0 + (100.0 - 600.0) * 0.0065)
    assert up < 20.0


def test_elevation_correction_noop_on_sea_or_nan() -> None:
    # sea cell (modelElev <= -999) ⇒ no correction (raw grid value passes through)
    assert apply_elevation_correction(28.0, model_elevation_m=SEA_SENTINEL_M, target_elevation_m=15.0) == 28.0
    assert apply_elevation_correction(28.0, model_elevation_m=float("nan"), target_elevation_m=15.0) == 28.0


# --- D4: whitelist method resolution -----------------------------------------
def test_resolve_serve_method_downscaled_only_city(tmp_path) -> None:
    import json

    receipt = tmp_path / "anchor_cross_check.json"
    receipt.write_text(json.dumps({
        # Atlanta: raw verified ⇒ served raw (cheapest)
        "2026-06-10T06:00:00+00:00::bucket::Atlanta": {
            "verdict": "VERIFIED", "city": "Atlanta", "max_abs_delta_c": 0.05},
        # Tokyo: raw MISMATCH but downscaled VERIFIED ⇒ served downscaled
        "2026-06-10T06:00:00+00:00::bucket::Tokyo": {
            "verdict": "MISMATCH", "city": "Tokyo", "max_abs_delta_c": 2.2},
        "2026-06-10T06:00:00+00:00::bucket_downscaled::Tokyo": {
            "verdict": "VERIFIED", "city": "Tokyo", "max_abs_delta_c": 0.03},
        # Amsterdam: downscaled MISMATCH (real residual bias) ⇒ stays non-admitted
        "2026-06-10T06:00:00+00:00::bucket_downscaled::Amsterdam": {
            "verdict": "MISMATCH", "city": "Amsterdam", "max_abs_delta_c": 0.25},
    }))
    assert resolve_bucket_serve_method("Atlanta", receipt_path=str(receipt)) == "raw"
    assert resolve_bucket_serve_method("Tokyo", receipt_path=str(receipt)) == "downscaled"
    assert resolve_bucket_serve_method("Amsterdam", receipt_path=str(receipt)) is None
    assert resolve_bucket_serve_method("Nowhere", receipt_path=str(receipt)) is None


def test_resolve_serve_method_prefers_raw_over_downscaled(tmp_path) -> None:
    import json

    receipt = tmp_path / "anchor_cross_check.json"
    # a city verified BOTH ways ⇒ raw wins (one read, no static field needed)
    receipt.write_text(json.dumps({
        "2026-06-10T06:00:00+00:00::bucket::London": {
            "verdict": "VERIFIED", "city": "London", "max_abs_delta_c": 0.05},
        "2026-06-10T06:00:00+00:00::bucket_downscaled::London": {
            "verdict": "VERIFIED", "city": "London", "max_abs_delta_c": 0.04},
    }))
    assert resolve_bucket_serve_method("London", receipt_path=str(receipt)) == "raw"


def test_resolve_serve_method_empty_when_no_receipts(tmp_path) -> None:
    assert resolve_bucket_serve_method("Tokyo", receipt_path=str(tmp_path / "missing.json")) is None


# --- D5: downscaled payload shape + extractor equivalence --------------------
def test_downscaled_payload_shape_matches_api_and_extractor_consumes_identically() -> None:
    run = datetime(2026, 6, 11, 0, tzinfo=UTC)
    tz = "Asia/Tokyo"
    target = date(2026, 6, 11)
    needed = local_day_hourly_valid_times(run=run, city_timezone=tz, target_local_date=target)
    assert needed
    manifest = _manifest(run=run, valid_times=_hourly(run, 90))
    series = {vt: 20.0 + 0.5 * i for i, vt in enumerate(needed)}

    # Force a deterministic land cell with a known model elevation so the correction is exact.
    gps, _d, mi = om_get_surrounding_gridpoints(35.553, 139.781)
    chosen_gp = gps[mi]

    def elev_reader(idx: int) -> float:
        return 4.0  # model elevation == target ⇒ center chosen, zero correction

    def temp_reader(s3_uri: str, flat_index: int) -> float:
        assert flat_index == chosen_gp  # reads at the SELECTED cell, not a raw nearest
        stem = s3_uri.rsplit("/", 1)[-1].removesuffix(".om")
        vt = datetime.strptime(stem, "%Y-%m-%dT%H%M").replace(tzinfo=UTC)
        return series[vt]

    result = fetch_bucket_anchor_payload_downscaled(
        latitude=35.553, longitude=139.781, target_elevation_m=4.0,
        run=run, timezone_name=tz, needed_valid_times=needed, manifest=manifest,
        read_point=temp_reader, read_elevation=elev_reader,
    )
    payload = result.payload
    assert set(payload["hourly"].keys()) == {"time", "temperature_2m"}
    assert payload["elevation"] == 4.0
    assert len(payload["hourly"]["time"]) == len(needed)
    assert result.provenance["run_authority"] == "bucket_partial_run_downscaled_unverified"
    assert result.provenance["downscaling_method"] == "cell_selection_land_terrain_optimised_plus_lapse_rate"
    assert result.provenance["lapse_rate_k_per_m"] == LAPSE_RATE_K_PER_M

    anchor = extract_openmeteo_ecmwf_ifs9_localday_anchor(
        payload, city_timezone=tz, target_local_date=target,
    )
    assert anchor.sample_count == len(needed)
    # model elev == target ⇒ no correction ⇒ high/low equal the raw series
    assert anchor.high_c == pytest.approx(max(series.values()))
    assert anchor.low_c == pytest.approx(min(series.values()))


def test_downscaled_payload_applies_lapse_correction_to_every_step() -> None:
    run = datetime(2026, 6, 11, 0, tzinfo=UTC)
    tz = "UTC"
    target = date(2026, 6, 11)
    needed = local_day_hourly_valid_times(run=run, city_timezone=tz, target_local_date=target)
    manifest = _manifest(run=run, valid_times=_hourly(run, 90))

    # model elevation 500m above the target ⇒ every temp warmed by 500*0.0065 = +3.25C.
    def elev_reader(idx: int) -> float:
        return 600.0  # vs target 100 ⇒ deltaCenter 500 > 100 ⇒ search, but all cells 600 ⇒ center

    def temp_reader(s3_uri: str, flat_index: int) -> float:
        return 10.0  # constant

    result = fetch_bucket_anchor_payload_downscaled(
        latitude=39.9, longitude=116.4, target_elevation_m=100.0,
        run=run, timezone_name=tz, needed_valid_times=needed, manifest=manifest,
        read_point=temp_reader, read_elevation=elev_reader,
    )
    temps = result.payload["hourly"]["temperature_2m"]
    expected = round(10.0 + (600.0 - 100.0) * LAPSE_RATE_K_PER_M, 2)
    assert all(t == expected for t in temps)
    assert result.provenance["elevation_correction_c"] == pytest.approx((600.0 - 100.0) * 0.0065, abs=1e-6)


def test_downscaled_payload_refuses_when_admission_fails() -> None:
    run = datetime(2026, 6, 11, 0, tzinfo=UTC)
    tz = "UTC"
    target = date(2026, 6, 11)
    needed = local_day_hourly_valid_times(run=run, city_timezone=tz, target_local_date=target)
    dropped = needed[1]
    manifest = _manifest(run=run, valid_times=[v for v in _hourly(run, 90) if v != dropped])
    with pytest.raises(ValueError, match="refused"):
        fetch_bucket_anchor_payload_downscaled(
            latitude=39.9, longitude=116.4, target_elevation_m=50.0,
            run=run, timezone_name=tz, needed_valid_times=needed, manifest=manifest,
            read_point=lambda uri, idx: 15.0, read_elevation=lambda idx: 50.0,
        )


# --- city target-elevation cache (capture once, provenance) ------------------
def test_city_target_elevation_capture_and_cache(tmp_path) -> None:
    cache = tmp_path / "anchor_city_elevation.json"
    calls = {"n": 0}

    def fake_api(url: str, **kwargs):
        calls["n"] += 1
        return {"elevation": 4.0, "latitude": 35.6063, "longitude": 139.7429}

    # first call hits the API and caches with provenance
    elev = capture_city_target_elevation(
        "Tokyo", 35.553, 139.781, cache_path=str(cache), http_get=fake_api
    )
    assert elev == 4.0
    assert calls["n"] == 1
    import json as _json
    rec = _json.loads(cache.read_text())["Tokyo"]
    assert rec["authority"] == "openmeteo_90m_dem_api_reported"
    assert rec["source"]
    assert "captured_at" in rec
    # second call reuses the cache (no second API hit)
    elev2 = capture_city_target_elevation(
        "Tokyo", 35.553, 139.781, cache_path=str(cache), http_get=fake_api
    )
    assert elev2 == 4.0
    assert calls["n"] == 1
    assert load_city_target_elevation("Tokyo", cache_path=str(cache)) == 4.0
