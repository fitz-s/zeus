"""A residual must compare measurements, not one channel's rounded view.

The weather.gov mirror renders the SAME METAR the fast feed carries, but its
published number is a rounded view of it: KAUS 78.8F is exactly 26.0C while the
report's own ``T02560...`` group says 25.6C.  Converting the published view
manufactured residuals of up to 0.4C out of a single measurement, which is a
fabricated disagreement -- the settlement source and the market both work from
the precise report.
"""

from __future__ import annotations

from src.data.day0_fast_obs import FAST_OBS_SOURCE_ID, _fast_residual_value_c

_KAUS = (
    "KAUS 180453Z 00000KT 10SM SCT200 26/22 A3015 RMK AO2 SLP198 T02560222"
)


class TestSettlementChannelPrecision:
    def test_published_fahrenheit_does_not_override_the_reports_tenths(self):
        """78.8F converts to exactly 26.0C; the report itself says 25.6C."""

        value = _fast_residual_value_c(
            channel="noaa_wrh_kaus",
            value_native=78.8,
            unit="F",
            raw_report=_KAUS,
            settlement_unit="F",
        )
        assert value == 25.6

    def test_both_channels_of_one_report_now_agree_exactly(self):
        """The whole point: one measurement must yield a zero residual."""

        settlement = _fast_residual_value_c(
            channel="noaa_wrh_kaus",
            value_native=78.8,
            unit="F",
            raw_report=_KAUS,
            settlement_unit="F",
        )
        fast = _fast_residual_value_c(
            channel=FAST_OBS_SOURCE_ID,
            value_native=25.6,
            unit="C",
            raw_report=_KAUS,
            settlement_unit="F",
        )
        assert settlement == fast
        assert round(settlement - fast, 6) == 0.0

    def test_a_report_without_the_group_still_uses_the_published_value(self):
        """Fallback is unchanged: no group means the published number is all we have."""

        value = _fast_residual_value_c(
            channel="noaa_wrh_kaus",
            value_native=78.8,
            unit="F",
            raw_report="KAUS 180453Z 00000KT 10SM SCT200 26/22 A3015",
            settlement_unit="F",
        )
        assert value is not None
        assert abs(value - (78.8 - 32.0) * 5.0 / 9.0) < 1e-9

    def test_celsius_channel_without_a_group_is_untouched(self):
        value = _fast_residual_value_c(
            channel="noaa_wrh_rjtt",
            value_native=21.0,
            unit="C",
            raw_report="",
            settlement_unit="C",
        )
        assert value == 21.0

    def test_a_celsius_city_still_prefers_the_reports_own_group(self):
        """One rule for both families: the report's tenths are the measurement."""

        value = _fast_residual_value_c(
            channel="noaa_wrh_rjtt",
            value_native=21.0,
            unit="C",
            raw_report="RJTT 181000Z 08004KT 9999 FEW020 21/15 Q1012 RMK T02110150",
            settlement_unit="C",
        )
        assert value == 21.1

    def test_a_malformed_group_falls_back_rather_than_raising(self):
        value = _fast_residual_value_c(
            channel="noaa_wrh_kaus",
            value_native=78.8,
            unit="F",
            raw_report="KAUS 180453Z 26/22 TXXXXXXXX",
            settlement_unit="F",
        )
        assert value is not None
        assert abs(value - (78.8 - 32.0) * 5.0 / 9.0) < 1e-9
