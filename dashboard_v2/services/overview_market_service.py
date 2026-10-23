"""Compact Overview index panels for the Portfolio Dashboard V2.

Price candles come from completed daily index OHLC bars (Yahoo Finance,
auto_adjust=False). Index volume is not reliably provided by Yahoo, so the
relative-volume mini-bars use explicitly labelled liquid ETF proxies:
SPY for S&P 500, QQQ for Nasdaq Composite, and DIA for Dow Jones Industrial
Average. Proxy volume is never used in the index price/candle calculations.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
import html
import math
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NY = ZoneInfo("America/New_York")
FINALIZATION_CUTOFF = time(16, 15)

INDEX_CONFIGS: tuple[dict[str, str], ...] = (
    {"label": "S&P 500", "symbol": "^GSPC", "proxy_symbol": "SPY"},
    {"label": "NASDAQ", "symbol": "^IXIC", "proxy_symbol": "QQQ"},
    {"label": "DOW", "symbol": "^DJI", "proxy_symbol": "DIA"},
)


class OverviewMarketDataError(RuntimeError):
    """Index market data is missing, malformed, or not current enough to display."""


def latest_completed_xnys_session(now: datetime | None = None) -> date:
    """Return the most recent XNYS session eligible for EOD display.

    Before 16:15 New York time (including weekends/holidays), today's session
    is withheld. At/after 16:15, today's session is eligible, but the caller
    still requires Yahoo to actually return that session's daily bar.
    """
    try:
        import exchange_calendars as xcals
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise OverviewMarketDataError("exchange-calendars is required for EOD cutoff") from exc

    current = now or datetime.now(tz=NY)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    current_ny = current.astimezone(NY)
    reference_date = current_ny.date()
    if current_ny.time().replace(tzinfo=None) < FINALIZATION_CUTOFF:
        reference_date -= timedelta(days=1)
    try:
        session = xcals.get_calendar("XNYS").date_to_session(
            pd.Timestamp(reference_date), direction="previous"
        )
    except Exception as exc:
        raise OverviewMarketDataError(
            f"Could not determine latest completed US session: {type(exc).__name__}"
        ) from exc
    return pd.Timestamp(session).date()


def normalize_completed_daily_bars(
    raw: pd.DataFrame | None,
    *,
    expected_session: date | str,
    require_current_session: bool = True,
) -> pd.DataFrame:
    """Normalize OHLC and optional volume, requiring the expected session.

    Unlike the stock EOD provider, index bars do not require non-zero volume.
    Missing/zero index volume is expected for some index symbols; this function
    validates OHLC only, leaving proxy ETF volume to a separate data path.
    """
    if raw is None or raw.empty:
        raise OverviewMarketDataError("No daily bars returned")
    if isinstance(raw.columns, pd.MultiIndex):
        raise OverviewMarketDataError("Unexpected MultiIndex columns for single-symbol history")

    expected = pd.Timestamp(expected_session).date()
    lower = {str(column).strip().lower(): column for column in raw.columns}
    required = ("open", "high", "low", "close")
    missing = [name.title() for name in required if name not in lower]
    if missing:
        raise OverviewMarketDataError(f"Missing daily OHLC fields: {', '.join(missing)}")

    date_col = next((lower[key] for key in ("date", "datetime", "timestamp") if key in lower), None)
    source_dates = raw[date_col].tolist() if date_col is not None else raw.index.tolist()
    dates: list[pd.Timestamp] = []
    for value in source_dates:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            raise OverviewMarketDataError("Invalid daily bar date")
        # Yahoo's daily timestamps are already exchange-local date labels. Use
        # the displayed date component to avoid UTC shifting around midnight.
        dates.append(pd.Timestamp(stamp.date()))

    data: dict[str, Any] = {"Date": dates}
    for name in required:
        data[name.title()] = pd.to_numeric(raw[lower[name]].to_numpy(), errors="coerce")
    volume_col = lower.get("volume")
    data["Volume"] = (
        pd.to_numeric(raw[volume_col].to_numpy(), errors="coerce")
        if volume_col is not None
        else np.full(len(raw), np.nan)
    )
    frame = pd.DataFrame(data).sort_values("Date", kind="stable").reset_index(drop=True)
    frame = frame.loc[frame["Date"].dt.date <= expected].copy()
    if frame.empty:
        raise OverviewMarketDataError("No bars on or before expected completed session")
    if frame["Date"].duplicated().any():
        raise OverviewMarketDataError("Duplicate daily dates returned")

    # Do not silently choose an older close if the required completed-session
    # bar is missing. Invalid latest OHLC therefore fails closed.
    latest_date = frame["Date"].iloc[-1].date()
    if require_current_session and latest_date != expected:
        raise OverviewMarketDataError(
            f"DATA_STALE: expected {expected.isoformat()}, received {latest_date.isoformat()}"
        )

    ohlc = frame[["Open", "High", "Low", "Close"]].to_numpy(dtype="float64")
    valid = np.isfinite(ohlc).all(axis=1) & (ohlc > 0).all(axis=1)
    valid &= frame["High"].to_numpy(dtype="float64") >= frame[["Open", "Close", "Low"]].max(axis=1).to_numpy(dtype="float64")
    valid &= frame["Low"].to_numpy(dtype="float64") <= frame[["Open", "Close", "High"]].min(axis=1).to_numpy(dtype="float64")
    frame = frame.loc[valid].copy().reset_index(drop=True)
    if frame.empty:
        raise OverviewMarketDataError("No valid daily OHLC rows returned")
    if require_current_session and frame["Date"].iloc[-1].date() != expected:
        raise OverviewMarketDataError("DATA_INVALID: expected completed-session OHLC is invalid")
    volume = pd.to_numeric(frame["Volume"], errors="coerce")
    frame["Volume"] = volume.where(np.isfinite(volume) & (volume >= 0), np.nan)
    return frame


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _rvol_by_date(proxy_bars: pd.DataFrame | None, expected: date | str) -> dict[str, float]:
    if proxy_bars is None or proxy_bars.empty:
        return {}
    try:
        proxy = normalize_completed_daily_bars(proxy_bars, expected_session=expected)
    except Exception:
        return {}
    volume = pd.to_numeric(proxy["Volume"], errors="coerce")
    previous_20_average = volume.shift(1).rolling(20, min_periods=20).mean()
    rvol = volume / previous_20_average.where(previous_20_average > 0)
    result: dict[str, float] = {}
    for dt, value in zip(proxy["Date"], rvol):
        numeric = _finite(value)
        if numeric is not None and numeric >= 0:
            result[pd.Timestamp(dt).date().isoformat()] = numeric
    return result


def build_index_panel(
    index_bars: pd.DataFrame | None,
    proxy_bars: pd.DataFrame | None,
    *,
    config: Mapping[str, str],
    expected_session: date | str,
) -> dict[str, Any]:
    """Build UI-friendly values for one index; price data and volume proxy stay separate."""
    label = str(config["label"])
    symbol = str(config["symbol"])
    proxy_symbol = str(config["proxy_symbol"])
    try:
        index = normalize_completed_daily_bars(index_bars, expected_session=expected_session)
        if len(index) < 2:
            raise OverviewMarketDataError("At least two completed daily bars are required")
        last_two = index.tail(2).reset_index(drop=True)
        previous = last_two.iloc[0]
        latest = last_two.iloc[1]
        previous_close = float(previous["Close"])
        latest_close = float(latest["Close"])
        delta = latest_close - previous_close
        delta_pct = (delta / previous_close * 100.0) if previous_close else math.nan
        proxy_rvol = _rvol_by_date(proxy_bars, expected_session)
        candles = []
        for row in last_two.to_dict(orient="records"):
            key = pd.Timestamp(row["Date"]).date().isoformat()
            candles.append({
                "date": key,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "rvol": proxy_rvol.get(key),
            })
        return {
            "available": True,
            "label": label,
            "symbol": symbol,
            "proxy_symbol": proxy_symbol,
            "as_of_date": pd.Timestamp(latest["Date"]).date().isoformat(),
            "latest_close": latest_close,
            "previous_close": previous_close,
            "change_points": delta,
            "change_pct": delta_pct,
            "candles": candles,
            "rvol_available": any(candle["rvol"] is not None for candle in candles),
            "volume_note": f"상대거래량은 {proxy_symbol} 거래량 기준의 보조 지표입니다. 지수 가격 계산에는 사용하지 않습니다.",
            "error": None,
        }
    except Exception as exc:
        return {
            "available": False,
            "label": label,
            "symbol": symbol,
            "proxy_symbol": proxy_symbol,
            "as_of_date": None,
            "latest_close": None,
            "previous_close": None,
            "change_points": None,
            "change_pct": None,
            "candles": [],
            "rvol_available": False,
            "volume_note": f"{proxy_symbol} ETF 거래량은 지수 거래량이 아닌 보조 지표입니다.",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _fetch_history(symbol: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise OverviewMarketDataError("yfinance is required to load Overview indices") from exc
    ticker = yf.Ticker(symbol)
    return ticker.history(
        period="6mo", interval="1d", auto_adjust=False, actions=False,
        prepost=False, timeout=15,
    )


def load_overview_market_panels(
    *,
    now: datetime | None = None,
    fetch_history: Callable[[str], pd.DataFrame] | None = None,
) -> list[dict[str, Any]]:
    """Fetch the three index panels and ETF-volume proxies in parallel."""
    expected = latest_completed_xnys_session(now)
    fetcher = fetch_history or _fetch_history
    symbols = tuple(dict.fromkeys(
        item[key] for item in INDEX_CONFIGS for key in ("symbol", "proxy_symbol")
    ))
    results: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(6, len(symbols))) as pool:
        futures = {pool.submit(fetcher, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                results[symbol] = future.result()
            except Exception as exc:
                errors[symbol] = f"{type(exc).__name__}: {exc}"

    panels = []
    for config in INDEX_CONFIGS:
        symbol, proxy_symbol = config["symbol"], config["proxy_symbol"]
        index_raw = results.get(symbol)
        if index_raw is None:
            panels.append(build_index_panel(
                None, results.get(proxy_symbol), config=config, expected_session=expected
            ) | {"error": errors.get(symbol, "Overview index data unavailable")})
            continue
        panels.append(build_index_panel(
            index_raw, results.get(proxy_symbol), config=config, expected_session=expected
        ))
        if errors.get(proxy_symbol) and panels[-1].get("available"):
            panels[-1]["volume_note"] = f"{proxy_symbol} 상대거래량 데이터를 사용할 수 없습니다. 지수 캔들은 정상적으로 표시됩니다."
    return panels


def _fmt_axis_price(value: float) -> str:
    if value >= 10_000:
        return f"{value / 1000:.1f}k"
    return f"{value:,.0f}"


def _fmt_price_tag(value: float) -> str:
    return f"{value:,.1f}"


def _format_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%b %d")
    except Exception:
        return value


def _render_card(panel: Mapping[str, Any]) -> str:
    label = html.escape(str(panel.get("label", "Market Index")))
    if not panel.get("available"):
        error_text = html.escape(str(panel.get("error") or "데이터를 사용할 수 없습니다."))
        return (
            '<section class="card"><header class="head">'
            f'<span class="name">{label}</span><span class="date">—</span><span class="delta neutral">DATA UNAVAILABLE</span>'
            f'</header><div class="error"><strong>지수 데이터를 표시할 수 없습니다.</strong><small>{error_text}</small></div></section>'
        )

    candles = list(panel.get("candles") or [])[-2:]
    if len(candles) != 2:
        return (
            '<section class="card"><header class="head">'
            f'<span class="name">{label}</span><span class="date">—</span><span class="delta neutral">DATA UNAVAILABLE</span>'
            '</header><div class="error">완료 일봉 2개가 필요합니다.</div></section>'
        )
    change = float(panel["change_points"])
    change_pct = float(panel["change_pct"])
    delta_class = "up" if change >= 0 else "down"
    date_text = html.escape(_format_date(str(panel.get("as_of_date") or "")))
    delta_text = f"{change:+,.2f} ({change_pct:+.2f}%)"
    header = (
        '<header class="head">'
        f'<span class="name">{label}</span><span class="date">{date_text}</span>'
        f'<span class="delta {delta_class}">{html.escape(delta_text)}</span></header>'
    )

    width, height = 380, 252
    plot_left, plot_right = 10.0, 314.0
    plot_top, plot_bottom = 15.0, 156.0
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    high = max(highs)
    low = min(lows)
    spread = high - low
    padding = spread * 0.15 if spread > 0 else max(high * 0.005, 1.0)
    scale_min, scale_max = low - padding, high + padding

    def y(value: float) -> float:
        return plot_top + (scale_max - value) / (scale_max - scale_min) * (plot_bottom - plot_top)

    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{label} latest two completed daily candles">']
    for index in range(4):
        value = scale_max - (scale_max - scale_min) * index / 3.0
        yy = y(value)
        parts.append(f'<line x1="{plot_left:.1f}" y1="{yy:.1f}" x2="{plot_right:.1f}" y2="{yy:.1f}" class="gridline"/>')
        parts.append(f'<text x="{plot_right + 7:.1f}" y="{yy + 3:.1f}" class="axis">{html.escape(_fmt_axis_price(value))}</text>')

    x_centres = (90.0, 224.0)
    body_width = 58.0
    for candle, centre in zip(candles, x_centres):
        opn, cls, hi, lo = (float(candle[k]) for k in ("open", "close", "high", "low"))
        color_class = "bull" if cls >= opn else "bear"
        y_high, y_low, y_open, y_close = y(hi), y(lo), y(opn), y(cls)
        body_top, body_bottom = min(y_open, y_close), max(y_open, y_close)
        body_h = max(3.0, body_bottom - body_top)
        tip = f"{candle['date']} · O {opn:,.2f} · H {hi:,.2f} · L {lo:,.2f} · C {cls:,.2f}"
        parts.append(f'<g class="candle {color_class}"><title>{html.escape(tip)}</title>')
        parts.append(f'<line x1="{centre:.1f}" y1="{y_high:.1f}" x2="{centre:.1f}" y2="{y_low:.1f}" class="wick"/>')
        parts.append(f'<rect x="{centre - body_width / 2:.1f}" y="{body_top:.1f}" width="{body_width:.1f}" height="{body_h:.1f}" rx="0.6" class="body"/></g>')

    latest_close = float(panel["latest_close"])
    current_y = y(latest_close)
    parts.append(f'<line x1="{plot_left:.1f}" y1="{current_y:.1f}" x2="{plot_right + 2:.1f}" y2="{current_y:.1f}" class="price-line"/>')
    tag_text = _fmt_price_tag(latest_close)
    tag_width = max(62.0, 7.0 * len(tag_text) + 10.0)
    parts.append(f'<rect x="{plot_right + 1:.1f}" y="{current_y - 10:.1f}" width="{tag_width:.1f}" height="20" class="price-tag"/>')
    parts.append(f'<text x="{plot_right + 5:.1f}" y="{current_y + 3.5:.1f}" class="price-text">{html.escape(tag_text)}</text>')

    parts.append('<text x="13" y="185" class="volume-label">RELATIVE</text><text x="13" y="195" class="volume-label">VOLUME</text>')
    rvol_values = [candle.get("rvol") for candle in candles]
    valid_rvol = [float(v) for v in rvol_values if _finite(v) is not None and float(v) >= 0]
    if valid_rvol:
        max_rvol = max(1.0, max(valid_rvol))
        baseline = 232.0
        max_height = 18.0
        for centre, value in zip(x_centres, rvol_values):
            if _finite(value) is None or float(value) < 0:
                continue
            bar_height = max(2.0, min(max_height, float(value) / max_rvol * max_height))
            parts.append(f'<rect x="{centre - 40:.1f}" y="{baseline - bar_height:.1f}" width="80" height="{bar_height:.1f}" class="volume-bar"><title>{float(value):.2f}x ETF proxy RVOL</title></rect>')
    else:
        parts.append('<text x="110" y="224" class="volume-na">ETF RVOL unavailable</text>')
    parts.append('</svg>')
    note = html.escape(str(panel.get("volume_note") or ""))
    return f'<section class="card">{header}{"".join(parts)}<div class="note">{note}</div></section>'


def render_overview_market_panels_html(panels: list[Mapping[str, Any]]) -> str:
    """Create responsive dark mini-candlestick cards matching the legacy Overview."""
    cards = "".join(_render_card(panel) for panel in panels)
    return f'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: #0b0d0f; color: #e6edf6; font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
.grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; width: 100%; }}
.card {{ min-width: 0; background: #20252c; border: 1px solid #252d38; border-radius: 10px 10px 0 0; overflow: hidden; box-shadow: none; }}
.head {{ height: 51px; display: flex; align-items: center; gap: 10px; padding: 0 14px; background: #20252d; border: 1px solid #3b4654; border-bottom: 1px dashed #e04450; border-radius: 9px 9px 0 0; white-space: nowrap; }}
.name {{ font-size: 19px; font-weight: 700; color: #a9bfdc; flex: 0 0 auto; }}
.date {{ font-size: 12px; color: #98a9c0; margin-left: auto; }}
.delta {{ font-size: 13px; font-weight: 700; margin-left: 9px; }}
.delta.down {{ color: #ff4d62; }} .delta.up {{ color: #16c96a; }} .delta.neutral {{ color: #aab6c7; font-size: 11px; }}
.chart {{ display: block; width: 100%; height: auto; background: #20252c; }}
.gridline {{ stroke: #303740; stroke-width: 0.8; }}
.axis {{ fill: #9bb1d0; font-size: 10px; text-anchor: start; }}
.candle .wick {{ stroke-width: 2.1; }} .candle.bull .wick {{ stroke: #17c964; }} .candle.bear .wick {{ stroke: #ff4d67; }}
.candle.bull .body {{ fill: #17c964; }} .candle.bear .body {{ fill: #ff4d67; }}
.price-line {{ stroke: #ffda39; stroke-width: 1.1; stroke-dasharray: 2.5 2.5; }}
.price-tag {{ fill: #ffd431; }} .price-text {{ fill: #111317; font-size: 10px; font-weight: 700; }}
.volume-label {{ fill: #6e8db5; font-size: 7px; }} .volume-bar {{ fill: #2165a6; }} .volume-na {{ fill: #8090a5; font-size: 10px; }}
.note {{ color: #8090a5; font-size: 8px; padding: 2px 10px 7px; background: #20252c; min-height: 18px; }}
.error {{ display:flex; flex-direction:column; gap:8px; min-height:245px; align-items:center; justify-content:center; padding:18px; text-align:center; color:#c4cfdd; font-size:13px; }}
.error small {{ color:#8b9bb0; overflow-wrap:anywhere; }}
@media(max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; gap: 14px; }} .name {{font-size:18px;}} }}
@media(min-width: 901px) and (max-width: 1120px) {{ .grid {{ gap: 10px; }} .head {{ padding: 0 8px; gap: 5px; }} .name {{font-size:16px;}} .delta {{font-size:10px;margin-left:3px;}} .date {{font-size:10px;}} }}
</style></head><body><main class="grid">{cards}</main></body></html>'''
