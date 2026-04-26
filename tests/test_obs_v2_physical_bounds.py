# Lifecycle: created=2026-04-26; last_reviewed=2026-04-26; last_reused=never
# Purpose: B4 antibody — pin physical-bounds rejection on obs_v2 temp values
#          (`temp_current`, `running_max`, `running_min`). Catches the Warsaw
#          88°C class of poison-data failure (workbook N1.8). Bounds enforced
#          at writer level (load-bearing — covers all DBs) and at schema
#          CREATE TABLE level (new DBs only — SQLite ALTER cannot add CHECK).
# Reuse: Covers src/data/observation_instants_v2_writer.py temperature-bounds
#        validation + src/state/schema/v2_schema.py CREATE TABLE CHECK.
#        Production-path integration test (test #8) exercises insert_rows()
#        end-to-end. If a future refactor weakens bounds OR drops the writer
#        check, this suite fires.
# Authority basis: docs/operations/task_2026-04-26_b4_physical_bounds/plan.md
#   §5 antibody design + parent docs/operations/task_2026-04-26_live_readiness_completion/plan.md
#   §5 K1+K3.B4 row + con-nyx G6 review pattern lesson #1
#   (production-path integration test required, not just literal-arg unit test).
"""B4 antibody — obs_v2 physical-bounds rejection.

Bounds:
  Celsius:    -90.0  to  60.0  (inclusive)
  Fahrenheit: -130.0 to 140.0  (inclusive)

Lower covers Vostok station 1983 record (-89.2°C); upper covers Death
Valley extreme (54.4°C verified, 56.7°C disputed) with margin. Kelvin
is rejected upstream by `_ALLOWED_TEMP_UNITS = {"F", "C"}`.

Production-path integration test (test #8) inserts a row through
`insert_rows()` to confirm the constructor's `__post_init__` validation
fires before the SQL INSERT — so a writer that bypassed the dataclass
constructor (e.g., by raw INSERT) would NOT be caught by these tests.
That residual gap is documented in the slice plan §7 (out-of-scope:
retroactive CHECK on legacy DBs not possible via SQLite ALTER).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.observation_instants_v2_writer import (
    InvalidObsV2RowError,
    ObsV2Row,
    insert_rows,
)
from src.state.schema.v2_schema import apply_v2_schema


def _valid_provenance() -> str:
    return json.dumps(
        {
            "tier": "WU_ICAO",
            "station_id": "EPWA",
            "payload_hash": "sha256:" + "c" * 64,
            "source_url": "https://api.weather.com/v1/REDACTED",
            "parser_version": "test_obs_v2_physical_bounds_v1",
        },
        sort_keys=True,
    )


def _row_kwargs(*, temp_unit: str = "C", **overrides) -> dict:
    """Warsaw row baseline; tests override one or more fields."""
    base = dict(
        city="Warsaw",
        target_date="2025-07-01",
        source="wu_icao_history",
        timezone_name="Europe/Warsaw",
        local_timestamp="2025-07-01T12:00:00+02:00",
        utc_timestamp="2025-07-01T10:00:00+00:00",
        utc_offset_minutes=120,
        time_basis="utc_hour_aligned",
        temp_unit=temp_unit,
        imported_at="2026-04-26T00:00:00+00:00",
        authority="VERIFIED",
        data_version="v1.wu-native.pilot",
        provenance_json=_valid_provenance(),
        temp_current=25.0,
        running_max=27.0,
        station_id="EPWA",
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Constants pin (1)
# ---------------------------------------------------------------------------


def test_physical_bounds_constants_typed_and_in_canonical_range():
    """Pin _PHYSICAL_TEMP_BOUNDS_C and _F constants by type + value.

    A future widening (e.g., upper bound moved to 100°C 'because we saw a
    sensor report it') silently re-admits poison data like Warsaw 88°C.
    This test fires on any drift.
    """
    from src.data.observation_instants_v2_writer import (
        _PHYSICAL_TEMP_BOUNDS_C,
        _PHYSICAL_TEMP_BOUNDS_F,
    )

    assert isinstance(_PHYSICAL_TEMP_BOUNDS_C, tuple)
    assert isinstance(_PHYSICAL_TEMP_BOUNDS_F, tuple)
    assert _PHYSICAL_TEMP_BOUNDS_C == (-90.0, 60.0), (
        f"Celsius bounds drift: {_PHYSICAL_TEMP_BOUNDS_C}. Update this pin and "
        "the slice receipt if expansion is deliberate."
    )
    assert _PHYSICAL_TEMP_BOUNDS_F == (-130.0, 140.0), (
        f"Fahrenheit bounds drift: {_PHYSICAL_TEMP_BOUNDS_F}."
    )


# ---------------------------------------------------------------------------
# Constructor rejection (2-3, 5-6)
# ---------------------------------------------------------------------------


def test_obs_v2_row_rejects_out_of_bounds_temp_current_celsius():
    """Warsaw 88°C scenario (workbook N1.8) — constructor MUST reject."""
    with pytest.raises(InvalidObsV2RowError) as exc_info:
        ObsV2Row(**_row_kwargs(temp_current=88.0))
    msg = str(exc_info.value)
    assert "88" in msg or "out of bounds" in msg.lower() or "physical" in msg.lower(), (
        f"Error message must indicate bounds violation: {msg!r}"
    )


def test_obs_v2_row_rejects_out_of_bounds_temp_current_fahrenheit():
    """Same boundary semantic in °F (200°F > 140°F upper)."""
    with pytest.raises(InvalidObsV2RowError):
        ObsV2Row(**_row_kwargs(temp_unit="F", temp_current=200.0))


def test_obs_v2_row_accepts_boundary_values():
    """BETWEEN-style inclusive — exactly -90 and 60 °C accepted."""
    # Lower boundary
    row_low = ObsV2Row(**_row_kwargs(temp_current=-90.0))
    assert row_low.temp_current == -90.0
    # Upper boundary
    row_high = ObsV2Row(**_row_kwargs(temp_current=60.0))
    assert row_high.temp_current == 60.0


def test_obs_v2_row_rejects_just_outside_bounds():
    """Edge — 60.01 and -90.01 °C rejected."""
    with pytest.raises(InvalidObsV2RowError):
        ObsV2Row(**_row_kwargs(temp_current=60.01))
    with pytest.raises(InvalidObsV2RowError):
        ObsV2Row(**_row_kwargs(temp_current=-90.01))


# ---------------------------------------------------------------------------
# Constructor acceptance (4, 7)
# ---------------------------------------------------------------------------


def test_obs_v2_row_accepts_in_bounds_values():
    """Control — typical-value rows accepted in both units."""
    row_c = ObsV2Row(**_row_kwargs(temp_current=25.0, running_max=28.0, running_min=20.0))
    assert row_c.temp_current == 25.0
    row_f = ObsV2Row(**_row_kwargs(temp_unit="F", temp_current=77.0, running_max=82.0, running_min=68.0))
    assert row_f.temp_current == 77.0


def test_obs_v2_row_accepts_null_temp_fields():
    """Nullable contract — None for any/all temp fields passes.

    Per schema (`temp_current REAL`, `running_max REAL`, `running_min REAL`)
    these are nullable. Bounds check must skip None inputs.
    """
    row = ObsV2Row(**_row_kwargs(temp_current=None, running_max=None, running_min=None))
    assert row.temp_current is None
    assert row.running_max is None
    assert row.running_min is None


def test_obs_v2_row_rejects_out_of_bounds_running_max():
    """Bounds apply to running_max + running_min, not just temp_current."""
    with pytest.raises(InvalidObsV2RowError):
        ObsV2Row(**_row_kwargs(temp_current=25.0, running_max=88.0))
    with pytest.raises(InvalidObsV2RowError):
        ObsV2Row(**_row_kwargs(temp_current=25.0, running_min=-95.0))


# ---------------------------------------------------------------------------
# Production-path integration (8) — con-nyx pattern lesson #1
# ---------------------------------------------------------------------------


def test_insert_rows_integration_path_rejects_out_of_bounds():
    """End-to-end: build row + call insert_rows() — rejection fires at construction.

    The dataclass `__post_init__` validates at row-construction time, so
    `insert_rows()` never sees a bad row. This test pins that the validation
    sits BEFORE the SQL boundary, not inside it (which would silently accept
    bad rows on writers that bypass the constructor — those bypasses are
    addressed by the schema CHECK on fresh DBs and documented as
    out-of-scope for legacy DBs).
    """
    conn = sqlite3.connect(":memory:")
    apply_v2_schema(conn)

    # Construction itself raises — insert_rows is never reached.
    with pytest.raises(InvalidObsV2RowError):
        bad_row = ObsV2Row(**_row_kwargs(temp_current=88.0))
        insert_rows(conn, [bad_row])

    # Confirm DB stayed clean.
    count = conn.execute(
        "SELECT COUNT(*) FROM observation_instants_v2"
    ).fetchone()[0]
    assert count == 0


def test_insert_rows_integration_path_accepts_in_bounds():
    """Control — clean row writes successfully via insert_rows()."""
    conn = sqlite3.connect(":memory:")
    apply_v2_schema(conn)

    good_row = ObsV2Row(**_row_kwargs(temp_current=25.0))
    inserted = insert_rows(conn, [good_row])
    assert inserted == 1
    count = conn.execute(
        "SELECT COUNT(*) FROM observation_instants_v2"
    ).fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# Drift catchers (11-12) — con-nyx B4 NICE-TO-HAVE #1 + #2
# ---------------------------------------------------------------------------


def test_schema_check_matches_writer_constants():
    """Schema CHECK literals must agree with writer _PHYSICAL_TEMP_BOUNDS_*.

    Cheap drift catcher: a future bound change has to touch both the writer
    constants AND the schema CHECK clause. Without this test, a writer-side
    bump (-90 → -100) silently leaves the schema at -90 and breaks parity.
    Catches missed-edit drift via simple substring check.
    """
    from src.data.observation_instants_v2_writer import (
        _PHYSICAL_TEMP_BOUNDS_C,
        _PHYSICAL_TEMP_BOUNDS_F,
    )

    schema_path = PROJECT_ROOT / "src" / "state" / "schema" / "v2_schema.py"
    schema_src = schema_path.read_text(encoding="utf-8")

    c_lo, c_hi = _PHYSICAL_TEMP_BOUNDS_C
    f_lo, f_hi = _PHYSICAL_TEMP_BOUNDS_F

    # Bounds are integer-valued in the schema literal (BETWEEN -90 AND 60).
    # Writer constants are float (-90.0); ensure schema string contains the
    # integer form by stripping trailing .0 — fires on drift in either direction.
    assert f"BETWEEN {int(c_lo)} AND {int(c_hi)}" in schema_src, (
        f"Celsius bounds drift between writer ({c_lo}, {c_hi}) and "
        f"v2_schema.py CHECK clause. Update both together."
    )
    assert f"BETWEEN {int(f_lo)} AND {int(f_hi)}" in schema_src, (
        f"Fahrenheit bounds drift between writer ({f_lo}, {f_hi}) and "
        f"v2_schema.py CHECK clause."
    )


def test_schema_check_rejects_raw_bypass_on_new_db():
    """Schema CHECK is the second-line defense if a future writer issues raw INSERT.

    Constructor validation (_validate) is the first line; the schema CHECK
    catches anything that bypasses the dataclass entirely (e.g., migration
    scripts, ad-hoc psql sessions, future writers built on raw cursor calls).
    Pinning so a future schema refactor that drops the CHECK clause fires here.

    Empirical: ran this raw-INSERT bypass against a fresh schema 2026-04-26 and
    confirmed sqlite3 raises IntegrityError naming the CHECK constraint.
    """
    conn = sqlite3.connect(":memory:")
    apply_v2_schema(conn)

    # Use raw INSERT with all required NOT NULL columns satisfied except the
    # one that violates: temp_current=88.0 with temp_unit='C'.
    with pytest.raises(sqlite3.IntegrityError, match=r"(?i)CHECK"):
        conn.execute(
            """
            INSERT INTO observation_instants_v2 (
                city, target_date, source, timezone_name, local_timestamp,
                utc_timestamp, utc_offset_minutes, time_basis, temp_unit,
                imported_at, authority, data_version, provenance_json,
                temp_current
            ) VALUES (
                'Warsaw', '2025-07-01', 'wu_icao_history', 'Europe/Warsaw',
                '2025-07-01T12:00:00+02:00', '2025-07-01T10:00:00+00:00',
                120, 'utc_hour_aligned', 'C',
                '2026-04-26T00:00:00+00:00', 'VERIFIED', 'v1.wu-native.pilot',
                '{"tier":"WU_ICAO"}',
                88.0
            )
            """
        )
