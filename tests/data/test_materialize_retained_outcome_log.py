# Created: 2026-10-02
# Last reused/audited: 2026-10-02
# Authority basis: operator observability — live 2026-10-02 Chongqing 10-02 high materialize
#   errors logged as `UNCLASSIFIED ... stderr=ts` for an hour; the worker's verdict
#   (BLOCKED / ZERO_MULTI_MODEL_EXTRAS) sat on stdout, the stderr tail was parse warnings.
"""A retained materialization failure logs the worker's own verdict line, log-only."""

from __future__ import annotations

import json
import subprocess

from src.data import replacement_forecast_live_materialization_queue as queue


def _completed(stdout: str, stderr: str, returncode: int = 1) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_outcome_is_the_stdout_verdict_not_the_stderr_tail():
    verdict = {"status": "BLOCKED", "reason_codes": ["REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET",
               "FUSION_DECLINED:ZERO_MULTI_MODEL_EXTRAS"], "posterior_id": None}
    noise = "\n".join("BAYES_PRECISION_FUSION parse batched single_runs (fail-soft): partial" for _ in range(40))
    outcome = json.loads(queue._subprocess_result_outcome(_completed(json.dumps(verdict) + "\n", noise)))
    assert outcome == {"status": "BLOCKED", "reason_codes": verdict["reason_codes"]}


def test_outcome_reads_an_error_verdict_on_stderr():
    error = {"status": "ERROR", "error_type": "ValueError", "error": "DAY0_X", "failure_category": "UNCLASSIFIED"}
    outcome = json.loads(queue._subprocess_result_outcome(_completed("", "warning\n" + json.dumps(error), 2)))
    assert outcome == error


def test_no_verdict_is_named():
    assert queue._subprocess_result_outcome(_completed("", "ts")) == "none"


def test_outcome_does_not_change_the_category_classification():
    verdict = {"status": "BLOCKED", "reason_codes": ["R"]}
    completed = _completed(json.dumps(verdict), "ts")
    assert queue._subprocess_result_failure_category(completed) is queue.FailureCategory.UNCLASSIFIED
