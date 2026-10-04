# Created: 2026-10-04
# Last reused/audited: 2026-10-04
# Authority basis: lease-v1 round-5 consult; copied unchanged from
#   artifacts/merge_safety_lease_v1_round5_review/test_remaining_io.py. Run from the checkout root.
"""Scoped remaining-I/O audit; isolated temporary files only."""
from pathlib import Path
import json,os,subprocess,sys
from types import SimpleNamespace
import pytest
import src.data.replacement_forecast_live_materialization_queue as q
from tests.adversarial.test_execution_lease_adversaries import _request
ROOT=Path.cwd()


def test_builder_output_json_does_not_modify_published_inode(tmp_path,monkeypatch):
    import scripts.build_replacement_forecast_materialization_request as builder
    import scripts.materialize_replacement_forecast_live as worker
    requests=tmp_path/'requests';requests.mkdir()
    a=requests/'first.json';q._write_request(a,_request())
    b=requests/'other.json';os.link(a,b)
    batch=q._new_claim_batch(tmp_path/'inflight',[a])
    before=(batch/a.name).read_bytes()
    replacement=_request(baseline_source_run_id='repaired-run')
    monkeypatch.setattr(builder,'_load_json',lambda path:{})
    monkeypatch.setattr(builder,'build_replacement_forecast_materialization_request',lambda *a,**k:SimpleNamespace(ok=True,request=replacement))
    try:
        result=builder.main(['--input-json',str(tmp_path/'seed.json'),'--output-json',str(b)])
        after=(batch/a.name).read_bytes();guard_rejects=False
        try:worker._require_claimed_bytes(batch/a.name,after)
        except worker.ClaimedBytesMismatch:guard_rejects=True
        print('PROBE',json.dumps({'probe':'output_json_inplace','exit':result,'claimed_bytes_changed':before!=after,'worker_hash_guard_rejects':guard_rejects}))
        assert result==0 and before==after, '--output-json still truncates an existing published request inode'
    finally:q._release_claim_batch(batch)


def test_named_dependency_fifo_does_not_wait_for_writer(tmp_path):
    fifo=tmp_path/'payload.json';os.mkfifo(fifo)
    payload=_request(openmeteo_payload_json=str(fifo))
    code=f'''from pathlib import Path
import sys,json
sys.path.insert(0,{str(ROOT)!r})
import tests.conftest
import src.data.replacement_forecast_live_materialization_queue as q
print('ENTER',flush=True)
r=q._materialization_dependency_record({payload!r},request_dir=Path({str(tmp_path)!r}))
print('RETURNED',r is None,flush=True)
'''
    child=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
    try:
        assert child.stdout.readline()==b'ENTER\n'
        exited=True
        try:child.wait(timeout=.75)
        except subprocess.TimeoutExpired:exited=False
        needed=False
        if not exited:
            fd=os.open(fifo,os.O_WRONLY|os.O_NONBLOCK);needed=True;os.close(fd)
        out,err=child.communicate(timeout=5)
        print('PROBE',json.dumps({'probe':'named_fifo','returned_before_writer':exited,'writer_was_required':needed,'stdout':out.decode(),'stderr':err.decode()}))
        assert child.returncode==0 and exited, 'named dependency VersionedFileReader blocks before fstat'
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)
