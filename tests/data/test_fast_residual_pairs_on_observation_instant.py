"""A residual pairs two channels' renderings of ONE observation.

Both the settlement channel and the fast METAR mirror republish the SAME physical
observation; only the publish instant differs, and each station's republish offset
is fixed.  Pairing on publish proximity therefore does not degrade gracefully --
a station whose mirror lags more than the tolerance pairs nothing, every hour,
forever, while a station inside the tolerance pairs everything.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.data.day0_fast_obs import _fast_residual_observation_instant

UTC = timezone.utc


def _metar(station: str, day: int, hour: int, minute: int, temp: int) -> str:
    return (
        f"{station} {day:02d}{hour:02d}{minute:02d}Z 08004KT 6000 FEW017 "
        f"{temp:02d}/25 Q1008 NOSIG"
    )


class TestObservationInstant:
    """The key is what the report says it observed, not when a mirror sent it."""

    def test_metar_report_yields_its_own_observation_instant(self) -> None:
        published = datetime(2026, 9, 18, 10, 8, 16, tzinfo=UTC)
        assert _fast_residual_observation_instant(
            raw_report=_metar("WMKK", 18, 10, 0, 28), published=published
        ) == datetime(2026, 9, 18, 10, 0, tzinfo=UTC)

    def test_both_channels_of_one_observation_agree(self) -> None:
        """The live defect: 8m16s apart on publish, identical on observation."""

        raw = _metar("WMKK", 18, 10, 0, 28)
        mirror = _fast_residual_observation_instant(
            raw_report=raw, published=datetime(2026, 9, 18, 10, 0, tzinfo=UTC)
        )
        fast = _fast_residual_observation_instant(
            raw_report=raw, published=datetime(2026, 9, 18, 10, 8, 16, tzinfo=UTC)
        )
        assert mirror == fast

    def test_absent_report_falls_back_to_the_publish_instant(self) -> None:
        """wu_icao_history carries no raw text and publishes AT the observation."""

        published = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)
        assert (
            _fast_residual_observation_instant(raw_report="", published=published)
            == published
        )
        assert (
            _fast_residual_observation_instant(raw_report=None, published=published)
            == published
        )

    def test_day_group_resolves_across_a_month_boundary(self) -> None:
        """A report published on the 1st can observe the previous month's 30th."""

        published = datetime(2026, 10, 1, 0, 8, tzinfo=UTC)
        assert _fast_residual_observation_instant(
            raw_report=_metar("WMKK", 30, 23, 30, 22), published=published
        ) == datetime(2026, 9, 30, 23, 30, tzinfo=UTC)

    def test_impossible_day_group_falls_back_rather_than_raising(self) -> None:
        published = datetime(2026, 9, 18, 10, 8, tzinfo=UTC)
        assert (
            _fast_residual_observation_instant(
                raw_report="WMKK 991099Z 08004KT", published=published
            )
            == published
        )

    def test_a_report_far_from_its_publish_instant_is_not_adopted(self) -> None:
        """A stale quoted group must not silently key an unrelated observation."""

        published = datetime(2026, 9, 18, 10, 8, tzinfo=UTC)
        # day 02 resolves to 2026-09-02, sixteen days before publication
        assert (
            _fast_residual_observation_instant(
                raw_report=_metar("WMKK", 2, 10, 0, 28), published=published
            )
            == published
        )

    def test_none_publish_instant_is_none(self) -> None:
        assert (
            _fast_residual_observation_instant(
                raw_report=_metar("WMKK", 18, 10, 0, 28), published=None
            )
            is None
        )
