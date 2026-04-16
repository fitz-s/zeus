"""Relationship tests for Phase 3 observation closure — R-F and R-H.

Phase: 3 (Observation client low_so_far + source registry collapse)
R-numbers covered:
  R-F (provider closure): every provider that returns a valid observation
       must include BOTH high_so_far: float AND low_so_far: float. Returning
       low_so_far=None is forbidden at the public seam; the provider must raise.
  R-H (evaluator low unblock): a city with valid low_so_far in Day0ObservationContext
       does NOT hit the OBSERVATION_UNAVAILABLE_LOW rejection branch.

These tests MUST FAIL today (2026-04-16) because:
  - src.data.observation_client.Day0ObservationContext does not yet exist
    (R-F tests will ImportError or AttributeError).
  - The evaluator low-reject branch fires on low_so_far=None in the old dict
    contract; R-H proves the new typed context removes that trigger.

First commit that should make this green: exec-bob's Phase 3 implementation
(adds Day0ObservationContext dataclass, unifies all providers to return it,
ensures low_so_far is never None at the public seam).
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# R-F — Provider closure: low_so_far is required, never None
# ---------------------------------------------------------------------------


class TestProviderClosureLowSoFar(unittest.TestCase):
    """R-F: every live provider path exposes low_so_far: float in its return value."""

    def _import_context(self):
        """Import Day0ObservationContext; fail clearly if Phase 3 not yet landed."""
        try:
            from src.data.observation_client import Day0ObservationContext
            return Day0ObservationContext
        except ImportError:
            self.fail(
                "Phase 3 not yet implemented: Day0ObservationContext does not exist "
                "in src.data.observation_client"
            )

    def test_day0_observation_context_exists(self):
        """R-F smoke test: Day0ObservationContext is importable from observation_client."""
        self._import_context()

    def test_day0_observation_context_has_low_so_far_field(self):
        """R-F: Day0ObservationContext has a low_so_far attribute (not absent, not optional)."""
        ctx_cls = self._import_context()
        import dataclasses
        fields = {f.name for f in dataclasses.fields(ctx_cls)}
        self.assertIn(
            "low_so_far",
            fields,
            "Day0ObservationContext must declare a low_so_far field",
        )

    def test_day0_observation_context_low_so_far_is_float(self):
        """R-F: A correctly-constructed Day0ObservationContext carries low_so_far as float."""
        ctx_cls = self._import_context()
        import dataclasses
        fields = {f.name: f for f in dataclasses.fields(ctx_cls)}
        self.assertIn("low_so_far", fields)
        field = fields["low_so_far"]
        # The type annotation must not be Optional[float] or allow None
        annotation = field.type if hasattr(field, "type") else None
        if annotation is not None:
            ann_str = str(annotation)
            self.assertNotIn(
                "None",
                ann_str,
                "low_so_far must not be Optional — None is forbidden at this seam",
            )

    def test_wu_provider_returns_low_so_far_in_context(self):
        """R-F: WU provider path returns Day0ObservationContext with non-None low_so_far.

        Today this fails because get_current_observation returns a plain dict
        without low_so_far, and Day0ObservationContext doesn't exist yet.
        """
        ctx_cls = self._import_context()
        try:
            from src.data.observation_client import get_current_observation
        except ImportError:
            self.fail("get_current_observation not importable from observation_client")

        from unittest.mock import MagicMock, patch
        import httpx

        fake_wu_payload = {
            "observations": [
                {"temp": 68.0, "valid_time_gmt": 1776333600},  # 2026-04-16 06:00 ET
                {"temp": 75.0, "valid_time_gmt": 1776355200},  # 2026-04-16 12:00 ET
                {"temp": 72.0, "valid_time_gmt": 1776376800},  # 2026-04-16 18:00 ET
            ]
        }

        mock_city = MagicMock()
        mock_city.name = "NYC"
        mock_city.lat = 40.77
        mock_city.lon = -73.87
        mock_city.timezone = "America/New_York"
        mock_city.settlement_unit = "F"
        mock_city.wu_station = "KLGA"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_wu_payload

        with patch("httpx.get", return_value=mock_resp):
            from datetime import datetime, timezone, date
            ref_time = datetime(2026, 4, 16, 18, 0, 0, tzinfo=timezone.utc)
            result = get_current_observation(
                mock_city,
                target_date=date(2026, 4, 16),
                reference_time=ref_time,
            )

        # After Phase 3, result must be a Day0ObservationContext (or have low_so_far)
        self.assertIsNotNone(result, "WU provider must return a result when data is available")
        if isinstance(result, dict):
            self.assertIn(
                "low_so_far",
                result,
                "WU provider must include low_so_far in its return value (R-F violation)",
            )
            self.assertIsNotNone(
                result.get("low_so_far"),
                "WU provider must not return low_so_far=None (R-F: fail-closed required)",
            )
        else:
            # Should be a Day0ObservationContext
            self.assertIsInstance(result, ctx_cls)
            self.assertIsNotNone(result.low_so_far)

    def test_openmeteo_provider_returns_low_so_far_in_context(self):
        """R-F: Open-Meteo fallback provider returns Day0ObservationContext with non-None low_so_far.

        Today this fails because _fetch_openmeteo_hourly returns a plain dict
        without low_so_far.
        """
        ctx_cls = self._import_context()
        try:
            from src.data.observation_client import _fetch_openmeteo_hourly
        except ImportError:
            self.fail("_fetch_openmeteo_hourly not importable")

        fake_openmeteo_payload = {
            "hourly": {
                "time": [
                    "2026-04-16T06:00",
                    "2026-04-16T10:00",
                    "2026-04-16T14:00",
                ],
                "temperature_2m": [55.0, 70.0, 63.0],
            }
        }

        mock_city = MagicMock()
        mock_city.name = "NYC"
        mock_city.lat = 40.77
        mock_city.lon = -73.87
        mock_city.timezone = "America/New_York"
        mock_city.settlement_unit = "F"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = fake_openmeteo_payload

        from datetime import datetime, timezone, date

        ref_local = datetime(2026, 4, 16, 16, 0, 0)
        from zoneinfo import ZoneInfo
        ref_local = ref_local.replace(tzinfo=ZoneInfo("America/New_York"))
        target_day = date(2026, 4, 16)

        with patch("httpx.get", return_value=mock_resp):
            with patch("src.data.observation_client.quota_tracker") as mock_quota:
                mock_quota.can_call.return_value = True
                mock_quota.record_call.return_value = None
                result = _fetch_openmeteo_hourly(
                    mock_city,
                    target_day=target_day,
                    reference_local=ref_local,
                    tz=ZoneInfo("America/New_York"),
                )

        self.assertIsNotNone(result, "Open-Meteo provider must return a result when data is available")
        if isinstance(result, dict):
            self.assertIn(
                "low_so_far",
                result,
                "Open-Meteo provider must include low_so_far in its return value (R-F violation)",
            )
            self.assertIsNotNone(
                result.get("low_so_far"),
                "Open-Meteo provider must not return low_so_far=None (R-F: fail-closed required)",
            )
        else:
            self.assertIsInstance(result, ctx_cls)
            self.assertIsNotNone(result.low_so_far)

    def test_observation_context_none_low_so_far_is_unconstructable(self):
        """R-F: Day0ObservationContext cannot be constructed with low_so_far=None.

        The constructor must reject None so the ban is enforced structurally,
        not by caller discipline.
        """
        ctx_cls = self._import_context()
        import dataclasses

        # Try to construct with low_so_far=None — must raise TypeError or ValueError
        required_fields = {f.name for f in dataclasses.fields(ctx_cls) if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING}
        kwargs = {}
        for f in dataclasses.fields(ctx_cls):
            if f.name == "low_so_far":
                kwargs[f.name] = None
            elif f.name in ("current_temp", "high_so_far"):
                kwargs[f.name] = 72.0
            elif f.name == "unit":
                kwargs[f.name] = "F"
            elif f.name == "source":
                kwargs[f.name] = "wu_api"
            elif f.name == "observation_time":
                kwargs[f.name] = "2026-04-16T18:00:00"
            elif f.default is not dataclasses.MISSING:
                kwargs[f.name] = f.default
            elif f.default_factory is not dataclasses.MISSING:
                kwargs[f.name] = f.default_factory()

        with self.assertRaises((TypeError, ValueError),
                               msg="Day0ObservationContext must refuse low_so_far=None"):
            ctx_cls(**kwargs)


# ---------------------------------------------------------------------------
# R-F (type seam) — providers must return Day0ObservationContext, never a bare dict
# ---------------------------------------------------------------------------


class TestProviderReturnsTypedContext(unittest.TestCase):
    """R-F type seam: get_current_observation must return Day0ObservationContext,
    not a plain dict.

    The 'isinstance(result, dict)' branches in the WU/Open-Meteo provider tests
    above are looser than the law. After Phase 3, the public seam of
    get_current_observation is typed: callers receive a Day0ObservationContext
    instance, never a raw dict. A plain dict at the seam re-opens the implicit
    field-access pattern that Phase 3 is designed to eliminate.

    These tests FAIL today if exec-bob's implementation still returns a dict
    from any provider path, or if Day0ObservationContext doesn't exist.
    """

    def _import_context(self):
        try:
            from src.data.observation_client import Day0ObservationContext
            return Day0ObservationContext
        except ImportError:
            self.fail(
                "Phase 3 not yet implemented: Day0ObservationContext not in observation_client"
            )

    def test_get_current_observation_returns_typed_context_not_dict(self):
        """R-F type seam: get_current_observation must return Day0ObservationContext.

        A plain dict return is forbidden at the public seam even if it contains
        the correct keys — the type contract is the seam, not the key set.
        """
        ctx_cls = self._import_context()
        from src.data.observation_client import get_current_observation
        from unittest.mock import MagicMock, patch
        from datetime import datetime, timezone, date

        fake_wu_payload = {
            "observations": [
                {"temp": 68.0, "valid_time_gmt": 1776333600},  # 2026-04-16 06:00 ET
                {"temp": 75.0, "valid_time_gmt": 1776355200},  # 2026-04-16 12:00 ET
                {"temp": 72.0, "valid_time_gmt": 1776376800},  # 2026-04-16 18:00 ET
            ]
        }
        mock_city = MagicMock()
        mock_city.name = "NYC"
        mock_city.lat = 40.77
        mock_city.lon = -73.87
        mock_city.timezone = "America/New_York"
        mock_city.settlement_unit = "F"
        mock_city.wu_station = "KLGA"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_wu_payload

        with patch("httpx.get", return_value=mock_resp):
            ref_time = datetime(2026, 4, 16, 18, 0, 0, tzinfo=timezone.utc)
            result = get_current_observation(
                mock_city,
                target_date=date(2026, 4, 16),
                reference_time=ref_time,
            )

        self.assertIsNotNone(result)
        self.assertIsInstance(
            result,
            ctx_cls,
            f"get_current_observation must return Day0ObservationContext, got {type(result).__name__}. "
            "A plain dict at the public seam is forbidden — callers depend on the typed contract.",
        )
        self.assertNotIsInstance(
            result,
            dict,
            "get_current_observation must not return a bare dict — "
            "the type seam requires Day0ObservationContext (R-F / INV-14 analog).",
        )

    def test_internal_provider_fetch_wu_returns_typed_context(self):
        """R-F type seam: _fetch_wu_observation must return Day0ObservationContext, not a dict.

        The internal provider helpers are the origin of the seam — if they return
        dicts, get_current_observation must convert them before returning. Either
        the helper returns the typed context directly, or the public function wraps
        it. This test probes the helper directly.
        """
        ctx_cls = self._import_context()
        try:
            from src.data.observation_client import _fetch_wu_observation
        except ImportError:
            self.fail("_fetch_wu_observation not importable from observation_client")

        from unittest.mock import MagicMock, patch
        from datetime import datetime, timezone, date
        from zoneinfo import ZoneInfo

        fake_wu_payload = {
            "observations": [
                {"temp": 68.0, "valid_time_gmt": 1776333600},
                {"temp": 75.0, "valid_time_gmt": 1776355200},
            ]
        }
        mock_city = MagicMock()
        mock_city.name = "NYC"
        mock_city.lat = 40.77
        mock_city.lon = -73.87
        mock_city.timezone = "America/New_York"
        mock_city.settlement_unit = "F"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_wu_payload

        tz = ZoneInfo("America/New_York")
        target_day = date(2026, 4, 16)
        reference_local = datetime(2026, 4, 16, 18, 0, 0, tzinfo=tz)

        with patch("httpx.get", return_value=mock_resp):
            result = _fetch_wu_observation(
                mock_city,
                target_day=target_day,
                reference_local=reference_local,
                tz=tz,
            )

        if result is not None:
            self.assertIsInstance(
                result,
                ctx_cls,
                f"_fetch_wu_observation must return Day0ObservationContext or None, got {type(result).__name__}",
            )
            self.assertNotIsInstance(result, dict)


# ---------------------------------------------------------------------------
# R-H — Evaluator low unblock: valid low_so_far prevents rejection
# ---------------------------------------------------------------------------


class TestEvaluatorLowUnblock(unittest.TestCase):
    """R-H: a city with valid low_so_far from Day0ObservationContext does not
    hit the OBSERVATION_UNAVAILABLE_LOW rejection branch in evaluator.py."""

    def _import_context(self):
        try:
            from src.data.observation_client import Day0ObservationContext
            return Day0ObservationContext
        except ImportError:
            self.fail(
                "Phase 3 not yet implemented: Day0ObservationContext not in observation_client"
            )

    def test_day0_observation_context_used_in_evaluator_path(self):
        """R-H smoke: evaluator imports or references Day0ObservationContext after Phase 3.

        Today fails because Day0ObservationContext doesn't exist.
        """
        # Importing Day0ObservationContext is the gate
        self._import_context()
        # evaluator must at minimum be importable after the new context lands
        try:
            import src.engine.evaluator  # noqa: F401
        except ImportError:
            self.fail("src.engine.evaluator not importable")

    def test_valid_low_so_far_bypasses_low_reject_branch(self):
        """R-H: candidate.observation with low_so_far: float must NOT hit the low-reject branch.

        Today this fails because:
        1. Day0ObservationContext doesn't exist (ImportError).
        2. Even if constructed by hand, the evaluator checks observation.get("low_so_far")
           on a plain dict — the Day0ObservationContext contract doesn't exist yet.

        After Phase 3, get_current_observation returns Day0ObservationContext with
        low_so_far always set, so the rejection_stage block at evaluator.py:800-809
        never fires for cities with a working provider path.
        """
        ctx_cls = self._import_context()

        import dataclasses
        # Construct a valid Day0ObservationContext with low_so_far set
        kwargs = {}
        for f in dataclasses.fields(ctx_cls):
            if f.name == "low_so_far":
                kwargs[f.name] = 62.0
            elif f.name == "high_so_far":
                kwargs[f.name] = 75.0
            elif f.name == "current_temp":
                kwargs[f.name] = 70.0
            elif f.name == "unit":
                kwargs[f.name] = "F"
            elif f.name == "source":
                kwargs[f.name] = "wu_api"
            elif f.name == "observation_time":
                kwargs[f.name] = "2026-04-16T18:00:00"
            elif f.default is not dataclasses.MISSING:
                kwargs[f.name] = f.default
            elif f.default_factory is not dataclasses.MISSING:
                kwargs[f.name] = f.default_factory()

        ctx = ctx_cls(**kwargs)

        # The low_so_far must be a non-None float — if the new context is
        # used in the evaluator, this attribute access replaces the old dict.get
        self.assertIsNotNone(ctx.low_so_far)
        self.assertIsInstance(ctx.low_so_far, float)

        # The evaluator gate: temperature_metric.is_low() AND low_so_far is None
        # → should NOT fire when low_so_far is a float.
        # Simulate the evaluator's guard expression with the new context:
        mock_metric = MagicMock()
        mock_metric.is_low.return_value = True

        # Pre-Phase-3 dict path (old contract) — this is what fires today:
        old_obs = {"high_so_far": 75.0, "current_temp": 70.0}  # no low_so_far key
        old_gate_fires = mock_metric.is_low() and old_obs.get("low_so_far") is None
        self.assertTrue(old_gate_fires, "Old dict path must fire the low-reject gate (test baseline)")

        # Post-Phase-3 context path — gate must NOT fire when context has low_so_far:
        # The evaluator accesses ctx.low_so_far (not dict.get), so None check is type-safe.
        new_gate_fires = mock_metric.is_low() and ctx.low_so_far is None
        self.assertFalse(
            new_gate_fires,
            "With valid low_so_far in Day0ObservationContext, evaluator low-reject gate must not fire",
        )

    def test_evaluator_low_reject_branch_rejection_stage_label(self):
        """R-H: after Phase 3 the low-reject branch (if it fires at all) uses
        rejection_stage='OBSERVATION_UNAVAILABLE_LOW', not 'SIGNAL_QUALITY'.

        Today evaluator.py:804 uses rejection_stage='SIGNAL_QUALITY' for the
        low unavailable path. Phase 3 renames it to OBSERVATION_UNAVAILABLE_LOW
        so callers can distinguish low-specific unavailability from ensemble quality.

        This test fails today because:
        1. Day0ObservationContext doesn't exist.
        2. The string 'OBSERVATION_UNAVAILABLE_LOW' is absent from evaluator.py.
        """
        self._import_context()  # gate: Phase 3 must have landed

        import src.engine.evaluator as ev_mod
        import inspect
        source = inspect.getsource(ev_mod)
        self.assertIn(
            "OBSERVATION_UNAVAILABLE_LOW",
            source,
            "evaluator.py must use rejection_stage='OBSERVATION_UNAVAILABLE_LOW' "
            "for the low_so_far unavailability path after Phase 3",
        )


# ---------------------------------------------------------------------------
# NC-8 — No bare implicit unit assumptions at the provider seam
# ---------------------------------------------------------------------------


class TestObservationContextExplicitUnit(unittest.TestCase):
    """NC-8: Day0ObservationContext.unit must be an explicit required field.

    NC-08 (negative_constraints.yaml): 'No bare implicit unit assumptions in
    semantic code paths.' A provider that silently defaults unit to 'F' or
    omits the field entirely creates an invisible unit assumption at the seam
    between the data layer and the evaluator. The evaluator must be able to
    trust that the unit it reads from the context is the one the provider
    explicitly asserted.

    These tests MUST FAIL today because Day0ObservationContext doesn't exist.
    Once it exists, the unit field must have no default value.
    """

    def _import_context(self):
        try:
            from src.data.observation_client import Day0ObservationContext
            return Day0ObservationContext
        except ImportError:
            self.fail(
                "Phase 3 not yet implemented: Day0ObservationContext not in observation_client"
            )

    def test_observation_context_unit_field_is_required(self):
        """NC-8: Day0ObservationContext.unit has no default — it must be
        explicitly provided by the provider, never inferred.

        A unit field with a default (e.g. default='F') is a bare implicit
        unit assumption at the provider seam — forbidden by NC-08.
        """
        ctx_cls = self._import_context()
        import dataclasses

        fields = {f.name: f for f in dataclasses.fields(ctx_cls)}
        self.assertIn(
            "unit",
            fields,
            "Day0ObservationContext must declare a unit field (NC-08)",
        )
        field = fields["unit"]
        self.assertIs(
            field.default,
            dataclasses.MISSING,
            "Day0ObservationContext.unit must have no default value — "
            "an implicit default is a bare unit assumption forbidden by NC-08",
        )
        self.assertIs(
            field.default_factory,
            dataclasses.MISSING,
            "Day0ObservationContext.unit must have no default_factory — "
            "any factory that infers unit is a bare unit assumption forbidden by NC-08",
        )

    def test_observation_context_unit_must_be_city_settlement_unit(self):
        """NC-8: providers must derive unit from city.settlement_unit, not hardcode it.

        A provider that hardcodes 'F' or 'C' in its return statement is a
        NC-08 violation regardless of correctness for that specific city.
        The unit value must trace back to city.settlement_unit (the authoritative
        source), not an assumed constant.

        This test verifies that the WU provider path reads unit from city config.
        """
        import inspect
        try:
            from src.data import observation_client as obs_mod
        except ImportError:
            self.fail("src.data.observation_client not importable")

        wu_source = inspect.getsource(obs_mod._fetch_wu_observation)

        # After Phase 3, the return must use city.settlement_unit for the unit field,
        # not a hardcoded string. The old plain dict returned "unit": city.settlement_unit
        # which is correct; the new Day0ObservationContext must preserve this.
        self.assertIn(
            "settlement_unit",
            wu_source,
            "WU provider must derive unit from city.settlement_unit (NC-08). "
            "Hardcoding 'F' or 'C' is a bare unit assumption.",
        )


if __name__ == "__main__":
    unittest.main()
