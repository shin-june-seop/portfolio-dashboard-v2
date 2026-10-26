from pathlib import Path


def test_chart_is_a_routed_page_with_watchlist_and_direct_ticker_inputs():
    app = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    pages = app.split("PAGES = [", 1)[1].split("]", 1)[0]
    assert '"Chart"' in pages
    assert 'elif page == "Chart":' in app
    assert '_render_chart_page(watchlist_rows, source)' in app
    assert '"관심종목"' in app
    assert '"직접 입력 (아무 티커)"' in app


def test_chart_uses_official_eod_analysis_outside_intraday_fragment():
    app = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    start = app.index("def _render_chart_page(")
    end = app.index("def _save_auth_session(", start)
    section = app[start:end]
    fragment = section.split('@st.fragment(run_every="3m")', 1)[1]
    assert "_analyze_cached(" in section
    assert section.index("_analyze_cached(") < section.index('@st.fragment(run_every="3m")')
    assert "_analyze_cached(" not in fragment
    assert "aggregate_current_session_bar(" in fragment
    assert '"LIVE RVOL"' not in fragment
    assert "run_every=\"3m\"" in section


def test_chart_does_not_use_legacy_pivot_resolver_or_write_persistent_state():
    app = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    start = app.index("def _render_chart_page(")
    end = app.index("def _save_auth_session(", start)
    section = app[start:end]
    assert "v50_resolve_pivot" not in section
    assert "v21_load_snapshots" not in section
    assert ".insert(" not in section
    assert ".update(" not in section
