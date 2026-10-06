# Created: 2026-07-22
# Last reused/audited: 2026-10-06
# Authority basis: operator-directed single-live-semantics extinction pass.
"""Relapse antibodies for dormant alternate-runtime concepts."""

from __future__ import annotations

from pathlib import Path

import ast
import pytest
import yaml

from scripts.check_single_live_semantics import violations
from src.config import entry_forecast_config


def _reviewed_counterfactual_fixture(tmp_path, monkeypatch, source):
    from scripts import check_single_live_semantics as gate
    path = tmp_path / "src/engine/global_batch_runtime.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    uses = {}
    for node in ast.walk(tree):
        name = (node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                else node.arg if isinstance(node, ast.arg) else node.id if isinstance(node, ast.Name)
                else node.attr if isinstance(node, ast.Attribute) else None)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("LIVE_SHADOW"):
            name = "literal:" + node.value
        if name is not None and (name.startswith("literal:") or gate._concept_name(name)):
            uses.setdefault(name, []).append(gate._evidence_use_hash(node, parents))
    registry = tmp_path / "architecture/money_path_objects.yaml"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(yaml.safe_dump({"single_live_evidence_contexts": {
        "src/engine/global_batch_runtime.py": {
            "role": "venue_inert_same_current_q_counterfactual", "reviewed_ast_uses": uses
        }}}))
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    return path


def test_gate_typed_same_q_budget_evidence_is_not_second_runtime(tmp_path, monkeypatch):
    path = _reviewed_counterfactual_fixture(tmp_path, monkeypatch,
        "diagnostic_bytes = 128\nreport = {'trace_bytes': diagnostic_bytes}\n")
    assert violations(tmp_path) == []
    # The declaration covers only the exact reviewed use, never the whole file.
    path.write_text(path.read_text() + "opaque(diagnostic_bytes)\n")
    assert any("UNKNOWN evidence use" in item for item in violations(tmp_path))


@pytest.mark.parametrize("source", [
    "diagnostic = 'LIVE_SHADOW'\nruntime = diagnostic\n",
    "label = 'LIVE_SHADOW'\ndef relay(p):\n    return p\nmode = relay(label)\n",
    "label = 'LIVE_SHADOW'\nif runtime == label:\n    pass\n",
    "label = 'LIVE_SHADOW'\nparser.add_argument('--mode', choices=[label])\n",
    "from enum import Enum\nclass Choices(Enum):\n    VALUE = 'LIVE_SHADOW'\n",
    "diagnostic = 'LIVE_SHADOW'\nrecord = {'probability_authority': diagnostic}\n",
    "diagnostic = 'LIVE_SHADOW'\ncommand_status = diagnostic\n",
    "diagnostic = 'LIVE_SHADOW'\nrecord = {'state': diagnostic}\n",
    "diagnostic = 'LIVE_SHADOW'\nrecord = {'side': diagnostic}\n",
])
def test_gate_reviewed_owner_never_exempts_actual_selector(tmp_path, monkeypatch, source):
    _reviewed_counterfactual_fixture(tmp_path, monkeypatch, source)
    assert violations(tmp_path)


def test_gate_prose_is_not_control_but_executable_fence_is(tmp_path):
    path = tmp_path / "docs/operations/current/PLAN.md"
    path.parent.mkdir(parents=True)
    path.write_text("Historical shadow semantics and diagnostic lanes are evidence.\n"
                    "The retired trade_authority_status column is absent.\n")
    assert violations(tmp_path) == []
    path.write_text(path.read_text() + "```python\nmode = 'shadow_veto_only'\n```\n")
    assert violations(tmp_path)


@pytest.mark.parametrize("source", [
    "record={'mode':'live','report':'diagnostic'}\nmode=record['mode']\n",
    "row=('live',{'report':'diagnostic'})\nmode=row[0]\n",
    "def pick(evidence,value):\n return value\nmode=pick('diagnostic','live')\n",
    "def pick():\n def unused():\n  return 'diagnostic'\n return 'live'\nmode=pick()\n",
    "def label(value):\n return {'report':value}\ndef separate(value):\n mode=value\nlabel('diagnostic')\nseparate('live')\n",
])
def test_projected_evidence_does_not_taint_clean_selected_value(source):
    from scripts.check_single_live_semantics import _alternate_control_violations
    assert _alternate_control_violations(source) == []


@pytest.mark.parametrize("source", [
    "record={'mode':'diagnostic','report':'live'}\nmode=record['mode']\n",
    "label='diagnostic'\ndef pick():\n return label\nruntime=pick()\n",
    "def outer():\n label='diagnostic'\n def inner():\n  return label\n return inner()\nruntime=outer()\n",
    "record={'mode':'live','report':'diagnostic'}\nmode=record[key]\n",
    "record={'report':'diagnostic'}\nopaque(record)\n",
    "bag={}\nbag['value']='diagnostic'\nmode=bag['value']\n",
    "bag={'value':'live'}\nalias=bag\nalias['value']='diagnostic'\nruntime=bag['value']\n",
    "def pick(evidence,value):\n global runtime\n runtime=evidence\n return value\nmode=pick('diagnostic','live')\n",
    "def mutate(bag):\n bag['value']='diagnostic'\nbag={}\nmutate(bag)\nruntime=bag['value']\n",
    "def mutate(p,value):\n p['value']=value\nbag={}\nmutate(p=bag,value='diagnostic')\nruntime=bag['value']\n",
    "def mutate(*,p,value):\n p['value']=value\nbag={}\nalias=bag\nmutate(p=alias,value='diagnostic')\nmode=bag['value']\n",
    "def mutate(p,value):\n p['value']=value\nbag={'nested':{}}\nmutate(bag['nested'],'diagnostic')\nruntime=bag['nested']['value']\n",
    "def mutate(*,p,value):\n p['value']=value\nbag={}\nmutate(p=bag.nested,value='diagnostic')\nruntime=bag.nested['value']\n",
    "def mutate(p,value):\n p['value']=value\nmutate(p=opaque_target(),value='diagnostic')\n",
    "receipt=opaque('diagnostic')\n",
    "def report():\n return opaque('diagnostic')\nreport()\n",
    "if opaque('diagnostic'):\n pass\n",
    "flag='diagnostic'\nlane='live'\nif flag:\n lane='disabled'\n",
    "flag='diagnostic'\nif flag=='diagnostic':\n q_authority='enabled'\n",
    "flag='diagnostic'\nwhile flag:\n runtime=None\n",
    "flag='diagnostic'\nmatch flag:\n case 'diagnostic':\n  mode='live'\n",
    "def change():\n global lane\n lane='disabled'\nflag='diagnostic'\nif flag:\n change()\n",
    "def pick(label='diagnostic'):\n return label\nruntime=pick()\n",
    "def outer():\n label='live'\n def mutate():\n  nonlocal label\n  label='diagnostic'\n mutate()\n return label\nruntime=outer()\n",
    "bag={'mode':'diagnostic'}\nruntime=bag.get('mode')\n",
    "def pick():\n return 'diagnostic'\npointer=pick\nruntime=pointer()\n",
])
def test_projected_evidence_preserves_bad_field_alias_closure_and_side_effect(source):
    from scripts.check_single_live_semantics import _alternate_control_violations
    assert _alternate_control_violations(source)


def test_registered_data_effect_never_exempts_new_control_or_opaque_use(tmp_path, monkeypatch):
    from scripts import check_single_live_semantics as gate
    legal = "payload={}\npayload.update({'report':'diagnostic'})\n"
    path = _reviewed_counterfactual_fixture(tmp_path, monkeypatch, legal)
    registry = tmp_path / 'architecture/money_path_objects.yaml'
    entries = yaml.safe_load(registry.read_text())
    declaration = entries['single_live_evidence_contexts']['src/engine/global_batch_runtime.py']
    declaration['proof'] = 'bounded reviewed venue-inert report effect'
    effect = ast.parse(legal).body[1]
    declaration['reviewed_evidence_effects'] = {
        gate._evidence_use_hash(effect, {}): 'structured_same_q_report_update'}
    registry.write_text(yaml.safe_dump(entries))
    assert violations(tmp_path) == []
    for source in (
        legal + "runtime=payload['report']\n",
        legal + "mode='LIVE_SHADOW'\n",
        legal + "lane='diagnostic'\n",
        legal + "q_authority=payload['report']\n",
        legal.replace("{'report':'diagnostic'}", "{'mode':'shadow'}"),
        legal + "opaque('diagnostic')\n",
    ):
        path.write_text(source)
        assert violations(tmp_path), source


def test_keyword_mutation_retains_clean_selected_field():
    from scripts.check_single_live_semantics import _alternate_control_violations
    source = ("def mutate(*,p,value):\n p['report']=value\n"
              "bag={'mode':'live'}\nmutate(p=bag,value='diagnostic')\nmode=bag['mode']\n")
    assert _alternate_control_violations(source) == []


def test_diagnostic_predicate_report_only_has_no_control_effect():
    from scripts.check_single_live_semantics import _alternate_control_violations
    source = ("flag='diagnostic'\nif flag:\n report={'status':'done'}\n"
              " def unused():\n  runtime='disabled'\n")
    assert _alternate_control_violations(source) == []


@pytest.mark.parametrize('source', [
    "variant={'number':2.,'report':'diagnostic'}\nrecord={**variant}\nresult=sqrt(record['number'])\n",
    "import json\nr={'report':'diagnostic','number':2.}\nparsed=json.loads(json.dumps(r))\nresult=sqrt(parsed['number'])\n",
    "rows=[{'report':'diagnostic','number':2.}]\nresult=sum(row['number']**2 for row in rows)\n",
    "row='diagnostic'\nrows=[{'number':2.}]\nresult=sum(row['number'] for row in rows)\n",
])
def test_known_structure_transfers_preserve_clean_selected_values(source):
    from scripts.check_single_live_semantics import _alternate_control_violations
    assert _alternate_control_violations(source) == []


@pytest.mark.parametrize('source', [
    "variant={'number':2.,'report':'diagnostic'}\nrecord={**variant}\nmode=record['report']\n",
    "r='diagnostic'\nrecord={**r}\nmode=record['anything']\n",
    "import json\nr={'report':'diagnostic','number':2.}\nparsed=json.loads(json.dumps(r))\nmode=parsed['report']\n",
    "import json\nr={'report':'diagnostic'}\nruntime=json.dumps(r)\n",
    "import json\nr={'report':'diagnostic'}\nparsed=json.loads(json.dumps(r,default=custom))\n",
    "import json\nparsed=json.loads('diagnostic',object_hook=custom)\n",
    "import json\njson.dumps=custom\nr={'report':'diagnostic'}\nresult=json.dumps(r)\n",
    "import json\nalias=json\nalias.dumps=custom\nr={'report':'diagnostic'}\nresult=json.dumps(r)\n",
    "import json\nsetattr(json,'dumps',custom)\nr={'report':'diagnostic'}\nresult=json.dumps(r)\n",
    "rows=[{'report':'diagnostic','number':2.}]\nmode=next(row['report'] for row in rows)\n",
    "rows=[{'report':'diagnostic','number':2.}]\nmode=next('live' for row in rows if row['report'])\n",
    "rows='diagnostic'\nmode=next(row['number'] for row in rows)\n",
    "rows=[{'report':'diagnostic'}]\nresult=[opaque(row['report']) for row in rows]\n",
])
def test_structure_transfers_do_not_exempt_control_unknown_or_hooks(source):
    from scripts.check_single_live_semantics import _alternate_control_violations
    assert _alternate_control_violations(source)


def test_builtin_str_json_metadata_preserves_primitive_clock_and_center():
    from scripts.check_single_live_semantics import _alternate_control_violations
    base = ("import json\nr={'report':'diagnostic','mu':20.,'clock':'2026-10-06T01:00:00+00:00'}\n"
            "parsed=json.loads(json.dumps(r,default=str))\n")
    assert _alternate_control_violations(base + "mu=sqrt(parsed['mu'])\nclock=fromisoformat(parsed['clock'])\n") == []
    assert _alternate_control_violations(base + "mode=parsed['report']\n")


@pytest.mark.parametrize('source', [
    "import json\nr={'report':'diagnostic'}\nparsed=json.loads(json.dumps(r,default=evil))\n",
    "import json\nstr=evil\nr={'report':'diagnostic'}\nparsed=json.loads(json.dumps(r,default=str))\n",
    "import json\nclass str:\n pass\nr={'report':'diagnostic'}\nparsed=json.loads(json.dumps(r,default=str))\n",
    "import json\nclass Box:\n def __init__(self,label):\n  self.label=label\n def __str__(self):\n  global runtime\n  runtime=self.label\n  return '20'\nbox=Box('diagnostic')\nresult=json.dumps({'box':box},default=str)\n",
    "import json\nclass Box:\n def __str__(self):\n  global mode\n  mode=self.label\n  return '20'\nbox=Box(label='diagnostic')\nresult=json.dumps({'box':box},default=str)\n",
    "import json\nobj=unknown_object('diagnostic')\nresult=json.dumps({'obj':obj},default=str)\n",
])
def test_builtin_str_json_does_not_prove_hook_or_object_effects(source):
    from scripts.check_single_live_semantics import _alternate_control_violations
    assert _alternate_control_violations(source)


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


def test_gate_rejects_pure_path_into_excluded_subtree(
    tmp_path: Path,
) -> None:
    loader = tmp_path / "src" / "config_loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        "from pathlib import PurePath\n"
        "with open(PurePath('docs', 'archive', 'historical.md')) as handle:\n"
        "    config = handle.read()\n",
        encoding="utf-8",
    )
    assert any(
        item.startswith("src/config_loader.py:")
        and "consumes an excluded subtree" in item
        for item in violations(tmp_path)
    )


def test_gate_rejects_aliased_pure_path_joinpath_into_excluded_subtree(
    tmp_path: Path,
) -> None:
    loader = tmp_path / "src" / "config_loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        "from pathlib import PurePath as P\n"
        "target = P('docs').joinpath('archive', 'historical.md')\n"
        "with open(target) as handle:\n"
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


def test_entry_forecast_config_has_no_alternate_runtime_mode() -> None:
    config = entry_forecast_config()

    assert "rollout_mode" not in config.__dataclass_fields__


def test_gate_rejects_resurrected_rollout_mode(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "config.py").write_text(
        "mode = 'rollout_' + 'mode'\n",
        encoding="utf-8",
    )
    assert any(item.startswith("src/config.py:") for item in violations(tmp_path))


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
