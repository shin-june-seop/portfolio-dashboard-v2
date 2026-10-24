from pathlib import Path


def test_technical_confirmation_is_rendered_after_momentum_monitor_market_map():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert "def _render_technical_confirmation_detail(" in source
    route = source.split('elif page in {"Momentum Monitor", "Pattern Scanner"}:', 1)[1]
    assert '_render_technical_confirmation_detail(raw, symbols, as_of_date=latest_date)' in route
    assert route.index('_render_momentum_market_map(') < route.index('_render_technical_confirmation_detail(')
    assert 'if source == "Yahoo Finance (EOD)":' in route


def test_technical_confirmation_reads_only_eod_raw_bars_and_never_live_bars():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    start = source.index("def _render_technical_confirmation_detail(")
    end = source.index("def _render_scan_table(", start)
    renderer = source[start:end]
    assert 'calculate_technical_confirmation(bars, as_of_date=as_of_date)' in renderer
    assert "_load_live_intraday_bars" not in renderer
    assert "raw_by_ticker" in renderer
    assert "as_of_date=as_of_date" in renderer


def test_technical_confirmation_service_is_descriptive_only():
    source = (Path(__file__).resolve().parents[1] / "dashboard_v2/services/technical_confirmation_service.py").read_text(encoding="utf-8")
    assert "never changes the canonical V2" in source
    assert "technical_score" in source
    assert "candidate_pivot" not in source
    assert "breakout_pivot" not in source
