"""Legacy V22/V24 Technical Confirmation calculations for completed daily bars.

This is a display-only confirmation layer. It never changes the canonical V2
pattern, pivot, rebase, RVOL, or Entry Stage results. The formulas are ported
from the legacy dashboard's ``v22_technical_confirmation`` helper.
"""
from __future__ import annotations

from typing import Any
import math

import numpy as np
import pandas as pd


def _finite(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _last_valid(series: pd.Series, default: float = np.nan) -> float:
    values = pd.to_numeric(pd.Series(series), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return float(values.iloc[-1]) if not values.empty else default


def _slope_pct(series: pd.Series, lookback: int = 10) -> float:
    values = pd.Series(series).dropna()
    if len(values) < lookback + 1:
        return 0.0  # legacy v171_slope_pct fallback
    start, end = float(values.iloc[-lookback - 1]), float(values.iloc[-1])
    return (end / start - 1.0) * 100.0 if start else 0.0


def _clean_completed_bars(bars: pd.DataFrame | None, as_of_date: Any = None) -> pd.DataFrame:
    """Sanitize a caller-supplied EOD frame without fetching or adding bars."""
    columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
    if not isinstance(bars, pd.DataFrame) or bars.empty:
        return pd.DataFrame(columns=columns)
    frame = bars.copy()
    if "Date" in frame.columns:
        dates = pd.DatetimeIndex(pd.to_datetime(frame["Date"], errors="coerce"))
    else:
        dates = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="coerce"))
    if dates.tz is not None:
        dates = dates.tz_convert("America/New_York").tz_localize(None)
    frame["Date"] = dates.normalize()
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col not in frame.columns:
            frame[col] = 0.0 if col == "Volume" else np.nan
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["High", "Low", "Close"]).copy()
    frame = frame[frame["Close"] > 0].copy()
    frame["Volume"] = frame["Volume"].fillna(0.0).clip(lower=0)
    frame = frame.dropna(subset=["Date"]).sort_values("Date", kind="stable")
    # Same duplicate policy as the legacy helper: retain the last normalized row.
    frame = frame.drop_duplicates("Date", keep="last")
    if as_of_date is not None and not pd.isna(as_of_date):
        cutoff = pd.Timestamp(as_of_date).normalize()
        frame = frame[frame["Date"] <= cutoff]
    return frame[columns].reset_index(drop=True)


def calculate_technical_confirmation(
    bars: pd.DataFrame | None, *, as_of_date: Any = None
) -> dict[str, Any]:
    """Calculate legacy MA/RSI/MACD/VFI/BB confirmation on completed EOD bars.

    Requires at least 60 usable daily bars, exactly as the legacy reference.
    Data insufficiency returns explicit N/A metrics rather than estimates.
    """
    out: dict[str, Any] = {
        "price": np.nan,
        "ma20": np.nan, "ma50": np.nan,
        "price_vs_ma20_pct": np.nan, "price_vs_ma50_pct": np.nan,
        "ma20_vs_ma50_pct": np.nan, "ma20_slope": np.nan, "ma50_slope": np.nan,
        "ma_spread_prev_pct": np.nan, "ma_spread_delta_pct": np.nan,
        "ma_aligned": False, "ma_status": "⚪ N/A",
        "rsi": np.nan, "rsi_prev": np.nan, "rsi_delta": np.nan, "rsi_trend": "⚪ N/A",
        "macd": np.nan, "macd_signal": np.nan, "macd_hist": np.nan,
        "macd_hist_prev": np.nan, "macd_hist_delta": np.nan, "macd_hist_trend": "⚪ N/A",
        "vfi": np.nan, "vfi_prev": np.nan, "vfi_delta": np.nan, "vfi_trend": "⚪ N/A",
        "bb_width": np.nan, "bb_width_prev": np.nan, "bb_width_delta": np.nan, "bb_trend": "⚪ N/A",
        "technical_score": 0, "technical_reasons": [], "technical_status": "⚪ N/A",
        "data_status": "DATA_INSUFFICIENT", "as_of_date": None,
    }
    x = _clean_completed_bars(bars, as_of_date=as_of_date)
    if x.empty or len(x) < 60:
        return out
    x = x.set_index("Date", drop=True)
    x.index.name = "Date"
    c = x["Close"].astype(float)
    v = x["Volume"].astype(float).clip(lower=0)
    if c.dropna().shape[0] < 60:
        return out

    ma20s = c.rolling(20, min_periods=20).mean()
    ma50s = c.rolling(50, min_periods=50).mean()
    ma20, ma50, price = _last_valid(ma20s), _last_valid(ma50s), _last_valid(c)
    if not (_finite(ma20) and _finite(ma50) and _finite(price)):
        return out

    out.update({
        "price": price, "ma20": ma20, "ma50": ma50,
        "price_vs_ma20_pct": (price / ma20 - 1) * 100 if ma20 else np.nan,
        "price_vs_ma50_pct": (price / ma50 - 1) * 100 if ma50 else np.nan,
        "ma20_vs_ma50_pct": (ma20 / ma50 - 1) * 100 if ma50 else np.nan,
        "ma20_slope": _slope_pct(ma20s, 10), "ma50_slope": _slope_pct(ma50s, 10),
        "as_of_date": x.index[-1].date().isoformat(),
    })
    valid_ma20, valid_ma50 = ma20s.dropna(), ma50s.dropna()
    if len(valid_ma20) >= 2 and len(valid_ma50) >= 2:
        prev20, prev50 = float(valid_ma20.iloc[-2]), float(valid_ma50.iloc[-2])
        spread_prev = (prev20 / prev50 - 1) * 100 if prev50 else np.nan
        out["ma_spread_prev_pct"] = spread_prev
        out["ma_spread_delta_pct"] = out["ma20_vs_ma50_pct"] - spread_prev if _finite(spread_prev) else np.nan

    aligned = bool(price > ma20 > ma50 and out["ma20_slope"] > 0 and out["ma50_slope"] >= 0)
    out["ma_aligned"] = aligned
    out["ma_status"] = "🟢 ALIGNED" if aligned else ("🟡 MIXED" if price > ma50 else "🔴 WEAK")

    # RSI(14), Wilder-style; preserve legacy no-loss convention of RSI=100.
    delta = c.diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = (100 - (100 / (1 + rs))).replace([np.inf, -np.inf], np.nan)
    rsi = rsi.fillna(100.0).clip(0, 100)
    out["rsi"] = _last_valid(rsi)
    if len(rsi.dropna()) >= 2:
        out["rsi_prev"] = float(rsi.dropna().iloc[-2])
        out["rsi_delta"] = out["rsi"] - out["rsi_prev"]

    # MACD 12/26/9.
    ema12 = c.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = c.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    hist = macd - signal
    out["macd"] = _last_valid(macd)
    out["macd_signal"] = _last_valid(signal)
    out["macd_hist"] = _last_valid(hist)
    if len(hist.dropna()) >= 2:
        out["macd_hist_prev"] = float(hist.dropna().iloc[-2])
        out["macd_hist_delta"] = out["macd_hist"] - out["macd_hist_prev"]

    # Legacy VFI-style transparent money-flow approximation and fallbacks.
    tp = (x["High"].astype(float) + x["Low"].astype(float) + c) / 3
    log_change = np.log(tp.replace(0, np.nan)).diff()
    vol_ma = v.rolling(50, min_periods=50).mean()
    cutoff = 0.2 * log_change.rolling(30, min_periods=30).std().fillna(0)
    flow = pd.Series(np.where(log_change > cutoff, v, np.where(log_change < -cutoff, -v, 0.0)), index=x.index)
    vfi = flow.rolling(130, min_periods=130).sum() / vol_ma.rolling(130, min_periods=130).sum() * 100
    if not _finite(_last_valid(vfi)):
        vfi = flow.rolling(60, min_periods=60).sum() / vol_ma.rolling(60, min_periods=60).sum() * 100
    if not _finite(_last_valid(vfi)):
        vfi = flow.rolling(30, min_periods=30).sum() / vol_ma.rolling(30, min_periods=30).sum() * 100
    out["vfi"] = _last_valid(vfi)
    if len(vfi.dropna()) >= 2:
        out["vfi_prev"] = float(vfi.dropna().iloc[-2])
        out["vfi_delta"] = out["vfi"] - out["vfi_prev"]

    # Bollinger Band Width (20, 2 sigma), normalized to middle band.
    mid = c.rolling(20, min_periods=20).mean()
    sd = c.rolling(20, min_periods=20).std(ddof=0)
    bbw = (4 * sd / mid * 100).replace([np.inf, -np.inf], np.nan)
    out["bb_width"] = _last_valid(bbw)
    if len(bbw.dropna()) >= 2:
        out["bb_width_prev"] = float(bbw.dropna().iloc[-2])
        out["bb_width_delta"] = out["bb_width"] - out["bb_width_prev"]

    def trend_label(series: pd.Series, higher_is_better: bool = True) -> tuple[str, int]:
        values = pd.to_numeric(pd.Series(series), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if len(values) < 5:
            return "⚪ N/A", 0
        now, old = float(values.iloc[-1]), float(values.iloc[-5])
        change = now - old
        scale = max(abs(old) * 0.01, 0.10)
        if abs(change) <= scale:
            return "→ NEUTRAL", 0
        direction = 1 if change > 0 else -1
        if not higher_is_better:
            direction = -direction
        return ("🟢 IMPROVING" if direction > 0 else "🔴 WEAKENING"), direction

    rsi_trend, rsi_score = trend_label(rsi, True)
    macd_trend, macd_score = trend_label(hist, True)
    vfi_trend, vfi_score = trend_label(vfi, True)
    bb_trend, bb_score = trend_label(bbw, False)
    out.update({"rsi_trend": rsi_trend, "macd_hist_trend": macd_trend,
                "vfi_trend": vfi_trend, "bb_trend": bb_trend})
    ma_score = 1 if aligned else (0 if price > ma50 else -1)
    score = int(ma_score + rsi_score + macd_score + vfi_score + bb_score)
    reasons = []
    for name, label in [("MA", out["ma_status"]), ("RSI", rsi_trend), ("MACD", macd_trend), ("VFI", vfi_trend), ("BB", bb_trend)]:
        if "WEAKENING" in str(label) or "WEAK" in str(label):
            reasons.append(f"{name} 약화")
        elif "IMPROVING" in str(label) or "ALIGNED" in str(label):
            reasons.append(f"{name} 개선")
        else:
            reasons.append(f"{name} 중립")
    out["technical_score"] = score
    out["technical_reasons"] = reasons
    out["technical_status"] = "🟢 CONFIRMING" if score >= 3 else ("🔴 WEAKENING" if score <= -2 else "🟡 MIXED")
    out["data_status"] = "OK"
    return out


def korean_ma_status(status: Any) -> str:
    text = str(status or "")
    if "ALIGNED" in text:
        return "🟢 정렬 양호"
    if "MIXED" in text:
        return "🟡 혼합"
    if "WEAK" in text:
        return "🔴 약세"
    return "⚪ 데이터 부족"


def korean_trend_status(status: Any) -> str:
    text = str(status or "")
    if "IMPROVING" in text:
        return "🟢 개선"
    if "WEAKENING" in text:
        return "🔴 약화"
    if "NEUTRAL" in text:
        return "→ 중립"
    return "⚪ 데이터 부족"


def korean_technical_status(status: Any) -> str:
    text = str(status or "")
    if "CONFIRMING" in text:
        return "🟢 확인 강화"
    if "WEAKENING" in text:
        return "🔴 약화"
    if "MIXED" in text:
        return "🟡 혼합"
    return "⚪ 데이터 부족"
