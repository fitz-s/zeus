# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 ~13:05Z "这根本不是从下载到下单的全流程
#   验证 到处都是断点 … 到死都不知道卡在哪里了" + the original 2026-06-10 e2e demand
#   ("e2e验证下载到下单 … 甚至没有人probe过是否能下载"). Every probe below is the
#   EXACT live query used during the 2026-06-11 day-long dig, codified so the next
#   stall is a 10-second command, never a day.
"""END-TO-END MONEY-PATH WALKER: download -> order, one scope, every stage, real state.

Usage:
    .venv/bin/python scripts/verify_e2e_money_path.py --city "Hong Kong" \
        --target-date 2026-06-12 --metric high

Walks the FULL chain against the LIVE databases (read-only) and the real provider
probes / bundle reader, printing one row per stage with PASS/WARN/FAIL + the exact
reason, and finishes with the FIRST FAILING STAGE — the answer to "卡在哪里".

Stages:
   1 provider_probes      cycle availability per leg (AIFS index / anchor ladder)
   2 raw_artifacts        anchor+AIFS journal rows for the newest cycle (age, transport)
   3 instruments          raw_model_forecasts rows for the cycle (models present)
   4 posterior            freshest tradeable posterior (mode, q_lcb, cycle age, brand)
   5 readiness            readiness_state row (status, expiry, staleness brand expected)
   6 market_topology      market_events bins + executable snapshot freshness + asks
   7 event_supply         newest opportunity event + processing status/attempts
   8 bundle_read          REAL read_replacement_forecast_bundle call (the live gate set)
   9 last_decision        latest regret/receipt for the scope (stage + full reason)
  10 submit_gates         M5 latch / riskguard level / admission flags / live scope
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

UTC = timezone.utc
FORECASTS = ROOT / "state" / "zeus-forecasts.db"
TRADES = ROOT / "state" / "zeus_trades.db"
WORLD = ROOT / "state" / "zeus-world.db"


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


class Walker:
    def __init__(self, city: str, target_date: str, metric: str) -> None:
        self.city, self.target_date, self.metric = city, target_date, metric
        self.now = datetime.now(UTC)
        self.rows: list[tuple[str, str, str]] = []  # (stage, verdict, detail)
        self.first_fail: str | None = None

    def add(self, stage: str, verdict: str, detail: str) -> None:
        self.rows.append((stage, verdict, detail))
        if verdict == "FAIL" and self.first_fail is None:
            self.first_fail = stage

    # -- 1 ------------------------------------------------------------------
    def provider_probes(self) -> None:
        try:
            from src.data.replacement_cycle_availability import (
                newest_complete_cycle,
                probe_aifs_cycle_available,
                probe_anchor_available_any,
                resolve_cycle_leg_availability,
            )

            availability = resolve_cycle_leg_availability(
                self.now,
                probe_aifs=probe_aifs_cycle_available,
                probe_anchor=probe_anchor_available_any,
            )
            newest = newest_complete_cycle(availability)
            aifs = next((a.cycle for a in availability if a.aifs_available), None)
            anchor = next((a.cycle for a in availability if a.anchor_available), None)
            if newest is None:
                self.add("1 provider_probes", "FAIL",
                         f"no pair-complete cycle provable (aifs={aifs} anchor={anchor})")
            else:
                self.add("1 provider_probes", "PASS",
                         f"newest_complete={newest.isoformat()} aifs={aifs} anchor={anchor}")
            self.cycle = newest
        except Exception as exc:  # noqa: BLE001
            self.add("1 provider_probes", "FAIL", f"probe machinery error: {exc}")
            self.cycle = None

    # -- 2 ------------------------------------------------------------------
    def raw_artifacts(self) -> None:
        conn = _ro(FORECASTS)
        try:
            row = conn.execute(
                """SELECT source_id, MAX(source_cycle_time) AS cyc, MAX(captured_at) AS cap
                   FROM raw_forecast_artifacts GROUP BY source_id
                   HAVING source_id IN ('ecmwf_aifs_ens','openmeteo_ecmwf_ifs_9km')"""
            ).fetchall()
            legs = {r["source_id"]: (r["cyc"], r["cap"]) for r in row}
            missing = {"ecmwf_aifs_ens", "openmeteo_ecmwf_ifs_9km"} - set(legs)
            anchor_city = conn.execute(
                """SELECT MAX(source_cycle_time) FROM raw_forecast_artifacts
                   WHERE source_id='openmeteo_ecmwf_ifs_9km'
                     AND json_extract(artifact_metadata_json,'$.city') = ?""",
                (self.city,),
            ).fetchone()[0]
            detail = " ".join(f"{k}={v[0]}" for k, v in sorted(legs.items()))
            detail += f" anchor[{self.city}]={anchor_city}"
            if missing:
                self.add("2 raw_artifacts", "FAIL", f"legs missing: {missing} | {detail}")
            elif anchor_city is None:
                self.add("2 raw_artifacts", "WARN", f"no per-city anchor artifact | {detail}")
            else:
                self.add("2 raw_artifacts", "PASS", detail)
        finally:
            conn.close()

    # -- 3 ------------------------------------------------------------------
    def instruments(self) -> None:
        conn = _ro(FORECASTS)
        try:
            cyc = self.cycle.isoformat() if self.cycle else None
            rows = conn.execute(
                """SELECT model, COUNT(*) AS n FROM raw_model_forecasts
                   WHERE source_cycle_time = COALESCE(?, source_cycle_time)
                     AND city = ? AND target_date = ? GROUP BY model""",
                (cyc, self.city, self.target_date),
            ).fetchall()
            models = sorted(r["model"] for r in rows)
            if not models:
                any_cycle = conn.execute(
                    "SELECT MAX(source_cycle_time) FROM raw_model_forecasts WHERE city=?",
                    (self.city,),
                ).fetchone()[0]
                self.add("3 instruments", "WARN",
                         f"0 instrument rows for cycle={cyc}; city high-water={any_cycle} "
                         "(fusion falls to single-anchor -> U0R_CAPTURE_MISSING)")
            else:
                self.add("3 instruments", "PASS", f"{len(models)} models: {','.join(models)}")
        except sqlite3.OperationalError as exc:
            self.add("3 instruments", "WARN", f"schema probe: {exc}")
        finally:
            conn.close()

    # -- 4 ------------------------------------------------------------------
    def posterior(self) -> None:
        conn = _ro(FORECASTS)
        try:
            row = conn.execute(
                """SELECT source_cycle_time, computed_at, q_lcb_json IS NOT NULL AS lcb,
                          json_extract(provenance_json,'$.replacement_q_mode') AS mode
                   FROM forecast_posteriors
                   WHERE city=? AND target_date=? AND temperature_metric=?
                     AND training_allowed=0
                     AND trade_authority_status IN ('SHADOW_ONLY','SHADOW_VETO_ONLY')
                     AND q_lcb_json IS NOT NULL
                   ORDER BY computed_at DESC LIMIT 1""",
                (self.city, self.target_date, self.metric),
            ).fetchone()
            if row is None:
                newest_any = conn.execute(
                    """SELECT json_extract(provenance_json,'$.replacement_q_mode'),
                              source_cycle_time FROM forecast_posteriors
                       WHERE city=? AND target_date=? AND temperature_metric=?
                       ORDER BY computed_at DESC LIMIT 1""",
                    (self.city, self.target_date, self.metric),
                ).fetchone()
                self.add("4 posterior", "FAIL",
                         f"NO tradeable-grade posterior; newest row mode={newest_any[0] if newest_any else None} "
                         f"cycle={newest_any[1] if newest_any else None}")
                return
            cyc = datetime.fromisoformat(str(row["source_cycle_time"]).replace("Z", "+00:00"))
            age_h = (self.now - cyc).total_seconds() / 3600.0
            verdict = "PASS" if age_h <= 30.0 else "WARN"
            self.add("4 posterior", verdict,
                     f"mode={row['mode']} cycle={row['source_cycle_time']} age={age_h:.1f}h "
                     f"(>30h serves WITH brand per operator law)")
        finally:
            conn.close()

    # -- 5 ------------------------------------------------------------------
    def readiness(self) -> None:
        conn = _ro(FORECASTS)
        try:
            row = conn.execute(
                """SELECT status, expires_at FROM readiness_state
                   WHERE json_extract(provenance_json,'$.city')=?
                     AND json_extract(provenance_json,'$.target_date')=?
                     AND json_extract(provenance_json,'$.temperature_metric')=?
                   ORDER BY rowid DESC LIMIT 1""",
                (self.city, self.target_date, self.metric),
            ).fetchone()
            if row is None:
                self.add("5 readiness", "FAIL", "no readiness row for scope")
                return
            expired = str(row["expires_at"] or "") <= self.now.isoformat()
            verdict = "WARN" if expired else "PASS"
            self.add("5 readiness", verdict,
                     f"status={row['status']} expires_at={row['expires_at']}"
                     + (" [EXPIRED -> brand, serves]" if expired else ""))
        finally:
            conn.close()

    # -- 6 ------------------------------------------------------------------
    def market_topology(self) -> None:
        fconn, tconn = _ro(FORECASTS), _ro(TRADES)
        try:
            bins = fconn.execute(
                """SELECT COUNT(*) FROM market_events
                   WHERE city=? AND target_date=? AND temperature_metric=?
                     AND token_id IS NOT NULL AND range_label IS NOT NULL""",
                (self.city, self.target_date, self.metric),
            ).fetchone()[0]
            snaps = tconn.execute(
                """SELECT COUNT(*) AS n, MAX(captured_at) AS cap,
                          SUM(CASE WHEN orderbook_top_ask IS NOT NULL THEN 1 ELSE 0 END) AS asks
                   FROM executable_market_snapshots
                   WHERE condition_id IN (
                       SELECT condition_id FROM forecasts.market_events
                       WHERE city=? AND target_date=? AND temperature_metric=?)"""
                if False else
                """SELECT COUNT(*) AS n, MAX(captured_at) AS cap,
                          SUM(CASE WHEN orderbook_top_ask IS NOT NULL THEN 1 ELSE 0 END) AS asks
                   FROM executable_market_snapshots WHERE event_slug LIKE ?""",
                (f"%temperature-in-{self.city.lower().replace(' ', '-')}-on-%",),
            ).fetchone()
            if bins == 0:
                self.add("6 market_topology", "FAIL", "0 market bins in registry for scope")
                return
            cap = str(snaps["cap"] or "")
            fresh = cap >= (self.now.isoformat()[:16])  # same-minute freshness hint
            self.add("6 market_topology", "PASS" if (snaps["n"] or 0) > 0 else "FAIL",
                     f"bins={bins} snapshots={snaps['n']} with_ask={snaps['asks']} "
                     f"last_capture={cap}{' (fresh-minute)' if fresh else ''}")
        finally:
            fconn.close()
            tconn.close()

    # -- 7 ------------------------------------------------------------------
    def event_supply(self) -> None:
        conn = _ro(WORLD)
        try:
            row = conn.execute(
                """SELECT e.created_at, p.processing_status, p.attempt_count
                   FROM opportunity_events e
                   LEFT JOIN opportunity_event_processing p ON p.event_id=e.event_id
                   WHERE e.entity_key LIKE ? ORDER BY e.created_at DESC LIMIT 1""",
                (f"{self.city}|{self.target_date}|{self.metric}%",),
            ).fetchone()
            if row is None:
                self.add("7 event_supply", "FAIL", "no opportunity event for scope (producer silent)")
                return
            age_min = (self.now - datetime.fromisoformat(
                str(row["created_at"]).replace("Z", "+00:00"))).total_seconds() / 60.0
            verdict = "PASS" if age_min <= 15 else "WARN"
            self.add("7 event_supply", verdict,
                     f"newest event {age_min:.0f}min old status={row['processing_status']} "
                     f"attempts={row['attempt_count']}")
        finally:
            conn.close()

    # -- 8 ------------------------------------------------------------------
    def bundle_read(self) -> None:
        try:
            from src.data.replacement_forecast_bundle_reader import read_replacement_forecast_bundle
            from src.engine.replacement_forecast_hook_factory import _latest_replacement_readiness

            conn = _ro(FORECASTS)
            try:
                readiness = _latest_replacement_readiness(
                    conn, city=self.city, target_date=self.target_date,
                    temperature_metric=self.metric,
                )
                if readiness is None:
                    self.add("8 bundle_read", "FAIL", "readiness decision unloadable")
                    return
                result = read_replacement_forecast_bundle(
                    conn, baseline_bundle=None, readiness=readiness,
                    city=self.city, target_date=self.target_date,
                    temperature_metric=self.metric, decision_time=self.now,
                    require_baseline_bundle=False,
                )
                if result.ok and result.bundle is not None:
                    prov = result.bundle.provenance_json or {}
                    brands = prov.get("staleness_violations") or []
                    self.add("8 bundle_read", "PASS",
                             f"{result.reason_code} mode={prov.get('replacement_q_mode')} "
                             f"cycle={result.bundle.source_cycle_time}"
                             + (f" brands={brands}" if brands else ""))
                else:
                    self.add("8 bundle_read", "FAIL", f"reason={result.reason_code}")
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            self.add("8 bundle_read", "FAIL", f"reader call error: {exc}")

    # -- 9 ------------------------------------------------------------------
    def last_decision(self) -> None:
        conn = _ro(WORLD)
        try:
            # envelope_json is additive (operator law 2026-06-11); guard for older DBs that
            # predate the column so the walker still runs on a not-yet-migrated copy.
            cols = {r[1] for r in conn.execute("PRAGMA table_info(no_trade_regret_events)")}
            envelope_select = ", envelope_json" if "envelope_json" in cols else ""
            row = conn.execute(
                f"""SELECT decision_time, rejection_stage, rejection_reason, bin_label{envelope_select}
                   FROM no_trade_regret_events
                   WHERE city=? AND target_date=? AND metric=?
                   ORDER BY decision_time DESC LIMIT 1""",
                (self.city, self.target_date, self.metric),
            ).fetchone()
            if row is None:
                self.add("9 last_decision", "WARN", "no regret receipt yet (scope unevaluated or accepted)")
            else:
                self.add("9 last_decision", "INFO",
                         f"[{row['decision_time']}] stage={row['rejection_stage']} "
                         f"reason={row['rejection_reason']} bin={row['bin_label']}")
                # FULL provenance envelope — the operator's "一切可被溯源" query entry. Pretty-print
                # (indented JSON, colon-free-safe) so every age / data-combination / settlement
                # delta / FULL rejection reason for the latest decision is human-readable here.
                envelope_text = row["envelope_json"] if "envelope_json" in cols else None
                if envelope_text:
                    try:
                        from src.contracts.decision_provenance import pretty_envelope

                        self.add("9 envelope", "INFO", "\n" + pretty_envelope(json.loads(envelope_text)))
                    except Exception as exc:  # noqa: BLE001 — display-only; never fail the walk
                        self.add("9 envelope", "WARN", f"envelope present but unrenderable: {exc}")
                else:
                    self.add("9 envelope", "WARN", "no provenance envelope on this receipt "
                             "(legacy row predating the envelope, or builder fail-soft NULL)")
        finally:
            conn.close()

    # -- 10 -----------------------------------------------------------------
    def submit_gates(self) -> None:
        details: list[str] = []
        conn = _ro(TRADES)
        try:
            unresolved = conn.execute(
                "SELECT COUNT(*) FROM exchange_reconcile_findings WHERE resolved_at IS NULL"
            ).fetchone()[0]
            details.append(f"unresolved_findings={unresolved}")
        finally:
            conn.close()
        try:
            status = json.loads((ROOT / "state" / "status_summary.json").read_text())
            details.append(f"risk={((status.get('risk') or {}).get('level'))}")
        except Exception:  # noqa: BLE001
            details.append("risk=unreadable")
        try:
            from src.config import settings

            edli = settings["edli_v1"]
            details.append(f"scope={edli.get('edli_live_scope')}")
            details.append(
                "intermediate_admission="
                + str(edli.get("replacement_0_1_intermediate_cycle_live_admission_enabled"))
            )
        except Exception as exc:  # noqa: BLE001
            details.append(f"settings=unreadable({exc})")
        verdict = "PASS" if "unresolved_findings=0" in details and "risk=GREEN" in " ".join(details) else "WARN"
        self.add("10 submit_gates", verdict, " ".join(details))

    # ------------------------------------------------------------------------
    def run(self) -> int:
        for fn in (self.provider_probes, self.raw_artifacts, self.instruments,
                   self.posterior, self.readiness, self.market_topology,
                   self.event_supply, self.bundle_read, self.last_decision,
                   self.submit_gates):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 — a stage probe crash is itself a finding
                self.add(fn.__name__, "FAIL", f"probe crashed: {exc}")
        width = max(len(s) for s, _, _ in self.rows)
        print(f"E2E MONEY-PATH WALK  scope={self.city}|{self.target_date}|{self.metric}  "
              f"now={self.now.isoformat()}")
        print("-" * 100)
        for stage, verdict, detail in self.rows:
            print(f"{stage:<{width}}  {verdict:<4}  {detail}")
        print("-" * 100)
        if self.first_fail:
            print(f"FIRST FAILING STAGE: {self.first_fail}  <-- 卡在这里")
            return 1
        print("ALL STAGES PASS/WARN — chain is live to the submit boundary; "
              "remaining no-trade verdicts are per-event economics (see stage 9).")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True)
    ap.add_argument("--target-date", required=True)
    ap.add_argument("--metric", default="high", choices=("high", "low"))
    args = ap.parse_args()
    return Walker(args.city, args.target_date, args.metric).run()


if __name__ == "__main__":
    raise SystemExit(main())
