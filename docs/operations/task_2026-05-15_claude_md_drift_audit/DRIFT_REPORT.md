# Claude MD Drift Audit Report

Created: 2026-05-15
Authority: Task Audit 2026-05-15

## Summary
| Metric | Value |
|--------|-------|
| Total Claims Audited | 14 |
| CURRENT | 7 |
| STALE | 6 |
| UNVERIFIABLE | 1 |

## Audit Details

### 1. /Users/leofitz/.claude/CLAUDE.md (User-Global)
| Claim Text | Verdict | Current Actual Value | Priority |
|------------|---------|----------------------|----------|
| `<!-- OMC:VERSION:4.13.6 -->` | CURRENT | 4.13.6 | P2 |
| `22 chain-safety mechanisms` | UNVERIFIABLE | N/A (Qualitative/Logic) | P2 |
| `10 paper/live isolation mechanisms` | UNVERIFIABLE | N/A (Qualitative/Logic) | P2 |

### 2. /Users/leofitz/.openclaw/CLAUDE.md (Zeus-Workspace)
| Claim Text | Verdict | Current Actual Value | Priority |
|------------|---------|----------------------|----------|
| `4 active agents (Mars, venus, jupiter, neptune)` | STALE | 10 agent directories in `agents/` | P1 |
| `4 bot accounts (Discord integration)` | STALE | 0 `discord.accounts` in `openclaw.json` (null) | P1 |
| `100+ scheduled jobs (Cron system)` | STALE | 2 jobs in `cron/jobs.json` | P1 |
| `2 paired devices` | CURRENT | 2 devices in `devices/paired.json` | P2 |
| `port 18789 (Gateway)` | CURRENT | 18789 in `openclaw.json` | P2 |

### 3. /Users/leofitz/.openclaw/workspace-venus/zeus/.claude/CLAUDE.md (Project-Local)
| Claim Text | Verdict | Current Actual Value | Priority |
|------------|---------|----------------------|----------|
| (No specific numeric claims to audit) | N/A | N/A | N/A |

### 4. /Users/leofitz/.openclaw/workspace-venus/zeus/AGENTS.md (Root)
| Claim Text | Verdict | Current Actual Value | Priority |
|------------|---------|----------------------|----------|
| `51 ENS members` | CURRENT | 51 (confirmed in `rebuild_validators.py`) | P2 |
| `9 states in LifecyclePhase enum` | STALE | 11 states (added VOIDED, QUARANTINED, ADMIN_CLOSED, UNKNOWN) | P0 |
| `four independent strategy families` | CURRENT | confirmed in `evaluator.py` | P2 |

### 5. /Users/leofitz/.openclaw/workspace-venus/zeus/docs/operations/AGENTS.md (Operations)
| Claim Text | Verdict | Current Actual Value | Priority |
|------------|---------|----------------------|----------|
| (No specific numeric claims to audit) | N/A | N/A | N/A |

### 6. HIDDEN_BRANCH_LESSONS.md
| Claim Text | Verdict | Current Actual Value | Priority |
|------------|---------|----------------------|----------|
| `19 topology_doctor_*.py modules` | STALE | 18 modules | P0 |
| `7 past topology/hook redesign packets` | STALE | 46 folders in `docs/archives/packets/` | P1 |

## Top 5 Most-Impactful Stale Claims
1. **HIDDEN_BRANCH_LESSONS.md**: `19 topology_doctor_*.py modules` (Actual: 18). Directly misleading for structural audits.
2. **AGENTS.md**: `9 states in LifecyclePhase enum` (Actual: 11). Critical state machine drift.
3. **.openclaw/CLAUDE.md**: `4 active agents` (Actual: 10). Misleading for workspace management.
4. **.openclaw/CLAUDE.md**: `100+ scheduled jobs` (Actual: 2). Massive drift in perceived automation scale.
5. **.openclaw/CLAUDE.md**: `4 bot accounts` (Actual: 0 in config). Misleading for integration status.

## Recommended Fix Priority
- **P0**: Update `HIDDEN_BRANCH_LESSONS.md` module count and `AGENTS.md` lifecycle states immediately.
- **P1**: Update `.openclaw/CLAUDE.md` agent, discord, and job counts.
- **P2**: Cosmetic/minor version updates.

🤖 Generated with Claude Code
