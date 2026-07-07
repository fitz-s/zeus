# `docs/operations/` — Authoritative Index

**Purpose:** every directory and top-level file under `docs/operations/` must be registered here. Not on this page → archival candidate (see `POLICY.md`).

**Last reviewed:** 2026-07-07 (rewritten against disk state; prior index was ~90% dead rows pointing at archived/deleted files — full history in git).

## Top-level files

| File | Purpose |
|---|---|
| `AGENTS.md` | Scoped agent rules for `docs/operations/`. |
| `POLICY.md` | Packet lifecycle + closeout rules. |
| `INDEX.md` | This file. |
| `current_state.md` | Single live control pointer (thin; rewritten 2026-07-07). |
| `current_data_state.md` | Data posture — trust only within its own Last-audited header. |
| `current_source_validity.md` | Source posture — same rule. |
| `known_gaps.md` | Known-gap worklist. |
| `packet_scope_protocol.md` | Packet scope conventions. |

## Directories

| Directory | Status | Purpose |
|---|---|---|
| `current/` | active | Active packets, plans (`current/plans/INDEX.md`), evidence, reports. |
| `activation/` | active | Activation evidence captures (rollout gating). |
| `edli_v1/` | keep | EDLI v1 design/evidence packet. |
| `live_egress/` | keep | Live egress evidence. |
| `sd3_validation_evidence/` | keep | SD3 validation evidence. |
| `before_after_fixture_2026-05-29/` | closed-evidence | Dated fixture; archive candidate on next sweep. |
| `tribunal_verification_2026-05-29/` | closed-evidence | Dated verification packet; archive candidate on next sweep. |
| `task_2026-05-21_mainline_completion_authority/` | closed (PR #284 merged) | Archive on next sweep. |
| `task_2026-05-23_probability_phantom_edge/` | closed (PR #323 merged) | Archive on next sweep. |
| `task_2026-06-14_percity_representativeness_debias/` | open | Per-city representativeness de-bias plan. |

## Removed 2026-07-07

Five never-written "permanent observation surface" shells (`attribution_drift/`, `calibration_observation/`, `edge_observation/`, `learning_loop_observation/`, `ws_poll_reaction/`) deleted — registered as active rolling evidence since 2026-04/05, zero data files ever written. Their weekly writer scripts remain in `scripts/` and `mkdir -p` their own output if ever scheduled.

## Archive

Closed packet bodies live untracked under `docs/archive/` — lookup via `docs/archive_registry.md`.
