# Batch H0b Current Diff Evidence — 2026-04-28

## Scope

H0b adds one exact digest/admission profile for the later Batch H legacy Day0-only canonical-history entry-backfill remediation, adds digest-profile regressions, updates test-topology freshness metadata, and regenerates `architecture/digest_profiles.py` from `architecture/topology.yaml`.

Non-goals: no Batch H production-source implementation yet; no `src/**` edits; no settlement/bin topology changes; no supervisor env grammar changes; no TIGGE/data-readiness/history-lore/current-fact edits; no Hong Kong WU ICAO/alias assumptions.

## Verification commands and outputs

```bash
$ python3 scripts/topology_doctor.py --navigation --task 'Batch H0 modify topology kernel add exact contamination remediation exit lifecycle backfill profile' --files architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py architecture/test_topology.yaml
navigation ok: True
profile: modify topology kernel
repo_health_warnings: 49 (34 error, 15 warning) [unrelated to this task; rerun with --issues-scope all to inspect]
excluded_lanes:
- strict: strict includes transient root/state artifact classification; run explicitly when workspace is quiescent
- scripts: script manifest can be blocked by active package scripts; run explicitly for script work
- planning_lock: requires caller-supplied --changed-files and optional --plan-evidence

# exit=0
```

```bash
$ python3 scripts/topology_doctor.py --planning-lock --changed-files architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h0b_current_diff_2026-04-28.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
{
  "ok": true,
  "issues": []
}

# exit=0
```

```bash
$ .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_batch_h_legacy_day0_backfill_routes_to_contamination_profile --no-header
.                                                                        [100%]
1 passed in 0.30s

# exit=0
```

```bash
$ .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_digest_admission_policy.py tests/test_digest_profile_matching.py tests/test_digest_regression_false_positive.py --no-header
.................................................                        [100%]
49 passed in 27.14s

# exit=0
```

```bash
$ python3 scripts/digest_profiles_export.py --check
OK: architecture/digest_profiles.py matches YAML

# exit=0
```

```bash
$ .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_digest_profiles_equivalence.py --no-header
....                                                                     [100%]
4 passed in 0.49s

# exit=0
```

```bash
$ .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_topology_doctor.py -k 'navigation or digest or admission' --no-header
.........................                                                [100%]
25 passed, 219 deselected in 10.93s

# exit=0
```

```bash
$ python3 - <<'PY'  # direct _check_schema(load_topology(), load_schema())
schema_issue_count=0
schema check passed: no topology schema issues

# exit=0
```

```bash
$ python3 scripts/topology_doctor.py --navigation --task 'Batch H legacy Day0-only canonical history entry backfill remediation' --files src/execution/exit_lifecycle.py tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
navigation ok: True
profile: batch h legacy day0 canonical history backfill remediation
repo_health_warnings: 49 (34 error, 15 warning) [unrelated to this task; rerun with --issues-scope all to inspect]
excluded_lanes:
- strict: strict includes transient root/state artifact classification; run explicitly when workspace is quiescent
- scripts: script manifest can be blocked by active package scripts; run explicitly for script work
- planning_lock: requires caller-supplied --changed-files and optional --plan-evidence

# exit=0
```

```bash
$ python3 scripts/topology_doctor.py --navigation --task 'Batch H legacy Day0-only canonical history entry backfill remediation' --files <downstream+forbidden>  # expected non-zero
navigation ok: False
profile: batch h legacy day0 canonical history backfill remediation
direct_blockers:
- [error:history_lore:history_lore_missing_antibody] architecture/history_lore.yaml:FEATURE_FLAG_DEFAULT_OFF_FOR_BEHAVIOR_RISK: critical/high active lore needs code, test, gate, or doc antibody
- [error:history_lore:history_lore_stale_antibody_reference] architecture/history_lore.yaml:DATA_COLLECTION_AND_TRADING_DAEMON_ARE_INDEPENDENT: antibodies.docs references missing path: docs/operations/task_2026-04-21_gate_f_data_backfill/step8_data_collection_decoupling.md
- [error:history_lore:history_lore_missing_antibody] architecture/history_lore.yaml:PERSISTENT_CRITIC_ROTATION_PREVENTS_RUBBER_STAMPING: critical/high active lore needs code, test, gate, or doc antibody
- [error:history_lore:history_lore_missing_antibody] architecture/history_lore.yaml:FORENSIC_DATA_AUDIT_TEMPLATE: critical/high active lore needs code, test, gate, or doc antibody
- [error:history_lore:history_lore_invalid_status] architecture/history_lore.yaml:CWA_STATION_IS_LEGACY_QUARANTINE_PATH_NOT_DEAD_CODE: invalid status 'active_knowledge'
- [error:navigation:navigation_admission_blocked] architecture/history_lore.yaml: admission status=blocked; profile=batch h legacy day0 canonical history backfill remediation; forbidden_hits=['architecture/history_lore.yaml', 'docs/authority/zeus_current_architecture.md', 'src/supervisor_api/contracts.py']
repo_health_warnings: 44 (29 error, 15 warning) [unrelated to this task; rerun with --issues-scope all to inspect]
excluded_lanes:
- strict: strict includes transient root/state artifact classification; run explicitly when workspace is quiescent
- scripts: script manifest can be blocked by active package scripts; run explicitly for script work
- planning_lock: requires caller-supplied --changed-files and optional --plan-evidence

# exit=1 (expected non-zero: forbidden/downstream negative proof)
```

```bash
$ python3 - <<'PY'  # build_digest negative/admission proofs
{
  "downstream_only": {
    "admitted": [],
    "forbidden": [],
    "out_of_scope": [
      "src/engine/lifecycle_events.py",
      "src/state/ledger.py",
      "src/engine/cycle_runtime.py",
      "tests/test_entry_exit_symmetry.py",
      "tests/test_day0_exit_gate.py"
    ],
    "profile": "batch h legacy day0 canonical history backfill remediation",
    "status": "scope_expansion_required"
  },
  "exact": {
    "admitted": [
      "src/execution/exit_lifecycle.py",
      "tests/test_runtime_guards.py",
      "architecture/test_topology.yaml",
      "docs/operations/task_2026-04-28_contamination_remediation/plan.md",
      "docs/operations/task_2026-04-28_contamination_remediation/work_log.md"
    ],
    "forbidden": [],
    "out_of_scope": [],
    "profile": "batch h legacy day0 canonical history backfill remediation",
    "status": "admitted"
  },
  "file_only_near_miss": {
    "admitted": [
      "src/execution/exit_lifecycle.py"
    ],
    "forbidden": [],
    "out_of_scope": [],
    "profile": "r3 collateral ledger implementation",
    "status": "admitted"
  },
  "forbidden": {
    "admitted": [],
    "forbidden": [
      "architecture/history_lore.yaml",
      "docs/authority/zeus_current_architecture.md",
      "src/supervisor_api/contracts.py",
      "src/contracts/settlement_semantics.py",
      "state/zeus-world.db"
    ],
    "out_of_scope": [],
    "profile": "batch h legacy day0 canonical history backfill remediation",
    "status": "blocked"
  }
}

# exit=0
```

```bash
$ python3 -m py_compile scripts/topology_doctor.py tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py architecture/digest_profiles.py

# exit=0
```

```bash
$ python3 scripts/topology_doctor.py --tests --json | python3 - <<'PY'  # filtered for H0b touched tests
topology_doctor --tests exit=1
global_issue_count=4
tests/test_digest_profile_matching.py issue_count=0
tests/test_digest_profiles_equivalence.py issue_count=0

# exit=0
```

```bash
$ git diff --check -- architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h0b_current_diff_2026-04-28.md

# exit=0
```

```bash
$ git diff -- scripts/digest_profiles_export.py src/execution/exit_lifecycle.py src/engine/lifecycle_events.py src/state/ledger.py src/engine/cycle_runtime.py src/supervisor_api/contracts.py src/contracts/settlement_semantics.py | wc -c
       0

# exit=0
```

## Current diff — H0b topology/test surfaces

```diff
diff --git a/architecture/digest_profiles.py b/architecture/digest_profiles.py
index 4995bbb..a7f17f7 100644
--- a/architecture/digest_profiles.py
+++ b/architecture/digest_profiles.py
@@ -185,23 +185,102 @@ PROFILES: list[dict] = [ { 'id': 'change settlement rounding',
                       'Generic fallback NEVER admits caller files (no-echo invariant).',
                       'Schema changes require updates to architecture/topology_schema.yaml in the same packet.'],
     'allowed_files': [ 'scripts/topology_doctor.py',
+                       'scripts/topology_doctor_docs_checks.py',
                        'scripts/topology_doctor_digest.py',
+                       'architecture/digest_profiles.py',
                        'architecture/topology.yaml',
                        'architecture/topology_schema.yaml',
                        'architecture/test_topology.yaml',
                        'tests/test_topology_doctor.py',
                        'tests/test_digest_admission_policy.py',
                        'tests/test_digest_profile_matching.py',
+                       'tests/test_digest_profiles_equivalence.py',
                        'tests/test_digest_regression_false_positive.py'],
     'forbidden_files': ['state/*.db', '.claude/worktrees/**', '.omx/**', 'docs/archives/**'],
     'gates': [ 'pytest -q tests/test_digest_admission_policy.py tests/test_digest_profile_matching.py '
                'tests/test_digest_regression_false_positive.py',
                "pytest -q tests/test_topology_doctor.py -k 'navigation or digest or admission'",
                'python3 scripts/topology_doctor.py --schema'],
-    'downstream': ['scripts/topology_doctor.py', 'tests/test_topology_doctor.py'],
+    'downstream': [ 'scripts/topology_doctor.py',
+                    'scripts/topology_doctor_docs_checks.py',
+                    'architecture/digest_profiles.py',
+                    'tests/test_topology_doctor.py',
+                    'tests/test_digest_profiles_equivalence.py'],
     'stop_conditions': [ 'Stop and plan if changes weaken forbidden-wins, no-echo, or ambiguity-detection invariants.',
                          'Stop and plan if profile resolver is allowed to admit files not declared in '
                          'profile.allowed_files.']},
+  { 'id': 'batch h legacy day0 canonical history backfill remediation',
+    'match_policy': { 'strong_phrases': [ 'Batch H legacy Day0-only canonical history',
+                                          'legacy Day0-only canonical history entry backfill',
+                                          'canonical history entry backfill remediation',
+                                          'exit_lifecycle backfill remediation'],
+                      'weak_terms': ['day0-only', 'canonical history', 'entry backfill', 'exit_lifecycle'],
+                      'negative_phrases': [ 'R3 M4',
+                                            'R3 M5',
+                                            'G1 live readiness',
+                                            'cancel/replace',
+                                            'exit safety',
+                                            'exchange reconciliation',
+                                            'live readiness',
+                                            'settlement rounding',
+                                            'settlement rebuild',
+                                            'TIGGE',
+                                            'supervisor env',
+                                            'paper env'],
+                      'single_terms_can_select': False,
+                      'min_confidence': 0.8},
+    'match': [ 'Batch H legacy Day0-only canonical history',
+               'legacy Day0-only canonical history entry backfill',
+               'canonical history entry backfill remediation',
+               'exit_lifecycle backfill remediation'],
+    'required_law': [ 'Batch H exists only to unblock a legacy Day0-only canonical-history entry-backfill remediation; '
+                      'H0b does not authorize production-source edits.',
+                      'The source implementation scope is limited to src/execution/exit_lifecycle.py and '
+                      'tests/test_runtime_guards.py unless a later critic-approved plan widens it.',
+                      'Do not mutate existing DAY0_WINDOW_ENTERED history rows; missing ENTRY_ORDER_PLACED and '
+                      'ENTRY_ORDER_FILLED rows must be appended with canonical sequence continuity.',
+                      'Do not alter settlement/bin topology, source routing/current-fact surfaces, '
+                      'TIGGE/data-readiness, supervisor env grammar, production DB/state artifacts, or Hong Kong '
+                      'source semantics.'],
+    'allowed_files': [ 'src/execution/exit_lifecycle.py',
+                       'tests/test_runtime_guards.py',
+                       'architecture/test_topology.yaml',
+                       'docs/operations/task_2026-04-28_contamination_remediation/plan.md',
+                       'docs/operations/task_2026-04-28_contamination_remediation/work_log.md',
+                       'docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h*.md'],
+    'forbidden_files': [ 'state/*.db',
+                         'state/**',
+                         '.claude/worktrees/**',
+                         '.omx/**',
+                         'docs/archives/**',
+                         'docs/authority/**',
+                         'docs/operations/current_source_validity.md',
+                         'docs/operations/current_data_state.md',
+                         'architecture/history_lore.yaml',
+                         'src/contracts/settlement_semantics.py',
+                         'src/supervisor_api/contracts.py',
+                         'src/data/**',
+                         'src/calibration/**',
+                         'scripts/rebuild_settlements.py'],
+    'gates': [ '.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py --no-header',
+               '.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py '
+               'tests/test_entry_exit_symmetry.py tests/test_day0_exit_gate.py --no-header',
+               'python3 -m py_compile src/execution/exit_lifecycle.py tests/test_runtime_guards.py',
+               'python3 scripts/topology_doctor.py --planning-lock --changed-files src/execution/exit_lifecycle.py '
+               'tests/test_runtime_guards.py architecture/test_topology.yaml '
+               'docs/operations/task_2026-04-28_contamination_remediation/plan.md '
+               'docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence '
+               'docs/operations/task_2026-04-28_contamination_remediation/plan.md --json'],
+    'downstream': [ 'src/engine/lifecycle_events.py',
+                    'src/state/ledger.py',
+                    'src/engine/cycle_runtime.py',
+                    'tests/test_entry_exit_symmetry.py',
+                    'tests/test_day0_exit_gate.py'],
+    'stop_conditions': [ 'Stop and plan if the implementation needs lifecycle builder, ledger, cycle-runtime, or '
+                         'additional test-file edits beyond the allowed list.',
+                         'Stop and plan if the fix would change canonical lifecycle grammar, settlement semantics, '
+                         'supervisor env grammar, or source-truth routing.',
+                         'Stop and plan if any Hong Kong WU ICAO/alias assumption appears; Hong Kong has no WU ICAO.']},
   { 'id': 'r3 live readiness gates implementation',
     'match': [ 'G1 live readiness gates',
                'R3 G1',
diff --git a/architecture/test_topology.yaml b/architecture/test_topology.yaml
index 22b8b73..4cc0515 100644
--- a/architecture/test_topology.yaml
+++ b/architecture/test_topology.yaml
@@ -104,7 +104,8 @@ test_trust_policy:
     tests/test_topology_doctor.py: {created: "2026-04-13", last_used: "2026-04-28"}
     tests/test_truth_surface_health.py: {created: "2026-04-07", last_used: "2026-04-25"}
     tests/test_digest_admission_policy.py: {created: "2026-04-25", last_used: "2026-04-25"}
-    tests/test_digest_profile_matching.py: {created: "2026-04-25", last_used: "2026-04-27"}
+    tests/test_digest_profile_matching.py: {created: "2026-04-25", last_used: "2026-04-28"}
+    tests/test_digest_profiles_equivalence.py: {created: "2026-04-28", last_used: "2026-04-28"}
     tests/test_digest_regression_false_positive.py: {created: "2026-04-25", last_used: "2026-04-25"}
     tests/test_admission_kernel_hardening.py: {created: "2026-04-25", last_used: "2026-04-25"}
     tests/test_decision_evidence_runtime_invocation.py: {created: "2026-04-23", last_used: "2026-04-23"}
@@ -247,6 +248,7 @@ categories:
     - tests/test_topology_doctor.py
     - tests/test_digest_admission_policy.py
     - tests/test_digest_profile_matching.py
+    - tests/test_digest_profiles_equivalence.py
     - tests/test_digest_regression_false_positive.py
     - tests/test_admission_kernel_hardening.py
     - tests/test_truth_surface_health.py
diff --git a/architecture/topology.yaml b/architecture/topology.yaml
index 45e99da..4f1cbed 100644
--- a/architecture/topology.yaml
+++ b/architecture/topology.yaml
@@ -921,13 +921,16 @@ digest_profiles:
       - "Schema changes require updates to architecture/topology_schema.yaml in the same packet."
     allowed_files:
       - "scripts/topology_doctor.py"
+      - "scripts/topology_doctor_docs_checks.py"
       - "scripts/topology_doctor_digest.py"
+      - "architecture/digest_profiles.py"
       - "architecture/topology.yaml"
       - "architecture/topology_schema.yaml"
       - "architecture/test_topology.yaml"
       - "tests/test_topology_doctor.py"
       - "tests/test_digest_admission_policy.py"
       - "tests/test_digest_profile_matching.py"
+      - "tests/test_digest_profiles_equivalence.py"
       - "tests/test_digest_regression_false_positive.py"
     forbidden_files:
       - "state/*.db"
@@ -940,10 +943,87 @@ digest_profiles:
       - "python3 scripts/topology_doctor.py --schema"
     downstream:
       - "scripts/topology_doctor.py"
+      - "scripts/topology_doctor_docs_checks.py"
+      - "architecture/digest_profiles.py"
       - "tests/test_topology_doctor.py"
+      - "tests/test_digest_profiles_equivalence.py"
     stop_conditions:
       - "Stop and plan if changes weaken forbidden-wins, no-echo, or ambiguity-detection invariants."
       - "Stop and plan if profile resolver is allowed to admit files not declared in profile.allowed_files."
+  - id: "batch h legacy day0 canonical history backfill remediation"
+    match_policy:
+      strong_phrases:
+        - "Batch H legacy Day0-only canonical history"
+        - "legacy Day0-only canonical history entry backfill"
+        - "canonical history entry backfill remediation"
+        - "exit_lifecycle backfill remediation"
+      weak_terms:
+        - "day0-only"
+        - "canonical history"
+        - "entry backfill"
+        - "exit_lifecycle"
+      negative_phrases:
+        - "R3 M4"
+        - "R3 M5"
+        - "G1 live readiness"
+        - "cancel/replace"
+        - "exit safety"
+        - "exchange reconciliation"
+        - "live readiness"
+        - "settlement rounding"
+        - "settlement rebuild"
+        - "TIGGE"
+        - "supervisor env"
+        - "paper env"
+      single_terms_can_select: false
+      min_confidence: 0.8
+    match:
+      - "Batch H legacy Day0-only canonical history"
+      - "legacy Day0-only canonical history entry backfill"
+      - "canonical history entry backfill remediation"
+      - "exit_lifecycle backfill remediation"
+    required_law:
+      - "Batch H exists only to unblock a legacy Day0-only canonical-history entry-backfill remediation; H0b does not authorize production-source edits."
+      - "The source implementation scope is limited to src/execution/exit_lifecycle.py and tests/test_runtime_guards.py unless a later critic-approved plan widens it."
+      - "Do not mutate existing DAY0_WINDOW_ENTERED history rows; missing ENTRY_ORDER_PLACED and ENTRY_ORDER_FILLED rows must be appended with canonical sequence continuity."
+      - "Do not alter settlement/bin topology, source routing/current-fact surfaces, TIGGE/data-readiness, supervisor env grammar, production DB/state artifacts, or Hong Kong source semantics."
+    allowed_files:
+      - "src/execution/exit_lifecycle.py"
+      - "tests/test_runtime_guards.py"
+      - "architecture/test_topology.yaml"
+      - "docs/operations/task_2026-04-28_contamination_remediation/plan.md"
+      - "docs/operations/task_2026-04-28_contamination_remediation/work_log.md"
+      - "docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h*.md"
+    forbidden_files:
+      - "state/*.db"
+      - "state/**"
+      - ".claude/worktrees/**"
+      - ".omx/**"
+      - "docs/archives/**"
+      - "docs/authority/**"
+      - "docs/operations/current_source_validity.md"
+      - "docs/operations/current_data_state.md"
+      - "architecture/history_lore.yaml"
+      - "src/contracts/settlement_semantics.py"
+      - "src/supervisor_api/contracts.py"
+      - "src/data/**"
+      - "src/calibration/**"
+      - "scripts/rebuild_settlements.py"
+    gates:
+      - ".venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py --no-header"
+      - ".venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py tests/test_entry_exit_symmetry.py tests/test_day0_exit_gate.py --no-header"
+      - "python3 -m py_compile src/execution/exit_lifecycle.py tests/test_runtime_guards.py"
+      - "python3 scripts/topology_doctor.py --planning-lock --changed-files src/execution/exit_lifecycle.py tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json"
+    downstream:
+      - "src/engine/lifecycle_events.py"
+      - "src/state/ledger.py"
+      - "src/engine/cycle_runtime.py"
+      - "tests/test_entry_exit_symmetry.py"
+      - "tests/test_day0_exit_gate.py"
+    stop_conditions:
+      - "Stop and plan if the implementation needs lifecycle builder, ledger, cycle-runtime, or additional test-file edits beyond the allowed list."
+      - "Stop and plan if the fix would change canonical lifecycle grammar, settlement semantics, supervisor env grammar, or source-truth routing."
+      - "Stop and plan if any Hong Kong WU ICAO/alias assumption appears; Hong Kong has no WU ICAO."
   - id: "r3 live readiness gates implementation"
     match:
       - "G1 live readiness gates"
diff --git a/tests/test_digest_profile_matching.py b/tests/test_digest_profile_matching.py
index 94be73e..5cd0df3 100644
--- a/tests/test_digest_profile_matching.py
+++ b/tests/test_digest_profile_matching.py
@@ -7,7 +7,7 @@ cannot collide with safety-critical profiles like "modify data ingestion".

 These cases come directly from §15 of docs/reference/Zeus_Apr25_review.md.
 """
-# Lifecycle: created=2026-04-25; last_reviewed=2026-04-27; last_reused=2026-04-27
+# Lifecycle: created=2026-04-25; last_reviewed=2026-04-28; last_reused=2026-04-28
 # Purpose: Lock the new word-boundary + denylist + veto profile resolver against
 # regression to the legacy substring matcher.
 # Reuse: When adding a new profile, add adversarial cases here first.
@@ -445,6 +445,90 @@ def test_r3_g1_live_readiness_routes_to_g1_profile_not_heartbeat():
     assert "tests/test_live_readiness_gates.py" in digest["admission"]["admitted_files"]


+def test_batch_h_legacy_day0_backfill_routes_to_contamination_profile():
+    """The contamination remediation Batch H profile must beat broad R3
+    file-pattern profiles and admit only the planned implementation surfaces."""
+    digest = build_digest(
+        "Batch H legacy Day0-only canonical history entry backfill remediation",
+        [
+            "src/execution/exit_lifecycle.py",
+            "tests/test_runtime_guards.py",
+            "architecture/test_topology.yaml",
+            "docs/operations/task_2026-04-28_contamination_remediation/plan.md",
+            "docs/operations/task_2026-04-28_contamination_remediation/work_log.md",
+            "docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h_current_diff_2026-04-28.md",
+        ],
+    )
+
+    assert digest["profile"] == "batch h legacy day0 canonical history backfill remediation"
+    assert digest["profile"] not in {
+        "r3 live readiness gates implementation",
+        "r3 cancel replace exit safety implementation",
+        "r3 exchange reconciliation sweep implementation",
+    }
+    assert digest["admission"]["status"] == "admitted"
+    assert "src/execution/exit_lifecycle.py" in digest["admission"]["admitted_files"]
+    assert "tests/test_runtime_guards.py" in digest["admission"]["admitted_files"]
+    assert digest["admission"]["out_of_scope_files"] == []
+
+
+def test_batch_h_profile_does_not_select_from_exit_lifecycle_file_alone():
+    """File evidence alone must not route to the Batch H contamination profile."""
+    digest = build_digest(
+        "fix exit_lifecycle backfill bug",
+        ["src/execution/exit_lifecycle.py"],
+    )
+
+    assert digest["profile"] != "batch h legacy day0 canonical history backfill remediation"
+
+
+def test_batch_h_downstream_files_remain_context_only():
+    digest = build_digest(
+        "Batch H legacy Day0-only canonical history entry backfill remediation",
+        [
+            "src/engine/lifecycle_events.py",
+            "src/state/ledger.py",
+            "src/engine/cycle_runtime.py",
+            "tests/test_entry_exit_symmetry.py",
+            "tests/test_day0_exit_gate.py",
+        ],
+    )
+
+    assert digest["profile"] == "batch h legacy day0 canonical history backfill remediation"
+    assert digest["admission"]["status"] == "scope_expansion_required"
+    assert digest["admission"]["admitted_files"] == []
+    assert set(digest["admission"]["out_of_scope_files"]) == {
+        "src/engine/lifecycle_events.py",
+        "src/state/ledger.py",
+        "src/engine/cycle_runtime.py",
+        "tests/test_entry_exit_symmetry.py",
+        "tests/test_day0_exit_gate.py",
+    }
+
+
+def test_batch_h_forbidden_surfaces_are_blocked():
+    digest = build_digest(
+        "Batch H legacy Day0-only canonical history entry backfill remediation",
+        [
+            "architecture/history_lore.yaml",
+            "docs/authority/zeus_current_architecture.md",
+            "src/supervisor_api/contracts.py",
+            "src/contracts/settlement_semantics.py",
+            "state/zeus-world.db",
+        ],
+    )
+
+    assert digest["profile"] == "batch h legacy day0 canonical history backfill remediation"
+    assert digest["admission"]["status"] == "blocked"
+    assert set(digest["admission"]["forbidden_hits"]) == {
+        "architecture/history_lore.yaml",
+        "docs/authority/zeus_current_architecture.md",
+        "src/supervisor_api/contracts.py",
+        "src/contracts/settlement_semantics.py",
+        "state/zeus-world.db",
+    }
+
+
 # ---------------------------------------------------------------------------
 # Ambiguity surface: when two profiles match equally, status reflects it.
 # ---------------------------------------------------------------------------
```

## Packet docs/evidence status note

```bash
$ git status --short -- <H0b packet docs/evidence>
?? docs/operations/task_2026-04-28_contamination_remediation/plan.md
?? docs/operations/task_2026-04-28_contamination_remediation/work_log.md
```

## Notes

- The production-scope navigation proof intentionally omits a not-yet-created future Batch H evidence file; once created, the profile admits `batch_h*.md` evidence under the packet's critic-harness folder.
- `scripts/topology_doctor_docs_checks.py` remains a prior Batch F/H0a mirror carryover, not a new H0b profile surface.
- Protected production/exporter/settlement/supervisor diff byte count remains 0; H0b did not implement Batch H source behavior.

## 2026-04-28 profile-law erratum after Batch H post-edit critic

Batch H post-edit critic found that the H0b machine-readable profile law still used invented `ENTRY_ORDER_PLACED` wording. The runtime source did not use that invented event, but the topology profile is an authority/admission surface, so this evidence is updated with the correction.

Fix applied:

- `architecture/topology.yaml`: Batch H `required_law` now names only `POSITION_OPEN_INTENT`, `ENTRY_ORDER_POSTED`, and `ENTRY_ORDER_FILLED`.
- `architecture/digest_profiles.py`: regenerated with `python3 scripts/digest_profiles_export.py`; no hand edit.
- `tests/test_digest_profile_matching.py`: added `test_batch_h_profile_law_names_real_canonical_entry_events_only`.

Verification after erratum:

```text
python3 scripts/topology_doctor.py --navigation --task "modify topology kernel correct Batch H profile required_law canonical event names and regenerate digest profile mirror" --files architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profile_matching.py architecture/test_topology.yaml
=> navigation ok: True; profile: modify topology kernel

python3 scripts/topology_doctor.py --planning-lock --changed-files architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profile_matching.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h0b_current_diff_2026-04-28.md docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h_current_diff_2026-04-28.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> {"ok": true, "issues": []}

python3 scripts/digest_profiles_export.py --check
=> OK: architecture/digest_profiles.py matches YAML

pytest focused Batch H profile tests including law regression
=> 5 passed

pytest tests/test_digest_profile_matching.py
=> 30 passed

pytest tests/test_digest_profiles_equivalence.py
=> 4 passed

pytest tests/test_topology_doctor.py -k 'navigation or digest or admission'
=> 25 passed, 219 deselected

direct topology schema check
=> issue_count: 0

python3 scripts/topology_doctor.py --navigation --task "Batch H legacy Day0-only canonical history entry backfill remediation" --files src/execution/exit_lifecycle.py tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h_current_diff_2026-04-28.md
=> navigation ok: True; profile: batch h legacy day0 canonical history backfill remediation

negative forbidden/downstream navigation
=> non-zero; forbidden surfaces blocked as expected

python3 -m py_compile architecture/digest_profiles.py tests/test_digest_profile_matching.py
=> passed

rg ENTRY_ORDER_PLACED architecture/topology.yaml architecture/digest_profiles.py
=> no matches
```

Relevant erratum diff excerpt:

```diff
- Do not mutate existing DAY0_WINDOW_ENTERED history rows; missing ENTRY_ORDER_PLACED and ENTRY_ORDER_FILLED rows must be appended with canonical sequence continuity.
+ Do not mutate existing DAY0_WINDOW_ENTERED history rows; missing POSITION_OPEN_INTENT, ENTRY_ORDER_POSTED, and ENTRY_ORDER_FILLED rows must be appended with canonical sequence continuity.
```
