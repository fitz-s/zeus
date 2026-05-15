# Runtime Improvement Engineering Package — Verification Plan

Status: SPEC
Purpose: prove each track of the engineering package is actually deliverable
and not aspirational. The implementation packets in `05_execution_packets/`
each carry their own per-packet acceptance; this file defines the package-
level verification that closes the parent.

## Package-Level Verification

### Plan integrity

```bash
python3 scripts/topology_doctor.py --navigation \
  --task "operation planning packet: runtime improvement engineering" \
  --intent create_new --write-intent docs \
  --files docs/operations/task_2026-05-15_runtime_improvement_engineering_package/PLAN.md \
          docs/operations/AGENTS.md --json

python3 scripts/topology_doctor.py --planning-lock \
  --changed-files docs/operations/task_2026-05-15_runtime_improvement_engineering_package/PLAN.md \
                  docs/operations/AGENTS.md \
  --plan-evidence docs/operations/task_2026-05-15_runtime_improvement_engineering_package/PLAN.md

python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory \
  --changed-files docs/operations/task_2026-05-15_runtime_improvement_engineering_package/PLAN.md \
                  docs/operations/AGENTS.md

git diff --check
```

All three calls must return `ok=True`. `git diff --check` must be empty.

### Universal-design Zeus-leak check

```bash
grep -niE 'zeus|polymarket|ENS|kelly|platt|calibration|settlement|venue|asos|wu_|metar|station|ECMWF|TIGGE|world_class|forecast_class|risk[_ ]?guard|harvester|monte[_ ]?carlo|orderbook|CLOB|nowcast|day0|backtest' \
  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/01_topology_v_next/UNIVERSAL_TOPOLOGY_DESIGN.md \
  | grep -v 'ZEUS_BINDING_LAYER.md' \
  | grep -v 'binding layer'
```

Expected: zero non-meta hits. Any line that mentions a Zeus-specific
identifier outside of meta-references to "the binding layer" is a
regression.

### Cross-track coherence checks

```bash
# Every TASK_CATALOG.yaml task references a real rule_source path
python3 -c "
import yaml, pathlib
PKG = 'docs/operations/task_2026-05-15_runtime_improvement_engineering_package'
cat = yaml.safe_load(open(f'{PKG}/02_daily_maintenance_agent/TASK_CATALOG.yaml'))
for t in cat['tasks']:
    src = t['rule_source'].split('#')[0]
    p = pathlib.Path(PKG) / src.lstrip('./').replace('../', '')
    p2 = pathlib.Path(f'{PKG}/02_daily_maintenance_agent') / src
    if not (p.exists() or p2.resolve().exists()):
        print(f'MISSING rule_source: {t[\"id\"]} -> {src}')
        exit(1)
print('all rule_source paths resolve')
"

# Every Cohort entry in DRIFT_ASSESSMENT cites a path that exists
# (this can be wired into the maintenance worker's authority_drift_surface task)

# Every PACKET_INDEX entry's Inputs cite real package files
PKG=docs/operations/task_2026-05-15_runtime_improvement_engineering_package
grep -oE '`[0-9]+_[a-zA-Z_/]+\.(md|yaml)`' \
  "$PKG/05_execution_packets/PACKET_INDEX.md" \
  | grep -oE '[0-9]+_[a-zA-Z_/]+\.(md|yaml)' \
  | sort -u \
  | while read ref; do
      [ -z "$ref" ] && continue
      [ -f "$PKG/$ref" ] || echo "MISSING: $ref"
    done
```

Expected: no MISSING rows.

### Safety contract self-test

The SAFETY_CONTRACT.md must explicitly forbid every path the agent could
plausibly want to touch but must not. Smoke check:

```bash
cd docs/operations/task_2026-05-15_runtime_improvement_engineering_package
for forbidden in 'src/' 'state/' 'architecture/' 'AGENTS.md' '.claude/' \
                 '.codex/' 'CLAUDE.md' '.env' 'credential' 'secret' \
                 '~/.aws/' '~/.ssh/' '.git/' 'launchctl'; do
  grep -F "$forbidden" 02_daily_maintenance_agent/SAFETY_CONTRACT.md > /dev/null \
    || echo "MISSING forbidden coverage: $forbidden"
done
```

Expected: no MISSING rows.

### Hidden-branch coverage

Every iteration listed in `00_evidence/HIDDEN_BRANCH_INVENTORY.md` must
appear as its own section in `01_topology_v_next/HIDDEN_BRANCH_LESSONS.md`.

```bash
PKG=docs/operations/task_2026-05-15_runtime_improvement_engineering_package
INV=$PKG/00_evidence/HIDDEN_BRANCH_INVENTORY.md
LSN=$PKG/01_topology_v_next/HIDDEN_BRANCH_LESSONS.md
grep -oE 'task_2026-05-[0-9]+_[a-z_]+' "$INV" | sort -u > /tmp/inv_iters
grep -oE 'task_2026-05-[0-9]+_[a-z_]+' "$LSN" | sort -u > /tmp/lsn_iters
diff /tmp/inv_iters /tmp/lsn_iters || echo 'HIDDEN_BRANCH coverage mismatch'
```

Expected: no diff output.

### Friction-pattern coverage

The 7 named friction patterns from the sibling audit packet must each be
addressed in `01_topology_v_next/UNIVERSAL_TOPOLOGY_DESIGN.md`:

```bash
for pat in LEXICAL_PROFILE_MISS UNION_SCOPE_EXPANSION SLICING_PRESSURE \
           PHRASING_GAME_TAX INTENT_ENUM_TOO_NARROW \
           CLOSED_PACKET_STILL_LOAD_BEARING ADVISORY_OUTPUT_INVISIBILITY; do
  grep -F "$pat" docs/operations/task_2026-05-15_runtime_improvement_engineering_package/01_topology_v_next/UNIVERSAL_TOPOLOGY_DESIGN.md > /dev/null \
    || echo "MISSING pattern coverage: $pat"
done
```

Expected: no MISSING rows.

## Per-Track Verification

### 00_evidence

- Each of 4 inventory files exists, non-empty, ≤ 200 lines per file
- Tables have at least one data row each (no schema-only files)

### 01_topology_v_next

- All 4 sonnet output files exist, ≤ 400 lines each
- Universal-design Zeus-leak check passes (above)
- Friction-pattern coverage check passes (above)
- Hidden-branch coverage check passes (above)

### 02_daily_maintenance_agent + 04_workspace_hygiene

- Cross-track coherence check passes (above)
- Safety contract smoke check passes (above)
- TASK_CATALOG.yaml is valid YAML
- Each PURGE_CATEGORIES.md category cites a "Currently observed example"
  drawn from the workspace mess audit

### 03_authority_drift_remediation

- DRIFT_ASSESSMENT.md cohort assignments cover all 62 inventory rows
  classified into Cohort 0 (critic-remediation additions), Cohorts 1–6, or
  explicitly named under the v2 inventory pass in Cohort 7; any row not
  yet classified appears in Cohort 0 with explicit rationale
- The 3 TOPOLOGY BLOCKING entries each appear in REMEDIATION_PLAN.md's
  investigation section
- Cohort 7 out-of-inventory list is enumerated

### 05_execution_packets

- PACKET_INDEX.md lists ≥ 4 packets (current count: 10)
- Each packet entry has Goal, Inputs, Scope, Acceptance, Dependency,
  Estimated size
- Dependency graph is acyclic

### 99_verification

- This file (VERIFICATION_PLAN.md) exists
- REGRESSION_PROBE_SUITE.md exists with probes covering each Track 01
  abstraction

## Critic Pass

A reviewer (any critic-class agent) must answer YES to all:

1. Does the package address all four named problems in PLAN.md (topology
   churn, authority drift, workspace mess, no scheduled hygiene)?
2. Is the universal-vs-Zeus-binding split clean?
3. Does the maintenance agent design have dry-run, kill switch, evidence
   trail, and a 30-day dry-run mandate?
4. Does the topology v_next address all 7 named friction patterns
   structurally (not by adding sidecars)?
5. Is the hidden-branch synthesis fair to past iterations (no "past
   designs were wrong" framing)?
6. Are the execution packets sized to fit the project's PR discipline?
7. Are the safety boundaries enforced as code AND documented?
8. Does the verification plan provide repeatable probes?

`REVISE` is not acceptance. APPROVE required.

## Failure Mode If Verification Fails

- Plan integrity fail → fix immediately; package is not creatable.
- Zeus-leak fail → strip leaked identifier from universal design,
  move to binding layer.
- Cross-track coherence fail → fix references; this is the most likely
  regression mode if any track is renamed/moved.
- Safety contract fail → add missing forbidden-path coverage. Hard
  gate on this; never ship a maintenance agent design with gaps.
- Friction-pattern coverage fail → either the design missed the pattern
  (regression) or the pattern name was reworded (update the check
  vocabulary). Investigate before silencing.
- Critic REVISE → re-route via the orchestrator; do not re-prompt
  critic without addressing the points raised.
