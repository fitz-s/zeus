# Post-P3 / P4 Preflight Evidence Packet

Date: 2026-04-25
Branch: `midstream_remediation`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus`
Status: closed

## Background

P3 4.5.B-lite closed the non-metric obs_v2 reader-gate slice, but it did not
resolve the forensic metric-layer decision for hourly observations. P4
canonical v2 population is still dependency-bound by market-rule evidence,
TIGGE local asset availability, and operator-owned runtime posture fixes.

This packet is a read-only phase-entry reassessment. It freezes the next
implementation boundaries after P3 and records which remaining work is
code-actionable only after operator evidence appears. It does not mutate
production DB rows, promote v2 tables, decide hourly high/low metric placement,
or run training.

## Phase Entry Evidence

- Reread `AGENTS.md`, `docs/operations/current_state.md`,
  `docs/operations/current_data_state.md`,
  `docs/operations/current_source_validity.md`,
  `docs/operations/known_gaps.md`, and
  `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md`.
- Ran `python3 scripts/topology_doctor.py --task-boot-profiles --json` and
  `python3 scripts/topology_doctor.py --fatal-misreads --json`; both passed.
- Ran topology navigation for this packet. New packet files were initially
  unclassified, so this packet includes the required docs registry/router
  updates before closeout.
- Scout identified stale P3 active pointers; those were closed in commit
  `95e20d2`.
- Architect recommended a narrow post-P3/P4-preflight evidence packet, not a
  P4 mutation packet, because remaining work is blocked by semantic/operator
  evidence.

## Scope

_The machine-readable list lives in `scope.yaml`; this section is a
human-readable mirror._

### In scope

- Read-only DB/table population inventory for P4 blockers.
- Local TIGGE asset and market-rule artifact inventory.
- Operator-owned runtime posture snapshot for `WU_API_KEY`,
  `k2_daily_obs`, `k2_forecasts_daily`, and auto-pause tombstone state.
- Acceptance gates and stop conditions for later 4.5.B-full, 4.6.A, 4.6.B,
  4.6.C, 4.7, and 4.8 packets.
- Packet/control docs and companion registries.

### Out of scope

- Production DB mutation.
- `settlements_v2`, `market_events_v2`, forecast/ensemble v2, or
  `calibration_pairs_v2` population.
- Hourly instant metric-layer decision.
- Schema/view changes.
- Source routing or Hong Kong source-truth changes.
- Clearing auto-pause tombstones, editing launch environments, or restarting
  live services.

## Deliverables

- `preflight.md` with blocker inventory, acceptance contracts, topology boot
  requirements, and stop conditions.
- Close the live pointer so the next packet starts from an explicit operator
  evidence or implementation trigger instead of re-entering P3.

## Verification

- `python3 scripts/topology_doctor.py --task-boot-profiles --json`
- `python3 scripts/topology_doctor.py --fatal-misreads --json`
- `python3 scripts/topology_doctor.py --navigation --task "Post-P3 phase-entry reassessment and P4 preflight evidence packet for remaining post-audit blockers" --files <packet files>`
- `sqlite3 state/zeus-world.db "<P4 blocker table-count query>"`
- `find state raw data -maxdepth 3 <TIGGE/market-rule artifact query>`
- `python3 scripts/topology_doctor.py --current-state-receipt-bound`
- `python3 scripts/topology_doctor.py --work-record --changed-files <packet files> --work-record-path docs/operations/task_2026-04-25_post_p3_p4_preflight/work_log.md`
- `python3 scripts/topology_doctor.py --change-receipts --changed-files <packet files> --receipt-path docs/operations/task_2026-04-25_post_p3_p4_preflight/receipt.json`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <packet files> --plan-evidence docs/operations/task_2026-04-25_post_p3_p4_preflight/plan.md`
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files <packet files>`
- `python3 -m json.tool docs/operations/task_2026-04-25_post_p3_p4_preflight/receipt.json`
- `git diff --check -- <packet files>`

## Closeout

- Outcome: P4 mutation remains blocked. The next safe implementation packet
  must be selected only after the relevant operator evidence exists or after a
  narrower read-only guardrail task is explicitly scoped.
- The most useful next code packet, if operator evidence remains unavailable,
  is a read-only readiness checker that packages the gates in `preflight.md`
  without mutating DB rows or runtime state.
