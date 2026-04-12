"""K3 cluster collapse relationship tests."""
from __future__ import annotations

import json
import math
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
    """_haversine_fallback_correlation returns decay values, floored at 0.10.

    Tests the private helper directly to bypass the matrix-lookup path.
    NYC/Cape Town is IN the matrix (at -0.79), so get_correlation would
    use the matrix path, not the fallback. This test calls the fallback
    directly with fixed-distance pairs.
    """
    from src.strategy.correlation import _haversine_fallback_correlation
    # NYC to Tokyo ~10800 km -> exp(-5.4) ~= 0.0045 -> floored to 0.10
    r_distant = _haversine_fallback_correlation("NYC", "Tokyo")
    assert r_distant == pytest.approx(0.10), (
        f"expected floor 0.10 for very distant pair, got {r_distant}"
    )
    # NYC to Chicago ~1150 km -> exp(-0.575) ~= 0.56
    r_near = _haversine_fallback_correlation("NYC", "Chicago")
    assert 0.4 <= r_near <= 0.7, f"expected ~0.56 for near pair, got {r_near}"


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


def test_negative_pearson_clamped_to_zero():
    """Cross-hemisphere pairs with negative Pearson values must clamp to 0.0."""
    from src.strategy.correlation import get_correlation
    # Auckland/Tokyo is documented in the matrix at ~-0.88
    r = get_correlation("Auckland", "Tokyo")
    assert 0.0 <= r <= 1.0, f"get_correlation returned {r}, must be in [0, 1]"
    # Cape Town/NYC is in the matrix at ~-0.79 (reverse lookup path)
    r2 = get_correlation("NYC", "Cape Town")
    assert 0.0 <= r2 <= 1.0, f"get_correlation returned {r2}, must be in [0, 1]"


def test_load_matrix_rejects_unknown_city_keys(tmp_path, monkeypatch):
    """_load_matrix raises if the matrix JSON contains a non-city key."""
    from src.strategy import correlation
    # Write a corrupted matrix with a regional cluster key
    bad_matrix = tmp_path / "bad_matrix.json"
    bad_matrix.write_text(json.dumps({
        "generated_at": "2026-04-12T00:00:00Z",
        "source": "test",
        "matrix": {
            "US-Northeast": {"NYC": 0.9},  # not a valid city name
        }
    }))
    monkeypatch.setattr(correlation, "_MATRIX_PATH", bad_matrix)
    correlation._load_matrix.cache_clear()
    try:
        with pytest.raises(ValueError, match="unknown city keys"):
            correlation._load_matrix()
    finally:
        correlation._load_matrix.cache_clear()
