# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 ~03:40Z (automatic download, ahead of
#   need, NO guessed numbers) — K4.0b(a) availability-poll organ. Relationship tests for
#   the probe-resolved cycle selection and the per-leg fetch decision.
"""The category these tests make unconstructable: a download cadence whose cycle choice
depends on a guessed release-lag constant, or whose whole-cycle granularity lets one
lagging provider leg (the 2026-06-10 12Z open-meteo anchor) block the other leg's
freshness."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data.replacement_cycle_availability import (
    CycleLegAvailability,
    candidate_cycles,
    floor_to_cycle,
    newest_complete_cycle,
    resolve_cycle_leg_availability,
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
        # Wall clock 03:20Z. A 14h-lag rule would claim newest available = previous-day
        # 12Z. The probes say the brand-new 00Z cycle IS published. The probes must win.
        now = _dt("2026-06-11T03:20:00")
        avail = resolve_cycle_leg_availability(
            now, probe_aifs=lambda c: True, probe_anchor=lambda c: True
        )
        assert newest_complete_cycle(avail) == _dt("2026-06-11T00:00:00")

    def test_unpublished_newest_falls_back_to_probed_older(self):
        # Inverse direction: a 0h-lag rule would claim 00Z available; the probes say the
        # providers have published nothing newer than 18Z. The probes must win again.
        now = _dt("2026-06-11T03:20:00")
        published_from = _dt("2026-06-10T18:00:00")
        avail = resolve_cycle_leg_availability(
            now,
            probe_aifs=lambda c: c <= published_from,
            probe_anchor=lambda c: c <= published_from,
        )
        assert newest_complete_cycle(avail) == published_from

    def test_12z_incident_shape_per_leg_divergence(self):
        # THE 2026-06-10 incident: AIFS 12Z published, open-meteo 12Z anchor NOT.
        # The resolver must report the divergence so the caller fetches the AIFS leg
        # immediately and keeps polling only the anchor leg.
        now = _dt("2026-06-10T22:30:00")
        cycle_12z = _dt("2026-06-10T12:00:00")
        cycle_06z = _dt("2026-06-10T06:00:00")
        avail = resolve_cycle_leg_availability(
            now,
            probe_aifs=lambda c: c <= cycle_12z,
            probe_anchor=lambda c: c <= cycle_06z,
        )
        by_cycle = {a.cycle: a for a in avail}
        assert by_cycle[cycle_12z].aifs_available is True
        assert by_cycle[cycle_12z].anchor_available is False
        assert by_cycle[cycle_12z].complete is False
        assert newest_complete_cycle(avail) == cycle_06z

    def test_probe_economy_monotone_publication_assumed_downward(self):
        # Once a leg probes available at cycle C, older cycles of the same leg are not
        # re-probed (provider publication is monotone). Counts probe invocations.
        now = _dt("2026-06-11T03:20:00")
        calls: list[datetime] = []

        def probe(c: datetime) -> bool:
            calls.append(c)
            return True

        resolve_cycle_leg_availability(now, probe_aifs=probe, probe_anchor=lambda c: True)
        assert len(calls) == 1  # newest cycle probed once; older cycles inferred

    def test_transport_failure_means_unavailable_not_crash(self):
        now = _dt("2026-06-11T03:20:00")

        def flaky(c: datetime) -> bool:
            return False  # probes already swallow transport errors into False

        avail = resolve_cycle_leg_availability(now, probe_aifs=flaky, probe_anchor=flaky)
        assert newest_complete_cycle(avail) is None
        assert all(not a.complete for a in avail)


class TestPollFetchDecision:
    """The production poll layer: per-leg high-water vs probed publication."""

    def _run_poll(self, monkeypatch, tmp_path, *, aifs_pub, anchor_pub, aifs_have, anchor_have):
        import src.data.replacement_forecast_production as prod

        fetched: list[tuple[str, str]] = []

        def fake_download(**kwargs):
            leg = "anchor" if kwargs.get("skip_aifs") else "aifs"
            fetched.append((leg, kwargs["cycle"].isoformat()))
            return {"status": "OK"}

        import scripts.download_replacement_forecast_current_targets as dl

        monkeypatch.setattr(dl, "download_current_target_raw_inputs", fake_download)
        import src.data.replacement_cycle_availability as rca

        monkeypatch.setattr(
            rca, "probe_aifs_cycle_available", lambda c, **k: c <= aifs_pub
        )
        monkeypatch.setattr(
            rca, "probe_openmeteo_single_run_available", lambda c, **k: c <= anchor_pub
        )
        # Hermetic boundary: probe_anchor_available_any now also consults the provider
        # meta endpoint and the S3 bucket manifest (rung-2/3 mirrors). Stub the whole
        # anchor probe so the poll decision test never touches the network and the
        # publication state is exactly the declared fixture.
        monkeypatch.setattr(
            rca, "probe_anchor_available_any", lambda c, **k: c <= anchor_pub
        )
        monkeypatch.setattr(
            prod,
            "_per_leg_downloaded_cycle",
            lambda db, sid: aifs_have if sid == "ecmwf_aifs_ens" else anchor_have,
        )
        cfg = {
            "download_current_targets_enabled": True,
            "forecast_db": tmp_path / "f.db",
            "download_output_dir": tmp_path,
        }
        report = prod._replacement_cycle_availability_poll_if_needed(cfg)
        return report, fetched

    def test_fetches_each_published_leg_it_lacks(self, monkeypatch, tmp_path):
        report, fetched = self._run_poll(
            monkeypatch,
            tmp_path,
            aifs_pub=_dt("2026-06-10T12:00:00"),
            anchor_pub=_dt("2026-06-10T06:00:00"),
            aifs_have=_dt("2026-06-10T06:00:00"),
            anchor_have=_dt("2026-06-10T06:00:00"),
        )
        # AIFS 12Z published but not held -> fetched; anchor newest published is already
        # held -> NOT fetched (and crucially, the lagging anchor does not block AIFS).
        assert ("aifs", "2026-06-10T12:00:00+00:00") in fetched
        assert all(leg != "anchor" for leg, _ in fetched)
        assert report["status"] == "AVAILABILITY_POLL"

    def test_noop_when_holdings_match_publication(self, monkeypatch, tmp_path):
        report, fetched = self._run_poll(
            monkeypatch,
            tmp_path,
            aifs_pub=_dt("2026-06-10T12:00:00"),
            anchor_pub=_dt("2026-06-10T12:00:00"),
            aifs_have=_dt("2026-06-10T12:00:00"),
            anchor_have=_dt("2026-06-10T12:00:00"),
        )
        assert fetched == []
        assert report["status"] == "AVAILABILITY_POLL_CURRENT"

    def test_unknown_holdings_fail_open_to_fetch(self, monkeypatch, tmp_path):
        _, fetched = self._run_poll(
            monkeypatch,
            tmp_path,
            aifs_pub=_dt("2026-06-10T12:00:00"),
            anchor_pub=_dt("2026-06-10T12:00:00"),
            aifs_have=None,
            anchor_have=None,
        )
        assert ("aifs", "2026-06-10T12:00:00+00:00") in fetched
        assert ("anchor", "2026-06-10T12:00:00+00:00") in fetched

    def test_flag_off_is_inert(self, tmp_path):
        import src.data.replacement_forecast_production as prod

        assert (
            prod._replacement_cycle_availability_poll_if_needed(
                {"download_current_targets_enabled": False}
            )
            is None
        )


class TestNoGuessAntibody:
    """AST-level pin: the availability module must not import or read the release-lag
    setting — the probe decision must be structurally incapable of consulting a guess."""

    def test_availability_module_has_no_release_lag_reference(self):
        import inspect

        import src.data.replacement_cycle_availability as rca

        src_text = inspect.getsource(rca)
        assert "release_lag" not in src_text
        assert "RELEASE_LAG" not in src_text
