from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from src.state.db import get_connection, init_schema


def _ensure_auth_column(conn) -> None:
    """No-op: init_schema now creates authority column. Retained for call-site compatibility."""
    pass


ROOT = Path(__file__).resolve().parents[1]
WORKTREE_VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
EXPECTED_SUBPROCESS_PYTHON = (
    WORKTREE_VENV_PYTHON if WORKTREE_VENV_PYTHON.exists() else Path(sys.executable)
)

REPRESENTATIVE_SHARED_SCRIPTS = (
    "scripts/etl_observation_instants.py",
    "scripts/etl_diurnal_curves.py",
    "scripts/etl_temp_persistence.py",
    "scripts/etl_tigge_direct_calibration.py",
    "scripts/refit_platt.py",
    "scripts/etl_tigge_ens.py",
    "scripts/etl_tigge_calibration.py",
    "scripts/run_replay.py",
)


def _seed_tigge_members(path: Path, *, base_value: float) -> None:
    payload = {
        "generated_at": "2026-01-01T08:00:00Z",
        "members": [{"value_native_unit": base_value + (i * 0.01)} for i in range(51)],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_etl_recalibrate_launches_expected_scripts_via_repo_venv(monkeypatch):
    import src.main as main_module

    calls: list[tuple[list[str], bool, bool, int]] = []

    class _Result:
        def __init__(self):
            self.returncode = 0
            self.stderr = ""
            self.stdout = ""

    def _run(cmd, *, capture_output, text, timeout):
        calls.append((list(cmd), capture_output, text, timeout))
        return _Result()

    monkeypatch.setattr(subprocess, "run", _run)

    main_module._etl_recalibrate()

    assert [Path(call[0][1]).name for call in calls] == [
        "migrate_rainstorm_full.py",
        "etl_observation_instants.py",
        "etl_diurnal_curves.py",
        "etl_temp_persistence.py",
        "etl_hourly_observations.py",
        "etl_tigge_direct_calibration.py",
        "refit_platt.py",
        "run_replay.py",
    ]
    for cmd, capture_output, text, timeout in calls[:7]:
        assert cmd[0] == str(EXPECTED_SUBPROCESS_PYTHON)
        assert Path(cmd[1]).is_absolute()
        assert capture_output is True
        assert text is True
        assert timeout == 300

    replay_cmd, capture_output, text, timeout = calls[-1]
    assert replay_cmd == [
        str(EXPECTED_SUBPROCESS_PYTHON),
        str(ROOT / "scripts" / "run_replay.py"),
        "--mode",
        "audit",
        "--start",
        "2025-01-01",
        "--end",
        "2099-12-31",
    ]
    assert capture_output is True
    assert text is True
    assert timeout == 600


@pytest.mark.parametrize("script_rel", REPRESENTATIVE_SHARED_SCRIPTS)
def test_representative_shared_scripts_import_from_outside_repo_cwd(script_rel, tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text(
        textwrap.dedent(
            """
            import importlib.util
            import json
            import pathlib
            import sys

            script = pathlib.Path(sys.argv[1]).resolve()
            spec = importlib.util.spec_from_file_location("probe_mod", script)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            print(json.dumps({
                "project_root": str(getattr(module, "PROJECT_ROOT", "")),
                "get_connection_name": getattr(getattr(module, "get_connection", None), "__name__", None),
            }))
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(EXPECTED_SUBPROCESS_PYTHON), str(probe), str(ROOT / script_rel)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload == {
        "project_root": str(ROOT),
        "get_connection_name": "get_world_connection",
    }


def test_etl_tigge_calibration_preserves_all_steps_and_lead_hours(tmp_path, monkeypatch):
    from scripts import etl_tigge_calibration as etl
    import src.calibration.manager as calibration_manager_module
    import src.state.db as db_module

    shared_db = tmp_path / "zeus-world.db"
    tigge_root = tmp_path / "tigge"
    date_dir = tigge_root / "nyc" / "20260101"
    date_dir.mkdir(parents=True)
    _seed_tigge_members(date_dir / "members_step_024.json", base_value=40.0)
    _seed_tigge_members(date_dir / "members_step_048.json", base_value=42.0)

    conn = get_connection(shared_db)
    init_schema(conn)
    _ensure_auth_column(conn)
    conn.execute(
        "INSERT INTO settlements (city, target_date, winning_bin) VALUES (?, ?, ?)",
        ("NYC", "2026-01-02", "39-40°F"),
    )
    conn.execute(
        "INSERT INTO settlements (city, target_date, winning_bin) VALUES (?, ?, ?)",
        ("NYC", "2026-01-03", "41-42°F"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(etl, "TIGGE_ROOT", tigge_root)
    monkeypatch.setattr(etl, "get_connection", lambda: db_module.get_connection(shared_db))
    monkeypatch.setattr(calibration_manager_module, "_fit_from_pairs", lambda conn, cluster, season: None)

    result = etl.run_etl()

    assert result["vectors_processed"] == 2
    assert result["settlements_matched"] == 2
    assert result["snapshots_after"] - result["snapshots_before"] == 2
    assert result["new_pairs"] == 22

    conn = get_connection(shared_db)
    rows = conn.execute(
        """
        SELECT target_date, lead_hours, data_version
        FROM ensemble_snapshots
        ORDER BY target_date, data_version
        """
    ).fetchall()
    pair_count = conn.execute("SELECT COUNT(*) FROM calibration_pairs").fetchone()[0]
    conn.close()

    assert [dict(row) for row in rows] == [
        {
            "target_date": "2026-01-02",
            "lead_hours": 24.0,
            "data_version": "tigge_cal_v3_step024",
        },
        {
            "target_date": "2026-01-03",
            "lead_hours": 48.0,
            "data_version": "tigge_cal_v3_step048",
        },
    ]
    assert pair_count == 22


def test_etl_tigge_calibration_uses_settlement_value_when_winning_bin_missing(tmp_path, monkeypatch):
    from scripts import etl_tigge_calibration as etl
    import src.calibration.manager as calibration_manager_module
    import src.state.db as db_module

    shared_db = tmp_path / "zeus-world.db"
    tigge_root = tmp_path / "tigge"
    date_dir = tigge_root / "nyc" / "20260101"
    date_dir.mkdir(parents=True)
    _seed_tigge_members(date_dir / "members_step_024.json", base_value=40.0)

    conn = get_connection(shared_db)
    init_schema(conn)
    _ensure_auth_column(conn)
    conn.execute(
        "INSERT INTO settlements (city, target_date, settlement_value) VALUES (?, ?, ?)",
        ("NYC", "2026-01-02", 40.0),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(etl, "TIGGE_ROOT", tigge_root)
    monkeypatch.setattr(etl, "get_connection", lambda: db_module.get_connection(shared_db))
    monkeypatch.setattr(calibration_manager_module, "_fit_from_pairs", lambda conn, cluster, season: None)

    result = etl.run_etl()

    conn = get_connection(shared_db)
    pair_count = conn.execute("SELECT COUNT(*) FROM calibration_pairs").fetchone()[0]
    positive_count = conn.execute("SELECT COUNT(*) FROM calibration_pairs WHERE outcome = 1").fetchone()[0]
    conn.close()

    assert result["vectors_processed"] == 1
    assert result["settlements_matched"] == 1
    assert result["snapshots_after"] - result["snapshots_before"] == 1
    assert pair_count == 11
    assert positive_count == 1


def test_tigge_scripts_expose_configured_city_coverage_gap():
    from scripts import etl_tigge_calibration as tigge_cal
    from scripts import etl_tigge_direct_calibration as direct_cal
    from scripts import etl_tigge_ens as tigge_ens
    from src.config import cities_by_name

    expected_gap = sorted(set(cities_by_name) - set(direct_cal.CITY_MAP.values()))

    assert tigge_ens._unsupported_configured_cities() == expected_gap
    assert direct_cal._unsupported_configured_cities() == expected_gap
    assert sorted(set(cities_by_name) - set(tigge_cal.CITY_MAP.values())) == expected_gap


def test_backfill_observations_from_settlements_uses_namespaced_settlement_source(tmp_path, monkeypatch):
    from scripts import backfill_observations_from_settlements as backfill
    import src.state.db as db_module

    shared_db = tmp_path / "zeus-world.db"
    conn = get_connection(shared_db)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, settlement_value, settlement_source)
        VALUES (?, ?, ?, ?)
        """,
        ("NYC", "2026-01-01", 42.0, "wu_daily_observed"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(backfill, "get_world_connection", lambda: db_module.get_connection(shared_db))

    dry = backfill.backfill_observations_from_settlements(dry_run=True)
    result = backfill.backfill_observations_from_settlements()

    conn = get_connection(shared_db)
    row = conn.execute("SELECT * FROM observations").fetchone()
    conn.close()

    assert dry["candidate_rows"] == 1
    assert dry["inserted"] == 0
    assert result["inserted"] == 1
    assert row["city"] == "NYC"
    assert row["target_date"] == "2026-01-01"
    assert row["source"] == "settlement_source:wu_daily_observed"
    assert row["high_temp"] == 42.0
    assert row["unit"] == "F"


def test_etl_forecast_skill_from_forecasts_materializes_bias_for_local_forecasts(tmp_path, monkeypatch):
    from scripts import etl_forecast_skill_from_forecasts as etl
    import src.state.db as db_module

    shared_db = tmp_path / "zeus-world.db"
    conn = get_connection(shared_db)
    init_schema(conn)
    for day in range(1, 7):
        target_date = f"2026-01-{day:02d}"
        conn.execute(
            "INSERT INTO settlements (city, target_date, settlement_value) VALUES (?, ?, ?)",
            ("NYC", target_date, 40.0),
        )
        conn.execute(
            """
            INSERT INTO forecasts
            (city, target_date, source, forecast_basis_date, lead_days, forecast_high, temp_unit)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("NYC", target_date, "ecmwf_previous_runs", "2025-12-31", 1, 42.0, "F"),
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(etl, "get_world_connection", lambda: db_module.get_connection(shared_db))

    dry = etl.run_etl(dry_run=True)
    result = etl.run_etl()

    conn = get_connection(shared_db)
    skill_count = conn.execute("SELECT COUNT(*) FROM forecast_skill").fetchone()[0]
    bias = conn.execute("SELECT * FROM model_bias").fetchone()
    conn.close()

    assert dry["candidate_rows"] == 6
    assert dry["forecast_skill_added"] == 0
    assert result["forecast_skill_added"] == 6
    assert skill_count == 6
    assert bias["city"] == "NYC"
    assert bias["source"] == "ecmwf"
    assert bias["n_samples"] == 6
    assert bias["bias"] == 2.0


def _tigge_member(member: int, *, forecast_type: str, value: float) -> dict:
    return {
        "member": member,
        "forecast_type": forecast_type,
        "short_name": "2t",
        "data_date": 20260101,
        "data_time": 0,
        "step_range": "24",
        "value_native_unit": value,
    }


def test_tigge_etl_member_normalization_deduplicates_identical_grib_messages():
    from scripts import etl_tigge_calibration as tigge_cal
    from scripts import etl_tigge_direct_calibration as direct_cal
    from scripts import etl_tigge_ens as tigge_ens

    members = [_tigge_member(0, forecast_type="cf", value=40.0)]
    members.extend(_tigge_member(i, forecast_type="pf", value=40.0 + i * 0.1) for i in range(1, 51))
    members.append(dict(members[-1]))

    for module in (direct_cal, tigge_ens, tigge_cal):
        normalized = module._normalize_tigge_members(members)
        assert len(members) == 52
        assert len(normalized) == 51
        assert normalized[0]["member"] == 0
        assert normalized[-1]["member"] == 50


def test_tigge_etl_member_normalization_rejects_conflicting_duplicates():
    from scripts import etl_tigge_calibration as tigge_cal
    from scripts import etl_tigge_direct_calibration as direct_cal
    from scripts import etl_tigge_ens as tigge_ens

    duplicate_a = _tigge_member(1, forecast_type="pf", value=41.0)
    duplicate_b = dict(duplicate_a)
    duplicate_b["value_native_unit"] = 42.0

    for module in (direct_cal, tigge_ens, tigge_cal):
        with pytest.raises(ValueError, match="Conflicting duplicate TIGGE member"):
            module._normalize_tigge_members([duplicate_a, duplicate_b])


def test_tigge_etl_member_normalization_rejects_oversized_legacy_records():
    from scripts import etl_tigge_calibration as tigge_cal
    from scripts import etl_tigge_direct_calibration as direct_cal
    from scripts import etl_tigge_ens as tigge_ens

    legacy_members = [{"value_native_unit": 40.0 + i * 0.1} for i in range(52)]

    for module in (direct_cal, tigge_ens, tigge_cal):
        with pytest.raises(ValueError, match="without identity fields"):
            module._normalize_tigge_members(legacy_members)
