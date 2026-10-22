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
    closes = [101.0 + i for i in range(len(index))]
    return pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(len(index))],
            "High": [102.0 + i for i in range(len(index))],
            "Low": [99.0 + i for i in range(len(index))],
            "Close": closes,
            # Different from Close so tests prove the two bases remain separate.
            "Adj Close": [value * (0.985 + 0.005 * i) for i, value in enumerate(closes)],
            "Volume": [1000 + i for i in range(len(index))],
        }, index=index,
    )


def test_live_request_excludes_current_daily_bar_before_finalization_cutoff():
    ticker = FakeTicker(market_frame(["2026-10-19", "2026-10-20"]))
    provider = YFinanceDailyProvider(
        ticker_factory=lambda _: ticker,
        now_fn=lambda: datetime(2026, 10, 20, 11, 30, tzinfo=NY),
    )
    bars = provider.daily_bars("amat")
    assert bars["Date"].dt.date.tolist() == [date(2026, 10, 19)]
    assert ticker.calls[0]["auto_adjust"] is False
    assert ticker.calls[0]["actions"] is False
    assert ticker.calls[0]["prepost"] is False
    assert ticker.calls[0]["end"] == "2026-10-21"
    assert "Adj Close" in bars.columns


def test_live_request_withholds_current_bar_during_post_close_finalization_buffer():
    ticker = FakeTicker(market_frame(["2026-10-19", "2026-10-20"]))
    provider = YFinanceDailyProvider(
        ticker_factory=lambda _: ticker,
        now_fn=lambda: datetime(2026, 10, 20, 16, 10, tzinfo=NY),
    )
    bars = provider.daily_bars("MU")
    assert bars["Date"].dt.date.tolist() == [date(2026, 10, 19)]


def test_live_request_includes_current_bar_after_finalization_buffer():
    ticker = FakeTicker(market_frame(["2026-10-19", "2026-10-20"]))
    provider = YFinanceDailyProvider(
        ticker_factory=lambda _: ticker,
        now_fn=lambda: datetime(2026, 10, 20, 16, 16, tzinfo=NY),
    )
    bars = provider.daily_bars("MU")
    assert bars["Date"].dt.date.tolist() == [date(2026, 10, 19), date(2026, 10, 20)]


def test_explicit_historical_as_of_includes_session_and_excludes_future_rows():
    ticker = FakeTicker(market_frame(["2026-10-19", "2026-10-20", "2026-10-21"]))
    provider = YFinanceDailyProvider(
        ticker_factory=lambda _: ticker,
        now_fn=lambda: datetime(2026, 10, 22, 11, 30, tzinfo=NY),
    )
    bars = provider.daily_bars("GOOG", as_of_date="2026-10-20")
    assert bars["Date"].dt.date.tolist() == [date(2026, 10, 19), date(2026, 10, 20)]
    assert ticker.calls[0]["end"] == "2026-10-21"


def test_historical_weekend_cutoff_uses_prior_xnys_session():
    ticker = FakeTicker(market_frame(["2026-10-16", "2026-10-19"]))
    provider = YFinanceDailyProvider(
        ticker_factory=lambda _: ticker,
        now_fn=lambda: datetime(2026, 10, 20, 11, 30, tzinfo=NY),
    )
    bars = provider.daily_bars("SPY", as_of_date="2026-10-17")
    assert bars["Date"].dt.date.tolist() == [date(2026, 10, 16)]


def test_explicit_as_of_today_before_finalization_still_excludes_incomplete_bar():
    ticker = FakeTicker(market_frame(["2026-10-19", "2026-10-20"]))
    provider = YFinanceDailyProvider(
        ticker_factory=lambda _: ticker,
        now_fn=lambda: datetime(2026, 10, 20, 16, 10, tzinfo=NY),
    )
    bars = provider.daily_bars("GOOG", as_of_date="2026-10-20")
    assert bars["Date"].dt.date.tolist() == [date(2026, 10, 19)]


def test_provider_rejects_missing_ohlcv_fields_without_guessing():
    frame = market_frame(["2026-10-19"]).drop(columns=["Volume"])
    provider = YFinanceDailyProvider(
        ticker_factory=lambda _: FakeTicker(frame),
        now_fn=lambda: datetime(2026, 10, 20, 17, 0, tzinfo=NY),
    )
    with pytest.raises(MarketDataFetchError, match="Volume"):
        provider.daily_bars("STX")


def test_provider_requires_adjusted_close_for_return_metrics():
    frame = market_frame(["2026-10-20"]).drop(columns=["Adj Close"])
    provider = YFinanceDailyProvider(
        ticker_factory=lambda _: FakeTicker(frame),
        now_fn=lambda: datetime(2026, 10, 20, 16, 16, tzinfo=NY),
    )
    with pytest.raises(MarketDataFetchError, match="Adj Close"):
        provider.daily_bars("STX")


def test_provider_rejects_missing_expected_latest_completed_session():
    ticker = FakeTicker(market_frame(["2026-10-19"]))
    provider = YFinanceDailyProvider(
        ticker_factory=lambda _: ticker,
        now_fn=lambda: datetime(2026, 10, 20, 16, 16, tzinfo=NY),
    )
    with pytest.raises(MarketDataFetchError, match="expected 2026-10-20"):
        provider.daily_bars("STX")


def test_provider_rejects_zero_volume_on_latest_completed_bar():
    frame = market_frame(["2026-10-20"])
    frame.iloc[-1, frame.columns.get_loc("Volume")] = 0
    provider = YFinanceDailyProvider(
        ticker_factory=lambda _: FakeTicker(frame),
        now_fn=lambda: datetime(2026, 10, 20, 16, 16, tzinfo=NY),
    )
    with pytest.raises(MarketDataFetchError, match="zero volume"):
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
        now_fn=lambda: datetime(2026, 10, 20, 16, 16, tzinfo=NY),
    )

    bars = provider.daily_bars("AMAT")

    assert bars["Date"].dt.date.tolist() == [
        date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20)
    ]
    assert list(bars.columns) == ["Date", "Open", "High", "Low", "Close", "Volume", "Adj Close"]
    assert not bars["Date"].duplicated().any()
