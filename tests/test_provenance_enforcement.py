"""Tests for provenance registry enforcement. §P9.7 / INV-13."""
import ast
import time
from pathlib import Path

import pytest

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

from src.contracts.provenance_registry import (
    ProvenanceRecord,
    REGISTRY,
    UnregisteredConstantError,
    require_provenance,
    register_emergency_bypass,
    _emergency_bypasses,
    _BYPASS_TTL_SECONDS,
)

ZEUS_ROOT = Path(__file__).parent.parent
REGISTRY_YAML = ZEUS_ROOT / "config" / "provenance_registry.yaml"
KELLY_PY = ZEUS_ROOT / "src" / "strategy" / "kelly.py"
MARKET_FUSION_PY = ZEUS_ROOT / "src" / "strategy" / "market_fusion.py"


def _load_registry_yaml() -> dict:
    if not REGISTRY_YAML.exists() or not HAS_YAML:
        return {}
    with REGISTRY_YAML.open() as f:
        data = yaml.safe_load(f) or {}
    # Registry YAML uses a list under "constants:" key
    constants = data.get("constants", [])
    return {entry["constant_name"]: entry for entry in constants if "constant_name" in entry}


class TestAllStrategyConstantsRegistered:
    """Every constant in kelly.py and market_fusion.py appears in provenance_registry.yaml."""

    @pytest.mark.skipif(not REGISTRY_YAML.exists(), reason="Registry YAML not yet created")
    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_kelly_constants_in_registry(self):
        """Core Kelly sizing constant appears in registry."""
        registry = _load_registry_yaml()
        assert "kelly_mult" in registry, (
            "kelly_mult not found in provenance_registry.yaml. "
            "Add a ProvenanceRecord for it."
        )

    @pytest.mark.skipif(not REGISTRY_YAML.exists(), reason="Registry YAML not yet created")
    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_market_fusion_constants_in_registry(self):
        """TAIL_ALPHA_SCALE appears in registry under its namespaced key."""
        registry = _load_registry_yaml()
        # Registry uses namespaced keys: market_fusion.TAIL_ALPHA_SCALE
        assert "market_fusion.TAIL_ALPHA_SCALE" in registry, (
            f"market_fusion.TAIL_ALPHA_SCALE not found in provenance_registry.yaml. "
            f"Keys present: {sorted(k for k in registry if 'fusion' in k or 'alpha' in k.lower())}"
        )

    @pytest.mark.skipif(not REGISTRY_YAML.exists(), reason="Registry YAML not yet created")
    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_registry_yaml_structure_when_present(self):
        """Every entry has required fields."""
        registry = _load_registry_yaml()
        required_fields = {"declared_target", "data_basis", "validated_at", "replacement_criteria"}
        for name, record in registry.items():
            if isinstance(record, dict):
                missing = required_fields - set(record.keys())
                assert not missing, f"Registry entry '{name}' missing fields: {missing}"

    @pytest.mark.skipif(not REGISTRY_YAML.exists(), reason="Registry YAML not yet created")
    @pytest.mark.skipif(not HAS_YAML, reason="PyYAML not installed")
    def test_in_memory_registry_populated(self):
        """REGISTRY loaded at module import contains expected keys."""
        assert "kelly_mult" in REGISTRY, "In-memory REGISTRY missing kelly_mult"
        assert "market_fusion.TAIL_ALPHA_SCALE" in REGISTRY, (
            "In-memory REGISTRY missing market_fusion.TAIL_ALPHA_SCALE"
        )


class TestUnregisteredConstantRaises:
    """Unregistered constant in a provenance-gated call raises UnregisteredConstantError."""

    def test_unregistered_constant_raises(self):
        """require_provenance() raises for a name not in REGISTRY."""
        with pytest.raises(UnregisteredConstantError, match="not registered"):
            require_provenance("MYSTERY_SCALE_0_42_NOTREAL")

    def test_registered_constant_does_not_raise(self):
        """A constant present in REGISTRY passes require_provenance()."""
        # kelly_mult is in the registry from the YAML
        record = require_provenance("kelly_mult")
        assert record is not None
        assert record.constant_name == "kelly_mult"

    def test_no_provenance_flag_skips_check(self):
        """requires_provenance=False allows unregistered constants through."""
        result = require_provenance("UNREGISTERED_BUT_EXEMPTED", requires_provenance=False)
        assert result is None  # Returns None when check skipped

    def test_error_message_names_the_constant(self):
        """UnregisteredConstantError message includes the constant name."""
        with pytest.raises(UnregisteredConstantError) as exc_info:
            require_provenance("CLEARLY_MISSING_CONSTANT_XYZ")
        assert "CLEARLY_MISSING_CONSTANT_XYZ" in str(exc_info.value)

    def test_tail_alpha_scale_passes_require_provenance(self):
        """market_fusion.TAIL_ALPHA_SCALE is registered and passes the check."""
        record = require_provenance("market_fusion.TAIL_ALPHA_SCALE")
        assert record is not None
        assert record.declared_target in {"brier_score", "ev", "risk_cap", "physical_constraint"}


class TestEmergencyBypassLogsAndExpires:
    """Emergency bypass works but auto-expires after 7 days."""

    def setup_method(self):
        """Clean up any test bypasses before each test."""
        for key in list(_emergency_bypasses.keys()):
            if key.startswith("TEST_BYPASS_"):
                del _emergency_bypasses[key]

    def teardown_method(self):
        """Clean up test bypasses after each test."""
        for key in list(_emergency_bypasses.keys()):
            if key.startswith("TEST_BYPASS_"):
                del _emergency_bypasses[key]

    def test_bypass_allows_unregistered_constant(self):
        """After register_emergency_bypass(), require_provenance() passes."""
        name = "TEST_BYPASS_ALLOWED_001"
        register_emergency_bypass(name)
        result = require_provenance(name)
        assert result is None  # Bypass active, returns None

    def test_bypass_emits_warning(self, caplog):
        """register_emergency_bypass() emits WARNING level log."""
        import logging
        name = "TEST_BYPASS_LOG_001"
        with caplog.at_level(logging.WARNING):
            register_emergency_bypass(name)
        assert any(
            "bypass" in r.message.lower() or "emergency" in r.message.lower()
            for r in caplog.records
        ), "register_emergency_bypass must emit a warning"

    def test_bypass_active_allows_require_provenance(self, caplog):
        """require_provenance() with active bypass emits warning and returns None."""
        import logging
        name = "TEST_BYPASS_ACTIVE_001"
        register_emergency_bypass(name)
        with caplog.at_level(logging.WARNING):
            result = require_provenance(name)
        assert result is None
        # Second warning: require_provenance logs the active bypass
        assert any("bypass" in r.message.lower() for r in caplog.records)

    def test_expired_bypass_raises(self):
        """An expired bypass no longer prevents UnregisteredConstantError."""
        name = "TEST_BYPASS_EXPIRED_001"
        register_emergency_bypass(name)
        # Force expire by backdating
        _emergency_bypasses[name] = time.time() - 1  # already expired
        with pytest.raises(UnregisteredConstantError):
            require_provenance(name)

    def test_bypass_ttl_is_7_days(self):
        """Bypass TTL is exactly 7 days (604800 seconds)."""
        assert _BYPASS_TTL_SECONDS == 7 * 24 * 3600

    def test_bypass_expiry_is_7_days_from_registration(self):
        """Registered bypass expires ~7 days from now."""
        name = "TEST_BYPASS_TTL_001"
        before = time.time()
        register_emergency_bypass(name)
        after = time.time()
        expiry = _emergency_bypasses.get(name)
        assert expiry is not None
        assert before + _BYPASS_TTL_SECONDS <= expiry <= after + _BYPASS_TTL_SECONDS + 1


# ---------------------------------------------------------------------------
# B009 relationship tests: per-entry YAML parse isolation (SD-B)
# ---------------------------------------------------------------------------

class TestB009YamlPerEntryIsolation:
    """_load_registry must not discard the entire registry on a single
    malformed entry. One bad row should log and skip; good rows survive.
    """

    def test_b009_single_bad_entry_does_not_poison_registry(self, tmp_path):
        """A YAML with 2 good entries and 1 missing-key entry should
        return a registry with 2 good records (not empty/degraded)."""
        from src.contracts.provenance_registry import _load_registry
        yaml_text = """
constants:
  - constant_name: good_alpha
    file_location: src/strategy/kelly.py
    declared_target: risk_cap
    data_basis: "calibration backtest 2026-03"
    validated_at: "2026-03-15"
    replacement_criteria: "recalibrate on Platt refit"
  - constant_name: bad_beta
    # missing file_location, declared_target, etc. — should be skipped
    data_basis: "partial"
  - constant_name: good_gamma
    file_location: src/strategy/market_fusion.py
    declared_target: ev
    data_basis: "ensemble spread study 2026-03"
    validated_at: "2026-03-20"
    replacement_criteria: "re-fit on tail recalibration"
"""
        f = tmp_path / "provenance_registry.yaml"
        f.write_text(yaml_text)

        registry, degraded = _load_registry(f)

        # Degraded flag False because the STRUCTURE was fine; just one
        # entry was bad. Registry has the two good ones.
        assert degraded is False
        assert "good_alpha" in registry
        assert "good_gamma" in registry
        assert "bad_beta" not in registry
        assert len(registry) == 2

    def test_b009_all_bad_entries_still_returns_empty_but_not_broken(self, tmp_path):
        """If every entry is malformed, registry is empty; loader does
        not crash."""
        from src.contracts.provenance_registry import _load_registry
        yaml_text = """
constants:
  - constant_name: bad_one
  - data_basis: "no constant_name at all"
"""
        f = tmp_path / "provenance_registry.yaml"
        f.write_text(yaml_text)
        registry, degraded = _load_registry(f)
        assert registry == {}
        # Loader did not raise — per-entry skip is the path.

    def test_b009_structural_yaml_failure_still_degrades_whole_file(self, tmp_path):
        """Malformed YAML at top level (not parseable) still degrades
        to empty+degraded=True. Per-entry isolation only applies once
        the top-level ``constants`` list is iterable."""
        from src.contracts.provenance_registry import _load_registry
        f = tmp_path / "provenance_registry.yaml"
        f.write_text("constants: [unclosed: {list\n")
        registry, degraded = _load_registry(f)
        assert registry == {}
        assert degraded is True
