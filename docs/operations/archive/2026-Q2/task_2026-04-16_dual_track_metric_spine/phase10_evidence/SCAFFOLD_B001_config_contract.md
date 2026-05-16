# Scaffold — B001 Config Contract Enforcement

Created: 2026-04-21
Authority basis: Phase 10 DT-close bug100 K1 tail — `src/config.py` L1 header contract

## Section 1 — Assumption Discovery

| Value | Current | Source | Volatility | Silent failure impact |
|---|---|---|---|---|
| `config/settings.json::bias_correction_enabled` | `False` | operator-edited settings.json | QUARTERLY | Bias correction silently off when operator expected on — stale Platt models, wrong Kelly size |
| `config/settings.json::feature_flags` | `{EXECUTION_PRICE_SHADOW: true, CANONICAL_EXIT_PATH: false}` | operator-edited settings.json | MONTHLY (new flags added) | Flag deletions silently default to off, no observability of flag regression |

## Section 2 — Provenance Interrogation

| Artifact | Created by / when | Assumptions | Still valid? | Recompute |
|---|---|---|---|---|
| `src/config.py` L1 header "No .get(key, fallback) pattern — every key must exist" | K1 Phase 1 commits `96b70a8` / `f6f612e` (2026-01) | Strict-contract regime: startup must fail loud on missing keys | **Partial** — `required` list enforces 11 keys, but 2 later-added properties (`bias_correction_enabled` L141, `feature_flags` L146) bypass via internal `.get(key, default)` | Derive `required` by AST walking `Settings` properties that read `self._data` — automated invariant |

## Section 3 — Cross-Module Relationships

| Relationship | Why must hold | Silent violation | Enforcement |
|---|---|---|---|
| Every `Settings.<property>` reading `self._data` → key MUST be in `required` list | Header contract: startup KeyError, not trade-time silence | Operator deletes key → startup succeeds → trade behavior diverges from intent → no alert | **Target**: AST antibody test walks `Settings` class, enumerates `@property` defs accessing `self._data`, asserts key in `required`. Makes "add property without adding to required" structurally impossible |

## Section 4 — What I Don't Know

- **Q**: Do any tests depend on silent fallback (bias_correction_enabled absent → False)?
- **Q**: Any CI / dev environment with partial settings.json? → Verified: both keys present in tracked `config/settings.json`.
- **Q**: Rollback cost if tightened? → Minimal. Import-time KeyError will be loud; fix is re-add key to settings.json.

## Completion criteria

- [x] Section 1: both external values have source + volatility
- [x] Section 2: `config.py` artifact has recompute plan (AST derive `required`)
- [x] Section 3: relationship enforced by AST antibody (not convention)
- [x] Section 4: all unknowns either verified (settings.json has both keys) or flagged
