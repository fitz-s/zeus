# The committed hero SVGs are the repository's most-screenshotted claim surface.
# This test pins them to scripts/gen_diagram.py::render so the showcase layer can
# never drift from the generator, and pins the generator itself to current law:
# stale mechanism copy (retired sizing/calibration/reconciliation claims) must
# never reappear in either theme.

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("gen_diagram", ROOT / "scripts" / "gen_diagram.py")
gen_diagram = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_diagram)

STALE_PHRASES = [
    "de-bias",                # RAW centers on the live path; eb_bias has no live caller
    "emp. Bayes",
    "empirical-Bayes",
    "fractional Kelly",
    "skill outcomes only",
    "a lucky win teaches nothing",
    "hourly chain reconcile",
    "hourly",
    "re-fit per-source bias",
    "history sets trust + width",   # predictive width is current-cycle evidence, not history
    "attribution explains",         # attribution classifies; causal "explains" overclaims
]

REQUIRED_PHRASES = [
    "raw current centers",
    "current-cycle evidence width",
    "posterior-mean action q",
    "robust log-wealth argmax",
    "frozen-q decisions graded; missing certificates stay counted",
    "ALL eligible frozen",
    "attribution classifies",
    "per-cycle chain reconciliation",
]

THEMES = sorted(gen_diagram.THEMES)


@pytest.mark.parametrize("theme", THEMES)
def test_committed_svg_matches_render(theme):
    committed = (ROOT / "docs" / f"architecture-{theme}.svg").read_text()
    assert committed == gen_diagram.render(theme), (
        f"docs/architecture-{theme}.svg drifted from render({theme!r}); "
        f"run python3 scripts/gen_diagram.py"
    )


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("phrase", STALE_PHRASES)
def test_stale_phrase_absent(theme, phrase):
    assert phrase.lower() not in gen_diagram.render(theme).lower()


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("phrase", REQUIRED_PHRASES)
def test_required_phrase_present(theme, phrase):
    assert phrase in gen_diagram.render(theme)


def test_import_has_no_side_effects():
    # Importing the renderer must write nothing; only main() touches the
    # filesystem, and only under docs/. Execute a fresh module instance (not
    # just a spec) so a module-level write would actually run and be caught.
    before = {p.name: p.stat().st_mtime_ns for p in (ROOT / "docs").glob("architecture-*.svg")}
    spec = importlib.util.spec_from_file_location("gen_diagram_reimport", ROOT / "scripts" / "gen_diagram.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    after = {p.name: p.stat().st_mtime_ns for p in (ROOT / "docs").glob("architecture-*.svg")}
    assert before == after
