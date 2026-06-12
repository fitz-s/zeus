# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 (most-correct method, never starve on
#   data again) — K4.0b(f) anchor transport ladder. Relationship tests: the meta-stamped
#   path is unconstructable for a cycle the provider does not declare, atomicity is
#   enforced, the cross-check comparator distinguishes truthful from divergent series,
#   and only the run-not-served rejection class may degrade transports.
"""Category killed: an anchor artifact whose run identity is silently wrong — either
because the provider was serving a different run than asked (declared-run mismatch),
because the dataset changed mid-fetch (mixed-run payload), or because a transport
degradation swallowed a non-availability defect."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data.openmeteo_ecmwf_ifs9_anchor import (
    RUN_AUTHORITY_META_DECLARED,
    build_anchor_request,
    fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped,
)

UTC = timezone.utc


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def _meta(init: str, avail: str, mod: str):
    return {
        "run_initialisation_utc": _dt(init),
        "run_availability_utc": _dt(avail),
        "run_modification_utc": _dt(mod),
    }


def _request(run: str = "2026-06-11T00:00:00"):
    return build_anchor_request(
        latitude=33.63, longitude=-84.44, run=_dt(run), timezone_name="UTC"
    )


class TestMetaStampedFetch:
    def test_declared_run_mismatch_refuses(self, monkeypatch):
        metas = [_meta("2026-06-10T18:00:00", "2026-06-11T01:00:00", "2026-06-11T01:00:00")]

        with pytest.raises(ValueError, match="provider declares run"):
            fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped(
                _request("2026-06-11T00:00:00"),
                meta_fetch=lambda **k: metas[0],
            )

    def test_mid_fetch_modification_discards(self, monkeypatch):
        calls = {"n": 0}

        def meta_fetch(**k):
            calls["n"] += 1
            mod = "2026-06-11T01:00:00" if calls["n"] == 1 else "2026-06-11T02:00:00"
            return _meta("2026-06-11T00:00:00", "2026-06-11T01:00:00", mod)

        import src.data.openmeteo_client as omc

        monkeypatch.setattr(omc, "fetch", lambda *a, **k: {"hourly": {}})
        with pytest.raises(ValueError, match="mid-fetch"):
            fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped(
                _request(), meta_fetch=meta_fetch
            )

    def test_happy_path_returns_payload_and_auditable_provenance(self, monkeypatch):
        meta = _meta("2026-06-11T00:00:00", "2026-06-11T01:00:00", "2026-06-11T01:00:00")
        captured_params: dict = {}

        def fake_fetch(url, params, **kwargs):
            captured_params.update(params)
            return {"hourly": {"time": ["2026-06-11T00:00"], "temperature_2m": [20.0]}}

        import src.data.openmeteo_client as omc

        monkeypatch.setattr(omc, "fetch", fake_fetch)
        payload, prov = fetch_openmeteo_ecmwf_ifs9_anchor_payload_meta_stamped(
            _request(), meta_fetch=lambda **k: meta
        )
        assert payload["hourly"]["temperature_2m"] == [20.0]
        assert prov["run_authority"] == RUN_AUTHORITY_META_DECLARED
        assert prov["meta_run_initialisation_utc"] == "2026-06-11T00:00:00+00:00"
        assert prov["cross_check_status"] == "PENDING_SINGLE_RUNS_PUBLICATION"
        # The standard API must NOT receive a run param (it serves the declared run).
        assert "run" not in captured_params


class TestCrossCheckComparator:
    def test_identical_series_verified(self):
        from src.data.anchor_cross_check import compare_hourly_series

        a = {"hourly": {"time": ["t1", "t2"], "temperature_2m": [20.0, 21.5]}}
        b = {"hourly": {"time": ["t1", "t2"], "temperature_2m": [20.0, 21.5]}}
        r = compare_hourly_series(a, b)
        assert r["verdict"] == "VERIFIED" and r["compared"] == 2

    def test_divergent_series_mismatch(self):
        from src.data.anchor_cross_check import compare_hourly_series

        a = {"hourly": {"time": ["t1", "t2"], "temperature_2m": [20.0, 21.5]}}
        b = {"hourly": {"time": ["t1", "t2"], "temperature_2m": [20.0, 22.4]}}
        r = compare_hourly_series(a, b)
        assert r["verdict"] == "MISMATCH"
        assert r["max_abs_delta_c"] == pytest.approx(0.9)

    def test_no_overlap_is_not_a_pass(self):
        from src.data.anchor_cross_check import compare_hourly_series

        a = {"hourly": {"time": ["t1"], "temperature_2m": [20.0]}}
        b = {"hourly": {"time": ["t9"], "temperature_2m": [20.0]}}
        assert compare_hourly_series(a, b)["verdict"] == "NO_OVERLAP"


class TestTransportLadderRejectionClass:
    """Only HTTP 400 (run not served) may degrade single-runs → meta-stamped."""

    def test_downloader_reraises_non_400(self):
        import inspect

        import scripts.download_replacement_forecast_current_targets as dl

        src_text = inspect.getsource(dl)
        assert "status_code != 400" in src_text
        assert "raise" in src_text.split("status_code != 400", 1)[1][:80]


class TestAnchorProbeLadder:
    def test_meta_probe_accepts_exact_declared_complete_run(self, monkeypatch):
        from src.data import replacement_cycle_availability as rca

        monkeypatch.setattr(
            rca, "probe_openmeteo_single_run_available", lambda c, **k: False
        )
        import src.data.openmeteo_ecmwf_ifs9_anchor as anchor

        monkeypatch.setattr(
            anchor,
            "fetch_openmeteo_ifs9_model_meta",
            lambda **k: _meta(
                "2026-06-11T00:00:00", "2026-06-11T01:00:00", "2026-06-11T01:00:00"
            ),
        )
        assert rca.probe_anchor_available_any(_dt("2026-06-11T00:00:00")) is True
        assert rca.probe_anchor_available_any(_dt("2026-06-10T12:00:00")) is False
