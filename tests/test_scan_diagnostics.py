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
    assert '"phase_wall_clock": phase_wall_clock' in app
    assert '"progress_callback_seconds": round(float(progress_callback_seconds), 6)' in app
    assert '"wall_excluding_progress_callback_seconds"' in app
    assert '"replay_pattern_seconds"' in app
    assert '"replay_rebase_seconds"' in app
    assert '_ANALYSIS_CACHE_SCHEMA_VERSION = "phase23-source-profile-v1"' in app
    assert 'benchmark, _ANALYSIS_CACHE_SCHEMA_VERSION)' in app
    assert '"candidate_pivot_source": candidate_source' not in app  # source is carried by the EOD service


def test_diagnostics_do_not_change_eod_engine_entrypoint_or_stage_boundaries():
    app = APP.read_text(encoding="utf-8")
    assert "return analyze_symbol(symbol, bars, seed_snapshot=seed_snapshot, benchmark_bars=benchmark)" in app
    assert 'ThreadPoolExecutor(max_workers=workers, thread_name_prefix="v2-eod-fetch")' in app
    assert 'ThreadPoolExecutor(max_workers=min(4, len(raw)), thread_name_prefix="v2-eod-engine")' in app
    assert '"Chart"' not in app.split("PAGES = [", 1)[1].split("]", 1)[0]
    assert 'elif page == "Chart":' not in app
    assert 'elif page in {"Momentum Monitor", "Pattern Scanner"}:' in app


def test_live_monitor_normalizes_missing_provenance_and_uses_real_pattern_upper():
    app = APP.read_text(encoding="utf-8")
    assert 'def _normalize_pivot_source(value: Any) -> str | None:' in app
    assert '"HISTORICAL_SOURCE_NOT_STORED"' in app
    assert 'display["Candidate Source"] = display["candidate_source"].map(_pivot_source_display)' in app
    assert '피벗 출처 누락 진단' in app
    assert '"pattern_upper": _num_or_none((baseline or {}).get("pattern_upper"))' in app
