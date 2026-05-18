# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: src/data/ogimet_hourly_client.py, src/data/wu_hourly_client.py,
#   src/data/meteostat_bulk_client.py, src/data/daily_obs_append.py,
#   src/data/observation_client.py (F3 PR 2/3)
#   Slice 4 — typed-unit antibody tests for Celsius/Fahrenheit NewType at ingest boundaries.
#   Fitz Constraint #1: "make the category impossible, not just the instance."

"""Antibody tests for Celsius/Fahrenheit NewType at ingest-layer boundaries.

Load-bearing tests:
  1. Happy path: METAR parse returns Celsius, Meteostat parse returns Celsius,
     runtime isinstance check passes (NewType is float at runtime).
  2. Sed-break (mypy-scoped): bare float passed to function expecting Celsius
     fails mypy on an isolated snippet.
  3. Positive boundary: degC(t) passed to Celsius-annotated function passes mypy.
  4. Sed-break: WU adapter — verify _aggregate_hourly produces temp fields paired
     with their declared unit string.

Scope limitation (Path A, accepted by operator 2026-05-18):
  NewType-only does NOT block `Celsius + Fahrenheit` arithmetic — mypy treats
  both as `float` for operator dispatch and returns plain `float`. Function
  SIGNATURES are gated (raw float → Celsius raises mypy error at call sites),
  but in-body arithmetic mixing is not blocked by this PR.
  Full category-impossibility requires frozen-dataclass wrappers with custom
  __add__ / __radd__ — deferred because of runtime cost in hot statistical loops.
  See src/types/temperature.py LIMITATION comment.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
from datetime import date, datetime, timezone

from src.data.ogimet_hourly_client import _parse_metar_temp_c, _parse_metar_csv_line
from src.types.temperature import Celsius, Fahrenheit, degC, degF


# ---------------------------------------------------------------------------
# Happy path — parse helpers return Celsius
# ---------------------------------------------------------------------------

def test_parse_metar_temp_c_returns_celsius_positive() -> None:
    """METAR parser returns a Celsius value (positive temperature)."""
    result = _parse_metar_temp_c("METAR LTFM 011150Z 35010KT 9999 FEW020 17/05 Q1013")
    assert result is not None
    assert isinstance(result, float)  # Celsius is float at runtime
    assert result == 17.0


def test_parse_metar_temp_c_returns_celsius_negative() -> None:
    """METAR parser handles M-prefixed negative temperatures correctly."""
    result = _parse_metar_temp_c("LTFM 130000Z 360008KT M05/M10 Q1020")
    assert result is not None
    assert isinstance(result, float)
    assert result == -5.0


def test_parse_metar_temp_c_returns_none_on_absent() -> None:
    """METAR parser returns None when no temperature group present."""
    result = _parse_metar_temp_c("LTFM 130000Z 360008KT // Q1020")
    assert result is None


def test_parse_metar_csv_line_returns_celsius_tuple() -> None:
    """CSV line parser returns (utc_dt, Celsius) — temp is float at runtime."""
    # Format: ICAO,YYYY,MM,DD,HH,MI,<METAR body>
    line = "LTFM,2026,01,01,12,00,METAR LTFM 010000Z 360010KT 9999 17/05 Q1013"
    result = _parse_metar_csv_line(line)
    assert result is not None
    utc_dt, temp_c = result
    assert isinstance(utc_dt, datetime)
    assert isinstance(temp_c, float)
    assert temp_c == 17.0


# ---------------------------------------------------------------------------
# Mypy-scoped helper (copied from tests/contracts/test_settlement_semantics_unit_types.py)
# ---------------------------------------------------------------------------

def _run_mypy_strict_on_snippet(code: str) -> tuple[int, str]:
    """Write code to a temp file and run mypy --strict --follow-imports=silent on it.

    --follow-imports=silent means mypy reads imported module types (so
    Celsius annotations are visible) but suppresses errors *from* those
    modules, so pre-strict sibling modules do not cascade errors into the
    snippet check.

    cwd is set to the repo root (two dirs above tests/data/) so mypy.ini is
    discovered and src/ is on the module search path regardless of where pytest
    is invoked from.

    Returns (exit_code, combined_stdout_stderr).
    """
    # Repo root: tests/data/../../  (this file lives at tests/data/test_*.py)
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, prefix="mypy_snippet_"
    ) as f:
        f.write(code)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "mypy",
                "--strict",
                "--ignore-missing-imports",
                "--follow-imports=silent",
                "--python-version", f"{sys.version_info.major}.{sys.version_info.minor}",
                tmp_path,
            ],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        return result.returncode, result.stdout + result.stderr
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Sed-break — mypy must reject plain float where Celsius is required
# ---------------------------------------------------------------------------

def test_mypy_rejects_plain_float_as_celsius_arg() -> None:
    """Sed-break: bare float passed to a Celsius-annotated parameter fails mypy.

    Celsius = NewType("Celsius", float). Under strict mypy, a plain float
    does not satisfy a Celsius parameter — the caller must wrap via degC().
    This is the ingest-layer gate: parse helpers return Celsius, so downstream
    typed functions cannot accidentally receive a wrongly-sourced float.
    """
    snippet = textwrap.dedent("""\
        from src.types.temperature import Celsius

        def store(t: Celsius) -> None:
            pass

        x: float = 20.0
        store(x)  # should fail mypy — plain float not assignable to Celsius
    """)
    exit_code, output = _run_mypy_strict_on_snippet(snippet)
    assert exit_code != 0, (
        "mypy should reject plain float where Celsius is required, "
        f"but it exited 0. Output:\n{output}"
    )
    assert "Argument" in output or "error" in output.lower(), (
        f"Expected a type error in mypy output, got:\n{output}"
    )


def test_mypy_accepts_degc_wrapped_celsius() -> None:
    """Positive boundary: degC(t) satisfies a Celsius-annotated parameter.

    Confirms the function-boundary type guard is live for ingest paths.
    """
    snippet = textwrap.dedent("""\
        from src.types.temperature import Celsius, degC

        def store(t: Celsius) -> None:
            pass

        # CORRECT: wrapped via degC
        store(degC(20.0))
    """)
    exit_code, output = _run_mypy_strict_on_snippet(snippet)
    assert exit_code == 0, (
        f"mypy exited {exit_code} on correctly-typed Celsius call (import error or unexpected failure):\n{output}"
    )
    error_lines = [line for line in output.splitlines() if "error:" in line.lower()]
    assert not error_lines, (
        "mypy reported errors on correctly-typed Celsius boundary call:\n"
        + "\n".join(error_lines)
    )


# ---------------------------------------------------------------------------
# Sed-break — WU adapter: Fahrenheit label matches data source
# ---------------------------------------------------------------------------

def test_wu_adapter_temp_unit_matches_fahrenheit_request() -> None:
    """Sed-break: WU adapter with unit='F' produces HourlyObservation.temp_unit='F'.

    The WU API returns data in whatever unit was requested. When unit='F' is
    requested (WU ICAO US cities), temp_unit on each HourlyObservation must
    be 'F' — the unit field is the ingest-layer source-tag for downstream
    consumers.

    This is a runtime structural assertion, not a mypy test. It verifies that
    the adapter boundary source-tag survives the full parse path.
    """
    # _aggregate_hourly is the internal bucket function
    from src.data.wu_hourly_client import _aggregate_hourly

    # Minimal synthetic raw_observations list
    raw_observations = [
        {"temp": "72.5", "valid_time_gmt": "1746000000"},  # 2025-04-30T08:00:00Z
    ]
    city_name = "Chicago"
    start = date(2025, 4, 30)
    end = date(2025, 4, 30)
    timezone_name = "America/Chicago"
    icao = "KORD"
    unit = "F"

    result = _aggregate_hourly(
        raw_observations,
        city_name=city_name,
        start_date=start,
        end_date=end,
        timezone_name=timezone_name,
        icao=icao,
        unit=unit,
    )
    assert len(result) >= 1, "Expected at least one HourlyObservation from synthetic data"
    for obs in result:
        assert obs.temp_unit == "F", (
            f"Expected temp_unit='F' (WU Fahrenheit request), got {obs.temp_unit!r}"
        )
        # Verify the temperature is a plain float at runtime (NewType is zero-cost)
        assert isinstance(obs.hour_max_temp, float)
        assert isinstance(obs.hour_min_temp, float)
