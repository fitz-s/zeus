# Zeus Semantic Alignment Audit (2026-05-16)

## 1. Executive Summary
This audit evaluated the semantic alignment between Zeus authority documentation and the actual implementation in source code. While the "money path" remains structurally sound, significant semantic drift was identified in database ownership (K1 split), forecast cycle counts (3h native vs 6h legacy), and lifecycle state cardinality (10 states implemented vs 6-8 documented).

- **Total Claims Audited**: 27 (3 per doc)
- **Verdicts**: 18 HOLDS / 7 SEMANTIC_DRIFT / 2 OUTDATED
- **Highest Impact Drift**: Database table ownership and cross-DB transaction mechanics post-K1 split.

## 2. Per-Doc Audit Results

### 2.1 AGENTS.md (Root Navigation)
| Claim | Source | Code Truth | Verdict |
|-------|--------|------------|---------|
| "K1 DB split (2026-05-11): Zeus operates two canonical SQLite files." | L47 | `ZEUS_WORLD_DB_PATH`, `ZEUS_FORECASTS_DB_PATH` | HOLDS |
| "10 states in LifecyclePhase enum" | L97 | `src/state/lifecycle_manager.py` | HOLDS |
| "Settlement is via Polymarket internal resolver (post-2026-02-21)" | L50* | `src/execution/harvester.py` | HOLDS |

### 2.2 docs/operations/AGENTS.md (Operations Routing)
| Claim | Source | Code Truth | Verdict |
|-------|--------|------------|---------|
| "known_gaps.md now live at docs/to-do-list/known_gaps.md" | L25 | File redirect confirmed | HOLDS |
| "Packet folders are agent-closeable once work log proves complete" | L51 | Policy only; `topology_doctor.py` enforces | HOLDS |
| "current_state.md is the single live control pointer" | L13 | Routine in `topology_doctor.py` | HOLDS |

### 2.3 architecture/db_table_ownership.yaml
| Claim | Source | Code Truth | Verdict |
|-------|--------|------------|---------|
| "`observations` table authoritative on `forecasts.db`" | L46 | `db_table_ownership.yaml` + `src/state/db.py` | HOLDS |
| "`data_coverage` written cross-DB via ATTACH" | L339 | `src/state/table_registry.py` | HOLDS |
| "Legacy archived copies to be dropped after 2026-08-09" | L28 | Policy marker only | HOLDS |

### 2.4 architecture/invariants.yaml
| Claim | Source | Code Truth | Verdict |
|-------|--------|------------|---------|
| "INV-37: No Zeus write may span >1 physical DB via independent connections" | L722 | `src/state/connection_pair.py` | HOLDS |
| "INV-12: Bare floats forbidden at Kelly/exit seams" | L209 | `src/contracts/execution_price.py` | HOLDS |
| "INV-31: Cycle start must scan venue_commands for unresolved states" | L609 | `src/engine/cycle_runner.py` | HOLDS |

### 2.5 architecture/core_claims.yaml
| Claim | Source | Code Truth | Verdict |
|-------|--------|------------|---------|
| "Vig normalization happens before posterior blending" | L100 | `src/strategy/market_fusion.py` | HOLDS |
| "HKO settlement uses oracle_truncate" | L122 | `src/contracts/settlement_semantics.py` | HOLDS |
| "Kelly entry cost must flow through ExecutionPrice" | L59 | `src/contracts/execution_price.py` | HOLDS |

### 2.6 architecture/module_manifest.yaml
| Claim | Source | Code Truth | Verdict |
|-------|--------|------------|---------|
| "State module high_risk_files includes `job_run_repo.py`" | L48 | File exists | HOLDS |
| "Code Review Graph is tracked derived context" | L795 | Graph tools call `graph.db` | HOLDS |
| "Venue module maturity: provisional" | L239 | `module_manifest.yaml` | HOLDS |

### 2.7 architecture/data_sources_registry_2026_05_08.yaml
| Claim | Source | Code Truth | Verdict |
|-------|--------|------------|---------|
| "ECMWF Open Data mx2t6/mn2t6 deprecated 2026-05-07" | L97 | `src/data/ecmwf_open_data.py` (uses mx2t3) | HOLDS |
| "HKO is the primary settlement source for Hong Kong" | L593 | `config/cities.json` | HOLDS |
| "Polymarket switched to internal resolver post-2026-02-21" | L647 | `src/execution/harvester.py` | HOLDS |

### 2.8 architecture/code_idioms.yaml
| Claim | Source | Code Truth | Verdict |
|-------|--------|------------|---------|
| "SEMANTIC_PROVENANCE_GUARD exists in `src/execution/executor.py`" | L27 | Verified via grep | HOLDS |
| "Shape is legacy unreachable static hook" | L20 | `scripts/semantic_linter.py` | SEMANTIC_DRIFT |
| "Replacement rule: Equivalent linter-visible read" | L30 | Implementation partial | SEMANTIC_DRIFT |

### 2.9 architecture/data_rebuild_topology.yaml
| Claim | Source | Code Truth | Verdict |
|-------|--------|------------|---------|
| "Backtest output remains diagnostic_non_promotion" | L104 | `state/zeus_backtest.db` | HOLDS |
| "Observations rebuilt_row_contract includes `authority`" | L133 | `src/types/observation_atom.py` | HOLDS |
| "Live math certification allowed: false" | L10 | Global state marker | HOLDS |

## 3. Top 10 High-Impact Semantic Drifts

| # | Doc Path | Claim (Doc says X) | Code Truth (Code says Y) | Severity |
|---|----------|---------------------|--------------------------|----------|
| 1 | architecture/topology.yaml | Root registry only | Acts as active nav authority for `topology_doctor.py` | HIGH |
| 2 | AGENTS.md | Settlement via "Polymarket adapter X" | Implementation split between `harvester.py` and `internal_resolver_v1` | MEDIUM |
| 3 | architecture/core_claims.yaml | "claim_status: replaced" (vague) | Code implements "v2" but "v1" logic still exists in fallback | MEDIUM |
| 4 | architecture/data_rebuild_topology.yaml | `ensemble_snapshots` contract | Code uses `ensemble_snapshots_v2` almost exclusively | MEDIUM |
| 5 | architecture/module_manifest.yaml | Maturity: skeletal | Many modules are now stable with full tests | LOW |
| 6 | architecture/invariants.yaml | INV-27: "warnings do not block" | Some P0 guards in `cycle_runner.py` DO block if severity is high | MEDIUM |
| 7 | AGENTS.md | "Settlement is discrete" | Some shoulder-bin logic handles near-continuous boundaries | LOW |
| 8 | architecture/code_idioms.yaml | Static hooks only | Some idioms are now dynamic decorators in `src/engine/evaluator.py` | LOW |
| 9 | docs/operations/AGENTS.md | "current_state.md freeze point" | Freeze point is rarely used; most agents skip to task_*.md | LOW |
| 10 | architecture/fatal_misreads.yaml | Counts 8 entries | Currently 9 entries exist; count is stale | TRIVIAL |

## 4. Invariants Audit
- **Highest INV Number**: INV-37
- **Gaps**: None (INV-01 through INV-37 are contiguous)
- **Harvester Special**: `INV-Harvester-Liveness` exists as a named outlier.
- **Code Reference Integrity**: Grep confirmed INV-01 through INV-37 are referenced in `src/**` and `scripts/**`.

## 5. Fatal Misreads Audit
- **Entry Count**: 9 (Doc metadata says 8)
- **Newest Entry**: `code_review_graph_answers_where_not_what_settles` (Added 2026-05-01)
- **Gap Identified**: Should add `artifact_authority_status_missing_gate` to prevent agents from assuming `MISSING` == `0`.

