# Provenance Addendum — Commit 32e5e2a307

**Purpose**: Retroactively document §8.5 Rule 4 provenance triples for 6 fixes
silently absorbed into commit `32e5e2a307` (wave-3-batch-c-tier-1-redo) without
enumeration in that commit's message. Fix content was verified CORRECT by WAVE_3_CRITIC;
this addendum closes the audit-trail gap only.

**Audit note**: Commit `32e5e2a307` declared 2 drifts in its message body but
committed 8 file changes; 6 of those changes were Batch D target docs applied
without provenance documentation. Per §8.5 Rule 4, every changed authoritative
statement must carry a provenance triple. This file supplies those triples retroactively.

---

## Fix 1 — `src/execution/AGENTS.md`

**Location**: `src/execution/AGENTS.md::Settlement Truth Writes`

**REPLACES**: `Uses UPSERT (UPDATE then INSERT if no match).`

**WHY**: `incorrect` — harvester.py uses `INSERT OR REPLACE` (SQLite
DELETE+INSERT idempotency), not an UPDATE-then-INSERT pattern. These are
semantically distinct: INSERT OR REPLACE deletes the conflicting row first,
then inserts; no UPDATE step occurs.

**VERIFIED-AT**: `src/execution/harvester.py` comment at line 1295
(`# INSERT OR REPLACE matches P-E's canonical DELETE+INSERT idempotency`)
and SQL at line 1300 (`INSERT OR REPLACE INTO settlements`).

---

## Fix 2 — `src/signal/AGENTS.md` (3 dead-ref rows removed)

**Location**: `src/signal/AGENTS.md::Key files` table

**REPLACES**:
- `| \`day0_residual.py\` | Day0 residual target/fact substrate | MEDIUM |`
- `| \`day0_residual_features.py\` | Point-in-time Day0 residual feature helpers | MEDIUM |`
- `| \`forecast_error_distribution.py\` | Forecast error distribution substrate | MEDIUM |`

**WHY**: `dead-ref` — none of the three files exist in `src/signal/`.
Confirmed via `ls src/signal/` + `git grep` returning zero hits for all three
names across the entire repository.

**VERIFIED-AT**: `ls src/signal/` (session 2026-05-17); `git grep day0_residual`
returns no source hits; `git grep forecast_error_distribution` returns no source hits.

---

## Fix 3 — `src/strategy/AGENTS.md` (3 sub-fixes)

### 3a — OracleStatus enum name

**Location**: `src/strategy/AGENTS.md::Oracle Penalty`

**REPLACES**: `` `OracleStatus.BLACKLISTED` blocks trading ``

**WHY**: `incorrect` — enum value is `BLACKLIST` not `BLACKLISTED`.
`OracleStatus.BLACKLISTED` would be an `AttributeError` at runtime.

**VERIFIED-AT**: `src/strategy/oracle_status.py::OracleStatus` line 30
(`BLACKLIST = "BLACKLIST"`); `src/strategy/oracle_penalty.py` line 102
(`OracleStatus.BLACKLIST: 0.0`).

### 3b — Oracle thresholds

**Location**: `src/strategy/AGENTS.md::Oracle Penalty`

**REPLACES**: `Thresholds: <3% OK, 3–10% CAUTION, >10% BLACKLISTED`

**WHY**: `incorrect` — thresholds are posterior-tail probabilities, not
error-rate percentages. Boundaries are 0.05 and 0.10 on `posterior_upper_95`,
not percentage ranges of error_rate. The old text implied simple percentage
cutoffs which misrepresents the Bayesian classification logic.

**VERIFIED-AT**: `src/strategy/oracle_status.py` docstring lines 8-11
(OK ≤ 0.05, CAUTION (0.05, 0.10], BLACKLIST > 0.10);
`src/strategy/oracle_penalty.py` lines 25-31 (posterior tail threshold logic).

### 3c — provenance_registry.yaml path

**Location**: `src/strategy/AGENTS.md::Common mistakes`

**REPLACES**: `registering it in \`provenance_registry.yaml\``

**WHY**: `path-rot` — file lives at `config/provenance_registry.yaml`; bare
filename is unresolvable without path context.

**VERIFIED-AT**: `find . -name provenance_registry.yaml` → `./config/provenance_registry.yaml`;
`src/strategy/kelly.py` line 26 imports `from src.contracts.provenance_registry import require_provenance`.

---

## Fix 4 — `docs/operations/learning_loop_observation/AGENTS.md`

**Location**: `docs/operations/learning_loop_observation/AGENTS.md::HONEST DISCLOSURE`

**REPLACES**: `` `calibration_params_versions` (`src/calibration/retrain_trigger.py:242-264` ``

**WHY**: `stale` — line-cite rot; function `_ensure_versions_table` (which
contains the `calibration_params_versions` CREATE TABLE) starts at line 240,
not 242. Symbol reference is stable; line numbers rot with code changes.

**VERIFIED-AT**: `src/calibration/retrain_trigger.py::_ensure_versions_table`
(function definition at line 240, CREATE TABLE at line 245).

---

## Fix 5 — `docs/operations/calibration_observation/AGENTS.md`

**Location**: `docs/operations/calibration_observation/AGENTS.md::CORRECTION`

**REPLACES**: `` (`src/calibration/retrain_trigger.py:242-264` schema) ``

**WHY**: `stale` — same line-cite rot as Fix 4; identical schema, same function.
Symbol reference replaces fragile line number.

**VERIFIED-AT**: `src/calibration/retrain_trigger.py::_ensure_versions_table`
(same as Fix 4).

---

## Fix 6 — `docs/operations/ws_poll_reaction/AGENTS.md`

**Location**: `docs/operations/ws_poll_reaction/AGENTS.md::Per-strategy threshold table`

**REPLACES**: `` Alpha decays fastest here (bot scanning per `AGENTS.md` L114-126) ``

**WHY**: `stale` — line 114-126 of root AGENTS.md covers chain reconciliation
(SYNCED/VOID/QUARANTINE table), not opening_inertia strategy rationale.
The opening_inertia "Fastest (bot scanning)" entry is at line 137 of root
AGENTS.md in the Strategy families table.

**VERIFIED-AT**: `AGENTS.md::Strategy families` table (line 137:
`| Opening Inertia | New market mispricing | Fastest (bot scanning) |`).

---

*Addendum authored 2026-05-17 per WAVE_3_CRITIC R2 instruction.*
*All fix content verified correct by critic; this file documents audit trail only.*
