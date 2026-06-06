from __future__ import annotations

# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Authority basis: Operator request — Opportunity Book evidence must persist in no-submit receipt_json without schema migration.

import json

from src.events.no_submit_receipts import _receipt_json
from src.events.reactor import EventSubmissionReceipt


def test_opportunity_book_omitted_from_receipt_json_when_absent():
    payload = json.loads(
        _receipt_json(
            EventSubmissionReceipt(
                submitted=False,
                event_id="event-1",
                final_intent_id="intent-1",
                side_effect_status="NO_SUBMIT",
                proof_accepted=True,
            )
        )
    )

    assert "opportunity_book" not in payload


def test_opportunity_book_included_in_receipt_json_when_present():
    payload = json.loads(
        _receipt_json(
            EventSubmissionReceipt(
                submitted=False,
                event_id="event-1",
                final_intent_id="intent-1",
                side_effect_status="NO_SUBMIT",
                proof_accepted=True,
                opportunity_book={
                    "book_id": "book-1",
                    "selected_candidate_id": "candidate-1",
                    "loser_reasons": {"candidate-2": "FAMILY_RANK_LOST:rank=2"},
                },
            )
        )
    )

    assert payload["opportunity_book"]["book_id"] == "book-1"
    assert payload["opportunity_book"]["selected_candidate_id"] == "candidate-1"
