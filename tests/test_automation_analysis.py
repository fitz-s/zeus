import sqlite3

import scripts.automation_analysis as automation_analysis


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _create_bias_tables(conn: sqlite3.Connection, *, model_bias_column: bool = False) -> None:
    conn.execute("CREATE TABLE model_bias (source TEXT, n_samples INTEGER)")
    conn.execute("CREATE TABLE calibration_pairs (bias_corrected INTEGER)")
    if model_bias_column:
        conn.execute(
            "CREATE TABLE platt_models (is_active INTEGER, trained_with_bias_correction INTEGER)"
        )
    else:
        conn.execute("CREATE TABLE platt_models (is_active INTEGER)")
    conn.execute("INSERT INTO model_bias VALUES ('ecmwf', 25)")
    conn.execute("INSERT INTO calibration_pairs VALUES (1)")


def test_bias_readiness_marks_missing_model_bias_column_as_not_instrumented():
    conn = _conn()
    _create_bias_tables(conn, model_bias_column=False)

    report = automation_analysis.analyze_bias_readiness(conn)

    assert "platt_models trained w/ bias: **unknown (schema not instrumented)**" in report
    assert "trained_with_bias_correction" in report
    assert "未被持久化" in report
    assert "platt_models trained w/ bias: **0**" not in report


def test_bias_readiness_raises_when_platt_models_table_missing():
    conn = _conn()
    conn.execute("CREATE TABLE model_bias (source TEXT, n_samples INTEGER)")
    conn.execute("CREATE TABLE calibration_pairs (bias_corrected INTEGER)")
    conn.execute("INSERT INTO model_bias VALUES ('ecmwf', 25)")

    try:
        automation_analysis.analyze_bias_readiness(conn)
    except sqlite3.OperationalError as exc:
        assert "missing table: platt_models" in str(exc)
    else:
        raise AssertionError("missing platt_models table must not be reported as not instrumented")


def test_bias_readiness_reports_calibration_pair_query_failure():
    conn = _conn()
    conn.execute("CREATE TABLE model_bias (source TEXT, n_samples INTEGER)")
    conn.execute("CREATE TABLE calibration_pairs (other_column INTEGER)")
    conn.execute("CREATE TABLE platt_models (is_active INTEGER)")
    conn.execute("INSERT INTO model_bias VALUES ('ecmwf', 25)")

    report = automation_analysis.analyze_bias_readiness(conn)

    assert "calibration_pairs bias_corrected=1: **unknown**" in report
    assert "query_failed:" in report
    assert "无法判断 bias readiness" in report


def test_bias_readiness_counts_model_bias_column_when_present():
    conn = _conn()
    _create_bias_tables(conn, model_bias_column=True)
    conn.executemany(
        "INSERT INTO platt_models VALUES (?, ?)",
        [(1, 1), (1, 1), (1, 0), (0, 1)],
    )

    report = automation_analysis.analyze_bias_readiness(conn)

    assert "platt_models trained w/ bias: **2**" in report
    assert "unknown (schema not instrumented)" not in report


def test_run_analysis_surfaces_bias_readiness_schema_gap(monkeypatch):
    conn = _conn()
    _create_bias_tables(conn, model_bias_column=False)

    monkeypatch.setattr(automation_analysis, "get_conn", lambda: conn)
    monkeypatch.setattr(automation_analysis, "analyze_alpha_overrides", lambda conn: "")
    monkeypatch.setattr(automation_analysis, "analyze_model_bias", lambda conn: "")
    monkeypatch.setattr(automation_analysis, "analyze_calibration_pairs", lambda conn: "")
    monkeypatch.setattr(automation_analysis, "analyze_pair_accrual_rate", lambda conn: "")
    monkeypatch.setattr(automation_analysis, "analyze_platt_models", lambda conn: "")
    monkeypatch.setattr(automation_analysis, "analyze_etl_freshness", lambda conn: "")

    report = automation_analysis.run_analysis()

    assert "Bias Correction Readiness" in report
    assert "unknown (schema not instrumented)" in report
