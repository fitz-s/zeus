# Created: 2026-10-04
# Last reused/audited: 2026-10-04
# Authority basis: lease-v1 round-7 consult; copied unchanged from
#   artifacts/merge_safety_lease_v1_round7_review/test_round7_reporting.py. Run from the checkout root.
"""Administrative/reporting checks, independent of live queue safety.
These probes inspect the actual default reconcile CLI and rehearsal helper.
"""
from __future__ import annotations
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
import src.data.replacement_forecast_live_materialization_queue as q
from tests.adversarial.test_execution_lease_adversaries import _request

ROOT=Path.cwd().resolve()


def test_reconcile_dry_run_reports_unreadable_receipt(tmp_path):
    requests=tmp_path/'requests';requests.mkdir()
    target=tmp_path/'target';target.write_text('do not read')
    alias=requests/'bad.json';alias.symlink_to(target)
    saved=q._quarantine_request_alias(alias)
    capture=saved.parent.parent
    control=capture/q._ALIAS_RECEIPT_NAME;original=control.read_bytes();control.chmod(0)
    if os.access(control,os.R_OK):
        control.chmod(0o600);pytest.skip('uid bypasses permission bits')
    before=control.lstat()
    outcomes=[]
    try:
        for apply in (False,True):
            cmd=[sys.executable,'-B',str(ROOT/'scripts/reconcile_materialization_inflight.py'),'--request-dir',str(requests)]
            if apply:cmd.append('--apply')
            result=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
            body=json.loads(result.stdout);outcomes.append({'apply':apply,'exit':result.returncode,'report':body})
        assert os.path.samestat(before,control.lstat())
        print('PROBE',json.dumps({'case':'dry_run_unknown_receipt','outcomes':outcomes}))
        assert all(x['exit']==3 and not x['report']['quiescent'] for x in outcomes), 'dry run reports free directory as quiescent despite unreadable terminal evidence'
    finally:
        control.chmod(0o600)
        assert control.read_bytes()==original


def test_rehearsal_tree_counts_legacy_named_payload_in_new_batch(tmp_path):
    requests=tmp_path/'requests';requests.mkdir()
    source=requests/'_claim.json';q._write_request(source,_request())
    batch=q._new_claim_batch(tmp_path/'inflight',[source])
    try:
        script=ROOT/'docs/operations/current/plans/canonical_execution_lease_rehearsal.py'
        module=ast.parse(script.read_text())
        run=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='run')
        tree=next(n for n in run.body if isinstance(n,ast.FunctionDef) and n.name=='tree')
        code=ast.fix_missing_locations(ast.Module(body=[tree],type_ignores=[]))
        env={'queue':tmp_path};exec(compile(code,str(script),'exec'),env)
        report=env['tree']()
        expected=f'{batch.name}/_claim.json'
        print('PROBE',json.dumps({'case':'rehearsal_inventory','actual_request':expected,'listed':report['inflight_requests']}))
        assert expected in report['inflight_requests'], 'rehearsal still excludes the old metadata basename even when it is request payload'
    finally:q._release_claim_batch(batch)
