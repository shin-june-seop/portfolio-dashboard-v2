from datetime import date
import pandas as pd
import pytest

from dashboard_v2.services.market_data_service import MarketDataService, MarketDataProviderNotConfigured


class Provider:
    def __init__(self, frame):
        self.frame = frame

    def daily_bars(self, symbol, *, as_of_date=None):
        return self.frame.copy()


def test_market_data_service_preserves_separate_adjusted_close():
    frame = pd.DataFrame({
        "Date": pd.to_datetime(["2026-10-20", "2026-10-21"]),
        "Open": [100, 101],
        "High": [102, 103],
        "Low": [99, 100],
        "Close": [101, 102],
        "Volume": [1000, 1100],
        "Adj Close": [100.5, 101.8],
    })
    result = MarketDataService(Provider(frame)).daily_bars("goog")
    assert list(result.columns) == ["Date", "Open", "High", "Low", "Close", "Volume", "Adj Close"]
    assert result["Close"].tolist() == [101.0, 102.0]
    assert result["Adj Close"].tolist() == [100.5, 101.8]


def test_market_data_service_fails_when_no_provider_is_configured():
    with pytest.raises(MarketDataProviderNotConfigured):
        MarketDataService().daily_bars("GOOG")
