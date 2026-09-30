# Created: 2026-09-29
# Last reused/audited: 2026-09-29
"""Bounded TRADE-owned rechecks of durable review debt, not forced resolution.

Existing fill, chain and order reducers continue to create authoritative facts.
This consumer closes only an absence dispute with a matching proven settlement.
All other debt retains explicit retry and age telemetry for its native owner.
"""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
import json
import logging
import sqlite3
from src.state.review_work_items import due_work,record_work_attempt,resolve_work_item

_LOG=logging.getLogger(__name__)
_ABSENCE_REASONS=frozenset({"TIMEOUT_ABSENCE_UNCONFIRMED","CONFIRMED_FILL_CHAIN_ABSENCE_CONFLICT","TERMINAL_RESTORE_EXPOSURE"})

def _utc(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (ValueError,TypeError):return None

def _resolution_evidence(conn,item):
    if item.owner_table!="position_current":return None
    if item.reason_code.value not in _ABSENCE_REASONS:return None
    # A display phase alone is not terminal proof. Only a durable finalized
    # settlement event with venue authority closes this class of exposure debt.
    row=conn.execute("SELECT event_id,payload_json,occurred_at FROM position_events "
                     "WHERE position_id=? AND event_type='SETTLED' ORDER BY sequence_no DESC LIMIT 1",(item.subject_id,)).fetchone()
    if row:
        data=json.loads(row[1] or "{}")
        stamp=_utc(row[2]);start=_utc(item.first_seen_at)
        if (stamp is not None and start is not None and stamp>=start
            and data.get("settlement_authority")=="VENUE_RESOLVED"
            and data.get("settlement_truth_source")):
            return "venue_finalized_position_event:"+str(row[0])
    # Current display rows are not independently sufficient chain evidence.
    # Existing chain/fill owners resolve those disputes with their native proofs.
    return None

def reconcile_review_work_items(conn:sqlite3.Connection, *, now:datetime|None=None, limit:int=8):
    """Called by scheduled recovery using its existing short TRADE write lease."""
    stats={"scanned":0,"advanced":0,"stayed":0,"errors":0,"attempted":0,"oldest_open_seconds":0.0}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_work_items'").fetchone() is None:return stats
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError("review retry time must be aware")
    stamp=now.astimezone(timezone.utc).isoformat()
    oldest=conn.execute("SELECT MIN(first_seen_at) FROM review_work_items WHERE status='OPEN'").fetchone()[0]
    if oldest and _utc(oldest):stats["oldest_open_seconds"]=max(0.0,(now-_utc(oldest)).total_seconds())
    for item in due_work(conn,now=stamp,limit=max(1,min(int(limit),50)),owner_domains=("trade","trades")):
        stats["scanned"]+=1
        retry=(now+timedelta(seconds=min(300,5*2**min(item.attempt_count,6)))).isoformat()
        if not record_work_attempt(conn,work_id=item.work_id,authority_revision=item.authority_revision,
                                  expected_attempt_count=item.attempt_count,at=stamp,retry_at=retry):continue
        stats["attempted"]+=1
        try:evidence=_resolution_evidence(conn,item)
        except (sqlite3.Error,ValueError,TypeError):
            stats["errors"]+=1;evidence=None
        if evidence and resolve_work_item(conn,work_id=item.work_id,authority_revision=item.authority_revision,
                                         resolver_identity="src.execution.review_work_delivery",
                                         resolution_evidence=evidence,resolved_at=stamp):stats["advanced"]+=1
        else:stats["stayed"]+=1
    if stats["scanned"]:
        _LOG.info("REVIEW_WORK_RETRY %s",json.dumps(stats,sort_keys=True))
    return stats
