#!/usr/bin/env python3
"""TIGGE Daily Pipeline — runs as cron job.

Downloads T-3 day TIGGE ENS for all settlement-matched cities,
extracts member vectors, and triggers zeus calibration import.

Must run with conda base python:
    /Users/leofitz/miniconda3/bin/python scripts/tigge_daily_pipeline.py

Cron:
    0 3 * * * /Users/leofitz/miniconda3/bin/python \\
      "/Users/leofitz/.openclaw/workspace-venus/51 source data/scripts/tigge_daily_pipeline.py" \\
      >> /Users/leofitz/.openclaw/logs/tigge-daily.log 2>&1
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ZEUS_ROOT = Path("/Users/leofitz/.openclaw/workspace-venus/zeus")
ZEUS_PYTHON = str(ZEUS_ROOT / ".venv" / "bin" / "python")
CONDA_PYTHON = "/Users/leofitz/miniconda3/bin/python"


def main() -> int:
    now = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"TIGGE Daily Pipeline — {now.isoformat()}")
    print(f"{'='*60}")

    # Step 1: Download recent dates plus full step24 gaps when settlement-backed work is empty.
    print("\n1. Running TIGGE settlement/step24 backfill (max 300 dates)...")
    backfill_script = SCRIPT_DIR / "tigge_settlement_backfill.py"
    if backfill_script.exists():
        r = subprocess.run(
            [CONDA_PYTHON, str(backfill_script), "--plan-mode", "auto", "--max-dates", "300", "--batch-mode", "--dates-per-request", "10", "--parallel", "3", "--delay-between-cities", "0"],
            capture_output=True, text=True, timeout=7200,  # 2h max
        )
        print(r.stdout[-500:] if r.stdout else "(no output)")
        if r.returncode != 0:
            print(f"  ⚠ Backfill exited with code {r.returncode}")
            if r.stderr:
                print(f"  stderr: {r.stderr[-300:]}")
    else:
        print(f"  ⚠ Script not found: {backfill_script}")

    # Step 2: Run direct calibration ETL in zeus
    print("\n2. Running TIGGE direct calibration ETL...")
    etl_script = ZEUS_ROOT / "scripts" / "etl_tigge_direct_calibration.py"
    if etl_script.exists():
        r = subprocess.run(
            [ZEUS_PYTHON, str(etl_script)],
            capture_output=True, text=True, timeout=300,
            cwd=str(ZEUS_ROOT),
        )
        print(r.stdout[-500:] if r.stdout else "(no output)")
        if r.returncode != 0:
            print(f"  ⚠ ETL exited with code {r.returncode}")
    else:
        print(f"  ⚠ Script not found: {etl_script}")

    # Step 3: Run standard TIGGE ENS ETL (market-bin based)
    print("\n3. Running standard TIGGE ENS ETL...")
    ens_script = ZEUS_ROOT / "scripts" / "etl_tigge_ens.py"
    if ens_script.exists():
        r = subprocess.run(
            [ZEUS_PYTHON, str(ens_script)],
            capture_output=True, text=True, timeout=300,
            cwd=str(ZEUS_ROOT),
        )
        output = r.stdout[-300:] if r.stdout else "(no output)"
        print(output)
    else:
        print(f"  ⚠ Script not found: {ens_script}")

    print(f"\n{'='*60}")
    print(f"Pipeline complete: {datetime.now(timezone.utc).isoformat()}")
    print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
