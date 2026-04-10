# Model Routing and Reasoning-Effort Policy

> Extracted from original root AGENTS.md §8. Applies only to Codex / GPT-family models.
> If you are not a Codex / GPT-family model, skip this file entirely — map the intent to your local runtime equivalent.

## Preferred models

Normal work in this repo should use exactly three models:
- `gpt-5.4` — leader model
- `gpt-5.4-mini` — verifier / writer / bounded-review
- `gpt-5.3-codex-spark` — default scout subagent

Do not recommend or auto-route to `gpt-5.3-codex`, `gpt-5-codex`, or `gpt-5-codex-mini` unless the user explicitly asks.

## Context windows and working budgets

| Model | Context ceiling | Preferred budget |
|-------|----------------|-----------------|
| `gpt-5.4` | ~272k | ≤ 220k input |
| `gpt-5.4-mini` | ~272k | ≤ 140k input |
| `gpt-5.3-codex-spark` | ~128k | ≤ 40k input |

Treat these as routing safety rails, not theoretical hard ceilings.

## Model → role mapping

| Role | Model | Use for |
|------|-------|---------|
| Leader | `gpt-5.4` | Architecture authority, contract freezing, cross-zone reasoning, packet judgment, final integration, final acceptance |
| Verifier / Writer | `gpt-5.4-mini` | Evidence collection, targeted review, bounded synthesis, documentation polish, compact follow-up analysis, contradiction extraction, scout-plus lanes |
| Scout | `gpt-5.3-codex-spark` | Narrow read-only lookup, repo mapping, symbol search, relationship tracing, diff triage, repeated fact gathering |

## Reasoning-effort levels

| Level | Use for |
|-------|---------|
| `low` | Fast lookup, grep-like exploration, structure mapping, obvious transforms |
| `medium` | Bounded comparison, packet drafting, shortlist building, moderate synthesis |
| `high` | Implementation planning, non-trivial debugging, verifier judgments, blast-radius code review |
| `xhigh` | Architecture authority, governance/law edits, schema/control-plane decisions, contradictory truth-surface resolution |

## Child-agent rules

- Hard cap: 6 active native subagents at once
- Preferred steady-state: 2-3 active lanes
- Prefer 2-4 parallel spark scouts before broad implementation on multi-file tasks
- Keep spark batons small, concrete, read-only, evidence-returning
- If spark times out or returns ambiguous synthesis → escalate to `gpt-5.4-mini`, don't retry spark
- Keep final judgment, contract freezing, and acceptance on `gpt-5.4`

## Forbidden

- `xhigh` on routine scans
- Spark for final architecture claims, governance edits, or overlapping write lanes
- Mini for unresolved cross-zone design or kernel-law decisions
- Treating 1M leader window as permission for unbounded prompts
