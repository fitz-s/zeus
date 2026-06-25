# Created: 2026-06-11
# Last reused or audited: 2026-06-18
# Authority basis: operator directive 2026-06-11 ~03:40Z (automatic download, ahead of
#   need, NO guessed numbers) and 2026-06-18 live/experiment separation. Relationship
#   tests for probe-resolved anchor cycle selection and fetch decision.
"""Make guessed release-lag cycle selection and retired non-live legs unconstructable."""
from __future__ import annotations

from datetime import datetime, timezone

from src.data.replacement_cycle_availability import (
    candidate_cycles,
    floor_to_cycle,
    newest_complete_cycle,
    resolve_anchor_cycle_availability,
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


class TestPollFetchDecision:
    """The production poll layer: anchor high-water vs probed publication."""

    def _run_poll(self, monkeypatch, tmp_path, *, anchor_pub, anchor_have):
        import scripts.download_replacement_forecast_current_targets as dl
        import src.data.replacement_cycle_availability as rca
        import src.data.replacement_forecast_production as prod
        import src.data.source_clock_update_probe as source_clock_probe

        fetched: list[tuple[str, str]] = []

        def fake_download(**kwargs):
            fetched.append(("anchor", kwargs["cycle"].isoformat()))
            return {"status": "OK"}

        monkeypatch.setattr(dl, "download_current_target_openmeteo_inputs", fake_download)
        monkeypatch.setattr(
            rca, "probe_openmeteo_single_run_available", lambda c, **k: c <= anchor_pub
        )
        monkeypatch.setattr(
            rca, "probe_anchor_available_any", lambda c, **k: c <= anchor_pub
        )
        monkeypatch.setattr(prod, "_per_leg_downloaded_cycle", lambda db, sid: anchor_have)

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
            lambda: _NoSourceClockChange(),
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
        assert fetched == [("anchor", "2026-06-10T06:00:00+00:00")]
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
        assert ("anchor", "2026-06-10T12:00:00+00:00") in fetched

    def test_flag_off_is_inert(self, tmp_path):
        import src.data.replacement_forecast_production as prod

        assert (
            prod._replacement_cycle_availability_poll_if_needed(
                {"download_current_targets_enabled": False}
            )
            is None
        )

    def test_source_clock_change_forces_extras_same_poll(self, monkeypatch, tmp_path):
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
            lambda: _SourceClockChanged(),
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
        assert calls == ["extras"]


class TestNoGuessAntibody:
    """Availability must be structurally incapable of consulting a lag guess."""

    def test_availability_module_has_no_release_lag_reference(self):
        import inspect

        import src.data.replacement_cycle_availability as rca

        src_text = inspect.getsource(rca)
        assert "release_lag" not in src_text
        assert "RELEASE_LAG" not in src_text
