from pathlib import Path


def test_momentum_monitor_route_uses_legacy_style_grouped_renderer_and_keeps_market_map():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert "def _render_momentum_monitor_full(" in source
    assert "build_momentum_monitor_frame(" in source
    route = source.split('elif page in {"Momentum Monitor", "Pattern Scanner"}:', 1)[1]
    assert '_render_momentum_monitor_full(result, raw, watchlist_rows)' in route
    assert "_render_momentum_market_map(" in route
    assert "Yahoo Finance (EOD)" in route


def test_full_momentum_renderer_does_not_overwrite_official_eod_engine_or_mix_live_bars():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    start = source.index("def _render_momentum_monitor_full(")
    end = source.index("def _render_scan_table(", start)
    renderer = source[start:end]
    assert "_render_scan_table(scan_result)" in renderer  # fail-closed fallback only
    assert "Entry Quality" in renderer
    assert "Candidate Pivot" in renderer
    assert "RVOL (20D)" in renderer
    assert "pocket_pivot_today" not in renderer  # reads precomputed display row only
    assert "_load_live_intraday_bars" not in renderer
    assert "_render_momentum_market_map" not in renderer  # rendered after the table, not nested within it
