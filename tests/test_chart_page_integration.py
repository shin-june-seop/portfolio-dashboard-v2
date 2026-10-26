from pathlib import Path


def test_full_chart_is_embedded_at_bottom_of_momentum_monitor_not_sidebar_page():
    app = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    pages = app.split("PAGES = [", 1)[1].split("]", 1)[0]
    assert '"Chart"' not in pages
    assert 'elif page == "Chart":' not in app
    assert "def _render_momentum_chart_section(" in app
    assert "_render_momentum_chart_section(" in app
    assert '"관심종목"' in app
    assert '"직접 입력 (아무 티커)"' in app

    route = app.split('elif page in {"Momentum Monitor", "Pattern Scanner"}:', 1)[1]
    momentum_branch = route.split('else:\n                st.subheader("Pattern Scanner")', 1)[0]
    assert "_render_momentum_chart_section(" in momentum_branch
    technical_detail_index = momentum_branch.index(
        "_render_technical_confirmation_detail(raw, symbols, as_of_date=latest_date)"
    )
    chart_call_positions = [
        index for index in range(len(momentum_branch))
        if momentum_branch.startswith("_render_momentum_chart_section(", index)
    ]
    assert chart_call_positions[-1] > technical_detail_index


def test_chart_uses_official_eod_analysis_outside_intraday_fragment():
    app = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    start = app.index("def _render_momentum_chart_section(")
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
    start = app.index("def _render_momentum_chart_section(")
    end = app.index("def _save_auth_session(", start)
    section = app[start:end]
    assert "v50_resolve_pivot" not in section
    assert "v21_load_snapshots" not in section
    assert ".insert(" not in section
    assert ".update(" not in section


def test_momentum_chart_reuses_scan_bars_and_official_eod_result():
    app = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    start = app.index("def _render_momentum_chart_section(")
    end = app.index("def _save_auth_session(", start)
    section = app[start:end]
    assert "scan_result: pd.DataFrame | None = None" in section
    assert "raw_by_ticker: Mapping[str, pd.DataFrame] | None = None" in section
    assert "precomputed_row = rows_for_ticker.iloc[0].to_dict()" in section
    assert "daily = normalized_raw.get(ticker) if precomputed_row is not None else None" in section
    assert "if precomputed_row is not None:\n                official = precomputed_row" in section
