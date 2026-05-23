# P3 Topology v_next Phase 2 — Shadow Blocking — SCAFFOLD

Created: 2026-05-15
Status: SPEC ONLY — no implementation code; this document is the build contract for P3
Authority basis:
- docs/operations/task_2026-05-15_runtime_improvement_engineering_package/05_execution_packets/PACKET_INDEX.md (P3 row, commit d6eac5d21a / e9d94c5630)
- docs/operations/task_2026-05-15_runtime_improvement_engineering_package/01_topology_v_next/UNIVERSAL_TOPOLOGY_DESIGN.md (§11 output normalization, §12 friction patterns, §1.1 glossary)
- docs/operations/task_2026-05-15_runtime_improvement_engineering_package/01_topology_v_next/ZEUS_BINDING_LAYER.md
- docs/operations/task_2026-05-15_runtime_improvement_engineering_package/01_topology_v_next/MIGRATION_PATH.md (Phase 2 entry/exit)
- docs/operations/task_2026-05-15_runtime_improvement_engineering_package/01_topology_v_next/HIDDEN_BRANCH_LESSONS.md (Cross-Iteration Meta-Pattern)
- docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md (rev 1.2 @ commit 1ebf1a7079; §6 "Open Items for P2 Packet" enumerates P3 deferrals)
- docs/operations/task_2026-05-15_p2_companion_required_mechanism/SCAFFOLD.md (@ commit 48fa92d3fe; §0.A prereqs + §3 MISSING_COMPANION + §5 skip-token log)
- scripts/topology_doctor.py:2636 `run_navigation` (grep-verified 2026-05-15; signature spans lines 2636–2652; final `return { ... }` at line 2753)
- scripts/topology_doctor_cli.py:94 `--intent` argparse declaration (grep-verified 2026-05-15); dispatch at line 404 `api.run_navigation(...)`; kwargs assembly at line 388 `_batch_cap = getattr(args, ...)`

P3 transitions v_next from "structures only" (P1's reframed scope) to "shadow blocking — advises but does not block; captures per-call divergence vs current admission". It ships the wire-up shim, divergence logger, divergence analyzer, normalized tool-output envelope, and the 14-day shadow probe sequence that gates the P4 cutover decision. Current admission remains authoritative throughout P3. The deliverables resolve the open items §6.1–§6.10 of P1 SCAFFOLD rev 1.2 and the P2 SCAFFOLD §0.A.2 cutover-trigger dependency.

**Density note**: this SCAFFOLD is ~870 lines (target range 350–450 lines from GOAL). The overage is load-bearing — driven by (a) §5's 17-probe enumeration with explicit kill criteria covering all 7 UNIVERSAL §12 friction patterns, (b) §3's 5-hunk grep-verified diff with full surrounding context including the single-hunk §3.4 covering the entire return dict, (c) §0's 6 explicit input inconsistencies (each requiring a binding-precedence resolution), and (d) §9's P2.1 SEV-3 carry-forward (P1.0 had no equivalent). None of these can compress without losing critic-resilience.

---

## §0. Input Inconsistencies Found (binding instruction precedence)

INCONSISTENCY-1 (MAJOR): GOAL §WHY field list `ok/decision/advisory/blockers` vs UNIVERSAL §11 actual field list.
- GOAL paraphrases UNIVERSAL §11 as mandating fields `ok, decision, advisory, blockers`.
- UNIVERSAL §11 (lines 296–312) actually mandates the **AdmissionDecision struct shape** from §2.3, populated with `issues`, `missing_phrases`, `closest_rejected_profile`, `friction_budget_used`. There is **no field named `decision`, `advisory`, or `blockers`** anywhere in §11 or §2.3.
- P1 SCAFFOLD §1.2 dataclasses are aligned with §2.3 — `AdmissionDecision(ok, profile_matched, intent_class, severity, issues, companion_files, missing_phrases, closest_rejected_profile, friction_budget_used, diagnosis, kernel_alerts)`. There is no `decision/advisory/blockers` field.
- Resolution (binding): the GOAL's `{ok, decision, advisory, blockers}` is interpreted as a **shim-output envelope shape** layered on top of AdmissionDecision — a normalized tool-result wrapper that the CLI shim derives from AdmissionDecision for human/agent consumption, NOT a new dataclass field set on AdmissionDecision itself. The AdmissionDecision struct (P1) is UNCHANGED. The shim computes:
  - `ok` ← AdmissionDecision.ok
  - `decision` ← AdmissionDecision.severity (one of ADMIT|ADVISORY|SOFT_BLOCK|HARD_STOP)
  - `advisory` ← `[IssueRecord for issue in AdmissionDecision.issues if issue.severity == ADVISORY]`
  - `blockers` ← `[IssueRecord for issue in AdmissionDecision.issues if issue.severity in {SOFT_BLOCK, HARD_STOP}]`
  - PASS-THROUGH: `profile_matched, intent_class, missing_phrases, closest_rejected_profile, friction_budget_used, companion_files, diagnosis, kernel_alerts` all carried through verbatim
- Flag for ZEUS_BINDING reviewer: UNIVERSAL §11 should be amended in a follow-up doc-only packet to either (a) explicitly name the envelope shape `{ok, decision, advisory, blockers}` as the agent-facing surface or (b) clarify that AdmissionDecision is the only normalized output and the envelope is a P3 shim concern, not a §11 requirement. P3 ships per (a) interpretation — the shim envelope is named explicitly so the contract is self-documenting; if reviewer prefers (b), the §2.4 module's `to_envelope()` method becomes a no-op identity transformation and AdmissionDecision is returned directly. The choice is one-line change in `cli_integration_shim.format_output()`.

INCONSISTENCY-2 (MAJOR): Admission-call threshold mismatch.
- MIGRATION_PATH §Phase 2 exit criterion (line 105): "Shadow runs for at least 10 distinct admission calls (mix of plan, create_new, modify, audit intents)"
- PACKET_INDEX P3 row (line 116): "14 days of shadow blocking with ≥500 admission calls"
- 50× discrepancy.
- Resolution (binding): PACKET_INDEX governs because (a) it is the more recent, more specific document, written explicitly to set P3 acceptance, and (b) MIGRATION_PATH's "≥10" is a minimum-viable smoke threshold from an early sketch, not a Phase-2 acceptance floor. P3 acceptance uses **≥500 admission calls over 14 calendar days** with a fallback floor of ≥100 calls (P1's coverage probe baseline) if calendar-window admission volume is low. The analyzer flags "insufficient sample" when count < 100; "marginal sample" when 100 ≤ count < 500; "sufficient" when count ≥ 500.
- Flag for ZEUS_BINDING reviewer: MIGRATION_PATH §Phase 2 should be amended in a follow-up doc-only packet to reflect the 500-call PACKET_INDEX threshold.

INCONSISTENCY-3 (MINOR): Probe-count subsumption.
- P1 SCAFFOLD §6.7 mentions a "7-day shadow probe sequence (Days 1–7)" as a deferred P2-packet (i.e. P3) item.
- GOAL §5 asks for "14 distinct probes — extending P1's 7-probe shadow"; reads as additive (7 + new = 14 probes total, OR 7 P1-noted + 14 new = 21 probes).
- Resolution (binding): P3 ships 17 distinct probes (initially framed as 14; 3 added to close remaining UNIVERSAL §12 friction patterns and handle intent=None edge case). P1 §6.7's "7-day shadow probe sequence" was a calendar sequence reference, not 7 distinct probes. P3's 17 probes cover: 6 friction-pattern shadow events (LEXICAL_PROFILE_MISS, UNION_SCOPE_EXPANSION, PHRASING_GAME_TAX, SLICING_PRESSURE, CLOSED_PACKET_STILL_LOAD_BEARING, ADVISORY_OUTPUT_INVISIBILITY — ALL 7 UNIVERSAL §12 patterns, with INTENT_ENUM_TOO_NARROW covered by probe5) + 3 inconsistency-detection probes (intent_enum_unknown, hard_stop divergence, severity divergence) + 2 P2-integration probes (MISSING_COMPANION shadow capture, companion_skip_token-not-counted-as-divergence) + 2 structural probes (profile_match disagreement, kernel_alerts disagreement) + 3 infrastructure probes (log rotation, concurrent-writer correctness, analyzer aggregation correctness with dual-metric P4 gate) + 1 edge-case probe (intent=None defaults to Intent.other). Total 17.

INCONSISTENCY-4 (MINOR): Divergence-log storage path convention.
- GOAL §1 names `state/topology_v_next_divergence.jsonl`.
- Repository convention check (2026-05-15): BOTH conventions coexist in the repo. `state/` has at least 3 JSONL writers: `obs_v2_backfill_log.jsonl`, `obs_v2_dst_fill_log.jsonl`, `obs_v2_meteostat_fill_log.jsonl`. P2 SCAFFOLD §5.1 also uses `state/companion_skip_token_log.jsonl`. `evidence/shadow_router/agreement_2026-05-06.jsonl` is the closest semantic precedent for shadow-comparison JSONLs specifically.
- Resolution (binding): use `evidence/topology_v_next_shadow/divergence_{YYYY-MM-DD}.jsonl`. The shadow-comparison precedent (`evidence/shadow_router/`) governs because it matches use-case (shadow comparison outputs), not because `state/` is reserved for non-JSONL content. One file per calendar day enables natural rotation without a separate rotator. `state/topology_v_next_divergence.jsonl` (GOAL's path) is REJECTED in favor of the use-case-matching `evidence/` precedent.
- P2 carry-forward: P2's `state/companion_skip_token_log.jsonl` path is a valid convention — `state/` is not restricted to DB files. P2.1 SEV-3 carry-forward (§9 below) does NOT recommend moving that path; the conventions coexist without either being wrong. §9.3 focuses solely on the `_atomic_append` concurrency bug in P2, not on path migration.

INCONSISTENCY-5 (MINOR): Status-mapping table absence.
- Current `run_navigation` returns `admission.status ∈ {admitted, advisory_only, blocked, scope_expansion_required, route_contract_conflict, ambiguous}` (grep-verified topology_doctor.py lines 2812–2820).
- v_next AdmissionDecision.severity ∈ {ADMIT, ADVISORY, SOFT_BLOCK, HARD_STOP}.
- Without a status-mapping table, AGREE/DISAGREE classification is undefined and the agreement-rate metric is uncomputable.
- Resolution (binding): §4 below ships the status-mapping table as a first-class deliverable inside `divergence_logger.py` — see §4.4 below.

INCONSISTENCY-6 (MAJOR): P2 SCAFFOLD §0.A premise is stale — `--v-next-shadow` lives in P3, not P1.
- P2 SCAFFOLD (@ commit 48fa92d3fe) §0.A.2 says: "P1's `--v-next-shadow` has reached 95% AGREE. The AGREE rate is defined in MIGRATION_PATH §Phase 2 ... This threshold must be met BEFORE the P2.a window opens..."
- P1 SCAFFOLD (rev 1.2 @ commit 1ebf1a7079) §6.3 EXPLICITLY MOVED `--v-next-shadow` CLI flag to P2 packet (which is P3 by packet-naming — note P1.0's terminology note clarifies "P3 packet" = `topology_v_next_phase2_shadow` = THIS packet).
- Therefore P2 §0.A.2's premise that P1 ships `--v-next-shadow` is FALSE. The shadow mechanism ships in P3 (this packet), not P1.
- Resolution (binding): the actual sequencing is: P1 ships structures (no shadow); **P3 ships shadow + P2's MISSING_COMPANION machinery becomes shadow-observable through P3's flag**; P2 functionally REQUIRES P3 to be at least partially deployed before P2.a can demonstrate the AGREE-rate gate, OR P2.a's AGREE-rate gate must be reframed to use a non-shadow signal (e.g., P2's own per-emission log self-consistency check).
- Flag for P2 reviewer: P2 SCAFFOLD needs an erratum acknowledging that P2.a's 95% AGREE-rate gate is a forward dependency on P3's `--v-next-shadow` flag landing first OR a self-instrumented alternative. Either P2.a runs AFTER P3 deploys (sequencing change), OR P2.a uses a P2-internal "agent added the doc within 24h" follow-through signal as its agreement metric (which doesn't require shadow infrastructure — only the P2 logger).
- P3 IMPACT: §6.1's claim that "P2.a and P3 run sequentially per P2 §0.A.4" is technically true (P2 §0.A.4 enforces sequential vs P1 cutover, not vs P3), BUT the practical sequencing question is unresolved. P3 ships the shadow infrastructure; whether P2.a runs before, during, or after P3 deployment is a packet-ordering decision for the operator, not a P3 design constraint. P3 captures both pre-cutover ADVISORY and post-cutover SOFT_BLOCK P2 behavior regardless of when P2.a runs.

---

## §1. Module Layout

Root: `scripts/topology_v_next/` (extends P1's 10-module layout; P3 adds 3 modules)
Total new module count: 3 (`divergence_logger.py`, `divergence_summary.py`, `cli_integration_shim.py`)
Summed LOC budget cap: ≤ 800 LOC across the 3 new modules. Per-module values are CAPS, not targets.

### 1.1 `scripts/topology_v_next/divergence_logger.py` (~280 LOC cap)

Concurrency-safe append-only JSONL writer for per-call divergence records. Per advisor + critic P2.1 SEV-3 carry-forward, uses `O_APPEND + O_CREAT` flags on a single os-level `write(2)` of a complete single-line record. This is multi-process-safe on POSIX (kernel guarantees atomic appends ≤ `PIPE_BUF` bytes for O_APPEND; records ≥ PIPE_BUF still atomic per-line per `man 2 write` for regular files when single write call).

Public API:
- `class DivergenceRecord` — frozen dataclass with all fields per §4.1 below
- `def log_divergence(record: DivergenceRecord, *, root: Path | str = "evidence/topology_v_next_shadow") -> None` — appends one JSONL line via `os.open(path, O_WRONLY|O_APPEND|O_CREAT)` + single `os.write(fd, line.encode("utf-8"))` + `os.close(fd)`. Path computed as `{root}/divergence_{YYYY-MM-DD}.jsonl` (UTC-day boundary). Never raises on disk-full or permission errors — logs to stderr via `sys.stderr` and continues. The shim must never break admission on logger failure.
- `def compute_event_type(*, old_status: str, new_severity: Severity, companion_skip_used: bool) -> str` — classifier returning one of `divergence_observation | companion_skip_honored | agree` per §4.4 mapping table. Pure function. No I/O.
- `def classify_divergence(record: DivergenceRecord) -> str` — returns one of `AGREE | DISAGREE_PROFILE | DISAGREE_SEVERITY | DISAGREE_COMPANION | DISAGREE_HARD_STOP | DISAGREE_INTENT | SKIP_HONORED` per §4.5 below.
- `def daily_path(*, root: Path | str = "evidence/topology_v_next_shadow", today: date | None = None) -> Path` — pure helper for tests; UTC-today by default.

Internal helpers:
- `_serialize_record(record: DivergenceRecord) -> str` — produces single-line JSON; asserts no embedded `\n` in any field; sorts keys deterministically.
- `_resolve_path(root, today)` — UTC-day filename resolution.

Imports: `os`, `json`, `sys`, `pathlib.Path`, `datetime.date, datetime`, `dataclasses`, `.dataclasses` (P1 types).

### 1.2 `scripts/topology_v_next/divergence_summary.py` (~300 LOC cap)

Analyzer + CLI subcommand that aggregates a date-range slice of divergence JSONLs into `evidence/topology_v_next_shadow/divergence_summary_{YYYY-MM-DD_to_YYYY-MM-DD}.json`. Used by P3's Day-7+ AGREE-rate gate that informs the P4 cutover decision.

Public API:
- `def aggregate(start_date: date, end_date: date, *, root: Path | str = "evidence/topology_v_next_shadow", out_path: Path | str | None = None, skip_honored_filter: bool = True) -> dict[str, Any]` — reads all `divergence_*.jsonl` files in [start_date, end_date], computes aggregates per §6 below, writes JSON summary to `out_path` (or default name in `root`), returns the summary dict. When `skip_honored_filter=True` (default), SKIP_HONORED records are excluded from the agreement-% denominator per §6 below.
- `def cli_main(argv: list[str] | None = None) -> int` — argparse entrypoint for `python -m scripts.topology_v_next.divergence_summary --start-date YYYY-MM-DD --end-date YYYY-MM-DD [--root PATH] [--out PATH] [--include-skip-honored]`. Returns exit code 0 on success, 1 on insufficient sample (<100 calls), 2 on hard error.

Internal helpers:
- `_load_window(start_date, end_date, root) -> Iterator[DivergenceRecord]` — generator over all records in window. Validates per-line JSON; emits per-line warnings to stderr for malformed records but continues.
- `_compute_per_profile_agreement(records) -> dict[profile_id, AgreementStats]` — groups by `profile_resolved` and computes (n_total, n_agree, agreement_pct).
- `_compute_per_friction_pattern(records) -> dict[FrictionPattern, int]` — counts shadow events per friction pattern.
- `_render_summary(stats, *, sample_size_label: str) -> dict[str, Any]` — produces the final JSON shape per §6.

Imports: `json`, `argparse`, `sys`, `pathlib.Path`, `datetime.date`, `collections.Counter`, `dataclasses`, `.divergence_logger` (DivergenceRecord, classify_divergence), `.dataclasses` (Severity, FrictionPattern).

### 1.3 `scripts/topology_v_next/cli_integration_shim.py` (~220 LOC cap)

Wires v_next.admit into `topology_doctor.py:run_navigation()`. Calls old admission first via existing `run_navigation` body (current returns authoritative result); then calls `v_next.admit()` in shadow; classifies divergence; appends one log record; returns the OLD `payload` ENRICHED with a new top-level field `v_next_shadow` containing the normalized envelope per §2 below. Payload's existing fields (`ok`, `task_blockers`, `admission`, etc.) are UNCHANGED — the shim is strictly additive on the response.

Public API:
- `def maybe_shadow_compare(payload: dict[str, Any], *, task: str, files: list[str], intent: str | None, v_next_shadow: bool, friction_state: dict[str, Any] | None = None) -> dict[str, Any]` — when `v_next_shadow=True`, runs v_next.admit (passing `friction_state` for SLICING_PRESSURE detection), classifies divergence, logs to `divergence_logger`, returns `{**payload, "v_next_shadow": envelope_dict}`. When `v_next_shadow=False`, returns payload unchanged (transparent no-op). Never raises on v_next failure — exceptions caught and logged to stderr, payload returned with `v_next_shadow={"error": str, "ok": None}`.
- `def format_output(decision: AdmissionDecision) -> dict[str, Any]` — derives the §2 envelope `{ok, decision, advisory, blockers, profile_matched, intent_class, missing_phrases, closest_rejected_profile, friction_budget_used, companion_files, diagnosis, kernel_alerts}` from AdmissionDecision. Pure function. No I/O.
- `def map_old_status_to_severity(old_status: str) -> Severity` — implements the §4.4 status-mapping table.

Internal helpers:
- `_extract_binding(payload) -> BindingLayer | None` — extracts the current BindingLayer from payload's `route_card` dict if present; used by `maybe_shadow_compare` (§2.4 line 193) to pass the resolved binding to v_next.admit.
- `_extract_old_admission(payload) -> tuple[str, str | None]` — returns `(old_status, old_profile_resolved)` from payload's `admission` and `route_card` dicts.
- `_build_divergence_record(*, payload, decision, task, task_hash: str, files, intent) -> DivergenceRecord` — assembles the §4.1 record. The `task_hash` parameter (sha256[:16] of raw task string) is required; passing the raw task string here would risk logging it in the JSONL record — the hash prevents that.

**Anti-PHRASING_GAME_TAX guard**: shim does NOT accept any `phrase`, `task_phrase`, or `wording` parameter. `task` is hashed via `sha256(task.encode())[:16].hex()` and the hash is passed to `v_next.admit(intent, files, hint=task_hash)` as the diagnostic `hint` parameter. Passing the raw task string would make `closest_rejected_profile` phrase-sensitive (soft PHRASING_GAME_TAX via the hint output field varying with wording). Passing the hash instead ensures `closest_rejected_profile` is IDENTICAL across 3 phrase-varying calls with the same intent+files. Per P1 SCAFFOLD §5.3 invariant, `hint` is OUTPUT-ONLY — it feeds only the `closest_rejected_profile` diagnostic field and is NEVER consumed by `coverage_map.resolve_candidates()` or `composition_rules.apply_composition()` (which do not accept a phrase parameter at all). The shim adds no new routing input; it relies on P1's structural hint-never-routes guarantee. The shim public API accepts no parameter that could become a phrase-routing reintroduction; `task` flows through to `task_hash` (sha256[:16] for grouping in the divergence log) only.

Imports (module top, not deferred): `os`, `sys`, `json`, `dataclasses`, `pathlib.Path`, `datetime`, `.admission_engine` (admit), `.dataclasses` (AdmissionDecision, Severity, IssueRecord), `.divergence_logger` (DivergenceRecord, log_divergence, classify_divergence, map_old_status_to_severity if extracted there). All imports are unconditional at module top so import-time errors surface immediately.

---

## §2. Tool Result Shape Normalization (UNIVERSAL §11)

Per §0 INCONSISTENCY-1 resolution, the envelope is a SHIM-OUTPUT WRAPPER over AdmissionDecision. AdmissionDecision (P1) is unchanged.

### 2.1 Envelope shape

```python
# Conceptual — implemented in cli_integration_shim.format_output()
envelope = {
    "ok": bool,                              # = decision.ok
    "decision": str,                         # = decision.severity.value (ADMIT|ADVISORY|SOFT_BLOCK|HARD_STOP)
    "advisory": list[dict],                  # = [issue.to_dict() for issue in decision.issues if issue.severity == ADVISORY]
    "blockers": list[dict],                  # = [issue.to_dict() for issue in decision.issues if issue.severity in {SOFT_BLOCK, HARD_STOP}]
    "profile_matched": str | None,           # = decision.profile_matched (pass-through)
    "intent_class": str,                     # = decision.intent_class.value (pass-through)
    "missing_phrases": list[str],            # = list(decision.missing_phrases) (pass-through)
    "closest_rejected_profile": str | None,  # = decision.closest_rejected_profile (pass-through)
    "friction_budget_used": int,             # = decision.friction_budget_used (pass-through)
    "companion_files": list[str],            # = list(decision.companion_files) (pass-through)
    "diagnosis": dict | None,                # = decision.diagnosis.to_dict() if not None
    "kernel_alerts": list[dict],             # = [a.to_dict() for a in decision.kernel_alerts]
}
```

### 2.2 Mandatory-field invariant

The four GOAL-named fields (`ok`, `decision`, `advisory`, `blockers`) are ALWAYS PRESENT in the envelope. `advisory` and `blockers` are always lists (possibly empty), never None. `ok` is always bool. `decision` is always one of the four severity strings. This satisfies UNIVERSAL §11's "MANDATORY fields" intent under the shim-envelope interpretation.

### 2.3 Backward-compatibility transformation

The new envelope sits **inside** the current `run_navigation` payload under a new top-level key `v_next_shadow`. Existing callers that read `payload["ok"]`, `payload["admission"]`, `payload["task_blockers"]`, `payload["route_card"]`, etc. see ZERO change. The envelope is purely additive at the response surface.

```python
# Pseudocode — what run_navigation returns when v_next_shadow=True
payload = { ... current run_navigation response, unchanged ... }
payload["v_next_shadow"] = format_output(v_next_decision)   # NEW key only
return payload

# When v_next_shadow=False, payload is returned WITHOUT the v_next_shadow key (transparent no-op)
```

### 2.4 Pseudocode for the shim transformation

```python
# scripts/topology_v_next/cli_integration_shim.py
import hashlib

def maybe_shadow_compare(
    payload: dict[str, Any],
    *,
    task: str,
    files: list[str],
    intent: str | None,
    v_next_shadow: bool,
    friction_state: dict[str, Any] | None = None,  # per-session in-memory dict for SLICING_PRESSURE
) -> dict[str, Any]:
    if not v_next_shadow:
        return payload  # transparent no-op

    try:
        task_hash = hashlib.sha256(task.encode()).hexdigest()[:16]  # anti-PHRASING_GAME_TAX
        decision = admit(
            intent=intent,
            files=files,
            hint=task_hash,          # hash, not raw task — closes soft PHRASING_GAME_TAX via hint
            binding=_extract_binding(payload),
            friction_state=friction_state or {},
        )
        envelope = format_output(decision)
        record = _build_divergence_record(
            payload=payload, decision=decision, task=task, task_hash=task_hash,
            files=files, intent=intent,
        )
        log_divergence(record)
    except Exception as exc:  # never break admission on shadow failure
        sys.stderr.write(f"[v_next_shadow] failed: {type(exc).__name__}: {exc}\n")
        envelope = {"error": f"{type(exc).__name__}: {exc}", "ok": None, "decision": None,
                    "advisory": [], "blockers": []}
    return {**payload, "v_next_shadow": envelope}
```

`friction_state` is an in-memory dict maintained by the caller (or defaulted to `{}` per call). In the `run_navigation` context, the caller (topology_doctor.py) may pass a session-scoped dict if it tracks per-session call history for SLICING_PRESSURE detection; otherwise empty dict causes v_next to start fresh per call. The shim does NOT own the friction_state lifecycle — it accepts it from above and passes it through. Single-process callers can maintain a module-level or instance-level dict; multi-process callers (concurrent topology_doctor invocations) each start with `{}` (no cross-process friction_state sharing — acceptable because SLICING_PRESSURE detection in probe4 is tested in a single-session scenario).

---

## §3. Wire-Up Diff (grep-verified 2026-05-15)

All line numbers and context grep-verified against the canonical files in the current worktree on 2026-05-15. Each hunk includes ≥3 lines of surrounding context to make the diff applicable without ambiguity. Total diff: 22 LOC (within ≤30 LOC budget).

### 3.1 `scripts/topology_doctor_cli.py` — argparse flag declaration

Grep-verified context: line 94 is the `--intent` argparse declaration; surrounding lines 92–95 unchanged in worktree. Diff INSERTS one line after line 94, ZERO modifications to existing lines.

```diff
--- a/scripts/topology_doctor_cli.py
+++ b/scripts/topology_doctor_cli.py
@@ -91,5 +91,6 @@ def _build_parser(api: Any) -> argparse.ArgumentParser:
     )
     parser.add_argument("--task", default="", help="Task string for --navigation")
     parser.add_argument("--files", nargs="*", default=[], help="Files for --navigation")
     parser.add_argument("--intent", default=None, help="Typed digest profile id; overrides free-text profile scoring but not admission")
+    parser.add_argument("--v-next-shadow", action="store_true", default=False, help="P3 shadow mode: run v_next.admit in parallel with current admission, log divergence to evidence/topology_v_next_shadow/; current admission remains authoritative")
     parser.add_argument("--task-class", default=None, help="Typed semantic boot task class")
```

### 3.2 `scripts/topology_doctor_cli.py` — dispatch kwarg

Grep-verified context: line 388 is `_batch_cap = getattr(args, "companion_loop_batch_cap", None)`; surrounding lines 388–392 unchanged in worktree. Diff INSERTS three lines after line 390 (after the existing `companion_loop_batch_cap` block), ZERO modifications to existing lines.

```diff
--- a/scripts/topology_doctor_cli.py
+++ b/scripts/topology_doctor_cli.py
@@ -388,6 +388,9 @@ def _run(api: Any, args: argparse.Namespace) -> int:
         _batch_cap = getattr(args, "companion_loop_batch_cap", None)
         if _batch_cap is not None:
             navigation_kwargs["companion_loop_batch_cap"] = _batch_cap
+        _v_next_shadow = getattr(args, "v_next_shadow", False)
+        if _v_next_shadow:
+            navigation_kwargs["v_next_shadow"] = True
         if args.issue_schema_version != "1":
             navigation_kwargs["issue_schema_version"] = args.issue_schema_version
         navigation_files = list(args.files or [])
```

### 3.3 `scripts/topology_doctor.py` — run_navigation signature

Grep-verified context: line 2636 is `def run_navigation(`; signature spans lines 2636–2652 with `companion_loop_batch_cap: int | None = None,` as the last keyword arg at line 2651. Diff INSERTS one line after line 2651, ZERO modifications to existing lines.

```diff
--- a/scripts/topology_doctor.py
+++ b/scripts/topology_doctor.py
@@ -2649,5 +2649,6 @@ def run_navigation(
     artifact_target: str | None = None,
     merge_state: str | None = None,
     companion_loop_batch_cap: int | None = None,
+    v_next_shadow: bool = False,
 ) -> dict[str, Any]:
     checks = {
```

### 3.0 `scripts/topology_doctor.py` — module-level import (added at top of file with other imports)

The shim import is placed at module top alongside existing imports, not deferred. This ensures import-time errors (missing module, syntax error) surface immediately when topology_doctor.py is loaded, not lazily on the first shadow call.

```diff
--- a/scripts/topology_doctor.py
+++ b/scripts/topology_doctor.py
@@ -19,1 +19,2 @@
 from typing import Any
+from scripts.topology_v_next.cli_integration_shim import maybe_shadow_compare
```

The import is placed after line 19 (`from typing import Any`), the last stdlib import in the block. No `scripts.topology_v_next` imports exist at module top in the current file — this is the first one. The import is unconditional; `maybe_shadow_compare` returns payload unchanged when `v_next_shadow=False` (transparent no-op), so the import cost is always paid but the shadow path has zero execution cost when disabled.

### 3.4 `scripts/topology_doctor.py` — shim call site

Grep-verified context: `return {` is at line 2753; the closing `}` of the dict literal is at line 2794 (grep-verified 2026-05-15). The full dict literal spans lines 2753–2794 = 42 source lines. The diff covers the ENTIRE dict literal in a single hunk to avoid the split-hunk defect class (P1.0 fabricated-diff anti-pattern). Net change: rename `return {` to `payload = {`; add `if v_next_shadow:` block after closing `}`; add `return payload`.

```diff
--- a/scripts/topology_doctor.py
+++ b/scripts/topology_doctor.py
@@ -2753,42 +2753,48 @@
-    return {
+    payload = {
         "ok": nav_ok,
         "command_ok": True,
         "ok_semantics": "command_success_only_not_write_authorization",
         "task": task,
         "digest": digest,
         "admission": admission,
         "route_card": digest.get("route_card", {}),
         "claim_evaluation": claim_evaluation,
         "claims_evaluated": claim_evaluation["evaluated"],
         "claims_blocked": claim_evaluation["blocked"],
         "claims_advisory": claim_evaluation["advisory"],
         "semantic_bootstrap": semantic_bootstrap,
         "context_assumption": digest.get("context_assumption", {}),
         "checks": {
             lane: {
                 "ok": result.ok,
                 "issue_count": len(result.issues),
                 "blocking_count": len([issue for issue in result.issues if issue.severity == "error"]),
                 "warning_count": len([issue for issue in result.issues if issue.severity == "warning"]),
             }
             for lane, result in checks.items()
         },
         "issues": issues,
         "issues_contract": "legacy_aggregate_not_task_blockers",
         "task_blockers": task_blockers,
         "admission_blockers": admission_blockers,
         "profile_selection_warnings": [
             issue for issue in task_blockers if issue.get("lane") == "navigation"
         ],
         "global_health_warnings": [] if strict_health else repo_health_warnings,
         "legacy_issues": issues,
         "direct_blockers": legacy_blocking if strict_health else direct_blockers,
         "route_context": route_context,
         "repo_health_warnings": [] if strict_health else repo_health_warnings,
         "global_health_counts": _global_health_counts(checks),
         "excluded_lanes": {
             "strict": "strict includes transient root/state artifact classification; run explicitly when workspace is quiescent",
             "scripts": "script manifest can be blocked by active package scripts; run explicitly for script work",
             "planning_lock": "requires caller-supplied --changed-files and optional --plan-evidence",
         },
     }
+    if v_next_shadow:
+        payload = maybe_shadow_compare(
+            payload, task=task, files=requested_paths,
+            intent=intent, v_next_shadow=v_next_shadow,
+        )
+    return payload
```

This is a single unified hunk covering all 42 source lines of the dict literal. The split into two hunks from the prior draft was the P1.0 fabricated-diff defect class — the second hunk's range `@@ -2791,3 @@` ended before the closing `}` at line 2794, making it unapplyable. This single-hunk form is unambiguous.

### 3.5 Diff total — LOC budget verification

| Hunk | LOC added | LOC modified |
|------|-----------|--------------|
| 3.0 module-top import | 1 | 0 |
| 3.1 argparse declaration | 1 | 0 |
| 3.2 dispatch kwarg | 3 | 0 |
| 3.3 signature | 1 | 0 |
| 3.4 shim call site (single hunk: payload= rename + shim if-block + return) | 6 added | 1 (return→payload= prefix) |
| **Total** | **12 added + 1 modified = 13 LOC** | within ≤30 budget |

The diff is small enough that a P1.0-style fabricated-diff defect is structurally unlikely — every hunk has been grep-verified against current canonical file contents (topology_doctor.py:2636, 2651, 2753; topology_doctor_cli.py:94, 388 — all confirmed on 2026-05-15).

---

## §4. Divergence Log Schema

### 4.1 DivergenceRecord — per-call schema

```python
@dataclass(frozen=True)
class DivergenceRecord:
    ts: str                                  # ISO-8601 UTC e.g. "2026-05-15T13:42:01.234Z"
    schema_version: str                      # "1" — bump on breaking schema change
    event_type: str                          # "divergence_observation" | "companion_skip_honored" | "agree"
    profile_resolved_old: str | None         # current admission's resolved profile_id; None if old returned None
    profile_resolved_new: str | None         # v_next.admit's profile_matched; None if v_next returned None
    intent_typed: str                        # the validated Intent enum value (post P1 intent_resolver)
    intent_supplied: str | None              # the raw caller-supplied intent string (None if not supplied)
    files: list[str]                         # verbatim copy of files argument
    old_admit_status: str                    # one of {admitted, advisory_only, blocked, scope_expansion_required, route_contract_conflict, ambiguous}
    new_admit_severity: str                  # one of {ADMIT, ADVISORY, SOFT_BLOCK, HARD_STOP}
    new_admit_ok: bool                       # AdmissionDecision.ok
    agreement_class: str                     # one of §4.5 below (AGREE | DISAGREE_* | SKIP_HONORED)
    friction_pattern_hit: str | None         # FrictionPattern enum value if shadow detected one; None otherwise
    missing_companion: list[str]             # P2 carry-forward: list of MISSING_COMPANION issue paths (empty if none)
    companion_skip_used: bool                # P2 carry-forward: True iff v_next emitted companion_skip_token_used issue
    closest_rejected_profile: str | None     # carried from AdmissionDecision (diagnostic)
    kernel_alert_count: int                  # len(AdmissionDecision.kernel_alerts)
    friction_budget_used: int                # AdmissionDecision.friction_budget_used
    task_hash: str                           # sha256(task)[:16] — for de-dup/grouping; NEVER for routing
    error: str | None                        # None on success; string on v_next exception (with type+msg)
```

Field rules:
- `ts` — ISO-8601 with millisecond precision, always Z-suffix UTC.
- `schema_version` — string literal "1" in P3; bumped only on breaking schema change.
- `task_hash` — first 16 hex chars of sha256 of the task string. Used to GROUP repeated attempts (e.g. for SLICING_PRESSURE detection by the analyzer); NEVER fed back into routing. Privacy: the raw task string is NOT logged because it may contain agent context the operator does not want persisted; the hash is sufficient for "did we see this attempt before" grouping.
- `files` — full verbatim list. Required for replay/audit. If your concern is path-length blowup, the JSONL line cap is the limit (no per-field truncation — truncating would defeat audit).
- `error` — populated when v_next.admit raised; in that case `profile_resolved_new`, `new_admit_severity`, `new_admit_ok`, `agreement_class` are best-effort or null and the record is still written so debug visibility is preserved.

### 4.2 Storage format

- One JSONL line per record. Single `os.write(fd, line.encode("utf-8"))` after `os.open(path, O_WRONLY|O_APPEND|O_CREAT, 0o644)` and before `os.close(fd)`.
- Path: `evidence/topology_v_next_shadow/divergence_{YYYY-MM-DD}.jsonl` (UTC day boundary). Per-day file enables rotation-by-existence.
- Each record's serialized JSON MUST be a single line — no embedded `\n`. `_serialize_record` asserts this.
- Per-record max size: 8 KiB. Records exceeding this cap are truncated at the `files` list (rest replaced with `["__TRUNCATED__"]`) and `error` field is populated with `record_size_exceeded`.

### 4.3 Concurrency contract

- POSIX kernel guarantees `O_APPEND` writes are atomic per single `write(2)` syscall for regular files when the write fits within the filesystem's atomic-write block size (typically ≥ PIPE_BUF = 4 KiB on Linux/macOS). Per-record cap of 8 KiB exceeds PIPE_BUF; HOWEVER, for **regular files** (not pipes), `man 2 write` specifies that `O_APPEND` ensures the file offset is atomically advanced to end-of-file before each write, and the write itself is atomic with respect to other concurrent writers on the same file when the writer uses a single `write()` syscall. This guarantee holds for our use case: single-process single-syscall writes of complete single-line JSONL records.
- Multi-process safety: divergence_logger is safe to call from multiple processes simultaneously (e.g., two concurrent topology_doctor.py --navigation invocations). Each opens its own fd; each writes one complete line; no interleaving.
- Per-record max size of 8 KiB is well above typical record size (~600–900 bytes); the cap exists to enforce single-syscall write semantics. Records exceeding 8 KiB after serialization trigger truncation per §4.2.

### 4.4 Old-status → new-severity mapping table

```python
OLD_STATUS_TO_NEW_SEVERITY = {
    "admitted":                  Severity.ADMIT,       # green
    "advisory_only":             Severity.ADVISORY,    # green-with-conditions; UNIVERSAL §11
    "blocked":                   Severity.SOFT_BLOCK,  # current returns blocked → v_next SOFT_BLOCK
    "scope_expansion_required":  Severity.SOFT_BLOCK,  # composition conflict equivalent
    "route_contract_conflict":   Severity.SOFT_BLOCK,  # contract violation = soft block
    "ambiguous":                 Severity.SOFT_BLOCK,  # caller-supplied disambiguation needed
    # HARD_STOP has no current equivalent — current admission has no kernel concept;
    # any v_next HARD_STOP is automatic DISAGREE_HARD_STOP (escalated severity)
}
```

Rationale: current admission has only soft outcomes. `advisory_only` is the closest to v_next ADVISORY. There is intentionally no `HARD_STOP` mapping from the old side — every old emission is at most SOFT_BLOCK equivalent. This means every v_next-side HARD_STOP is automatically classified `DISAGREE_HARD_STOP` and forced into the per-day summary's escalation count — this is the desired behavior because v_next HARD_STOP is a NEW SAFETY signal that current admission does not have.

### 4.5 AGREE/DISAGREE classifier

```python
def classify_divergence(record: DivergenceRecord) -> str:
    # Defensive guard: error envelope may have None severity (v_next raised before severity was set)
    if record.new_admit_severity is None or record.error is not None:
        return "ERROR"  # excluded from agreement-% denominator by the analyzer

    old_severity_equiv = OLD_STATUS_TO_NEW_SEVERITY[record.old_admit_status]
    new_severity = Severity(record.new_admit_severity)

    # SKIP_HONORED is a P2-integration case: v_next emitted companion_skip_token_used.
    # Per §6 below, these are excluded from agreement-% denominator.
    if record.companion_skip_used:
        return "SKIP_HONORED"

    # Hard escalation — v_next added a HARD_STOP the old side cannot express
    if new_severity == Severity.HARD_STOP:
        return "DISAGREE_HARD_STOP"

    # MISSING_COMPANION (P2.a) — old side has no companion check
    if record.missing_companion and old_severity_equiv == Severity.ADMIT:
        return "DISAGREE_COMPANION"  # v_next caught a P2 drift the old side missed

    # Severity mismatch
    if old_severity_equiv != new_severity:
        return "DISAGREE_SEVERITY"

    # Profile mismatch (severities agree)
    if record.profile_resolved_old != record.profile_resolved_new:
        return "DISAGREE_PROFILE"

    # Intent mismatch (rare; only when intent_supplied != intent_typed and that flips routing)
    if record.intent_supplied and record.intent_supplied != record.intent_typed:
        # intent was normalized; this is informational only — counts as AGREE if severity+profile match
        pass

    return "AGREE"
```

### 4.6 Retention policy

- 90 days rolling window. After day 90, daily files are auto-rotated to a compressed archive `evidence/topology_v_next_shadow/archive/divergence_{YYYY-MM-DD}.jsonl.gz` by the existing weekly maintenance task (P5/P6 binding). Compressed files retained ≥ 180 days for audit.
- `.gitignore` MUST add `evidence/topology_v_next_shadow/*.jsonl*` (current + archived) so raw divergence records are NEVER committed. The summary JSON (`divergence_summary_*.json`) IS committed because it is human-readable, audit-relevant, and pre-aggregated.
- Single-file size cap: 50 MiB per daily file. If exceeded, log rotation falls back to `divergence_{YYYY-MM-DD}_part{N}.jsonl` automatically (handled in `divergence_logger._resolve_path` by checking file size before write).

---

## §5. Shadow-Window Acceptance Probes (17 distinct probes)

Located at `tests/topology_v_next/regression/shadow/` — each probe is an independent pytest test file. Each probe explicitly states its kill criterion as a concrete numeric/string assertion so unfalsifiable success is structurally precluded.

### probe1 — `test_shadow_lexical_profile_miss_resolved.py`
Trigger: invoke `run_navigation(task="add a thing", files=["scripts/new_helper.py"], v_next_shadow=True)` and again with `task="update helper logic"` — same files, different phrase.
Expected divergence pattern: both calls produce SAME `profile_resolved_new` (v_next intent-routed) but POTENTIALLY DIFFERENT `profile_resolved_old` (current is phrase-sensitive). At least one call should classify as `DISAGREE_PROFILE`.
Kill criterion if fails: if BOTH calls return `agreement_class == "AGREE"` AND `profile_resolved_old` is identical across both, the LEXICAL_PROFILE_MISS structural fix is unmeasurable in shadow → fail with `assert at_least_one_classification == "DISAGREE_PROFILE"`.

### probe2 — `test_shadow_union_scope_expansion_resolved.py`
Trigger: invoke with `files=["src/calibration/platt.py", "tests/test_calibration_platt.py"]` and intent="modify_existing" (a Universal §8 cohort; `src/calibration/weighting.py` does not exist — substituted `platt.py` which is verified present).
Expected: `v_next` admits via cohort (`new_admit_severity == ADMIT`); old returns `advisory_only` or `scope_expansion_required`. `agreement_class == "DISAGREE_SEVERITY"` (v_next more permissive on legitimate cohort).
Kill criterion: if `new_admit_severity != ADMIT` and `friction_pattern_hit != UNION_SCOPE_EXPANSION`, the cohort admission is broken → fail with `assert new_admit_severity == "ADMIT" or friction_pattern_hit == "UNION_SCOPE_EXPANSION"`.

### probe3 — `test_shadow_phrasing_game_tax_resolved.py`
Trigger: 3 calls with same `files` and same `intent`, varying `task` phrase ("add a module", "add module logic", "create the module").
Expected: all 3 records show same `profile_resolved_new` AND same `new_admit_severity` AND identical `agreement_class` AND identical `closest_rejected_profile`. The shim passes `hint=sha256(task)[:16]` (hash), so the diagnostic field is phrase-independent. The friction_budget_used in record is independent of phrase variation.
Kill criterion (two assertions, both must pass):
1. `assert len({(r.profile_resolved_new, r.new_admit_severity) for r in records}) == 1` — routing must be phrase-independent.
2. `assert len({envelope["closest_rejected_profile"] for envelope in envelopes}) == 1` — diagnostic output must also be phrase-independent (hash hint prevents soft PHRASING_GAME_TAX via hint field). If this assertion fails, the raw task string is leaking through hint instead of the hash.

### probe4 — `test_shadow_slicing_pressure_detected.py`
Trigger: 3 calls within 30 min sharing overlapping file sets, each a strict subset of the previous.
Expected: third call has `friction_pattern_hit == "SLICING_PRESSURE"` (now testable because shim supplies `friction_state` — the P1 SCAFFOLD §5.2 deferred case is closed in P3). `new_admit_severity == "SOFT_BLOCK"`.
Kill criterion: `assert record3.friction_pattern_hit == "SLICING_PRESSURE"` AND `record3.new_admit_severity == "SOFT_BLOCK"` — failure means SLICING_PRESSURE detection still doesn't fire even with shim plumbing.

### probe5 — `test_shadow_intent_enum_unknown_advisory.py`
Trigger: invoke with `intent="frobnicate_thing"` (not in canonical enum, not in zeus.* namespace).
Expected: v_next emits ADVISORY with `intent_enum_unknown` code; old side ignores unknown intent string. `agreement_class == "DISAGREE_SEVERITY"`.
Kill criterion: `assert any(blocker["code"] == "intent_enum_unknown" for blocker in envelope["advisory"])` — missing advisory means INTENT_ENUM_TOO_NARROW structural fix is broken.

### probe6 — `test_shadow_hard_stop_divergence.py`
Trigger: invoke with `files=["src/execution/executor.py"]` and intent="modify_existing" (LIVE_SIDE_EFFECT_PATH per ZEUS_BINDING; `src/execution/executor.py` verified present; `src/execution/order_router.py` does not exist).
Expected: v_next emits HARD_STOP via kernel; old side returns its normal admission. `agreement_class == "DISAGREE_HARD_STOP"`.
Kill criterion: `assert record.agreement_class == "DISAGREE_HARD_STOP"` AND `record.kernel_alert_count >= 1` — missing kernel detection means the Hard Safety Kernel didn't wire up.

### probe7 — `test_shadow_severity_disagreement_logged.py`
Trigger: a known case where old returns `blocked` and v_next admits cleanly (e.g., a packet whose cohort declaration is fresh and old admission hasn't been migrated to cohort awareness).
Expected: `record.agreement_class == "DISAGREE_SEVERITY"`; record persists to JSONL; analyzer counts it under per-profile agreement.
Kill criterion: read back the JSONL; `assert record.agreement_class == "DISAGREE_SEVERITY"`.

### probe8 — `test_shadow_missing_companion_capture.py` (P2 integration)
Trigger: invoke with `files=["src/calibration/platt.py"]` (missing companion `docs/reference/zeus_calibration_platt_authority.md`; `src/calibration/platt.py` verified present; `src/calibration/weighting.py` does not exist), intent="modify_existing".
Expected: v_next emits MISSING_COMPANION ADVISORY (per P2 §3.2); record's `missing_companion` field is non-empty; `agreement_class == "DISAGREE_COMPANION"`.
Kill criterion: `assert "docs/reference/zeus_calibration_platt_authority.md" in record.missing_companion` AND `record.agreement_class == "DISAGREE_COMPANION"`.

### probe9 — `test_shadow_companion_skip_not_counted_as_divergence.py` (P2 integration)
Trigger: construct a fixture binding YAML with `companion_skip_tokens: {<profile_id>: "COMPANION_SKIP_NEEDS_HUMAN_REVIEW=1"}` for the relevant profile; set env var `COMPANION_SKIP_NEEDS_HUMAN_REVIEW=1` in test setup; invoke with `files=["src/data/vendor_response_x.py"]` using that fixture binding. (Note: the actual mechanism reads `binding.companion_skip_tokens[profile_id]` per-profile — there is no global env var `COMPANION_SKIP_NEEDS_HUMAN_REVIEW`; the per-profile token string IS the env var name that must be set.)
Expected: v_next emits `companion_skip_token_used` ADVISORY (skip token present AND env var set); record's `companion_skip_used == True`; `agreement_class == "SKIP_HONORED"` (per §4.5); analyzer aggregator EXCLUDES this record from agreement-% denominator (per §6 below).
Kill criterion: `assert record.agreement_class == "SKIP_HONORED"` AND analyzer-computed agreement-pct EXCLUDES this row from denominator (verify via mock 100-record fixture where 50 are SKIP_HONORED — agreement-pct should be 50/50 = 100%, not 50/100 = 50%).

### probe10 — `test_shadow_profile_match_disagreement.py`
Trigger: invoke with `files=["src/data/replay_x.py"]` (Cohort 4 profile per P2 §6) and intent="modify_existing".
Expected: old side resolves to a generic data profile; v_next resolves to `modify_data_replay_surface`. `record.agreement_class == "DISAGREE_PROFILE"`.
Kill criterion: `assert record.profile_resolved_old != record.profile_resolved_new` AND `record.agreement_class == "DISAGREE_PROFILE"`.

### probe11 — `test_shadow_kernel_alerts_disagreement.py`
Trigger: invoke with `files=[".env", "src/some_runtime.py"]` (CREDENTIAL_OR_AUTH_SURFACE per ZEUS_BINDING §3 / Universal §5).
Expected: v_next emits kernel alert for `.env`; old side returns its normal admission. `record.kernel_alert_count >= 1`.
Kill criterion: `assert record.kernel_alert_count >= 1` — failure means kernel wiring is broken for credential paths.

### probe12 — `test_shadow_log_rotation_on_day_boundary.py` (infrastructure)
Trigger: monkeypatch `divergence_logger._resolve_path` to use a `today` parameter; invoke logger twice across simulated day boundary (UTC).
Expected: two records written to two different files (`divergence_2026-05-15.jsonl` and `divergence_2026-05-16.jsonl`).
Kill criterion: `assert path1.exists() and path2.exists() and path1 != path2` — rotation by UTC day-boundary failure.

### probe13 — `test_shadow_concurrent_writer_safety.py` (infrastructure)
Trigger: spawn 4 subprocesses each writing 100 records to the same daily file via `divergence_logger.log_divergence`.
Expected: file contains exactly 400 lines; every line is valid JSON; no line is corrupted by interleaving.
Kill criterion: `assert line_count == 400 and all(json.loads(line) for line in lines)` — interleaving means O_APPEND atomicity is broken.

### probe14 — `test_shadow_analyzer_aggregation_correctness.py` (infrastructure)
Trigger: synthesize a 1000-record fixture covering all 7 friction patterns, AGREE/DISAGREE mix, SKIP_HONORED rows (exactly 150 SKIP_HONORED = 15% skip rate); run `divergence_summary.aggregate(start_date, end_date, root=tmp_path)`.
Expected: summary's per-profile agreement-pct excludes SKIP_HONORED rows from denominator; per-friction-pattern counts match the fixture's distribution exactly; skip_honored_rate = 0.15; p4_gate_ok reflects both metrics.
Kill criteria (three assertions, all must pass):
1. `assert summary["agreement_pct_excluding_skips"]["modify_calibration_platt"] == expected_pct_excluding_skip` — denominator-inclusion bug.
2. `assert abs(summary["skip_honored_rate"] - 0.15) < 0.001` — skip_honored_rate correctly computed over ALL records.
3. `assert summary["p4_gate_ok"] == (all_profiles_above_95 and 0.15 < 0.20)` — p4_gate_ok derivation correctness. Run a second fixture with skip_honored_rate=0.25 and assert `p4_gate_ok == False` even when per-profile agreement-pct is 1.0.

### probe15 — `test_shadow_closed_packet_authority_visible.py`
Trigger: invoke with `files=["docs/authority/some_frozen_authority_doc.md"]` tagged with `artifact_authority_status: CURRENT_HISTORICAL` (a closed-packet authority surface per ZEUS_BINDING §6). Current admission has no equivalent check; v_next emits ADVISORY `closed_packet_authority_touched`.
Expected: envelope's `advisory` list contains an entry with `code == "closed_packet_authority_touched"`; old side returns no equivalent advisory. `record.agreement_class == "DISAGREE_SEVERITY"`.
Kill criterion: `assert any(a["code"] == "closed_packet_authority_touched" for a in envelope["advisory"])` AND `record.agreement_class == "DISAGREE_SEVERITY"` — if this fails, CLOSED_PACKET_STILL_LOAD_BEARING friction pattern is not observable in shadow.

### probe16 — `test_shadow_advisory_output_invisibility_envelope.py`
Trigger: invoke a case that produces `ok=True` with one or more ADVISORY-tier issues in v_next (e.g., probe5's `intent_enum_unknown` at advisory severity). Confirm the shim envelope's `advisory` list is non-empty and surfaces through the `v_next_shadow` key.
Expected: `envelope["ok"] == True`, `len(envelope["advisory"]) >= 1`, `envelope["blockers"] == []`. The `advisory` field is ALWAYS a list (never None); when ok=True with advisories this is the expected shape.
Kill criterion: `assert envelope["ok"] == True and len(envelope["advisory"]) >= 1 and isinstance(envelope["advisory"], list)` — failure means ADVISORY issues are being silently dropped in the envelope derivation (ADVISORY_OUTPUT_INVISIBILITY structural issue).

### probe17 — `test_shadow_intent_none_defaults_to_other.py`
Trigger: invoke `maybe_shadow_compare` with `intent=None` (caller did not supply a typed intent string).
Expected: v_next resolves `intent=None` to `Intent.other` via `intent_resolver`; emits ADVISORY `intent_unspecified` (informational — caller should supply explicit intent for best admission quality). `record.intent_supplied == None`, `record.intent_typed == "other"`. `agreement_class` computed correctly against old admission result.
Kill criterion: `assert record.intent_supplied is None` AND `assert record.intent_typed == "other"` AND `assert any(a["code"] == "intent_unspecified" for a in envelope["advisory"])` — failure means `intent=None` is not handled gracefully (possible exception or silent misclassification).

### Probe summary

| Probe | Category | Closes friction pattern / verifies |
|-------|----------|-------------------------------------|
| 1 | Friction-pattern shadow | LEXICAL_PROFILE_MISS measurable in shadow |
| 2 | Friction-pattern shadow | UNION_SCOPE_EXPANSION resolved by v_next cohort |
| 3 | Friction-pattern shadow | PHRASING_GAME_TAX deterministic in shadow (hash-hint assertion) |
| 4 | Friction-pattern shadow | SLICING_PRESSURE detection fires (P1-deferred case closed) |
| 5 | Inconsistency-detection | INTENT_ENUM_TOO_NARROW: intent_enum_unknown advisory |
| 6 | Inconsistency-detection | HARD_STOP divergence captured |
| 7 | Inconsistency-detection | severity disagreement logged and read-back |
| 8 | P2 integration | MISSING_COMPANION shadow event captured |
| 9 | P2 integration | companion_skip_token NOT counted as divergence |
| 10 | Structural | profile_match disagreement logged |
| 11 | Structural | kernel_alerts disagreement logged |
| 12 | Infrastructure | log rotation on UTC day boundary |
| 13 | Infrastructure | concurrent-writer atomicity |
| 14 | Infrastructure | analyzer aggregation correctness (dual metric: agreement_pct + skip_honored_rate) |
| 15 | Friction-pattern shadow | CLOSED_PACKET_STILL_LOAD_BEARING observable in shadow |
| 16 | Friction-pattern shadow | ADVISORY_OUTPUT_INVISIBILITY — advisory list non-empty and visible through envelope |
| 17 | Edge case | intent=None defaults to Intent.other + emits intent_unspecified ADVISORY |

Total 17 probes, ~800 LOC of test code, each independently runnable. All 7 UNIVERSAL §12 friction patterns are now covered: probes 1+3 (LEXICAL_PROFILE_MISS + PHRASING_GAME_TAX), probe 2 (UNION_SCOPE_EXPANSION), probe 4 (SLICING_PRESSURE), probe 5 (INTENT_ENUM_TOO_NARROW), probe 15 (CLOSED_PACKET_STILL_LOAD_BEARING), probe 16 (ADVISORY_OUTPUT_INVISIBILITY).

---

## §6. P2 SCAFFOLD Compatibility

P2 introduced `_check_companion_required()` and the `companion_skip_token_used` ADVISORY. P3's shadow must integrate both without conflating them with general divergence signal.

### 6.1 MISSING_COMPANION flows through divergence_logger

Per probe8 above and §4.1 schema, `DivergenceRecord.missing_companion` is a first-class field populated from `AdmissionDecision.issues` (filter for `code == "missing_companion"`). The 14-day shadow window captures every MISSING_COMPANION emission. `agreement_class` for these records is `DISAGREE_COMPANION` (per §4.5 classifier), so they count toward the per-profile divergence rate as a NEW signal current admission doesn't have.

This means MISSING_COMPANION is OBSERVED but not BLOCKED in P3's shadow phase — P3 captures the rate so P4 cutover decision is informed. Per §0 INCONSISTENCY-6, the exact sequencing of P2.a (P2's own shadow phase) vs P3's deployment is unresolved at the P2 SCAFFOLD level; whichever order they ship, P3's divergence_logger faithfully captures every MISSING_COMPANION emission AND every `companion_skip_token_used` emission as long as v_next.admit is invoked via the shim. The agreement-% denominator excludes SKIP_HONORED per §6.2 so operator-intentional skips don't depress the metric below the P4-cutover threshold.

### 6.2 companion_skip_token usage IS NOT divergence

Per §4.5 classifier, `agreement_class == "SKIP_HONORED"` when `companion_skip_used == True`. The analyzer (§1.2 `aggregate(..., skip_honored_filter=True)`) excludes SKIP_HONORED records from the agreement-% denominator. This is the EXPLICIT POLICY required to prevent intentional human overrides from depressing the agreement metric below the 95% P4-cutover threshold.

### 6.3 Aggregation formula (TWO metrics required for P4 gate)

The P4 cutover gate requires BOTH of the following metrics to pass:
1. `agreement_pct_excluding_skips >= 0.95` (per-profile, SKIP_HONORED records excluded from denominator)
2. `skip_honored_rate < 0.20` (across all records — guards against a high skip rate masking real divergence)

If 50% of records are SKIP_HONORED and the remaining 50% all AGREE, `agreement_pct_excluding_skips == 1.0` but `skip_honored_rate == 0.50` — the second metric blocks P4 cutover, correctly signaling that operator overrides are too frequent to trust the agreement signal.

```python
# In divergence_summary.aggregate(), per-profile agreement-pct:
def per_profile_agreement_pct(records: list[DivergenceRecord], profile_id: str, *, exclude_skip_honored: bool = True) -> float:
    profile_records = [r for r in records if r.profile_resolved_new == profile_id]
    if exclude_skip_honored:
        profile_records = [r for r in profile_records if r.agreement_class != "SKIP_HONORED"]
    if not profile_records:
        return None  # insufficient sample for this profile
    n_agree = sum(1 for r in profile_records if r.agreement_class == "AGREE")
    return n_agree / len(profile_records)

# Across all records (not per-profile):
def skip_honored_rate(records: list[DivergenceRecord]) -> float:
    if not records:
        return 0.0
    n_skip = sum(1 for r in records if r.agreement_class == "SKIP_HONORED")
    return n_skip / len(records)
```

The summary JSON surfaces BOTH metrics. The `aggregate()` return dict contains:
- `"agreement_pct_excluding_skips"`: dict[profile_id, float | None] — per-profile
- `"skip_honored_rate"`: float — across all records in the window
- `"p4_gate_ok"`: bool — True iff ALL per-profile agreement_pct >= 0.95 AND skip_honored_rate < 0.20 AND sample_size >= 500

### 6.4 Per-friction-pattern miss-count surfaces

```python
def per_friction_pattern_count(records: list[DivergenceRecord]) -> dict[str, int]:
    counter = Counter()
    for r in records:
        if r.friction_pattern_hit:
            counter[r.friction_pattern_hit] += 1
    return dict(counter)
```

Surfaced to operator via `evidence/topology_v_next_shadow/divergence_summary_{start}_{end}.json` and visible in `python -m scripts.topology_v_next.divergence_summary --start-date ... --end-date ...` CLI output.

---

## §7. Sub-Packet Decomposition

P3 ships as 3 independently testable sub-packets, each ≤1000 LOC.

### P3.1 — Divergence Logger (~450 LOC cap)

**Deliverables**: `divergence_logger.py` (~280 LOC) + `tests/topology_v_next/test_divergence_logger.py` (~170 LOC unit tests).

**Tests in P3.1**:
- `DivergenceRecord` frozen dataclass roundtrip
- `_serialize_record` produces single-line JSON; asserts no embedded `\n`
- `log_divergence` writes one line; verifies via `Path.read_text().splitlines()`
- `compute_event_type` classifier covers all 3 event_types
- `classify_divergence` returns every agreement class for synthetic records
- `daily_path` resolves UTC-day correctly
- `map_old_status_to_severity` covers all 6 current-side status values
- Stderr write on disk-full simulation (monkeypatch os.write to raise)

**Exit criterion**: `pytest tests/topology_v_next/test_divergence_logger.py` all pass; `evidence/topology_v_next_shadow/` writes work under tmp_path.

**Dependencies**: P1 SCAFFOLD merged (uses `.dataclasses` types).

### P3.2 — Divergence Summary Analyzer + CLI (~350 LOC cap)

**Deliverables**: `divergence_summary.py` (~300 LOC) + `tests/topology_v_next/test_divergence_summary.py` (~250 LOC).

**Tests in P3.2**:
- `aggregate` with synthetic 1000-record fixture (probe14 above)
- skip_honored exclusion correctness (probe14 sub-case)
- Per-friction-pattern counting
- CLI argparse: `--start-date`, `--end-date`, `--root`, `--out`, `--include-skip-honored`
- Insufficient/marginal/sufficient sample-size labels per INCONSISTENCY-2 resolution
- Malformed-JSON line handling (warning to stderr, continue)

**Exit criterion**: `pytest tests/topology_v_next/test_divergence_summary.py` all pass; `python -m scripts.topology_v_next.divergence_summary --start-date 2026-05-15 --end-date 2026-05-22` succeeds on a tmp fixture.

**Dependencies**: P3.1 must be complete (uses DivergenceRecord, classify_divergence).

**Note**: P3.2 is testable in isolation against synthetic divergence logs; does NOT require the shim to be wired up. This is the deliberate decomposition that lets P3.2 ship before P3.3 if needed.

### P3.3 — CLI Integration Shim + Wire-Up + 17 Probes

**Deliverables**: `cli_integration_shim.py` (~220 LOC) + `__init__.py` re-export update (~5 LOC) + the 5-hunk wire-up diff (§3 above, 13 LOC including module-top import) + 17 shadow probes under `tests/topology_v_next/regression/shadow/` (~800 LOC). Production code ≤800 LOC; test code (17 probes) ~800 LOC separate (per PACKET_INDEX § Packet Sizing Discipline — tests excluded from sub-packet cap per per-packet convention). Deliverable production sum: ~238 LOC (shim 220 + __init__ 5 + wire-up 13), well within the ≤800 LOC production cap.

**Tests in P3.3**:
- 17 shadow probes per §5 above
- `format_output` derives envelope correctly from each AdmissionDecision shape
- `maybe_shadow_compare` is transparent no-op when `v_next_shadow=False`
- `maybe_shadow_compare` catches v_next exceptions and returns `{"error": ...}` envelope without breaking payload
- `_build_divergence_record` correctly populates all §4.1 fields
- Anti-PHRASING_GAME_TAX guard: introspect `maybe_shadow_compare` and `format_output` signatures; assert no `phrase` / `task_phrase` / `wording` parameter exists

**Exit criterion**: `pytest tests/topology_v_next/regression/shadow/` all 17 probes pass; `python scripts/topology_doctor_cli.py --navigation --task "test" --files src/foo.py --v-next-shadow` produces a daily JSONL file with one record AND adds `v_next_shadow` key to JSON output.

**Dependencies**: P3.1 + P3.2 must be complete; AND P1 SCAFFOLD merged; AND P2 SCAFFOLD merged (probes 8 and 9 require P2's `_check_companion_required` and skip-token machinery).

---

## §8. Self-Check (anti-meta-pattern + anti-sidecar)

### 8.1 Does divergence_logger become a write-only sink that no one reads?

**NO.** divergence_summary.py IS the reader. The aggregate JSON it produces (`divergence_summary_{start}_{end}.json`) IS committed to git (per §4.6 retention). The operator reviews the summary at Day-7 (mid-window check) and Day-14 (P4 cutover gate) per PACKET_INDEX P3 acceptance. The summary surfaces:
- Per-profile agreement-pct (must be >95% for cutover)
- Per-friction-pattern hit counts (must be stable or declining)
- Insufficient/marginal/sufficient sample-size label
- Top 5 most-frequent DISAGREE_PROFILE pairs (debug surface)
- Top 5 SLICING_PRESSURE-affected file sets

The summary is also the input to P4's cutover decision in `MIGRATION_PATH §Phase 3`. No write-only sink risk — the reader is shipped in the same packet (P3.2).

### 8.2 Does the shim become a sidecar admission rail vs current?

**NO.** Per §1.3 contract and §2.4 pseudocode:
- The shim CALLS current admission first (via the existing `run_navigation` body that produces `payload` with `admission` field).
- The shim THEN calls v_next.admit in shadow.
- The shim NEVER mutates the `payload["ok"]`, `payload["admission"]`, `payload["task_blockers"]`, `payload["route_card"]` fields. It only ADDS a new top-level `v_next_shadow` key.
- Current admission remains AUTHORITATIVE for the return value of `run_navigation` — `nav_ok` is computed pre-shim per existing logic.
- The shim is a TRANSPARENT WRAPPER, not a parallel rail. v_next ADVISES; current DECIDES.

Anti-sidecar property verified by probe coverage:
- probes 1–7: confirm shim runs in parallel without affecting payload
- probe9: confirms SKIP_HONORED doesn't trip cutover (operator-intent-preservation)
- probes 12–14: confirm logger infrastructure is sound but never blocks admission
- probe17: confirms intent=None is gracefully handled without exception

### 8.3 Does the ADVISORY field in the normalized envelope get ignored?

**NO.** Per §2.1 the `advisory` field is ALWAYS populated (empty list when no advisory issues). The shim's envelope is added to `payload["v_next_shadow"]` which IS surfaced in the JSON output of `topology_doctor.py --navigation --json --v-next-shadow`. Agents that read `payload["v_next_shadow"]["advisory"]` see the advisory issues. Future P4 cutover packet will promote the envelope to top-level (replacing `payload["admission"]`); P3 captures the data to prove the surfacing path works.

The ADVISORY_OUTPUT_INVISIBILITY structural fix from P1 (issues at top level of AdmissionDecision) flows through `format_output` → envelope `advisory` field → operator-visible.

### 8.4 PHRASING_GAME_TAX guard: shim doesn't accept phrase parameter

**Verified anti-meta-pattern.** Per §1.3 and §2.4:
- `maybe_shadow_compare(payload, *, task, files, intent, v_next_shadow, friction_state)` — `task` is hashed to `task_hash = sha256(task)[:16]`; the hash (not the raw string) is passed to v_next.admit as `hint`. This prevents `closest_rejected_profile` from varying across phrase-varying calls with identical intent+files — the soft PHRASING_GAME_TAX that would otherwise leak through the hint output field.
- `format_output(decision)` — pure transformation of AdmissionDecision; no phrase input.
- `_build_divergence_record(*, payload, decision, task, task_hash, files, intent)` — raw `task` stored only for the `task_hash` sha256 computation; `task_hash` (not the raw string) is written to the JSONL record.

The shim does NOT have any `phrase`, `task_phrase`, `wording`, `hint_text`, `description`, or similar parameter that could become a phrase-routing reintroduction. `hint=task_hash` carries forward P1's structural hint-never-routes invariant while additionally eliminating the phrase-sensitivity of the diagnostic output field. The risk this guard addresses is shim-level reintroduction of phrase-as-routing, which would require either (a) a new phrase parameter on the shim's API OR (b) a new phrase-consuming call inside coverage_map/composition_rules — neither of which P3 introduces.

### 8.5 Anticipated critic SEV-1 catches (preempt)

Per 4-for-4 critic-catch pattern: each prior SCAFFOLD has had a SEV-1 caught. Anticipated P3 critic concerns and how this SCAFFOLD addresses them:

| Anticipated catch | Where addressed in this SCAFFOLD |
|---|---|
| GOAL field-name mismatch silently resolved | §0 INCONSISTENCY-1: explicit reviewer flag + reversibility note |
| Status-mapping table absence | §4.4 + §1.3 (`map_old_status_to_severity` as first-class) |
| Skip-token leaking into agreement-% denominator | §4.5 classifier + §6.2 + §6.3 formula + probe9 + probe14 |
| High skip rate masking divergence (50% SKIP_HONORED + 100% agreement = P4 unlock) | §6.3 DUAL-METRIC: `skip_honored_rate < 0.20` ALSO required; probe14 asserts both |
| Divergence log path false-convention claim (state/ vs evidence/) | §0 INCONSISTENCY-4 (C1 fix): BOTH conventions coexist; evidence/ governs by use-case match, not by state/ being reserved |
| Probe kill-criteria unfalsifiable | §5: every probe has ≥1 concrete `assert` statement as kill criterion |
| Probe count incomplete (not all 7 friction patterns covered) | §5 probes 15+16 (M1 fix): all 7 UNIVERSAL §12 patterns now covered; INC-3 updated |
| Admission-call threshold ambiguity (10 vs 500) | §0 INCONSISTENCY-2: PACKET_INDEX 500 governs; analyzer labels sample-size tier |
| HARD_STOP has no current equivalent — auto-DISAGREE problem | §4.4 footnote — intentional: HARD_STOP is a NEW safety signal, so DISAGREE_HARD_STOP is the desired classification |
| P2.a / P3 window overlap | §0 INC-6 + §6.1 — P2 §0.A premise on P1's `--v-next-shadow` is stale; flagged for P2 erratum; P3 captures regardless of P2.a sequencing |
| task→hint causes phrase-sensitive closest_rejected_profile | §1.3 + §2.4 + §8.4 (M3 fix): hash passed as hint, not raw task; probe3 asserts IDENTICAL closest_rejected_profile across phrase-varying calls |
| Wire-up diff split-hunk unapplicable (closing `}` outside range) | §3.4 (C2 fix): single unified hunk `@@ -2753,42 +2753,48 @@` covers entire dict literal; module-top import in §3.0 hunk |
| Lazy import hides import-time errors | §3.0 + §1.3 (M2 fix): import at module top; transparent no-op is in the function body, not the import |
| Single-syscall write-atomicity claim | §4.3: explicit POSIX guarantee citation + per-record 8 KiB cap to enforce single-syscall semantics |
| SLICING_PRESSURE not testable without friction_state | §2.4 (minor fix): `friction_state` parameter added to shim; probe4 now testable |
| Classifier crashes on None severity (v_next exception path) | §4.5 (minor fix): `if record.new_admit_severity is None: return "ERROR"` guard added |
| Shadow window double-counts P2-phase pre-cutover ADVISORY | §6.1: noted explicitly; P3 captures both pre-cutover ADVISORY and post-cutover SOFT_BLOCK behavior of P2 — this is the desired audit completeness |
| Operator can't tell sample is too small | §1.2: `aggregate` returns sample-size label `insufficient | marginal | sufficient` per §0 INC-2 resolution; CLI exit code 1 on insufficient |

---

## §9. P2.1 SEV-3 Carry-Forward

P2.0 critic flagged three SEV-3 items in `companion_skip_logger`-adjacent code that P2.1 was meant to address. Status per item below; addressed inline OR explicit deferral documented.

### 9.1 `datetime.utcnow()` deprecation in companion_skip_logger:148

**Status: APPLIED in P3.1 + retrofit recommended for P2.1.**

P3's `divergence_logger.log_divergence` MUST use `datetime.now(UTC)` (Python 3.11+) NOT `datetime.utcnow()` (deprecated in 3.12, scheduled removal). Implementation requires `from datetime import datetime, UTC` import. Inline doc-comment in `divergence_logger.py` cites the deprecation reason.

Retrofit recommendation for P2.1: change `companion_skip_logger:148` (the deprecated `datetime.utcnow()` call) to `datetime.now(UTC)` in a one-line follow-up commit. This is a P2.1 commit, not blocking P3. If P3 ships before the P2.1 retrofit, `companion_skip_logger` continues to work on Python 3.11/3.12 (deprecation, not removal); future Python 3.13/3.14 will fail.

### 9.2 token_value verbatim logging — secret leak risk

**Status: DEFERRED to P2.1 with explicit doc; P3 does not log secrets.**

P3's divergence_logger does NOT log `token_value` (no skip-token field in DivergenceRecord schema per §4.1). P3 logs `companion_skip_used: bool` only — a true/false flag indicating that A skip token was honored, without exposing WHICH token. This is by design: divergence_logger is a public-audit-tier surface; skip-token semantics are P2's concern.

Retrofit recommendation for P2.1: add an inline warning comment in `scripts/topology_v_next/profile_loader.py` `_parse_companion_fields` (or equivalent function that reads `companion_skip_acknowledge_token`) noting that token values are sensitive and should NEVER be logged to a public-audit surface, with reference to this carry-forward note. P2.1 should additionally redact `token_value` in the `companion_skip_token_log.jsonl` schema (P2 §5.2) to a sha256(token_value)[:16] hash so the log records "this token was used" without exposing the literal string. P3 does NOT block on this; it's a P2.1 follow-up.

### 9.3 `companion_skip_logger._atomic_append` concurrent-writer safety

**Status: P3 USES the corrected pattern; retrofit recommended for P2.1.**

P3's `divergence_logger.log_divergence` uses `O_APPEND + O_CREAT` + single `os.write` for multi-process safety (per §4.3 above). This is the CORRECTED pattern that handles concurrent writers from multiple topology_doctor processes.

Retrofit recommendation for P2.1: change `companion_skip_logger._atomic_append` to the SAME O_APPEND pattern (instead of the current `tmp + os.rename` pattern, which is whole-file atomicity — wrong contract for an append-only JSONL). The tmp+rename pattern is correct for "write a full new file"; it's the WRONG pattern for "append a single record to a shared file", because if two processes both compute the rename target simultaneously, one rename clobbers the other's appended record. P2.1 retrofit is the same code shape as P3's `divergence_logger.log_divergence` — direct copy-paste with path changed. NOTE: the P2 log's path (`state/companion_skip_token_log.jsonl`) does NOT need to move — state/ is a valid JSONL convention per INC-4 resolution above; only the write-pattern needs fixing.

If P2.1 retrofit hasn't shipped before P3 deploys, single-process operation (one topology_doctor process at a time on the laptop) keeps companion_skip_logger correct; the bug only manifests under concurrent multi-process invocations. P3's `divergence_logger` does NOT have this latent bug — uses O_APPEND from day one.

### 9.4 Summary table

| SEV-3 item | P3 status | P2.1 retrofit status |
|---|---|---|
| `datetime.utcnow()` deprecation | APPLIED (P3 uses `datetime.now(UTC)`) | Documented one-line follow-up |
| `token_value` verbatim logging | NOT APPLICABLE (P3 logs bool only); documented warning recommended | Deferred with explicit doc + hash-redaction recommendation |
| `_atomic_append` concurrent-writer safety | APPLIED (P3 uses O_APPEND) | Documented copy-paste retrofit |

All three carry-forwards are recorded so P2.1 author knows exactly what to fix.

---

## §10. LOC Budget Table

| Surface | LOC (cap) | Notes |
|---------|-----------|-------|
| `divergence_logger.py` | 280 | Per-call JSONL writer + classifier + path resolver |
| `divergence_summary.py` | 300 | Analyzer + argparse CLI subcommand |
| `cli_integration_shim.py` | 220 | Wire-up + format_output + transparent no-op |
| `__init__.py` re-export update | 5 | Re-export `maybe_shadow_compare` for direct import |
| Wire-up diff (`topology_doctor.py` + `topology_doctor_cli.py`) | 11 | Per §3.5 grep-verified hunks |
| `test_divergence_logger.py` | 170 | Unit tests for P3.1 |
| `test_divergence_summary.py` | 250 | Aggregate + CLI tests for P3.2 |
| `tests/topology_v_next/regression/shadow/` (17 probes) | 800 | Shadow integration tests for P3.3 |
| Stub binding YAML additions (none — P3 reuses P1's binding) | 0 | No new binding entries |
| `.gitignore` update | 2 | Adds `evidence/topology_v_next_shadow/*.jsonl*` exclusion |
| **Module subtotal** | **815** | Production code only (updated for 17-probe shim + friction_state plumbing) |
| **Test subtotal** | **1270** | Test code only (17 probes × ~800 LOC total; unit tests unchanged) |
| **Total** | **2085** | Production LOC is within PACKET_INDEX P3's 1000–1500 LOC budget; test LOC is not counted against the packet ceiling (tests are excluded from packet LOC accounting per PACKET_INDEX policy) |

Production LOC (~815) is within PACKET_INDEX P3's 1000–1500 budget. Test coverage is heavy (17 probes) because P3's shadow window is the gate for the highest-blast P4 cutover decision — probe density is intentional risk-reduction investment, not scope creep.

---

## §11. P1 + P2 Compatibility Confirmation

- P1 modules (`admission_engine`, `dataclasses`, `coverage_map`, `composition_rules`, etc.) are CONSUMED unchanged. P3 calls `admit()` via P1's public re-export `from scripts.topology_v_next import admit`.
- P2 modules (`_check_companion_required` helper, `BindingLayer.companion_required` field, `companion_skip_token_log.jsonl`) are CONSUMED unchanged. P3 reads MISSING_COMPANION issues from AdmissionDecision.issues (which P2 already populates) and reads `companion_skip_used` boolean from issue codes.
- The `composition_rules.apply_composition` §3.0 hook from P2 SCAFFOLD ensures probe2 and probe8 don't trip the composition_conflict trap.
- No P1 or P2 SCAFFOLD field is removed or renamed.
- No P1 or P2 public function signature changes.
- The `__init__.py` re-export gains `maybe_shadow_compare` so direct callers can import it. Existing P1 re-exports (admit, AdmissionDecision, Severity, Intent, load_binding_layer) are preserved.

---

## §12. Open Items for P4 (NOT in P3 scope)

For traceability — flagged so P4 packet (`topology_v_next_phase3_cutover_pilot`) authors know what P3 deliberately leaves open:
1. Promotion of v_next to authoritative for the lowest-blast profiles (per MIGRATION_PATH §Phase 3 cutover order — `packet_evidence`, `scripts_tooling` first).
2. Removal of the `v_next_shadow` CLI flag — it becomes the default behavior once cutover ships, with a `--v-current-fallback` for emergency revert.
3. Replacement of `payload["admission"]` with `payload["v_next_shadow"]` envelope at the top level (the cutover commit, per MIGRATION_PATH §Phase 4).
4. Deletion of OLD admission code paths once Phase 4 is reached (out of P3 scope; gated on 30-day post-cutover validation).
5. P2.1 SEV-3 retrofits per §9 above (P3-recommended; P3 does not block on them).
6. Reconciliation of UNIVERSAL §11 field-name spec with the envelope shape (§0 INCONSISTENCY-1 — doc-only update).
7. Reconciliation of MIGRATION_PATH §Phase 2 admission-call threshold (10 → 500) with PACKET_INDEX (§0 INCONSISTENCY-2 — doc-only update).
8. Optional: per-profile shadow-mode enable/disable in binding YAML (would let operator scope shadow to highest-risk profiles only; currently shim runs shadow for ALL admissions when flag is set).
