# Batch H0a Current Diff Evidence — 2026-04-28

## Scope

H0a admits generated digest-profile companion surfaces under the existing `modify topology kernel` profile only. It registers/reuses `tests/test_digest_profiles_equivalence.py` as a trusted relationship test and regenerates `architecture/digest_profiles.py` from `architecture/topology.yaml`.

Non-goals: no Batch H production-source profile yet, no `src/**` source edits, no settlement/bin topology changes, no live/credentialed/prod DB side effects.

## Verification commands and outputs

```bash
$ python3 scripts/topology_doctor.py --navigation --task 'Batch H0a modify topology kernel admit digest profile generated companion surfaces' --files architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profiles_equivalence.py architecture/test_topology.yaml
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
$ python3 scripts/topology_doctor.py --planning-lock --changed-files architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profiles_equivalence.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h0a_current_diff_2026-04-28.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
{
  "ok": true,
  "issues": []
}

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
4 passed in 0.48s

# exit=0
```

```bash
$ .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_digest_admission_policy.py tests/test_digest_profile_matching.py tests/test_digest_regression_false_positive.py --no-header
.............................................                            [100%]
45 passed in 25.44s

# exit=0
```

```bash
$ python3 - <<'PY'  # direct _check_schema(load_topology(), load_schema())
schema_issue_count=0
schema check passed: no topology schema issues

# exit=0
```

```bash
$ python3 -m py_compile scripts/topology_doctor.py tests/test_digest_profiles_equivalence.py architecture/digest_profiles.py

# exit=0
```

```bash
$ python3 scripts/topology_doctor.py --tests --json | python3 - <<'PY'  # filtered for tests/test_digest_profiles_equivalence.py
topology_doctor --tests exit=1
global_issue_count=4
digest_profiles_equivalence_issue_count=0

# exit=0
```

```bash
$ git diff --check -- architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profiles_equivalence.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h0a_current_diff_2026-04-28.md

# exit=0
```

```bash
$ git diff -- src/execution/exit_lifecycle.py src/engine/lifecycle_events.py src/state/ledger.py src/engine/cycle_runtime.py src/supervisor_api/contracts.py | wc -c
       0

# exit=0
```

```bash
$ python3 - <<'PY'  # profile-id check: no new Batch H/legacy-Day0 profile in H0a
existing_profiles_referencing_exit_lifecycle=["r3 collateral ledger implementation", "r3 cancel replace exit safety implementation", "r3 exchange reconciliation sweep implementation"]
forbidden_batch_h_profile_hits=[]

# exit=0
```

## Current diff — H0a topology surfaces

```diff
diff --git a/architecture/digest_profiles.py b/architecture/digest_profiles.py
index 4995bbb..8c0bfd0 100644
--- a/architecture/digest_profiles.py
+++ b/architecture/digest_profiles.py
@@ -185,20 +185,27 @@ PROFILES: list[dict] = [ { 'id': 'change settlement rounding',
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
diff --git a/architecture/test_topology.yaml b/architecture/test_topology.yaml
index 22b8b73..1bc3dd8 100644
--- a/architecture/test_topology.yaml
+++ b/architecture/test_topology.yaml
@@ -105,6 +105,7 @@ test_trust_policy:
     tests/test_truth_surface_health.py: {created: "2026-04-07", last_used: "2026-04-25"}
     tests/test_digest_admission_policy.py: {created: "2026-04-25", last_used: "2026-04-25"}
     tests/test_digest_profile_matching.py: {created: "2026-04-25", last_used: "2026-04-27"}
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
index 45e99da..98f742c 100644
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
@@ -940,7 +943,10 @@ digest_profiles:
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
```

## Packet docs/evidence diff summary

```bash
```

## Notes

- `scripts/topology_doctor_docs_checks.py` appears in the generated mirror because it was already admitted in YAML in the prior Batch F topology-docs work; H0a did not create a new docs-check script scope.
- Protected production-source diff byte count is 0 for representative high-risk Batch H/prod surfaces.
- H0a did not add the Batch H `exit_lifecycle`/legacy-Day0 digest profile; that is reserved for H0b after this critic/verifier gate.

## Packet docs/evidence status note

`plan.md`, `work_log.md`, and this evidence file are untracked packet-evidence files in the current worktree, so `git diff --stat` does not report them. Current path status:

```bash
$ git status --short -- docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h0a_current_diff_2026-04-28.md
?? docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h0a_current_diff_2026-04-28.md
?? docs/operations/task_2026-04-28_contamination_remediation/plan.md
?? docs/operations/task_2026-04-28_contamination_remediation/work_log.md
```
