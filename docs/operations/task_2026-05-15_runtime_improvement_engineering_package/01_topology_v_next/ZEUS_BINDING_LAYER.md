# Zeus Binding Layer

Created: 2026-05-15
Status: SPEC ONLY — project-specific bindings for UNIVERSAL_TOPOLOGY_DESIGN.md v_next

This is the ONLY file in 01_topology_v_next/ where Zeus-specific identifiers
may appear. All universal mechanisms referenced here are defined in
UNIVERSAL_TOPOLOGY_DESIGN.md.

---

## 1. Project Identity

```yaml
project:
  id: zeus
  description: >
    Live quantitative trading engine operating in Polymarket weather derivatives.
    Converts atmospheric forecast data into sized limit orders with positive
    expectation, bound by market settlement mechanics and dynamic risk limits.
  primary_language: python
  canonical_db_files:
    - state/zeus-world.db     # WORLD_CLASS tables
    - state/zeus-forecasts.db # FORECAST_CLASS tables
```

---

## 2. Intent Extensions

Zeus-specific intent values (namespaced with `zeus.`):

```yaml
intent_extensions:
  - id: zeus.settlement_followthrough
    description: >
      Post-settlement data harvesting; resolves market outcomes against DB records.
      Does not change live pricing or position logic.
  - id: zeus.calibration_update
    description: >
      Update calibration pair records or model parameters in the forecasts DB.
      Requires proof that existing active Platt rows are not overwritten.
  - id: zeus.data_authority_receipt
    description: >
      Record a new verified data source or refresh an existing source-truth receipt.
      Read-only on all runtime tables; writes only to source_rationale surfaces.
  - id: zeus.topology_tooling
    description: >
      Modify topology_doctor.py, topology_doctor_digest.py, or supporting
      architecture YAMLs without touching any money-path source.
```

---

## 3. Hard Safety Kernel Bindings

```yaml
hard_stop_paths:
  # Live side effects — any write here may trigger a live API call or position change
  - pattern: "src/execution/**"
    category: LIVE_SIDE_EFFECT_PATH
    reason: Live order placement and venue interaction
  - pattern: "src/venue/**"
    category: LIVE_SIDE_EFFECT_PATH
    reason: CLOB API and venue submission envelope
  - pattern: "src/riskguard/**"
    category: LIVE_SIDE_EFFECT_PATH
    reason: Live risk limit enforcement

  # Canonical truth — single source for domain facts
  - pattern: "architecture/source_rationale.yaml"
    category: CANONICAL_TRUTH_REWRITE
    reason: Source-of-truth authority for all observation source assignments
  - pattern: "architecture/city_truth_contract.yaml"
    category: CANONICAL_TRUTH_REWRITE
    reason: City canonical coordinates and timezone binding
  - pattern: "architecture/fatal_misreads.yaml"
    category: CANONICAL_TRUTH_REWRITE
    reason: Enumeration of known fatal agent misinterpretation patterns
  - pattern: "architecture/db_table_ownership.yaml"
    category: CANONICAL_TRUTH_REWRITE
    reason: Machine-checked table-to-DB assignment; INV-37 enforcement

  # Schema migration — canonical store structural changes
  - pattern: "src/state/migrations/**"
    category: SCHEMA_MIGRATION
    reason: SQLite schema migration; must not run without explicit migration ceremony
  - pattern: "state/zeus-world.db"
    category: SCHEMA_MIGRATION
    reason: Live world DB; direct file mutation is forbidden
  - pattern: "state/zeus-forecasts.db"
    category: SCHEMA_MIGRATION
    reason: Live forecasts DB; direct file mutation is forbidden

  # Lifecycle grammar
  - pattern: "src/control/**"
    category: LIFECYCLE_GRAMMAR
    reason: Daemon lifecycle and command grammar
  - pattern: "src/supervisor_api/**"
    category: LIFECYCLE_GRAMMAR
    reason: Supervisor API; changes alter the agent command surface

  # Credentials and auth
  - pattern: "config/credentials/**"
    category: CREDENTIAL_OR_AUTH_SURFACE
    reason: API keys and auth tokens
  - pattern: ".claude/hooks/**"
    category: CREDENTIAL_OR_AUTH_SURFACE
    reason: Hook dispatch and registry; changes alter what hooks fire
```

---

## 4. Coverage Map

```yaml
coverage_map:
  profiles:
    - id: agent_runtime
      patterns:
        - "architecture/task_boot_profiles.yaml"
        - "architecture/admission_severity.yaml"
        - "architecture/test_topology.yaml"
        - "scripts/topology_doctor.py"
        - "scripts/topology_doctor_digest.py"
        - "scripts/worktree_doctor.py"
        - "docs/operations/AGENTS.md"

    - id: money_path_pricing
      patterns:
        - "src/contracts/settlement_semantics.py"
        - "src/contracts/execution_price.py"
        - "src/contracts/venue_submission_envelope.py"
        - "src/contracts/fx_classification.py"
        - "src/engine/evaluator.py"

    - id: money_path_execution
      patterns:
        - "src/execution/executor.py"
        - "src/execution/exit_triggers.py"
        - "src/execution/harvester.py"

    - id: monitoring
      patterns:
        - "src/engine/monitor_refresh.py"
        - "src/observability/**"

    - id: forecast_pipeline
      patterns:
        - "src/engine/cycle_runner.py"
        - "src/engine/cycle_runtime.py"

    - id: calibration
      patterns:
        - "src/calibration/**"

    - id: data_ingestion
      patterns:
        - "src/ingestion/**"
        - "src/sources/**"

    - id: state_read_model
      patterns:
        - "src/state/db.py"
        - "src/state/table_registry.py"
        - "src/state/**"

    - id: config_management
      patterns:
        - "config/settings.json"
        - "config/*.yaml"
        - "config/*.json"

    - id: docs_authority
      patterns:
        - "docs/reference/**"
        - "AGENTS.md"
        - "docs/operations/AGENTS.md"

    - id: test_suite
      patterns:
        - "tests/test_*.py"
        - "tests/fixtures/**"

    - id: scripts_tooling
      patterns:
        - "scripts/*.py"
        - "scripts/*.sh"

    - id: architecture_docs
      patterns:
        - "architecture/*.yaml"
        - "architecture/*.md"

    - id: packet_evidence
      patterns:
        - "docs/operations/task_*/**.md"
        - "docs/operations/task_*/**.yaml"

  orphaned:
    - "tmp/**"
    - "*.bak.*"
    - "*.replaced"
    - "*.locked"
    - ".gitignore"
    - ".env*"

  hard_stop_paths:
    - "src/execution/**"
    - "src/venue/**"
    - "src/riskguard/**"
    - "architecture/source_rationale.yaml"
    - "architecture/city_truth_contract.yaml"
    - "architecture/fatal_misreads.yaml"
    - "architecture/db_table_ownership.yaml"
    - "src/state/migrations/**"
    - "state/zeus-world.db"
    - "state/zeus-forecasts.db"
    - "src/control/**"
    - "src/supervisor_api/**"
    - "config/credentials/**"
    - ".claude/hooks/**"
```

---

## 5. High-Fanout File Route Hints

Files that appear in multiple profiles must be resolved by intent context.
When these files appear in a change set, the admission system should
check typed-intent before profile selection:

```yaml
high_fanout_hints:
  - file: "src/state/db.py"
    resolution:
      - intent: [zeus.calibration_update, zeus.settlement_followthrough]
        profile: state_read_model
        note: "DB read/query path; not schema change"
      - intent: [modify_existing, refactor]
        profile: state_read_model
        note: "Only state_read_model unless migration files also present"
      - intent: [create_new]
        profile: state_read_model
        note: "New DB utility; pair with test_suite companion requirement"

  - file: "src/engine/cycle_runtime.py"
    resolution:
      - intent: [audit, plan_only]
        profile: forecast_pipeline
      - intent: [modify_existing, hotfix]
        profile: forecast_pipeline
        note: "Core cycle path; requires evaluator proof questions if execution-path adjacent"

  - file: "src/observability/status_summary.py"
    resolution:
      - intent: [modify_existing, create_new]
        profile: monitoring
```

---

## 6. Cohort Declarations

```yaml
cohorts:
  - id: zeus.new_test_with_topology_registration
    description: >
      Every new tests/test_*.py file requires a companion entry in
      architecture/test_topology.yaml (trust policy) and may require
      a new category bucket. These three files form a cohort for create_new intent.
    profile: test_suite
    intent_classes: [create_new]
    files:
      - "tests/test_{new_module}.py"      # pattern; resolved at admission time
      - "architecture/test_topology.yaml"
    note: >
      This cohort replaces the manual reminder "update test_topology.yaml for
      every new test file." The admission system enforces it structurally.

  - id: zeus.topology_tooling_with_severity
    description: >
      topology_doctor changes that add new issue codes must be paired with
      admission_severity.yaml updates. These two files form a cohort.
    profile: agent_runtime
    intent_classes: [modify_existing, refactor, create_new]
    files:
      - "scripts/topology_doctor_digest.py"
      - "architecture/admission_severity.yaml"

  - id: zeus.packet_plan_with_agents_update
    description: >
      New operation packet PLAN.md files must be paired with the operations
      AGENTS.md update. These two files form a cohort for create_new intent
      on planning packets.
    profile: packet_evidence
    intent_classes: [create_new]
    files:
      - "docs/operations/task_*/PLAN.md"
      - "docs/operations/AGENTS.md"
```

---

## 7. Severity Overrides

Zeus promotes several universal advisories to soft_block given the live-money
context:

```yaml
severity_overrides:
  # Promoted from advisory → soft_block
  - issue_code: canonical_truth_adjacent
    severity: soft_block
    reason: "Any change near canonical truth surfaces in a live-money system warrants explicit pause"

  - issue_code: closed_packet_authority
    severity: soft_block
    reason: "Closed packets in Zeus may still be load-bearing for live runtime decisions"

  - issue_code: high_fanout_file_unresolved
    severity: soft_block
    reason: "High-fanout files without intent disambiguation create real routing risk"

  # Kept at advisory (not promoted)
  - issue_code: coverage_gap
    severity: advisory
    reason: "Orphaned files exist legitimately; gap is surfaced for awareness only"

  - issue_code: companion_missing
    severity: advisory
    reason: "Companion requirement is a prompt, not a gate, for create_new intent"
```

---

## 8. Artifact Authority Status Registry

```yaml
artifact_authority_status:
  # Current load-bearing — do not overwrite without proof
  - path: "architecture/task_boot_profiles.yaml"
    status: CURRENT_LOAD_BEARING
    last_confirmed: 2026-05-14
    confirmation_ttl_days: 14
    reason: "Boot profiles govern all pipeline-impacting task routing"

  - path: "architecture/admission_severity.yaml"
    status: CURRENT_LOAD_BEARING
    last_confirmed: 2026-05-14
    confirmation_ttl_days: 14
    reason: "Severity assignments govern every admission hook outcome"

  - path: "architecture/db_table_ownership.yaml"
    status: CURRENT_LOAD_BEARING
    last_confirmed: 2026-05-11
    confirmation_ttl_days: 30
    reason: "INV-37 machine-checked enforcement; K1 split contract"

  # Historical — preserved but not live authority
  - path: "docs/operations/task_2026-05-06_topology_redesign"
    status: CURRENT_HISTORICAL
    last_confirmed: 2026-05-15
    confirmation_ttl_days: 90
    reason: "Design proposal; core concepts absorbed into current architecture"

  - path: "docs/operations/task_2026-05-06_hook_redesign"
    status: CURRENT_HISTORICAL
    last_confirmed: 2026-05-15
    confirmation_ttl_days: 90
    reason: "Structured override model retracted by hook_redesign_v2"
```

---

## 9. Binding Layer Maintenance Contract

The binding layer must be updated when:
1. A new source file pattern is added to `src/` that is not covered by
   an existing profile → add to coverage_map
2. A new issue code is introduced in topology_doctor_digest.py → add to
   severity_overrides if Zeus treatment differs from universal default
3. A new cohort is discovered through operational friction → add to cohorts
4. An authority document changes status → update artifact_authority_status

The binding layer must NOT be modified in the same packet that modifies
the universal topology system. Universal changes and binding changes are
separate packets with separate review.
