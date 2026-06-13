# Created: 2026-06-12
# Authority basis: docs/superpowers/specs/2026-06-12-codegraph-topology-overhaul-design.md (Component 3)
import importlib.util
from pathlib import Path

_MOD_PATH = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "codegraph_context_inject.py"
_spec = importlib.util.spec_from_file_location("codegraph_context_inject", _MOD_PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_emits_banner_and_context_on_code_task(monkeypatch):
    monkeypatch.setattr(mod, "_run_codegraph_context", lambda p: (True, "evaluator.py:42 score_edge()"))
    out = mod._run_advisory_check_codegraph_context_inject(
        {"hook_event_name": "UserPromptSubmit", "prompt": "fix the edge calc in evaluator.py"}
    )
    assert out is not None
    assert "codegraph" in out.lower()
    assert "evaluator.py:42" in out
    assert "FIRST" in out or "before grep" in out.lower()


def test_no_emit_on_pure_non_code_prompt(monkeypatch):
    monkeypatch.setattr(mod, "_run_codegraph_context", lambda p: (True, "should not be called"))
    out = mod._run_advisory_check_codegraph_context_inject(
        {"hook_event_name": "UserPromptSubmit", "prompt": "thanks, that looks great!"}
    )
    assert out is None


def test_fail_open_index_missing(monkeypatch):
    monkeypatch.setattr(mod, "_run_codegraph_context", lambda p: (False, "Not initialized"))
    out = mod._run_advisory_check_codegraph_context_inject(
        {"hook_event_name": "UserPromptSubmit", "prompt": "refactor the executor daemon"}
    )
    assert out is not None
    assert "codegraph init -i" in out


def test_review_prompt_also_surfaces_code_review_graph(monkeypatch):
    monkeypatch.setattr(mod, "_run_codegraph_context", lambda p: (True, "x.py:1 f()"))
    out = mod._run_advisory_check_codegraph_context_inject(
        {"hook_event_name": "UserPromptSubmit", "prompt": "review this PR diff for blast radius"}
    )
    assert out is not None
    assert "code-review-graph" in out.lower()


def test_default_on_no_recent_call_suppression(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "_run_codegraph_context", lambda p: (calls.append(p), (True, "a.py:1 g()"))[1])
    out = mod._run_advisory_check_codegraph_context_inject(
        {"hook_event_name": "UserPromptSubmit", "prompt": "where is place_limit_order defined"}
    )
    assert out is not None
    assert len(calls) == 1
