from pension_dashboard import enrich_holdings, portfolio_summary


def test_enrich_holdings_calculates_price_values_and_allocation():
    holdings = [
        {"asset_group": "성장", "name": "ETF A", "ticker": "390390", "target_weight": 0.5,
         "monthly_investment": 1000, "avg_cost": 100, "quantity": 10, "last_known_price": 120, "sort_order": 0},
        {"asset_group": "안전", "name": "ETF B", "ticker": "360750", "target_weight": 0.5,
         "monthly_investment": 1000, "avg_cost": 50, "quantity": 20, "last_known_price": 40, "sort_order": 1},
    ]
    prices = {"390390": {"price": 130, "asof": "2026-10-28", "source": "test"}}
    df = enrich_holdings(holdings, prices)
    assert list(df["현재가"]) == [130, 40]
    assert list(df["평가금"]) == [1300, 800]
    assert round(df["현재비중"].sum(), 8) == 1
    assert abs(df.loc[0, "수익률"] - 0.3) < 1e-9
    assert df.loc[0, "시세출처"] == "test"


def test_portfolio_summary():
    holdings = [
        {"asset_group": "A", "name": "ETF", "ticker": "390390", "target_weight": 1,
         "monthly_investment": 1, "avg_cost": 10, "quantity": 2, "last_known_price": 15, "sort_order": 0},
    ]
    summary = portfolio_summary(enrich_holdings(holdings))
    assert summary["invested"] == 20
    assert summary["market_value"] == 30
    assert summary["profit"] == 10
    assert summary["return_pct"] == 0.5
