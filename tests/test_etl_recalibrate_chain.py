from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from src.state.db import get_connection, init_schema


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
        "get_connection_name": "get_shared_connection",
    }


def test_etl_tigge_calibration_preserves_all_steps_and_lead_hours(tmp_path, monkeypatch):
    from scripts import etl_tigge_calibration as etl
    import src.calibration.manager as calibration_manager_module
    import src.state.db as db_module

    shared_db = tmp_path / "zeus-shared.db"
    tigge_root = tmp_path / "tigge"
    date_dir = tigge_root / "nyc" / "20260101"
    date_dir.mkdir(parents=True)
    _seed_tigge_members(date_dir / "members_step_024.json", base_value=40.0)
    _seed_tigge_members(date_dir / "members_step_048.json", base_value=42.0)

    conn = get_connection(shared_db)
    init_schema(conn)
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
