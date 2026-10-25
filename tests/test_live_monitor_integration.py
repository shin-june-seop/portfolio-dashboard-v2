from pathlib import Path


def test_live_monitor_has_legacy_style_columns_and_v2_live_quality():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    for expected in [
        "def _render_live_monitor_page(",
        "ENTRY STAGE", "LIVE STATE", "LIVE ENTRY QUALITY", "현재가", "장중 등락률",
        "Pattern 상단", "Candidate Pivot", "Candidate Source", "20D Reference",
        "돌파 Pivot", "돌파 Pivot Source", "Next Pivot", "Next Pivot Source",
        "돌파 Pivot 거리", "RVOL AT TIME", "최근 공식 상태", "내 매수 이력", "업데이트",
        "compute_live_entry_quality(", "format_recent_official_state_history(",
        "format_buy_history(", "@st.fragment(run_every=\"30m\")",
    ]:
        assert expected in source, f"missing live monitor integration: {expected}"


def test_live_monitor_keeps_eod_engine_outside_intraday_fragment_and_does_not_write_eod_state():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    start = source.index("def _render_live_monitor_page(")
    end = source.index("def _save_auth_session(", start)
    section = source[start:end]
    fragment = section.split("@st.fragment(run_every=\"30m\")", 1)[1]
    assert '_run_scan(symbols, "Yahoo Finance (EOD)")' in section
    assert "_run_scan(" not in fragment
    assert "insert(" not in fragment.lower()
    assert "upsert(" not in fragment.lower()
    assert "공식 ENTRY STAGE" in section
    assert "완료 일봉 엔진 소유" in section
    assert "stage" not in fragment.lower() or "entry_stage" in fragment.lower()
