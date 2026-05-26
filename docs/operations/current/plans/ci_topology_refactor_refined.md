# CI Topology Refactor — Refined Plan (post live-run)

> Created: 2026-05-26
> Authority basis: external CI topology refactor spec supplied by operator on 2026-05-26 (out-of-repo authoring artifact) + live-run reconciliation against `scripts/topology_doctor.py` (active authority) and `scripts/topology_v_next/` (P3 shadow mode).
> Status: PROPOSED, awaiting operator sign-off.

## 1. Why this is a refinement, not a rewrite

External spec proposed ~50 new files + 4 new workflows + new `scripts/topology/` package. Live-run reconciliation found:

1. **Doctor already emits a "route card / context pack"** (`scripts/topology_doctor.py digest --json` → full route_card with `admission_status`, `risk_tier`, `gate_budget`, `hard_stops`, `expansion_hints`, `operation_vector`, `route_candidates`). The Context Pack abstraction is already there.
2. **Doctor falls back to `generic` profile on path-only input** — exactly the failure mode spec §1 names ("agent 仍依赖用户 prompt 写得足够准"). For PR330-like files (`src/engine/cycle_runtime.py` + `src/execution/**` + `src/venue/**`), doctor returns `selected_by: high_fanout_file_only`, ties 10 candidates at 0.75, `admission_status: advisory_only`. Path-only routing is exactly what spec §2.2 Surface Registry would fix.
3. **`topology_v_next/`** (P3 shadow mode, 14 profiles, hard_stop_paths) already runs alongside doctor via `--v-next-shadow`. It's the in-flight successor admission engine; spec's proposed admission would be a third parallel system.
4. **`architecture/context_pack_profiles.yaml`** exists (2026-04-15, "Topology Context Efficiency" packet) and would name-collide with spec's `architecture/topology_context_packs.yaml`.
5. **`architecture/invariants.yaml`** referenced by spec §4 — DOES exist (INV-NN structural-law invariants, 44KB). Money-path invariants (MP-ECO-001, MP-SIDE-001, etc.) are separately defined in `architecture/money_path_ci.yaml#invariants:`. The refined `active_invariants.source` enum permits all three (`money_path_ci.yaml`, `money_path_objects.yaml`, `invariants.yaml`) plus `custom`. (Corrected per Copilot finding on PR #343 — earlier draft incorrectly claimed the file did not exist.)

## 2. Delta (what is genuinely missing)

| Concept | Status | Spec value-add |
|---|---|---|
| **Surface Registry** (path/symbol/table → surface_id) | absent | high — replaces phrase-based profile match with deterministic path mapping |
| **Failure Chain Registry** (FC-01..FC-10) | absent | high — historical failure precedent injected automatically |
| **Fatal misread injection into route card** | exists (`architecture/fatal_misreads.yaml`) but doctor doesn't pull into emitted pack | medium — wire-up only |
| **Required relationship tests selected by surface** | `gate_budget.required` says "relationship_test" generically; no specific test paths | high — surface→test mapping is novel |
| **ci_overrides** (expiry/owner/scope/follow-up) | absent | medium |
| **no_override hazards registry** | implicit in `hard_stops` but not enumerated as schema | low — formalize what exists |
| **Context Pack schema** (formal contract) | informal in route_card | medium — lock the contract |
| **CI gate router consuming Context Pack JSON** | absent | high — selects relationship tests per pack |

What spec proposes that's already in doctor and should NOT be rebuilt:
- admission engine (doctor has it + v_next has it)
- route card rendering (doctor emits it)
- profile matching (doctor has it + v_next has 14 profiles)
- risk tier classification (doctor emits T0..T4)
- hard stops (doctor emits)
- operation_vector typed routing (doctor has)

## 3. Refined scope

**Original spec:** ~50 files (architecture: 7, scripts/topology/: 8, scripts/ci/: 11, tests/: 11, workflows: 4, modifies: 8).

**Refined:** ~25 files. Drop duplicates, reuse doctor as substrate, keep genuine value-add.

### Create (refined set)

```text
architecture/topology_surfaces.yaml          # NEW — path→surface_id (lightweight, additive)
architecture/failure_chains.yaml             # NEW — FC-01..FC-10 registry
architecture/context_pack_schema.yaml        # NEW — formal Context Pack contract
architecture/topology_enforcement.yaml       # NEW — blocking vs advisory split + no_override hazards
architecture/ci_overrides.yaml               # NEW — override registry

scripts/topology_doctor_context_pack.py      # NEW — single module, extends doctor's existing route_card with FC + surface + injected fatal misreads. NOT a new package. Lazy-imports doctor primitives.

scripts/ci/check_context_pack_integrity.py   # NEW
scripts/ci/check_topology_structural_blockers.py  # NEW
scripts/ci/check_context_pack_overrides.py   # NEW
scripts/ci/context_pack_gate_router.py       # NEW — reads Context Pack JSON from doctor, selects relationship tests
scripts/ci/check_workflow_repo_refs.py       # NEW — sole rule: workflow run paths exist
scripts/ci/check_stdlib_shadowing.py         # NEW — sole rule: prevent PR#306 recurrence
scripts/ci/check_source_rationale_delta.py   # NEW — sole rule: new source needs rationale
scripts/ci/check_db_table_delta.py           # NEW — sole rule: new table needs ownership

tests/topology/test_topology_surfaces_schema.py
tests/topology/test_failure_chains_schema.py
tests/topology/test_context_pack_schema.py
tests/topology/test_context_pack_integration.py  # historical fixtures: pr325/pr330/pr335/pr312/pr306
tests/ci/test_context_pack_gate_router.py
tests/ci/test_context_pack_overrides.py
tests/ci/test_structural_blockers.py

.github/workflows/topology-context-required.yml   # NEW — blocks ONLY no_override hazards
.github/workflows/topology-context-advisory.yml   # NEW — emits PR summary, never blocks
```

### Modify (refined set)

```text
architecture/money_path_ci.yaml         # add `surface_ids:` + `context_pack_ids:` keys per invariant
architecture/money_path_objects.yaml    # add `surface_ids:` per economic_object/state_machine
scripts/topology_doctor_cli.py          # add `--context-pack-render` flag that calls scripts/topology_doctor_context_pack.py
.github/pull_request_template.md        # add surface_id checkboxes
```

### Skip (drop from original spec)

```text
architecture/topology_context_packs.yaml  → use existing context_pack_profiles.yaml
scripts/topology/__init__.py + 7 modules  → consolidate into single scripts/topology_doctor_context_pack.py
.github/workflows/money-path-context-gated.yml → fold into topology-context-required.yml (one blocking workflow)
.github/workflows/nightly-context-replay.yml  → defer to Phase 5+ (separate decision; nightly tests not yet exist)
scripts/ci/check_scheduler_registry.py    → tests/test_writer_jobs_registry_guard.py already enforces this
scripts/ci/check_required_context_consumers.py → fold into context_pack_integrity
scripts/ci/tier0_pairing_gate.py         → fold into structural_blockers (one rule, not a separate script)
```

## 4. Refined phasing

**Phase A — Schemas + Surface/FC registries (PR #1, ~600 LOC).**
- `architecture/topology_surfaces.yaml`
- `architecture/failure_chains.yaml`
- `architecture/context_pack_schema.yaml`
- `architecture/ci_overrides.yaml`
- `architecture/topology_enforcement.yaml`
- Schema validation tests
- All advisory, no CI blocking yet.

**Phase B — Context Pack renderer + doctor extension (PR #2, ~500 LOC).**
- `scripts/topology_doctor_context_pack.py`
- `scripts/topology_doctor_cli.py` flag wiring
- Modify `money_path_ci.yaml` + `money_path_objects.yaml` (additive surface_ids)
- `tests/topology/test_context_pack_integration.py` with PR325/PR330/PR335/PR312/PR306 fixtures
- Output: doctor route_card now includes surface_ids + FC IDs + injected fatal misreads + required relationship tests
- Run alongside existing doctor output; no behavior change to admission.

**Phase B.5 — Universal first-principle PR monitor (added to PR #2, ~600 LOC).**
- Operator first-principle directive 2026-05-26: meaningful findings only,
  no self-reflection, CI failure immediate, no CI success run.
- `scripts/ci/pr_monitor.py` — reusable CLI tool with persistent dedup state
  in `~/.cache/zeus/pr_monitor/`. Stdlib only, no PyYAML.
- Four invariants enforced:
  1. Unresolved review threads with non-empty body emit; resolved or empty silent
  2. CI checks with FAILURE/TIMED_OUT/CANCELLED/STARTUP_FAILURE emit immediately;
     SUCCESS/pending/SKIPPED/NEUTRAL silent
  3. Dedup across invocations via state file (thread_id + name:conclusion keys)
  4. Terminal MERGED/CLOSED emits exactly once and exits 0
- `tests/ci/test_pr_monitor.py` — 32 tests verifying all 4 invariants + format + JSON mode
- Replaces ad-hoc in-session Bash Monitors. Any agent on any future PR
  invokes `python scripts/ci/pr_monitor.py <pr>` and gets the same
  first-principle filtering.

**Phase B.6 — Stale-silence detector (added to PR #2, ~370 LOC).**
- Operator fifth first-principle directive 2026-05-26: if monitor has been
  silent for ≥15 min, something is wrong — check PR directly.
- `--stale-after SECONDS` flag (default 900 = 15 min; 0 disables).
- Anchors silence clock on `max(last_event_at, monitor_started_at)`. The
  real-event reset semantics mean the timer always measures "time since
  monitor saw anything actionable".
- Emits one-shot `PR#NN STALE_SILENCE elapsed=Ns last_event_at=<ts|never>
  hint=check PR directly (gh pr view <pr>)`.
- Dedup: after a stale emit, suppress until threshold passes again OR a
  real event resets the clock. Persisted via `last_stale_emit_at` in state.
- Fires even when `gh pr view` returns None (network/auth/PR-ref drift
  = exactly the failure mode the directive is meant to catch).
- `tests/ci/test_pr_monitor.py` adds 17 stale-silence tests for a total of 49.

**Phase C — Advisory CI workflow (PR #3, ~580 LOC).**
- `.github/workflows/topology-context-advisory.yml` — pull_request trigger,
  continue-on-error: true, timeout 5 min, pull-requests:write permission.
  Concurrency group per PR with cancel-in-progress. Actions pinned to
  major versions. Uploads `/tmp/context-packs.{json,md}` as artifact
  `topology-context-pack-<PR>`.
- `scripts/ci/post_pr_context_pack_comment.py` — sticky PR comment poster.
  Hidden marker `<!-- zeus-context-pack-summary -->` for upsert detection.
  Builds body with summary count + per-pack table + collapsible full
  pack(s) + topology boundary disclaimer. `--dry-run` mode.
- `tests/ci/test_post_pr_context_pack_comment.py` — 13 tests (sticky
  marker, body construction, table truncation, dry-run, error paths).
- `tests/ci/test_topology_context_advisory_workflow.py` — 9 structural
  validation tests (YAML parses, pull_request trigger, paths filter,
  continue-on-error, permissions, timeout, workflow_refs_exist
  enforcement, concurrency, action pinning, artifact upload).
- Never blocks the build — every step has `continue-on-error: true`.

**Phase D — Structural blockers + override system (PR #4, ~500 LOC).**
- `scripts/ci/check_context_pack_integrity.py`
- `scripts/ci/check_topology_structural_blockers.py`
- `scripts/ci/check_context_pack_overrides.py`
- `scripts/ci/check_workflow_repo_refs.py`
- `scripts/ci/check_stdlib_shadowing.py`
- `scripts/ci/check_source_rationale_delta.py`
- `scripts/ci/check_db_table_delta.py`
- `.github/workflows/topology-context-required.yml`
- ONLY no_override hazards block (≤6 enumerated rules).

**Phase E — Gate router (PR #5, ~400 LOC).**
- `scripts/ci/context_pack_gate_router.py`
- Wire into `topology-context-required.yml`
- Selected relationship tests per surface.

**Phase F — Operator review + cutover decision (no PR).**
- Compare topology_doctor.py + topology_v_next + new Context Pack output on 5+ live PRs.
- Operator decides: keep three-system shadow, retire topology_doctor's profile matching, or retire topology_v_next.
- Cutover or further consolidation in a future spec.

Total: **5 PRs, ~2300 LOC**, ~25 files. Down from spec's ~50 files / 7 phases.

## 5. Acceptance gate (refined)

For each historical PR fixture, `python scripts/topology_doctor.py digest --task <task> --files <files> --context-pack-render --json` must emit:

| Fixture | surface_ids | FC IDs | fatal_misreads injected | required relationship tests |
|---|---|---|---|---|
| PR325 (market discovery) | `market_discovery_scanner` | FC-02 | ≥1 | `tests/test_market_discovery_full_coverage.py` |
| PR330 (exec fresh-submit) | `execution_cycle_runtime` | FC-03 | ≥1 stale-snapshot misread | `tests/test_exec_freshness_recapture.py` |
| PR335 (scheduler registry) | `ingest_scheduler` | FC-04 + FC-05 | ≥1 | `tests/test_writer_jobs_registry_guard.py` |
| PR312 (forecast bundle) | `executable_forecast_reader` | FC-01 | ≥1 | `tests/test_executable_forecast_bundle_selection.py` |
| PR306 (stdlib shadow) | `topology_v_next` | FC-09 | n/a | structural_blockers must fire |

And: no_override hazard list ≤6 entries, each with enforcer script + proving test.

## 6. Operator decisions still needed

1. **Phase A yaml naming** — keep `context_pack_profiles.yaml` (existing) untouched OR migrate it into the new schema? **Recommend: leave untouched, new yamls additive.**
2. **topology_v_next cutover schedule** — out of scope for this refactor, but the new Context Pack system could either consume v_next admission output as input (clean) or compete (messy). **Recommend: input-only consumer of v_next.**
3. **Phase F shape** — single follow-up spec consolidating into one system, or accept three-system steady-state? **Defer.**

## 7. Concrete next step

If approved: branch `feat/ci-topology-context-pack`, start Phase A. Worktree at `.claude/worktrees/ci-topology-refactor` ready.
