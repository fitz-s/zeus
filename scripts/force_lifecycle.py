import logging
from datetime import datetime, timezone
import uuid

from src.state.portfolio import load_portfolio, save_portfolio, Position, add_position, close_position
from src.state.db import get_connection, log_trade_entry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mock_lifecycle")

def run():
    from src.state.db import init_schema
    conn = get_connection()
    init_schema(conn)
    portfolio = load_portfolio()
    
    # 1. Grab a real token_id with tick history so Replay works
    row = conn.execute(
        "SELECT token_id, city, target_date, range_label FROM token_price_log "
        "WHERE token_id IS NOT NULL ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    
    if not row:
        logger.error("No token_price_log entries found to mock.")
        return
        
    token_id, city, target_date, bin_label = row
    trade_id = "sim-" + str(uuid.uuid4())[:12]
    
    # Fake Entry
    pos = Position(
        trade_id=trade_id,
        market_id="mock_market_1",
        token_id=token_id,
        city=city,
        cluster="",
        target_date=target_date,
        bin_label=bin_label or "MOCK_BIN",
        direction="buy_yes",
        entry_price=0.45,
        size_usd=10.0,
        shares=10.0 / 0.45,
        cost_basis_usd=10.0,
        p_posterior=0.60,
        edge=0.15,
        entry_ci_width=0.08,
        entry_method="ens_member_counting",
        entered_at=datetime.now(timezone.utc).isoformat(),
        state="entered",
        edge_source="mock"
    )
    
    logger.info(f"Initiating Mock Paper Entry for {city} | Trade: {trade_id}")
    add_position(portfolio, pos)
    log_trade_entry(conn, pos)
    
    # Fake Exit
    logger.info(f"Triggering Mock Paper Exit for {city} | Trade: {trade_id}")
    closed = close_position(portfolio, trade_id, exit_price=0.55, exit_reason="MOCK_PROFIT_TEST")
    
    if closed:
        save_portfolio(portfolio)
        logger.info("Lifecycle complete. recent_exits and trade_decisions updated.")
    else:
        logger.error("Failed to close position.")
        
if __name__ == "__main__":
    run()
