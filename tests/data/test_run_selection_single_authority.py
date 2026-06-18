# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: K4.0b(a) probe-resolved availability poll; task #30 run-selection
#   single authority (2026-06-11 incident: guessed now-lag clock requested unpublished
#   12Z/18Z runs, rung-2 meta refusal aborted the whole download->materialize cycle,
#   logs/zeus-forecast-live.err "provider declares run 06:00 but caller wants 18:00").
"""Antibodies: run selection has exactly ONE authority — provider probes.

Category killed: any production lane deriving "which run to fetch" from wall-clock
minus a release-lag constant. The guess factory (``_parse_cycle(None, ...)``) is dead
code-path; the production module may not reference it at all; the capture lane and the
current-target download lane both resolve through ``_probe_resolved_available_cycle``;
the anchor availability probe mirrors EVERY downloader transport rung (a rung the probe
cannot see is a rung the run-selection authority starves).
"""
from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_PATH = ROOT / "src" / "data" / "replacement_forecast_production.py"


def test_production_module_never_references_the_guess_factory() -> None:
    """``_parse_cycle`` (whose None-path was the now-lag guess) must not appear in the
    production module in any form — import, attribute, or call. New jobs that need a
    cycle go through ``_probe_resolved_available_cycle``."""
    source = PRODUCTION_PATH.read_text()
    assert "_parse_cycle" not in source, (
        "replacement_forecast_production.py references _parse_cycle — run selection has "
        "exactly one authority (_probe_resolved_available_cycle, probe-resolved); the "
        "now-minus-release-lag guess path is dead (2026-06-11)"
    )


def test_no_release_lag_arithmetic_feeds_a_cycle_assignment() -> None:
    """AST relationship pin: in the production module, no assignment whose target is a
    ``cycle``-named variable may contain ``release_lag`` anywhere in its value
    expression. release_lag survives ONLY as downloader metadata (source_available_at
    model), never as run selection."""
    tree = ast.parse(PRODUCTION_PATH.read_text())
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not any("cycle" in name for name in targets):
            continue
        value_src = ast.dump(node.value)
        if "release_lag" in value_src:
            offenders.append(f"line {node.lineno}: {targets}")
    assert not offenders, (
        f"cycle assignment(s) derived from release_lag arithmetic: {offenders}"
    )


def test_guess_factory_none_path_is_unconstructable() -> None:
    from scripts.download_replacement_forecast_current_targets import _parse_cycle

    with pytest.raises(ValueError, match="probe-resolved"):
        _parse_cycle(None, now=datetime.now(UTC), release_lag_hours=14.0)


def test_probe_resolved_authority_returns_newest_pair_complete_cycle(monkeypatch) -> None:
    """The single authority returns exactly what the probes confirm — a lag constant
    contradicting the probes loses."""
    import src.data.replacement_cycle_availability as availability
    import src.data.replacement_forecast_production as production

    published = {
        datetime(2026, 6, 11, 0, 0, tzinfo=UTC),
        datetime(2026, 6, 10, 18, 0, tzinfo=UTC),
        datetime(2026, 6, 10, 12, 0, tzinfo=UTC),
    }
    # 00Z is 3.2h old at probe time — far younger than any release-lag model would
    # admit; the probes say it is published, so it MUST be selected.
    monkeypatch.setattr(
        availability, "probe_anchor_available_any", lambda c, **kw: c in published
    )

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D102
            return datetime(2026, 6, 11, 3, 12, tzinfo=UTC)

    monkeypatch.setattr(production, "datetime", _FrozenDatetime)
    assert production._probe_resolved_available_cycle() == datetime(
        2026, 6, 11, 0, 0, tzinfo=UTC
    )


def test_download_job_skips_with_receipt_when_probes_unresolved(monkeypatch, tmp_path) -> None:
    """No anchor-complete cycle provable -> the job returns a skip receipt and the
    downloader is NEVER invoked with a guessed cycle."""
    import scripts.download_replacement_forecast_current_targets as dl
    import src.data.replacement_forecast_production as production

    monkeypatch.setattr(production, "_probe_resolved_available_cycle", lambda: None)

    def _must_not_run(**kwargs):  # pragma: no cover - the antibody trips before this
        raise AssertionError(
            f"downloader invoked without a probe-confirmed cycle: {kwargs.get('cycle')}"
        )

    monkeypatch.setattr(dl, "download_current_target_openmeteo_inputs", _must_not_run)
    report = production._download_replacement_forecast_current_targets_if_needed(
        {
            "download_current_targets_enabled": True,
            "forecast_db": tmp_path / "forecasts.db",
            "download_output_dir": tmp_path / "manifests",
        }
    )
    assert report is not None
    assert report["status"] == "CYCLE_PROBE_UNRESOLVED_SKIP"


def test_bayes_precision_fusion_capture_lane_skips_with_receipt_when_probes_unresolved(monkeypatch) -> None:
    import src.config as cfg
    import src.data.replacement_forecast_production as production

    monkeypatch.setitem(
        cfg.settings["edli"], "replacement_0_1_bayes_precision_fusion_capture_enabled", True
    )
    monkeypatch.setattr(production, "_probe_resolved_available_cycle", lambda: None)
    report = production._download_bayes_precision_fusion_extra_raw_inputs_if_needed(
        {"forecast_db": "zeus-forecasts.db"}
    )
    assert report is not None
    assert report["status"] == "BAYES_PRECISION_FUSION_EXTRA_CYCLE_PROBE_UNRESOLVED_SKIP"


def test_anchor_probe_mirrors_the_bucket_rung(monkeypatch) -> None:
    """Probe-set ⊇ downloader-ladder relationship: when single-runs 400s and the meta
    endpoint is down (the exact 2026-06-11 morning state), a bucket manifest declaring
    the run makes the anchor leg available — the run-selection authority can see the
    same rung the downloader would serve from."""
    import src.data.openmeteo_ecmwf_ifs9_anchor as anchor_mod
    import src.data.openmeteo_ecmwf_ifs9_bucket_transport as bucket_mod
    from src.data.replacement_cycle_availability import probe_anchor_available_any

    cycle = datetime(2026, 6, 11, 0, 0, tzinfo=UTC)

    def _urlopen_400(url, timeout=None):  # single-runs does not serve the run yet
        import urllib.error

        raise urllib.error.HTTPError(url, 400, "run not available", None, None)

    def _meta_down():  # the provider meta endpoint is unreachable (SSL EOF / 502)
        raise OSError("SSL: UNEXPECTED_EOF_WHILE_READING")

    monkeypatch.setattr(anchor_mod, "fetch_openmeteo_ifs9_model_meta", _meta_down)

    sentinel_manifest = object()
    monkeypatch.setattr(bucket_mod, "fetch_bucket_run_manifest", lambda: [sentinel_manifest])
    monkeypatch.setattr(
        bucket_mod,
        "select_declaring_manifest",
        lambda manifests, *, wanted_run: sentinel_manifest if wanted_run == cycle else None,
    )

    assert probe_anchor_available_any(cycle, urlopen=_urlopen_400) is True
    # And a bucket that declares a DIFFERENT run does not leak availability.
    assert (
        probe_anchor_available_any(cycle + timedelta(hours=6), urlopen=_urlopen_400)
        is False
    )


def test_manifest_availability_is_proof_of_possession_bounded() -> None:
    """source_available_at stamped on download must never exceed capture time: an
    early (probe-resolved) fetch proves availability at capture, so the nominal
    cycle+lag publication model only applies when capture happens AFTER it."""
    import inspect

    import scripts.download_replacement_forecast_current_targets as dl

    source = inspect.getsource(dl.download_current_target_raw_inputs)
    assert "min(" in source.split("manifests: list")[0] and "_source_available_at" in source, (
        "download_current_target_raw_inputs must bound source_available by "
        "min(captured_at, nominal) — a future-stamped manifest is invisible to seed "
        "discovery until the lag elapses, defeating probe-resolved early fetch"
    )


def test_availability_poll_also_feeds_the_extras_lane(monkeypatch, tmp_path) -> None:
    """Relationship pin: the probe-driven poll tick invokes the bayes_precision_fusion extras lane so
    fusion's same-cycle multimodel rows (the q_lcb substrate) arrive with the anchor —
    never hours later on a lag-modeled cron."""
    import src.data.replacement_cycle_availability as availability
    import src.data.replacement_forecast_production as production

    cycle = datetime(2026, 6, 11, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(
        availability, "probe_anchor_available_any", lambda c, **kw: c <= cycle
    )
    monkeypatch.setattr(
        production, "_per_leg_downloaded_cycle", lambda db, sid: cycle
    )
    extras_calls: list[dict] = []

    def _record_extras(cfg):
        extras_calls.append(dict(cfg))
        return {"status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"}

    monkeypatch.setattr(
        production, "_download_bayes_precision_fusion_extra_raw_inputs_if_needed", _record_extras
    )
    report = production._replacement_cycle_availability_poll_if_needed(
        {
            "download_current_targets_enabled": True,
            "forecast_db": tmp_path / "f.db",
            "download_output_dir": tmp_path,
        }
    )
    assert report is not None
    assert extras_calls, "poll tick must feed the extras lane (q_lcb substrate)"
    assert report["bayes_precision_fusion_extras_status"] == "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED"
