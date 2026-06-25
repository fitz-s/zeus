import sqlite3

from src.data.replacement_forecast_materializer import (
    _ensure_observation_hourly_extrema_compatibility as materializer_ensure_extrema,
)
from src.state.schema.v2_schema import (
    _ensure_observation_hourly_extrema_compatibility as schema_ensure_extrema,
)


_OBSERVATION_INSTANTS_DDL = """
CREATE TABLE observation_instants (
    id INTEGER PRIMARY KEY,
    running_max REAL,
    running_min REAL
)
"""

_VIEW_DDL = """
CREATE VIEW observation_hourly_extrema AS
            SELECT
                o.*,
                o.running_max AS hour_bucket_max,
                o.running_min AS hour_bucket_min
            FROM observation_instants o
"""


def _conn_with_current_extrema_view() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(_OBSERVATION_INSTANTS_DDL)
    conn.execute(_VIEW_DDL)
    return conn


def _assert_no_schema_write(fn) -> None:
    conn = _conn_with_current_extrema_view()
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    fn(conn)

    schema_writes = [
        stmt
        for stmt in statements
        if stmt.lstrip().upper().startswith(("ALTER ", "CREATE ", "DROP "))
    ]
    assert schema_writes == []


def test_v2_schema_extrema_helper_noops_when_view_current() -> None:
    _assert_no_schema_write(schema_ensure_extrema)


def test_materializer_extrema_helper_noops_when_view_current() -> None:
    _assert_no_schema_write(materializer_ensure_extrema)
