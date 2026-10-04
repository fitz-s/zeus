# Created: 2026-10-03
# Last reused/audited: 2026-10-03
# Authority basis: lease-v1 round-3 consult; copied unchanged from
#   artifacts/merge_safety_lease_v1_round3/test_round3_move_alias.py. Run from the checkout root.
"""A regular-file precheck alone must not admit a symlink swapped in at rename."""
import json,os,errno
from pathlib import Path
import pytest
import src.data.replacement_forecast_live_materialization_queue as q
from tests.adversarial.test_execution_lease_adversaries import _request

def test_regular_request_replaced_by_alias_at_move_is_rejected(tmp_path,monkeypatch):
    requests=tmp_path/'requests';requests.mkdir();inflight=tmp_path/'inflight'
    p=requests/'London.json';p.write_text(json.dumps(_request()))
    target=tmp_path/'publisher-latest.json';target.write_bytes(p.read_bytes())
    assert not p.is_symlink()
    move=q.os.replace;injected=False;batch=None
    def inject(src,dst):
        nonlocal injected
        if Path(src)==p and not injected:
            injected=True
            move(p,tmp_path/'original-immutable.json')
            p.symlink_to(target)
        return move(src,dst)
    monkeypatch.setattr(q.os,'replace',inject)
    try:
        try:batch=q._new_claim_batch(inflight,[p])
        except (OSError,ValueError):return
        aliased=(batch/p.name).is_symlink()
        print('ROUND3',json.dumps({'probe':'alias_at_move','initially_regular':True,'accepted_alias':aliased,'recorded_identity':q._claim_records(batch)[0].identity}))
        assert not aliased,'a post-move follow-symlink read accepts a mutable publication'
    finally:
        if batch:q._release_claim_batch(batch)
