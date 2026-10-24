"""Legacy-compatible watchlist Market Map for the V2 Momentum Monitor.

The map uses 5-minute regular-session Yahoo bars while the US market is open,
and the latest completed session close outside regular hours. It is purely a
visual overlay: it never updates the EOD technical, pattern, pivot, stage, or
PP journal state.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go

NY = ZoneInfo("America/New_York")
LIVE_MAX_AGE_MINUTES = 15
CLOSE_BAR_MAX_GAP_MINUTES = 25


def _empty_quote(ticker: str, error: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "price": np.nan,
        "daily_pct": np.nan,
        "quote_mode": "UNAVAILABLE",
        "quote_time": "",
        "eod_date": "",
        "error": error,
    }


def normalize_market_map_bars(raw: pd.DataFrame | None) -> pd.DataFrame:
    """Normalize Yahoo OHLCV and retain regular-session bars only."""
    if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    x = raw.copy()
    if isinstance(x.columns, pd.MultiIndex):
        # Ticker.history normally returns flat columns; support yf.download too.
        preferred = []
        for col in x.columns:
            parts = [str(p).strip() for p in col]
            for part in parts:
                if part.lower() in {"open", "high", "low", "close", "volume"}:
                    preferred.append((col, part.title()))
                    break
        if preferred:
            x = x.loc[:, [pair[0] for pair in preferred]].copy()
            x.columns = [pair[1] for pair in preferred]
    rename = {}
    for col in x.columns:
        name = str(col).strip().lower()
        if name in {"open", "high", "low", "close", "volume"}:
            rename[col] = name.title()
        elif name == "adj close":
            rename[col] = "Close"
    x = x.rename(columns=rename)
    if "Close" not in x.columns:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    for col in ("Open", "High", "Low"):
        if col not in x.columns:
            x[col] = pd.to_numeric(x["Close"], errors="coerce")
    if "Volume" not in x.columns:
        x["Volume"] = np.nan
    index = pd.DatetimeIndex(pd.to_datetime(x.index, errors="coerce"))
    valid = ~index.isna()
    x = x.loc[valid].copy()
    index = index[valid]
    if index.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    if index.tz is None:
        index = index.tz_localize(NY)
    else:
        index = index.tz_convert(NY)
    x.index = index
    for col in ("Open", "High", "Low", "Close", "Volume"):
        x[col] = pd.to_numeric(x[col], errors="coerce")
    x = x.sort_index().loc[lambda frame: ~frame.index.duplicated(keep="last")].copy()
    local_time = x.index.time
    x = x.loc[
        (local_time >= pd.Timestamp("09:30").time())
        & (local_time < pd.Timestamp("16:00").time())
    ].copy()
    x = x.loc[np.isfinite(x[["Open", "High", "Low", "Close"]].to_numpy(dtype=float)).all(axis=1)]
    x = x.loc[(x[["Open", "High", "Low", "Close"]] > 0).all(axis=1)]
    x["Volume"] = x["Volume"].where(np.isfinite(x["Volume"]) & (x["Volume"] >= 0), np.nan)
    return x


def _session_closes(bars: pd.DataFrame) -> pd.Series:
    if bars is None or bars.empty:
        return pd.Series(dtype=float)
    frame = bars.copy()
    frame["_session_date"] = [stamp.date() for stamp in frame.index]
    close = pd.to_numeric(frame["Close"], errors="coerce")
    frame["_close"] = close
    return frame.groupby("_session_date", sort=True)["_close"].last().dropna()


def quote_from_bars(
    ticker: str,
    intraday: pd.DataFrame | None,
    *,
    now: datetime | None = None,
    daily_fallback: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build LIVE/EOD quote with previous-session comparison and stale guards.

    `daily_fallback` is only used when the 5-minute feed cannot provide two
    reliable sessions. It must contain at least a Close column.
    """
    t = str(ticker).upper().strip()
    current = (now or datetime.now(tz=NY))
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    current = current.astimezone(NY)
    today = current.date()
    regular = current.weekday() < 5 and (
        pd.Timestamp("09:30").time() <= current.time() < pd.Timestamp("16:00").time()
    )
    bars = normalize_market_map_bars(intraday)
    if not bars.empty:
        bars = bars.loc[bars.index <= pd.Timestamp(current)].copy()
        closes = _session_closes(bars)
        today_rows = bars.loc[[stamp.date() == today for stamp in bars.index]].copy()
        if regular and not today_rows.empty:
            latest_ts = today_rows.index[-1]
            age_minutes = max(0.0, (current - latest_ts.to_pydatetime()).total_seconds() / 60.0)
            previous_dates = [d for d in closes.index if d < today]
            if age_minutes <= LIVE_MAX_AGE_MINUTES and previous_dates:
                previous_close = float(closes.loc[previous_dates[-1]])
                price = float(today_rows["Close"].iloc[-1])
                if np.isfinite(price) and np.isfinite(previous_close) and previous_close > 0:
                    return {
                        "ticker": t, "price": price,
                        "daily_pct": (price / previous_close - 1.0) * 100.0,
                        "quote_mode": "LIVE",
                        "quote_time": latest_ts.strftime("%H:%M ET"),
                        "eod_date": previous_dates[-1].isoformat(), "error": "",
                    }
        # Outside regular hours, the latest 5-minute session must look complete.
        eligible_dates = [d for d in closes.index if d <= today] if not regular else [d for d in closes.index if d < today]
        if eligible_dates:
            latest_date = eligible_dates[-1]
            latest_rows = bars.loc[[stamp.date() == latest_date for stamp in bars.index]]
            if not regular:
                try:
                    now_ts = pd.Timestamp(current)
                    close_ts = now_ts.normalize() + pd.Timedelta(hours=16)
                    if latest_date == today and (close_ts - latest_rows.index[-1]).total_seconds() > CLOSE_BAR_MAX_GAP_MINUTES * 60:
                        latest_date = eligible_dates[-2] if len(eligible_dates) >= 2 else None
                except Exception:
                    pass
            if latest_date is not None:
                previous_dates = [d for d in closes.index if d < latest_date]
                if previous_dates:
                    price = float(closes.loc[latest_date])
                    previous_close = float(closes.loc[previous_dates[-1]])
                    if np.isfinite(price) and np.isfinite(previous_close) and previous_close > 0:
                        return {
                            "ticker": t, "price": price,
                            "daily_pct": (price / previous_close - 1.0) * 100.0,
                            "quote_mode": "EOD", "quote_time": latest_date.isoformat(),
                            "eod_date": latest_date.isoformat(), "error": "",
                        }

    # Legacy-compatible daily-close fallback; while the regular session is
    # open the current partial daily bar is excluded by selecting date < today.
    daily = daily_fallback.copy() if isinstance(daily_fallback, pd.DataFrame) else pd.DataFrame()
    if not daily.empty and "Close" in daily.columns:
        index = pd.DatetimeIndex(pd.to_datetime(daily.index, errors="coerce"))
        valid = ~index.isna()
        daily = daily.loc[valid].copy()
        index = index[valid]
        if index.tz is not None:
            index = index.tz_convert(NY).tz_localize(None)
        daily.index = index
        close = pd.to_numeric(daily["Close"], errors="coerce")
        daily = daily.loc[close.notna()].copy()
        daily["Close"] = close.loc[daily.index]
        daily = daily.sort_index()
        daily = daily.loc[[d.date() < today for d in daily.index] if regular else [d.date() <= today for d in daily.index]]
        if len(daily) >= 2:
            price = float(daily["Close"].iloc[-1])
            previous = float(daily["Close"].iloc[-2])
            if np.isfinite(price) and np.isfinite(previous) and previous > 0:
                day = daily.index[-1].date().isoformat()
                return {
                    "ticker": t, "price": price, "daily_pct": (price / previous - 1.0) * 100.0,
                    "quote_mode": "EOD", "quote_time": day, "eod_date": day, "error": "",
                }
    return _empty_quote(t, "NO_RELIABLE_INTRADAY_OR_EOD_PAIR")


def fetch_market_map_quote(
    ticker: str,
    *,
    yf_module: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch one symbol's legacy-compatible intraday/EOD quote."""
    t = str(ticker or "").upper().strip()
    if not t:
        return _empty_quote(t, "EMPTY_TICKER")
    try:
        if yf_module is None:
            import yfinance as yf_module  # type: ignore[no-redef]
        intraday = yf_module.Ticker(t).history(
            period="5d", interval="5m", auto_adjust=True, actions=False, prepost=False
        )
    except Exception as exc:
        intraday = pd.DataFrame()
        intraday_error = f"INTRADAY_FETCH:{type(exc).__name__}:{exc}"
    else:
        intraday_error = ""
    provisional = quote_from_bars(t, intraday, now=now)
    if provisional.get("quote_mode") != "UNAVAILABLE":
        return provisional
    try:
        if yf_module is None:
            import yfinance as yf_module  # type: ignore[no-redef]
        daily = yf_module.Ticker(t).history(
            period="1mo", interval="1d", auto_adjust=True, actions=False, prepost=False
        )
    except Exception as exc:
        return _empty_quote(t, f"{intraday_error}; EOD_FETCH:{type(exc).__name__}:{exc}")
    result = quote_from_bars(t, intraday, now=now, daily_fallback=daily)
    if result.get("quote_mode") == "UNAVAILABLE" and intraday_error:
        result["error"] = f"{intraday_error}; {result.get('error', '')}"
    return result


def fetch_market_map_data(
    tickers: Iterable[str],
    *,
    sector_by_ticker: dict[str, str] | None = None,
    yf_module: Any | None = None,
    now: datetime | None = None,
    max_workers: int = 6,
) -> pd.DataFrame:
    """Fetch map quotes concurrently and return alphabetical available rows."""
    clean = sorted({str(t).upper().strip() for t in (tickers or []) if str(t).strip()})
    sectors = {str(k).upper().strip(): str(v or "미분류") for k, v in (sector_by_ticker or {}).items()}
    if not clean:
        return pd.DataFrame(columns=["ticker", "price", "daily_pct", "quote_mode", "quote_time", "eod_date", "sector", "error"])
    rows: list[dict[str, Any]] = []
    workers = max(1, min(int(max_workers), len(clean)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_market_map_quote, ticker, yf_module=yf_module, now=now): ticker for ticker in clean}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = _empty_quote(ticker, f"{type(exc).__name__}:{exc}")
            row["sector"] = sectors.get(ticker, "미분류")
            rows.append(row)
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result = result.loc[result["quote_mode"].astype(str) != "UNAVAILABLE"].copy()
    result["ticker"] = result["ticker"].astype(str).str.upper().str.strip()
    result["daily_pct"] = pd.to_numeric(result["daily_pct"], errors="coerce")
    return result.sort_values("ticker", kind="stable").reset_index(drop=True)


def build_market_map_figure(
    quotes: pd.DataFrame,
    *,
    stage_by_ticker: dict[str, str] | None = None,
    sector_by_ticker: dict[str, str] | None = None,
    height: int = 520,
) -> go.Figure:
    """Create legacy-style treemap, with equal tile area and A→Z ticker order."""
    stage_map = {str(k).upper().strip(): str(v or "-") for k, v in (stage_by_ticker or {}).items()}
    sector_map = {str(k).upper().strip(): str(v or "미분류") for k, v in (sector_by_ticker or {}).items()}
    if quotes is None or quotes.empty:
        raise ValueError("MARKET_MAP_NO_QUOTES")
    frame = quotes.copy()
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    frame = frame.drop_duplicates("ticker", keep="last").sort_values("ticker", kind="stable").reset_index(drop=True)
    frame["daily_pct"] = pd.to_numeric(frame["daily_pct"], errors="coerce")
    frame["Entry Stage"] = frame["ticker"].map(stage_map).fillna("-").astype(str).str.replace("BASE + PP", "BASE+PP", regex=False)
    frame["sector"] = frame["ticker"].map(sector_map).fillna(frame.get("sector", "미분류"))
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame["label"] = frame.apply(
        lambda row: f"<b>{row['ticker']}</b><br>{row['daily_pct']:+.2f}%<br>{row['Entry Stage']}"
        if pd.notna(row["daily_pct"]) else f"<b>{row['ticker']}</b><br>—<br>{row['Entry Stage']}", axis=1
    )
    frame["hover"] = frame.apply(
        lambda row: (
            f"{row['ticker']} · {row['Entry Stage']}<br>현재가 ${row['price']:,.2f}<br>"
            f"1D {row['daily_pct']:+.2f}%<br>섹터 {row['sector']}<br>{row.get('quote_mode', '—')} {row.get('quote_time', '')}"
        ) if pd.notna(row["price"]) and pd.notna(row["daily_pct"]) else f"{row['ticker']} · 데이터 부족",
        axis=1,
    )
    custom = frame[["ticker", "price", "daily_pct", "Entry Stage", "sector", "hover"]].to_numpy()
    fig = go.Figure(go.Treemap(
        labels=frame["label"], parents=[""] * len(frame), values=[1.0] * len(frame),
        sort=False, customdata=custom, textinfo="label",
        hovertemplate="%{customdata[5]}<extra></extra>",
        marker=dict(
            colors=frame["daily_pct"].fillna(0.0),
            colorscale=[[0.0, "#8b1e1e"], [0.45, "#252a30"], [0.5, "#252a30"], [0.55, "#263c2a"], [1.0, "#16803c"]],
            cmid=0, colorbar=dict(title="1D %"), line=dict(width=2, color="#11161b"),
        ),
        tiling=dict(packing="squarify", pad=3), pathbar=dict(visible=False),
    ))
    fig.update_layout(
        height=height, margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#ffffff", family="Arial", size=12),
    )
    return fig


def market_map_status_caption(quotes: pd.DataFrame) -> str:
    """Return legacy-style LIVE/EOD caption with a transparent quote basis."""
    if quotes is None or quotes.empty:
        return "가격 데이터 없음 · 잠시 후 다시 갱신하세요."
    modes = quotes["quote_mode"].astype(str).value_counts()
    if modes.get("LIVE", 0) >= modes.get("EOD", 0):
        times = quotes.loc[quotes["quote_mode"].astype(str) == "LIVE", "quote_time"].astype(str)
        timestamp = times.iloc[0] if not times.empty else datetime.now(tz=NY).strftime("%H:%M ET")
        return f"LIVE · {timestamp} · 1D = 현재가 / 직전 완료 정규장 종가 · 5분 자동 갱신"
    dates = pd.to_datetime(quotes["eod_date"], errors="coerce").dropna()
    day = dates.max().strftime("%Y-%m-%d") if not dates.empty else ""
    return f"EOD · {day} · 1D = 확정 종가 / 전일 종가 · 장외 시세"
