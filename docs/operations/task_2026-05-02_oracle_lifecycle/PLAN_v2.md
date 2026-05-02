# Oracle Artifact Lifecycle Fix — Plan v2

**Status**: DRAFT (awaiting critic-opus adversarial review)
**Author**: team-lead (Claude opus-4-7)
**Date**: 2026-05-02
**Scope**: Resolve oracle artifact worktree-pinning + redesign fail-closed gate to tiered fallback

---

## 1. Problem Statement

### Symptom
Live trading daemon `com.zeus.live-trading` shut down today because oracle artifacts (`data/oracle_error_rates.json`) were missing in the active worktree. `evaluator.py` fail-closes ALL trades when this file is absent → "ORACLE_EVIDENCE_UNAVAILABLE" → system halt.

### Root causes (4 defects identified)

| # | Defect | Evidence |
|---|--------|----------|
| D1 | Writers worktree-pinned | `crontab` line: `cd /Users/leofitz/.openclaw/workspace-venus/zeus && ... oracle_snapshot_listener.py` |
| D2 | Readers worktree-pinned, two inconsistent path resolutions | `evaluator.py:109` uses `PROJECT_ROOT / "data"`; `oracle_penalty.py:23` uses `Path(__file__).resolve().parent.parent.parent / "data"` |
| D3 | `bridge_oracle_to_calibration.py` has NO scheduled invocation | `crontab -l \| grep bridge` returns nothing; `oracle_error_rates.json` only refreshes on manual trigger |
| D4 | Artifacts gitignored: `/raw/*` + `/data/oracle_error_rates.json` | `.gitignore` lines 125-155 |

### Operator constraint (added 2026-05-02)
> "oracle 数据不新鲜不能卡住 live 交易,因为 Kelly 折损对 live 影响其实较低,就算做错了 Monte Carlo 和 Kelly 计算也能最小化损失,但是 live 被 block 了就是系统停滞。"

**Translation**: oracle should DEGRADE Kelly sizing, not HALT trading. Halt only on unbounded-harm scenarios; oracle calibration error is bounded harm.

---

## 2. Design

### 2.1 Storage path centralization (path fix)

New module `src/contracts/storage_paths.py`:

```python
import os
from pathlib import Path

ZEUS_STORAGE_ROOT = Path(
    os.environ.get("ZEUS_STORAGE_ROOT", "~/.openclaw/storage/zeus")
).expanduser()

ORACLE_DIR = ZEUS_STORAGE_ROOT / "oracle"
ORACLE_SHADOW_SNAPSHOTS = ORACLE_DIR / "shadow_snapshots"

# Default live file path; overridable for emergency rollback
ORACLE_ERROR_RATES = Path(os.environ.get(
    "ZEUS_ORACLE_FILE_OVERRIDE",
    str(ORACLE_DIR / "error_rates.json")
))
```

Replace 4 hardcoded sites:
- `scripts/oracle_snapshot_listener.py:43` → import `ORACLE_SHADOW_SNAPSHOTS`
- `scripts/bridge_oracle_to_calibration.py:45-46` → import `ORACLE_SHADOW_SNAPSHOTS` + `ORACLE_ERROR_RATES`
- `src/engine/evaluator.py:109` → via `OracleEvidenceRepository` (see 2.3)
- `src/strategy/oracle_penalty.py:23-24` → via `OracleEvidenceRepository`

### 2.2 Tiered fallback hierarchy (kill the halt)

Replace evaluator's binary fail-closed with a 5-tier sizing degradation:

| Tier | Trigger | Source | Kelly multiplier | Heartbeat alert |
|------|---------|--------|------------------|-----------------|
| **T1 FRESH** | live file mtime <26h | `~/.openclaw/storage/zeus/oracle/error_rates.json` | 1.0× | none |
| **T2 STALE** | mtime 26h-7d | live file (still used) | 0.8× | warn |
| **T3 OLD** | mtime >7d, file readable | live file | 0.5× | escalate |
| **T4 FLOOR** | live file unreadable | **`data/oracle_error_rates_floor.json`** (git-tracked) | 0.5× | escalate |
| **T5 HARDCODE** | floor file also unreadable | hardcoded constant in `OracleEvidenceRepository._ULTIMATE_FLOOR` | 0.5× | critical |

**T5 ensures NEVER halt**. The previous `ORACLE_EVIDENCE_UNAVAILABLE` exception path is **deleted entirely** — it is not reachable.

### 2.3 `OracleEvidenceRepository` (single read entry)

```python
# src/engine/oracle_evidence_repository.py
from datetime import datetime, timezone
from pathlib import Path
import json
import logging
from src.contracts.storage_paths import ORACLE_ERROR_RATES

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FLOOR_FILE = REPO_ROOT / "data" / "oracle_error_rates_floor.json"

# Last-resort hardcoded constant — ensures repository NEVER raises
_ULTIMATE_FLOOR = {
    "_default": {"high": 0.05, "low": 0.05},
    "_metadata": {"tier": "T5_HARDCODE", "doc": "embedded in code"},
}

class OracleEvidenceRepository:
    def __init__(self, *, now: datetime | None = None):
        self._now = now or datetime.now(timezone.utc)

    def resolve(self) -> tuple[dict, str, float, float]:
        """Return (error_rates_dict, tier, age_hours, kelly_multiplier).
        NEVER raises. NEVER returns None.
        """
        # Tier 1-3: live file
        if ORACLE_ERROR_RATES.exists():
            try:
                age_h = (self._now.timestamp() - ORACLE_ERROR_RATES.stat().st_mtime) / 3600
                data = json.loads(ORACLE_ERROR_RATES.read_text())
                if age_h < 26:
                    return data, "T1_FRESH", age_h, 1.0
                if age_h < 24 * 7:
                    return data, "T2_STALE", age_h, 0.8
                return data, "T3_OLD", age_h, 0.5
            except (OSError, json.JSONDecodeError) as e:
                logging.warning("oracle live file unreadable: %s", e)

        # Tier 4: shipped floor
        if FLOOR_FILE.exists():
            try:
                return json.loads(FLOOR_FILE.read_text()), "T4_FLOOR", float("inf"), 0.5
            except (OSError, json.JSONDecodeError) as e:
                logging.error("oracle floor file unreadable: %s", e)

        # Tier 5: hardcoded ultimate
        logging.critical("oracle T5 HARDCODE — both live and floor unreadable")
        return _ULTIMATE_FLOOR, "T5_HARDCODE", float("inf"), 0.5
```

### 2.4 Floor file (committed to git)

`data/oracle_error_rates_floor.json`:
```json
{
  "_metadata": {
    "version": "floor_v1",
    "doc": "Last-resort conservative baseline for oracle error rates. Used when live error_rates.json is unreadable. Designed to OVERSTATE error → undersize Kelly. Never auto-updated. PR + review required to change.",
    "review_cadence": "quarterly_PR",
    "last_reviewed": "2026-05-02"
  },
  "_default": {"high": 0.05, "low": 0.05},
  "_overrides": {
    "Lagos":     {"high": 0.10, "low": 0.10},
    "Hong Kong": {"high": 0.08, "low": 0.08}
  }
}
```

**Initial values 0.05 are placeholders pending empirical analysis** (see Open Question Q1).

### 2.5 Bridge cron + permissions

- Add cron entry: `5 10 * * * cd /Users/leofitz/.openclaw/workspace-venus/zeus && .venv/bin/python scripts/bridge_oracle_to_calibration.py >> /Users/leofitz/.openclaw/logs/oracle-bridge.log 2>&1`
- `bridge_oracle_to_calibration.py` writes with atomic `.tmp + os.replace()` and final `chmod 0o664`
- Verify launchd-vs-cron user identity:
  ```bash
  launchctl print gui/$(id -u)/com.zeus.live-trading | grep euid
  crontab -l | head -1  # implicit current user
  ```

### 2.6 Heartbeat instrumentation

`state/daemon-heartbeat-ingest.json` and `state/daemon-heartbeat.json` add fields:
```json
{
  "oracle_evidence_age_hours": 14.5,
  "oracle_evidence_tier": "T1_FRESH",
  "oracle_evidence_kelly_multiplier": 1.0
}
```

External monitor alerts when `tier ∈ {T3, T4, T5}` for >2 consecutive heartbeats.

### 2.7 Migration

1. `cp data/oracle_error_rates.json ~/.openclaw/storage/zeus/oracle/error_rates.json`
2. `cp -r raw/oracle_shadow_snapshots/ ~/.openclaw/storage/zeus/oracle/shadow_snapshots/`
3. Verify SHA256 match
4. Old paths kept for 7 days (read-only fallback removed; just on disk for archaeological recovery)
5. `.gitignore` updates: drop `/data/oracle_error_rates.json` from ignore list (no longer written there); KEEP `/raw/*` ignore (snapshots stay out of git, just relocated)

### 2.8 Tests

`tests/contracts/test_oracle_evidence_repository.py`:
- T1: file with mtime now → tier T1_FRESH, mult=1.0
- T2: file with mtime -36h → tier T2_STALE, mult=0.8
- T3: file with mtime -10d → tier T3_OLD, mult=0.5
- T4: file deleted, floor present → tier T4_FLOOR, mult=0.5
- T5: floor also deleted → tier T5_HARDCODE, mult=0.5
- **Invariant**: `resolve()` never raises in any of these states
- **Invariant**: `kelly_multiplier ∈ {1.0, 0.8, 0.5}` always returned

`tests/contracts/test_oracle_storage_invariants.py`:
- No file in `src/` or `scripts/` contains literal `"data" / "oracle_error_rates"` or `"data/oracle_error_rates"`
- All callers go through `OracleEvidenceRepository`
- Floor file exists, parses, has `_default.high` and `_default.low` as floats in [0, 1]

### 2.9 Verification sequence (gate before live-trading restart)

1. `python scripts/oracle_snapshot_listener.py` writes to new path ✓
2. `python scripts/bridge_oracle_to_calibration.py` writes new error_rates.json ✓
3. `pytest tests/contracts/test_oracle_evidence_repository.py` all pass ✓
4. Manually delete live file → run repository smoke test → see T4_FLOOR returned with mult=0.5 ✓
5. Cron entries verified: listener at 10:00, bridge at 10:05 ✓
6. heartbeat shows tier + age fields ✓
7. ONLY THEN: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.zeus.live-trading.plist`

---

## 3. Out of scope (split tasks)

- **#17 UMA timing alignment** (scout finding L): UMA proposer may submit non-10:00 UTC. bridge needs retry/timestamp-align. Separate task.
- **#13 daemon never-offline resilience**: launchd KeepAlive failed today; needs watchdog. Already in queue.
- **Adjacent fail-closed gates audit**: any other gates with same "should-degrade-not-halt" pattern? (operator's open question Q4) — separate audit task.

---

## 4. Open questions for operator

| Q | Decision needed |
|---|----------------|
| **Q1** | Floor file `_default.high/low = 0.05` placeholder. Should run DB analysis on actual per-city error rate distribution (last 90 days) to set evidence-based defaults? |
| **Q2** | Kelly multipliers 1.0/0.8/0.5. Too aggressive degradation for T2 (oracle 30h stale = drop sizing 20%)? Alternative: 1.0/0.95/0.7/0.5/0.5? |
| **Q3** | Should T5 hardcoded value be 0.05 (matches floor file default) or more conservative 0.10? |
| **Q4** | Audit other fail-closed gates? bankroll, on-chain auth, market data, WU live — which warrant degrade vs halt? |
| **Q5** | T2/T3 boundary 7d arbitrary. Tie to bridge cron cadence + N×grace? |

---

## 5. Risk register

| Risk | Severity | Mitigation |
|------|----------|------------|
| Path migration races (cron writes old path while reader expects new) | Med | Update cron BEFORE flipping reader; verify with smoke test |
| launchd-vs-cron user owns floor file → bridge can't overwrite | High | `chmod 0o664` + verify both run as `leofitz` user |
| Floor file rots (no enforcement of quarterly review) | Med | Add `last_reviewed` field check in CI; warn if >180d |
| Hardcoded T5 value is wrong (5% might oversize on Lagos which is 12%) | Med | T5 only triggers when both live and floor missing — should be never; extreme edge |
| Kelly always-degraded if mtime detection broken | High | Test mtime detection across timezones + filesystem types (HFS+, APFS) |
| Removing `ORACLE_EVIDENCE_UNAVAILABLE` breaks downstream callers | High | grep all references; update or remove cleanly |

---

## 6. Implementation phases

| Phase | Steps | Reversible? |
|-------|-------|-------------|
| P1 | Add storage_paths.py + OracleEvidenceRepository + floor file (no caller changes yet) | yes |
| P2 | Migrate data; update writers (listener + bridge) to new path | yes (writers can dual-write briefly) |
| P3 | Update readers (evaluator + oracle_penalty) through repository | yes (env var override) |
| P4 | Add bridge cron entry | yes |
| P5 | Heartbeat instrumentation | yes |
| P6 | Verification + live-trading restart | gated |

Each phase commits separately. P3 + P6 are the high-risk steps.
