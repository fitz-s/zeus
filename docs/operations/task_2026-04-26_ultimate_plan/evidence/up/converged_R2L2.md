# Region-Up R2L2 — Converged

Created: 2026-04-26
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (main)
Region: Up (boundary + provenance + raw payloads)
Layer: 2 (cross-module data-provenance / relationship invariants)
Status: CONVERGED — pending judge accept

Signed-by:
- proponent-up @ 2026-04-26
- opponent-up @ 2026-04-26

---

## Consensus

K=7 slice cards (up-01..up-07), with L2 enforcement gates locked.

| Card | Title | depends_on | gate |
|---|---|---|---|
| up-01 | polymarket_truth_contract.yaml (3-axis authority + collateral_tokens enum) | [] | manifest-only |
| up-02 | OrderSemantics.for_market() dispatcher | [up-01] | semgrep `zeus-create-order-via-order-semantics-only` (NC-NEW-C, mirrors NC-16) |
| up-03 | ExecutableMarketSnapshot table | [up-01] | append-only (NC-NEW-B) + Python single-insertion gate (see up-07) |
| up-04 | EXTEND venue_commands ALTER (12 cols incl. collateral_token) | [up-01, up-02, up-03, up-06] | additive ALTER + `pragma user_version` marker; forward-only retry on SQLite 3.51.2 |
| up-05 | SignedExecutionEnvelope | [up-04, up-07, X4-resolved] | construction-time fail-closed; authority derived from state |
| up-06 | UNVERIFIED rejection matrix (7 consumers) | [] **LANDS FIRST** | per-consumer guard (chain_recon, fill_tracker, harvester, cycle_summary, riskguard, executor.preflight, calibration training) |
| up-07 | snapshot↔command freshness gate | [up-03, up-04] | Python single-insertion-point in venue_command_repo + semgrep `zeus-venue-commands-repo-only` (NC-NEW-A) + StaleMarketSnapshotError |

## L2 lock-ins (all 7 attacks resolved)

| # | Attack | Resolution |
|---|---|---|
| L2-1 | "authority single linear" | 3-axis tuple in up-01 yaml: finality_order, discovery_order, realtime_order. Cross-axis disagreement expected. |
| L2-2 | "INV-NEW-A is prose, not gate" | (ii) Python single-insertion-point + semgrep NC-NEW-A `zeus-venue-commands-repo-only` (mirrors NC-16). NOT FK+trigger (now() unreachable in CHECK). |
| L2-3 | "UNVERIFIED matrix incomplete (4-row)" | 7-row matrix in up-06 (added: risk_guard, executor.preflight, calibration training). |
| L2-4 | "up-06-after-up-04 leaks UNVERIFIED" | Reverse: up-06 lands FIRST (defensive deploy: consumers reject UNVERIFIED unconditionally), up-04 lands second with `authority_tier` backfilling 'UNVERIFIED' on legacy. |
| L2-5 | "precision conflict resolution unspecified" | (iii) fail-closed via PrecisionAuthorityConflictError. No "snapshot wins"; disagreement = stale OR corrupted, both unsafe. Same for tick/min/sigtype. |
| L2-6 | "ALTER rollback unaddressed" | (i) forward-only with retry. SQLite 3.51.2 verified (`>>` 3.35); additive ALTER atomic; `pragma user_version` bump as retry marker. |
| L2-7 | "up-02 Python class can be bypassed" | (i) NC-NEW-C semgrep `zeus-create-order-via-order-semantics-only` mirrors NC-16. `ClobClient.create_order` callsites limited to allowlist. |

## Q-NEW-1 incorporation

Operator dispatched direct Polygon RPC `eth_call` (polygon.drpc.org) on `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`:
- `symbol()` → `pUSD`
- `name()` → `Polymarket USD`

Earlier "marketing label for USDC" judge ruling OVERTURNED. pUSD IS a distinct ERC-20.

Effect on Up cards:
- up-01: `collateral_tokens: ['pUSD','USDC']` enum encoded.
- up-04: `collateral_token TEXT CHECK (collateral_token IN ('pUSD','USDC') OR collateral_token IS NULL)` column added; populated by Down's redemption path.
- up-04 single ALTER preserves X-UM-1 commitment (one migration, all consumer needs).

## NC-NEW (3 new negative constraints minted under L2)

- **NC-NEW-A**: No `INSERT INTO venue_commands` outside `src/state/venue_command_repo.py`. (semgrep `zeus-venue-commands-repo-only`)
- **NC-NEW-B**: `executable_market_snapshots` is APPEND-ONLY. Old snapshots aged via freshness window check; never DELETE'd.
- **NC-NEW-C**: No `ClobClient.create_order()` outside allowlist (`src/contracts/order_semantics.py` + `src/data/polymarket_client.py` + `scripts/live_smoke_test.py`). (semgrep `zeus-create-order-via-order-semantics-only`)

## Open (deferred to L3)

- REORGED transition rules for SignedExecutionEnvelope (Layer 3 — concrete state-transition table needed).
- Cross-axis interaction: up-05 chain_anchor lifecycle vs up-01 finality/realtime axes (does CONFIRMED imply finality=Chain or finality=CLOB+Chain?).
- PrecisionAuthorityConflictError operator-fallback path (does conflict trigger an ops alert, or just hard-reject? Layer 3.)

## Cross-region asks (logged at cross_region_questions.md)

- X-UM-1 (`condition_id` Up↔Mid) → ROUTED to X3. up-04 single ALTER covers both.
- X-UD-1 (D-phase collapse impact on F-011 sequencing) → already FOLDED into X4 by judge.
- Q-NEW-1 RESOLVED-DIVERGENT (pUSD distinct ERC-20) → up-04 collateral_token col populated by Down's down-07.

## Slice card paths

- `slice_cards/up-01.yaml` (updated R2L2 — 3-axis + collateral_tokens)
- `slice_cards/up-02.yaml` (updated R2L2 — semgrep antibody + precision conflict)
- `slice_cards/up-03.yaml` (updated R2L2 — Python-gate + NC-NEW-B + collateral_token col)
- `slice_cards/up-04.yaml` (updated R2L2 — depends_on adds up-06; +collateral_token + snapshot_fk; pragma user_version marker)
- `slice_cards/up-05.yaml` (updated R2L2 — depends_on adds up-07; X4-resolved replaces X-UD-1)
- `slice_cards/up-06.yaml` (NEW — UNVERIFIED 7-row rejection matrix, lands FIRST)
- `slice_cards/up-07.yaml` (NEW — snapshot↔command freshness gate, semgrep + StaleMarketSnapshotError)
