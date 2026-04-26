# P4 Readiness Checker Packet

Date: 2026-04-25
Branch: `midstream_remediation`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus`
Status: closed

## Background

The post-P3/P4 preflight evidence packet concluded that P4 mutation remains
blocked by missing operator/data evidence. The next safe code slice is a
read-only readiness checker that turns those blockers into machine-readable
status without mutating DB rows, clearing runtime state, editing launch
environments, or deciding hourly high/low metric placement.

## Phase Entry Evidence

- Reread `AGENTS.md`, `scripts/AGENTS.md`, `tests/AGENTS.md`,
  `docs/operations/current_state.md`, and the post-P3/P4 preflight packet.
- Ran `python3 scripts/topology_doctor.py --task-boot-profiles --json` and
  `python3 scripts/topology_doctor.py --fatal-misreads --json`; both passed.
- Scout mapped the smallest implementation surface to
  `scripts/verify_truth_surfaces.py` and `tests/test_truth_surface_health.py`.
- The packet must reuse existing diagnostic report patterns and not create a
  duplicate script.

## Scope

_The machine-readable list lives in `scope.yaml`; this section is a
human-readable mirror._

### In scope

- Add `p4-readiness` mode to `scripts/verify_truth_surfaces.py`.
- Report lane blockers for 4.5.B-full, 4.6.A, 4.6.B, 4.6.C, and operator
  runtime/data prerequisites.
- Add deterministic tests in `tests/test_truth_surface_health.py`.
- Confirm existing script/test manifest coverage and update packet/control docs.

### Out of scope

- Production DB mutation or canonical v2 population.
- Market-rule inference.
- TIGGE rsync or filesystem writes.
- `WU_API_KEY` env changes, scheduler restart, or auto-pause tombstone changes.
- Hourly metric-layer decision.
- New dependencies.

## Deliverables

- `build_p4_readiness_report(...)` and CLI `--mode p4-readiness`.
- JSON output compatible with existing readiness report shape.
- Tests proving read-only behavior and blocker mapping.

## Verification

- `python3 -m py_compile scripts/verify_truth_surfaces.py tests/test_truth_surface_health.py`
- `pytest -q tests/test_truth_surface_health.py`
- `python3 scripts/verify_truth_surfaces.py --mode p4-readiness --json`
- `python3 scripts/topology_doctor.py --scripts --json`
- `python3 scripts/topology_doctor.py --tests --json`
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files scripts/verify_truth_surfaces.py tests/test_truth_surface_health.py`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <packet files> --plan-evidence docs/operations/task_2026-04-25_p4_readiness_checker/plan.md`
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files <packet files>`
- `python3 scripts/topology_doctor.py --change-receipts --receipt-path docs/operations/task_2026-04-25_p4_readiness_checker/receipt.json`
- `git diff --check -- <packet files>`

## Closeout

- Implemented `build_p4_readiness_report(...)` and CLI
  `--mode p4-readiness`.
- The checker reports `NOT_READY` on the live state and preserves blockers for
  unresolved metric-layer decision, missing accepted market-rule contract,
  missing TIGGE parity/hash/source-time manifests, empty P4 v2 tables, missing
  `WU_API_KEY`, failing `k2_daily_obs`, missing forecasts row-count evidence,
  non-green infrastructure, and the auto-pause tombstone.
- The packet did not mutate production DB rows, runtime JSON, launch
  environment, tombstones, TIGGE assets, or market-rule artifacts.

## Stop Conditions

- Stop if the checker needs to write any DB/runtime state.
- Stop if correctness requires choosing instant-level versus daily-aggregate
  metric identity.
- Stop if P4 population, TIGGE rsync, launch env repair, or tombstone clearing
  becomes necessary to make tests pass.
