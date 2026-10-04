# Created: 2026-10-03
# Last reused/audited: 2026-10-03
# Authority basis: consult REQ-20261003-211813-a30820 (lease-v1 re-review);
#   copied from artifacts/merge_safety_lease_v1_review/test_resume_transport.py.
# Seam changes: the sibling import "test_review_execution_lease" is now its tracked name, tests.adversarial.test_lease_v1_review.
"""Extra ACK/frame controls using the real resident transport and an inert compute body."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import pytest
import src.runtime.warm_materializer as w
import src.data.replacement_forecast_live_materialization_queue as q
from tests.adversarial.test_lease_v1_review import queued, _worker_wrapper

ROOT = Path(__file__).resolve().parents[2]

@pytest.mark.parametrize('fault', ['lost_ack', 'wrong_request_id', 'missing_fd'])
def test_transport_fault_reaps_worker_before_claim_release(tmp_path, monkeypatch, fault):
    wrapper = _worker_wrapper(tmp_path, 'normal')
    text = wrapper.read_text()
    injection = '''\nfault = %r
original_receive = w.receive_leases

def faulty_receive(message, write):
    if fault == 'lost_ack':
        return original_receive(message, lambda frame: None)
    if fault == 'wrong_request_id':
        def bad_id(frame):
            value = json.loads(frame)
            value['request_id'] = 'different-invocation'
            write(json.dumps(value) + '\\n')
        return original_receive(message, bad_id)
    return original_receive(message, write)
w.receive_leases = faulty_receive
''' % fault
    text = text.replace('m._resident_worker()', injection + '\nm._resident_worker()')
    wrapper.write_text(text)
    monkeypatch.setattr(w, '_SCRIPT', wrapper)
    worker = w.ResidentMaterializer()
    requests, inflight, path = queued(tmp_path)
    batch = q._new_claim_batch(inflight, (path,))
    command = [sys.executable, str(wrapper), '--input-json', str(batch/path.name), '--commit']
    if fault == 'missing_fd':
        import socket
        original_send = socket.send_fds
        monkeypatch.setattr(socket, 'send_fds', lambda sock, buffers, fds: original_send(sock, buffers, fds[:-1]))
    try:
        with pytest.raises((EOFError, ValueError, subprocess.TimeoutExpired)):
            worker.run(command, timeout=3, lease_fds=q._claim_lease_fds([batch/path.name]))
        assert worker._process is None
        # Parent retains its ownership until the failed invocation has unwound.
        assert q._claim_owner_alive(batch) is True
        q._release_claim_batch(batch)
        assert q._claim_owner_alive(batch) is False
        print('PROBE', json.dumps(dict(probe='extra_transfer_fault', fault=fault, worker_reaped=True, claim_free_after_release=True)))
    finally:
        worker.close()
        q._release_claim_batch(batch)
