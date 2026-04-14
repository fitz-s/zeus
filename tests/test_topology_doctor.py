from scripts import topology_doctor


def test_topology_strict_passes_after_residual_classification():
    result = topology_doctor.run_strict()

    assert result.ok
    assert result.issues == []


def test_topology_docs_mode_passes_with_active_data_package_excluded():
    result = topology_doctor.run_docs()

    assert result.ok
    assert result.issues == []


def test_topology_source_mode_covers_all_tracked_src_files():
    result = topology_doctor.run_source()

    assert result.ok
    assert result.issues == []


def test_topology_tests_mode_classifies_actual_suite_and_law_gate():
    result = topology_doctor.run_tests()

    assert result.ok
    assert result.issues == []


def test_tests_mode_checks_relationship_manifest_symbols(monkeypatch):
    topology = topology_doctor.load_test_topology()
    topology["relationship_test_manifests"][0] = {
        **topology["relationship_test_manifests"][0],
        "required_symbols": ["MISSING_RELATIONSHIP_SYMBOL"],
    }

    monkeypatch.setattr(topology_doctor, "load_test_topology", lambda: topology)
    result = topology_doctor.run_tests()

    assert not result.ok
    assert any(issue.code == "test_relationship_manifest_missing_symbol" for issue in result.issues)


def test_settlement_rounding_digest_names_wmo_law_and_gates():
    digest = topology_doctor.build_digest(
        "change settlement rounding",
        ["src/contracts/settlement_semantics.py"],
    )
    joined = "\n".join(str(item) for values in digest.values() if isinstance(values, list) for item in values)

    assert digest["profile"] == "change settlement rounding"
    assert "floor(x + 0.5)" in joined
    assert "src/contracts/settlement_semantics.py" in digest["allowed_files"]
    assert "state/*.db" in digest["forbidden_files"]
    assert any("test_instrument_invariants.py" in gate for gate in digest["gates"])
    assert "src/engine/replay.py" in digest["downstream"]
    assert digest["source_rationale"][0]["path"] == "src/contracts/settlement_semantics.py"
    assert digest["source_rationale"][0]["authority_role"] == "settlement_rounding_law"


def test_replay_fidelity_digest_names_non_promotion_and_point_in_time_truth():
    digest = topology_doctor.build_digest("edit replay fidelity")
    joined = "\n".join(str(item) for values in digest.values() if isinstance(values, list) for item in values)

    assert digest["profile"] == "edit replay fidelity"
    assert "diagnostic_non_promotion" in joined
    assert "point-in-time" in joined
    assert any("state/zeus_backtest.db" in item for item in digest["downstream"])
    assert any("Do not promote" in item for item in digest["stop_conditions"])
    assert any(item["path"] == "src/engine/replay.py" for item in digest["source_rationale"])


def test_source_mode_rejects_known_writer_without_route(monkeypatch):
    rationale = topology_doctor.load_source_rationale()
    rationale["files"]["src/calibration/store.py"] = {
        **rationale["files"]["src/calibration/store.py"],
        "write_routes": ["script_repair_write"],
    }

    monkeypatch.setattr(topology_doctor, "load_source_rationale", lambda: rationale)
    result = topology_doctor.run_source()

    assert not result.ok
    assert any(issue.code == "source_file_write_route_missing" for issue in result.issues)


def test_source_mode_locks_derived_strategy_tracker_role(monkeypatch):
    rationale = topology_doctor.load_source_rationale()
    rationale["files"]["src/state/strategy_tracker.py"] = {
        **rationale["files"]["src/state/strategy_tracker.py"],
        "authority_role": "runtime_authority",
    }

    monkeypatch.setattr(topology_doctor, "load_source_rationale", lambda: rationale)
    result = topology_doctor.run_source()

    assert not result.ok
    assert any(issue.code == "source_file_role_mismatch" for issue in result.issues)


def test_tests_mode_rejects_active_reverse_antibody(monkeypatch):
    topology = topology_doctor.load_test_topology()
    topology["reverse_antibody_status"] = {"active": ["bad_test"], "resolved": []}

    monkeypatch.setattr(topology_doctor, "load_test_topology", lambda: topology)
    result = topology_doctor.run_tests()

    assert not result.ok
    assert any(issue.code == "test_reverse_antibody_active" for issue in result.issues)


def test_tests_mode_rejects_law_gate_test_outside_core(monkeypatch):
    topology = topology_doctor.load_test_topology()
    topology["categories"]["core_law_antibody"].remove("tests/test_fdr.py")
    topology["categories"]["useful_regression"].append("tests/test_fdr.py")

    monkeypatch.setattr(topology_doctor, "load_test_topology", lambda: topology)
    result = topology_doctor.run_tests()

    assert not result.ok
    assert any(issue.code == "test_law_gate_non_core" for issue in result.issues)


def test_tests_mode_rejects_high_sensitivity_skip_count_drift(monkeypatch):
    topology = topology_doctor.load_test_topology()
    topology["high_sensitivity_skips"]["tests/test_db.py"] = {
        **topology["high_sensitivity_skips"]["tests/test_db.py"],
        "skip_count": -1,
    }

    monkeypatch.setattr(topology_doctor, "load_test_topology", lambda: topology)
    result = topology_doctor.run_tests()

    assert not result.ok
    assert any(
        issue.code == "test_high_sensitivity_skip_count_mismatch"
        for issue in result.issues
    )


def test_topology_scripts_mode_covers_all_top_level_scripts():
    result = topology_doctor.run_scripts()

    assert result.ok
    assert result.issues == []


def test_topology_data_rebuild_mode_encodes_certification_blockers():
    result = topology_doctor.run_data_rebuild()

    assert result.ok
    assert result.issues == []


def test_topology_history_lore_mode_validates_dense_cards():
    result = topology_doctor.run_history_lore()

    assert result.ok
    assert result.issues == []


def test_topology_context_budget_mode_passes_after_entry_slimming():
    result = topology_doctor.run_context_budget()

    assert result.ok
    assert result.issues == []


def test_topology_agents_coherence_mode_matches_machine_zones():
    result = topology_doctor.run_agents_coherence()

    assert result.ok
    assert result.issues == []


def test_topology_idioms_mode_registers_non_obvious_code_shapes():
    result = topology_doctor.run_idioms()

    assert result.ok
    assert result.issues == []


def test_topology_self_check_coherence_mode_aligns_zero_context_overlay():
    result = topology_doctor.run_self_check_coherence()

    assert result.ok
    assert result.issues == []


def test_topology_runtime_modes_mode_keeps_discovery_modes_visible():
    result = topology_doctor.run_runtime_modes()

    assert result.ok
    assert result.issues == []


def test_topology_reference_replacement_mode_tracks_reference_docs():
    result = topology_doctor.run_reference_replacement()

    assert result.ok
    assert result.issues == []


def test_map_maintenance_requires_test_topology_for_new_test_file(monkeypatch):
    original_exists = topology_doctor.Path.exists
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: ["architecture/test_topology.yaml"])

    def fake_exists(self):
        if self == topology_doctor.ROOT / "tests/test_new_behavior.py":
            return True
        return original_exists(self)

    monkeypatch.setattr(topology_doctor.Path, "exists", fake_exists)
    result = topology_doctor.run_map_maintenance(["tests/test_new_behavior.py"])

    assert not result.ok
    assert any(issue.code == "map_maintenance_companion_missing" for issue in result.issues)
    assert any("architecture/test_topology.yaml" in issue.message for issue in result.issues)


def test_map_maintenance_allows_new_test_file_when_companion_present(monkeypatch):
    original_exists = topology_doctor.Path.exists
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: ["architecture/test_topology.yaml"])

    def fake_exists(self):
        if self == topology_doctor.ROOT / "tests/test_new_behavior.py":
            return True
        return original_exists(self)

    monkeypatch.setattr(topology_doctor.Path, "exists", fake_exists)
    result = topology_doctor.run_map_maintenance(
        ["tests/test_new_behavior.py", "architecture/test_topology.yaml"]
    )

    assert result.ok
    assert result.issues == []


def test_map_maintenance_does_not_require_registry_for_plain_modification(monkeypatch):
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: ["src/engine/evaluator.py"])
    result = topology_doctor.run_map_maintenance(["src/engine/evaluator.py"])

    assert result.ok
    assert result.issues == []

def test_navigation_aggregates_default_health_and_digest():
    payload = topology_doctor.run_navigation(
        "fix settlement rounding in replay",
        ["src/engine/replay.py"],
    )

    assert payload["ok"]
    assert payload["digest"]["profile"] == "change settlement rounding"
    assert payload["checks"]["context_budget"]["ok"]
    assert payload["checks"]["agents_coherence"]["ok"]
    assert payload["checks"]["self_check_coherence"]["ok"]
    assert "scripts" in payload["excluded_lanes"]
    assert "strict" in payload["excluded_lanes"]
    assert "planning_lock" in payload["excluded_lanes"]
    assert any(card["id"] == "WMO_ROUNDING_BANKER_FAILURE" for card in payload["digest"]["history_lore"])


def test_agents_coherence_rejects_prose_zone_that_lowers_manifest(monkeypatch):
    rationale = topology_doctor.load_source_rationale()
    rationale["package_defaults"]["src/observability"] = {
        **rationale["package_defaults"]["src/observability"],
        "zone": "K4_experimental",
    }

    monkeypatch.setattr(topology_doctor, "load_source_rationale", lambda: rationale)
    result = topology_doctor.run_agents_coherence()

    assert not result.ok
    assert any(issue.code == "agents_zone_mismatch" for issue in result.issues)


def test_planning_lock_requires_evidence_for_control_change():
    result = topology_doctor.run_planning_lock(["src/control/control_plane.py"])

    assert not result.ok
    assert any(issue.code == "planning_lock_required" for issue in result.issues)
    assert "changed files" in result.issues[0].message or result.issues[0].path != "<change-set>"


def test_planning_lock_uses_changed_file_count_not_read_budget():
    result = topology_doctor.run_planning_lock(
        ["src/engine/evaluator.py"] * 5,
    )

    assert not result.ok
    assert any(
        issue.path == "<change-set>" and "changed files" in issue.message
        for issue in result.issues
    )


def test_planning_lock_accepts_current_state_as_evidence():
    result = topology_doctor.run_planning_lock(
        ["src/control/control_plane.py"],
        "docs/operations/current_state.md",
    )

    assert result.ok
    assert result.issues == []


def test_idioms_mode_rejects_unregistered_semantic_guard(monkeypatch):
    manifest = topology_doctor.load_code_idioms()
    manifest["idioms"][0] = {
        **manifest["idioms"][0],
        "examples": [],
    }

    monkeypatch.setattr(topology_doctor, "load_code_idioms", lambda: manifest)
    result = topology_doctor.run_idioms()

    assert not result.ok
    assert any(issue.code == "code_idiom_unregistered_occurrence" for issue in result.issues)


def test_self_check_coherence_rejects_missing_root_reference(monkeypatch):
    original_read_text = topology_doctor.Path.read_text

    def fake_read_text(self, *args, **kwargs):
        text = original_read_text(self, *args, **kwargs)
        if self.name == "AGENTS.md" and self.parent == topology_doctor.ROOT:
            return text.replace("architecture/self_check/zero_context_entry.md", "")
        return text

    monkeypatch.setattr(topology_doctor.Path, "read_text", fake_read_text)
    result = topology_doctor.run_self_check_coherence()

    assert not result.ok
    assert any(issue.code == "self_check_root_reference_missing" for issue in result.issues)


def test_runtime_modes_rejects_missing_mode(monkeypatch):
    topology = topology_doctor.load_runtime_modes()
    topology["required_modes"]["opening_hunt"] = {
        **topology["required_modes"]["opening_hunt"],
        "enum": "MISSING_ENUM",
    }

    monkeypatch.setattr(topology_doctor, "load_runtime_modes", lambda: topology)
    result = topology_doctor.run_runtime_modes()

    assert not result.ok
    assert any(issue.code == "runtime_mode_enum_missing" for issue in result.issues)


def test_reference_replacement_rejects_unsafe_deletion(monkeypatch):
    manifest = topology_doctor.load_reference_replacement()
    manifest["entries"][0] = {
        **manifest["entries"][0],
        "delete_allowed": True,
        "replacement_status": "partial_replacement_candidate",
    }

    monkeypatch.setattr(topology_doctor, "load_reference_replacement", lambda: manifest)
    result = topology_doctor.run_reference_replacement()

    assert not result.ok
    assert any(issue.code == "reference_replacement_delete_unsafe" for issue in result.issues)


def test_reference_replacement_detects_default_read_mismatch(monkeypatch):
    manifest = topology_doctor.load_reference_replacement()
    manifest["entries"] = [
        {
            **entry,
            "default_read": True if entry["path"] == "docs/reference/repo_overview.md" else entry["default_read"],
        }
        for entry in manifest["entries"]
    ]

    monkeypatch.setattr(topology_doctor, "load_reference_replacement", lambda: manifest)
    result = topology_doctor.run_reference_replacement()

    assert not result.ok
    assert any(issue.code == "reference_replacement_default_read_mismatch" for issue in result.issues)


def test_lore_digest_routes_discovery_mode_tasks():
    digest = topology_doctor.build_digest("optimize update_reaction discovery mode")
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert "DISCOVERY_MODES_SHAPE_RUNTIME_CYCLE" in lore_ids


def test_lore_digest_routes_bin_contract_kind_tasks():
    digest = topology_doctor.build_digest("fix position calculation for open_shoulder bin")
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert "BIN_CONTRACT_KIND_DISCRETE_SETTLEMENT_SUPPORT" in lore_ids


def test_scripts_mode_rejects_diagnostic_canonical_write(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["audit_replay_fidelity.py"] = {
        **manifest["scripts"]["audit_replay_fidelity.py"],
        "write_targets": ["state/zeus-world.db"],
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_diagnostic_forbidden_write_target" for issue in result.issues)


def test_scripts_mode_rejects_dangerous_script_without_target(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["cleanup_ghost_positions.py"] = {
        **manifest["scripts"]["cleanup_ghost_positions.py"],
        "target_db": None,
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_dangerous_missing_target_db" for issue in result.issues)


def test_scripts_mode_rejects_fake_apply_flag(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["cleanup_ghost_positions.py"] = {
        **manifest["scripts"]["cleanup_ghost_positions.py"],
        "apply_flag": "--definitely-not-present",
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_dangerous_apply_flag_not_in_source" for issue in result.issues)


def test_scripts_mode_rejects_diagnostic_file_write_without_target(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["generate_monthly_bounds.py"] = {"class": "diagnostic"}

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_diagnostic_untracked_file_write" for issue in result.issues)


def test_scripts_mode_applies_diagnostic_rules_to_report_writers(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["baseline_experiment.py"] = {
        **manifest["scripts"]["baseline_experiment.py"],
        "write_targets": ["state/zeus-world.db"],
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_diagnostic_forbidden_write_target" for issue in result.issues)


def test_scripts_mode_rejects_long_lived_one_off_script_name(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["scratch_probe.py"] = {"class": "utility"}

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    monkeypatch.setattr(topology_doctor, "_top_level_scripts", lambda: set(manifest["scripts"]))
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_long_lived_one_off_name" for issue in result.issues)
    assert any(issue.code == "script_long_lived_bad_name" for issue in result.issues)


def test_scripts_mode_rejects_ephemeral_without_delete_trigger(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["task_2026-04-14_probe_replay_gap.py"] = {
        "class": "utility",
        "lifecycle": "packet_ephemeral",
        "owner_packet": "SCRIPT-LIFECYCLE",
        "created_for": "temporary replay gap inspection",
        "delete_policy": "delete_on_packet_close",
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    monkeypatch.setattr(topology_doctor, "_top_level_scripts", lambda: set(manifest["scripts"]))
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_ephemeral_delete_policy_missing" for issue in result.issues)


def test_scripts_mode_rejects_malformed_ephemeral_name(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["task_badname.py"] = {
        "class": "utility",
        "lifecycle": "packet_ephemeral",
        "owner_packet": "SCRIPT-LIFECYCLE",
        "created_for": "temporary replay gap inspection",
        "delete_policy": "delete_on_packet_close",
        "delete_on_packet_close": True,
        "delete_by": "2999-01-01",
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    monkeypatch.setattr(topology_doctor, "_top_level_scripts", lambda: set(manifest["scripts"]))
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_ephemeral_bad_name" for issue in result.issues)


def test_scripts_mode_rejects_expired_ephemeral_script(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["task_2000-01-01_probe_replay_gap.py"] = {
        "class": "utility",
        "lifecycle": "packet_ephemeral",
        "owner_packet": "SCRIPT-LIFECYCLE",
        "created_for": "temporary replay gap inspection",
        "delete_policy": "delete_on_packet_close",
        "delete_by": "2000-01-01",
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    monkeypatch.setattr(topology_doctor, "_top_level_scripts", lambda: set(manifest["scripts"]))
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_ephemeral_expired" for issue in result.issues)


def test_scripts_mode_rejects_deprecated_script_without_fail_closed_lifecycle(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["analyze_paper_trading.py"] = {
        **manifest["scripts"]["analyze_paper_trading.py"],
        "status": "deprecated",
        "lifecycle": "long_lived",
        "canonical_command": "python scripts/analyze_paper_trading.py",
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_deprecated_not_fail_closed" for issue in result.issues)


def test_data_rebuild_mode_rejects_live_certification_with_uncertified_blockers(monkeypatch):
    topology = topology_doctor.load_data_rebuild_topology()
    topology["live_math_certification"] = {
        **topology["live_math_certification"],
        "allowed": True,
    }

    monkeypatch.setattr(topology_doctor, "load_data_rebuild_topology", lambda: topology)
    result = topology_doctor.run_data_rebuild()

    assert not result.ok
    assert any(issue.code == "data_rebuild_live_math_certification_unsafe" for issue in result.issues)


def test_data_rebuild_mode_rejects_missing_or_nonboolean_certification_flag(monkeypatch):
    topology = topology_doctor.load_data_rebuild_topology()
    topology["live_math_certification"].pop("allowed")
    monkeypatch.setattr(topology_doctor, "load_data_rebuild_topology", lambda: topology)
    result = topology_doctor.run_data_rebuild()
    assert any(issue.code == "data_rebuild_certification_allowed_missing" for issue in result.issues)

    topology = topology_doctor.load_data_rebuild_topology()
    topology["live_math_certification"]["allowed"] = "false"
    monkeypatch.setattr(topology_doctor, "load_data_rebuild_topology", lambda: topology)
    result = topology_doctor.run_data_rebuild()
    assert any(issue.code == "data_rebuild_certification_allowed_invalid" for issue in result.issues)


def test_data_rebuild_mode_rejects_wu_only_strategy_coverage(monkeypatch):
    topology = topology_doctor.load_data_rebuild_topology()
    topology["replay_coverage_rule"] = {
        **topology["replay_coverage_rule"],
        "wu_settlement_sample_is_strategy_coverage": True,
    }

    monkeypatch.setattr(topology_doctor, "load_data_rebuild_topology", lambda: topology)
    result = topology_doctor.run_data_rebuild()

    assert not result.ok
    assert any(issue.code == "data_rebuild_replay_coverage_unsafe" for issue in result.issues)


def test_data_rebuild_mode_rejects_empty_row_contract(monkeypatch):
    topology = topology_doctor.load_data_rebuild_topology()
    topology["rebuilt_row_contract"]["tables"]["observations"].pop("required_fields")

    monkeypatch.setattr(topology_doctor, "load_data_rebuild_topology", lambda: topology)
    result = topology_doctor.run_data_rebuild()

    assert not result.ok
    assert any(issue.code == "data_rebuild_row_contract_missing_fields" for issue in result.issues)


def test_data_rebuild_mode_rejects_missing_non_db_promotion_targets(monkeypatch):
    topology = topology_doctor.load_data_rebuild_topology()
    topology["diagnostic_non_promotion"]["forbidden_promotions"] = [
        "state/zeus_trades.db",
        "state/zeus-world.db",
    ]

    monkeypatch.setattr(topology_doctor, "load_data_rebuild_topology", lambda: topology)
    result = topology_doctor.run_data_rebuild()

    assert not result.ok
    assert any(issue.code == "data_rebuild_non_promotion_incomplete" for issue in result.issues)


def test_data_backfill_digest_includes_row_contract_and_replay_coverage():
    digest = topology_doctor.build_digest("add a data backfill")
    data_topology = digest["data_rebuild_topology"]

    assert data_topology["live_math_certification"]["allowed"] is False
    assert "calibration_pairs" in data_topology["row_contract_tables"]
    assert "decision_group_id" in data_topology["row_contract_tables"]["calibration_pairs"]["required_fields"]
    assert "market_price_linkage" in data_topology["replay_coverage_rule"]["required_for_strategy_replay_coverage"]
    assert "calibration model activation" in data_topology["diagnostic_non_promotion"]["forbidden_promotions"]


def test_script_digest_routes_agents_to_lifecycle_law():
    digest = topology_doctor.build_digest("add a replay diagnostic script")
    script_lifecycle = digest["script_lifecycle"]

    assert digest["profile"] == "add or change script"
    assert "packet_ephemeral" in script_lifecycle["allowed_lifecycles"]
    assert "audit_" in script_lifecycle["long_lived_naming"]["allowed_prefixes"]
    assert "audit_replay_fidelity.py" in script_lifecycle["existing_scripts"]
    assert "python scripts/topology_doctor.py --scripts" in digest["gates"]
    assert any("delete_by=YYYY-MM-DD" in law for law in digest["required_law"])
    assert any(card["id"] == "SCRIPT_LIFECYCLE_REUSE_BEFORE_NEW_TOOL" for card in digest["history_lore"])


def test_lore_digest_routes_rounding_tasks_to_wmo_lesson():
    digest = topology_doctor.build_digest(
        "fix settlement rounding in replay",
        ["src/engine/replay.py"],
    )
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert "WMO_ROUNDING_BANKER_FAILURE" in lore_ids
    assert "DIAGNOSTIC_BACKTEST_NON_PROMOTION" in lore_ids
    assert "UNCOMMITTED_AGENT_EDIT_LOSS" not in lore_ids


def test_lore_digest_routes_history_tasks_to_density_policy():
    digest = topology_doctor.build_digest("extract lore from historical work packets")
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert digest["profile"] == "extract historical lore"
    assert "HISTORICAL_LORE_DENSITY_POLICY" in lore_ids
    assert any("not default reading material" in law for law in digest["required_law"])
    assert "python scripts/topology_doctor.py --history-lore" in digest["gates"]


def test_lore_digest_routes_alpha_tasks_to_profit_safety_lessons():
    digest = topology_doctor.build_digest("retune alpha tail treatment for buy_no EV")
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert "ALPHA_TARGET_AND_TAIL_TREATMENT_NOT_PROFIT_SAFE" in lore_ids
    assert "VIG_TREATMENT_RAW_PRICE_VS_CLEAN_PROBABILITY" not in lore_ids


def test_lore_digest_routes_risk_loss_tasks_to_derived_truth_warning():
    digest = topology_doctor.build_digest("fix daily_loss in risk_state reporting")
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert "STRATEGY_TRACKER_AND_ROLLING_LOSS_ARE_DERIVED_NOT_WALLET_TRUTH" in lore_ids
    assert "CANONICAL_DB_TRUTH_OUTRANKS_JSON_FALLBACK" not in lore_ids


def test_lore_digest_routes_data_rebuild_tasks_to_certification_block():
    digest = topology_doctor.build_digest("certify data rebuild for live math")
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert "DATA_REBUILD_LIVE_MATH_CERTIFICATION_BLOCKED" in lore_ids
    assert "VERIFIED_AUTHORITY_IS_CONTRACT_NOT_STAMP" in lore_ids


def test_lore_digest_does_not_overload_dst_rebuild_with_data_rebuild_lore():
    digest = topology_doctor.build_digest("fix DST diurnal rebuild")
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert digest["profile"] == "generic"
    assert "DST_DIURNAL_HISTORY_REBUILD_RISK" in lore_ids
    assert "VERIFIED_AUTHORITY_IS_CONTRACT_NOT_STAMP" not in lore_ids
    assert "EXACT_SEMANTIC_TESTS_OVER_EXISTENCE_TESTS" not in lore_ids


def test_lore_digest_routes_semantic_provenance_guard_cleanup():
    digest = topology_doctor.build_digest(
        "remove dead if False provenance guard",
        ["src/strategy/market_analysis.py"],
    )
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert "SEMANTIC_PROVENANCE_GUARD_STATIC_HOOK" in lore_ids
    card = next(card for card in digest["history_lore"] if card["id"] == "SEMANTIC_PROVENANCE_GUARD_STATIC_HOOK")
    assert any("semantic_linter.py" in gate for gate in card["antibodies"]["gates"])
    assert "static-analysis hooks" in card["zero_context_digest"]


def test_history_lore_mode_rejects_critical_card_without_antibody(monkeypatch):
    lore = topology_doctor.load_history_lore()
    lore["cards"] = [
        {
            **lore["cards"][0],
            "id": "BROKEN_LORE",
            "antibodies": {},
        }
    ]

    monkeypatch.setattr(topology_doctor, "load_history_lore", lambda: lore)
    result = topology_doctor.run_history_lore()

    assert not result.ok
    assert any(issue.code == "history_lore_missing_antibody" for issue in result.issues)


def test_context_budget_mode_can_block_when_promoted(monkeypatch):
    budget = {
        "file_budgets": [
            {
                "path": "AGENTS.md",
                "role": "boot_contract_only",
                "max_lines": 1,
                "enforcement": "blocking",
            }
        ],
        "digest_budgets": {},
        "default_read_path": {"max_pre_code_reads": 6},
    }

    monkeypatch.setattr(topology_doctor, "load_context_budget", lambda: budget)
    result = topology_doctor.run_context_budget()

    assert not result.ok
    assert any(issue.code == "context_budget_file_over" for issue in result.issues)
    assert any(issue.severity == "error" for issue in result.issues)


def test_context_budget_mode_checks_digest_card_budget(monkeypatch):
    budget = {
        "file_budgets": [],
        "digest_budgets": {
            "history_lore": {
                "max_cards_per_digest": 1,
                "max_zero_context_digest_chars": 10000,
                "enforcement": "blocking",
                "sample_tasks": ["certify data rebuild for live math"],
            }
        },
        "default_read_path": {"max_pre_code_reads": 6},
    }

    monkeypatch.setattr(topology_doctor, "load_context_budget", lambda: budget)
    result = topology_doctor.run_context_budget()

    assert not result.ok
    assert any(issue.code == "context_budget_digest_card_over" for issue in result.issues)


def test_generic_digest_includes_effective_source_rationale_for_core_file():
    digest = topology_doctor.build_digest(
        "change lifecycle manager",
        ["src/state/lifecycle_manager.py"],
    )
    rationale = digest["source_rationale"][0]

    assert rationale["zone"] == "K0_frozen_kernel"
    assert rationale["authority_role"] == "lifecycle_law"
    assert "upstream" in rationale
    assert "downstream" in rationale
    assert any("test_architecture_contracts.py" in gate for gate in rationale["gates"])
