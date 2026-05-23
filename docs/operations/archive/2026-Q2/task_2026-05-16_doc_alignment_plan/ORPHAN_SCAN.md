# Orphan Scan Report - 2026-05-16

## 1. .claude/plans/
*No plans directory found at .claude/plans/. Plan-like files detected in .omc/plans/.*

| Plan File | Status | Last Modified | Merged? |
|-----------|--------|---------------|---------|
| .omc/plans/sqlite_contention_structural_design_v4_2026_05_07.md | DRAFT | 2026-05-07 | No (Branch: deploy/live-order-e2e-verification-2026-05-15) |
| .omc/plans/track_a6_daemon_path_retrofit_2026_05_08.md | Unknown | 2026-05-08 | Unknown |
| .omc/plans/open-questions.md | ACTIVE | 2026-05-13 | N/A |

## 2. .omc/ Directory
| Item | Type | Age / Status | Recommended Action |
|------|------|--------------|-------------------|
| .omc/state/agent-replay-*.jsonl | State | 9 files > 7 days | Cleanup per CLAUDE.md |
| .omc/state/checkpoints/ | Cache | ~110 files | Review for storage optimization |
| .omc/research/cleanup_final_2026_05_08.md | Doc | 8 days old | Archive if work is done |

## 3. state/ Directory
| File | Age / Status | Recommended Action |
|------|--------------|-------------------|
| state/entry_forecast_promotion_evidence.json.lock | 10 days old (0 bytes) | **REMOVE (STALE)** |
| state/zeus_world.db | 9 days old (0 bytes) | **REMOVE (STALE)** |
| state/zeus-trades.db | 6 days old (0 bytes) | **REMOVE (STALE)** |
| state/zeus-risk.db | 6 days old (0 bytes) | **REMOVE (STALE)** |
| state/maintenance_state/ | Missing | N/A |
| state/topology_v_next_shadow/ | Missing | N/A |
| state/zeus-world.db | 1 day old | Keep (Sanity Check: PASS) |
| state/zeus-forecasts.db | 1 day old | Keep (Sanity Check: PASS) |

## 4. docs/to-do-list/
| File | Age / Status | Recommended Action |
|------|--------------|-------------------|
| docs/to-do-list/known_gaps.md | Modified 2026-05-15 | Keep (High Priority) |
| docs/to-do-list/known_gaps_archive.md | Modified 2026-05-15 | Keep |
| docs/to-do-list/AGENTS.md | 2026-05-02 | Review for staleness |

## 5. logs/ Directory
| Log Category | Size | Last Modified | Recommended Action |
|--------------|------|---------------|-------------------|
| riskguard-live.err | 9.7 MB | 2026-05-16 | Rotate if > 30 days old |
| zeus-ingest.err | 114 MB | 2026-05-15 | Rotate / Investigate size |
| zeus-live.err | 85 MB | 2026-05-16 | Rotate / Investigate size |

## 6. Open Q gaps
| File | Status | Last Modified |
|------|--------|---------------|
| .omc/plans/open-questions.md | ACTIVE | 2026-05-13 |

## 7. Symlink Integrity
- Verified 0 broken symlinks in top 20 check.

## 8. Recently Deleted (Lingering References)
| Deleted File | Deletion Commit | Lingering References? |
|--------------|-----------------|-----------------------|
| docs/operations/archive/PROPOSALS_2026-05-04.md | eba80d2b9d | None detected |
| .claude/agents/critic-opus.md | 1addab0b30 | References may exist in docs/ |

---

## Top 10 orphans worth attention
1. **state/entry_forecast_promotion_evidence.json.lock** (Severity: HIGH) - Stale lock file blocking potential writes. Remove.
2. **state/zeus_world.db** (Severity: HIGH) - Zero-byte ghost file. Remove.
3. **state/zeus-trades.db** (Severity: HIGH) - Zero-byte ghost file. Remove.
4. **state/zeus-risk.db** (Severity: HIGH) - Zero-byte ghost file. Remove.
5. **.omc/state/agent-replay-*.jsonl** (Severity: MED) - 9 files > 7 days old. Cleanup per CLAUDE.md.
6. **zeus-ingest.err** (Severity: MED) - 114MB error log. Check for rotation/excessive logging.
7. **zeus-live.err** (Severity: MED) - 85MB error log. Check for rotation/excessive logging.
8. **.omc/plans/sqlite_contention_structural_design_v4_2026_05_07.md** (Severity: LOW) - Draft plan from 9 days ago. Finalize or archive.
9. **docs/to-do-list/AGENTS.md** (Severity: LOW) - Not modified since May 2nd. Verify contents.
10. **.omc/state/checkpoints/** (Severity: LOW) - Over 100 checkpoint files. Purge old ones.

**Sum**:
- **HIGH-PRIORITY cleanups**: 4
- **LOW-PRIORITY cleanups**: 6

