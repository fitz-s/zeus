# Created: 2026-10-04
# Last reused/audited: 2026-10-04
# Authority basis: live defect 2026-10-04 (batch *.lease-v1.*.pid97142 stranded
#   with CLAIM_DEFERRED_READ_DEADLINE after its claim was published).
"""A claim deadline bounds pre-claim reads; a published leased batch is a fact."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import src.data.replacement_forecast_live_materialization_queue as queue


def _request() -> dict[str, object]:
    return {
        "city": "London",
        "target_date": "2026-08-25",
        "temperature_metric": "high",
        "source_cycle_time": "2026-08-24T00:00:00+00:00",
        "computed_at": "2026-08-24T08:00:00+00:00",
        "baseline_source_run_id": "baseline-run",
        "openmeteo_source_run_id": "openmeteo-run",
        "openmeteo_payload_json": "payload.json",
        "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "30C"}],
    }


@pytest.fixture(autouse=True)
def _release_claims():
    before = set(queue._HELD_CLAIM_LEASES)
    yield
    for batch in set(queue._HELD_CLAIM_LEASES) - before:
        queue._release_claim_batch(Path(batch))


def _expire_after(monkeypatch, name: str) -> None:
    """The claim succeeds, then the claim deadline passes before the guard exits."""
    original = getattr(queue, name)

    def claim_then_expire(*args, **kwargs):
        claim = original(*args, **kwargs)
        assert claim.batch_path is not None
        queue._active_claim_read_deadline().deadline_monotonic = 0.0
        return claim

    monkeypatch.setattr(queue, name, claim_then_expire)


@pytest.mark.parametrize(
    "claim_fn, seeded",
    [
        ("_apply_request_claim_read_plan", False),
        ("_claim_replacement_forecast_live_materialization_queue_locked", True),
    ],
)
def test_deadline_after_claim_processes_the_batch(tmp_path, monkeypatch, claim_fn, seeded):
    requests = tmp_path / "requests"
    requests.mkdir()
    request = requests / "London.2026-08-25.high.json"
    request.write_text(json.dumps(_request()), encoding="utf-8")
    seeds = {}
    if seeded:
        for name in ("seeds", "seeds_processed", "seeds_failed"):
            (tmp_path / name).mkdir()
        seeds = dict(seed_dir=tmp_path / "seeds",
                     seed_processed_dir=tmp_path / "seeds_processed",
                     seed_failed_dir=tmp_path / "seeds_failed", seed_limit=1)
    else:
        seeds = dict(seed_limit=0)
    _expire_after(monkeypatch, claim_fn)
    held_before = set(queue._HELD_CLAIM_LEASES)
    spawned: list[list[str]] = []

    def runner(argv):
        spawned.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0, stdout="", stderr="")

    report = queue.process_replacement_forecast_live_materialization_queue(
        request_dir=requests, processed_dir=tmp_path / "processed",
        failed_dir=tmp_path / "failed", forecast_db=None, limit=1,
        runner=runner, **seeds,
    )

    assert report.status == "PROCESSED", report.reason_codes
    assert report.processed_count == 1 and len(spawned) == 1
    assert queue._CLAIM_READ_DEFERRED_REASON not in report.reason_codes
    inflight = tmp_path / queue.MATERIALIZATION_INFLIGHT_DIR_NAME
    assert not inflight.exists() or not list(inflight.iterdir())
    assert set(queue._HELD_CLAIM_LEASES) == held_before
