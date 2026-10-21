from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from dashboard_v2.services.yfinance_provider import YFinanceDailyProvider, MarketDataFetchError

NY = ZoneInfo("America/New_York")


class FakeTicker:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def history(self, **kwargs):
        self.calls.append(kwargs)
        return self.frame.copy()


def market_frame(dates):
    index = pd.DatetimeIndex([pd.Timestamp(d, tz=NY) for d in dates])
    return pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(len(index))],
            "High": [102.0 + i for i in range(len(index))],
            "Low": [99.0 + i for i in range(len(index))],
            "Close": [101.0 + i for i in range(len(index))],
            "Volume": [1000 + i for i in range(len(index))],
        }, index=index,
    )


def test_live_request_excludes_current_daily_bar_before_regular_close():
    ticker = FakeTicker(market_frame(["2026-10-19", "2026-10-20"]))
    provider = YFinanceDailyProvider(
        ticker_factory=lambda _: ticker,
        now_fn=lambda: datetime(2026, 10, 20, 11, 30, tzinfo=NY),
    )
    bars = provider.daily_bars("amat")
    assert bars["Date"].dt.date.tolist() == [date(2026, 10, 19)]
    assert ticker.calls[0]["auto_adjust"] is True
    assert ticker.calls[0]["actions"] is False
    assert ticker.calls[0]["prepost"] is False
    assert ticker.calls[0]["end"] == "2026-10-21"


def test_live_request_can_include_current_bar_after_regular_close():
    ticker = FakeTicker(market_frame(["2026-10-19", "2026-10-20"]))
    provider = YFinanceDailyProvider(
        ticker_factory=lambda _: ticker,
        now_fn=lambda: datetime(2026, 10, 20, 16, 1, tzinfo=NY),
    )
    bars = provider.daily_bars("MU")
    assert bars["Date"].dt.date.tolist() == [date(2026, 10, 19), date(2026, 10, 20)]


def test_explicit_historical_as_of_includes_that_date_and_excludes_future_rows():
    ticker = FakeTicker(market_frame(["2026-10-19", "2026-10-20", "2026-10-21"]))
    provider = YFinanceDailyProvider(
        ticker_factory=lambda _: ticker,
        now_fn=lambda: datetime(2026, 10, 20, 11, 30, tzinfo=NY),
    )
    bars = provider.daily_bars("GOOG", as_of_date="2026-10-20")
    assert bars["Date"].dt.date.tolist() == [date(2026, 10, 19), date(2026, 10, 20)]
    assert ticker.calls[0]["end"] == "2026-10-21"


def test_provider_rejects_missing_ohlcv_fields_without_guessing():
    frame = market_frame(["2026-10-19"]).drop(columns=["Volume"])
    provider = YFinanceDailyProvider(
        ticker_factory=lambda _: FakeTicker(frame),
        now_fn=lambda: datetime(2026, 10, 20, 17, 0, tzinfo=NY),
    )
    with pytest.raises(MarketDataFetchError, match="Volume"):
        provider.daily_bars("STX")


def test_live_provider_requires_timezone_aware_clock():
    provider = YFinanceDailyProvider(
        ticker_factory=lambda _: FakeTicker(market_frame(["2026-10-19"])),
        now_fn=lambda: datetime(2026, 10, 20, 17, 0),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        provider.daily_bars("META")

def test_provider_handles_yfinance_index_named_date_without_ambiguous_sort():
    frame = market_frame(["2026-10-16", "2026-10-19", "2026-10-20"])
    frame.index.name = "Date"  # mirrors common yfinance history output
    provider = YFinanceDailyProvider(
        ticker_factory=lambda _: FakeTicker(frame),
        now_fn=lambda: datetime(2026, 10, 20, 17, 0, tzinfo=NY),
    )

    bars = provider.daily_bars("AMAT")

    assert bars["Date"].dt.date.tolist() == [
        date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20)
    ]
    assert list(bars.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert not bars["Date"].duplicated().any()

