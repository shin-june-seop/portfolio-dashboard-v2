"""Helpers for inspecting historical OHLCV anomalies and rebase review snapshots.

Outputs are review aids only. They do not infer corporate-action causes or
turn V2's own predictions into regression ground truth.
"""
from __future__ import annotations

from typing import Any
import pandas as pd

from ..engines.data_validation import normalize_ohlcv


def large_close_move_context(
    symbol: str,
    raw_frame: pd.DataFrame,
    *,
    threshold: float = 0.20,
    context_bars: int = 3,
) -> list[dict[str, Any]]:
    """Return event bars with nearby context for absolute close moves > threshold."""
    if not 0 < threshold < 1:
        raise ValueError("threshold must be between 0 and 1")
    if context_bars < 0:
        raise ValueError("context_bars must be non-negative")
    frame = normalize_ohlcv(raw_frame)
    if len(frame) < 2:
        return []

    frame = frame.sort_values("Date").reset_index(drop=True)
    move = frame["Close"].pct_change()
    event_indices = [int(i) for i, value in enumerate(move) if pd.notna(value) and abs(float(value)) > threshold]
    rows: list[dict[str, Any]] = []
    for idx in event_indices:
        lo = max(0, idx - context_bars)
        hi = min(len(frame) - 1, idx + context_bars)
        prior = frame.iloc[idx - 1]
        event = frame.iloc[idx]
        event_pct = float(move.iloc[idx]) * 100.0
        for j in range(lo, hi + 1):
            bar = frame.iloc[j]
            rows.append({
                "ticker": symbol.upper(),
                "event_date": event["Date"].date().isoformat(),
                "event_previous_date": prior["Date"].date().isoformat(),
                "event_previous_close": round(float(prior["Close"]), 6),
                "event_close": round(float(event["Close"]), 6),
                "event_change_pct": round(event_pct, 4),
                "context_offset_bars": j - idx,
                "context_date": bar["Date"].date().isoformat(),
                "Open": round(float(bar["Open"]), 6),
                "High": round(float(bar["High"]), 6),
                "Low": round(float(bar["Low"]), 6),
                "Close": round(float(bar["Close"]), 6),
                "Volume": int(bar["Volume"]),
                "is_event_bar": j == idx,
                "price_basis_note": "Source file uses its captured adjustment basis; this report does not determine whether the move is genuine or adjustment-related.",
            })
    return rows


def summarize_rebase_review(raw_review: pd.DataFrame) -> list[dict[str, Any]]:
    """Summarize the first visible post-rebase candidate in sampled snapshots."""
    if raw_review.empty:
        return []
    required = {
        "ticker", "cycle_id", "rebase_started_at_rb01_proxy", "observation_date",
        "offset_bars_from_trigger", "ungated_pattern", "ungated_candidate_pivot",
        "ungated_pattern_start", "rebase_gated_pattern", "rebase_gated_candidate_pivot",
        "rebase_gated_pattern_start",
    }
    missing = sorted(required - set(raw_review.columns))
    if missing:
        raise ValueError("Review CSV missing columns: " + ", ".join(missing))

    base_label = "BASE FORMING / NO VALID PATTERN"
    rows: list[dict[str, Any]] = []
    data = raw_review.copy()
    data["offset_bars_from_trigger"] = pd.to_numeric(data["offset_bars_from_trigger"], errors="coerce")
    for (_, _), group in data.groupby(["ticker", "cycle_id"], sort=True, dropna=False):
        group = group.sort_values("offset_bars_from_trigger")
        trigger = group.loc[group["offset_bars_from_trigger"] == 0]
        if trigger.empty:
            trigger = group.iloc[[0]]
        t = trigger.iloc[0]
        date_start = pd.to_datetime(t["rebase_started_at_rb01_proxy"], errors="coerce")
        old_start = pd.to_datetime(t.get("ungated_pattern_start", ""), errors="coerce")
        old_pattern = str(t.get("ungated_pattern", base_label))
        old_exists = old_pattern != base_label and pd.notna(old_start)
        old_predates_trigger = bool(old_exists and pd.notna(date_start) and old_start < date_start)

        valid_gated = group[group["rebase_gated_pattern"].astype(str) != base_label].copy()
        if not valid_gated.empty:
            valid_gated = valid_gated.sort_values("offset_bars_from_trigger")
            first = valid_gated.iloc[0]
            first_date = str(first["observation_date"])
            first_offset = int(first["offset_bars_from_trigger"])
            first_pattern = str(first["rebase_gated_pattern"])
            first_pivot = first.get("rebase_gated_candidate_pivot", "")
            first_start = first.get("rebase_gated_pattern_start", "")
        else:
            first_date, first_offset, first_pattern, first_pivot, first_start = "", "", "", "", ""

        rows.append({
            "ticker": str(t["ticker"]).upper(),
            "cycle_id": str(t["cycle_id"]),
            "rebase_started_at_rb01_proxy": str(t["rebase_started_at_rb01_proxy"]),
            "trigger_observation_date": str(t["observation_date"]),
            "trigger_close_vs_20d_high_pct": t.get("close_vs_20d_high_pct", ""),
            "ungated_pattern_at_trigger": old_pattern,
            "ungated_pattern_start_at_trigger": t.get("ungated_pattern_start", ""),
            "ungated_pattern_predates_trigger": old_predates_trigger,
            "gated_pattern_at_trigger": str(t.get("rebase_gated_pattern", "")),
            "snapshot_count": int(len(group)),
            "first_sampled_valid_post_rebase_pattern_offset": first_offset,
            "first_sampled_valid_post_rebase_pattern_date": first_date,
            "first_sampled_valid_post_rebase_pattern": first_pattern,
            "first_sampled_candidate_pivot": first_pivot,
            "first_sampled_pattern_start": first_start,
            "review_status": "REVIEW_REQUIRED",
            "expected_pattern_manual": "",
            "expected_candidate_pivot_manual": "",
            "evidence_reference": "",
            "review_note": "RB-01 proxy and sampled snapshots only; compare against independent historical evidence before labelling.",
        })
    return rows
