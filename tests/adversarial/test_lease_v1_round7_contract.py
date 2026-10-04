# Created: 2026-10-04
# Last reused/audited: 2026-10-04
# Authority basis: lease-v1 round-7 consult; copied unchanged from
#   artifacts/merge_safety_lease_v1_round7_review/test_round7_contract.py. Run from the checkout root.
"""Input-language control for the adapted round-6 collision probe."""
from pathlib import Path
import json
import subprocess
import src.data.replacement_forecast_live_materialization_queue as q
from tests.adversarial.test_execution_lease_adversaries import _request


def test_non_json_control_basename_is_ignored_not_deleted(tmp_path,monkeypatch):
    requests=tmp_path/'requests';requests.mkdir()
    source=requests/'claim.control';q._write_request(source,_request())
    before=source.read_bytes()
    seen=[]
    report=q.process_replacement_forecast_live_materialization_queue(
        request_dir=requests,processed_dir=tmp_path/'processed',failed_dir=tmp_path/'failed',
        forecast_db=None,seed_limit=0,limit=1,lane=q.MATERIALIZATION_LANE_PRIORITY,
        runner=lambda argv:(seen.append(argv) or subprocess.CompletedProcess(argv,0,'','')))
    print('PROBE',json.dumps({'case':'non_json_input_boundary','status':report.status,'leased':report.leased_count,'unchanged':source.exists() and source.read_bytes()==before}))
    assert source.exists() and source.read_bytes()==before and not seen and report.leased_count==0
