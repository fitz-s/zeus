# Created: 2026-09-29
# Last reused/audited: 2026-10-05
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

def test_worker_stage_trace_reaches_the_parent_log_stream_not_the_reply(tmp_path):
    import os
    log=tmp_path/"daemon.log";fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_APPEND)
    probe=tmp_path/"probe.py"
    # The real worker hook, in a child holding only the inherited descriptor.
    probe.write_text("import logging,sys\nsys.path.insert(0,sys.argv[1])\n"
        "from src.runtime.warm_materializer import attach_trace_log\nattach_trace_log()\n"
        "logging.getLogger('zeus.observation_reaction').info('OBSERVATION_REACTION_TRACE {\"stage\": \"POSTERIOR_READY\"}')\n"
        "logging.getLogger('zeus.observation_reaction').handlers[0].stream.close()\n"
        "logging.getLogger('zeus.observation_reaction').info('after close')\nprint('reply-only')\n")
    import os as _os
    from src.runtime.warm_materializer import TRACE_FD_ENV
    try:
        done=subprocess.run([sys.executable,str(probe),str(SCRIPT.parents[1])],capture_output=True,text=True,
            pass_fds=(fd,),env={**_os.environ,TRACE_FD_ENV:str(fd)},timeout=30)
    finally:os.close(fd)
    assert done.returncode==0,done.stderr
    assert done.stdout=="reply-only\n" and "OBSERVATION_REACTION_TRACE" not in done.stderr
    assert 'OBSERVATION_REACTION_TRACE {"stage": "POSTERIOR_READY"}' in log.read_text()

def test_resident_worker_inherits_the_trace_descriptor(tmp_path):
    import os, shutil
    if shutil.which("lsof") is None:pytest.skip("lsof unavailable")
    log=tmp_path/"daemon.log";fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_APPEND)
    worker=ResidentMaterializer(trace_fd=fd);args=[sys.executable,str(SCRIPT),"--commit","--input-json",str(tmp_path/"absent.json")]
    try:
        assert worker.run(args,timeout=20).returncode!=0
        held=subprocess.run(["lsof","-a","-p",str(worker._process.pid),"-Fn"],capture_output=True,text=True).stdout
        assert f"n{log.resolve()}" in held.splitlines()
        assert worker.run(args,timeout=20).returncode!=0  # Same interpreter, trace stream intact.
    finally:
        worker.close();os.close(fd)
