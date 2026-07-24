# Created: 2026-07-22
# Last reused/audited: 2026-07-24
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
        "docs/operations/current/plans/other.md",
        "docs/reference/current.md",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("mode = '" + "shadow_" + "veto_only'\n", encoding="utf-8")
    assert len({item.split(":", 1)[0] for item in violations(tmp_path)}) == 9


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


def test_gate_rejects_executable_shell_under_excluded_subtree(
    tmp_path: Path,
) -> None:
    script = tmp_path / "docs" / "archive" / "bad.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\necho historical\n", encoding="utf-8")
    script.chmod(0o755)
    found = violations(tmp_path)
    assert any(
        item.startswith("docs/archive/bad.sh:")
        and "non-document artifact" in item
        for item in found
    )
    assert any("executable permission" in item for item in found)
    assert any("has a shebang" in item for item in found)


def test_gate_rejects_subprocess_target_under_excluded_subtree(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "src" / "runtime_launcher.py"
    target = tmp_path / "docs" / "archive" / "historical.md"
    launcher.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    launcher.write_text(
        "import subprocess\n"
        "subprocess.run(['bash', 'docs/archive/historical.md'], check=True)\n",
        encoding="utf-8",
    )
    target.write_text("historical evidence\n", encoding="utf-8")
    assert any(
        item.startswith("src/runtime_launcher.py:")
        and "consumes an excluded subtree" in item
        for item in violations(tmp_path)
    )


def test_gate_rejects_shell_source_from_excluded_subtree(tmp_path: Path) -> None:
    launcher = tmp_path / "scripts" / "runtime.sh"
    target = tmp_path / "docs" / "evidence" / "historical.md"
    launcher.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    launcher.write_text(
        ". docs/evidence/historical.md\n",
        encoding="utf-8",
    )
    target.write_text("historical evidence\n", encoding="utf-8")
    assert any(
        item.startswith("scripts/runtime.sh:")
        and "executes or sources an excluded subtree" in item
        for item in violations(tmp_path)
    )


def test_gate_rejects_plist_program_argument_under_excluded_subtree(
    tmp_path: Path,
) -> None:
    plist = tmp_path / "deploy" / "runtime.plist"
    target = tmp_path / "docs" / "rebuild" / "historical.md"
    plist.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    plist.write_text(
        "<plist><dict><key>ProgramArguments</key><array>"
        "<string>bash</string><string>docs/rebuild/historical.md</string>"
        "</array></dict></plist>\n",
        encoding="utf-8",
    )
    target.write_text("historical evidence\n", encoding="utf-8")
    assert any(
        item.startswith("deploy/runtime.plist:")
        and "ProgramArguments" in item
        for item in violations(tmp_path)
    )


def test_gate_rejects_live_config_load_from_excluded_subtree(
    tmp_path: Path,
) -> None:
    loader = tmp_path / "src" / "config_loader.py"
    target = tmp_path / "docs" / "evidence" / "historical.md"
    loader.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    loader.write_text(
        "from pathlib import Path\n"
        "config = Path('docs/evidence/historical.md').read_text()\n",
        encoding="utf-8",
    )
    target.write_text("historical evidence\n", encoding="utf-8")
    assert any(
        item.startswith("src/config_loader.py:")
        and "consumes an excluded subtree" in item
        for item in violations(tmp_path)
    )


def test_gate_rejects_pathlib_composition_into_excluded_subtree(
    tmp_path: Path,
) -> None:
    loader = tmp_path / "src" / "config_loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        "from pathlib import Path\n"
        "base = Path('docs') / 'archive'\n"
        "config = (base / 'historical.md').read_text()\n",
        encoding="utf-8",
    )
    assert any(
        item.startswith("src/config_loader.py:")
        and "consumes an excluded subtree" in item
        for item in violations(tmp_path)
    )


def test_gate_rejects_bound_pathlib_subprocess_target(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "scripts" / "runtime_launcher.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(
        "from pathlib import Path\n"
        "import subprocess\n"
        "target = Path('docs') / 'evidence' / 'historical.md'\n"
        "subprocess.run(['bash', str(target)], check=True)\n",
        encoding="utf-8",
    )
    assert any(
        item.startswith("scripts/runtime_launcher.py:")
        and "consumes an excluded subtree" in item
        for item in violations(tmp_path)
    )


def test_gate_rejects_multi_argument_path_into_excluded_subtree(
    tmp_path: Path,
) -> None:
    loader = tmp_path / "src" / "config_loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        "from pathlib import Path\n"
        "config = Path('docs', 'archive', 'historical.md').read_text()\n",
        encoding="utf-8",
    )
    assert any(
        item.startswith("src/config_loader.py:")
        and "consumes an excluded subtree" in item
        for item in violations(tmp_path)
    )


def test_gate_rejects_aliased_path_into_excluded_subtree(
    tmp_path: Path,
) -> None:
    loader = tmp_path / "src" / "config_loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        "from pathlib import Path as P\n"
        "config = (P('docs') / 'archive' / 'historical.md').read_text()\n",
        encoding="utf-8",
    )
    assert any(
        item.startswith("src/config_loader.py:")
        and "consumes an excluded subtree" in item
        for item in violations(tmp_path)
    )


def test_gate_rejects_aliased_join_into_excluded_subtree(
    tmp_path: Path,
) -> None:
    loader = tmp_path / "src" / "config_loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        "from os.path import join as j\n"
        "with open(j('docs', 'archive', 'historical.md')) as handle:\n"
        "    config = handle.read()\n",
        encoding="utf-8",
    )
    assert any(
        item.startswith("src/config_loader.py:")
        and "consumes an excluded subtree" in item
        for item in violations(tmp_path)
    )


def test_gate_rejects_aliased_os_join_into_excluded_subtree(
    tmp_path: Path,
) -> None:
    loader = tmp_path / "src" / "config_loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        "import os as operating\n"
        "with open(operating.path.join('docs', 'archive', 'historical.md')) as handle:\n"
        "    config = handle.read()\n",
        encoding="utf-8",
    )
    assert any(
        item.startswith("src/config_loader.py:")
        and "consumes an excluded subtree" in item
        for item in violations(tmp_path)
    )


def test_gate_rejects_joinpath_into_excluded_subtree(
    tmp_path: Path,
) -> None:
    loader = tmp_path / "src" / "config_loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        "from pathlib import Path\n"
        "config = Path('docs').joinpath('archive', 'historical.md').read_text()\n",
        encoding="utf-8",
    )
    assert any(
        item.startswith("src/config_loader.py:")
        and "consumes an excluded subtree" in item
        for item in violations(tmp_path)
    )


def test_gate_rejects_starred_path_into_excluded_subtree(
    tmp_path: Path,
) -> None:
    loader = tmp_path / "src" / "config_loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        "from pathlib import Path\n"
        "config = Path(*('docs', 'archive', 'historical.md')).read_text()\n",
        encoding="utf-8",
    )
    assert any(
        item.startswith("src/config_loader.py:")
        and "consumes an excluded subtree" in item
        for item in violations(tmp_path)
    )


def test_gate_rejects_starred_os_join_into_excluded_subtree(
    tmp_path: Path,
) -> None:
    loader = tmp_path / "src" / "config_loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        "import os\n"
        "with open(os.path.join(*('docs', 'archive', 'historical.md'))) as handle:\n"
        "    config = handle.read()\n",
        encoding="utf-8",
    )
    assert any(
        item.startswith("src/config_loader.py:")
        and "consumes an excluded subtree" in item
        for item in violations(tmp_path)
    )


def test_gate_allows_plain_historical_markdown_under_excluded_subtree(
    tmp_path: Path,
) -> None:
    history = tmp_path / "docs" / "archive" / "historical.md"
    history.parent.mkdir(parents=True)
    history.write_text("historical evidence only\n", encoding="utf-8")
    assert violations(tmp_path) == []


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


def test_cutover_var_kwargs_cannot_launder_deletion_constant_into_control(
    tmp_path: Path,
) -> None:
    script = tmp_path / "scripts" / "migrations" / "202607_single_live_semantics_cutover.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "RETIRED_CONFIG_KEYS = ('entry_forecast_' + 'rollout',)\n"
        "config = {}\n"
        "def apply(**options):\n"
        "    config['mode'] = options['value']\n"
        "apply(**{'value': RETIRED_CONFIG_KEYS[0]})\n",
        encoding="utf-8",
    )
    assert any("flows into 'mode'" in item for item in violations(tmp_path))


def test_cutover_default_parameter_cannot_launder_deletion_constant(
    tmp_path: Path,
) -> None:
    script = tmp_path / "scripts" / "migrations" / "202607_single_live_semantics_cutover.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "RETIRED_CONFIG_KEYS = ('entry_' + 'forecast_rollout',)\n"
        "config = {}\n"
        "def apply(value=RETIRED_CONFIG_KEYS[0]):\n"
        "    config['mode'] = value\n"
        "apply()\n",
        encoding="utf-8",
    )
    assert any("flows into 'mode'" in item for item in violations(tmp_path))


def test_cutover_return_value_cannot_launder_deletion_constant(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "migrations" / "202607_single_live_semantics_cutover.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "RETIRED_CONFIG_KEYS = ('entry_' + 'forecast_rollout',)\n"
        "def pick():\n"
        "    return RETIRED_CONFIG_KEYS[0]\n"
        "mode = pick()\n",
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


def test_gate_rejects_retired_concept_as_control_value_or_modifier(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    token = "diag" + "nostic"
    (source / "value.py").write_text(f"mode = {token!r}\n", encoding="utf-8")
    (source / "modifier.py").write_text(
        f"parser.help = {f'{token} mode'!r}\n",
        encoding="utf-8",
    )
    found = violations(tmp_path)
    assert any(item.startswith("src/value.py:") for item in found)
    assert any(item.startswith("src/modifier.py:") for item in found)


def test_gate_rejects_retired_concept_in_python_identifier(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    token = "diag" + "nostic"
    (source / "bad.py").write_text(
        f"def collect_{token}_rows():\n    return []\n",
        encoding="utf-8",
    )
    assert any(
        "forbidden alternate-runtime identifier" in item
        for item in violations(tmp_path)
    )


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
