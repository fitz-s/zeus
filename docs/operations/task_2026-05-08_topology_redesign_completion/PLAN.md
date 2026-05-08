# Task: Complete the R12 topology redesign deletion

Created: 2026-05-08
Authority basis: PR #71 (`f56b33b0`) R12 Phase 5.B; mainline ledger
  `docs/operations/task_2026-05-05_object_invariance_mainline/PLAN.md` §Verification Debt
  ("Topology route friction is intentionally left for the operator's separate topology
  redesign branch")
Branch: `topology-redesign-2026-05-08` (off `main` `1d9859d9`)

## 1. Goal

Finish the deletion that PR #71 began. Remove dead checker call-sites, retire CLI
flows that crash on import, and update the discoverability surfaces so the next
wave-packet author finds the replacement (`--navigation`) without being told.

## 2. Root cause

R12 Phase 5.B (commit `f56b33b0`) deleted three helper modules
(`topology_doctor_context_pack.py` -1307 LOC, `topology_doctor_core_map.py` -454 LOC,
`topology_doctor_packet_prefill.py` -293 LOC) and `architecture/topology_schema.yaml`,
inlining the schema constants into `scripts/topology_doctor.py`. The cleanup of
**call sites and references** was incomplete:

- `scripts/topology_doctor.py:1367-1591` keeps ~30 stubs that import the deleted
  `topology_doctor_context_pack` module.
- CLI exposes `semantic-bootstrap` and `context-pack` subcommands plus
  `--context-packs` / `--module-books` / `--module-manifest` flags that crash with
  `ModuleNotFoundError: No module named 'scripts'`.
- Eight surfaces still cite `architecture/topology_schema.yaml` as if it existed.
- `AGENTS.md`, `task_boot_profiles.yaml`, `topology.yaml` direct agents at the
  retired commands, so wave-packet authors keep filing "topology friction" notes
  for issues the redesign already solved (Wave27/28 §Topology Notes).

## 3. K-compression — one structural decision

This is **one** unfinished cleanup, not N bugs. The single decision: complete the
deletion path PR #71 chose, including discoverability. No restoration; no new
features.

## 4. Scope

### In-scope
- Code deletion in `scripts/topology_doctor.py` (dead `_context_pack_checks()` family).
- CLI subcommand and flag deletion in `scripts/topology_doctor_cli.py`.
- Test deletion in `tests/test_topology_doctor.py` and the marker in `tests/conftest.py`.
- Stale `topology_schema.yaml` reference cleanup across registries and digest profiles.
- Discoverability surface update in root `AGENTS.md`, `architecture/AGENTS.md`,
  `architecture/topology.yaml`, `architecture/task_boot_profiles.yaml`, and
  `docs/reference/modules/topology_doctor_system.md`.

### Out-of-scope (separate K-decisions)
- RiskGuard duplicate loader route admission (Wave29/30 residual).
- `current_data_state.md` packet pointer drift (Wave27 footnote).
- New flow design — the replacement (`--navigation`) is already shipped.
- Restoration of any deleted module.

## 5. Surfaces to change

### 5.1 `scripts/topology_doctor.py`
Delete the contiguous dead block at lines 1367-1591:

- `_context_pack_checks()` (1367-1373)
- `build_impact()` (1376-1377)
- `_context_pack_profiles()` (1380-1381)
- `run_context_packs()` (1384-1385)
- `run_module_books()` (1388-1389)
- `run_module_manifest()` (1392-1393)
- `_strict_result_summary()` (1490-1491)
- `_route_health_for_context_pack()` (1494-1495)
- `_repo_health_for_context_pack()` (1498-1499)
- `_proof_claims_for_files()` (1502-1503)
- `_lore_summary()` (1506-1507)
- `_layered_history_lore()` (1510-1511)
- `_context_pack_contract_surfaces()` (1514-1515)
- `_context_pack_coverage_gaps()` (1518-1519)
- `_context_pack_downstream_risks()` (1522-1523)
- `_context_pack_questions()` (1527-1532)
- `_debug_red_green_checks()` (1536-1541)
- `_debug_suspected_boundaries()` (1544-1549)
- `build_debug_context_pack()` (1552-1553)
- `_looks_like_package_review()` (1556-1557)
- `_looks_like_debug()` (1560-1561)
- `_infer_task_class()` (1564-1565)
- `build_semantic_bootstrap()` (1568-1574)
- `build_package_review_context_pack()` (1577-1578)
- `build_context_pack()` (1581-1591)

**Keep** (these are independent of the deleted module):
- Line 42 `CONTEXT_PACK_PROFILES_PATH` constant.
- Line 525 `load_context_pack_profiles()` data loader.
- Line 30 + 149-150 documentation comments explaining the schema inlining.

### 5.2 `scripts/topology_doctor_cli.py`
- Lines 35-37: drop `--context-packs`, `--module-books`, `--module-manifest` argparse entries.
- Line 183-194: drop `context-pack` subparser.
- Line 195-211 (approx): drop `semantic-bootstrap` subparser.
- Lines 353-355: drop the `_strict_check_pairs` entries for `context_packs`,
  `module_books`, `module_manifest`.
- Lines 542 (impact), 613 (context-pack), 621 (semantic-bootstrap): drop the
  command dispatch handlers.
- Keep `--navigation` flow intact.

### 5.3 `tests/test_topology_doctor.py` and `tests/conftest.py`
Delete:
- `test_topology_doctor.py:284` (`build_impact` payload check).
- `test_topology_doctor.py:4433`, `:4446`, `:4479` (`build_impact` smoke tests).
- `test_topology_doctor.py:4487-4495` (context_packs / module_books / module_manifest
  validators).
- `tests/conftest.py:156` reference in the topology-doctor strict-check list.

### 5.4 Stale `topology_schema.yaml` references
- `architecture/task_boot_profiles.yaml:372` (required_reads): drop the line.
- `architecture/task_boot_profiles.yaml:380` (proof.evidence): replace with
  `scripts/topology_doctor.py` (inline `SCHEMA_REQUIRED_*` constants).
- `architecture/topology.yaml:1095, 1127, 1166, 1173, 1427, 1466`: drop the entries
  or replace with the inline-constants pointer.
- `architecture/module_manifest.yaml:723`: drop the line.
- `architecture/code_review_graph_protocol.yaml:6`: rewrite the comment to
  `scripts/topology_doctor*.py` only.
- `architecture/AGENTS.md:44`: drop the registry row for `topology_schema.yaml`.
- `architecture/digest_profiles.py:339, 363, 395, 401, 616, 649`: drop the
  `topology_schema.yaml` entries from the listed digest profile path lists.
- `scripts/topology_doctor_ownership_checks.py:5-6`: rewrite the comment to drop
  the "remain in topology_schema.yaml" claim (lines 16, 20 already say "Inlined
  from..." and stay).
- `tests/test_digest_profile_matching.py:1699`: drop `topology_schema.yaml` from
  the expected-files list if present.

### 5.5 Discoverability surfaces (the operator's discipline rule)
- `AGENTS.md:338`: drop the `context-pack` subcommand line; the surrounding list
  already cites `--planning-lock`, `--map-maintenance`, `--code-review-graph-status`,
  `impact`. Add `--navigation --task ... --intent ... --files ...` as the agent
  context-routing entry.
- `AGENTS.md:340-353` Stage 1: rewrite "semantic boot via `semantic-bootstrap`"
  to "semantic boot via `--navigation --task-class <class> --task <task>
  --files <files>`". Stage 2 paragraph stays.
- `architecture/task_boot_profiles.yaml:agent_runtime.required_reads` (lines
  368-373): replace `architecture/topology_schema.yaml` with the
  `scripts/topology_doctor.py` inline-constants pointer.
- `architecture/task_boot_profiles.yaml:agent_runtime.required_proofs[0].evidence`
  (lines 378-381): same replacement.
- `architecture/task_boot_profiles.yaml:agent_runtime.graph_usage` (line 405 area):
  drop `--context-packs --json`; the `--task-boot-profiles --json` line stays.
- `architecture/topology.yaml:1457`: drop the `--context-packs --json` command
  example.
- `docs/reference/modules/topology_doctor_system.md:79`: rewrite to say "navigation
  output is treated as derived context, not authority" without naming the dead
  appendix.

## 6. Phase order

1. **Phase A (code)**: §5.1 + §5.2 + §5.3 — drop the dead stubs, CLI, tests in one
   commit. After this phase, `python3 scripts/topology_doctor.py --strict` and the
   full pytest suite must pass.
2. **Phase B (registries)**: §5.4 — yaml/comment cleanup in one commit.
3. **Phase C (discoverability)**: §5.5 — agent-facing docs in one commit.
4. **Phase D (verify)**: see §7.

Phases A, B, C are committed separately so a regression in one is bisectable.

## 7. Verification plan

After each phase:
- `python3 scripts/topology_doctor.py --task-boot-profiles` exits 0 (no path_missing).
- `python3 scripts/topology_doctor.py --strict --schema --docs --source --tests --scripts`
  matches baseline `(passed_before - 0, failed_before - 0)`.
- `python3 -m pytest tests/test_topology_doctor.py tests/test_digest_profile_matching.py
  tests/conftest.py -q` green.

After Phase D (final):
- `grep -rn topology_schema.yaml architecture/ scripts/ tests/ docs/ AGENTS.md` returns
  only the deletion-acknowledgement comments (`scripts/topology_doctor.py:30`,
  `:149-150`, `topology_doctor_ownership_checks.py:16,20`, plus archived packets).
- `grep -rn "semantic-bootstrap\|context-pack\|--context-packs\|--module-books\|--module-manifest" AGENTS.md architecture/ docs/reference/ docs/operations/AGENTS.md`
  returns no live references; archived wave packets retain their historical citations.
- `python3 scripts/topology_doctor.py --navigation --task "verify topology redesign
  cleanup is admitted" --intent modify_existing --write-intent edit --files
  scripts/topology_doctor.py architecture/topology.yaml AGENTS.md` admits.

## 8. Stop conditions

Stop and ask the operator if the work would require:
- restoring any deleted module;
- changing live `--navigation` semantics;
- updating risk/execution/state code outside topology surfaces;
- relabeling or migrating live DB rows;
- changing `architecture/topology.yaml::registry_directories` shape.

## 9. Self-discoverability acceptance

The deliverable is **not** complete until a fresh agent following the
`--task-boot-profiles` output for `agent_runtime` task class can navigate to
the current flow without consulting chat. Reviewer checklist:

- Open `architecture/task_boot_profiles.yaml`, run `agent_runtime` profile
  reads, follow each path. Every path must exist and lead to current authority.
- Open root `AGENTS.md`, search for `topology_doctor`. Every command listed
  must run cleanly on `--help`.
- Open a Wave 27/28 §Topology Notes claim. Resolve it without code/chat — the
  resolution path must be reachable from the surfaces above.

If any of those probes still requires being told the answer in chat, the redesign
is unfinished — file a follow-up packet rather than rubber-stamp closure.
