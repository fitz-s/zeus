# Current State

Role: single live control pointer for the repo.

## Status

Zeus live posture remains operator-controlled; this file only points agents at
active work surfaces and current-fact companions.

- Branch observed during 2026-05-02 cleanup: `live-unblock-ws-snapshot-2026-05-01`
- Runtime entry: `src/main.py` (code-authoritative live runtime state)
- Posture: live-capable; operator params live in `config/settings.json`

## Active packet control

- Active package source: none frozen; use rows below.
- Active execution packet: none frozen for implementation.
- Receipt-bound source: none; current active packets below predate the `scope.yaml` / `receipt.json` closeout protocol.
- Required evidence: packet-local `PLAN.md`, `work_log.md`, or runtime-gating evidence named below.
- Next action: keep only rows in this file active; archive completed/superseded packet folders and move residual open work to `docs/to-do-list/known_gaps.md`.

## Active monitoring surfaces

| Surface | Directory | Closeout rule |
|--------|-----------|---------------|
| Edge observation | `docs/operations/edge_observation/` | Operator-managed rolling evidence; keep active. |
| Attribution drift | `docs/operations/attribution_drift/` | Operator-managed rolling evidence; keep active. |
| WS/poll reaction | `docs/operations/ws_poll_reaction/` | Operator-managed rolling evidence; keep active. |
| Calibration observation | `docs/operations/calibration_observation/` | Operator-managed rolling evidence; keep active. |
| Learning loop | `docs/operations/learning_loop_observation/` | Operator-managed rolling evidence; keep active. |

## Active / deferred operation packets

| Surface | Status | Why it remains in operations |
|---------|--------|------------------------------|
| `docs/operations/task_2026-04-26_ultimate_plan/2026-05-01_live_alpha/evidence/tigge_ingest_decision_2026-05-01.md` | ACTIVE runtime-gating evidence | TIGGE `entry_primary` authorization depends on this evidence path; do not archive without a replacement operator-decision path. |
| `docs/operations/task_2026-05-01_tigge_5_01_backfill/work_log.md` | DEFERRED/OPEN until TIGGE 2026-05-01 embargo clears | 2026-04-29 retry succeeded; original 2026-05-01 issue remains embargoed until 2026-05-03T00:00Z. |
| `docs/operations/task_2026-05-01_ultrareview25_remediation/PLAN.md` | OPERATOR-DEFERRED planning residue | Keeps F12/F16 and follow-up routing context until operator confirms closure or archives it. |
| `docs/operations/task_2026-05-02_review_crash_remediation/PLAN.md` | PLANNED / awaiting operator approval | Current crash-review remediation plan; do not archive until approved/refuted/closed. |
| `docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md` | ACTIVE — branch ready for critic | Oracle/Kelly evidence rebuild closes Bug review Findings A/B/C/D/E/F + 7th-site dispatch closure. Branch `oracle-kelly-evidence-rebuild-2026-05-04` (8 commits A1-A8); pending critic-opus R6 adversarial review per memory L11 before merge. ZEUS_MARKET_PHASE_DISPATCH default flips ON post-merge — operator-controlled kill-switch via env override. |

## Current fact and checklist companions

- `docs/operations/current_data_state.md`
- `docs/operations/current_source_validity.md`
- `docs/to-do-list/known_gaps.md` — active known-gap worklist
- `docs/to-do-list/known_gaps_archive.md` — closed gap antibody archive

## Closed packet archive

Closed or superseded packet bodies from the 2026-05-02 cleanup were moved to
`docs/archives/packets/` and indexed in `docs/archive_registry.md`.

## Operations routing

- `docs/operations/AGENTS.md` — packet/package routing and closeout rules
- `docs/archive_registry.md` — archived packet lookup
- `architecture/history_lore.yaml` — durable lessons
