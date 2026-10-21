"""Shared analysis service consumed by Momentum Monitor and Pattern Scanner.

Every screen reads the same chronological replay result and the same derived
metrics. The bundled historical seed is a convenience snapshot, not an
independently labelled ground-truth result; providers must still be approved.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from ..engines.data_validation import atr_wilder, normalize_ohlcv
from ..engines.pattern_detectors import detect_pattern_candidates
from ..engines.pattern_engine import select_primary_pattern
from ..engines.pivot_engine import price_distance_pct, twenty_day_reference
from ..engines.pocket_pivot_engine import detect_pocket_pivot
from ..engines.risk_engine import compute_initial_risk
from .chronological_replay import replay_symbol

ROOT = Path(__file__).resolve().parents[2]
SEED_REPLAY_PATH = ROOT / "data" / "regression" / "chronological_replay.csv"


def load_seed_snapshots(path: Path | str = SEED_REPLAY_PATH) -> dict[str, dict[str, Any]]:
    """Load the most recent bundled replay row per symbol, if present."""
    file_path = Path(path)
    if not file_path.exists():
        return {}
    frame = pd.read_csv(file_path)
    if frame.empty or not {"ticker", "as_of_date"}.issubset(frame.columns):
        return {}
    frame["as_of_date"] = pd.to_datetime(frame["as_of_date"])
    latest = frame.sort_values("as_of_date").groupby("ticker", as_index=False).tail(1)
    return {str(row["ticker"]).upper(): row.to_dict() for _, row in latest.iterrows()}


def _num(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(value: float | None) -> float | None:
    return round(value, 2) if value is not None and pd.notna(value) else None


def _date_string(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    try:
        return pd.Timestamp(value).date().isoformat()
    except (TypeError, ValueError):
        return str(value)


def _compatible_seed(bars: pd.DataFrame, seed: dict[str, Any] | None) -> bool:
    if not seed:
        return False
    seed_date = pd.Timestamp(seed.get("as_of_date")).normalize()
    matching = bars.loc[bars["Date"] == seed_date]
    if matching.empty:
        return False
    seed_close, actual_close = _num(seed.get("close")), _num(matching.iloc[-1]["Close"])
    if seed_close is None or actual_close is None or actual_close <= 0:
        return False
    # A mismatched adjustment basis invalidates the state seed.
    return abs(actual_close / seed_close - 1.0) <= 0.005


def _history_snapshot(symbol: str, bars: pd.DataFrame, seed: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
    if _compatible_seed(bars, seed):
        seed_date = pd.Timestamp(seed["as_of_date"]).normalize()
        if bars["Date"].max() <= seed_date:
            return dict(seed), "BUNDLED_REPLAY_SNAPSHOT"
        replay = replay_symbol(
            symbol,
            bars,
            resume_snapshot=seed,
            process_from_date=seed_date,
        )
        if not replay.empty:
            return replay.iloc[-1].to_dict(), "INCREMENTAL_FROM_BUNDLED_SNAPSHOT"
        return dict(seed), "BUNDLED_REPLAY_SNAPSHOT"

    # For symbols without a compatible seed, use a transparent 260-bar rolling
    # replay. This is enough for the current detector set but is not a full
    # multi-year reconstruction of older breakout events.
    rolling = bars.tail(260).reset_index(drop=True)
    replay = replay_symbol(symbol, rolling)
    if replay.empty:
        return {}, "NO_REPLAY_RESULT"
    return replay.iloc[-1].to_dict(), "ROLLING_260_BAR_REPLAY"


def analyze_symbol(
    symbol: str,
    raw_bars: pd.DataFrame,
    *,
    seed_snapshot: dict[str, Any] | None = None,
    benchmark_bars: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Produce the one canonical row shared by the Momentum and Pattern screens."""
    ticker = symbol.upper().strip()
    bars = normalize_ohlcv(raw_bars)
    if bars.empty:
        raise ValueError(f"No daily bars available for {ticker}")
    state, provenance = _history_snapshot(ticker, bars, seed_snapshot)
    last = bars.iloc[-1]
    previous = bars.iloc[-2] if len(bars) > 1 else None
    close = float(last["Close"])
    prior_close = float(previous["Close"]) if previous is not None else None
    close_change = (close / prior_close - 1.0) * 100 if prior_close else None

    sma = {period: _num(bars["Close"].tail(period).mean()) if len(bars) >= period else None for period in (10, 20, 50, 200)}
    twenty_ref = twenty_day_reference(bars)
    candidate_pivot = _num(state.get("candidate_pivot"))
    breakout_pivot = _num(state.get("breakout_pivot"))
    candidate_distance = price_distance_pct(close, candidate_pivot)
    breakout_distance = price_distance_pct(close, breakout_pivot)
    twenty_distance = price_distance_pct(close, twenty_ref)
    avg_prior_volume = _num(bars["Volume"].iloc[-21:-1].mean()) if len(bars) >= 21 else None
    rvol = float(last["Volume"]) / avg_prior_volume if avg_prior_volume and avg_prior_volume > 0 else None

    return_pct: dict[int, float | None] = {}
    for lookback in (21, 63, 126, 252):
        if len(bars) > lookback and float(bars["Close"].iloc[-lookback - 1]) > 0:
            return_pct[lookback] = (close / float(bars["Close"].iloc[-lookback - 1]) - 1) * 100
        else:
            return_pct[lookback] = None

    rs_63 = None
    if benchmark_bars is not None and not benchmark_bars.empty:
        benchmark = normalize_ohlcv(benchmark_bars)
        merged = bars[["Date", "Close"]].merge(benchmark[["Date", "Close"]], on="Date", suffixes=("_symbol", "_benchmark"))
        if len(merged) > 63:
            sym_return = merged["Close_symbol"].iloc[-1] / merged["Close_symbol"].iloc[-64] - 1
            bench_return = merged["Close_benchmark"].iloc[-1] / merged["Close_benchmark"].iloc[-64] - 1
            rs_63 = (sym_return - bench_return) * 100

    # The primary pattern/pivot is always the one chosen by the chronological
    # engine. Re-scan the latest bars only to expose secondary detector diagnostics.
    # This scan never replaces the active candidate pivot.
    rebase_start = state.get("rebase_started_at")
    secondary = detect_pattern_candidates(
        bars.tail(120),
        rebase_started_at=rebase_start if bool(state.get("rebase_active", False)) else None,
        prior_breakout_pivot=breakout_pivot,
        prior_breakout_date=state.get("breakout_date"),
    )
    primary = select_primary_pattern(secondary)
    current_pattern = str(state.get("pattern") or "BASE FORMING / NO VALID PATTERN")
    # If the replay snapshot predates today's bars, use the current scan for a
    # diagnostic only when no rebase is active; the official pivot is not silently
    # replaced by this scan. This prevents an un-replayed pattern from becoming a signal.
    detected_pattern = primary.pattern_name if primary else None
    if bool(state.get("rebase_active", False)):
        current_pattern = "BASE FORMING / NO VALID PATTERN"
        candidate_pivot = None
        candidate_distance = None
    elif not candidate_pivot and primary:
        current_pattern = primary.pattern_name
        # The pattern scan is valid as a candidate only when the chronological
        # engine produced a valid pivot; otherwise it is displayed as diagnostic.

    # Pocket Pivot is informational; stage and candidate pivot remain owned by replay.
    constructive = bool(primary) or (sma[50] is not None and sma[200] is not None and close > sma[50] and sma[50] >= sma[200])
    pp = detect_pocket_pivot(bars, constructive_context=constructive)
    return {
        "ticker": ticker,
        "as_of_date": pd.Timestamp(last["Date"]).date().isoformat(),
        "close": close,
        "day_change_pct": _pct(close_change),
        "pattern": current_pattern,
        "detected_pattern_diagnostic": detected_pattern,
        "pattern_start_date": _date_string(state.get("pattern_start_date")),
        "candidate_pivot": candidate_pivot,
        "candidate_distance_pct": _pct(candidate_distance),
        "breakout_pivot": breakout_pivot,
        "breakout_distance_pct": _pct(breakout_distance),
        "twenty_day_reference": twenty_ref,
        "twenty_day_distance_pct": _pct(twenty_distance),
        "stage": ("BASE" if bool(state.get("rebase_active", False)) and not bool(state.get("breakout_failure", False)) else str(state.get("stage") or "BASE")),
        "rebase_active": bool(state.get("rebase_active", False)),
        "rebase_started_at": _date_string(state.get("rebase_started_at")),
        "rebase_reason": (None if state.get("rebase_reason") is None or pd.isna(state.get("rebase_reason")) else str(state.get("rebase_reason"))),
        "breakout_cycle_active": bool(state.get("breakout_cycle_active", False)),
        "breakout_event": bool(state.get("breakout_event", False)),
        "breakout_failure": bool(state.get("breakout_failure", False)),
        "rvol": _pct(rvol),
        "sma10": sma[10], "sma20": sma[20], "sma50": sma[50], "sma200": sma[200],
        "return_1m_pct": _pct(return_pct[21]), "return_3m_pct": _pct(return_pct[63]),
        "return_6m_pct": _pct(return_pct[126]), "return_12m_pct": _pct(return_pct[252]),
        "rs_3m_vs_spy_pct": _pct(rs_63),
        "pocket_pivot_today": bool(pp.valid_signal),
        "pocket_pivot_reason": "|".join(pp.reason_codes),
        "data_rows": len(bars),
        "replay_provenance": provenance,
        "validation_status": str(state.get("validation_status") or "OBSERVED_NOT_VALIDATED"),
        "bars": bars,
    }
