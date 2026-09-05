"""Model tests for PortfolioClosedPosition (backend.db.models)."""
from backend.db.models.portfolio_close import PortfolioClosedPosition


def test_model_table_and_columns():
    cols = set(PortfolioClosedPosition.__table__.columns.keys())
    assert PortfolioClosedPosition.__tablename__ == (
        "portfolio_closed_positions"
    )
    assert {
        "id", "user_id", "ticker", "quantity", "buy_price", "sell_price",
        "buy_date", "sell_date", "fees", "realized_pnl",
        "realized_pnl_pct", "currency", "market", "sell_transaction_id",
        "notes", "created_at",
    } <= cols
