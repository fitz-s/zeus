import json
import sqlite3

from scripts.etl_market_price_history import _extract_token_market_meta


def _row(raw_response: dict) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE market_events (
            city TEXT,
            target_date TEXT,
            event_id TEXT,
            condition_id TEXT,
            range_label TEXT,
            raw_response TEXT,
            imported_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO market_events
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Atlanta",
            "2026-04-11",
            "event-123",
            "row-condition",
            "54-55 F",
            json.dumps(raw_response),
            "2026-04-10T00:00:00Z",
        ),
    )
    return conn.execute("SELECT * FROM market_events").fetchone()


def test_extract_token_market_meta_matches_selected_market_id():
    raw_response = {
        "event_id": "event-123",
        "market_id": "market-2",
        "groupItemTitle": "54-55 F",
        "raw_event": {
            "id": "event-123",
            "slug": "highest-temperature-in-atlanta-on-april-11",
            "createdAt": "2026-04-09T00:00:00Z",
            "markets": [
                {
                    "id": "market-1",
                    "groupItemTitle": "52-53 F",
                    "conditionId": "condition-1",
                    "clobTokenIds": json.dumps(["yes-1", "no-1"]),
                },
                {
                    "id": "market-2",
                    "groupItemTitle": "54-55 F",
                    "conditionId": "condition-2",
                    "clobTokenIds": json.dumps(["yes-2", "no-2"]),
                },
            ],
        },
    }

    mappings = dict(_extract_token_market_meta(_row(raw_response)))

    assert set(mappings) == {"yes-2", "no-2"}
    assert mappings["yes-2"].market_slug == "event-123"
    assert mappings["yes-2"].condition_id == "condition-2"
    assert mappings["yes-2"].range_label == "54-55 F"
