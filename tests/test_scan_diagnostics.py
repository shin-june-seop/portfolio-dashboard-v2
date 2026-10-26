from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"


def test_scan_collects_per_symbol_download_and_engine_timings():
    app = APP.read_text(encoding="utf-8")
    start = app.index("def _run_scan(")
    end = app.index("def _load_market_regime_bars(", start)
    scan = app[start:end]

    assert "diagnostics_callback: Callable[[dict[str, Any]], None] | None = None" in scan
    assert "download_diagnostics: list[dict[str, Any]]" in scan
    assert "analysis_diagnostics: list[dict[str, Any]]" in scan
    assert "def _timed_download(ticker: str):" in scan
    assert "def _timed_analysis(ticker: str, bars: pd.DataFrame):" in scan
    assert '"소요시간(초)": round(float(seconds), 3)' in scan
    assert '"download": sorted(download_diagnostics' in scan
    assert '"analysis": sorted(analysis_diagnostics' in scan


def test_scan_ui_exposes_detail_tables_for_download_and_engine_timings():
    app = APP.read_text(encoding="utf-8")
    assert 'with st.expander("종목별 상세 성능 진단", expanded=False):' in app
    assert 'st.markdown("**일봉 다운로드 — 느린 순**")' in app
    assert 'st.markdown("**공통 EOD 엔진 호출 — 느린 순**")' in app
    assert "재생 경로가 ROLLING_260_BAR_REPLAY인 종목" in app


def test_diagnostics_do_not_change_eod_engine_entrypoint_or_stage_boundaries():
    app = APP.read_text(encoding="utf-8")
    assert "return analyze_symbol(symbol, bars, seed_snapshot=seed_snapshot, benchmark_bars=benchmark)" in app
    assert 'ThreadPoolExecutor(max_workers=workers, thread_name_prefix="v2-eod-fetch")' in app
    assert 'ThreadPoolExecutor(max_workers=min(4, len(raw)), thread_name_prefix="v2-eod-engine")' in app
    assert '"Chart"' not in app.split("PAGES = [", 1)[1].split("]", 1)[0]
    assert 'elif page == "Chart":' not in app
    assert 'elif page in {"Momentum Monitor", "Pattern Scanner"}:' in app
