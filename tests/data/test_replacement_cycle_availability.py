# Created: 2026-06-11
# Last reused or audited: 2026-08-21
# Authority basis: operator directive 2026-06-11 ~03:40Z (automatic download, ahead of
#   need, NO guessed numbers) and 2026-06-18 live/experiment separation. Relationship
#   tests for probe-resolved anchor cycle selection and fetch decision.
"""Make guessed release-lag cycle selection and retired non-live legs unconstructable."""
from __future__ import annotations

from datetime import datetime, timezone

from src.data.replacement_cycle_availability import (
    AnchorAvailabilityProbe,
    candidate_cycles,
    floor_to_cycle,
    newest_complete_cycle,
    probe_openmeteo_single_run_available,
    resolve_anchor_cycle_availability,
)
from src.data.openmeteo_model_updates import (
    OpenMeteoModelUpdate,
    write_model_updates_jsonl,
)

UTC = timezone.utc


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


class TestCycleGrid:
    def test_floor_to_cycle_grid(self):
        assert floor_to_cycle(_dt("2026-06-11T03:20:00")) == _dt("2026-06-11T00:00:00")
        assert floor_to_cycle(_dt("2026-06-11T06:00:00")) == _dt("2026-06-11T06:00:00")
        assert floor_to_cycle(_dt("2026-06-11T23:59:00")) == _dt("2026-06-11T18:00:00")

    def test_candidates_newest_first_6h_grid(self):
        cands = candidate_cycles(_dt("2026-06-11T03:20:00"))
        assert cands[0] == _dt("2026-06-11T00:00:00")
        assert all(
            (cands[i] - cands[i + 1]).total_seconds() == 6 * 3600
            for i in range(len(cands) - 1)
        )


class TestProbeResolvedSelection:
    """The no-guess antibody: probes are the ONLY availability authority."""

    def test_probes_decide_not_any_lag_constant(self):
        now = _dt("2026-06-11T03:20:00")
        avail = resolve_anchor_cycle_availability(now, probe_anchor=lambda c: True)
        assert newest_complete_cycle(avail) == _dt("2026-06-11T00:00:00")

    def test_unpublished_newest_falls_back_to_probed_older(self):
        now = _dt("2026-06-11T03:20:00")
        published_from = _dt("2026-06-10T18:00:00")
        avail = resolve_anchor_cycle_availability(
            now,
            probe_anchor=lambda c: c <= published_from,
        )
        assert newest_complete_cycle(avail) == published_from

    def test_anchor_unpublished_newest_is_not_complete(self):
        now = _dt("2026-06-10T22:30:00")
        cycle_12z = _dt("2026-06-10T12:00:00")
        cycle_06z = _dt("2026-06-10T06:00:00")
        avail = resolve_anchor_cycle_availability(
            now,
            probe_anchor=lambda c: c <= cycle_06z,
        )
        by_cycle = {a.cycle: a for a in avail}
        assert by_cycle[cycle_12z].anchor_available is False
        assert by_cycle[cycle_12z].complete is False
        assert newest_complete_cycle(avail) == cycle_06z

    def test_probe_economy_monotone_publication_assumed_downward(self):
        now = _dt("2026-06-11T03:20:00")
        calls: list[datetime] = []

        def probe(c: datetime) -> bool:
            calls.append(c)
            return True

        resolve_anchor_cycle_availability(now, probe_anchor=probe)
        assert len(calls) == 1

    def test_transport_failure_means_unavailable_not_crash(self):
        now = _dt("2026-06-11T03:20:00")
        avail = resolve_anchor_cycle_availability(now, probe_anchor=lambda c: False)
        assert newest_complete_cycle(avail) is None
        assert all(not a.complete for a in avail)

    def test_provider_resolution_fetches_current_meta_once(self, monkeypatch):
        import src.data.replacement_cycle_availability as rca

        meta_calls: list[None] = []
        bucket_calls: list[datetime] = []
        published = _dt("2026-06-11T12:00:00")

        monkeypatch.setattr(
            rca,
            "probe_openmeteo_single_run_available",
            lambda cycle, **kwargs: False,
        )
        probe = AnchorAvailabilityProbe(
            meta_fetch=lambda: meta_calls.append(None) or {
                "run_initialisation_utc": published,
                "run_availability_utc": published,
            },
        )
        monkeypatch.setattr(
            rca,
            "probe_bucket_run_declared",
            lambda cycle: bucket_calls.append(cycle) or False,
        )

        availability = resolve_anchor_cycle_availability(
            _dt("2026-06-11T19:00:00"),
            probe_anchor=probe,
        )

        assert newest_complete_cycle(availability) == published
        assert meta_calls == [None]
        assert bucket_calls == [_dt("2026-06-11T18:00:00")]

    def test_provider_resolution_reuses_source_clock_metadata(self, monkeypatch, tmp_path):
        import src.data.replacement_cycle_availability as rca

        updates_path = tmp_path / "updates.jsonl"
        published = _dt("2026-06-11T12:00:00")
        write_model_updates_jsonl(
            updates_path,
            [
                OpenMeteoModelUpdate(
                    model="ecmwf_ifs",
                    last_run_initialisation_time=published,
                    last_run_availability_time=published,
                )
            ],
        )
        monkeypatch.setattr(
            rca,
            "probe_openmeteo_single_run_available",
            lambda cycle, **kwargs: False,
        )
        monkeypatch.setattr(rca, "probe_bucket_run_declared", lambda cycle: False)
        monkeypatch.setattr(
            "src.data.openmeteo_ecmwf_ifs9_anchor.fetch_openmeteo_ifs9_model_meta",
            lambda: (_ for _ in ()).throw(AssertionError("network metadata duplicated")),
        )

        probe = AnchorAvailabilityProbe(cached_updates_path=updates_path)
        availability = resolve_anchor_cycle_availability(
            _dt("2026-06-11T19:00:00"),
            probe_anchor=probe,
        )

        assert newest_complete_cycle(availability) == published

    def test_corrupt_source_clock_metadata_falls_back_to_provider(self, monkeypatch, tmp_path):
        import src.data.replacement_cycle_availability as rca

        updates_path = tmp_path / "updates.jsonl"
        updates_path.write_text("not-json\n", encoding="utf-8")
        published = _dt("2026-06-11T12:00:00")
        monkeypatch.setattr(
            rca,
            "probe_openmeteo_single_run_available",
            lambda cycle, **kwargs: False,
        )
        monkeypatch.setattr(rca, "probe_bucket_run_declared", lambda cycle: False)
        monkeypatch.setattr(
            "src.data.openmeteo_ecmwf_ifs9_anchor.fetch_openmeteo_ifs9_model_meta",
            lambda: {
                "run_initialisation_utc": published,
                "run_availability_utc": published,
            },
        )

        probe = AnchorAvailabilityProbe(cached_updates_path=updates_path)

        assert probe(published) is True

    def test_free_meta_confirmation_avoids_metered_single_runs_probe(self):
        # Cost-order law (2026-08-25): the cached meta and S3 bucket manifest
        # are free; the single-runs API probe costs one quota unit (and 400s
        # before publication). When a free signal confirms, the metered probe
        # must never fire.
        cycle = _dt("2026-06-11T18:00:00")
        single_runs_calls: list[None] = []

        def counting_urlopen(*args, **kwargs):
            single_runs_calls.append(None)
            return type("Response", (), {
                "status": 200,
                "__enter__": lambda self: self,
                "__exit__": lambda self, *exc: None,
            })()

        probe = AnchorAvailabilityProbe(
            urlopen=counting_urlopen,
            meta_fetch=lambda: {
                "run_initialisation_utc": cycle,
                "run_availability_utc": cycle,
                "run_modification_utc": cycle,
            },
        )

        assert probe(cycle) is True
        assert single_runs_calls == []

    def test_metered_single_runs_probe_is_last_rung(self, monkeypatch):
        # When neither free signal confirms, the metered probe still decides.
        import src.data.replacement_cycle_availability as rca

        monkeypatch.setattr(rca, "probe_bucket_run_declared", lambda cycle: False)
        probe = AnchorAvailabilityProbe(
            urlopen=lambda *args, **kwargs: type("Response", (), {
                "status": 200,
                "__enter__": lambda self: self,
                "__exit__": lambda self, *exc: None,
            })(),
            meta_fetch=lambda: {},
        )

        assert probe(_dt("2026-06-11T18:00:00")) is True

    def test_production_single_run_probe_uses_shared_priority_quota(self, monkeypatch):
        import src.data.replacement_cycle_availability as rca

        calls = []

        def tracked_fetch(url, params, **kwargs):
            calls.append((url, params, kwargs, rca.quota_tracker._is_priority()))
            return {"hourly": {"temperature_2m": [20.0]}}

        monkeypatch.setattr(rca, "_fetch_openmeteo", tracked_fetch)

        assert probe_openmeteo_single_run_available(
            _dt("2026-06-11T18:00:00")
        ) is True
        assert calls[0][1] == {}
        assert calls[0][2]["endpoint_label"] == "source_clock_anchor_availability"
        assert calls[0][2]["fast_fail_429"] is True
        assert calls[0][2]["conditional_status_codes"] == frozenset({400})
        assert calls[0][3] is True

    def test_malformed_meta_falls_through_to_bucket(self, monkeypatch):
        import src.data.replacement_cycle_availability as rca

        monkeypatch.setattr(
            rca,
            "probe_openmeteo_single_run_available",
            lambda cycle, **kwargs: False,
        )
        monkeypatch.setattr(rca, "probe_bucket_run_declared", lambda cycle: True)

        assert AnchorAvailabilityProbe(meta_fetch=lambda: {})(
            _dt("2026-06-11T18:00:00")
        ) is True


class TestPollFetchDecision:
    """The production poll layer: anchor high-water vs probed publication."""

    def _run_poll(self, monkeypatch, tmp_path, *, anchor_pub, anchor_have, anchor_gaps=0):
        import scripts.download_replacement_forecast_current_targets as dl
        import src.data.replacement_cycle_availability as rca
        import src.data.replacement_forecast_production as prod
        import src.data.source_clock_update_probe as source_clock_probe

        fetched: list[tuple[str, dict[str, object]]] = []

        def fake_download(**kwargs):
            fetched.append(("anchor", kwargs))
            return {"status": "OK"}

        monkeypatch.setattr(dl, "download_current_target_openmeteo_inputs", fake_download)
        monkeypatch.setattr(
            rca, "probe_openmeteo_single_run_available", lambda c, **k: c <= anchor_pub
        )
        monkeypatch.setattr(
            rca, "probe_anchor_available_any", lambda c, **k: c <= anchor_pub
        )
        monkeypatch.setattr(prod, "_per_leg_downloaded_cycle", lambda db, sid: anchor_have)
        monkeypatch.setattr(
            prod,
            "_current_target_anchor_gap_count",
            lambda db, cycle: anchor_gaps,
        )

        class _NoSourceClockChange:
            updated_sources = ()

            def as_dict(self):
                return {
                    "status": "SOURCE_CLOCK_NO_PUBLICLY_USABLE_CHANGE",
                    "updated_sources": [],
                    "affected_cities": [],
                    "error": None,
                }

        monkeypatch.setattr(
            source_clock_probe,
            "probe_openmeteo_source_clock_updates",
            lambda **_kwargs: _NoSourceClockChange(),
        )

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: D102
                return datetime(2026, 6, 10, 22, 30, tzinfo=UTC)

        monkeypatch.setattr(prod, "datetime", _FrozenDatetime)
        cfg = {
            "download_current_targets_enabled": True,
            "forecast_db": tmp_path / "f.db",
            "download_output_dir": tmp_path,
        }
        report = prod._replacement_cycle_availability_poll_if_needed(cfg)
        return report, fetched

    def test_fetches_published_anchor_it_lacks(self, monkeypatch, tmp_path):
        report, fetched = self._run_poll(
            monkeypatch,
            tmp_path,
            anchor_pub=_dt("2026-06-10T06:00:00"),
            anchor_have=None,
        )
        assert [(leg, row["cycle"].isoformat()) for leg, row in fetched] == [
            ("anchor", "2026-06-10T06:00:00+00:00")
        ]
        assert fetched[0][1]["quota_priority"] is True
        assert report["status"] == "AVAILABILITY_POLL"

    def test_noop_when_holdings_match_publication(self, monkeypatch, tmp_path):
        report, fetched = self._run_poll(
            monkeypatch,
            tmp_path,
            anchor_pub=_dt("2026-06-10T12:00:00"),
            anchor_have=_dt("2026-06-10T12:00:00"),
        )
        assert fetched == []
        assert report["status"] == "AVAILABILITY_POLL_CURRENT"

    def test_unknown_holdings_fail_open_to_fetch(self, monkeypatch, tmp_path):
        _, fetched = self._run_poll(
            monkeypatch,
            tmp_path,
            anchor_pub=_dt("2026-06-10T12:00:00"),
            anchor_have=None,
        )
        assert [(leg, row["cycle"].isoformat()) for leg, row in fetched] == [
            ("anchor", "2026-06-10T12:00:00+00:00")
        ]

    def test_partial_cycle_coverage_keeps_fetching_missing_targets(
        self, monkeypatch, tmp_path
    ):
        cycle = _dt("2026-06-10T12:00:00")
        report, fetched = self._run_poll(
            monkeypatch,
            tmp_path,
            anchor_pub=cycle,
            anchor_have=cycle,
            anchor_gaps=205,
        )

        assert report["anchor_missing_scope_count"] == 205
        assert len(fetched) == 1
        assert fetched[0][1]["include_covered"] is False
        assert fetched[0][1]["missing_manifests_only"] is True
        assert fetched[0][1]["quota_priority"] is True

    def test_flag_off_is_inert(self, tmp_path):
        import src.data.replacement_forecast_production as prod

        assert (
            prod._replacement_cycle_availability_poll_if_needed(
                {"download_current_targets_enabled": False}
            )
            is None
        )

    def test_source_clock_change_does_not_bypass_extras_coverage_gate(self, monkeypatch, tmp_path):
        import src.data.replacement_forecast_production as prod
        import src.data.source_clock_update_probe as source_clock_probe

        cycle = _dt("2026-06-10T12:00:00")
        calls: list[str] = []

        class _SourceClockChanged:
            updated_sources = ("met_nordic",)

            def as_dict(self):
                return {
                    "status": "SOURCE_CLOCK_UPDATES_CHANGED",
                    "updated_sources": ["met_nordic"],
                    "affected_cities": ["Helsinki"],
                    "error": None,
                }

        monkeypatch.setattr(prod, "_per_leg_downloaded_cycle", lambda db, sid: cycle)
        monkeypatch.setattr(
            source_clock_probe,
            "probe_openmeteo_source_clock_updates",
            lambda **_kwargs: _SourceClockChanged(),
        )
        monkeypatch.setattr(prod, "_probe_resolved_bayes_precision_fusion_extras_cycle", lambda: cycle)
        monkeypatch.setattr(prod, "_extras_cycle_incomplete", lambda cfg, resolved_cycle: False)
        monkeypatch.setattr(
            prod,
            "_download_bayes_precision_fusion_extra_raw_inputs_if_needed",
            lambda cfg: calls.append("extras") or {
                "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
                "written_row_count": 0,
            },
        )
        monkeypatch.setattr(
            prod,
            "_enqueue_fusion_upgrade_reseeds_if_needed",
            lambda cfg: {"status": "FUSION_UPGRADE_TRIGGER", "seeds_enqueued": 0},
        )
        monkeypatch.setattr(
            prod,
            "_enqueue_cycle_advance_reseeds_if_needed",
            lambda cfg: {"status": "CYCLE_ADVANCE_TRIGGER", "seeds_enqueued": 0},
        )

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: D102
                return datetime(2026, 6, 10, 22, 30, tzinfo=UTC)

        monkeypatch.setattr(prod, "datetime", _FrozenDatetime)
        report = prod._replacement_cycle_availability_poll_if_needed(
            {
                "download_current_targets_enabled": True,
                "forecast_db": tmp_path / "f.db",
                "download_output_dir": tmp_path,
            }
        )

        assert report["source_clock_status"] == "SOURCE_CLOCK_UPDATES_CHANGED"
        assert report["source_clock_updated_sources"] == ["met_nordic"]
        assert report["bayes_precision_fusion_extras_status"] == "EXTRAS_CURRENT_CYCLE_COMPLETE_SKIPPED"
        assert calls == []


class TestNoGuessAntibody:
    """Availability must be structurally incapable of consulting a lag guess."""

    def test_availability_module_has_no_release_lag_reference(self):
        import inspect

        import src.data.replacement_cycle_availability as rca

        src_text = inspect.getsource(rca)
        assert "release_lag" not in src_text
        assert "RELEASE_LAG" not in src_text
