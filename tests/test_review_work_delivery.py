# Created: 2026-09-29
# Last reused/audited: 2026-10-01
# Authority: REQ-20260929-223929-bf51a2; owner-local review retry/CAS law.
from datetime import datetime,timedelta,timezone
import json
import sqlite3
from src.state.schema.review_work_items_schema import ensure_table
from src.state.review_work_items import open_work_item, record_work_attempt
from src.contracts.review_work_item import ReviewReasonCode
from src.execution.review_work_delivery import reconcile_review_work_items

NOW=datetime(2026,9,30,4,tzinfo=timezone.utc)

def setup(reason=ReviewReasonCode.TIMEOUT_ABSENCE_UNCONFIRMED):
    conn=sqlite3.connect(":memory:");ensure_table(conn)
    conn.execute("CREATE TABLE position_events(event_id TEXT,position_id TEXT,event_type TEXT,payload_json TEXT,occurred_at TEXT,sequence_no INTEGER)")
    item=open_work_item(conn,owner_domain="trade",owner_table="position_current",subject_id="fixture",
        reason_code=reason,authority_revision=4,unbounded=True,now=(NOW-timedelta(minutes=1)).isoformat())
    return conn,item

def test_retry_is_owned_bounded_and_does_not_resolve_without_proof():
    conn,item=setup();stats=reconcile_review_work_items(conn,now=NOW)
    assert stats["attempted"]==stats["stayed"]==1 and stats["oldest_open_seconds"]==60
    assert reconcile_review_work_items(conn,now=NOW)["attempted"]==0
    assert conn.execute("SELECT attempt_count,status FROM review_work_items").fetchone()==(1,"OPEN")
    assert not record_work_attempt(conn,work_id=item.work_id,authority_revision=3,expected_attempt_count=1,
        at=(NOW+timedelta(hours=1)).isoformat(),retry_at=(NOW+timedelta(hours=2)).isoformat())
    conn.close()

def test_resolution_requires_matching_native_proof_and_correct_reason():
    for reason,expected in [(ReviewReasonCode.TIMEOUT_ABSENCE_UNCONFIRMED,1),(ReviewReasonCode.MISSING_FILL_ECONOMICS,0)]:
        conn,item=setup(reason)
        conn.execute("INSERT INTO position_events VALUES('settled','fixture','SETTLED',?,?,1)",
            (json.dumps({"settlement_authority":"VENUE_RESOLVED","settlement_truth_source":"fixture_finalized"}),NOW.isoformat()))
        assert reconcile_review_work_items(conn,now=NOW)["advanced"]==expected
        conn.close()


def test_chain_proof_resolves_only_matching_older_debt_under_cas():
    from src.execution.review_work_delivery import resolve_exit_absence_from_chain_proof
    from src.state.review_work_items import supersede_on_new_revision
    conn=sqlite3.connect(":memory:");ensure_table(conn)
    def debt(subject,asset,revision,at):
        return open_work_item(conn,owner_domain="trade",owner_table="position_current",subject_id=subject,
            reason_code=ReviewReasonCode.TIMEOUT_ABSENCE_UNCONFIRMED,authority_revision=revision,
            evidence_refs=(subject,asset),unbounded=True,now=at.isoformat())
    old=debt("p","tok",1,NOW-timedelta(minutes=5))
    other=debt("p","other-tok",5,NOW-timedelta(minutes=5))
    proof=lambda at:resolve_exit_absence_from_chain_proof(conn,subject_id="p",asset_id="tok",balance_units=7,observed_at=at)
    # A newer revision supersedes the old debt before the proof lands: the stale
    # revision cannot be resolved, and debt opened after the observation stays OPEN.
    supersede_on_new_revision(conn,owner_table="position_current",subject_id="p",
        reason_code=ReviewReasonCode.TIMEOUT_ABSENCE_UNCONFIRMED,new_authority_revision=3)
    newer=debt("p","tok",3,NOW+timedelta(minutes=1))
    assert proof(NOW)==0
    status=lambda w:conn.execute("SELECT status FROM review_work_items WHERE work_id=?",(w.work_id,)).fetchone()[0]
    assert (status(old),status(other),status(newer))==("SUPERSEDED","OPEN","OPEN")
    assert proof(NOW+timedelta(minutes=2))==1 and status(newer)=="RESOLVED" and status(other)=="OPEN"
    conn.close()
