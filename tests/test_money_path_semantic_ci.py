# Created: 2026-05-21
# Last reused/audited: 2026-10-06
# Lifecycle: created=2026-05-21; last_reviewed=2026-10-06; last_reused=2026-10-06
# Purpose: Self-defense tests for money-path semantic CI helper scripts.
# Reuse: Run when changing scripts/ci money-path classifier/coverage/test-quality gates.
# Authority basis: architecture/money_path_objects.yaml; architecture/money_path_ci.yaml; architecture/test_quality.yaml
"""Self-defense tests for money-path semantic CI helper scripts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _source_protocol_classification(tmp_path, source, *, owner="src/data/ecmwf_open_data.py"):
    diff = tmp_path / "source.patch"
    diff.write_text(f"diff --git a/{owner} b/{owner}\n+++ b/{owner}\n" +
        "\n".join("+" + line for line in source.splitlines()) + "\n")
    proc = subprocess.run([sys.executable, "scripts/ci/semantic_diff_classifier.py",
        "--diff-file", str(diff), "--fail-on-unregistered"], cwd=ROOT,
        text=True, capture_output=True)
    return proc.returncode, json.loads(proc.stdout)


def test_classifier_routes_declared_source_reason_and_ingest_mode_not_lifecycle(tmp_path):
    rc, payload = _source_protocol_classification(tmp_path,
        'def capture(ingest_mode="ARCHIVE_BACKFILL"):\n'
        '    return {"reason": "NATIVE_2T_CAPTURE_UNKNOWN"}\n')
    assert rc == 0 and not payload["unregistered_objects"]
    assert len(payload["new_source_protocol_values"]) == 2
    assert "ARCHIVE_BACKFILL" not in payload["new_states"]
    assert "NATIVE_2T_CAPTURE_UNKNOWN" not in payload["new_states"]
    assert {"MP-EXT-001", "MP-EXT-002"} <= set(payload["required_invariants"])
    rc, payload = _source_protocol_classification(tmp_path,
        'def capture(ingest_mode="ARCHIVE_BACKFILL"):\n'
        '    return {"qualification_status": "UNKNOWN" if ingest_mode == "SCHEDULED_LIVE" else "OFFLINE_ONLY"}\n')
    assert rc == 0 and not payload["unregistered_objects"]


def test_classifier_native_21_tokens_have_structural_protocol_proof():
    from scripts.ci.semantic_diff_classifier import classify, load_yaml
    objects = load_yaml(ROOT / "architecture/money_path_objects.yaml")
    mapping = load_yaml(ROOT / "architecture/money_path_ci.yaml")
    declarations = {key: spec for key, spec in objects["source_protocol_objects"].items()
                    if spec["owner"] in {"src/data/ecmwf_open_data.py", "src/ingest/forecast_live_daemon.py"}}
    owners = {spec["owner"] for spec in declarations.values()}
    sources = {owner: (ROOT / owner).read_text() for owner in owners}
    # Real producer AST, without a historical git-object dependency in shallow
    # CI checkouts. The separately captured base->candidate CLI proves routing.
    diff = ""
    for owner in sorted(owners):
        values = {value for spec in declarations.values() if spec["owner"] == owner
                  for value in spec["values"] if value != "SCHEDULED_LIVE"}
        diff += f"diff --git a/{owner} b/{owner}\n+++ b/{owner}\n"
        diff += "\n".join('+"' + value + '"' for value in sorted(values)) + "\n"
    payload = classify(diff, sorted(owners), objects, mapping, sources=sources).to_dict()
    assert not payload["unregistered_objects"], payload["unregistered_objects"]
    protocols = {value.rsplit(":", 1)[-1] for value in payload["new_source_protocol_values"]}
    assert "ARCHIVE_BACKFILL" in protocols
    assert len([value for value in protocols if value.endswith("_UNKNOWN")]) == 20
    assert not protocols.intersection(payload["new_states"])


def test_classifier_source_reason_mixed_with_money_uses_remains_failclosed(tmp_path):
    reason = "NATIVE_2T_CAPTURE_UNKNOWN"
    for use in (f'state = "{reason}"', f'return {{"status": "{reason}"}}',
                f'return {{"command_state": "{reason}"}}',
                f'return command_state == "{reason}"',
                f'return {{"action": "{reason}"}}', f'return {{"side": "{reason}"}}',
                f'return NativeTemperatureSource("{reason}", None, 0, ())'):
        rc, payload = _source_protocol_classification(tmp_path,
            f'def capture():\n    report = {{"reason": "{reason}"}}\n    {use}\n')
        assert rc == 2 and payload["unregistered_objects"], use
        assert not any(value.endswith(reason) for value in payload["new_source_protocol_values"])


@pytest.mark.parametrize("owner,reason,statement", [
    ("scripts/deploy_live.py", "PROBABILITY_UPGRADE_CODE_IDENTITY_UNKNOWN", "return False, {value}"),
    ("scripts/check_live_restart_preflight.py", "PROBABILITY_UPGRADE_CURRENT_INPUT_ROLE_UNKNOWN", "return {{'reason': {value}}}"),
    ("scripts/check_live_restart_preflight.py", "PROBABILITY_UPGRADE_HELD_SCOPE_UNKNOWN", "scope['reason'] = {value}"),
])
def test_classifier_upgrade_refusal_is_reason_not_state(tmp_path, owner, reason, statement):
    legal = "def qualify():\n    " + statement.format(value=repr(reason)) + "\n"
    rc, payload = _source_protocol_classification(tmp_path, legal, owner=owner)
    assert rc == 0 and not payload["unregistered_objects"]
    for money in (f"status = {reason!r}\n", f"sql = \"CHECK(state IN ('{reason}'))\"\n",
                  f"from enum import Enum\nclass Status(Enum):\n    FIELD={reason!r}\n"):
        rc, payload = _source_protocol_classification(tmp_path, legal + money, owner=owner)
        assert rc == 2 and payload["unregistered_objects"]


def test_classifier_source_declared_literal_enum_or_sql_check_is_not_exempt(tmp_path):
    for body in ('from enum import Enum\nclass Kind(Enum):\n    FIELD = "NATIVE_2T_CAPTURE_UNKNOWN"\n',
                 'sql = "CHECK (state IN (\'NATIVE_2T_CAPTURE_UNKNOWN\'))"\n'):
        rc, payload = _source_protocol_classification(tmp_path, body)
        assert rc == 2 and payload["unregistered_objects"]
        # A separate legitimate reason cannot launder the same SQL/Enum state.
        rc, payload = _source_protocol_classification(tmp_path,
            'report = {"reason": "NATIVE_2T_CAPTURE_UNKNOWN"}\n' + body)
        assert rc == 2 and payload["unregistered_objects"]
        assert not payload["new_source_protocol_values"]


def test_classifier_source_wrong_owner_unknown_reason_and_mode_fail(tmp_path):
    rc, payload = _source_protocol_classification(tmp_path,
        'def submit():\n    return {"reason": "NATIVE_2T_CAPTURE_UNKNOWN"}\n',
        owner="src/execution/executor.py")
    assert rc == 2 and "state:NATIVE_2T_CAPTURE_UNKNOWN" in payload["unregistered_objects"]
    for source in ('def capture(ingest_mode="SURPRISE_PROTOCOL"):\n    return ingest_mode\n',
                   'def capture():\n    return {"reason": "NATIVE_UNDECLARED_UNKNOWN"}\n'):
        rc, payload = _source_protocol_classification(tmp_path, source)
        assert rc == 2 and payload["unregistered_objects"]
    rc, payload = _source_protocol_classification(tmp_path,
        'def capture(ingest_mode="SCHEDULED_LIVE"):\n    return {"command_status": "SCHEDULED_LIVE"}\n')
    assert rc == 2 and payload["unregistered_objects"]


def test_classifier_cli_fails_on_unregistered_redeem_state(tmp_path: Path) -> None:
    diff = tmp_path / "diff.patch"
    diff.write_text(
        "diff --git a/src/execution/settlement_commands.py b/src/execution/settlement_commands.py\n"
        "+++ b/src/execution/settlement_commands.py\n"
        "+REDEEM_AUTORETRYABLE_REVIEW = 'REDEEM_AUTORETRYABLE_REVIEW'\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/ci/semantic_diff_classifier.py",
            "--diff-file",
            str(diff),
            "--fail-on-unregistered",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert "state:REDEEM_AUTORETRYABLE_REVIEW" in payload["unregistered_objects"]


def test_classifier_cli_fails_on_unregistered_intent_state(tmp_path: Path) -> None:
    diff = tmp_path / "diff.patch"
    diff.write_text(
        "diff --git a/src/state/venue_command_repo.py b/src/state/venue_command_repo.py\n"
        "+++ b/src/state/venue_command_repo.py\n"
        "+INTENT_CREATED_V2 = 'INTENT_CREATED_V2'\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/ci/semantic_diff_classifier.py",
            "--diff-file",
            str(diff),
            "--fail-on-unregistered",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert "state:INTENT_CREATED_V2" in payload["unregistered_objects"]


def test_classifier_accepts_registered_reactor_runtime_config(tmp_path: Path) -> None:
    diff = tmp_path / "diff.patch"
    diff.write_text(
        "diff --git a/src/main.py b/src/main.py\n"
        "+++ b/src/main.py\n"
        "+float(os.environ.get('ZEUS_REACTOR_GAMMA_EMPTY_BACKOFF_SECONDS', '300.0'))\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/ci/semantic_diff_classifier.py",
            "--diff-file",
            str(diff),
            "--fail-on-unregistered",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert "ZEUS_REACTOR_GAMMA_EMPTY_BACKOFF_SECONDS" in payload["new_states"]
    assert "state:ZEUS_REACTOR_GAMMA_EMPTY_BACKOFF_SECONDS" not in payload[
        "unregistered_objects"
    ]


def test_classifier_accepts_registered_pre_submit_inner_timeout_config(tmp_path: Path) -> None:
    diff = tmp_path / "diff.patch"
    diff.write_text(
        "diff --git a/src/main.py b/src/main.py\n"
        "+++ b/src/main.py\n"
        "+float(os.environ.get('ZEUS_PRE_SUBMIT_INNER_IO_TIMEOUT_SECONDS', '1.0'))\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/ci/semantic_diff_classifier.py",
            "--diff-file",
            str(diff),
            "--fail-on-unregistered",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert "ZEUS_PRE_SUBMIT_INNER_IO_TIMEOUT_SECONDS" in payload["new_states"]
    assert "state:ZEUS_PRE_SUBMIT_INNER_IO_TIMEOUT_SECONDS" not in payload[
        "unregistered_objects"
    ]


def test_classifier_accepts_registered_held_sell_reauction_protocol(tmp_path: Path) -> None:
    diff = tmp_path / "diff.patch"
    diff.write_text(
        "diff --git a/src/events/reactor.py b/src/events/reactor.py\n"
        "+++ b/src/events/reactor.py\n"
        "+claim_reason = 'GLOBAL_WINNER_SUBMIT_FENCED'\n"
        "+status = 'CAPITAL_REJECTED'\n"
        "+reason = 'GLOBAL_AUCTION_CAPITAL_REJECTED'\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/ci/semantic_diff_classifier.py",
            "--diff-file",
            str(diff),
            "--fail-on-unregistered",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert {
        "GLOBAL_WINNER_SUBMIT_FENCED",
        "CAPITAL_REJECTED",
        "GLOBAL_AUCTION_CAPITAL_REJECTED",
    }.issubset(payload["new_states"])
    assert payload["unregistered_objects"] == []


def test_classifier_accepts_registered_global_submit_receipt_vocabulary(
    tmp_path: Path,
) -> None:
    vocabulary = {
        "GLOBAL_SELL_RECEIPT_INTENT_KIND_MISMATCH",
        "GLOBAL_SELL_RECEIPT_AUDIT_INTENT_EVENT_MISSING",
        "JIT_SUBMIT",
        "PRE_SUBMIT_BOOK_AUTHORITY_JIT_REQUIRED",
        "PRE_SUBMIT_SEALED_BOOK_IDENTITY_MISMATCH",
        "PRE_SUBMIT_SEALED_BOOK_FRESHNESS_INVALID",
        "PRE_SUBMIT_SEALED_BOOK_DEPTH_INVALID",
        "PRE_SUBMIT_SEALED_BOOK_HASH_MISMATCH",
        "PRE_SUBMIT_SEALED_BOOK_WITNESS_MISMATCH",
    }
    diff = tmp_path / "diff.patch"
    added_literals = "".join(
        f"+reason = {value!r}\n" for value in sorted(vocabulary)
    )
    diff.write_text(
        "diff --git a/src/events/reactor.py b/src/events/reactor.py\n"
        "+++ b/src/events/reactor.py\n"
        + added_literals,
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/ci/semantic_diff_classifier.py",
            "--diff-file",
            str(diff),
            "--fail-on-unregistered",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert vocabulary.issubset(payload["new_states"])
    assert not {f"state:{value}" for value in vocabulary}.intersection(
        payload["unregistered_objects"]
    )


def test_semantic_ci_registry_changes_select_self_defense_tests(tmp_path: Path) -> None:
    diff = tmp_path / "diff.patch"
    diff.write_text(
        "diff --git a/architecture/money_path_ci.yaml b/architecture/money_path_ci.yaml\n"
        "+++ b/architecture/money_path_ci.yaml\n"
        "+  MP-NEW-001:\n"
        "+    description: new invariant\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/ci/semantic_diff_classifier.py",
            "--diff-file",
            str(diff),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert "MP-CI-001" in payload["required_invariants"]
    assert "tests/test_money_path_semantic_ci.py" in payload["tests"]


def test_invariant_coverage_rejects_missing_selected_test() -> None:
    classification = {
        "required_invariants": ["MP-SCH-001"],
        "tests": ["tests/test_semantic_linter.py"],
    }
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/ci/assert_invariant_coverage.py",
            "--classification-json",
            json.dumps(classification),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 1
    assert "MP-SCH-001" in proc.stdout
    assert "none of registered tests selected" in proc.stdout


def test_test_quality_gate_accepts_registered_money_path_tests() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/ci/assert_test_quality.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "money-path test quality OK" in proc.stdout


def test_submit_order_patterns_include_place_limit_order() -> None:
    """P1-5 antibody: place_limit_order must be a registered submit_order side-effect pattern.
    Missing patterns allow undetected order submission paths to bypass MP-SIDE invariants.
    """
    import yaml

    objects_path = ROOT / "architecture" / "money_path_objects.yaml"
    data = yaml.safe_load(objects_path.read_text(encoding="utf-8"))
    patterns = data["side_effect_calls"]["submit_order"]["patterns"]
    for expected in ("place_limit_order", "place_market_order", "post_order", "build_order"):
        assert expected in patterns, (
            f"{expected} missing from submit_order.patterns — "
            "semantic classifier will not flag this as a side-effect path"
        )


def test_copilot_instruction_change_routes_to_self_defense_segment(tmp_path: Path) -> None:
    """P1-2 antibody: .github/copilot-instructions.md changes must select MP-CI-001
    and the self-defense tests. Without this, Copilot instruction drift is invisible
    to the semantic CI gate.
    """
    diff = tmp_path / "diff.patch"
    diff.write_text(
        "diff --git a/.github/copilot-instructions.md b/.github/copilot-instructions.md\n"
        "+++ b/.github/copilot-instructions.md\n"
        "+# changed review guidance\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/ci/semantic_diff_classifier.py",
            "--diff-file",
            str(diff),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "MP-CI-001" in payload["required_invariants"], (
        f"MP-CI-001 not in {payload['required_invariants']} — "
        ".github/copilot-instructions.md not routed to semantic_ci_self_defense segment"
    )


def test_strategy_profile_registry_change_routes_to_strategy_authority(tmp_path: Path) -> None:
    """P1-6 antibody: architecture/strategy_profile_registry.yaml changes must select
    MP-STR-001/STR-002 and the strategy_authority tests. Without this, registry changes
    that add/remove strategies bypass the governance gate.
    """
    diff = tmp_path / "diff.patch"
    diff.write_text(
        "diff --git a/architecture/strategy_profile_registry.yaml"
        " b/architecture/strategy_profile_registry.yaml\n"
        "+++ b/architecture/strategy_profile_registry.yaml\n"
        "+  new_strategy:\n"
        "+    breakeven_win_rate: 0.52\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/ci/semantic_diff_classifier.py",
            "--diff-file",
            str(diff),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert "MP-STR-001" in payload["required_invariants"] or "MP-STR-002" in payload["required_invariants"], (
        f"Neither MP-STR-001 nor MP-STR-002 in {payload['required_invariants']} — "
        "architecture/strategy_profile_registry.yaml not routed to strategy_authority segment"
    )
