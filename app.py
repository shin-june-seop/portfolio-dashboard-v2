"""Portfolio Dashboard V2 — shared-engine UI milestone.

This app is intentionally read-only. Momentum Monitor and Pattern Scanner consume
one common analysis service; neither screen computes pattern/pivot/stage locally.
"""
from __future__ import annotations

from pathlib import Path
import time
from datetime import datetime
from typing import Any, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components_html

from dashboard_v2.services.screening_service import analyze_symbol, load_seed_snapshots
from dashboard_v2.services.quality_recovery_service import (
    analyze_legacy_quality_recovery,
    fetch_yfinance_fundamentals,
    fundamentals_from_income_statement,
    quality_recovery_table,
)
from dashboard_v2.services.live_monitor_service import (
    NY as LIVE_NY,
    YFinanceIntradayProvider,
    assess_live_data_status,
    classify_live_state,
    crossed_pivot_this_session,
    live_session_metrics,
    present_live_state,
    compute_live_entry_quality,
    format_recent_official_state_history,
    format_buy_history,
)
from dashboard_v2.services.pp_journal_repository import (
    ENTRY_STAGES, EXIT_REASONS, PPJournalValidationError,
    add_pp_trade_add, add_pp_trade_exit, create_pp_trade,
    list_pp_signals, list_pp_trades, list_trade_adds, list_trade_exits, save_pp_signals,
)
from dashboard_v2.services.pp_journal_analytics import (
    analyze_pp_signal_outcome, summarize_trade, summarize_trades,
)
from dashboard_v2.services.market_regime_service import (
    MARKET_SYMBOLS,
    breadth_from_closes,
    compute_market_regime,
    market_metrics_from_bars,
)
from dashboard_v2.services.overview_intraday_service import (
    INDEX_INTRADAY_CONFIGS,
    build_intraday_index_figure,
    build_intraday_index_panel,
    fetch_intraday_index_bars,
    render_intraday_index_header_html,
)
from dashboard_v2.services.momentum_market_map_service import (
    build_market_map_figure,
    fetch_market_map_data,
    market_map_status_caption,
)
from dashboard_v2.services.technical_confirmation_service import (
    calculate_technical_confirmation, korean_ma_status, korean_technical_status, korean_trend_status,
)
from dashboard_v2.services.momentum_monitor_service import (
    FALLBACK_MOMENTUM_UNIVERSE, LEGACY_SECTOR_OPTIONS,
    build_momentum_monitor_frame, sector_leadership_reason,
    sector_leadership_table_html, trend_template_strip_html,
)
from dashboard_v2.services.watchlist_repository import (
    active_tickers,
    add_watchlist_item,
    create_supabase_client,
    delete_watchlist_item,
    list_watchlist,
    update_watchlist_item,
    WatchlistValidationError,
)

ROOT = Path(__file__).resolve().parent
PAGES = [
    "Watchlist", "Momentum Monitor", "Live Monitor", "Quality Recovery", "Pattern Scanner", "PP Journal",
    "Overview", "Holdings",
    "Performance", "Allocation", "Chart", "Leaders",
]

st.set_page_config(page_title="Portfolio Dashboard V2", page_icon="📊", layout="wide")


@st.cache_data(ttl=900, show_spinner=False)
def _load_yfinance_bars(symbol: str) -> pd.DataFrame:
    from dashboard_v2.services.yfinance_provider import YFinanceDailyProvider
    return YFinanceDailyProvider(history_days=5 * 365).daily_bars(symbol)


@st.cache_data(ttl=60 * 60, show_spinner=False)
def _load_adjusted_close_history(symbol: str) -> pd.DataFrame:
    """Load dividend/split-adjusted daily closes for legacy V30.5 RS only.

    These values are never passed into the technical moving-average or pivot
    calculations. The same completed-US-session cutoff as the V2 EOD layer is
    applied before returning them.
    """
    import yfinance as yf

    ticker = yf.Ticker(str(symbol).upper().strip())
    raw = ticker.history(period="2y", interval="1d", auto_adjust=True, actions=False, timeout=15)
    if raw is None or raw.empty or "Close" not in raw.columns:
        return pd.DataFrame(columns=["Date", "Close"])
    idx = pd.DatetimeIndex(pd.to_datetime(raw.index))
    idx_et = idx.tz_convert("America/New_York") if idx.tz is not None else idx
    now_et = pd.Timestamp.now(tz="America/New_York")
    row_dates = [stamp.date() for stamp in idx_et]
    cutoff_time = pd.Timestamp("16:15").time()
    if now_et.time().replace(tzinfo=None) < cutoff_time:
        keep = [day < now_et.date() for day in row_dates]
    else:
        keep = [day <= now_et.date() for day in row_dates]
    values = pd.to_numeric(raw["Close"], errors="coerce")
    out = pd.DataFrame({"Date": pd.to_datetime(row_dates), "Close": values.to_numpy()})
    out = out.loc[keep].dropna(subset=["Date", "Close"]).copy()
    out = out.drop_duplicates("Date", keep="last").sort_values("Date").reset_index(drop=True)
    return out


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def _load_quality_fundamentals(symbol: str) -> dict[str, Any]:
    """Legacy-compatible annual Yahoo income-statement gate; no SEC dependency."""
    return fetch_yfinance_fundamentals(symbol)


@st.cache_data(ttl=60, show_spinner=False)
def _load_live_intraday_bars(symbol: str) -> pd.DataFrame:
    """Provisional 5-minute bars for Live Monitor only; never pass these to EOD engines."""
    return YFinanceIntradayProvider().intraday_bars(symbol)


@st.cache_data(ttl=900, show_spinner=False)
def _load_regression_bars(symbol: str) -> pd.DataFrame:
    path = ROOT / "data" / "regression" / "ohlcv" / f"{symbol.upper()}.csv"
    if not path.exists():
        raise FileNotFoundError(f"No bundled regression OHLCV for {symbol}")
    return pd.read_csv(path)


@st.cache_data(ttl=900, show_spinner=False)
def _load_seed_state() -> dict:
    return load_seed_snapshots()


@st.cache_data(ttl=900, show_spinner=False)
def _analyze_cached(symbol: str, bars: pd.DataFrame, seed_snapshot: dict | None, benchmark: pd.DataFrame | None) -> dict:
    return analyze_symbol(symbol, bars, seed_snapshot=seed_snapshot, benchmark_bars=benchmark)


def _load_bars(symbol: str, source: str) -> pd.DataFrame:
    if source == "Yahoo Finance (EOD)":
        return _load_yfinance_bars(symbol)
    if source == "Bundled regression data (historical)":
        return _load_regression_bars(symbol)
    raise RuntimeError("Select a data source to load market data")


def _run_scan(symbols: list[str], source: str) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], list[tuple[str, str]], float]:
    start = time.perf_counter()
    seed = _load_seed_state()
    raw: dict[str, pd.DataFrame] = {}
    errors: list[tuple[str, str]] = []
    for symbol in symbols:
        try:
            raw[symbol] = _load_bars(symbol, source)
        except Exception as exc:
            errors.append((symbol, f"{type(exc).__name__}: {exc}"))

    benchmark = None
    if source == "Yahoo Finance (EOD)":
        try:
            benchmark = _load_yfinance_bars("SPY")
        except Exception as exc:
            errors.append(("SPY benchmark", f"{type(exc).__name__}: {exc}"))

    rows = []
    for symbol, bars in raw.items():
        try:
            rows.append(_analyze_cached(symbol, bars, seed.get(symbol), benchmark))
        except Exception as exc:
            errors.append((symbol, f"Analysis {type(exc).__name__}: {exc}"))
    elapsed = time.perf_counter() - start
    return pd.DataFrame(rows), raw, errors, elapsed


@st.cache_data(ttl=60 * 60, show_spinner=False)
def _load_market_regime_bars(symbol: str) -> pd.DataFrame:
    """Load complete daily market bars for broad-market regime only."""
    from dashboard_v2.services.yfinance_provider import YFinanceDailyProvider
    return YFinanceDailyProvider(history_days=5 * 365).daily_bars(symbol)




@st.cache_data(ttl=180, show_spinner=False)
def _load_overview_intraday_bars(symbol: str) -> tuple[pd.DataFrame, str, str]:
    """Cached legacy-style 5-minute index feed, separate from every EOD engine."""
    return fetch_intraday_index_bars(symbol)


@st.fragment(run_every="5m")
def _render_overview_intraday_cards() -> None:
    """Render the legacy V50.53 3-card intraday index overview."""
    st.markdown("---")
    columns = st.columns(3, gap="large")
    for column, config in zip(columns, INDEX_INTRADAY_CONFIGS):
        label = config["label"]
        symbol = config["symbol"]
        with column:
            raw, method, fetch_error = _load_overview_intraday_bars(symbol)
            panel = build_intraday_index_panel(
                raw, label=label, symbol=symbol, fetch_error=fetch_error
            )
            panel["method"] = method
            if not panel.get("available"):
                st.markdown(
                    f"<div style='background:#20242b;border:1px solid #46505f;border-radius:8px;"
                    f"padding:12px;color:#9aa3b2'><b>{label}</b><br>"
                    f"장중 데이터 없음 · {panel.get('error', 'DATA_UNAVAILABLE')}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                continue
            st.markdown(render_intraday_index_header_html(panel), unsafe_allow_html=True)
            figure = build_intraday_index_figure(panel)
            st.plotly_chart(
                figure,
                use_container_width=True,
                config={"displayModeBar": False, "responsive": True},
                key=f"v2_overview_index_{symbol}",
            )
    st.caption(
        "S&P 500 · NASDAQ · DOW: Yahoo Finance 5분봉 기반, 전일 정규장 종가 대비 변화. "
        "카드는 5분마다 갱신됩니다. 상대거래량은 각 지수에 공급된 장중 거래량을 과거 동일 시간대와 비교한 보조 지표이며, "
        "데이터가 없으면 임의의 RVOL을 표시하지 않습니다. 이 화면은 읽기 전용이며 공식 EOD 패턴·피봇·Stage를 변경하지 않습니다."
    )


@st.cache_data(ttl=120, show_spinner=False)
def _load_momentum_market_map_quotes(
    tickers: tuple[str, ...], sectors: tuple[tuple[str, str], ...]
) -> pd.DataFrame:
    """Cached 5-minute/EOD quote layer; independent of the official EOD scan."""
    return fetch_market_map_data(tickers, sector_by_ticker=dict(sectors))


def _render_momentum_market_map(
    symbols: list[str],
    watchlist_rows: list[dict[str, Any]],
    scan_result: pd.DataFrame | None = None,
) -> None:
    """Render the legacy V50.53 watchlist Market Map without touching EOD state."""
    st.markdown("### 📊 관심종목 Market Map")
    st.caption("5분 자동 갱신 · 장중 = 현재가/전일 종가 · 장외 = 확정 종가/전일 종가 · 색 = 1D 수익률")
    if not symbols:
        st.info("활성 관심종목이 없습니다. Watchlist에서 종목을 추가하거나 활성화해 주세요.")
        return

    sector_by_ticker = {
        str(row.get("ticker", "")).upper().strip(): str(row.get("sector") or "미분류")
        for row in (watchlist_rows or []) if str(row.get("ticker", "")).strip()
    }
    sectors = tuple(sorted(sector_by_ticker.items()))
    stage_by_ticker: dict[str, str] = {}
    # Historical regression data must not be mixed with a current live market map.
    if isinstance(scan_result, pd.DataFrame) and not scan_result.empty and {"ticker", "stage"}.issubset(scan_result.columns):
        stage_by_ticker = {
            str(row["ticker"]).upper().strip(): (
                str(row["stage"]) if pd.notna(row["stage"]) and str(row["stage"]).strip() else "-"
            )
            for row in scan_result[["ticker", "stage"]].to_dict("records")
        }

    @st.fragment(run_every="5m")
    def _market_map_fragment() -> None:
        with st.spinner("관심종목 Market Map 업데이트 중…"):
            quotes = _load_momentum_market_map_quotes(tuple(sorted(set(symbols))), sectors)
        if quotes is None or quotes.empty:
            st.warning("Market Map 가격 데이터를 계산하지 못했습니다. 잠시 후 다시 갱신하세요.")
            return
        figure = build_market_map_figure(
            quotes, stage_by_ticker=stage_by_ticker, sector_by_ticker=sector_by_ticker
        )
        st.plotly_chart(
            figure, use_container_width=True,
            config={"displayModeBar": False, "responsive": True},
            key="v2_momentum_market_map_chart",
        )
        st.caption(market_map_status_caption(quotes))
        unavailable = sorted(set(symbols) - set(quotes["ticker"].astype(str).str.upper()))
        if unavailable:
            st.caption("현재 시세를 확보하지 못해 맵에서 제외된 종목: " + ", ".join(unavailable))
        if not stage_by_ticker:
            st.caption("Entry Stage는 현재 EOD 스캔 결과가 있을 때 표시합니다. 과거 번들 데이터의 Stage는 현재 시세 지도에 섞지 않습니다.")

    _market_map_fragment()


@st.cache_data(ttl=12 * 60 * 60, show_spinner=False)
def _load_market_constituent_tickers() -> tuple[str, ...]:
    """Load S&P 500 + Nasdaq-100 members; ETF breadth is the safe fallback."""
    from io import BytesIO
    from urllib.request import Request, urlopen
    sources = [
        ("sp500", "https://raw.githubusercontent.com/Ate329/top-us-stock-tickers/main/tickers/sp500.csv"),
        ("sp500", "https://yfiua.github.io/index-constituents/constituents-sp500.csv"),
        ("nasdaq100", "https://yfiua.github.io/index-constituents/constituents-nasdaq100.csv"),
    ]
    chosen: dict[str, tuple[str, ...]] = {}
    for kind, url in sources:
        if kind in chosen:
            continue
        try:
            req = Request(url, headers={"User-Agent": "PortfolioDashboardV2 market breadth"})
            with urlopen(req, timeout=8) as response:
                payload = response.read()
            frame = pd.read_csv(BytesIO(payload))
            columns = {str(c).strip().lower(): c for c in frame.columns}
            ticker_col = next((columns[k] for k in ("symbol", "ticker", "ticker symbol", "ticker_symbol") if k in columns), None)
            if ticker_col is None:
                continue
            values = (frame[ticker_col].astype(str).str.replace(".", "-", regex=False)
                      .str.upper().str.strip())
            values = values[values.str.match(r"^[A-Z][A-Z0-9-]{0,7}$", na=False)]
            tickers = tuple(sorted(set(values.tolist())))
            if (kind == "sp500" and len(tickers) >= 300) or (kind == "nasdaq100" and len(tickers) >= 70):
                chosen[kind] = tickers
        except Exception:
            continue
    # The broad-breadth series is only labelled S&P500 + Nasdaq-100 when both
    # member lists loaded. If either is unavailable, use the legacy ETF proxy.
    if not {"sp500", "nasdaq100"}.issubset(chosen):
        return tuple()
    return tuple(sorted(set().union(*[set(v) for v in chosen.values()])))


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def _load_broad_breadth_closes(anchor_date: str, tickers: tuple[str, ...]) -> pd.DataFrame:
    """Download adjusted closes for index breadth in cached, resilient chunks."""
    import yfinance as yf
    if not tickers:
        return pd.DataFrame()
    anchor = pd.Timestamp(anchor_date).date()
    start = (pd.Timestamp(anchor) - pd.Timedelta(days=820)).date().isoformat()
    end = (pd.Timestamp(anchor) + pd.Timedelta(days=1)).date().isoformat()
    frames = []
    for start_i in range(0, len(tickers), 40):
        chunk = list(tickers[start_i:start_i + 40])
        try:
            data = yf.download(chunk, start=start, end=end, interval="1d", auto_adjust=True,
                               progress=False, threads=False, group_by="column")
            if data is None or data.empty:
                continue
            if isinstance(data.columns, pd.MultiIndex):
                level0 = [str(x) for x in data.columns.get_level_values(0)]
                level1 = [str(x) for x in data.columns.get_level_values(1)]
                if "Close" in level0:
                    closes = data.xs("Close", axis=1, level=0).copy()
                elif "Close" in level1:
                    closes = data.xs("Close", axis=1, level=1).copy()
                else:
                    continue
            elif "Close" in data.columns and len(chunk) == 1:
                closes = data[["Close"]].copy()
                closes.columns = [chunk[0]]
            else:
                continue
            idx = pd.to_datetime(closes.index, errors="coerce")
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_convert("America/New_York").tz_localize(None)
            closes.index = pd.DatetimeIndex(idx).normalize()
            closes = closes.loc[~closes.index.isna()]
            closes = closes.loc[closes.index.date <= anchor]
            closes.columns = [str(c).upper().strip() for c in closes.columns]
            closes = closes.loc[:, ~closes.columns.duplicated()].copy()
            if not closes.empty:
                frames.append(closes)
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1)
    out = out.loc[:, ~out.columns.duplicated()].sort_index()
    out = out.loc[:, ~out.columns.duplicated()].copy()
    # A stale broad-universe frame must not silently pass as today's market breadth.
    if out.empty or pd.Timestamp(out.index[-1]).date() != anchor:
        return pd.DataFrame()
    return out


def _compute_market_regime() -> dict[str, Any]:
    """Compute the legacy V25 score using only completed US-session bars."""
    market_bars: dict[str, pd.DataFrame] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_load_market_regime_bars, symbol): symbol for symbol in MARKET_SYMBOLS}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                market_bars[symbol] = future.result()
            except Exception as exc:
                errors.append(f"{symbol}: {type(exc).__name__}")
    metrics = {symbol: market_metrics_from_bars(bars) for symbol, bars in market_bars.items()}
    metrics = {symbol: metric for symbol, metric in metrics.items() if metric is not None}
    # Keep the same fallback as the legacy dashboard: broad S&P500 + Nasdaq-100
    # breadth when complete, otherwise the 13-ETF market participation proxy.
    qqq = metrics.get("QQQ")
    spy = metrics.get("SPY")
    broad = None
    if qqq and spy and qqq.get("data_date") == spy.get("data_date"):
        constituents = _load_market_constituent_tickers()
        if constituents:
            closes = _load_broad_breadth_closes(str(qqq["data_date"]), constituents)
            broad = breadth_from_closes(closes, expected_date=str(qqq["data_date"]))
    result = compute_market_regime(metrics, broad_breadth=broad)
    result["market_data_errors"] = errors
    return result


def _render_momentum_market_header() -> None:
    """Render the legacy-style Market Score / Market Regime summary only."""
    st.markdown("## 🔭 Momentum Monitor")
    with st.spinner("완료된 미국 시장 데이터로 Market Score와 Regime을 계산하는 중…"):
        regime = _compute_market_regime()
    if regime.get("available") and regime.get("data_date"):
        st.session_state["v2_market_regime_snapshot"] = {
            "data_date": str(regime.get("data_date")), "label": str(regime.get("label") or "—")
        }
    if not regime.get("available"):
        st.warning(str(regime.get("action") or "시장 레짐 데이터를 사용할 수 없습니다."))
        with st.expander("시장 데이터 진단"):
            st.write(str(regime.get("reason") or "MARKET_DATA_UNAVAILABLE"))
            errs = regime.get("market_data_errors") or []
            if errs:
                st.write(" · ".join(errs))
        return

    score = int(regime["score"])
    c_score, c_regime, c_action = st.columns([1.0, 1.9, 3.1])
    c_score.metric("Market Score", f"{score}/100")
    label = str(regime.get("label") or "⚪ 시장 데이터 부족")
    c_regime.markdown(
        f"<div style='padding-top:2px;'><div style='font-size:14px;color:#8b9bb4;margin-bottom:4px;'>Market Regime</div>"
        f"<div style='font-size:27px;line-height:1.15;font-weight:600;white-space:nowrap;'>{label}</div></div>",
        unsafe_allow_html=True,
    )
    action = str(regime.get("action") or "")
    if score < 65:
        c_action.warning("⚠️ " + action)
    else:
        c_action.info("🌐 " + action)
    st.progress(score / 100.0)
    st.caption(
        f"기준일: {regime.get('data_date', '—')} · 완료된 미국 정규장 일봉 기준 · "
        f"시장 참여도: {regime.get('breadth_source', '데이터 부족')} · "
        "Market Regime은 개별 종목의 공식 Entry Stage를 변경하지 않습니다."
    )
    with st.expander("Market Score 구성 근거"):
        components = regime.get("components") or {}
        maxima = {"QQQ 장기추세": 20, "S&P500 장기추세": 15, "50/200MA 구조": 15,
                  "시장 참여도": 20, "분산매도": 15, "성장 리더십": 15}
        cols = st.columns(6)
        for col, (name, value) in zip(cols, components.items()):
            col.metric(name, f"{value}/{maxima.get(name, 20)}")
        q = regime.get("metrics", {}).get("QQQ", {})
        spy = regime.get("metrics", {}).get("SPY", {})
        a, b, c, d = st.columns(4)
        a.metric("QQQ vs 200MA", f"{q.get('price_vs_200_pct', np.nan):+.2f}%" if q else "—")
        b.metric("S&P500 vs 200MA", f"{spy.get('price_vs_200_pct', np.nan):+.2f}%" if spy else "—")
        b50, b200 = regime.get("breadth_above50_pct"), regime.get("breadth_above200_pct")
        c.metric("50MA 위 시장", f"{b50:.0f}%" if pd.notna(b50) else "—")
        d.metric("200MA 위 시장", f"{b200:.0f}%" if pd.notna(b200) else "—")
        e, f, g = st.columns(3)
        e.metric("QQQ 분산매도(25일)", int(regime.get("distribution_qqq", 0)))
        f.metric("SPY 분산매도(25일)", int(regime.get("distribution_spy", 0)))
        leader = regime.get("leader_above50_pct")
        leader_rel = regime.get("leader_relative_3m_pct")
        g.metric("성장 리더십", f"{leader:.0f}% · RS {leader_rel:+.1f}%p" if pd.notna(leader) and pd.notna(leader_rel) else "데이터 부족")


def _fmt_num(value, digits: int = 2):
    return "—" if value is None or pd.isna(value) else f"{float(value):,.{digits}f}"


def _render_momentum_monitor_full(
    scan_result: pd.DataFrame,
    raw_by_ticker: Mapping[str, pd.DataFrame],
    watchlist_rows: list[dict[str, Any]],
) -> None:
    """Legacy V50.53-style sector leadership and per-sector Momentum tables.

    All official EOD fields come from the V2 chronological scan. This layer adds
    legacy-compatible cross-sectional Momentum, RS, and explanatory columns only.
    """
    if scan_result is None or scan_result.empty:
        st.info("Momentum Monitor 결과가 없습니다.")
        return

    try:
        anchor = pd.to_datetime(scan_result["as_of_date"], errors="coerce").dropna().max()
        if pd.isna(anchor):
            raise ValueError("NO_EOD_ANCHOR_DATE")
        anchor_date = anchor.date().isoformat()
    except Exception:
        st.warning("완료 일봉 기준일을 확인할 수 없어 Momentum/RS/섹터 점수를 계산하지 않습니다.")
        _render_scan_table(scan_result)
        return

    benchmark_tickers = tuple(_load_market_constituent_tickers() or ())
    universe_label = "S&P 500 + Nasdaq-100 구성 종목"
    if not benchmark_tickers:
        benchmark_tickers = FALLBACK_MOMENTUM_UNIVERSE
        universe_label = "기존 ETF 대체 바스켓 (구성 종목 데이터 부족)"

    benchmark_closes = _load_broad_breadth_closes(anchor_date, tuple(benchmark_tickers))
    # The legacy formula requires a full year of aligned completed adjusted-close
    # data. Retry with the explicit ETF fallback if constituent history is partial.
    if (benchmark_closes is None or benchmark_closes.empty or len(benchmark_closes) < 253) and benchmark_tickers != FALLBACK_MOMENTUM_UNIVERSE:
        benchmark_tickers = FALLBACK_MOMENTUM_UNIVERSE
        universe_label = "기존 ETF 대체 바스켓 (구성 종목 데이터 부족)"
        benchmark_closes = _load_broad_breadth_closes(anchor_date, tuple(benchmark_tickers))

    qqq_history = None
    try:
        qqq_history = _load_yfinance_bars("QQQ")
    except Exception:
        qqq_history = None

    with st.spinner("기존 방식의 Momentum·RS·Sector Leadership을 계산하는 중…"):
        monitor, leadership = build_momentum_monitor_frame(
            scan_result, raw_by_ticker, watchlist_rows,
            benchmark_closes, benchmark_tickers, qqq_history,
        )

    if monitor.empty:
        st.warning("모니터 표시값을 구성하지 못했습니다. 공식 EOD 결과는 아래 표에서 확인할 수 있습니다.")
        _render_scan_table(scan_result)
        return

    st.markdown("### 🏆 Sector Leadership — 섹터 리더십")
    st.caption(
        "활성 관심종목을 섹터 단위로 묶어 RS·Momentum·최근 변화·Breakout Readiness·강한 종목 비율을 종합합니다. "
        "한 종목의 급등보다 섹터 전체 참여를 중요하게 봅니다. Readiness의 대체 20일 기준 고점은 참고용이며 공식 Candidate Pivot이 아닙니다."
    )
    if benchmark_closes is None or benchmark_closes.empty or len(benchmark_closes) < 253 or pd.Timestamp(benchmark_closes.index[-1]).date().isoformat() != anchor_date:
        st.warning("Momentum 순위용 adjusted-close 벤치마크가 기준일과 일치하지 않거나 253개 완료 일봉이 부족합니다. 해당 Momentum 점수는 비워 둡니다.")
    else:
        st.caption(f"Momentum 기준: {universe_label} · 조정종가 기준 1/3/6/12개월 수익률 백분위 40/30/20/10 · 기준일 {anchor_date}")
    st.caption("RS는 QQQ 대비 21/63/126/252 거래일(15/35/25/25 가중치), 변화는 20거래일 전 RS 대비입니다.")

    if leadership is None or leadership.empty:
        st.info("섹터 리더십을 계산할 수 있는 유효한 RS / Momentum / Readiness 자료가 없습니다.")
    else:
        sector_html = sector_leadership_table_html(leadership)
        components_html_height = max(120, 55 + 37 * len(leadership))
        components_html_height = min(850, components_html_height)
        components_html.html(sector_html, height=components_html_height, scrolling=False)
        top = leadership.iloc[0].to_dict()
        st.caption("🥇 섹터 리더: " + str(top.get("섹터", "—")) + " · " + sector_leadership_reason(top))

    st.markdown("### 📋 오늘의 Momentum 상태")
    st.caption("RS Engine V30.5 호환 · Momentum은 횡단면 백분위 · 초기 위험도와 Turtle 2N은 참고 지표이며 Entry Quality 계산에 포함하지 않습니다.")
    st.caption("공식 Pattern / Candidate Pivot / Breakout Pivot / Rebase / EOD Stage / RVOL은 V2 chronological engine 값입니다. Breakout Readiness와 Base는 참고용이며 공식 Stage나 Pivot을 생성·수정하지 않습니다.")
    st.caption("RVOL은 V2 규칙에 따라 현재 완료 일봉 거래량 ÷ 직전 20개 완료 일봉 평균으로 표시합니다. 기존 화면의 RVOL (50D) 표기로 바꾸지 않습니다.")

    preferred_columns = [
        "종목", "현재가", "Entry Stage", "Entry Quality", "Initial Risk", "Turtle 2N Risk", "Action",
        "Momentum", "Momentum 변화", "RS", "RS 변화", "Base", "Pattern", "Pattern 상단",
        "Candidate Pivot", "Candidate Pivot Source", "VCP Volume", "PP Guide 10D", "PP Guide 50D",
        "PP Confluence", "PP Status", "Trend Template", "돌파 Pivot", "Pivot Source", "Next Pivot",
        "돌파 Pivot 거리", "RVOL (20D)", "Breakout Readiness", "돌파 준비도", "MA", "RSI", "MACD", "VFI",
        "BB Width", "Tech", "변화",
    ]
    present_columns = [c for c in preferred_columns if c in monitor.columns]
    ordered_sectors = [s for s in LEGACY_SECTOR_OPTIONS if s in set(monitor["섹터"].astype(str))]
    custom_sectors = sorted(set(monitor["섹터"].astype(str)) - set(LEGACY_SECTOR_OPTIONS))
    ordered_sectors.extend(custom_sectors)
    for sector in ordered_sectors:
        part = monitor[monitor["섹터"].astype(str) == sector].copy().sort_values("종목", kind="stable")
        if part.empty:
            continue
        st.markdown(f"#### {sector} · {len(part)}개")
        st.markdown(trend_template_strip_html(part), unsafe_allow_html=True)
        st.dataframe(
            part[present_columns], use_container_width=True, hide_index=True,
            height=min(580, max(100, 38 + 35 * len(part))),
            key="v2_momentum_sector_" + "_".join(ch for ch in sector.lower().replace("/", " ").split() if ch.isalnum()),
        )

    options = monitor["종목"].astype(str).tolist()
    if options:
        with st.expander("🔎 선택 종목 상세 및 판정 근거", expanded=False):
            selected = st.selectbox("종목", options, key="v2_momentum_selected_ticker")
            row = monitor.loc[monitor["종목"].astype(str) == selected].iloc[0]
            a, b, c, d = st.columns(4)
            a.metric("현재가", row.get("현재가", "—"))
            b.metric("Entry Stage", row.get("Entry Stage", "—"))
            c.metric("Entry Quality", row.get("Entry Quality", "—"))
            d.metric("Momentum / RS", f"{row['Momentum']:.1f} / {row['RS']:.0f}" if pd.notna(row.get("Momentum")) and pd.notna(row.get("RS")) else "—")
            st.write("**공식 패턴:** " + str(row.get("Pattern", "—")))
            st.write("**Candidate Pivot / Source:** " + str(row.get("Candidate Pivot", "—")) + " · " + str(row.get("Candidate Pivot Source", "—")))
            st.write("**Breakout Pivot / Source:** " + str(row.get("돌파 Pivot", "—")) + " · " + str(row.get("Pivot Source", "—")))
            st.write("**Entry Quality 구성:** " + str(row.get("Entry Quality Detail", "—")))
            st.write("**Trend Template 미달:** " + str(row.get("Trend Template 미달", "—")))
            st.write("**Readiness 참고 기준:** " + str(row.get("_readiness_reference", "—")) + " · 거리 " + (f"{float(row['_readiness_distance']):+.2f}%" if pd.notna(row.get("_readiness_distance")) else "—"))



def _render_technical_confirmation_detail(
    raw_by_ticker: Mapping[str, pd.DataFrame],
    tickers: list[str],
    *,
    as_of_date: Any = None,
) -> None:
    """Render the legacy V50.53 Technical Confirmation detail below Momentum Monitor.

    This uses only the same completed daily OHLCV frame already consumed by the
    official V2 EOD scan. It is a descriptive confirmation layer and cannot
    modify official pattern, pivot, rebase, RVOL, or Entry Stage outputs.
    """
    st.markdown("---")
    st.markdown("## 🧭 Technical Confirmation 상세")
    st.caption(
        "기존 대시보드 V22/V24 계산 방식 · 완료된 EOD 일봉 기준 · "
        "기술적 확인 전용이며 공식 Pattern / Pivot / Entry Stage를 변경하지 않습니다."
    )
    options = sorted({str(t).strip().upper() for t in (tickers or []) if str(t).strip()})
    if not options:
        st.info("기술적 확인을 표시할 활성 관심종목이 없습니다.")
        return

    selected_tech = st.selectbox("기술적 확인 종목", options, key="v2_technical_confirmation_ticker")
    bars = (raw_by_ticker or {}).get(selected_tech)
    tr = calculate_technical_confirmation(bars, as_of_date=as_of_date)
    if tr.get("data_status") != "OK":
        st.warning(
            f"{selected_tech}: 기술 지표를 계산할 완료 일봉이 부족합니다. "
            "유효한 일봉이 최소 60개 필요하며 데이터가 부족하면 임의 값을 표시하지 않습니다."
        )
        return

    first = st.columns(5)
    first[0].metric("현재가", f"${tr['price']:,.2f}" if pd.notna(tr.get("price")) else "—")
    first[1].metric("20일선", f"${tr['ma20']:,.2f}" if pd.notna(tr.get("ma20")) else "—")
    first[2].metric("50일선", f"${tr['ma50']:,.2f}" if pd.notna(tr.get("ma50")) else "—")
    first[3].metric("주가 vs 20MA", f"{tr['price_vs_ma20_pct']:+.2f}%" if pd.notna(tr.get("price_vs_ma20_pct")) else "—")
    first[4].metric("주가 vs 50MA", f"{tr['price_vs_ma50_pct']:+.2f}%" if pd.notna(tr.get("price_vs_ma50_pct")) else "—")

    second = st.columns(4)
    second[0].metric("20MA vs 50MA", f"{tr['ma20_vs_ma50_pct']:+.2f}%" if pd.notna(tr.get("ma20_vs_ma50_pct")) else "—")
    second[1].metric("MA Spread 변화", f"{tr['ma_spread_delta_pct']:+.2f}%p" if pd.notna(tr.get("ma_spread_delta_pct")) else "—")
    second[2].metric("RSI(14)", f"{tr['rsi']:.1f}" if pd.notna(tr.get("rsi")) else "—")
    second[3].metric("기술 확인 점수", korean_technical_status(tr.get("technical_status", "⚪ N/A")))

    if all(pd.notna(tr.get(key)) for key in ("macd_hist", "vfi", "bb_width")):
        st.caption(
            f"MA 상태: {korean_ma_status(tr.get('ma_status', '⚪ N/A'))} · "
            f"MACD Hist {tr['macd_hist']:+.2f} · VFI {tr['vfi']:+.1f} · "
            f"BB Width {tr['bb_width']:.2f}% · 기준일 {tr.get('as_of_date', '—')}"
        )
    else:
        st.caption("기술 지표 데이터가 아직 충분하지 않습니다. 표시 가능한 지표만 해석하세요.")

    st.markdown("**방향성 판단**")
    direction_cols = st.columns(5)
    direction_cols[0].metric("MA", korean_ma_status(tr.get("ma_status", "⚪ N/A")))
    direction_cols[1].metric("RSI", korean_trend_status(tr.get("rsi_trend", "⚪ N/A")))
    direction_cols[2].metric("MACD", korean_trend_status(tr.get("macd_hist_trend", "⚪ N/A")))
    direction_cols[3].metric("VFI", korean_trend_status(tr.get("vfi_trend", "⚪ N/A")))
    direction_cols[4].metric("BB", korean_trend_status(tr.get("bb_trend", "⚪ N/A")))
    reasons = tr.get("technical_reasons") or []
    if reasons:
        st.caption("판정 근거: " + " · ".join(str(reason) for reason in reasons))
    st.caption("VFI는 기존 대시보드의 VFI-style 거래량·가격 흐름 근사치이며, 별도의 공식 매수 신호가 아닙니다.")


def _render_scan_table(frame: pd.DataFrame, *, pattern_mode: bool = False):
    if frame.empty:
        st.info("표시할 종목 분석 결과가 없습니다.")
        return
    view = frame.copy()
    view["재베이스"] = view["rebase_active"].map({True: "진행 중", False: "아님"})
    view["가격"] = view["close"].map(lambda x: _fmt_num(x))
    view["일간 %"] = view["day_change_pct"].map(lambda x: "—" if pd.isna(x) else f"{x:+.2f}%")
    view["후보 피봇"] = view["candidate_pivot"].map(_fmt_num)
    view["후보 거리"] = view["candidate_distance_pct"].map(lambda x: "—" if pd.isna(x) else f"{x:+.2f}%")
    view["돌파 피봇"] = view["breakout_pivot"].map(_fmt_num)
    view["돌파 거리"] = view["breakout_distance_pct"].map(lambda x: "—" if pd.isna(x) else f"{x:+.2f}%")
    view["20D Ref"] = view["twenty_day_reference"].map(_fmt_num)
    view["RVOL"] = view["rvol"].map(lambda x: "—" if pd.isna(x) else f"{x:.2f}×")
    view["3개월 수익률"] = view["return_3m_pct"].map(lambda x: "—" if pd.isna(x) else f"{x:+.2f}%")
    view["SPY 대비 RS(3M)"] = view["rs_3m_vs_spy_pct"].map(lambda x: "—" if pd.isna(x) else f"{x:+.2f}%p")
    view["패턴"] = view["pattern"]
    view["Stage"] = view["stage"]
    view["기준일"] = view["as_of_date"]
    view["상태 출처"] = view["replay_provenance"].map({
        "BUNDLED_REPLAY_SNAPSHOT": "번들 스냅샷",
        "INCREMENTAL_FROM_BUNDLED_SNAPSHOT": "스냅샷 이후 증분 재생",
        "ROLLING_300_BAR_REPLAY": "최근 300봉 재생",
    }).fillna(view["replay_provenance"])
    cols = ["ticker", "기준일", "가격", "일간 %", "패턴", "후보 피봇", "후보 거리", "돌파 피봇", "돌파 거리", "20D Ref", "Stage", "재베이스", "RVOL", "3개월 수익률", "SPY 대비 RS(3M)"]
    if pattern_mode:
        cols = ["ticker", "기준일", "패턴", "후보 피봇", "후보 거리", "돌파 피봇", "돌파 거리", "Stage", "재베이스", "RVOL", "3개월 수익률", "SPY 대비 RS(3M)"]
    st.dataframe(view[cols].rename(columns={"ticker": "종목"}), use_container_width=True, hide_index=True)


def _render_symbol_detail(frame: pd.DataFrame, raw: dict[str, pd.DataFrame]):
    if frame.empty:
        return
    st.markdown("#### 종목 상세")
    symbol = st.selectbox("차트 및 엔진 상세", frame["ticker"].tolist(), key="detail_symbol")
    row = frame.loc[frame["ticker"] == symbol].iloc[0]
    bars = raw.get(symbol)
    a, b, c, d = st.columns(4)
    a.metric("종가", f"${row['close']:,.2f}", None if pd.isna(row["day_change_pct"]) else f"{row['day_change_pct']:+.2f}%")
    b.metric("Stage", str(row["stage"]))
    c.metric("패턴", str(row["pattern"]))
    d.metric("재베이스", "진행 중" if row["rebase_active"] else "아님")
    if bars is not None and not bars.empty:
        chart = bars.copy()
        chart["Date"] = pd.to_datetime(chart["Date"])
        chart = chart.set_index("Date")
        chart = chart[["Close"]].copy()
        chart["SMA20"] = bars["Close"].rolling(20).mean().to_numpy()
        chart["SMA50"] = bars["Close"].rolling(50).mean().to_numpy()
        st.line_chart(chart.tail(180), height=280)
    with st.expander("엔진 상태 및 데이터 출처"):
        st.json({
            "as_of_date": row["as_of_date"],
            "pattern_start_date": row["pattern_start_date"],
            "candidate_pivot": _num_or_none(row["candidate_pivot"]),
            "breakout_pivot": _num_or_none(row["breakout_pivot"]),
            "twenty_day_reference": _num_or_none(row["twenty_day_reference"]),
            "rebase_active": bool(row["rebase_active"]),
            "rebase_started_at": row["rebase_started_at"],
            "rebase_reason": row["rebase_reason"],
            "rebase_low": _num_or_none(row["rebase_low"]),
            "rebase_low_date": row["rebase_low_date"],
            "rebase_secondary_reasons": row["rebase_secondary_reasons"],
            "breakout_date": row["breakout_date"],
            "breakout_cycle_active": bool(row["breakout_cycle_active"]),
            "breakout_event_today": bool(row["breakout_event"]),
            "breakout_failure_today": bool(row["breakout_failure"]),
            "historical_breakout_recovered": bool(row.get("historical_breakout_recovered", False)),
            "historical_breakout_recovery_pattern": row.get("historical_breakout_recovery_pattern"),
            "historical_breakout_recovery_event_date": row.get("historical_breakout_recovery_event_date"),
            "historical_breakout_recovery_reason": row.get("historical_breakout_recovery_reason"),
            "historical_breakout_recovery_seed_pattern": row.get("historical_breakout_recovery_seed_pattern"),
            "historical_breakout_recovery_seed_pattern_matches_current": row.get("historical_breakout_recovery_seed_pattern_matches_current"),
            "historical_breakout_recovery_candidate_pivot": _num_or_none(row.get("historical_breakout_recovery_candidate_pivot")),
            "historical_breakout_recovery_previous_close": _num_or_none(row.get("historical_breakout_recovery_previous_close")),
            "historical_breakout_recovery_event_close": _num_or_none(row.get("historical_breakout_recovery_event_close")),
            "historical_breakout_recovery_attempted_event_date": row.get("historical_breakout_recovery_attempted_event_date"),
            "pocket_pivot_today": bool(row["pocket_pivot_today"]),
            "pocket_pivot_reason": row["pocket_pivot_reason"],
            "detected_pattern_diagnostic": row["detected_pattern_diagnostic"],
            "replay_provenance": row["replay_provenance"],
            "validation_status": row["validation_status"],
            "technical_price_basis": row["technical_price_basis"],
            "return_price_basis": row["return_price_basis"],
        })


def _num_or_none(value):
    return None if value is None or pd.isna(value) else float(value)


def _pivot_distance(price: float | None, pivot: float | None):
    if price is None or pivot is None or pd.isna(price) or pd.isna(pivot):
        return None
    try:
        price_value, pivot_value = float(price), float(pivot)
    except (TypeError, ValueError):
        return None
    if price_value <= 0 or pivot_value <= 0:
        return None
    return (price_value / pivot_value - 1.0) * 100.0


def _previous_completed_close_before_session(daily_bars: pd.DataFrame | None, session_date):
    """Return the last completed daily close strictly before the intraday session date."""
    if daily_bars is None or daily_bars.empty or "Date" not in daily_bars or "Close" not in daily_bars:
        return None
    dates = pd.to_datetime(daily_bars["Date"], errors="coerce")
    frame = daily_bars.loc[dates.dt.date < session_date].copy()
    if frame.empty:
        return None
    try:
        value = float(frame.iloc[-1]["Close"])
    except (TypeError, ValueError):
        return None
    return value if value > 0 and pd.notna(value) else None


def _live_source_status(status_code: str) -> str:
    return {
        "LIVE": "LIVE · 5분 데이터 정상",
        "DATA_STALE": "DATA STALE · 신호 비활성",
        "DATA_INCOMPLETE": "DATA INCOMPLETE · 신호 비활성",
        "MARKET_CLOSED": "MARKET CLOSED · 마지막 세션 데이터",
    }.get(status_code, f"{status_code} · 신호 비활성")


def _build_live_monitor_quality_context(
    scan_result: pd.DataFrame,
    raw_by_ticker: Mapping[str, pd.DataFrame],
    watchlist_rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], str]:
    """Build the same cached Momentum/RS/Readiness context used by Momentum Monitor."""
    if scan_result is None or scan_result.empty:
        return {}, "EOD 분석 결과 없음"
    try:
        anchor = pd.to_datetime(scan_result["as_of_date"], errors="coerce").dropna().max()
        if pd.isna(anchor):
            return {}, "EOD 기준일 없음"
        anchor_date = anchor.date().isoformat()
        benchmark_tickers = tuple(_load_market_constituent_tickers() or ())
        universe_label = "S&P 500 + Nasdaq-100 구성 종목"
        if not benchmark_tickers:
            benchmark_tickers = FALLBACK_MOMENTUM_UNIVERSE
            universe_label = "ETF 대체 바스켓"
        benchmark_closes = _load_broad_breadth_closes(anchor_date, tuple(benchmark_tickers))
        if (
            (benchmark_closes is None or benchmark_closes.empty or len(benchmark_closes) < 253)
            and benchmark_tickers != FALLBACK_MOMENTUM_UNIVERSE
        ):
            benchmark_tickers = FALLBACK_MOMENTUM_UNIVERSE
            universe_label = "ETF 대체 바스켓"
            benchmark_closes = _load_broad_breadth_closes(anchor_date, tuple(benchmark_tickers))
        qqq_history = _load_yfinance_bars("QQQ")
        monitor, _leadership = build_momentum_monitor_frame(
            scan_result, raw_by_ticker, watchlist_rows,
            benchmark_closes, benchmark_tickers, qqq_history,
        )
        mapping = {
            str(row.get("종목") or "").strip().upper(): row
            for row in monitor.to_dict("records")
            if str(row.get("종목") or "").strip()
        }
        if benchmark_closes is None or benchmark_closes.empty or len(benchmark_closes) < 253:
            return mapping, "Momentum/RS 기준 자료 부족 · 일부 품질 구성요소는 제외됨"
        return mapping, f"Momentum/RS 기준: {universe_label} · EOD {anchor_date}"
    except Exception as exc:
        return {}, f"LIVE Entry Quality 보조 데이터 부족: {type(exc).__name__}"


def _live_price_text(value: Any) -> str:
    try:
        number = float(value)
        return f"${number:,.2f}" if np.isfinite(number) and number > 0 else "—"
    except (TypeError, ValueError):
        return "—"


def _live_pct_text(value: Any) -> str:
    try:
        number = float(value)
        return f"{number:+.2f}%" if np.isfinite(number) else "—"
    except (TypeError, ValueError):
        return "—"


def _live_pattern_text(value: Any, *, rebase_active: bool = False) -> str:
    pattern = str(value or "—").strip()
    if rebase_active or pattern.upper() in {"BASE FORMING / NO VALID PATTERN", "BASE FORMING", "NONE", "NAN", ""}:
        return "⏳ BASE FORMING"
    if pattern == "Saucer Base":
        return "🥣 Saucer Base"
    if pattern == "Base-on-Base":
        return "🧱 Base-on-Base"
    if pattern == "Double Bottom":
        return "🔵 Double Bottom"
    if pattern == "Cup Without Handle":
        return "☕ Cup Without Handle"
    if pattern == "High Tight Flag":
        return "🚩 High Tight Flag"
    return pattern


def _render_live_monitor_page(
    client,
    user_id: str,
    symbols: list[str],
    watchlist_rows: list[dict[str, Any]],
) -> None:
    st.subheader("⏱️ Live Monitor — 30분")
    st.caption("ENTRY STAGE = 공식 완료 일봉 상태 · LIVE STATE = 장중 임시 상태. 장중 계산은 공식 EOD Snapshot/Stage/Pattern/Pivot 이력을 수정하지 않습니다.")
    st.warning(
        "Yahoo Finance 5분봉은 잠정 시세이며 거래소 원천 실시간 가격을 보장하지 않습니다. "
        "LIVE STATE와 LIVE ENTRY QUALITY는 보조 관찰값이고 주문을 제출하거나 공식 돌파/실패를 확정하지 않습니다."
    )
    with st.expander("계산 기준 및 안전장치", expanded=False):
        st.markdown(
            "- **ENTRY STAGE / Pattern / Candidate Pivot / Breakout Pivot / Next Pivot:** V2의 완료 일봉 chronological EOD 엔진 결과만 사용합니다. 재베이스·Base·피봇 규칙을 장중 시세로 재계산하지 않습니다.\n"
            "- **LIVE STATE:** 장중 5분봉의 마지막 관측 가격을 V2 공식 피봇과 비교한 임시 표시입니다. `BREAKOUT (LIVE)`는 공식 `BREAKOUT` Stage 이벤트가 아닙니다. 장중 하락만으로 공식 `BREAKOUT FAILURE`를 기록하지 않습니다.\n"
            "- **LIVE ENTRY QUALITY:** V2 명세의 Pattern 15, Setup 20, RS 15, Live Momentum 10, Live Volume 10, MA Structure 10 가중치를 씁니다. Initial Risk는 숫자 참고값이며 품질 점수에 포함되지 않습니다.\n"
            "- **RVOL AT TIME:** 오늘 완료된 5분봉의 세션 누적 거래량 ÷ 직전 최대 5개 세션의 같은 시각까지 누적 거래량 평균입니다. 최소 3개 비교 세션이 없으면 표시하지 않습니다. 공식 EOD RVOL/1.30× 판정과 혼합하지 않습니다.\n"
            "- 정규장 중 마지막 관측 봉이 15분보다 오래됐거나 당일 봉이 없으면 `DATA STALE`, 기준값/데이터가 부족하면 `DATA INCOMPLETE`로 표시하고 LIVE Quality를 비활성화합니다. 장이 닫힌 동안에는 `MARKET CLOSED`로 표시합니다.\n"
            "- `최근 공식 상태`는 V2 chronological replay가 반환한 완료 일봉 Stage 이력입니다. `내 매수 이력`은 사용자 본인의 V2 PP Journal 거래/추가매수 기록만 읽습니다."
        )

    if not symbols:
        st.info("활성 관심종목이 없습니다. Watchlist에서 종목을 추가하거나 활성화해 주세요.")
        return

    refresh_col, info_col = st.columns([1, 3])
    if refresh_col.button("장중 데이터 새로고침", type="primary", key="v2_live_refresh"):
        _load_live_intraday_bars.clear()
        st.rerun()
    info_col.caption("장중 화면은 30분마다 자동 갱신 · Yahoo 5분봉 캐시 최대 60초 · EOD 기준 캐시 최대 15분")

    with st.spinner(f"{len(symbols)}개 활성 관심종목의 공식 EOD 기준값을 확인 중…"):
        eod_result, daily_raw, eod_errors, elapsed = _run_scan(symbols, "Yahoo Finance (EOD)")

    eod_by_symbol: dict[str, dict[str, Any]] = {}
    if not eod_result.empty:
        eod_by_symbol = {
            str(row["ticker"]).upper().strip(): row.to_dict()
            for _, row in eod_result.iterrows()
        }

    with st.spinner("Live Entry Quality용 EOD Momentum/RS 기준값을 불러오는 중…"):
        monitor_context, context_note = _build_live_monitor_quality_context(eod_result, daily_raw, watchlist_rows)

    trades: list[dict[str, Any]] = []
    trade_adds: list[dict[str, Any]] = []
    trade_exits: list[dict[str, Any]] = []
    trade_history_error = ""
    try:
        trades = list_pp_trades(client, user_id)
        trade_ids = [str(row.get("id") or "") for row in trades if row.get("id")]
        if trade_ids:
            trade_adds = list_trade_adds(client, user_id, trade_ids)
            trade_exits = list_trade_exits(client, user_id, trade_ids)
    except Exception as exc:
        trade_history_error = f"{type(exc).__name__}: V2 PP Journal 매수 이력을 조회하지 못했습니다."

    @st.fragment(run_every="30m")
    def _live_monitor_fragment() -> None:
        # EOD context and trade records are intentionally captured from the
        # parent run. Fragment reruns refresh only provisional intraday values.
        now = datetime.now(tz=LIVE_NY)
        bars_by_symbol: dict[str, pd.DataFrame] = {}
        live_rows: list[dict[str, Any]] = []
        live_errors: list[tuple[str, str]] = []

        def load_intraday_one(symbol: str):
            try:
                return symbol, _load_live_intraday_bars(symbol), None
            except Exception as exc:
                return symbol, None, f"{type(exc).__name__}: {exc}"

        with ThreadPoolExecutor(max_workers=min(4, max(1, len(symbols)))) as pool:
            futures = {pool.submit(load_intraday_one, symbol): symbol for symbol in symbols}
            intraday_results = [future.result() for future in as_completed(futures)]

        for symbol, intraday_bars, fetch_error in intraday_results:
            key = str(symbol).upper().strip()
            baseline = eod_by_symbol.get(key)
            eod_date = str((baseline or {}).get("as_of_date") or "—")
            official_stage = str((baseline or {}).get("stage") or "DATA INCOMPLETE")
            pattern_raw = str((baseline or {}).get("pattern") or "—")
            candidate_pivot = _num_or_none((baseline or {}).get("candidate_pivot"))
            breakout_pivot = _num_or_none((baseline or {}).get("breakout_pivot"))
            twenty_ref = _num_or_none((baseline or {}).get("twenty_day_reference"))
            breakout_active = bool((baseline or {}).get("breakout_cycle_active", False))
            candidate_source = str((baseline or {}).get("candidate_pivot_source") or ("SOURCE_NOT_STORED" if candidate_pivot is not None else "—"))
            breakout_source = str((baseline or {}).get("breakout_pivot_source") or ("HISTORICAL_SOURCE_NOT_STORED" if breakout_pivot is not None else "—"))
            next_pivot = _num_or_none((baseline or {}).get("next_pivot_candidate"))
            next_source = str((baseline or {}).get("next_pivot_candidate_source") or "—")
            if breakout_active and candidate_pivot is not None and (breakout_pivot is None or abs(candidate_pivot - breakout_pivot) > 1e-9):
                next_pivot, next_source = candidate_pivot, candidate_source
            elif next_pivot is None and candidate_pivot is not None and not breakout_active:
                next_pivot, next_source = candidate_pivot, candidate_source

            if fetch_error or intraday_bars is None or intraday_bars.empty:
                live_errors.append((key, fetch_error or "LiveMarketDataError: no intraday bars"))
                live_rows.append({
                    "ticker": key, "entry_stage": official_stage, "live_state": "DATA INCOMPLETE",
                    "live_quality": "—", "live_quality_score": None, "live_quality_detail": "",
                    "live_price": None, "change_pct": None, "pattern": _live_pattern_text(pattern_raw, rebase_active=bool((baseline or {}).get("rebase_active", False))),
                    "pattern_upper": None, "candidate_pivot": candidate_pivot, "candidate_source": candidate_source,
                    "twenty_day_reference": twenty_ref, "breakout_pivot": breakout_pivot, "breakout_source": breakout_source,
                    "next_pivot": next_pivot, "next_source": next_source, "pivot_distance_pct": None,
                    "rvol_at_time": None, "recent_official_state": format_recent_official_state_history((baseline or {}).get("recent_official_states")),
                    "buy_history": "조회 오류" if trade_history_error else "—", "updated": "—", "data_status": "DATA INCOMPLETE · LIVE 비활성",
                    "eod_as_of_date": eod_date, "priority": -2, "_status_code": "DATA_INCOMPLETE",
                })
                continue

            bars_by_symbol[key] = intraday_bars
            try:
                data_status = assess_live_data_status(intraday_bars, now=now)
                metrics = live_session_metrics(intraday_bars, now=now)
                latest_stamp = pd.Timestamp(intraday_bars["Date"].iloc[-1]).tz_convert(LIVE_NY)
                session_date = latest_stamp.date()
                live_price = _num_or_none(metrics.get("last_price"))
                daily_bars = daily_raw.get(key)
                previous_close = _previous_completed_close_before_session(daily_bars, session_date)
                change_pct = _pivot_distance(live_price, previous_close)
                eod_baseline_ok = baseline is not None and previous_close is not None and _num_or_none((baseline or {}).get("close")) is not None
                raw_state = "DATA INCOMPLETE"
                if not eod_baseline_ok:
                    raw_state = "DATA INCOMPLETE"
                    display_state = "DATA INCOMPLETE"
                    display_status = "DATA INCOMPLETE · EOD 기준선 없음"
                elif data_status.code in {"DATA_STALE", "DATA_INCOMPLETE"}:
                    raw_state = data_status.code
                    display_state = present_live_state(raw_state, official_stage=official_stage, rebase_active=bool((baseline or {}).get("rebase_active", False)), data_status=data_status.code)
                    display_status = _live_source_status(data_status.code)
                else:
                    crossed = crossed_pivot_this_session(intraday_bars, candidate_pivot, now=now)
                    raw_state = classify_live_state(
                        live_price=live_price,
                        candidate_pivot=candidate_pivot,
                        breakout_pivot=breakout_pivot,
                        breakout_cycle_active=breakout_active,
                        candidate_crossed_today=crossed,
                    )
                    display_state = present_live_state(
                        raw_state, official_stage=official_stage,
                        rebase_active=bool((baseline or {}).get("rebase_active", False)),
                        data_status=data_status.code,
                    )
                    display_status = _live_source_status(data_status.code)
                    if data_status.code == "MARKET_CLOSED":
                        display_state = "MARKET CLOSED"

                candidate_distance = _pivot_distance(live_price, candidate_pivot)
                breakout_distance = _pivot_distance(live_price, breakout_pivot)
                # `돌파 Pivot 거리` follows the active official pivot: fixed Breakout
                # Pivot while the official breakout cycle is active; Candidate Pivot otherwise.
                pivot_distance = breakout_distance if breakout_active and breakout_pivot is not None else candidate_distance
                rvol_value = _num_or_none(metrics.get("live_rvol"))
                monitor_row = monitor_context.get(key, {})
                quality = compute_live_entry_quality(
                    live_available=(data_status.code == "LIVE" and eod_baseline_ok),
                    official_stage=official_stage,
                    raw_live_state=raw_state,
                    live_price=live_price,
                    eod_row=baseline,
                    monitor_row=monitor_row,
                    daily_bars=daily_bars,
                    live_rvol=rvol_value,
                    candidate_distance_pct=candidate_distance,
                    breakout_distance_pct=breakout_distance,
                    breakout_cycle_active=breakout_active,
                )
                quality_label = quality.get("label", "—")
                if data_status.code in {"DATA_STALE", "DATA_INCOMPLETE", "MARKET_CLOSED"}:
                    quality_label = "—"
                recent_states = format_recent_official_state_history((baseline or {}).get("recent_official_states"))
                buy_history = "조회 오류" if trade_history_error else format_buy_history(
                    key, live_price, trades, trade_adds, trade_exits
                )
                state_rank = {
                    "BREAKOUT (LIVE)": 5,
                    "BREAKOUT HOLD (LIVE)": 4,
                    "PIVOT APPROACH (LIVE)": 3,
                    "BASE + PP": 2,
                    "BASE": 1,
                    "BASE FORMING": 1,
                    "BELOW BREAKOUT PIVOT (LIVE)": 0,
                    "MARKET CLOSED": -1,
                    "DATA STALE": -2,
                    "DATA INCOMPLETE": -3,
                }.get(display_state, 0)
                live_rows.append({
                    "ticker": key, "entry_stage": official_stage, "live_state": display_state,
                    "live_quality": quality_label, "live_quality_score": quality.get("score"),
                    "live_quality_detail": quality.get("detail", ""),
                    "live_price": live_price, "change_pct": change_pct,
                    "pattern": _live_pattern_text(pattern_raw, rebase_active=bool((baseline or {}).get("rebase_active", False))),
                    "pattern_upper": _num_or_none((baseline or {}).get("pattern_upper", candidate_pivot)),
                    "candidate_pivot": candidate_pivot, "candidate_source": candidate_source,
                    "twenty_day_reference": twenty_ref, "breakout_pivot": breakout_pivot, "breakout_source": breakout_source,
                    "next_pivot": next_pivot, "next_source": next_source, "pivot_distance_pct": pivot_distance,
                    "rvol_at_time": rvol_value, "recent_official_state": recent_states,
                    "buy_history": buy_history, "updated": latest_stamp.strftime("%H:%M ET"),
                    "data_status": display_status, "eod_as_of_date": eod_date,
                    "priority": state_rank, "_status_code": data_status.code,
                    "_rvol_reference_sessions": int(metrics.get("reference_sessions") or 0),
                    "_rvol_as_of": metrics.get("rvol_as_of"),
                })
            except Exception as exc:
                live_errors.append((key, f"Live analysis {type(exc).__name__}: {exc}"))
                live_rows.append({
                    "ticker": key, "entry_stage": official_stage, "live_state": "DATA INCOMPLETE",
                    "live_quality": "—", "live_quality_score": None, "live_quality_detail": "",
                    "live_price": None, "change_pct": None,
                    "pattern": _live_pattern_text(pattern_raw, rebase_active=bool((baseline or {}).get("rebase_active", False))),
                    "pattern_upper": None, "candidate_pivot": candidate_pivot, "candidate_source": candidate_source,
                    "twenty_day_reference": twenty_ref, "breakout_pivot": breakout_pivot, "breakout_source": breakout_source,
                    "next_pivot": next_pivot, "next_source": next_source, "pivot_distance_pct": None,
                    "rvol_at_time": None, "recent_official_state": format_recent_official_state_history((baseline or {}).get("recent_official_states")),
                    "buy_history": "조회 오류" if trade_history_error else "—", "updated": "—", "data_status": "DATA INCOMPLETE · 계산 오류",
                    "eod_as_of_date": eod_date, "priority": -3, "_status_code": "DATA_INCOMPLETE",
                })

        frame = pd.DataFrame(live_rows)
        if frame.empty:
            st.error("Live Monitor 결과가 없습니다.")
            return
        frame["_rvol_sort"] = pd.to_numeric(frame.get("rvol_at_time"), errors="coerce")
        frame = frame.sort_values(["priority", "_rvol_sort", "ticker"], ascending=[False, False, True], kind="stable")
        fresh_n = int(frame.get("_status_code", pd.Series(dtype=str)).eq("LIVE").sum())
        stale_n = int(frame.get("_status_code", pd.Series(dtype=str)).isin(["DATA_STALE", "DATA_INCOMPLETE"]).sum())
        market_closed = bool(frame.get("_status_code", pd.Series(dtype=str)).eq("MARKET_CLOSED").all())
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("활성 관심종목", len(symbols))
        k2.metric("LIVE 정상", fresh_n)
        k3.metric("데이터 제한/오류", stale_n)
        k4.metric("EOD 기준 계산", f"{elapsed:.1f}s")
        if market_closed:
            st.info("미국 정규장이 닫혀 있습니다. 마지막 세션 가격과 공식 EOD 정보는 참고용이며 LIVE Entry Quality는 비활성화됩니다.")
        st.caption(context_note)
        if trade_history_error:
            st.caption(trade_history_error)

        display = frame.copy()
        display["현재가"] = display["live_price"].map(_live_price_text)
        display["장중 등락률"] = display["change_pct"].map(_live_pct_text)
        for raw_col, output_col in (
            ("pattern_upper", "Pattern 상단"),
            ("candidate_pivot", "Candidate Pivot"),
            ("twenty_day_reference", "20D Reference"),
            ("breakout_pivot", "돌파 Pivot"),
            ("next_pivot", "Next Pivot"),
        ):
            display[output_col] = display[raw_col].map(_live_price_text)
        display["돌파 Pivot 거리"] = display["pivot_distance_pct"].map(_live_pct_text)
        display["RVOL AT TIME"] = display["rvol_at_time"].map(
            lambda value: f"{float(value):.2f}×" if value is not None and pd.notna(value) else "—"
        )
        display["ENTRY STAGE"] = display["entry_stage"]
        display["LIVE STATE"] = display["live_state"]
        display["LIVE ENTRY QUALITY"] = display["live_quality"]
        display["Candidate Source"] = display["candidate_source"].fillna("—")
        display["돌파 Pivot Source"] = display["breakout_source"].fillna("—")
        display["Next Pivot Source"] = display["next_source"].fillna("—")
        display["최근 공식 상태"] = display["recent_official_state"].fillna("—")
        display["내 매수 이력"] = display["buy_history"].fillna("—")
        display["업데이트"] = display["updated"].fillna("—")
        columns = [
            "ticker", "ENTRY STAGE", "LIVE STATE", "LIVE ENTRY QUALITY", "현재가", "장중 등락률",
            "pattern", "Pattern 상단", "Candidate Pivot", "Candidate Source", "20D Reference",
            "돌파 Pivot", "돌파 Pivot Source", "Next Pivot", "Next Pivot Source", "돌파 Pivot 거리",
            "RVOL AT TIME", "최근 공식 상태", "내 매수 이력", "업데이트",
        ]
        display = display[columns].rename(columns={"ticker": "종목", "pattern": "Pattern"})
        st.dataframe(display, use_container_width=True, hide_index=True, height=min(680, max(220, 40 + 36 * len(display))))

        quality_rows = frame.loc[frame["live_quality"].astype(str).ne("—")].copy()
        if not quality_rows.empty:
            with st.expander("LIVE Entry Quality 상세", expanded=False):
                st.dataframe(
                    quality_rows[["ticker", "entry_stage", "live_state", "live_quality", "live_quality_score", "live_quality_detail"]]
                    .rename(columns={
                        "ticker": "종목", "entry_stage": "공식 ENTRY STAGE", "live_state": "LIVE STATE",
                        "live_quality": "LIVE ENTRY QUALITY", "live_quality_score": "점수", "live_quality_detail": "구성/판정 근거",
                    }), use_container_width=True, hide_index=True,
                )

        if eod_errors:
            with st.expander(f"완료 일봉 기준선 오류 ({len(eod_errors)})", expanded=False):
                st.dataframe(pd.DataFrame(eod_errors, columns=["종목", "오류"]), use_container_width=True, hide_index=True)
        if live_errors:
            with st.expander(f"장중 데이터/계산 오류 ({len(live_errors)})", expanded=False):
                st.dataframe(pd.DataFrame(live_errors, columns=["종목", "오류"]), use_container_width=True, hide_index=True)
        st.caption(
            "5분봉 잠정 가격 · 30분 자동 갱신 · RVOL AT TIME은 완료된 장중 봉만 사용 · "
            "공식 ENTRY STAGE/패턴/피봇/리베이스는 완료 일봉 엔진 소유 · 주문 제출 없음"
        )

    _live_monitor_fragment()


def _save_auth_session(session) -> None:
    if session is None:
        return
    access_token = getattr(session, "access_token", None)
    refresh_token = getattr(session, "refresh_token", None)
    if access_token and refresh_token:
        st.session_state["_v2_supabase_access_token"] = access_token
        st.session_state["_v2_supabase_refresh_token"] = refresh_token


def _clear_auth_session() -> None:
    st.session_state.pop("_v2_supabase_access_token", None)
    st.session_state.pop("_v2_supabase_refresh_token", None)
    st.session_state.pop("_v2_supabase_user_email", None)


def _user_from_auth_response(response):
    return getattr(response, "user", None)


def _require_v2_login():
    """Protect the public Streamlit app and only expose each user's own V2 rows."""
    try:
        supabase_url = str(st.secrets.get("SUPABASE_URL", "")).strip()
        supabase_anon_key = str(st.secrets.get("SUPABASE_ANON_KEY", "")).strip()
    except Exception:
        supabase_url, supabase_anon_key = "", ""

    if not supabase_url or not supabase_anon_key:
        st.title("Portfolio Dashboard V2 · 최초 설정")
        st.warning("V2 전용 Supabase 프로젝트 연결이 아직 설정되지 않았습니다.")
        st.markdown("""
이 대시보드는 공개 URL로 배포되므로, 관심종목을 보호하기 위해 로그인과 사용자별 RLS를 사용합니다.

1. **새로운 빈 Supabase 프로젝트**를 만듭니다. 기존 프로젝트와 기존 데이터는 연결하지 않습니다.
2. 저장소의 `sql/v2_watchlist_schema.sql`을 Supabase SQL Editor에서 실행합니다.
3. Supabase **Authentication → Users**에서 본인 계정을 직접 생성하고, 공개 회원가입은 비활성화합니다.
4. Streamlit Cloud의 앱 설정 → **Secrets**에 아래 두 값만 추가합니다. 키는 GitHub에 올리지 마세요.
""")
        st.code('SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"\nSUPABASE_ANON_KEY = "YOUR_PUBLIC_ANON_OR_PUBLISHABLE_KEY"', language="toml")
        st.caption("service_role 키는 사용하지 않습니다. 로그인 후에는 사용자별 Row Level Security가 데이터 접근을 제한합니다.")
        st.stop()

    try:
        client = create_supabase_client(supabase_url, supabase_anon_key)
    except Exception as exc:
        st.title("Portfolio Dashboard V2 · 연결 설정 확인")
        st.error(f"Supabase 클라이언트를 만들지 못했습니다: {type(exc).__name__}: {exc}")
        st.stop()

    user = None
    access = st.session_state.get("_v2_supabase_access_token")
    refresh = st.session_state.get("_v2_supabase_refresh_token")
    if access and refresh:
        try:
            restored = client.auth.set_session(access, refresh)
            _save_auth_session(getattr(restored, "session", None) or restored)
            user_response = client.auth.get_user()
            user = getattr(user_response, "user", None)
        except Exception:
            _clear_auth_session()
            user = None

    if user is None:
        st.title("Portfolio Dashboard V2 · 로그인")
        st.caption("새 V2 전용 Supabase 계정으로 로그인하세요. 기존 대시보드 데이터는 가져오지 않습니다.")
        with st.form("v2_login_form", clear_on_submit=False):
            email = st.text_input("이메일", key="v2_login_email")
            password = st.text_input("비밀번호", type="password", key="v2_login_password")
            submitted = st.form_submit_button("로그인", type="primary", use_container_width=True)
        if submitted:
            try:
                response = client.auth.sign_in_with_password({"email": email.strip(), "password": password})
                session = getattr(response, "session", None)
                user = _user_from_auth_response(response)
                if session is None or user is None:
                    raise RuntimeError("로그인 세션을 만들지 못했습니다.")
                _save_auth_session(session)
                st.session_state["_v2_supabase_user_email"] = getattr(user, "email", email.strip())
                st.rerun()
            except Exception as exc:
                st.error(f"로그인에 실패했습니다. 이메일/비밀번호와 Supabase Auth 사용자를 확인하세요. ({type(exc).__name__})")
        st.info("계정은 새 Supabase 프로젝트의 Authentication → Users에서 직접 생성합니다. 앱 안에는 공개 회원가입을 제공하지 않습니다.")
        st.stop()

    # The access token may have been refreshed by the SDK; keep the newest values in session state.
    try:
        current = client.auth.get_session()
        _save_auth_session(current)
    except Exception:
        pass
    st.session_state["_v2_supabase_user_email"] = getattr(user, "email", "") or st.session_state.get("_v2_supabase_user_email", "")
    return client, user


def _render_watchlist_page(client, user_id: str, rows: list[dict]) -> None:
    st.subheader("관심종목 관리")
    st.caption("V2 전용 신규 목록입니다. 기존 관심종목이나 거래 기록은 가져오지 않았습니다. 활성 종목만 Momentum Monitor/Live Monitor/Pattern Scanner의 분석 대상에 포함됩니다.")

    with st.expander("➕ 관심종목 추가", expanded=not rows):
        with st.form("v2_watchlist_add_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            ticker = c1.text_input("티커", placeholder="예: NVDA, GOOG, BRK-B")
            sector = c2.text_input("섹터", placeholder="예: AI 반도체")
            c3, c4 = st.columns(2)
            classification_label = c3.selectbox("분류", ["일반 관찰", "Core", "Satellite"])
            enabled = c4.checkbox("활성화 (분석 대상에 포함)", value=True)
            note = st.text_area("메모 (선택)", max_chars=500, placeholder="관찰 이유, 관심 테마 등")
            add_submitted = st.form_submit_button("관심종목 저장", type="primary", use_container_width=True)
        if add_submitted:
            classification = {"일반 관찰": "WATCH", "Core": "CORE", "Satellite": "SATELLITE"}[classification_label]
            try:
                add_watchlist_item(client, user_id, ticker=ticker, sector=sector, classification=classification, note=note, enabled=enabled)
                st.success(f"{ticker.strip().upper()} 저장 완료")
                st.rerun()
            except WatchlistValidationError as exc:
                st.error(str(exc))
            except Exception as exc:
                if "v2_watchlist_user_ticker_unique" in str(exc) or "duplicate key" in str(exc).lower():
                    st.error("이미 등록된 티커입니다. 아래 목록에서 수정해 주세요.")
                else:
                    st.error(f"저장 실패: {type(exc).__name__}: {exc}")

    if not rows:
        st.info("관심종목이 없습니다. 위에서 첫 종목을 추가해 주세요.")
        return

    role_labels = {"WATCH": "일반 관찰", "CORE": "Core", "SATELLITE": "Satellite"}
    display_rows = [{
        "티커": r["ticker"], "섹터": r.get("sector") or "미분류",
        "분류": role_labels.get(str(r.get("classification", "WATCH")), str(r.get("classification", "WATCH"))),
        "활성": bool(r.get("enabled", True)), "메모": r.get("note") or "",
    } for r in rows]
    n_active = sum(bool(r.get("enabled", True)) for r in rows)
    a, b, c = st.columns(3)
    a.metric("전체 관심종목", len(rows))
    b.metric("분석 활성", n_active)
    c.metric("비활성", len(rows) - n_active)
    st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)

    st.markdown("#### 종목 수정 또는 삭제")
    tickers = [str(r["ticker"]) for r in rows]
    selected_ticker = st.selectbox("수정할 티커", tickers, key="v2_watchlist_edit_ticker")
    current = next(r for r in rows if str(r["ticker"]) == selected_ticker)
    current_class = str(current.get("classification", "WATCH"))
    class_to_label = {"WATCH": "일반 관찰", "CORE": "Core", "SATELLITE": "Satellite"}
    label_to_class = {v: k for k, v in class_to_label.items()}
    with st.form(f"v2_watchlist_edit_form_{selected_ticker}"):
        e1, e2 = st.columns(2)
        edit_sector = e1.text_input("섹터", value=str(current.get("sector") or ""), key=f"v2_sector_{selected_ticker}")
        edit_class_label = e2.selectbox("분류", ["일반 관찰", "Core", "Satellite"], index=["일반 관찰", "Core", "Satellite"].index(class_to_label.get(current_class, "일반 관찰")), key=f"v2_class_{selected_ticker}")
        edit_enabled = st.checkbox("활성화 (분석 대상에 포함)", value=bool(current.get("enabled", True)), key=f"v2_enabled_{selected_ticker}")
        edit_note = st.text_area("메모", value=str(current.get("note") or ""), max_chars=500, key=f"v2_note_{selected_ticker}")
        save_edit = st.form_submit_button("변경 저장", type="primary", use_container_width=True)
    if save_edit:
        try:
            update_watchlist_item(client, user_id, selected_ticker, sector=edit_sector, classification=label_to_class[edit_class_label], note=edit_note, enabled=edit_enabled)
            st.success(f"{selected_ticker} 변경 완료")
            st.rerun()
        except Exception as exc:
            st.error(f"수정 실패: {type(exc).__name__}: {exc}")

    with st.expander("🗑️ 관심종목 삭제", expanded=False):
        confirm_delete = st.checkbox(f"{selected_ticker} 삭제를 확인합니다", key=f"v2_confirm_delete_{selected_ticker}")
        if st.button("선택 종목 삭제", disabled=not confirm_delete, key=f"v2_delete_{selected_ticker}"):
            try:
                delete_watchlist_item(client, user_id, selected_ticker)
                st.success(f"{selected_ticker} 삭제 완료")
                st.rerun()
            except Exception as exc:
                st.error(f"삭제 실패: {type(exc).__name__}: {exc}")


def _pp_constructive_context(row: Mapping[str, Any]) -> bool:
    if bool(row.get("detected_pattern_diagnostic")):
        return True
    close = _num_or_none(row.get("close"))
    sma50 = _num_or_none(row.get("sma50"))
    sma200 = _num_or_none(row.get("sma200"))
    return close is not None and sma50 is not None and sma200 is not None and close > sma50 and sma50 >= sma200


def _pp_quality_grade(rs, rs_delta, momentum, volume_ratio, base_score, pivot_distance):
    """Legacy-style PP Quality snapshot; missing inputs are not silently scored as zero."""
    values = [rs, rs_delta, momentum, volume_ratio, base_score, pivot_distance]
    if not any(_num_or_none(v) is not None for v in values):
        return None
    rs, rs_delta, momentum, volume_ratio, base_score, pivot_distance = [_num_or_none(v) for v in values]
    score = 0
    if rs is not None and rs >= 80:
        score += 2
    if rs_delta is not None and rs_delta > 0:
        score += 1
    if momentum is not None and momentum >= 70:
        score += 1
    if volume_ratio is not None and volume_ratio >= 1.2:
        score += 1
    if base_score is not None and base_score >= 7:
        score += 1
    if pivot_distance is not None and -15 <= pivot_distance <= 0:
        score += 1
    return "A" if score >= 6 else ("B" if score >= 4 else "C")


def _recalculate_pp_signal_outcomes(saved_signals: list[dict[str, Any]], analyze_pp_signal_outcome) -> None:
    """Refresh forward outcomes from completed EOD bars only; no official stage events are inferred."""
    outcomes = []
    errors = []
    unique_symbols = list(dict.fromkeys(str(sig.get("ticker", "")).upper() for sig in saved_signals if sig.get("ticker")))
    bars_cache: dict[str, pd.DataFrame] = {}
    for symbol in unique_symbols:
        try:
            bars_cache[symbol] = _load_yfinance_bars(symbol)
        except Exception as exc:
            errors.append((symbol, f"{type(exc).__name__}: {exc}"))
    for sig in saved_signals:
        outcome = analyze_pp_signal_outcome(sig, bars_cache.get(str(sig.get("ticker", "")).upper(), pd.DataFrame()))
        for field in ("signal_price", "candidate_pivot", "breakout_pivot", "pivot_dist_pct", "pp_quality", "rs_rating", "momentum_score", "market_regime", "entry_stage", "pattern_name"):
            outcome[field] = sig.get(field)
        outcomes.append(outcome)
    st.session_state["v2_pp_signal_outcomes"] = outcomes
    st.session_state["v2_pp_signal_outcome_errors"] = errors


def _scan_pp_for_symbols(symbols: list[str], watchlist_rows: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[str, str]], float]:
    if not symbols:
        return [], [], [], 0.0
    scan_frame, raw_by_symbol, errors, elapsed = _run_scan(symbols, "Yahoo Finance (EOD)")
    valid_signals: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    if scan_frame.empty:
        return valid_signals, diagnostics, errors, elapsed
    from dashboard_v2.engines.pocket_pivot_engine import detect_pocket_pivot

    quality_context: dict[str, dict[str, Any]] = {}
    if watchlist_rows:
        try:
            quality_context, _context_note = _build_live_monitor_quality_context(scan_frame, raw_by_symbol, watchlist_rows)
        except Exception:
            quality_context = {}
    regime_snapshot = st.session_state.get("v2_market_regime_snapshot", {}) if hasattr(st, "session_state") else {}

    for _, series in scan_frame.iterrows():
        row = series.to_dict()
        symbol = str(row.get("ticker", "")).upper()
        bars = raw_by_symbol.get(symbol)
        if bars is None or bars.empty:
            diagnostics.append({"종목": symbol, "기준일": row.get("as_of_date"), "결과": "DATA INCOMPLETE"})
            continue
        try:
            pp = detect_pocket_pivot(
                bars,
                constructive_context=_pp_constructive_context(row),
                as_of_date=row.get("as_of_date"),
            )
        except Exception as exc:
            diagnostics.append({"종목": symbol, "기준일": row.get("as_of_date"), "결과": f"ERROR: {type(exc).__name__}"})
            continue
        if not pp.valid_signal:
            diagnostics.append({
                "종목": symbol,
                "기준일": row.get("as_of_date"),
                "결과": " | ".join(pp.reason_codes),
            })
            continue
        signal_date = (pp.as_of_date.isoformat() if pp.as_of_date else str(row.get("as_of_date", ""))[:10])
        signal_price = _num_or_none(row.get("close"))
        candidate_pivot = _num_or_none(row.get("candidate_pivot"))
        breakout_pivot = _num_or_none(row.get("breakout_pivot"))
        pivot_ref = breakout_pivot if breakout_pivot is not None else candidate_pivot
        pivot_dist_pct = ((signal_price / pivot_ref - 1.0) * 100.0) if signal_price and pivot_ref and pivot_ref > 0 else None
        quality_row = quality_context.get(symbol, {})
        rs_rating = _num_or_none(quality_row.get("RS"))
        momentum_score = _num_or_none(quality_row.get("Momentum"))
        rs_delta = _num_or_none(quality_row.get("_rs_delta"))
        base_score = _num_or_none(quality_row.get("_base_score"))
        volume_ratio = _num_or_none(row.get("rvol"))
        if volume_ratio is None:
            volume_ratio = _num_or_none(row.get("RVOL"))
        pp_quality = _pp_quality_grade(rs_rating, rs_delta, momentum_score, volume_ratio, base_score, pivot_dist_pct)
        market_regime = regime_snapshot.get("label") if str(regime_snapshot.get("data_date", ""))[:10] == signal_date else None
        valid_signals.append({
            "ticker": symbol,
            "signal_date": signal_date,
            "signal_price": signal_price,
            "signal_volume": pp.signal_volume,
            "max_prior_down_day_volume": pp.max_prior_down_day_volume,
            "reference_ma": pp.reference_ma,
            "pattern_name": row.get("pattern") or "BASE FORMING / NO VALID PATTERN",
            "entry_stage": row.get("stage") or "BASE",
            "candidate_pivot": candidate_pivot,
            "breakout_pivot": breakout_pivot,
            "rs_rating": rs_rating,
            "momentum_score": momentum_score,
            "pp_quality": pp_quality,
            "market_regime": market_regime,
            "pivot_dist_pct": pivot_dist_pct,
            "context_valid": bool(pp.context_valid),
            "reason_codes": list(pp.reason_codes),
            "valid_signal": bool(pp.valid_signal),
        })
    return valid_signals, diagnostics, errors, elapsed


def _render_pp_journal_page(client, user_id: str, symbols: list[str], watchlist_rows: list[dict[str, Any]] | None = None) -> None:
    from datetime import date
    from dashboard_v2.services.pp_journal_repository import (
        ENTRY_STAGES, EXIT_REASONS, PPJournalValidationError,
        add_pp_trade_add, add_pp_trade_exit, create_pp_trade,
        list_pp_signals, list_pp_trades, list_trade_adds, list_trade_exits,
        save_pp_signals,
    )
    from dashboard_v2.services.pp_journal_analytics import (
        analyze_pp_signal_outcome, summarize_trade, summarize_trades,
    )

    st.subheader("🧪 Pocket Pivot Journal")
    st.caption("PP Tracking은 모든 유효 PP Signal을 기록하고, PP Trade는 실제 매수만 기록하며, PP Analytics는 신호 후속 가격 움직임과 실제 거래 성과를 분리해 보여줍니다. 주문 제출·자동 매매는 없습니다.")
    pp_journal_view = st.radio(
        "PP Journal 보기", ["📡 PP Tracking", "📝 PP Trade", "📊 PP Analytics"],
        horizontal=True, key="v2_pp_journal_view", label_visibility="collapsed"
    )
    with st.expander("저장·계산 기준", expanded=False):
        st.markdown(
            "- **PP Signal:** 완료 일봉 공통 엔진의 Pocket Pivot 조건을 통과한 이벤트만 저장합니다. 사용자·티커·신호일 조합으로 중복을 막고 당시 가격/거래량/피봇/Stage와 이용 가능한 RS/Momentum/Quality/Regime 스냅샷을 보존합니다.\n"
            "- **Trade:** 최초 진입, 추가매수, 부분청산, 전량청산은 별도 행으로 추가됩니다. 최초 Stage와 진입 시점의 초기 손절선은 나중의 Stage 변화로 덮어쓰지 않습니다.\n"
            "- **Position 상태:** OPEN은 청산 기록 없음, PARTIAL은 일부 청산 후 잔여 수량이 있음, CLOSED는 잔여 수량이 0인 경우에만 표시합니다. 예를 들어 90% 청산 후 10%가 남으면 PARTIAL입니다.\n"
            "- **손익:** 청산 손익은 진입 건을 시간순으로 적용하는 FIFO 기준 참고치입니다. 세무/브로커 명세서와 별개의 저널 분석값입니다.\n"
            "- **신호 후속 결과:** +5%/+10%/+20%, MFE/MAE, 저장 당시 기준 Pivot 도달과 돌파 후 실패 여부는 신호일 다음 완료 일봉으로 측정합니다. 이는 가격 관측값이지 공식 ENTRY STAGE의 BREAKOUT/FAILURE 이벤트가 아닙니다.\n"
            "- **신호 스냅샷 컬럼:** RS/Momentum/PP Quality/Market Regime/Pivot 거리를 영구 저장하려면 V2 Supabase에서 `sql/v2_pp_journal_tracking_metrics.sql`을 한 번 실행합니다. 적용 전에도 기본 저널은 동작하지만 해당 추가 값은 보존되지 않습니다. 기존 신호에 오늘 값을 소급 적용하지 않습니다."
        )

    try:
        saved_signals = list_pp_signals(client, user_id)
        if pp_journal_view in {"📝 PP Trade", "📊 PP Analytics"}:
            trades = list_pp_trades(client, user_id)
            trade_ids = [str(row.get("id")) for row in trades]
            all_adds = list_trade_adds(client, user_id, trade_ids)
            all_exits = list_trade_exits(client, user_id, trade_ids)
        else:
            trades, all_adds, all_exits = [], [], []
    except Exception as exc:
        st.error(f"PP Journal 테이블을 읽을 수 없습니다: {type(exc).__name__}: {exc}")
        st.info("V2 Supabase SQL Editor에서 `sql/v2_pp_journal_schema.sql`과 `sql/v2_pp_journal_tracking_metrics.sql`을 순서대로 실행했는지 확인하세요. 기존 Watchlist SQL을 다시 실행할 필요는 없습니다.")
        return

    if pp_journal_view == "📡 PP Tracking":
        st.markdown("#### 활성 관심종목에서 PP 신호 탐색")
        if not symbols:
            st.info("활성 관심종목이 없습니다. Watchlist에서 종목을 활성화해 주세요. 이미 저장된 신호와 거래 기록은 아래에 계속 보존됩니다.")
        c1, c2 = st.columns([1, 3])
        if c1.button("완료 일봉 PP 재스캔 및 기록", type="primary", key="v2_pp_scan"):
            st.session_state.pop("v2_pp_scan_save_result", None)
            st.session_state.pop("v2_pp_scan_save_error", None)
            if not symbols:
                valid, diagnostics, errors, elapsed = [], [], [], 0.0
            else:
                with st.spinner(f"{len(symbols)}개 활성 관심종목의 완료 일봉 PP 조건을 확인 중…"):
                    valid, diagnostics, errors, elapsed = _scan_pp_for_symbols(symbols, watchlist_rows)
            st.session_state["v2_pp_scan_results"] = valid
            st.session_state["v2_pp_scan_diagnostics"] = diagnostics
            st.session_state["v2_pp_scan_errors"] = errors
            st.session_state["v2_pp_scan_elapsed"] = elapsed
            if valid:
                try:
                    st.session_state["v2_pp_scan_save_result"] = save_pp_signals(client, user_id, valid)
                except Exception as exc:
                    st.session_state["v2_pp_scan_save_error"] = f"{type(exc).__name__}: {exc}"
            else:
                st.session_state["v2_pp_scan_save_result"] = {"inserted": 0, "duplicates": 0}
            st.rerun()
        c2.caption("완료 일봉으로 PP 조건을 계산하고 신규 신호를 즉시 기록합니다. 사용자·종목·신호일 중복은 자동으로 건너뜁니다. 장중 5분봉은 신호 탐지/저장에 사용하지 않습니다.")

        scan_results = st.session_state.get("v2_pp_scan_results", [])
        if scan_results:
            scan_view = pd.DataFrame(scan_results)
            for col in ("signal_price", "candidate_pivot", "breakout_pivot"):
                if col in scan_view:
                    scan_view[col] = scan_view[col].map(lambda value: _fmt_num(value))
            scan_view = scan_view.rename(columns={
                "ticker": "종목", "signal_date": "신호일", "signal_price": "신호 가격",
                "signal_volume": "신호일 거래량", "max_prior_down_day_volume": "직전 하락일 최대 거래량",
                "reference_ma": "참조 MA", "pattern_name": "패턴", "entry_stage": "Stage",
                "candidate_pivot": "저장 당시 Candidate Pivot", "breakout_pivot": "저장 당시 Breakout Pivot",
                "rs_rating": "RS", "momentum_score": "Momentum", "pp_quality": "PP Quality",
                "market_regime": "Market Regime", "pivot_dist_pct": "Pivot 거리 (%)",
            })
            show_cols = [col for col in ["종목", "신호일", "신호 가격", "신호일 거래량", "직전 하락일 최대 거래량", "참조 MA", "패턴", "Stage", "저장 당시 Candidate Pivot", "저장 당시 Breakout Pivot", "Pivot 거리 (%)", "RS", "Momentum", "PP Quality", "Market Regime"] if col in scan_view]
            st.dataframe(scan_view[show_cols], use_container_width=True, hide_index=True)
        elif "v2_pp_scan_results" in st.session_state:
            st.info("현재 완료 일봉 기준으로 유효 PP 신호가 없습니다. 임의로 신호를 만들거나 이전 날짜 신호로 대체하지 않았습니다.")
        scan_save_result = st.session_state.get("v2_pp_scan_save_result")
        if scan_save_result is not None:
            st.success(f"스캔 완료 · 신규 신호 {scan_save_result.get('inserted', 0)}건 기록 · 중복 {scan_save_result.get('duplicates', 0)}건 건너뜀")
        scan_save_error = st.session_state.get("v2_pp_scan_save_error")
        if scan_save_error:
            st.error(f"신호 자동 기록 실패: {scan_save_error}")
            if scan_results and st.button("스캔 결과 저장 재시도", key="v2_pp_scan_save_retry"):
                try:
                    st.session_state["v2_pp_scan_save_result"] = save_pp_signals(client, user_id, scan_results)
                    st.session_state.pop("v2_pp_scan_save_error", None)
                    st.rerun()
                except Exception as exc:
                    st.error(f"PP 신호 저장 재시도 실패: {type(exc).__name__}: {exc}")

        scan_errors = st.session_state.get("v2_pp_scan_errors", [])
        if scan_errors:
            with st.expander(f"스캔 오류 ({len(scan_errors)})", expanded=False):
                st.dataframe(pd.DataFrame(scan_errors, columns=["종목", "오류"]), use_container_width=True, hide_index=True)
        rejected = st.session_state.get("v2_pp_scan_diagnostics", [])
        if rejected:
            with st.expander(f"PP 조건 미충족/진단 ({len(rejected)}종목)", expanded=False):
                st.dataframe(pd.DataFrame(rejected), use_container_width=True, hide_index=True)

        st.markdown("#### 저장된 PP Signal · Tracking")
        if saved_signals:
            tracking_rows = []
            outcome_map = {
                (str(row.get("ticker", "")).upper(), str(row.get("signal_date", ""))[:10]): row
                for row in st.session_state.get("v2_pp_signal_outcomes", [])
            }
            for sig in saved_signals:
                key = (str(sig.get("ticker", "")).upper(), str(sig.get("signal_date", ""))[:10])
                outcome = outcome_map.get(key, {})
                tracking_rows.append({
                    "종목": sig.get("ticker"), "신호일": str(sig.get("signal_date", ""))[:10],
                    "PP 가격": sig.get("signal_price"), "Pivot 거리 (%)": sig.get("pivot_dist_pct"),
                    "RS": sig.get("rs_rating"), "Momentum": sig.get("momentum_score"),
                    "PP Quality": sig.get("pp_quality"), "Market Regime": sig.get("market_regime"),
                    "Pivot": outcome.get("reference_pivot_reached"),
                    "Breakout": outcome.get("breakout_reached"),
                    "Breakout Failure": outcome.get("breakout_failure_reached"),
                    "Failure 일": outcome.get("breakout_failure_date"),
                    "+5%": outcome.get("hit_plus_5_pct"), "+10%": outcome.get("hit_plus_10_pct"),
                    "+20%": outcome.get("hit_plus_20_pct"), "MFE (%)": outcome.get("mfe_pct"),
                    "MAE (%)": outcome.get("mae_pct"), "상태": outcome.get("outcome_status", "결과 미계산"),
                })
            tracking_frame = pd.DataFrame(tracking_rows)
            for col in ("Pivot", "Breakout", "Breakout Failure", "+5%", "+10%", "+20%"):
                tracking_frame[col] = tracking_frame[col].map(lambda v: "✓" if pd.notna(v) and bool(v) else "—")
            for col in ("PP 가격", "Pivot 거리 (%)", "MFE (%)", "MAE (%)"):
                tracking_frame[col] = tracking_frame[col].map(lambda v: _fmt_num(v) if v is not None and pd.notna(v) else "—")
            for col in ("RS", "Momentum"):
                tracking_frame[col] = tracking_frame[col].map(lambda v: _fmt_num(v, 1) if v is not None and pd.notna(v) else "—")
            st.dataframe(tracking_frame, use_container_width=True, hide_index=True)
            if st.button("후속 완료 일봉 결과 업데이트", key="v2_pp_revalue_outcomes_tracking"):
                with st.spinner("신호일 이후 완료 일봉으로 PP 후속 결과를 계산 중…"):
                    _recalculate_pp_signal_outcomes(saved_signals, analyze_pp_signal_outcome)
                st.rerun()
            st.caption("Pivot/Breakout/Failure는 저장된 기준 Pivot에 대한 후속 일봉 가격 관측입니다. 공식 ENTRY STAGE 이벤트를 대체하지 않습니다. RS·Momentum·Quality·Market Regime은 신호 저장 시 확보된 값만 표시되며, 값이 없으면 추정하지 않습니다. 값이 계속 비어 있으면 `sql/v2_pp_journal_tracking_metrics.sql` 적용 여부를 확인하세요.")
        else:
            st.caption("아직 저장된 PP 신호가 없습니다.")

    elif pp_journal_view == "📝 PP Trade":
        st.markdown("#### 최초 진입 기록")
        signal_options: dict[str, dict[str, Any] | None] = {"연결 안 함": None}
        for sig in saved_signals:
            label = f"{str(sig.get('signal_date',''))[:10]} · {sig.get('ticker')} · ${float(sig.get('signal_price') or 0):,.2f} · {str(sig.get('id',''))[:8]}"
            signal_options[label] = sig
        selected_signal_label = st.selectbox("연결할 PP Signal (선택)", list(signal_options.keys()), key="v2_pp_trade_signal_link")
        selected_signal = signal_options[selected_signal_label]
        ticker_options = list(dict.fromkeys(symbols + [str(s.get("ticker", "")) for s in saved_signals if s.get("ticker")]))
        default_ticker = str((selected_signal or {}).get("ticker") or (ticker_options[0] if ticker_options else ""))
        ticker = st.text_input("거래 티커", value=default_ticker, key=f"v2_pp_trade_ticker_{(selected_signal or {}).get('id','manual')}", help="PP Signal을 연결하면 해당 신호의 티커를 사용합니다.")
        with st.form("v2_pp_create_trade_form", clear_on_submit=False):
            a, b, c = st.columns(3)
            default_price = float((selected_signal or {}).get("signal_price") or 1.0)
            entry_price = a.number_input("최초 진입 가격", min_value=0.000001, value=max(0.000001, default_price), step=0.5, format="%.4f")
            shares = b.number_input("최초 진입 수량 (주)", min_value=0.000001, value=1.0, step=1.0, format="%.6f")
            weight = c.number_input("최초 진입 포트폴리오 비중 (%) · 선택", min_value=0.0, max_value=100.0, value=0.0, step=0.25, format="%.4f")
            d, e, f = st.columns(3)
            entry_date = d.date_input("최초 진입일", value=datetime.now(tz=LIVE_NY).date())
            stage_default = str((selected_signal or {}).get("entry_stage") or "BASE")
            stage_index = ENTRY_STAGES.index(stage_default) if stage_default in ENTRY_STAGES else 0
            entry_stage = e.selectbox("최초 진입 Stage (고정 기록)", ENTRY_STAGES, index=stage_index)
            initial_stop = f.number_input("최초 진입 당시 초기 손절선 · 선택", min_value=0.0, value=0.0, step=0.5, format="%.4f")
            entry_reasons = st.multiselect(
                "매수 근거 (선택)",
                ["RS 개선", "좋은 Base", "VCP 양호", "MA 지지", "거래량 증가", "Pivot 접근", "Market Regime 양호", "추세 구조 양호", "기타"],
                key="v2_pp_entry_reasons",
            )
            notes = st.text_area("거래 메모", max_chars=1500, placeholder="진입 이유, 셋업, 계획 등")
            create_submitted = st.form_submit_button("최초 진입 기록 저장", type="primary", use_container_width=True)
        if create_submitted:
            trade_ticker = str((selected_signal or {}).get("ticker") or ticker).strip()
            try:
                created = create_pp_trade(
                    client, user_id, ticker=trade_ticker,
                    pp_signal_id=(selected_signal or {}).get("id"),
                    initial_entry_date=entry_date, initial_entry_price=entry_price,
                    initial_shares=shares, initial_weight_pct=(weight if weight > 0 else None),
                    initial_entry_stage=entry_stage,
                    initial_stop_price=(initial_stop if initial_stop > 0 else None),
                    notes=(("[매수 근거] " + ", ".join(entry_reasons) + "\n" if entry_reasons else "") + notes)[:1500],
                )
                st.success(f"{created['ticker']} 최초 진입을 기록했습니다. ID: {str(created['id'])[:8]}")
                st.rerun()
            except Exception as exc:
                st.error(f"거래 기록 저장 실패: {type(exc).__name__}: {exc}")

        trade_summaries = []
        for trade in trades:
            tid = str(trade.get("id"))
            trade_summaries.append(summarize_trade(
                trade,
                [row for row in all_adds if str(row.get("trade_id")) == tid],
                [row for row in all_exits if str(row.get("trade_id")) == tid],
            ))
        if trade_summaries:
            st.markdown("#### 포지션 현황")
            table = pd.DataFrame(trade_summaries)
            label_status = {"OPEN": "OPEN · 보유", "PARTIAL": "PARTIAL · 부분청산", "CLOSED": "CLOSED · 전량청산"}
            table["status"] = table["status"].map(label_status)
            table = table.rename(columns={
                "ticker": "종목", "status": "상태", "initial_entry_date": "최초 진입일",
                "initial_entry_stage": "최초 진입 Stage", "initial_entry_price": "최초 진입가",
                "total_entered_shares": "총 매수 수량", "total_exited_shares": "총 청산 수량",
                "remaining_shares": "잔여 수량", "average_remaining_cost": "잔여 수량 가중평균단가",
                "realized_pnl": "실현손익 ($)", "realized_return_pct": "실현수익률 (%)",
                "r_multiple": "R 배수", "adds_count": "추가매수 횟수", "exits_count": "청산 횟수",
            })
            show_cols = ["종목", "상태", "최초 진입일", "최초 진입 Stage", "최초 진입가", "총 매수 수량", "총 청산 수량", "잔여 수량", "잔여 수량 가중평균단가", "실현손익 ($)", "실현수익률 (%)", "R 배수", "추가매수 횟수", "청산 횟수"]
            st.dataframe(table[[col for col in show_cols if col in table]], use_container_width=True, hide_index=True)

            st.markdown("#### 선택 포지션 관리 — 추가매수 / 청산 기록")
            trade_labels = {}
            trade_by_label = {}
            summary_by_id = {str(row["trade_id"]): row for row in trade_summaries}
            for trade in trades:
                summ = summary_by_id[str(trade["id"])]
                label = f"{trade['ticker']} · {summ['status']} · 최초 {summ['initial_entry_date']} · {str(trade['id'])[:8]}"
                trade_labels[label] = str(trade["id"])
                trade_by_label[label] = trade
            selected_trade_label = st.selectbox("관리할 포지션", list(trade_labels.keys()), key="v2_pp_manage_trade")
            selected_trade = trade_by_label[selected_trade_label]
            selected_id = str(selected_trade["id"])
            selected_summary = summary_by_id[selected_id]
            st.caption(
                f"{selected_trade['ticker']} · 상태 {selected_summary['status']} · "
                f"잔여 {selected_summary['remaining_shares']:.6g}주 · "
                f"가중평균 잔여 단가 {_fmt_num(selected_summary['average_remaining_cost'])}"
            )
            add_col, exit_col = st.columns(2)
            with add_col:
                st.markdown("**추가매수 기록**")
                with st.form(f"v2_pp_add_form_{selected_id}"):
                    add_date = st.date_input("추가매수일", value=datetime.now(tz=LIVE_NY).date(), key=f"v2_pp_add_date_{selected_id}")
                    add_price = st.number_input("추가매수 가격", min_value=0.000001, value=float(selected_summary["average_remaining_cost"] or selected_trade["initial_entry_price"]), step=0.5, format="%.4f", key=f"v2_pp_add_price_{selected_id}")
                    add_shares = st.number_input("추가매수 수량", min_value=0.000001, value=1.0, step=1.0, format="%.6f", key=f"v2_pp_add_shares_{selected_id}")
                    add_weight = st.number_input("추가매수 비중 (%) · 선택", min_value=0.0, max_value=100.0, value=0.0, step=0.25, format="%.4f", key=f"v2_pp_add_weight_{selected_id}")
                    add_stage = st.selectbox("추가매수 Stage", ENTRY_STAGES, key=f"v2_pp_add_stage_{selected_id}")
                    add_stop = st.number_input("추가매수 당시 초기 손절선 · 선택", min_value=0.0, value=0.0, step=0.5, format="%.4f", key=f"v2_pp_add_stop_{selected_id}")
                    add_note = st.text_input("추가매수 메모", max_chars=1000, key=f"v2_pp_add_note_{selected_id}")
                    add_submit = st.form_submit_button("추가매수 기록 저장", use_container_width=True)
                if add_submit:
                    try:
                        add_pp_trade_add(
                            client, user_id, selected_trade, entry_date=add_date,
                            entry_price=add_price, shares=add_shares,
                            portfolio_weight_pct=(add_weight if add_weight > 0 else None),
                            entry_stage=add_stage, initial_stop_price=(add_stop if add_stop > 0 else None), note=add_note,
                        )
                        st.success("추가매수를 기록했습니다.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"추가매수 저장 실패: {type(exc).__name__}: {exc}")
            with exit_col:
                st.markdown("**부분청산 / 전량청산 기록**")
                remaining_now = float(selected_summary["remaining_shares"])
                with st.form(f"v2_pp_exit_form_{selected_id}"):
                    exit_date = st.date_input("청산일", value=datetime.now(tz=LIVE_NY).date(), key=f"v2_pp_exit_date_{selected_id}")
                    exit_price = st.number_input("청산 가격", min_value=0.000001, value=float(selected_summary["average_remaining_cost"] or selected_trade["initial_entry_price"]), step=0.5, format="%.4f", key=f"v2_pp_exit_price_{selected_id}")
                    exit_shares = st.number_input("청산 수량", min_value=0.000001, max_value=max(0.000001, remaining_now), value=max(0.000001, remaining_now), step=1.0, format="%.6f", key=f"v2_pp_exit_shares_{selected_id}", disabled=remaining_now <= 0)
                    reason_labels = {
                        "돌파 실패": "BREAKOUT_FAILURE", "초기 손절": "INITIAL_STOP",
                        "추적 손절": "TRAILING_STOP", "목표가/익절": "TAKE_PROFIT",
                        "수동 청산": "MANUAL", "기타": "OTHER",
                    }
                    reason_label = st.selectbox("청산 사유", list(reason_labels.keys()), key=f"v2_pp_exit_reason_{selected_id}")
                    exit_note = st.text_input("청산 메모", max_chars=1000, key=f"v2_pp_exit_note_{selected_id}")
                    exit_submit = st.form_submit_button("청산 기록 저장", use_container_width=True, disabled=remaining_now <= 0)
                if exit_submit:
                    try:
                        add_pp_trade_exit(
                            client, user_id, selected_trade,
                            exits=[row for row in all_exits if str(row.get("trade_id")) == selected_id],
                            adds=[row for row in all_adds if str(row.get("trade_id")) == selected_id],
                            exit_date=exit_date, exit_price=exit_price, shares=exit_shares,
                            reason_code=reason_labels[reason_label], note=exit_note,
                        )
                        st.success("청산 내역을 추가했습니다. 잔여 수량이 남아 있으면 포지션은 계속 열려 있습니다.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"청산 기록 저장 실패: {type(exc).__name__}: {exc}")

            with st.expander("선택 포지션의 원본 진입/청산 내역"):
                selected_adds = [row for row in all_adds if str(row.get("trade_id")) == selected_id]
                selected_exits = [row for row in all_exits if str(row.get("trade_id")) == selected_id]
                fill_rows = [{
                    "유형": "최초 진입", "날짜": selected_trade.get("initial_entry_date"),
                    "가격": selected_trade.get("initial_entry_price"), "수량": selected_trade.get("initial_shares"),
                    "Stage/사유": selected_trade.get("initial_entry_stage"), "초기 손절선": selected_trade.get("initial_stop_price"),
                }]
                fill_rows += [{"유형": "추가매수", "날짜": row.get("entry_date"), "가격": row.get("entry_price"), "수량": row.get("shares"), "Stage/사유": row.get("entry_stage"), "초기 손절선": row.get("initial_stop_price")} for row in selected_adds]
                fill_rows += [{"유형": "청산", "날짜": row.get("exit_date"), "가격": row.get("exit_price"), "수량": row.get("shares"), "Stage/사유": row.get("reason_code"), "초기 손절선": None} for row in selected_exits]
                st.dataframe(pd.DataFrame(fill_rows), use_container_width=True, hide_index=True)
        else:
            st.info("아직 거래 기록이 없습니다. 위에서 최초 진입부터 기록해 주세요. PP 신호를 연결하지 않고 수동 거래를 기록할 수도 있습니다.")

    elif pp_journal_view == "📊 PP Analytics":
        trade_summaries = []
        for trade in trades:
            tid = str(trade.get("id"))
            trade_summaries.append(summarize_trade(
                trade,
                [row for row in all_adds if str(row.get("trade_id")) == tid],
                [row for row in all_exits if str(row.get("trade_id")) == tid],
            ))
        stats = summarize_trades(trade_summaries)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("전체 거래", stats["trade_count"])
        m2.metric("OPEN / PARTIAL", f"{stats['open_count']} / {stats['partial_count']}")
        m3.metric("CLOSED", stats["closed_count"])
        m4.metric("승률 · 종료 거래", "—" if stats["win_rate_pct"] is None else f"{stats['win_rate_pct']:.1f}%")
        m5, m6, m7, m8 = st.columns(4)
        m5.metric("누적 실현손익 ($)", f"${stats['total_realized_pnl']:,.2f}")
        m6.metric("평균 승리 수익률", "—" if stats["avg_win_pct"] is None else f"{stats['avg_win_pct']:+.2f}%")
        m7.metric("평균 손실 수익률", "—" if stats["avg_loss_pct"] is None else f"{stats['avg_loss_pct']:+.2f}%")
        m8.metric("Profit Factor", "—" if stats["profit_factor"] is None else f"{stats['profit_factor']:.2f}")
        m9, m10, m11 = st.columns(3)
        m9.metric("기대값 · 종료 거래 평균", "—" if stats["expectancy_pct"] is None else f"{stats['expectancy_pct']:+.2f}%")
        m10.metric("평균 R 배수", "—" if stats["avg_r_multiple"] is None else f"{stats['avg_r_multiple']:+.2f}R")
        m11.metric("PP Signal 누적", len(saved_signals))
        st.caption("승률·평균 승패·Profit Factor·기대값은 CLOSED 거래만 사용합니다. OPEN/PARTIAL은 성과 분모에서 제외합니다. R 배수는 해당 진입 건의 초기 손절선이 기록된 거래에서만 계산됩니다.")

        st.markdown("#### 저장 PP 신호의 후속 가격 움직임")
        if not saved_signals:
            st.caption("신호가 저장되면 완료 일봉의 이후 High/Low를 기준으로 MFE/MAE와 +5%/+10%/+20% 도달을 계산할 수 있습니다.")
        else:
            if st.button("후속 EOD 결과 재계산", key="v2_pp_revalue_outcomes"):
                with st.spinner("신호일 이후 완료 일봉으로 PP 후속 결과를 계산 중…"):
                    _recalculate_pp_signal_outcomes(saved_signals, analyze_pp_signal_outcome)
                st.rerun()

            outcomes = st.session_state.get("v2_pp_signal_outcomes", [])
            if outcomes:
                outcome_frame = pd.DataFrame(outcomes)
                outcome_frame = outcome_frame.rename(columns={
                    "ticker": "종목", "signal_date": "신호일", "signal_price": "신호 가격",
                    "candidate_pivot": "저장 당시 Candidate Pivot", "breakout_pivot": "저장 당시 Breakout Pivot",
                    "pp_quality": "PP Quality", "market_regime": "Signal 당시 Market Regime",
                    "entry_stage": "Signal 당시 Stage", "pattern_name": "Signal 당시 Pattern",
                    "candidate_pivot_touched": "Candidate Pivot 고가 도달", "reference_pivot_reached": "기준 Pivot 도달",
                    "breakout_reached": "종가 돌파 관측", "breakout_failure_reached": "돌파 실패 관측",
                    "breakout_failure_date": "실패 관측일", "hit_plus_5_pct": "+5% 도달",
                    "hit_plus_10_pct": "+10% 도달", "hit_plus_20_pct": "+20% 도달",
                    "mfe_pct": "MFE (%)", "mae_pct": "MAE (%)", "observed_bars": "후속 완료 일봉 수",
                    "outcome_status": "결과 상태",
                })
                wanted = ["종목", "신호일", "신호 가격", "Signal 당시 Pattern", "Signal 당시 Stage", "PP Quality", "Signal 당시 Market Regime", "저장 당시 Candidate Pivot", "저장 당시 Breakout Pivot", "기준 Pivot 도달", "종가 돌파 관측", "돌파 실패 관측", "실패 관측일", "+5% 도달", "+10% 도달", "+20% 도달", "MFE (%)", "MAE (%)", "후속 완료 일봉 수", "결과 상태"]
                outcome_frame = outcome_frame[[col for col in wanted if col in outcome_frame.columns]]
                for col in ("신호 가격", "저장 당시 Candidate Pivot", "저장 당시 Breakout Pivot", "MFE (%)", "MAE (%)"):
                    if col in outcome_frame:
                        outcome_frame[col] = outcome_frame[col].map(lambda v: _fmt_num(v) if v is not None and pd.notna(v) else "—")
                st.dataframe(outcome_frame, use_container_width=True, hide_index=True)
                observed = [row for row in outcomes if row.get("outcome_status") == "OBSERVED_PRICE_EXCURSION"]
                if observed:
                    for label, key in (("기준 Pivot 도달률", "reference_pivot_reached"), ("종가 돌파 관측률", "breakout_reached"), ("돌파 실패 관측률", "breakout_failure_reached"), ("+5% 도달률", "hit_plus_5_pct"), ("+10% 도달률", "hit_plus_10_pct"), ("+20% 도달률", "hit_plus_20_pct")):
                        values = [row.get(key) for row in observed if row.get(key) is not None]
                        rate = sum(bool(x) for x in values) / len(values) * 100 if values else None
                        st.metric(label, "—" if rate is None else f"{rate:.1f}%", help=f"결과가 관찰된 {len(values)}개 신호 기준. 저장 당시 후보 피봇이 없거나 후속 일봉이 없는 신호는 해당 비율의 분모에서 제외됩니다.")
                st.caption("MFE/MAE는 신호일 다음 완료 일봉부터 계산합니다. 종가 돌파·실패 비율은 저장된 기준 Pivot의 가격 관측 통계이며 공식 Stage 이벤트 통계가 아닙니다. 새 패턴이 생겨도 저장된 스냅샷 값은 바뀌지 않습니다.")
                observed_frame = pd.DataFrame([row for row in outcomes if row.get("outcome_status") == "OBSERVED_PRICE_EXCURSION"])
                if not observed_frame.empty:
                    st.markdown("#### PP Quality별 신호 후속 결과")
                    signal_meta = { (str(sig.get("ticker", "")).upper(), str(sig.get("signal_date", ""))[:10]): sig for sig in saved_signals }
                    for idx, row in observed_frame.iterrows():
                        meta = signal_meta.get((str(row.get("ticker", "")).upper(), str(row.get("signal_date", ""))[:10]), {})
                        observed_frame.loc[idx, "pp_quality"] = meta.get("pp_quality") or "미평가"
                    grouped_rows = []
                    for quality, group in observed_frame.groupby("pp_quality", dropna=False):
                        def _pct_true(field, subset):
                            vals = [v for v in subset[field].tolist() if v is not None and pd.notna(v)] if field in subset else []
                            return round(sum(bool(v) for v in vals) / len(vals) * 100, 1) if vals else np.nan
                        breakout_vals = [v for v in group["breakout_reached"].tolist() if v is not None and pd.notna(v)]
                        failure_vals = [v for v in group["breakout_failure_reached"].tolist() if v is not None and pd.notna(v)]
                        breakout_n = sum(bool(v) for v in breakout_vals)
                        failure_n = sum(bool(v) for v in failure_vals)
                        grouped_rows.append({
                            "PP Quality": quality, "관측 신호 수": len(group),
                            "Pivot 도달률 (%)": _pct_true("reference_pivot_reached", group),
                            "종가 돌파 관측률 (%)": _pct_true("breakout_reached", group),
                            "돌파 실패 관측률 (%)": _pct_true("breakout_failure_reached", group),
                            "돌파→실패 비율 (%)": round(failure_n / breakout_n * 100, 1) if breakout_n else np.nan,
                            "+5% 도달률 (%)": _pct_true("hit_plus_5_pct", group),
                            "+10% 도달률 (%)": _pct_true("hit_plus_10_pct", group),
                            "+20% 도달률 (%)": _pct_true("hit_plus_20_pct", group),
                        })
                    st.dataframe(pd.DataFrame(grouped_rows), use_container_width=True, hide_index=True)
            if trade_summaries:
                st.markdown("#### 실제 최초 진입 Stage별 성과")
                stage_rows = []
                for entry_stage, group in pd.DataFrame(trade_summaries).groupby("initial_entry_stage", dropna=False):
                    group_closed = group[group["status"].astype(str).str.upper().eq("CLOSED")]
                    returns = pd.to_numeric(group_closed.get("realized_return_pct"), errors="coerce")
                    wins = returns[returns > 0]
                    losses = returns[returns < 0]
                    pnl = pd.to_numeric(group["realized_pnl"], errors="coerce")
                    rvals = pd.to_numeric(group_closed.get("r_multiple"), errors="coerce")
                    stage_rows.append({
                        "최초 진입 Stage": entry_stage or "미기록", "Position 수": len(group),
                        "전량 종료 수": len(group_closed),
                        "승률 (%)": round((returns > 0).mean() * 100, 1) if not returns.dropna().empty else np.nan,
                        "평균 승리 (%)": round(float(wins.mean()), 2) if not wins.empty else np.nan,
                        "평균 손실 (%)": round(float(losses.mean()), 2) if not losses.empty else np.nan,
                        "평균 R": round(float(rvals.mean()), 2) if rvals.notna().any() else np.nan,
                        "누적 실현손익 ($)": round(float(pnl.sum()), 2) if pnl.notna().any() else 0.0,
                    })
                st.dataframe(pd.DataFrame(stage_rows), use_container_width=True, hide_index=True)
                st.caption("Stage 성과는 각 Position의 최초 실제 진입 Stage 기준입니다. 추가매수 Stage는 PP Trade의 개별 거래 원장에서 확인합니다.")
            outcome_errors = st.session_state.get("v2_pp_signal_outcome_errors", [])
            if outcome_errors:
                with st.expander(f"후속 데이터 조회 오류 ({len(outcome_errors)})", expanded=False):
                    st.dataframe(pd.DataFrame(outcome_errors, columns=["종목", "오류"]), use_container_width=True, hide_index=True)




def _render_quality_recovery_page(symbols: list[str], source: str) -> None:
    """Render the legacy V30 Quality Recovery Radar on the V2 watchlist/EOD layer."""
    st.subheader("🧬 Quality Recovery Radar")
    st.caption("Watchlist가 Single Source of Truth입니다. 활성 관심종목만 평가하며, 이 화면은 공식 ENTRY STAGE나 주문을 생성·변경하지 않습니다.")
    st.info("진행 단계 참고: **접근중 → 테스트 → 지지 반등** 또는 **접근중 → 이탈 → 무너짐 → 회복 시도 → 회복 확정 → 리테스트 → 리테스트 확인 → 추세 회복**")
    st.caption("120MA = 중기 추세 회복 경로 · 200MA = 장기 추세 회복 경로. 이벤트 판정 방식은 동일하며, 200MA 이벤트를 더 중요하게 봅니다.")
    st.caption("RS Engine V30.5 호환 · QQQ 대비 21/63/126/252 거래일 상대강도 · Momentum Monitor 및 다른 엔진의 공식 Stage와 분리")

    if source == "연결 안 함 (기본)":
        st.info("왼쪽에서 Yahoo Finance (EOD)를 선택하면 기존 Quality Recovery 방식으로 분석합니다. 임의의 시세나 펀더멘털 판정은 표시하지 않습니다.")
        return
    if not symbols:
        st.warning("활성 관심종목이 없습니다. Watchlist에서 종목을 추가하거나 활성화하세요.")
        return

    result_rows: list[dict[str, Any]] = []
    raw_by_symbol: dict[str, pd.DataFrame] = {}
    adjusted_by_symbol: dict[str, pd.DataFrame] = {}
    errors: list[tuple[str, str]] = []
    benchmark_bars: pd.DataFrame | None = None
    benchmark_adjusted: pd.DataFrame | None = None

    if source == "Yahoo Finance (EOD)":
        try:
            benchmark_bars = _load_bars("QQQ", source)
        except Exception as exc:
            errors.append(("QQQ benchmark", f"{type(exc).__name__}: {exc}"))
        try:
            benchmark_adjusted = _load_adjusted_close_history("QQQ")
        except Exception as exc:
            errors.append(("QQQ adjusted-close RS", f"{type(exc).__name__}: {exc}"))
    elif source == "Bundled regression data (historical)":
        st.warning("고정된 과거 가격 데이터에는 현재 연간 재무제표와 현재 RS를 결합하지 않습니다. 펀더멘털 및 RS는 미평가로 표시됩니다.")

    with st.spinner(f"{len(symbols)}개 관심종목의 Quality Recovery를 분석 중…"):
        for symbol in symbols:
            try:
                bars = _load_bars(symbol, source)
                raw_by_symbol[symbol] = bars
                fundamental = fundamentals_from_income_statement(None)
                adjusted = pd.DataFrame(columns=["Date", "Close"])
                if source == "Yahoo Finance (EOD)":
                    try:
                        fundamental = _load_quality_fundamentals(symbol)
                        if fundamental.get("fundamental_error"):
                            errors.append((f"{symbol} fundamentals", str(fundamental["fundamental_error"])))
                    except Exception as exc:
                        errors.append((f"{symbol} fundamentals", f"{type(exc).__name__}: {exc}"))
                    try:
                        adjusted = _load_adjusted_close_history(symbol)
                    except Exception as exc:
                        errors.append((f"{symbol} adjusted-close RS", f"{type(exc).__name__}: {exc}"))
                adjusted_by_symbol[symbol] = adjusted
                row = analyze_legacy_quality_recovery(
                    symbol,
                    bars,
                    fundamentals=fundamental,
                    adjusted_close=adjusted,
                    benchmark_adjusted_close=benchmark_adjusted,
                    benchmark_bars=benchmark_bars,
                )
                result_rows.append(row)
            except Exception as exc:
                errors.append((symbol, f"Technical {type(exc).__name__}: {exc}"))

    if not result_rows:
        st.error("Quality Recovery 데이터를 계산할 수 없습니다. 데이터 공급원과 일봉 이력을 확인하세요.")
        if errors:
            with st.expander(f"데이터/분석 오류 ({len(errors)})", expanded=True):
                st.dataframe(pd.DataFrame(errors, columns=["종목", "오류"]), use_container_width=True, hide_index=True)
        return

    result = pd.DataFrame(result_rows)
    display_table = quality_recovery_table(result_rows)
    st.dataframe(display_table, use_container_width=True, hide_index=True)
    st.caption("Quality Gate: EPS Growth > 0 + Sales Growth > 0 + 자동 감지된 Fundamental Damage 없음. Decline Reason은 참고용이며 매수 게이트가 아닙니다. Valuation / Analyst Target / Earnings Revision / Market Regime은 사용하지 않습니다.")
    st.caption("기존 V50 Quality Recovery와 결과 호환을 위해 'EPS Growth'는 기존 코드와 동일하게 연간 Net Income 증가율을 사용합니다(실제 주당 EPS 증가율은 아님). Fundamental 데이터가 없으면 PASS하지 않고 CHECK로 표시합니다.")

    st.markdown("### 🔎 선택 종목 상세")
    selected = st.selectbox("종목", result["ticker"].tolist(), key="quality_recovery_detail_symbol_v2")
    row = next(item for item in result_rows if item["ticker"] == selected)
    metric1, metric2, metric3, metric4 = st.columns(4)
    metric1.metric("현재가", f"${row['price']:,.2f}")
    metric2.metric("EPS Growth", f"{row['eps_growth']:+.1f}%" if pd.notna(row["eps_growth"]) else "—")
    metric3.metric("Sales Growth", f"{row['sales_growth']:+.1f}%" if pd.notna(row["sales_growth"]) else "—")
    metric4.metric("RS", f"{row['rs']:.0f}" if pd.notna(row["rs"]) else "—", f"{row['rs_delta']:+.1f}" if pd.notna(row["rs_delta"]) else None)
    st.markdown(f"**현재 상태: {row['status']}** · Quality **{'PASS' if row['quality_gate'] else 'CHECK'}**")
    left, right = st.columns(2)
    for col, label, ma_key, event_key in (
        (left, "120MA", "ma120", "e120"),
        (right, "200MA", "ma200", "e200"),
    ):
        event = row[event_key]
        distance = event.get("distance")
        delta = event.get("delta")
        with col:
            st.markdown(f"**{label}**")
            st.write(
                f"현재 ${row[ma_key]:,.2f} · 이격 {distance:+.1f}% · "
                f"이격 변화 {delta:+.1f}%p · **{event['state']}**"
                if pd.notna(distance) and pd.notna(delta)
                else f"현재 ${row[ma_key]:,.2f} · 데이터 부족"
            )
    st.write(
        f"Correction {row['correction']:+.1f}% · Decline Reason(참고) {row['decline_reason']} · "
        f"Fundamental {row['fundamental_direction']} · 훼손 판정 {row['fundamental_damage_status']}"
    )

    bars = raw_by_symbol.get(selected)
    if bars is not None and not bars.empty:
        chart_bars = bars.copy()
        chart_bars["Date"] = pd.to_datetime(chart_bars["Date"], errors="coerce")
        chart_bars["Close"] = pd.to_numeric(chart_bars["Close"], errors="coerce")
        chart_bars = chart_bars.dropna(subset=["Date", "Close"]).sort_values("Date")
        chart = pd.DataFrame({"Close": chart_bars["Close"].to_numpy()}, index=chart_bars["Date"])
        for window in (20, 50, 120, 200):
            chart[f"SMA{window}"] = chart_bars["Close"].rolling(window, min_periods=window).mean().to_numpy()
        st.line_chart(chart.tail(260), height=340)
        st.caption("차트의 Close 및 20/50/120/200일선은 완료 일봉의 기술적 가격을 사용합니다. 조정 종가는 RS 계산에만 별도로 사용합니다.")

    if errors:
        with st.expander(f"데이터/분석 오류 ({len(errors)})", expanded=False):
            st.dataframe(pd.DataFrame(errors, columns=["종목", "오류"]), use_container_width=True, hide_index=True)
            st.caption("가격 분석에 실패한 종목은 제외합니다. Yahoo 재무제표 또는 RS 조회 실패는 기술적 결과를 지우지 않으며 Quality는 CHECK로 유지합니다.")


supabase_client, auth_user = _require_v2_login()
user_id = str(auth_user.id)
try:
    watchlist_rows = list_watchlist(supabase_client, user_id)
except Exception as exc:
    st.title("Portfolio Dashboard V2")
    st.error(f"V2 관심종목 테이블을 읽을 수 없습니다: {type(exc).__name__}: {exc}")
    st.info("새 Supabase 프로젝트에서 sql/v2_watchlist_schema.sql을 실행했고, 로그인 사용자가 생성되어 있는지 확인하세요.")
    st.stop()
symbols = active_tickers(watchlist_rows)

st.sidebar.title("Portfolio Dashboard V2")
st.sidebar.caption("V2 clean data · personal watchlist · shared EOD + isolated LIVE layer")
page = st.sidebar.radio("화면", PAGES, index=0)
st.sidebar.caption(f"관심종목 {len(watchlist_rows)}개 · 분석 활성 {len(symbols)}개")
st.sidebar.caption(st.session_state.get("_v2_supabase_user_email", getattr(auth_user, "email", "")))
if st.sidebar.button("로그아웃", key="v2_logout"):
    try:
        supabase_client.auth.sign_out()
    except Exception:
        pass
    _clear_auth_session()
    st.rerun()
st.sidebar.divider()
if page == "Live Monitor":
    source = "Yahoo Finance (EOD)"
    st.sidebar.markdown("**Live Monitor 데이터**")
    st.sidebar.info("별도 5분봉 잠정 공급원 + 완료 일봉 기준선. EOD 패턴/피봇 엔진과 장중 계산을 분리합니다.")
else:
    st.sidebar.markdown("**시장 데이터**")
    source = st.sidebar.selectbox(
        "데이터 공급원",
        ["연결 안 함 (기본)", "Yahoo Finance (EOD)", "Bundled regression data (historical)"],
        index=0,
        help="미국 거래소의 가장 최근 완료 일봉만 사용합니다. 패턴/피봇은 auto_adjust=False OHLC, 다기간 수익률과 RS는 split·배당 조정 Adj Close 기준입니다. 완료 일봉이 없거나 데이터가 불완전하면 해당 종목을 표시하지 않습니다.",
    )
    if source == "Yahoo Finance (EOD)":
        st.sidebar.info("EOD 정책 확정: auto_adjust=False OHLC로 패턴/피봇, Adj Close로 다기간 수익률·RS, RVOL은 당일 완료 거래량 ÷ 직전 20개 완료 일봉 평균. 미국 동부시간 16:15 전에는 당일 봉을 제외합니다.")
    elif source == "Bundled regression data (historical)":
        st.sidebar.info("고정된 과거 검증 데이터입니다. 현재 시세가 아닙니다.")

st.title("Portfolio Dashboard V2")
st.caption("기준 명세: Functional Spec v0.12 · 기존 앱/DB와 분리 · EOD/LIVE 읽기 전용 분석 + 명시적 PP Journal 기록")

if page == "Watchlist":
    try:
        _render_watchlist_page(supabase_client, user_id, watchlist_rows)
    except Exception as exc:
        st.error(f"관심종목 화면 오류: {type(exc).__name__}: {exc}")

elif page == "Live Monitor":
    _render_live_monitor_page(supabase_client, user_id, symbols, watchlist_rows)

elif page == "Quality Recovery":
    _render_quality_recovery_page(symbols, source)

elif page == "PP Journal":
    _render_pp_journal_page(supabase_client, user_id, symbols, watchlist_rows)

elif page in {"Momentum Monitor", "Pattern Scanner"}:
    if page == "Momentum Monitor":
        _render_momentum_market_header()
    if source == "연결 안 함 (기본)":
        if page != "Momentum Monitor":
            st.subheader(page)
        st.info("왼쪽에서 데이터 공급원을 선택하면 공통 재베이스·패턴·피봇 엔진이 종목을 분석합니다. 기본 상태에서는 임의의 시세나 신호를 표시하지 않습니다.")
        st.markdown("**이 화면에서 사용하는 공통 엔진**")
        st.write("완료 일봉 → 날짜순 재베이스 상태 → 단일 주 패턴/후보 피봇 → 역사적 Breakout Pivot → EOD Stage → 거리/RVOL/모멘텀")
        st.caption("Yahoo Finance EOD 정책: 완료된 미국 일봉만 사용합니다. 장중 가격/누적 거래량은 별도 Live Monitor에서 분리해 확인합니다.")
        if page == "Momentum Monitor":
            _render_momentum_market_map(symbols, watchlist_rows)
    elif not symbols:
        st.warning("활성 관심종목이 없습니다. 화면 왼쪽의 Watchlist에서 종목을 추가하거나 비활성화 여부를 확인하세요.")
    else:
        with st.spinner(f"{len(symbols)}개 종목의 일봉과 공통 엔진 결과를 계산 중…"):
            result, raw, errors, elapsed = _run_scan(symbols, source)
        if result.empty:
            st.error("분석할 수 있는 종목이 없습니다. 데이터 공급원과 심볼을 확인하세요.")
            if page == "Momentum Monitor":
                _render_momentum_market_map(symbols, watchlist_rows)
        else:
            latest_date = result["as_of_date"].max()
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("분석 종목", len(result))
            k2.metric("최신 기준일", latest_date)
            k3.metric("재베이스 진행", int(result["rebase_active"].sum()))
            k4.metric("계산 시간", f"{elapsed:.1f}s")
            if source == "Yahoo Finance (EOD)":
                st.warning("Yahoo Finance는 외부 공급원이며 과거 데이터가 정정될 수 있습니다. 이 화면은 읽기 전용 분석이며 자동 주문을 제출하지 않습니다.")
            if result["replay_provenance"].eq("ROLLING_300_BAR_REPLAY").any():
                st.warning("Yahoo EOD는 새 auto_adjust=False OHLC 기준을 사용하므로, 과거 auto_adjust=True 스냅샷과 혼합하지 않습니다. 과거 Breakout Pivot은 현재 OHLC에서 패턴과 실제 종가 돌파를 재검증한 경우에만 복원됩니다.")
            if result["validation_status"].eq("OBSERVED_NOT_VALIDATED").any():
                st.caption("엔진 출력은 관측 결과이며, 별도 라벨링된 정답 차트와 대조되지 않은 항목은 검증 완료로 간주하지 않습니다.")

            if page == "Momentum Monitor":
                st.caption("패턴·Candidate Pivot·Breakout Pivot·20D Reference·Stage는 동일한 날짜순 엔진 결과에서 가져옵니다.")
                if source == "Yahoo Finance (EOD)":
                    _render_momentum_monitor_full(result, raw, watchlist_rows)
                else:
                    st.info("기존 스타일의 횡단면 Momentum·RS·섹터 리더십은 완료된 Yahoo Finance EOD 데이터에서 표시합니다. 과거 번들 분석에서는 공식 EOD 표만 표시합니다.")
                    _render_scan_table(result)
                _render_momentum_market_map(
                    symbols, watchlist_rows,
                    result if source == "Yahoo Finance (EOD)" else None,
                )
                if source == "Yahoo Finance (EOD)":
                    _render_technical_confirmation_detail(raw, symbols, as_of_date=latest_date)
            else:
                st.subheader("Pattern Scanner")
                st.caption("종목별 주 패턴과 활성 후보 피봇을 스캔합니다. 진단용 보조 탐지는 활성 피봇을 덮어쓰지 않습니다.")
                p1, p2 = st.columns(2)
                patterns = ["전체", *sorted(result["pattern"].dropna().astype(str).unique())]
                stages = ["전체", *sorted(result["stage"].dropna().astype(str).unique())]
                pattern_filter = p1.selectbox("주 패턴", patterns)
                stage_filter = p2.selectbox("Stage", stages)
                filtered = result.copy()
                if pattern_filter != "전체":
                    filtered = filtered[filtered["pattern"].astype(str) == pattern_filter]
                if stage_filter != "전체":
                    filtered = filtered[filtered["stage"].astype(str) == stage_filter]
                _render_scan_table(filtered, pattern_mode=True)
                st.caption(f"필터 결과 {len(filtered)} / {len(result)}개 종목")
            _render_symbol_detail(result, raw)

        if errors:
            with st.expander(f"데이터/분석 오류 ({len(errors)})", expanded=bool(errors)):
                st.dataframe(pd.DataFrame(errors, columns=["종목", "오류"]), use_container_width=True, hide_index=True)


elif page == "Overview":
    st.subheader("Overview")
    st.caption("시장 장중 흐름 · S&P 500 / NASDAQ / DOW · 5분봉 · 전일 종가 대비 변화")
    _render_overview_intraday_cards()
    st.info("Market Score와 Market Regime은 Momentum Monitor 상단에서 확인할 수 있습니다. 이 오버뷰는 장중 시장 시각화이며 개별 종목의 공식 Entry Stage·패턴·피봇을 변경하지 않습니다.")
    st.divider()
    st.write("V2는 새로 입력한 관심종목으로 시작합니다. 실제 보유 포지션과 거래 기록은 별도 화면에서 관리합니다.")
    st.metric("핵심 화면 연결", "Momentum + Live + PP Journal")
else:
    st.subheader(page)
    st.info("이 화면은 메뉴를 유지하고 있습니다. 기능 명세서에 따라 후속 단계에서 실제 데이터/기능을 연결합니다. 현재 화면은 아직 기능 이관이 완료되지 않았습니다.")

st.divider()
st.caption("운영 안전장치: 새 V2 전용 DB만 사용 · RLS 사용자별 분리 · 완료 일봉 EOD 분석과 장중 LIVE 분리 · 오래된 데이터는 신호 비활성 · 주문 제출 없음 · 기존 DB 데이터 미사용 · Yahoo 원천 데이터 정정 가능")
