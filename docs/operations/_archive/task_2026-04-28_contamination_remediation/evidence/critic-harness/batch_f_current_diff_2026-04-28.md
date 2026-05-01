# Batch F Current Diff and Verification Evidence — 2026-04-28

## Scope
- Non-history topology_doctor docs-mode synthetic visible-path regression.
- No `architecture/history_lore.yaml` remediation.
- `check_hidden_docs()` only; `check_docs_registry()` unchanged.

## Diff
diff --git a/architecture/test_topology.yaml b/architecture/test_topology.yaml
index 3c1abcb..e9d020b 100644
--- a/architecture/test_topology.yaml
+++ b/architecture/test_topology.yaml
@@ -101,7 +101,7 @@ test_trust_policy:
     tests/test_proxy_health.py: {created: "2026-04-21", last_used: "2026-04-21"}
     tests/test_semantic_linter.py: {created: "2026-04-13", last_used: "2026-04-25"}
     tests/test_tier_resolver.py: {created: "2026-04-21", last_used: "2026-04-21"}
-    tests/test_topology_doctor.py: {created: "2026-04-13", last_used: "2026-04-21"}
+    tests/test_topology_doctor.py: {created: "2026-04-13", last_used: "2026-04-28"}
     tests/test_truth_surface_health.py: {created: "2026-04-07", last_used: "2026-04-25"}
     tests/test_digest_admission_policy.py: {created: "2026-04-25", last_used: "2026-04-25"}
     tests/test_digest_profile_matching.py: {created: "2026-04-25", last_used: "2026-04-27"}
diff --git a/architecture/topology.yaml b/architecture/topology.yaml
index 45e99da..c7a2052 100644
--- a/architecture/topology.yaml
+++ b/architecture/topology.yaml
@@ -921,6 +921,7 @@ digest_profiles:
       - "Schema changes require updates to architecture/topology_schema.yaml in the same packet."
     allowed_files:
       - "scripts/topology_doctor.py"
+      - "scripts/topology_doctor_docs_checks.py"
       - "scripts/topology_doctor_digest.py"
       - "architecture/topology.yaml"
       - "architecture/topology_schema.yaml"
@@ -940,6 +941,7 @@ digest_profiles:
       - "python3 scripts/topology_doctor.py --schema"
     downstream:
       - "scripts/topology_doctor.py"
+      - "scripts/topology_doctor_docs_checks.py"
       - "tests/test_topology_doctor.py"
     stop_conditions:
       - "Stop and plan if changes weaken forbidden-wins, no-echo, or ambiguity-detection invariants."
diff --git a/scripts/topology_doctor_docs_checks.py b/scripts/topology_doctor_docs_checks.py
index b6291ec..51a2ea0 100644
--- a/scripts/topology_doctor_docs_checks.py
+++ b/scripts/topology_doctor_docs_checks.py
@@ -4,7 +4,7 @@ This module intentionally receives the topology_doctor module as `api` instead
 of importing its internals. That keeps this first checker-family split small and
 preserves the existing public helper surface during migration.
 """
-# Lifecycle: created=2026-04-16; last_reviewed=2026-04-21; last_reused=2026-04-21
+# Lifecycle: created=2026-04-16; last_reviewed=2026-04-28; last_reused=2026-04-28
 # Purpose: Docs-tree, operations-registry, runtime-plan, and docs-registry checks for topology_doctor.
 # Reuse: Keep docs-specific policy checks here; route root/state/source checks through their checker modules.
 
@@ -147,7 +147,7 @@ def check_hidden_docs(api: Any, topology: dict[str, Any]) -> list[Any]:
     visible_docs_files = [
         api.ROOT / rel
         for rel in api._git_visible_files()
-        if rel.startswith("docs/") and (api.ROOT / rel).is_file()
+        if rel.startswith("docs/")
     ]
     allowed_root_files = set(topology.get("docs_root_allowed_files") or {"docs/AGENTS.md", "docs/README.md", "docs/archive_registry.md"})
     for path in sorted(visible_docs_files):
diff --git a/tests/test_topology_doctor.py b/tests/test_topology_doctor.py
index 4256a66..149d794 100644
--- a/tests/test_topology_doctor.py
+++ b/tests/test_topology_doctor.py
@@ -1,5 +1,5 @@
 """Tests for topology_doctor compiled topology gates."""
-# Lifecycle: created=2026-04-13; last_reviewed=2026-04-21; last_reused=2026-04-21
+# Lifecycle: created=2026-04-13; last_reviewed=2026-04-28; last_reused=2026-04-28
 # Purpose: Regression tests for topology_doctor lanes, CLI parity, and closeout compilation.
 # Reuse: Use targeted -k selectors for the lane being changed; inspect current manifest law first.
 

## Verification summary
- targeted docs-mode tests: 4 passed
- post-edit navigation: ok true, profile=modify topology kernel
- py_compile: pass
- planning-lock: ok true
- digest admission/profile/false-positive tests: 45 passed
- topology_doctor navigation/digest/admission selector: 25 passed, 219 deselected
- full tests/test_topology_doctor.py: 228 passed, 16 deselected
- direct topology schema check: pass (CLI --schema is not available in current executable)
- docs baseline filtered gate: before=21, after=22, new_batch_f_docs_issues=0; unrelated new issue docs/operations/edge_observation/AGENTS.md
- scripts topology filtered: no topology_doctor_docs_checks issue
- tests topology filtered: no test_topology_doctor issue
- map-maintenance: ok true with existing packet-file warnings
- diff-check: pass
