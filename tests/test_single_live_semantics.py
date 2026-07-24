# Created: 2026-07-22
# Last reused/audited: 2026-07-22
# Authority basis: operator-directed single-live-semantics extinction pass.
"""Relapse antibodies for dormant alternate-runtime concepts."""

from __future__ import annotations

from pathlib import Path

from scripts.check_single_live_semantics import violations


def test_gate_scans_live_and_current_surfaces(tmp_path: Path) -> None:
    for relative in (
        "src/live.py",
        "architecture/live.yaml",
        "config/settings.json",
        "deploy/live.plist",
        ".github/instructions/live.instructions.md",
        ".github/workflows/live.yml",
        "docs/authority/current.md",
        "docs/reference/current.md",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("mode = '" + "shadow_" + "veto_only'\n", encoding="utf-8")
    assert len({item.split(":", 1)[0] for item in violations(tmp_path)}) == 8


def test_gate_scans_selected_active_scripts_and_current_plan(tmp_path: Path) -> None:
    for relative in (
        "scripts/INDEX.md",
        "scripts/migrations/202607_single_live_semantics_cutover.py",
        "docs/operations/current/plans/INDEX.md",
        "docs/operations/current/plans/single_live_semantics_2026-07-22.md",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("mode = '" + "sha" + "dow'\n", encoding="utf-8")
    assert len({item.split(":", 1)[0] for item in violations(tmp_path)}) == 4


def test_gate_scans_arbitrary_executable_script(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "new_runtime_tool.py"
    script.parent.mkdir(parents=True)
    script.write_text("mode = 'shadow_veto_only'\n", encoding="utf-8")
    assert any(item.startswith("scripts/new_runtime_tool.py:") for item in violations(tmp_path))


def test_gate_scans_history_named_live_module(tmp_path: Path) -> None:
    source = tmp_path / "src" / "history" / "bad.py"
    source.parent.mkdir(parents=True)
    source.write_text("mode = 'shadow_veto_only'\n", encoding="utf-8")
    assert any(item.startswith("src/history/bad.py:") for item in violations(tmp_path))


def test_gate_scans_live_reachable_module_under_exact_exclusion(tmp_path: Path) -> None:
    main = tmp_path / "src" / "main.py"
    archived = tmp_path / "docs" / "archive" / "alternate.py"
    main.parent.mkdir(parents=True)
    archived.parent.mkdir(parents=True)
    main.write_text("from docs.archive import alternate\n", encoding="utf-8")
    archived.write_text("mode = 'shadow_veto_only'\n", encoding="utf-8")
    assert any(item.startswith("docs/archive/alternate.py:") for item in violations(tmp_path))


def test_gate_scans_dynamically_importable_python_under_exclusion(tmp_path: Path) -> None:
    loader = tmp_path / "src" / "loader.py"
    alternate = tmp_path / "docs" / "rebuild" / "alternate.py"
    loader.parent.mkdir(parents=True)
    alternate.parent.mkdir(parents=True)
    loader.write_text(
        "import importlib\nimportlib.import_module('docs.rebuild.alternate')\n",
        encoding="utf-8",
    )
    alternate.write_text("mode = 'shadow_veto_only'\n", encoding="utf-8")
    assert any(
        item.startswith("docs/rebuild/alternate.py:") for item in violations(tmp_path)
    )


def test_gate_scans_new_config_deploy_and_workflow_surfaces(tmp_path: Path) -> None:
    for relative in (
        "config/new-runtime.toml",
        "deploy/new-runtime.sh",
        ".github/workflows/new-runtime.yml",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("mode = 'shadow_veto_only'\n", encoding="utf-8")
    assert len({item.split(":", 1)[0] for item in violations(tmp_path)}) == 3


def test_ci_trigger_surface_covers_scanner_surface() -> None:
    workflow = Path(".github/workflows/money-path-release-gate.yml").read_text(
        encoding="utf-8"
    )
    assert "paths:" not in workflow


def test_gate_rejects_resurrected_inactive_lane(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    token = "shadow_" + "veto_only"
    (source / "bad.py").write_text(f"mode = {token!r}\n", encoding="utf-8")
    assert violations(tmp_path)


def test_gate_rejects_literal_split_dormant_token(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "bad.py").write_text(
        "mode = 'entry_forecast_' + 'rollout'\n",
        encoding="utf-8",
    )
    assert any(item.startswith("src/bad.py:") for item in violations(tmp_path))


def test_gate_rejects_literal_fstring_dormant_token(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "bad.py").write_text(
        "mode = f\"entry_forecast_{'rollout'}\"\n",
        encoding="utf-8",
    )
    assert any(item.startswith("src/bad.py:") for item in violations(tmp_path))


def test_gate_rejects_bound_literal_dormant_token(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "bad.py").write_text(
        "prefix = 'entry_forecast_'\n"
        "suffix = 'rollout'\n"
        "mode = prefix + suffix\n",
        encoding="utf-8",
    )
    assert any(item.startswith("src/bad.py:") for item in violations(tmp_path))


def test_cutover_exemption_rejects_arbitrary_retired_assignment(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "migrations" / "202607_single_live_semantics_cutover.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "RETIRED_RUNTIME_MODE = 'entry_forecast_' + 'rollout'\n"
        "mode = RETIRED_RUNTIME_MODE\n",
        encoding="utf-8",
    )
    assert any(item.startswith(f"{script.relative_to(tmp_path)}:") for item in violations(tmp_path))


def test_cutover_deletion_constant_cannot_flow_into_live_control(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "migrations" / "202607_single_live_semantics_cutover.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "RETIRED_CONFIG_KEYS = ('entry_forecast_' + 'rollout',)\n"
        "alias = RETIRED_CONFIG_KEYS[0]\n"
        "mode = alias\n",
        encoding="utf-8",
    )
    assert any("flows into 'mode'" in item for item in violations(tmp_path))


def test_cutover_deletion_constant_cannot_flow_into_subscript_control(
    tmp_path: Path,
) -> None:
    script = tmp_path / "scripts" / "migrations" / "202607_single_live_semantics_cutover.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "RETIRED_CONFIG_KEYS = ('entry_forecast_' + 'rollout',)\n"
        "config = {}\n"
        "config['mode'] = RETIRED_CONFIG_KEYS[0]\n",
        encoding="utf-8",
    )
    assert any("flows into 'mode'" in item for item in violations(tmp_path))


def test_cutover_deletion_constant_cannot_flow_through_setattr(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "migrations" / "202607_single_live_semantics_cutover.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "RETIRED_CONFIG_KEYS = ('entry_forecast_' + 'rollout',)\n"
        "setattr(config, 'mode', RETIRED_CONFIG_KEYS[0])\n",
        encoding="utf-8",
    )
    assert any("setattr control 'mode'" in item for item in violations(tmp_path))


def test_cutover_bound_control_key_cannot_receive_deletion_constant(
    tmp_path: Path,
) -> None:
    script = tmp_path / "scripts" / "migrations" / "202607_single_live_semantics_cutover.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "RETIRED_CONFIG_KEYS = ('entry_forecast_' + 'rollout',)\n"
        "CONTROL = 'mode'\n"
        "config = {}\n"
        "config[CONTROL] = RETIRED_CONFIG_KEYS[0]\n",
        encoding="utf-8",
    )
    assert any("flows into 'mode'" in item for item in violations(tmp_path))


def test_cutover_bound_setattr_key_cannot_receive_deletion_constant(
    tmp_path: Path,
) -> None:
    script = tmp_path / "scripts" / "migrations" / "202607_single_live_semantics_cutover.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "RETIRED_CONFIG_KEYS = ('entry_forecast_' + 'rollout',)\n"
        "CONTROL = 'mode'\n"
        "setattr(config, CONTROL, RETIRED_CONFIG_KEYS[0])\n",
        encoding="utf-8",
    )
    assert any("setattr control 'mode'" in item for item in violations(tmp_path))


def test_cutover_deletion_constant_cannot_control_live_mutation(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "migrations" / "202607_single_live_semantics_cutover.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "RETIRED_CONFIG_KEYS = ('entry_' + 'forecast_rollout',)\n"
        "if RETIRED_CONFIG_KEYS:\n"
        "    mode = 'live'\n",
        encoding="utf-8",
    )
    assert any("controls mutation of 'mode'" in item for item in violations(tmp_path))


def test_cutover_helper_cannot_launder_deletion_constant_into_control(
    tmp_path: Path,
) -> None:
    script = tmp_path / "scripts" / "migrations" / "202607_single_live_semantics_cutover.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "RETIRED_CONFIG_KEYS = ('entry_forecast_' + 'rollout',)\n"
        "CONTROL = 'mode'\n"
        "config = {}\n"
        "def apply(value):\n"
        "    config[CONTROL] = value\n"
        "apply(RETIRED_CONFIG_KEYS[0])\n",
        encoding="utf-8",
    )
    assert any("flows into 'mode'" in item for item in violations(tmp_path))


def test_cutover_match_guard_cannot_control_live_mutation(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "migrations" / "202607_single_live_semantics_cutover.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "RETIRED_CONFIG_KEYS = ('entry_' + 'forecast_rollout',)\n"
        "match 0:\n"
        "    case _ if RETIRED_CONFIG_KEYS:\n"
        "        mode = 'live'\n",
        encoding="utf-8",
    )
    assert any("controls mutation of 'mode'" in item for item in violations(tmp_path))


def test_cutover_kwargs_cannot_launder_deletion_constant_into_control(
    tmp_path: Path,
) -> None:
    script = tmp_path / "scripts" / "migrations" / "202607_single_live_semantics_cutover.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "RETIRED_CONFIG_KEYS = ('entry_forecast_' + 'rollout',)\n"
        "config = {}\n"
        "def apply(value):\n"
        "    config['mode'] = value\n"
        "apply(**{'value': RETIRED_CONFIG_KEYS[0]})\n",
        encoding="utf-8",
    )
    assert any("flows into 'mode'" in item for item in violations(tmp_path))


def test_cutover_deletion_constant_is_allowed_only_as_cleanup_target(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "migrations" / "202607_single_live_semantics_cutover.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "RETIRED_CONFIG_KEYS = ('entry_forecast_' + 'rollout',)\n"
        "def clean(mapping):\n"
        "    mapping.pop(RETIRED_CONFIG_KEYS[0], None)\n",
        encoding="utf-8",
    )
    assert violations(tmp_path) == []


def test_gate_rejects_retired_runtime_category(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    token = "telemetry_" + "only"
    (source / "bad.py").write_text(f"category = {token!r}\n", encoding="utf-8")
    assert violations(tmp_path)


def test_gate_rejects_extended_alternate_concept_variants(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "allowed.py").write_text(
        "# " + "diag" + "nostic alternate path\n"
        "# offline replay remains evidence-only\n"
        "mode = 'shadow_veto_only_extended'\n",
        encoding="utf-8",
    )
    assert violations(tmp_path)


def test_gate_rejects_persisted_parallel_freshness_authority(tmp_path: Path) -> None:
    source = tmp_path / "src" / "data"
    source.mkdir(parents=True)
    (source / "bad.py").write_text(
        "table = 'source_time_' + 'frontier'\n",
        encoding="utf-8",
    )
    assert any(item.startswith("src/data/bad.py:") for item in violations(tmp_path))


def test_gate_ignores_historical_and_migration_preview_surfaces(tmp_path: Path) -> None:
    for relative in (
        "docs/archive/old.md",
        "docs/evidence/old.md",
        "docs/rebuild/old.md",
        "docs/operations/current/plans/migration_preview/old.md",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("mode = '" + "sha" + "dow'\n", encoding="utf-8")
    assert violations(tmp_path) == []


def test_gate_allows_legitimate_audit_and_replay_concepts(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "audit_replay.py"
    script.parent.mkdir(parents=True)
    script.write_text("mode = 'audit_only'\n# replay evidence\n", encoding="utf-8")
    assert violations(tmp_path) == []
