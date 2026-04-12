"""K3 cluster collapse relationship tests."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.config import cities_by_name


REPO_ROOT = Path(__file__).parent.parent


def test_every_city_cluster_equals_name():
    """After K3, city.cluster must equal city.name for all 46 cities."""
    for name, city in cities_by_name.items():
        assert city.cluster == city.name, f"{name}: cluster={city.cluster!r}"


def test_route_to_bucket_returns_city_season_format():
    """route_to_bucket returns `{city.name}_{season}` after K3."""
    from src.calibration.manager import route_to_bucket
    paris = cities_by_name["Paris"]
    result = route_to_bucket(paris, "2026-07-15")
    assert result == "Paris_JJA", f"got {result}"


def test_risk_limits_has_no_max_region_pct():
    """max_region_pct field removed from RiskLimits dataclass."""
    from src.strategy import risk_limits
    if hasattr(risk_limits, "RiskLimits"):
        import dataclasses
        fields = {f.name for f in dataclasses.fields(risk_limits.RiskLimits)}
        assert "max_region_pct" not in fields, f"max_region_pct still present: {fields}"


def test_settings_json_has_no_correlation_matrix():
    """config/settings.json has no correlation.matrix field."""
    settings_path = REPO_ROOT / "config" / "settings.json"
    with open(settings_path) as f:
        settings = json.load(f)
    correlation = settings.get("correlation", {})
    assert "matrix" not in correlation, "correlation.matrix still in settings.json"


def test_settings_json_has_no_max_region_pct():
    """config/settings.json has no sizing.max_region_pct field."""
    settings_path = REPO_ROOT / "config" / "settings.json"
    with open(settings_path) as f:
        settings = json.load(f)
    sizing = settings.get("sizing", {})
    assert "max_region_pct" not in sizing, "sizing.max_region_pct still in settings.json"


def test_correlation_self_is_one():
    """Self-correlation is always 1.0."""
    from src.strategy.correlation import get_correlation
    assert get_correlation("NYC", "NYC") == 1.0


def test_correlation_function_returns_float_in_01():
    """get_correlation returns a float in [0, 1]."""
    from src.strategy.correlation import get_correlation
    r = get_correlation("NYC", "Tokyo")
    assert isinstance(r, float)
    assert 0.0 <= r <= 1.0


def test_haversine_fallback_decays_with_distance():
    """Cities > 5000 km apart get small correlation from haversine fallback.

    NYC to Cape Town is ~12700 km and this pair is absent from the Pearson
    matrix (build script only covered pairs with sufficient historical overlap),
    so get_correlation must use the haversine fallback.
    exp(-12700/2000) ~= 0.0017, floored to 0.05 — well under 0.5.
    """
    from src.strategy.correlation import get_correlation
    r = get_correlation("NYC", "Cape Town")
    assert r <= 0.5, f"expected small haversine fallback for NYC-Cape Town, got {r}"


def test_all_clusters_are_city_names():
    """src.config.ALL_CLUSTERS equals the set of city names."""
    from src.config import ALL_CLUSTERS
    expected = set(cities_by_name.keys())
    assert set(ALL_CLUSTERS) == expected


def test_no_regional_cluster_strings_in_src():
    """No .py file under src/ contains a hardcoded regional cluster literal.

    The semantic_linter enforces this long-term; here we do a grep as an antibody.
    """
    forbidden = [
        "US-Northeast", "US-Southeast", "US-GreatLakes", "US-Texas-Triangle",
        "Asia-Northeast", "Europe-Maritime", "Europe-Continental",
        "Oceania-Temperate", "China-Central",
    ]
    src_dir = REPO_ROOT / "src"
    violations = []
    for py in src_dir.rglob("*.py"):
        content = py.read_text()
        for needle in forbidden:
            if needle in content:
                violations.append(f"{py.relative_to(REPO_ROOT)}: {needle}")
    assert not violations, f"Regional cluster literals found: {violations}"
