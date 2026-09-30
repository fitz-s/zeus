# Created: 2026-09-29
# Last reused/audited: 2026-09-29
# Authority: REQ-20260929-223929-bf51a2; real CLI process reuse, no production files.
"""Transport tests use rejected missing-input requests, never claim a posterior."""
from pathlib import Path
import subprocess
import sys
import time
import pytest
from src.runtime.warm_materializer import ResidentMaterializer

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/materialize_replacement_forecast_live.py"

def test_resident_cli_reuses_interpreter_without_fabricating_success(tmp_path):
    worker=ResidentMaterializer();args=[sys.executable,str(SCRIPT),"--commit","--input-json",str(tmp_path/"absent.json")]
    try:
        first=worker.run(args,timeout=20);pid=worker._process.pid
        started=time.monotonic_ns();second=worker.run(args,timeout=20)
        assert worker._process.pid==pid
        assert first.returncode!=0 and second.returncode!=0
        print("WARM_PROTOCOL_MS",(time.monotonic_ns()-started)/1e6)
    finally:worker.close()

def test_unrelated_cli_and_options_are_rejected_before_start(tmp_path):
    worker=ResidentMaterializer()
    with pytest.raises(ValueError):worker.run([sys.executable,"elsewhere.py","--commit"],timeout=1)
    with pytest.raises(ValueError):worker.run([sys.executable,str(SCRIPT),"--commit","--input-json",str(tmp_path/"none"),"--unsafe"],timeout=1)
    assert worker._process is None

def test_dead_worker_restarts_and_deadline_is_not_success(tmp_path):
    worker=ResidentMaterializer();args=[sys.executable,str(SCRIPT),"--commit","--input-json",str(tmp_path/"absent.json")]
    try:
        with pytest.raises(subprocess.TimeoutExpired):worker.run(args,timeout=0.001)
        result=worker.run(args,timeout=20)
        assert result.returncode!=0
    finally:worker.close()
