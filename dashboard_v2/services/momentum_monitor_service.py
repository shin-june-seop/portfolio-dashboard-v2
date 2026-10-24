"""Legacy-style Momentum Monitor display calculations for Portfolio Dashboard V2.

This module ports the V50.53 Momentum / Sector Leadership *display analytics*.
The V2 chronological EOD screening result remains the sole source of official
pattern, candidate pivot, breakout pivot, stage, rebase, Pocket Pivot, and EOD
RVOL. Any legacy-style base/readiness/indicator values here are descriptive
columns only and never overwrite those official values.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence
import math
import html

import numpy as np
import pandas as pd

from ..engines.data_validation import atr_wilder, normalize_ohlcv
from ..engines.entry_quality_engine import score_entry_quality, apply_price_extension_gate
from ..engines.risk_engine import compute_initial_risk
from .quality_recovery_service import canonical_legacy_rs

LEGACY_SECTOR_OPTIONS = [
    "AI / Big Tech", "Software / Cybersecurity", "Semiconductor", "Memory / Storage",
    "Networking / Optical", "Power / Data Center", "Cloud / Internet", "Healthcare / Pharma",
    "Financial", "Consumer", "Industrial / Defense", "Energy", "Space", "ETF / Index", "Other", "미분류",
]

# These are legacy display defaults only. The user's saved Watchlist sector is
# never rewritten by this module.
LEGACY_DEFAULT_SECTORS = {
    "AAPL": "AI / Big Tech", "AMZN": "AI / Big Tech", "MSFT": "AI / Big Tech",
    "META": "AI / Big Tech", "PLTR": "AI / Big Tech", "GOOG": "AI / Big Tech",
    "AVGO": "Semiconductor", "TSM": "Semiconductor", "INTC": "Semiconductor",
    "LRCX": "Semiconductor", "NVDA": "Semiconductor",
    "MU": "Memory / Storage", "SNDK": "Memory / Storage", "STX": "Memory / Storage",
    "ANET": "Networking / Optical", "COHR": "Networking / Optical",
    "MRVL": "Networking / Optical", "LITE": "Networking / Optical",
    "BE": "Power / Data Center", "GEV": "Power / Data Center", "PWR": "Power / Data Center",
    "VRT": "Power / Data Center", "ETN": "Power / Data Center",
    "NFLX": "Cloud / Internet", "NOW": "Software / Cybersecurity", "CRWD": "Software / Cybersecurity",
    "HWM": "Industrial / Defense", "SPGI": "Financial", "BRK-B": "Financial",
    "LLY": "Healthcare / Pharma", "ABBV": "Healthcare / Pharma",
    "SCHD": "ETF / Index", "SOXX": "ETF / Index", "SPY": "ETF / Index", "QQQ": "ETF / Index",
    "SPCX": "Space", "ASTS": "Space", "RKLB": "Space",
    "ASML": "Semiconductor", "AMAT": "Semiconductor", "AMD": "Semiconductor",
    "COST": "Consumer", "WMT": "Consumer", "KO": "Consumer", "MELI": "Consumer", "TSLA": "Consumer",
    "JPM": "Financial", "MA": "Financial", "V": "Financial",
    "PANW": "Software / Cybersecurity", "IBM": "AI / Big Tech", "DELL": "AI / Big Tech",
    "NBIS": "Cloud / Internet", "CGNX": "Industrial / Defense", "XOM": "Energy",
}

FALLBACK_MOMENTUM_UNIVERSE = (
    "QQQ", "SPY", "IWM", "DIA", "XLK", "XLF", "XLI", "XLE", "XLV", "XLC", "XLY", "XLP", "XLU",
    "SOXX", "IGV",
)


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _date_frame(bars: pd.DataFrame | None) -> pd.DataFrame:
    """Return a cleaned completed daily frame indexed by exchange-local dates."""
    if not isinstance(bars, pd.DataFrame) or bars.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume", "Adj Close"])
    frame = bars.copy()
    if "Date" in frame.columns:
        dates = pd.to_datetime(frame["Date"], errors="coerce")
    else:
        dates = pd.to_datetime(frame.index, errors="coerce")
    if getattr(dates, "tz", None) is not None:
        dates = dates.tz_convert("America/New_York").tz_localize(None)
    frame["_date"] = pd.DatetimeIndex(dates).normalize()
    required = ("Open", "High", "Low", "Close")
    for col in (*required, "Volume", "Adj Close"):
        if col not in frame.columns:
            frame[col] = np.nan if col != "Volume" else 0.0
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["_date", *required]).copy()
    frame = frame[(frame["Close"] > 0) & (frame["High"] > 0) & (frame["Low"] > 0)]
    frame["Volume"] = frame["Volume"].fillna(0).clip(lower=0)
    frame = frame.sort_values("_date", kind="stable").drop_duplicates("_date", keep="last")
    frame = frame.set_index("_date", drop=True)
    frame.index.name = "Date"
    return frame[["Open", "High", "Low", "Close", "Volume", "Adj Close"]]


def _adjusted_close_series(bars: pd.DataFrame | None) -> pd.Series:
    frame = _date_frame(bars)
    if frame.empty or "Adj Close" not in frame.columns:
        return pd.Series(dtype=float)
    values = pd.to_numeric(frame["Adj Close"], errors="coerce")
    # No fallback to OHLC Close: return/momentum/RS policy requires adjusted close.
    values = values.where(values > 0).dropna()
    values.index = pd.DatetimeIndex(values.index).normalize()
    return values[~values.index.duplicated(keep="last")].sort_index()


def resolve_watchlist_sectors(watchlist_rows: Sequence[Mapping[str, Any]] | None) -> dict[str, str]:
    """Resolve display-sector labels while preserving any custom user value."""
    out: dict[str, str] = {}
    for row in watchlist_rows or []:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        raw = str(row.get("sector") or "").strip()
        if not raw or raw == "미분류":
            raw = LEGACY_DEFAULT_SECTORS.get(ticker, "미분류")
        out[ticker] = raw
    return out


def _rank_momentum_at(panel: pd.DataFrame, position: int, benchmark_symbols: Sequence[str], targets: Sequence[str]) -> dict[str, float]:
    windows = (21, 63, 126, 252)
    weights = (0.40, 0.30, 0.20, 0.10)
    result_parts: dict[str, list[tuple[float, float]]] = {t: [] for t in targets}
    if position < 252:
        return {t: float("nan") for t in targets}
    bench_cols = [t for t in benchmark_symbols if t in panel.columns]
    if not bench_cols:
        return {t: float("nan") for t in targets}
    for period, weight in zip(windows, weights):
        if position - period < 0:
            continue
        current = pd.to_numeric(panel.iloc[position], errors="coerce")
        prior = pd.to_numeric(panel.iloc[position - period], errors="coerce")
        returns = ((current / prior) - 1.0) * 100.0
        returns = returns.replace([np.inf, -np.inf], np.nan)
        benchmark_returns = returns.reindex(bench_cols).dropna()
        if benchmark_returns.empty:
            continue
        percentile = benchmark_returns.rank(pct=True, ascending=True) * 100.0
        for ticker in targets:
            if ticker not in panel.columns:
                continue
            own = _finite(returns.get(ticker))
            if own is None:
                continue
            if ticker in bench_cols:
                rank_value = _finite(percentile.get(ticker))
            else:
                less = float((benchmark_returns < own).sum())
                equal = float((benchmark_returns == own).sum())
                rank_value = (less + 0.5 * equal) / len(benchmark_returns) * 100.0
            if rank_value is not None:
                result_parts[ticker].append((rank_value, weight))
    out: dict[str, float] = {}
    for ticker, parts in result_parts.items():
        den = sum(weight for _, weight in parts)
        out[ticker] = round(sum(score * weight for score, weight in parts) / den, 1) if den else float("nan")
    return out


def calculate_price_momentum_scores(
    tickers: Sequence[str],
    raw_by_ticker: Mapping[str, pd.DataFrame],
    benchmark_closes: pd.DataFrame | None,
    benchmark_tickers: Sequence[str],
    *,
    as_of_date: str | pd.Timestamp,
) -> pd.DataFrame:
    """Legacy V48 cross-sectional Momentum: 1/3/6/12M percentiles 40/30/20/10.

    The ranking universe is fixed to S&P 500 + Nasdaq-100 members where available,
    otherwise the legacy 15-ETF fallback. Adding an off-universe watchlist ticker
    does not change the benchmark universe or the ranks of its members.
    """
    targets = sorted({str(t).strip().upper() for t in tickers if str(t).strip()})
    empty = pd.DataFrame(columns=["ticker", "Momentum", "Momentum 변화", "momentum_status"])
    if not targets or not isinstance(benchmark_closes, pd.DataFrame) or benchmark_closes.empty:
        return empty
    try:
        anchor = pd.Timestamp(as_of_date).normalize()
        panel = benchmark_closes.copy()
        idx = pd.to_datetime(panel.index, errors="coerce")
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_convert("America/New_York").tz_localize(None)
        panel.index = pd.DatetimeIndex(idx).normalize()
        panel = panel.loc[~panel.index.isna()].sort_index()
        panel = panel.loc[~panel.index.duplicated(keep="last")]
        panel = panel.apply(pd.to_numeric, errors="coerce")
        panel.columns = [str(c).strip().upper() for c in panel.columns]
        panel = panel.loc[:, ~pd.Index(panel.columns).duplicated()]
        panel = panel.loc[panel.index <= anchor]
        universe = tuple(dict.fromkeys(str(t).strip().upper() for t in benchmark_tickers if str(t).strip()))
        if panel.empty or panel.index[-1] != anchor or len(panel) < 253:
            return pd.DataFrame([{"ticker": t, "Momentum": np.nan, "Momentum 변화": np.nan, "momentum_status": "DATA UNAVAILABLE"} for t in targets])
        # Target names use their own V2 provider-adjusted history for consistency.
        for ticker in targets:
            series = _adjusted_close_series(raw_by_ticker.get(ticker))
            if series.empty or series.index.max() != anchor or len(series.loc[series.index <= anchor]) < 253:
                if ticker not in panel.columns:
                    panel[ticker] = np.nan
                continue
            panel[ticker] = series.reindex(panel.index)
        current_position = len(panel) - 1
        current = _rank_momentum_at(panel, current_position, universe, targets)
        previous = _rank_momentum_at(panel, current_position - 1, universe, targets)
        rows = []
        for ticker in targets:
            cv = _finite(current.get(ticker))
            pv = _finite(previous.get(ticker))
            rows.append({
                "ticker": ticker,
                "Momentum": cv if cv is not None else np.nan,
                "Momentum 변화": (cv - pv) if cv is not None and pv is not None else np.nan,
                "momentum_status": "AVAILABLE" if cv is not None else "INSUFFICIENT_ADJUSTED_HISTORY",
            })
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame([{"ticker": t, "Momentum": np.nan, "Momentum 변화": np.nan, "momentum_status": "CALCULATION_FAILED"} for t in targets])


def _base_score(history: pd.DataFrame) -> tuple[float, dict[str, float]]:
    """Legacy V17 quantified Base score (0–10), informational only."""
    h = _date_frame(history)
    if len(h) < 60:
        return float("nan"), {"drawdown": np.nan, "vol_contract": np.nan, "volume_dryup": np.nan, "ma50_gap": np.nan}
    c, v = h["Close"].astype(float), h["Volume"].astype(float)
    ma50 = c.rolling(50, min_periods=50).mean()
    hi20, price = float(c.tail(20).max()), float(c.iloc[-1])
    dd = (price / hi20 - 1) * 100 if hi20 else np.nan
    ma50_last = _finite(ma50.iloc[-1])
    ma50_gap = (price / ma50_last - 1) * 100 if ma50_last else np.nan
    ret = c.pct_change()
    vol10 = float(ret.tail(10).std()) if ret.dropna().shape[0] >= 10 else np.nan
    prior = ret.iloc[-30:-10]
    vol_prev = float(prior.std()) if prior.dropna().shape[0] >= 10 else np.nan
    vol_contract = vol10 / vol_prev if pd.notna(vol10) and pd.notna(vol_prev) and vol_prev > 0 else np.nan
    avg10, avg50 = float(v.tail(10).mean()), float(v.tail(50).mean())
    volume_dryup = avg10 / avg50 if avg50 > 0 else np.nan
    score = 0
    if -12 <= dd <= -2:
        score += 3
    elif -20 <= dd < -12 or -2 < dd <= 0:
        score += 2
    elif -25 <= dd < -20:
        score += 1
    if pd.notna(vol_contract):
        score += 3 if vol_contract <= 0.75 else 2 if vol_contract <= 1.0 else 1 if vol_contract <= 1.20 else 0
    if pd.notna(volume_dryup):
        score += 2 if volume_dryup <= 0.75 else 1 if volume_dryup <= 0.95 else 0
    if pd.notna(ma50_gap):
        score += 2 if 0 <= ma50_gap <= 8 else 1 if (-3 <= ma50_gap < 0 or 8 < ma50_gap <= 12) else 0
    return float(min(score, 10)), {"drawdown": dd, "vol_contract": vol_contract, "volume_dryup": volume_dryup, "ma50_gap": ma50_gap}


def _slope_pct(series: pd.Series, lookback: int = 20) -> float:
    values = pd.Series(series, dtype=float).dropna()
    if len(values) < lookback + 1:
        return float("nan")
    start, end = _finite(values.iloc[-lookback - 1]), _finite(values.iloc[-1])
    return (end / start - 1) * 100 if start else float("nan")


def _trend_template(history: pd.DataFrame, rs_value: float) -> dict[str, Any]:
    """Legacy Minervini-style 8-condition Trend Template (display filter only)."""
    h = _date_frame(history)
    result = {"score": 0, "total": 8, "fails": ["데이터 부족"], "passed": False}
    if len(h) < 252:
        result["fails"] = ["데이터 부족(252거래일 미만)"]
        return result
    c, high, low = h["Close"].astype(float), h["High"].astype(float), h["Low"].astype(float)
    price = float(c.iloc[-1])
    ma50 = float(c.tail(50).mean())
    ma150 = float(c.tail(150).mean())
    ma200s = c.rolling(200, min_periods=200).mean()
    ma200 = _finite(ma200s.iloc[-1])
    ma200_slope = _slope_pct(ma200s, 20)
    year = h.tail(252)
    high_52w, low_52w = float(year["High"].max()), float(year["Low"].min())
    checks = [
        ("가격 > 150일선 & 200일선", ma200 is not None and price > ma150 and price > ma200),
        ("150일선 > 200일선", ma200 is not None and ma150 > ma200),
        ("200일선 상승 추세", pd.notna(ma200_slope) and ma200_slope > 0),
        ("50일선 > 150일선 & 200일선", ma200 is not None and ma50 > ma150 and ma50 > ma200),
        ("가격 > 50일선", price > ma50),
        ("52주 저점 대비 +30% 이상", low_52w > 0 and price >= low_52w * 1.30),
        ("52주 고점 대비 -25% 이내", high_52w > 0 and price >= high_52w * 0.75),
        ("RS Proxy ≥ 70", pd.notna(rs_value) and float(rs_value) >= 70),
    ]
    fails = [label for label, ok in checks if not ok]
    result.update({"score": 8 - len(fails), "total": 8, "fails": fails, "passed": not fails,
                   "ma50": ma50, "ma150": ma150, "ma200": ma200, "ma200_slope": ma200_slope,
                   "high_52w": high_52w, "low_52w": low_52w})
    return result


def _indicator_metrics(history: pd.DataFrame) -> dict[str, Any]:
    h = _date_frame(history)
    out = {"ma_status": "⚪ N/A", "rsi": np.nan, "macd_hist": np.nan, "vfi": np.nan,
           "bb_width": np.nan, "technical_status": "⚪ 데이터 부족", "sma20": np.nan, "sma50": np.nan}
    if h.empty:
        return out
    c, high, low, volume = (h[k].astype(float) for k in ("Close", "High", "Low", "Volume"))
    price = float(c.iloc[-1])
    ma20s = c.rolling(20, min_periods=20).mean()
    ma50s = c.rolling(50, min_periods=50).mean()
    ma120s = c.rolling(120, min_periods=120).mean()
    ma200s = c.rolling(200, min_periods=200).mean()
    ma20, ma50 = _finite(ma20s.iloc[-1]), _finite(ma50s.iloc[-1])
    s20, s50 = _slope_pct(ma20s, 10), _slope_pct(ma50s, 10)
    if ma20 is None or ma50 is None:
        ma_status = "⚪ N/A"
    elif price > ma20 > ma50 and pd.notna(s20) and pd.notna(s50) and s20 > 0 and s50 >= 0:
        ma_status = "🟢 ALIGNED"
    elif price > ma50:
        ma_status = "🟡 MIXED"
    else:
        ma_status = "🔴 WEAK"

    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    if (loss.iloc[-1] == 0) and pd.notna(gain.iloc[-1]):
        rsi_value = 100.0
    else:
        rsi_value = _finite(rsi_series.iloc[-1])

    ema12 = c.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = c.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    macd_series = macd - signal
    macd_value = _finite(macd_series.iloc[-1])

    tp = (high + low + c) / 3
    log_change = np.log(tp.replace(0, np.nan)).diff()
    volume_ma = volume.rolling(50, min_periods=50).mean()
    cutoff = 0.2 * log_change.rolling(30, min_periods=30).std().fillna(0)
    flow = pd.Series(np.where(log_change > cutoff, volume, np.where(log_change < -cutoff, -volume, 0.0)), index=h.index)
    vfi_series = flow.rolling(130, min_periods=130).sum() / volume_ma.rolling(130, min_periods=130).sum() * 100
    if vfi_series.dropna().empty:
        vfi_series = flow.rolling(60, min_periods=60).sum() / volume_ma.rolling(60, min_periods=60).sum() * 100
    if vfi_series.dropna().empty:
        vfi_series = flow.rolling(30, min_periods=30).sum() / volume_ma.rolling(30, min_periods=30).sum() * 100
    vfi_value = _finite(vfi_series.replace([np.inf, -np.inf], np.nan).iloc[-1])

    mid = c.rolling(20, min_periods=20).mean()
    sd = c.rolling(20, min_periods=20).std()
    bbw_series = (4 * sd / mid * 100).replace([np.inf, -np.inf], np.nan)
    bbw = _finite(bbw_series.iloc[-1])
    confirming = sum([
        ma_status == "🟢 ALIGNED",
        pd.notna(macd_value) and macd_value > 0,
        pd.notna(vfi_value) and vfi_value > 0,
        pd.notna(rsi_value) and rsi_value >= 50,
        pd.notna(bbw_series.iloc[-1]) and len(bbw_series.dropna()) >= 6 and bbw_series.iloc[-1] <= bbw_series.dropna().iloc[-6],
    ])
    weakening = sum([
        ma_status == "🔴 WEAK",
        pd.notna(macd_value) and macd_value < 0,
        pd.notna(vfi_value) and vfi_value < 0,
        pd.notna(rsi_value) and rsi_value < 45,
        pd.notna(bbw_series.iloc[-1]) and len(bbw_series.dropna()) >= 6 and bbw_series.iloc[-1] > bbw_series.dropna().iloc[-6],
    ])
    tech = "🟢 CONFIRMING" if confirming >= 3 else ("🔴 WEAKENING" if weakening >= 3 else "🟡 MIXED")
    out.update({"ma_status": ma_status, "rsi": rsi_value if rsi_value is not None else np.nan,
                "macd_hist": macd_value if macd_value is not None else np.nan,
                "vfi": vfi_value if vfi_value is not None else np.nan,
                "bb_width": bbw if bbw is not None else np.nan,
                "technical_status": tech, "sma20": ma20 if ma20 is not None else np.nan,
                "sma50": ma50 if ma50 is not None else np.nan,
                "sma120": _finite(ma120s.iloc[-1]) if len(h) >= 120 else np.nan,
                "sma200": _finite(ma200s.iloc[-1]) if len(h) >= 200 else np.nan,
                "ma20_slope": s20, "ma50_slope": s50})
    return out


def _readiness(history: pd.DataFrame, pivot_reference: float | None) -> dict[str, Any]:
    """Legacy V257 readiness scoring adapted as a descriptive-only overlay.

    The reference is V2 Candidate Pivot when one exists; otherwise it uses a
    previous-20-completed-bar high strictly as a readiness reference. This never
    creates or writes an official V2 candidate/breakout pivot or stage.
    """
    h = _date_frame(history)
    unavailable = {"score": np.nan, "status": "🟡 데이터 부족", "pivot_distance_pct": np.nan, "pivot_reference": np.nan}
    if len(h) < 60:
        return unavailable
    c, hi, lo, vol = (h[k].astype(float) for k in ("Close", "High", "Low", "Volume"))
    price = float(c.iloc[-1])
    prev_close = c.shift(1)
    tr = pd.concat([(hi - lo), (hi - prev_close).abs(), (lo - prev_close).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14, min_periods=14).mean()
    atr_prev = float(atr14.iloc[-31:-10].median()) if atr14.dropna().shape[0] >= 31 else np.nan
    atr_now = float(atr14.iloc[-10:].mean()) if atr14.dropna().shape[0] >= 10 else np.nan
    atr_ratio = atr_now / atr_prev if pd.notna(atr_now) and pd.notna(atr_prev) and atr_prev > 0 else np.nan
    ret = c.pct_change()
    ret_now = float(ret.tail(10).std()) if ret.dropna().shape[0] >= 10 else np.nan
    ret_prev = float(ret.iloc[-30:-10].std()) if ret.dropna().shape[0] >= 30 else np.nan
    ret_ratio = ret_now / ret_prev if pd.notna(ret_now) and pd.notna(ret_prev) and ret_prev > 0 else np.nan
    price_contract = 0.0
    if pd.notna(atr_ratio):
        price_contract += 6 if atr_ratio <= .65 else 5 if atr_ratio <= .80 else 4 if atr_ratio <= .95 else 2 if atr_ratio <= 1.10 else 0
    if pd.notna(ret_ratio):
        price_contract += 4 if ret_ratio <= .65 else 3 if ret_ratio <= .80 else 2 if ret_ratio <= .95 else 1 if ret_ratio <= 1.10 else 0
    mid = c.rolling(20, min_periods=20).mean()
    sd = c.rolling(20, min_periods=20).std()
    bbw = (4 * sd / mid * 100).replace([np.inf, -np.inf], np.nan)
    bbw_now = _finite(bbw.dropna().iloc[-1]) if not bbw.dropna().empty else None
    bbw_values = bbw.dropna().tail(120)
    bbw_pct = float((bbw_values <= bbw_now).mean() * 100) if bbw_now is not None and len(bbw_values) >= 30 else np.nan
    bbw_prev = _finite(bbw.dropna().iloc[-6]) if len(bbw.dropna()) >= 6 else None
    bbw_change_pct = ((bbw_now / bbw_prev) - 1) * 100 if bbw_now is not None and bbw_prev is not None and bbw_prev > 0 else np.nan
    bb_score = 0
    if pd.notna(bbw_pct):
        bb_score += 7 if bbw_pct <= 10 else 6 if bbw_pct <= 20 else 5 if bbw_pct <= 30 else 3 if bbw_pct <= 40 else 2 if bbw_pct <= 60 else 0
    persistence = 3 if pd.notna(bbw_change_pct) and bbw_change_pct <= -15 else 2 if pd.notna(bbw_change_pct) and bbw_change_pct <= -7 else 1 if pd.notna(bbw_change_pct) and bbw_change_pct < 0 else 0
    avg10, avg50 = float(vol.tail(10).mean()), float(vol.tail(50).mean())
    dry = avg10 / avg50 if avg50 > 0 else np.nan
    volume_contract = 5 if pd.notna(dry) and dry <= .70 else 4 if pd.notna(dry) and dry <= .85 else 2 if pd.notna(dry) and dry <= 1.0 else 0
    contraction = min(25.0, max(0.0, price_contract + bb_score + volume_contract + persistence))
    base10, _ = _base_score(h)
    base_quality = (base10 / 10 * 15) if pd.notna(base10) else 0

    pivot = _finite(pivot_reference)
    if pivot is None:
        pivot = float(hi.iloc[-21:-1].max()) if len(h) >= 21 else float(hi.max())
    recent_high_idx = hi.iloc[-21:-1].idxmax() if len(h) >= 21 else hi.idxmax()
    base_hi = float(hi.iloc[max(0, len(h)-60):-1].max()) if len(h) > 1 else float(hi.max())
    touch_hi = hi.iloc[max(0, len(h)-60):-1].copy()
    if recent_high_idx in touch_hi.index:
        touch_hi = touch_hi.drop(index=recent_high_idx, errors="ignore")
    touches = int(((touch_hi >= pivot * .985) & (touch_hi <= pivot * 1.015)).sum()) if pivot and pivot > 0 else 0
    try:
        pos = (len(h) - 1 - h.index.get_loc(recent_high_idx)) / 20
    except Exception:
        pos = 99
    pivot_structure = 0
    if pivot and pivot > 0:
        pivot_structure += 8 if touches >= 3 else 6 if touches == 2 else 4 if touches == 1 else 0
        pivot_structure += 4 if pos <= .35 else 2 if pos <= .70 else 0
        if base_hi <= pivot * 1.01:
            pivot_structure += 3
    pivot_dist = (price / pivot - 1) * 100 if pivot and pivot > 0 else np.nan
    proximity = 10 if pd.notna(pivot_dist) and -2 <= pivot_dist <= 0 else 9 if pd.notna(pivot_dist) and -4 <= pivot_dist < -2 else 7 if pd.notna(pivot_dist) and -6 <= pivot_dist < -4 else 4 if pd.notna(pivot_dist) and -10 <= pivot_dist < -6 else 2 if pd.notna(pivot_dist) and -15 <= pivot_dist < -10 else 0
    if pd.notna(pivot_dist) and pivot_dist > 3:
        proximity = 0
    up_vol = float(vol.where(c.diff() > 0).tail(10).mean())
    down_vol = float(vol.where(c.diff() < 0).tail(10).mean())
    vol_score = 0
    if pd.notna(up_vol) and pd.notna(down_vol) and down_vol > 0:
        ratio = up_vol / down_vol
        vol_score = 15 if ratio >= 1.30 else 11 if ratio >= 1.10 else 6 if ratio >= .90 else 0
    ema12 = c.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = c.ewm(span=26, adjust=False, min_periods=26).mean()
    macd_hist = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False, min_periods=9).mean()
    hv = macd_hist.dropna()
    macd_score = 0
    if len(hv) >= 6:
        delta, now = float(hv.iloc[-1] - hv.iloc[-6]), float(hv.iloc[-1])
        macd_score = 10 if delta > 0 and now >= 0 else 8 if delta > 0 else 5 if now >= 0 else 2
    tp = (hi + lo + c) / 3
    log_change = np.log(tp.replace(0, np.nan)).diff()
    vol_ma = vol.rolling(50, min_periods=50).mean()
    cutoff = .2 * log_change.rolling(30, min_periods=30).std().fillna(0)
    flow = pd.Series(np.where(log_change > cutoff, vol, np.where(log_change < -cutoff, -vol, 0.0)), index=h.index)
    vfi_series = flow.rolling(130, min_periods=130).sum() / vol_ma.rolling(130, min_periods=130).sum() * 100
    if vfi_series.dropna().empty:
        vfi_series = flow.rolling(60, min_periods=60).sum() / vol_ma.rolling(60, min_periods=60).sum() * 100
    if vfi_series.dropna().empty:
        vfi_series = flow.rolling(30, min_periods=30).sum() / vol_ma.rolling(30, min_periods=30).sum() * 100
    vv = vfi_series.replace([np.inf, -np.inf], np.nan).dropna()
    vfi_score = 0
    if len(vv) >= 6:
        delta, now = float(vv.iloc[-1] - vv.iloc[-6]), float(vv.iloc[-1])
        vfi_score = 10 if delta > 0 and now >= 0 else 8 if delta > 0 else 5 if now >= 0 else 2
    total = int(round(max(0, min(100, contraction + base_quality + pivot_structure + proximity + vol_score + macd_score + vfi_score))))
    if pd.isna(pivot_dist):
        status = "🟢 NEAR BREAKOUT" if total >= 65 else "🟡 DEVELOPING" if total >= 50 else "🔵 BASE BUILDING" if total >= 30 else "⚪ NOT READY"
    elif -2 <= pivot_dist <= 0 and total >= 80:
        status = "🔥 BREAKOUT READY"
    elif -4 <= pivot_dist < -2 and total >= 65:
        status = "🟢 NEAR BREAKOUT"
    elif -6 <= pivot_dist < -4 and total >= 50:
        status = "🟡 DEVELOPING"
    elif pivot_dist < -6 and total >= 50:
        status = "🟡 DEVELOPING"
    elif total >= 50:
        status = "🟡 DEVELOPING"
    elif total >= 30:
        status = "🔵 BASE BUILDING"
    else:
        status = "⚪ NOT READY"
    return {"score": total, "status": status, "pivot_distance_pct": pivot_dist,
            "pivot_reference": pivot, "pivot_reference_source": "V2_CANDIDATE" if _finite(pivot_reference) is not None else "DISPLAY_ONLY_20D_REFERENCE",
            "contraction": contraction, "base_quality": base_quality, "pivot_structure": pivot_structure,
            "proximity": proximity, "volume_structure": vol_score, "macd_flow": macd_score, "vfi_flow": vfi_score}


def _pp_guide(history: pd.DataFrame, price: float, pp_event: bool) -> dict[str, Any]:
    h = _date_frame(history)
    out = {"guide_10_low": np.nan, "guide_10_high": np.nan, "guide_50_low": np.nan, "guide_50_high": np.nan,
           "confluence_low": np.nan, "confluence_high": np.nan, "pp_status": "OUTSIDE"}
    if len(h) < 15:
        return out
    c = h["Close"].astype(float)
    ma10 = float(c.tail(10).mean())
    ma50 = float(c.tail(50).mean()) if len(c) >= 50 else np.nan
    if pd.notna(ma10):
        out["guide_10_low"], out["guide_10_high"] = ma10 * .98, ma10 * 1.08
    if pd.notna(ma50):
        out["guide_50_low"], out["guide_50_high"] = ma50 * .98, ma50 * 1.08
    lo10, hi10, lo50, hi50 = (out[k] for k in ("guide_10_low", "guide_10_high", "guide_50_low", "guide_50_high"))
    in10 = pd.notna(lo10) and lo10 <= price <= hi10
    in50 = pd.notna(lo50) and lo50 <= price <= hi50
    if pd.notna(lo50) and pd.notna(hi50) and pd.notna(lo10) and pd.notna(hi10):
        confluence_low, confluence_high = max(lo10, lo50), min(hi10, hi50)
    else:
        confluence_low, confluence_high = np.nan, np.nan
    if pd.notna(confluence_low) and pd.notna(confluence_high) and confluence_low <= confluence_high:
        out["confluence_low"], out["confluence_high"] = confluence_low, confluence_high
        in_confluence = confluence_low <= price <= confluence_high
    else:
        in_confluence = False
    out["pp_status"] = "PP EVENT" if pp_event else "CONFLUENCE" if in_confluence else "WATCH" if in10 or in50 else "OUTSIDE"
    return out


def _vcp_volume_display(history: pd.DataFrame) -> str:
    h = _date_frame(history)
    if len(h) < 60:
        return "—"
    vol = h["Volume"].astype(float)
    avg5, prior5, avg50 = float(vol.tail(5).mean()), float(vol.iloc[-10:-5].mean()), float(vol.tail(50).mean())
    ratio = avg5 / prior5 if prior5 > 0 else np.nan
    avg_ratio = avg5 / avg50 if avg50 > 0 else np.nan
    if pd.notna(ratio) and pd.notna(avg_ratio) and ratio <= .7 and avg_ratio <= .9:
        label = "🟢 VCP-LIKE"
    elif pd.notna(ratio) and ratio <= .9:
        label = "🟡 감소"
    elif pd.notna(ratio) and ratio > 1.1:
        label = "🔴 증가"
    else:
        label = "🟡 보통"
    if pd.notna(ratio):
        delta = (1 - ratio) * 100
        label += f" · {'↓' if delta >= 0 else '↑'} {abs(delta):.0f}%"
    return label


def _format_delta(value: Any) -> str:
    v = _finite(value)
    if v is None:
        return "—"
    if abs(v) < 1e-9:
        return "→ 0"
    return f"↑ {v:+.0f}" if v > 0 else f"↓ {v:+.0f}"


def _format_price(value: Any) -> str:
    v = _finite(value)
    return f"${v:,.2f}" if v is not None else "—"


def _action_for_stage(stage: str, pivot_distance: float | None) -> str:
    s = str(stage or "BASE").upper().replace("BASE + PP", "BASE+PP")
    d = pivot_distance
    if "FAILURE" in s:
        return "EXIT / REDUCE"
    if s == "BREAKOUT CONFIRMED":
        if d is not None and d >= 7:
            return "EXTENDED — 신규 진입 금지"
        if d is not None and d > 5:
            return "ADD CAUTION — 추격 주의"
        return "ADD"
    if s == "BREAKOUT HOLD":
        if d is not None and d >= 7:
            return "EXTENDED — 신규 진입 금지"
        if d is not None and d > 5:
            return "HOLD CAUTION — 추격 주의"
        return "HOLD"
    if s == "BREAKOUT":
        if d is not None and d >= 7:
            return "EXTENDED — 신규 진입 금지"
        if d is not None and d > 5:
            return "EARLY ENTRY CAUTION — 추격 주의"
        return "EARLY ENTRY (SMALL)"
    if s in {"PIVOT APPROACH", "BASE+PP"}:
        return "EARLY ENTRY (SMALL)"
    if s == "CONTINUATION":
        return "ADD"
    return "OBSERVE"


def build_momentum_monitor_frame(
    scan_result: pd.DataFrame,
    raw_by_ticker: Mapping[str, pd.DataFrame],
    watchlist_rows: Sequence[Mapping[str, Any]],
    benchmark_closes: pd.DataFrame | None,
    benchmark_tickers: Sequence[str],
    qqq_history: pd.DataFrame | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Enrich canonical V2 scan rows for the old-style monitor and sector table."""
    if scan_result is None or scan_result.empty:
        return pd.DataFrame(), pd.DataFrame()
    current = scan_result.copy()
    current["ticker"] = current["ticker"].astype(str).str.upper().str.strip()
    tickers = current["ticker"].tolist()
    momentum = calculate_price_momentum_scores(tickers, raw_by_ticker, benchmark_closes, benchmark_tickers,
                                               as_of_date=str(current["as_of_date"].max()))
    mom_map = {str(r["ticker"]).upper(): r for r in momentum.to_dict("records")} if not momentum.empty else {}
    sectors = resolve_watchlist_sectors(watchlist_rows)
    qqq_frame = _date_frame(qqq_history) if qqq_history is not None else pd.DataFrame()
    # Convert the benchmark's adjusted close to the format expected by the shared
    # legacy-compatible RS engine. This is a fallback only if a QQQ history frame
    # was not supplied (normal app path supplies qqq_history).
    if qqq_frame.empty and isinstance(benchmark_closes, pd.DataFrame) and "QQQ" in benchmark_closes.columns:
        qqq_frame = pd.DataFrame({"Date": benchmark_closes.index, "Adj Close": benchmark_closes["QQQ"].to_numpy(),
                                  "Open": benchmark_closes["QQQ"].to_numpy(), "High": benchmark_closes["QQQ"].to_numpy(),
                                  "Low": benchmark_closes["QQQ"].to_numpy(), "Close": benchmark_closes["QQQ"].to_numpy(),
                                  "Volume": 0})
    qqq_date = qqq_frame.index.max() if not qqq_frame.empty else None
    rows: list[dict[str, Any]] = []
    for _, scan_series in current.iterrows():
        scan = scan_series.to_dict()
        ticker = str(scan.get("ticker") or "").upper().strip()
        bars = raw_by_ticker.get(ticker)
        h = _date_frame(bars)
        latest_date = h.index.max() if not h.empty else None
        expected_date = pd.Timestamp(scan.get("as_of_date")).normalize()
        price = _finite(scan.get("close"))
        if not h.empty:
            # A row cannot be overlaid with a different session than its official
            # V2 EOD stage. Failing closed protects mixed-date display columns.
            if latest_date != expected_date:
                h = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume", "Adj Close"])
        mom_row = mom_map.get(ticker, {})
        mom_value = _finite(mom_row.get("Momentum"))
        mom_delta = _finite(mom_row.get("Momentum 변화"))
        rs_result = {"rs": np.nan, "rs_delta": np.nan}
        if bars is not None and not h.empty and not qqq_frame.empty and qqq_date == expected_date:
            rs_result = canonical_legacy_rs(bars, qqq_history if qqq_history is not None else qqq_frame)
        rs_value = _finite(rs_result.get("rs"))
        rs_delta = _finite(rs_result.get("rs_delta"))
        tech = _indicator_metrics(h)
        base, base_details = _base_score(h)
        candidate_pivot = _finite(scan.get("candidate_pivot"))
        breakout_pivot = _finite(scan.get("breakout_pivot"))
        candidate_distance = _finite(scan.get("candidate_distance_pct"))
        breakout_distance = _finite(scan.get("breakout_distance_pct"))
        readiness = _readiness(h, candidate_pivot)
        readiness_score = _finite(readiness.get("score"))
        readiness_status = str(readiness.get("status") or "🟡 데이터 부족")
        trend = _trend_template(h, rs_value if rs_value is not None else np.nan)
        guide = _pp_guide(h, price if price is not None else np.nan, bool(scan.get("pocket_pivot_today", False)))
        init_risk = compute_initial_risk(
            price,
            atr14=atr_wilder(normalize_ohlcv(bars), 14) if bars is not None and not h.empty else None,
            swing_low_10d=float(h["Low"].tail(10).min()) if len(h) >= 10 else None,
            sma20=_finite(h["Close"].tail(20).mean()) if len(h) >= 20 else None,
        ) if price is not None and bars is not None and not h.empty else None
        risk_pct = _finite(getattr(init_risk, "risk_pct", None)) if init_risk else None
        risk_stop = _finite(getattr(init_risk, "stop_price", None)) if init_risk else None
        atr20 = atr_wilder(normalize_ohlcv(bars), 20) if bars is not None and not h.empty else None
        turtle_risk = -((2 * atr20) / price * 100) if atr20 and price and price > 0 else None
        stage = str(scan.get("stage") or "BASE")
        quality_stage = stage.replace("BASE+PP", "BASE + PP")
        management_stage = "FAILURE" in stage or stage in {"BREAKOUT HOLD", "CONTINUATION", "EXTENDED"}
        active_pattern = bool(candidate_pivot and str(scan.get("pattern") or "").upper() not in {
            "", "—", "BASE FORMING / NO VALID PATTERN", "NONE", "NAN", "DATA INCOMPLETE"
        })
        pattern_component = (100.0 if active_pattern else 0.0) if len(h) >= 60 else None
        ma_component = {"🟢 ALIGNED": 100.0, "🟡 MIXED": 60.0, "🔴 WEAK": 20.0}.get(str(tech.get("ma_status")))
        components = {
            "pattern": pattern_component,
            "breakout_readiness": readiness_score,
            "relative_strength": rs_value,
            "momentum": mom_value,
            "ma_structure": ma_component,
        }
        quality = score_entry_quality(components, mode="EOD", stage=stage) if not management_stage else None
        quality_label = "—" if quality is None else {
            "GOOD": "🟢 GOOD", "CAUTION": "🟡 CAUTION", "WAIT": "🔴 WAIT", "DATA INCOMPLETE": "⚪ DATA INCOMPLETE"
        }.get(str(getattr(quality.label, "value", quality.label)), str(getattr(quality.label, "value", quality.label)))
        quality_score = _finite(getattr(quality, "score", None)) if quality else None
        # V2's extension gate remains a separate presentation/action guard. Initial
        # Risk and Turtle 2N are informational and never enter the score/action.
        extension_label = quality.label if quality else None
        if quality is not None:
            gated, gate_action, gate_reasons = apply_price_extension_gate(
                quality.label, candidate_distance_pct=candidate_distance, breakout_distance_pct=breakout_distance
            )
            if any("PRICE_EXTENSION_GE_7_PCT" in reason for reason in gate_reasons):
                quality_label = "🔴 WAIT (EXTENDED)"
        else:
            gated, gate_action, gate_reasons = (None, None, ())
        # Action uses V2's official stage and current pivot distances. Quality does
        # not overwrite Action, and an extension gate is used only for the display.
        distance_for_action = breakout_distance if "BREAKOUT" in stage else candidate_distance
        action = _action_for_stage(stage, distance_for_action)
        if quality is not None and gate_action is not None and str(getattr(gate_action, "value", gate_action)) == "BLOCK_NEW_ENTRY_OR_ADD":
            action = "EXTENDED — 신규 진입 금지"
        elif quality is not None and gate_action is not None and str(getattr(gate_action, "value", gate_action)) == "CAUTION":
            if stage == "BREAKOUT CONFIRMED":
                action = "ADD CAUTION — 추격 주의"
            elif stage == "BREAKOUT":
                action = "EARLY ENTRY CAUTION — 추격 주의"

        # A confirmed V2 pocket-pivot event owns PP Status; the guide zones are only
        # watch areas and do not create or imply a Pocket Pivot signal.
        pp_status = guide.get("pp_status", "OUTSIDE")
        if bool(scan.get("pocket_pivot_today", False)):
            pp_status = "PP EVENT"
        # The source is metadata only. It never calculates or replaces either pivot.
        candidate_source = scan.get("candidate_pivot_source") or (
            "V2 OFFICIAL CANDIDATE (SOURCE METADATA UNAVAILABLE)" if candidate_pivot is not None else "—"
        )
        pivot_source = scan.get("breakout_pivot_source") or scan.get("candidate_pivot_source") or (
            "V2 OFFICIAL BREAKOUT HISTORY" if breakout_pivot is not None else candidate_source
        )
        if scan.get("breakout_cycle_active") and candidate_pivot is not None and (breakout_pivot is None or abs(candidate_pivot - breakout_pivot) > 1e-9):
            next_pivot = candidate_pivot
        else:
            next_pivot = _finite(scan.get("next_pivot_candidate"))
            if next_pivot is None and candidate_pivot is not None and not bool(scan.get("breakout_cycle_active", False)):
                next_pivot = candidate_pivot
        if bool(scan.get("breakout_event", False)):
            change = f"BASE → {stage}"
        elif bool(scan.get("breakout_failure", False)):
            change = "BREAKOUT → BREAKOUT FAILURE"
        else:
            change = "—"
        risk_cell = "⚪ —" if risk_pct is None else f"{'🟢' if abs(risk_pct) <= 7 else '🟡' if abs(risk_pct) <= 12 else '🔴'} {risk_pct:+.1f}%"
        rows.append({
            "종목": ticker, "섹터": sectors.get(ticker, "미분류"), "현재가": _format_price(price),
            "_current_price": price if price is not None else np.nan,
            "_as_of_date": scan.get("as_of_date"), "Entry Stage": quality_stage,
            "Entry Quality": quality_label, "Entry Quality Score": quality_score,
            "Entry Quality Detail": " · ".join(f"{k} {v:.0f}" for k, v in components.items() if v is not None),
            "Initial Risk": risk_cell, "Initial Stop": risk_stop if risk_stop is not None else np.nan,
            "Initial Risk Basis": getattr(init_risk, "source", "—") if init_risk else "—",
            "Turtle 2N Risk": f"{turtle_risk:+.1f}%" if turtle_risk is not None else "⚪ —",
            "Action": action,
            "Momentum": mom_value if mom_value is not None else np.nan,
            "Momentum 변화": _format_delta(mom_delta), "_momentum_delta": mom_delta if mom_delta is not None else np.nan,
            "RS": round(rs_value) if rs_value is not None else np.nan,
            "RS 변화": _format_delta(rs_delta), "_rs_delta": rs_delta if rs_delta is not None else np.nan,
            "Base": f"{int(base)}/10" if pd.notna(base) else "—", "_base_score": base,
            "Pattern": str(scan.get("pattern") or "—"),
            "Pattern 상단": _format_price(candidate_pivot), "Candidate Pivot": _format_price(candidate_pivot),
            "Candidate Pivot Source": candidate_source,
            "VCP Volume": _vcp_volume_display(h),
            "PP Guide 10D": f"{_format_price(guide['guide_10_low'])}–{_format_price(guide['guide_10_high'])}" if pd.notna(guide["guide_10_low"]) else "—",
            "PP Guide 50D": f"{_format_price(guide['guide_50_low'])}–{_format_price(guide['guide_50_high'])}" if pd.notna(guide["guide_50_low"]) else "—",
            "PP Confluence": f"{_format_price(guide['confluence_low'])}–{_format_price(guide['confluence_high'])}" if pd.notna(guide["confluence_low"]) else "—",
            "PP Status": pp_status, "Trend Template": f"{trend['score']}/{trend['total']} 충족" if trend['passed'] else f"{trend['score']}/{trend['total']} 미달",
            "Trend Template 미달": ", ".join(trend.get("fails", [])) if trend.get("fails") else "—",
            "돌파 Pivot": _format_price(breakout_pivot), "Pivot Source": str(pivot_source),
            "Next Pivot": _format_price(next_pivot),
            "돌파 Pivot 거리": f"{breakout_distance:+.2f}%" if breakout_distance is not None else "—",
            "RVOL (20D)": f"{float(scan['rvol']):.2f}×" if _finite(scan.get("rvol")) is not None else "—",
            "Breakout Readiness": readiness_score if readiness_score is not None else np.nan,
            "돌파 준비도": readiness_status,
            "MA": tech.get("ma_status", "⚪ N/A"), "RSI": f"{tech['rsi']:.1f}" if pd.notna(tech.get("rsi")) else "—",
            "MACD": f"{tech['macd_hist']:+.2f}" if pd.notna(tech.get("macd_hist")) else "—",
            "VFI": f"{tech['vfi']:+.1f}" if pd.notna(tech.get("vfi")) else "—",
            "BB Width": f"{tech['bb_width']:.1f}%" if pd.notna(tech.get("bb_width")) else "—",
            "Tech": tech.get("technical_status", "⚪ 데이터 부족"), "변화": change,
            "_stage": stage, "_candidate_distance": candidate_distance if candidate_distance is not None else np.nan,
            "_breakout_distance": breakout_distance if breakout_distance is not None else np.nan,
            "_breakout_pivot": breakout_pivot if breakout_pivot is not None else np.nan,
            "_breakout_cycle_active": bool(scan.get("breakout_cycle_active", False)),
            "_pattern_score": pattern_component if pattern_component is not None else np.nan,
            "_readiness_score": readiness_score if readiness_score is not None else np.nan,
            "_rs_score": rs_value if rs_value is not None else np.nan,
            "_momentum_score": mom_value if mom_value is not None else np.nan,
            "_ma_score": ma_component if ma_component is not None else np.nan,
            "_atr14": atr_wilder(normalize_ohlcv(bars), 14) if bars is not None and not h.empty else np.nan,
            "_sma20": tech.get("sma20", np.nan), "_sma50": tech.get("sma50", np.nan),
            "_rebase_active": bool(scan.get("rebase_active", False)),
            "_breakout_event": bool(scan.get("breakout_event", False)),
            "_breakout_failure": bool(scan.get("breakout_failure", False)),
            "_pocket_pivot_today": bool(scan.get("pocket_pivot_today", False)),
            "_readiness_reference": readiness.get("pivot_reference_source"),
            "_readiness_distance": readiness.get("pivot_distance_pct"),
        })

    monitor = pd.DataFrame(rows)
    if monitor.empty:
        return monitor, pd.DataFrame()
    sector_table = build_sector_leadership(monitor)
    # Legacy watchlist group order: investment-theme buckets before custom/unclassified labels.
    order_map = {name: i for i, name in enumerate(LEGACY_SECTOR_OPTIONS)}
    monitor["_sector_order"] = monitor["섹터"].map(lambda s: order_map.get(str(s), len(order_map)))
    monitor = monitor.sort_values(["_sector_order", "종목"], kind="stable").drop(columns=["_sector_order"]).reset_index(drop=True)
    return monitor, sector_table


def _change_num(value: Any) -> float:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    if isinstance(value, (int, np.integer, float, np.floating)):
        return float(value)
    text = str(value).strip().replace("↑", "").replace("↓", "").replace("→", "").replace("+", "").strip()
    if not text or text in {"—", "-", "nan", "None"}:
        return np.nan
    try:
        return float(text)
    except ValueError:
        return np.nan


def build_sector_leadership(monitor: pd.DataFrame, min_names: int = 1) -> pd.DataFrame:
    """Port of legacy V27.3 sector leadership score and hover ticker lists."""
    if monitor is None or monitor.empty:
        return pd.DataFrame()
    x = monitor.copy()
    for c in ("RS", "Momentum", "Breakout Readiness"):
        x[c] = pd.to_numeric(x.get(c), errors="coerce")
    x["Momentum 변화값"] = x.get("_momentum_delta", x.get("Momentum 변화", pd.Series(index=x.index, dtype=object))).map(_change_num)
    x["RS 변화값"] = x.get("_rs_delta", x.get("RS 변화", pd.Series(index=x.index, dtype=object))).map(_change_num)
    x = x[x["섹터"].astype(str).ne("미분류")].copy()
    x = x[x[["RS", "Momentum", "Breakout Readiness"]].notna().any(axis=1)]
    rows: list[dict[str, Any]] = []
    for sector, group in x.groupby("섹터", sort=False):
        if len(group) < min_names:
            continue
        rs = float(group["RS"].mean()) if group["RS"].notna().any() else np.nan
        mom = float(group["Momentum"].mean()) if group["Momentum"].notna().any() else np.nan
        readiness = float(group["Breakout Readiness"].mean()) if group["Breakout Readiness"].notna().any() else np.nan
        momentum_delta = float(group["Momentum 변화값"].mean()) if group["Momentum 변화값"].notna().any() else 0.0
        rs_delta = float(group["RS 변화값"].mean()) if group["RS 변화값"].notna().any() else 0.0
        acceleration = max(0.0, min(100.0, 50.0 + ((momentum_delta + rs_delta) / 2.0) * 2.0))
        participation = float(((group["RS"] >= 70) | (group["Momentum"] >= 60)).mean() * 100)
        score = (0.30 * (rs if pd.notna(rs) else 50) + 0.25 * (mom if pd.notna(mom) else 50)
                 + 0.15 * (readiness if pd.notna(readiness) else 50) + 0.15 * acceleration + 0.15 * participation)
        label = "🟢 LEADING" if score >= 80 else "🟡 STRONG" if score >= 65 else "🟠 NEUTRAL" if score >= 50 else "🔴 WEAK"
        strong_mask = (group["RS"] >= 70) & (group["Momentum"] >= 50)
        improve_mask = (group["Momentum 변화값"] > 0) | (group["RS 변화값"] > 0)
        strong_names = sorted(group.loc[strong_mask.fillna(False), "종목"].astype(str).str.upper().tolist())
        improve_names = sorted(group.loc[improve_mask.fillna(False), "종목"].astype(str).str.upper().tolist())
        rows.append({
            "섹터": sector, "리더십": int(round(score)), "판단": label, "종목수": int(len(group)),
            "RS 평균": round(rs, 1) if pd.notna(rs) else np.nan,
            "Momentum 평균": round(mom, 1) if pd.notna(mom) else np.nan,
            "Momentum 변화": round(momentum_delta, 1) if pd.notna(momentum_delta) else np.nan,
            "RS 변화": round(rs_delta, 1) if pd.notna(rs_delta) else np.nan,
            "Readiness 평균": round(readiness, 1) if pd.notna(readiness) else np.nan,
            "강한 종목": len(strong_names), "개선 종목": len(improve_names),
            "강한 종목 목록": ", ".join(strong_names) if strong_names else "없음",
            "개선 종목 목록": ", ".join(improve_names) if improve_names else "없음",
            "_participation": participation, "_acceleration": acceleration,
        })
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["리더십", "RS 평균"], ascending=[False, False], kind="stable").reset_index(drop=True)
    return out


def sector_leadership_reason(row: Mapping[str, Any]) -> str:
    score = _finite(row.get("리더십")) or 0.0
    rs = _finite(row.get("RS 평균")) or 50.0
    mom = _finite(row.get("Momentum 평균")) or 50.0
    n = int(_finite(row.get("종목수")) or 0)
    strong = int(_finite(row.get("강한 종목")) or 0)
    improving = int(_finite(row.get("개선 종목")) or 0)
    if score >= 80:
        label = "섹터 전체 강도가 높고 주도주가 뚜렷함"
    elif score >= 65:
        label = "강한 종목이 있고 추가 주도 여부 관찰"
    elif score >= 50:
        label = "혼조 · 개별 종목 선별 필요"
    else:
        label = "섹터 전반 약세 · 신규 진입 우선순위 낮음"
    return f"{label} · RS {rs:.0f} · Momentum {mom:.0f} · 강한 종목 {strong}/{n} · 개선 {improving}개"


def trend_template_strip_html(frame: pd.DataFrame) -> str:
    """Hover strip used above each legacy-style sector table."""
    items = []
    for _, row in frame.iterrows():
        ticker = html.escape(str(row.get("종목") or "").strip())
        if not ticker:
            continue
        label = html.escape(str(row.get("Trend Template") or "—").strip())
        fails = str(row.get("Trend Template 미달") or "—").strip()
        if fails and fails not in {"—", "-", "nan", "None"}:
            lines = [html.escape(x.strip()) for x in fails.split(",") if x.strip()]
            tip = "미달 조건:<br>" + "<br>".join("• " + item for item in lines)
        else:
            tip = "8개 조건 모두 충족"
        title = html.escape(("미달 조건: " + " / ".join(x.strip() for x in fails.split(",")) if fails not in {"—", "-", "nan", "None"} else "8개 조건 모두 충족"), quote=True)
        items.append(
            f'<span class="tt-badge" title="{title}"><span class="tt-label">{ticker} {label}</span><span class="tt-tip">{tip}</span></span>'
        )
    if not items:
        return ""
    return (
        '<style>.tt-strip{display:flex;flex-wrap:wrap;gap:6px 11px;align-items:center;margin:0 0 8px 0;position:relative;overflow:visible;z-index:100;}'
        '.tt-title{font-size:11px;opacity:.62;white-space:nowrap;margin-right:2px;}'
        '.tt-badge{display:inline-block;position:relative;cursor:help;font-size:11px;line-height:1.55;white-space:nowrap;opacity:.92;}'
        '.tt-label{border-bottom:1px dotted rgba(255,255,255,.55);}'
        '.tt-tip{visibility:hidden;opacity:0;position:absolute;left:0;top:calc(100% + 6px);z-index:2147483647;display:block;min-width:240px;max-width:380px;padding:8px 10px;border:1px solid rgba(255,255,255,.22);border-radius:7px;background:#111820;color:#fff;text-align:left;white-space:normal;line-height:1.5;box-shadow:0 8px 22px rgba(0,0,0,.48);pointer-events:none;transition:opacity .10s ease,visibility .10s ease;}'
        '.tt-badge:hover .tt-tip,.tt-badge:focus .tt-tip{visibility:visible;opacity:1;}</style>'
        '<div class="tt-strip"><span class="tt-title">Trend Template 상세 · 마우스를 올려보세요</span>' + "".join(items) + "</div>"
    )


def sector_leadership_table_html(sectors: pd.DataFrame) -> str:
    """Isolated iframe table matching legacy hover ticker tooltips."""
    if sectors is None or sectors.empty:
        return ""
    headers = ["섹터", "리더십", "판단", "종목수", "RS 평균", "Momentum 평균", "Momentum 변화", "RS 변화", "Readiness 평균", "강한 종목", "개선 종목"]
    header_html = "".join(f"<th>{html.escape(col)}</th>" for col in headers)
    body: list[str] = []
    def fmt(value: Any, digits: int = 0) -> str:
        v = _finite(value)
        return "—" if v is None else f"{v:.{digits}f}"
    for _, row in sectors.iterrows():
        base = [row.get("섹터", ""), row.get("리더십", ""), row.get("판단", ""), row.get("종목수", ""),
                fmt(row.get("RS 평균")), fmt(row.get("Momentum 평균")), fmt(row.get("Momentum 변화")),
                fmt(row.get("RS 변화")), fmt(row.get("Readiness 평균"), 1)]
        cells = [f"<td>{html.escape(str(value))}</td>" for value in base]
        for count_key, list_key in (("강한 종목", "강한 종목 목록"), ("개선 종목", "개선 종목 목록")):
            count = int(_finite(row.get(count_key)) or 0)
            names = html.escape(str(row.get(list_key) or "없음"))
            cells.append(f'<td class="tip-cell"><span class="qmark">{count} ❓</span><span class="tooltip">{html.escape("강한 종목" if count_key=="강한 종목" else "개선 종목")}: {names}</span></td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        '*{box-sizing:border-box}body{margin:0;background:#0b0d0f;color:#e8edf3;font:13px/1.45 Arial,sans-serif;}'
        '.wrap{width:100%;overflow:visible;padding-bottom:8px}table{width:100%;border-collapse:collapse;table-layout:auto;}'
        'th{background:#171a1d;color:#f1f4f7;font-weight:650;text-align:left;padding:8px 10px;border-bottom:1px solid #30363b;white-space:nowrap;}'
        'td{padding:8px 10px;border-bottom:1px solid #25292d;white-space:nowrap;}'
        'tbody tr:hover{background:#15191d}.tip-cell{position:relative;cursor:help;text-decoration:underline dotted;text-underline-offset:3px;}'
        '.qmark{color:#e6edf4}.tooltip{visibility:hidden;opacity:0;position:absolute;left:8px;top:calc(100% + 3px);z-index:9999;display:block;'
        'background:#222;color:#fff;border:1px solid rgba(255,255,255,.25);border-radius:7px;padding:8px 10px;font-size:12px;line-height:1.45;'
        'white-space:normal;min-width:150px;max-width:320px;text-align:left;box-shadow:0 5px 18px rgba(0,0,0,.35);pointer-events:none;transition:opacity .12s ease;}'
        '.tip-cell:hover .tooltip{visibility:visible;opacity:1}.hint{font-size:11px;opacity:.65;margin-top:5px}'
        '</style></head><body><div class="wrap"><table><thead><tr>' + header_html + '</tr></thead><tbody>' + "".join(body) +
        '</tbody></table><div class="hint">※ ❓ 위에 마우스를 올리면 실제 티커가 표시됩니다.</div></div></body></html>'
    )
