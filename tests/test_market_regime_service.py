from __future__ import annotations

import numpy as np
import pandas as pd

from dashboard_v2.services.market_regime_service import (
    GROWTH_LEADERS,
    MARKET_UNIVERSE,
    breadth_from_closes,
    compute_market_regime,
    market_metrics_from_bars,
    regime_label,
    trend_points,
)


def _bars(symbol: str, n: int = 240, slope: float = 0.12) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=n)
    close = 100.0 * (1.0 + slope / 100.0) ** np.arange(n)
    # Keep all prices positive and volume increasing enough to make the frame realistic.
    volume = np.full(n, 1_000_000.0)
    return pd.DataFrame({"Date": dates, "Close": close * 1.03, "Adj Close": close,
                         "Volume": volume, "Open": close, "High": close * 1.01,
                         "Low": close * 0.99})


def test_market_metrics_uses_adj_close_to_match_legacy_regime_basis():
    bars = _bars("QQQ")
    metrics = market_metrics_from_bars(bars)
    assert metrics is not None
    assert metrics["price"] == bars["Adj Close"].iloc[-1]
    assert metrics["data_date"] == bars["Date"].iloc[-1].strftime("%Y-%m-%d")
    assert metrics["above200"] is True
    assert metrics["ma50_above200"] is True


def test_trend_points_matches_legacy_tiers():
    assert trend_points({"price": 110.0, "ma200": 100.0, "ma200_slope": 1.0}, 20) == 20
    assert trend_points({"price": 110.0, "ma200": 100.0, "ma200_slope": 0.0}, 20) == 14
    assert trend_points({"price": 98.0, "ma200": 100.0, "ma200_slope": -1.0}, 20) == 7
    assert trend_points({"price": 95.0, "ma200": 100.0, "ma200_slope": -1.0}, 20) == 0


def test_score_and_label_use_legacy_v25_weights_and_thresholds():
    data = {symbol: market_metrics_from_bars(_bars(symbol))
            for symbol in set(MARKET_UNIVERSE) | set(GROWTH_LEADERS)}
    regime = compute_market_regime(data, broad_breadth=(100.0, 100.0, 500))
    assert regime["available"] is True
    assert regime["score"] == 89
    assert regime["label"] == "🟢 상승장 / 적극"
    assert sum(regime["components"].values()) == regime["score"]
    assert regime["breadth_source"] == "S&P500+Nasdaq100"
    assert regime_label(80) == "🟢 상승장 / 적극"
    assert regime_label(65) == "🟡 상승장 / 주의"
    assert regime_label(45) == "🟠 조정 / 방어"
    assert regime_label(44) == "🔴 하락장 / 신규매수 제한"


def test_missing_core_index_fails_closed():
    data = {"QQQ": market_metrics_from_bars(_bars("QQQ"))}
    result = compute_market_regime(data)
    assert result["available"] is False
    assert result["score"] is None
    assert result["reason"] == "QQQ_OR_SPY_DATA_UNAVAILABLE"


def test_misaligned_qqq_spy_dates_fail_closed():
    q = market_metrics_from_bars(_bars("QQQ"))
    sp = market_metrics_from_bars(_bars("SPY"))
    sp["data_date"] = "2099-01-01"
    result = compute_market_regime({"QQQ": q, "SPY": sp})
    assert result["available"] is False
    assert result["reason"] == "QQQ_SPY_SESSION_MISMATCH"


def test_breadth_from_closes_requires_current_anchor_and_100_valid_names():
    dates = pd.bdate_range("2025-01-02", periods=220)
    values = np.tile(np.linspace(50, 100, len(dates)).reshape(-1, 1), (1, 120))
    closes = pd.DataFrame(values, index=dates, columns=[f"T{i}" for i in range(values.shape[1])])
    result = breadth_from_closes(closes, expected_date=dates[-1].strftime("%Y-%m-%d"))
    assert result is not None
    assert result[2] == 120
    assert result[0] == 100.0
    assert result[1] == 100.0
    assert breadth_from_closes(closes, expected_date="2099-01-01") is None


def test_short_history_is_unavailable_not_scored():
    assert market_metrics_from_bars(_bars("QQQ", n=150)) is None
