# TRIVIAL_BATCH_NOTES — wave2 trivial sweep 2026-05-17

| F# | Outcome | SHA / Reason |
|----|---------|--------------|
| F17 | DOCUMENT | 156fa62f — scheduling rationale in evaluate_calibration_transfer_oos.py header (Phase B path, deliberately dormant) |
| F27 | RETRACT | 7468218e — fixed by PR #137 (migration 202605_add_redeem_operator_required_state.py) |
| F32 | NOTE | c0512262 — cron proposal in docs/operations/CRON_PROPOSALS_F32_F35.md; operator adds to jobs.json after F44 resolved |
| F35 | NOTE | c0512262 — bundled with F32; same proposal file |
| F86 | NOTE | 809f707f — deferred to post-Karachi ops-debt PR; per-daemon SIGTERM handler or healthcheck.py last_exit_status antibody |
| F89 | RETRACT | 3213fc2c — heartbeat-sensor PID='-' is correct for cron-driven StartCalendarInterval plist |
| F92 | FIX | 3d69f315 — VENUE_AUTH_FALLBACK_TRIGGERED WARNING log in polymarket_v2_adapter; test passes |
| F101 | MISCLASSIFY | 6899ff83 — 5-writer schema unification; audit defers to follow-on PR |
| F104 | FIX | 8e1588e2 — PERSISTENCE_NO_DATA DEBUG log when temp_persistence has no row; test passes |
| F105 | MISCLASSIFY | 2de6de66 — fix site src/execution/exit_lifecycle.py (excluded surface) |
| F107 | FIX | e6061dc3 — _non_empty skips "unknown_entered_at" sentinel; CHAIN_SYNCED occurred_at now uses chain_verified_at; test passes |
| F109 | MISCLASSIFY | afe4ae3d — requires src/execution/ + schema migration (excluded) |

Misclassified (4): F101, F105, F109 — excluded live cascade surfaces or multi-file structural refactors.
Retracted (2): F27 (PR #137), F89 (cron-driven plist, correct steady-state).
Fixed (3): F92, F104, F107 — each ≤5 LOC with regression test.
Documented/noted (3): F17 (Phase B rationale), F32+F35 (cron proposal), F86 (ops-debt note).
