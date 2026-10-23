from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from dashboard_v2.services.overview_market_service import (
    INDEX_CONFIGS,
    OverviewMarketDataError,
    build_index_panel,
    latest_completed_xnys_session,
    normalize_completed_daily_bars,
    render_overview_market_panels_html,
)

NY = ZoneInfo("America/New_York")


def _bars(dates, *, base=100.0, volume=1_000_000.0, close_values=None):
    if close_values is None:
        close_values = [base + i for i in range(len(dates))]
    rows = []
    for i, (day, close) in enumerate(zip(dates, close_values)):
        open_ = close - 0.5
        rows.append({
            "Date": pd.Timestamp(day),
            "Open": open_, "High": close + 1.0,
            "Low": open_ - 1.0, "Close": close,
            "Volume": volume * (i + 1),
        })
    return pd.DataFrame(rows).set_index("Date")


def test_latest_completed_session_omits_current_session_before_cutoff():
    # Monday before the close: last completed session is Friday (assuming no holiday).
    now = datetime(2026, 10, 26, 10, 0, tzinfo=NY)
    assert latest_completed_xnys_session(now) == date(2026, 10, 23)


def test_latest_completed_session_allows_today_after_finalization_buffer():
    now = datetime(2026, 10, 23, 16, 20, tzinfo=NY)
    assert latest_completed_xnys_session(now) == date(2026, 10, 23)


def test_normalize_requires_current_expected_session_and_valid_ohlc():
    days = pd.to_datetime(["2026-10-21", "2026-10-22"])
    valid = _bars(days)
    result = normalize_completed_daily_bars(valid, expected_session="2026-10-22")
    assert len(result) == 2
    with pytest.raises(OverviewMarketDataError, match="DATA_STALE"):
        normalize_completed_daily_bars(valid.iloc[:1], expected_session="2026-10-22")


def test_build_index_panel_computes_daily_change_and_proxy_rvol_separately():
    days = pd.bdate_range("2026-09-01", periods=30)
    index_bars = _bars(days, base=7000, close_values=[7000 + 5 * i for i in range(30)])
    proxy_bars = _bars(days, base=500, volume=1000, close_values=[500 + i for i in range(30)])
    config = INDEX_CONFIGS[0]
    result = build_index_panel(index_bars, proxy_bars, config=config, expected_session=days[-1].date())
    assert result["available"] is True
    assert result["label"] == "S&P 500"
    assert result["latest_close"] == pytest.approx(7145.0)
    assert result["change_points"] == pytest.approx(5.0)
    assert result["change_pct"] == pytest.approx(5 / 7140 * 100)
    assert result["rvol_available"] is True
    assert result["candles"][-1]["rvol"] == pytest.approx(30 / np.mean(range(10, 30)))
    # RVOL values are computed from proxy volume, never from index bars.
    assert result["volume_note"].startswith("상대거래량은 SPY")


def test_build_index_panel_fails_closed_for_stale_index_bars():
    days = pd.to_datetime(["2026-10-21", "2026-10-22"])
    index_bars = _bars(days)
    result = build_index_panel(index_bars, None, config=INDEX_CONFIGS[0], expected_session="2026-10-23")
    assert result["available"] is False
    assert "DATA_STALE" in result["error"]


def test_missing_proxy_does_not_hide_valid_index_candles():
    days = pd.bdate_range("2026-10-20", periods=3)
    index_bars = _bars(days, base=7000)
    result = build_index_panel(index_bars, None, config=INDEX_CONFIGS[0], expected_session=days[-1].date())
    assert result["available"] is True
    assert result["rvol_available"] is False
    assert len(result["candles"]) == 2


def test_overview_html_renders_three_cards_and_keeps_market_proxy_label_transparent():
    days = pd.bdate_range("2026-10-20", periods=25)
    panels = []
    for i, config in enumerate(INDEX_CONFIGS):
        prices = [1000 + i * 100 + 2 * j for j in range(len(days))]
        index_bars = _bars(days, close_values=prices)
        proxy_bars = _bars(days, volume=1000)
        panels.append(build_index_panel(index_bars, proxy_bars, config=config, expected_session=days[-1].date()))
    rendered = render_overview_market_panels_html(panels)
    assert rendered.count('<section class="card">') == 3
    assert "S&amp;P 500" in rendered  # ampersand is HTML-escaped in the embedded card
    assert "NASDAQ" in rendered and "DOW" in rendered
    assert "ETF proxy RVOL" in rendered


def test_sidebar_removes_separate_market_regime_page_but_keeps_market_monitor_header():
    import ast
    from pathlib import Path

    source_path = Path(__file__).resolve().parents[1] / "app.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    pages = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "PAGES" for target in node.targets
        ):
            pages = ast.literal_eval(node.value)
            break
    assert pages is not None
    assert "Overview" in pages
    assert "Momentum Monitor" in pages
    assert "Market Regime" not in pages
    assert "_render_momentum_market_header()" in source
    assert 'elif page == "Market Regime"' not in source


def test_overview_route_renders_market_panels_and_explains_market_regime_location():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert 'elif page == "Overview":' in source
    assert "render_overview_market_panels_html(overview_panels)" in source
    assert "Market Score와 Market Regime은 Momentum Monitor 상단" in source
