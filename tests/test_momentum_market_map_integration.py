from pathlib import Path


def test_momentum_monitor_integrates_watchlist_market_map_and_five_minute_refresh():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert "def _render_momentum_market_map(" in source
    assert "@st.fragment(run_every=\"5m\")" in source
    assert "_render_momentum_market_map(symbols, watchlist_rows)" in source
    assert "result if source == \"Yahoo Finance (EOD)\" else None" in source
    assert "build_market_map_figure(" in source
    assert "Market Map" in source


def test_market_map_does_not_run_from_overview_or_modify_official_eod_engine():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    overview_route = source.split('elif page == "Overview":', 1)[1]
    assert "_render_momentum_market_map(" not in overview_route
    assert "_render_scan_table(result)" in source
    assert 'if page == "Momentum Monitor":' in source
    assert "Market Map" in source
