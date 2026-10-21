"""Build a human-review worksheet for likely 20D-high drawdown/rebase dates.

This is a candidate-review aid, not a ground-truth labeler. It does not claim to
reconstruct RB-02/RB-03 because the original time-series pivot/event journal is
not available in captured OHLCV alone.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from dashboard_v2.engines.data_validation import normalize_ohlcv
from dashboard_v2.engines.pattern_detectors import detect_pattern_candidates
from dashboard_v2.engines.pattern_engine import select_primary_pattern

DEFAULT_SYMBOLS = ("AMAT", "MU", "SOXX", "META", "GOOG", "STX", "LRCX")
SNAPSHOT_OFFSETS = (0, 19, 24, 34, 44, 59, 79)


def _primary_observation(frame: pd.DataFrame, rebase_started_at: str | None = None) -> dict[str, Any]:
    if frame.empty:
        return {"pattern": "BASE FORMING / NO VALID PATTERN", "pivot": "", "start": "", "candidates": ""}
    candidates = detect_pattern_candidates(frame, rebase_started_at=rebase_started_at)
    primary = select_primary_pattern(candidates)
    return {
        "pattern": primary.pattern_name if primary else "BASE FORMING / NO VALID PATTERN",
        "pivot": round(float(primary.candidate_pivot), 4) if primary and primary.candidate_pivot is not None else "",
        "start": primary.pattern_start_date.isoformat() if primary and primary.pattern_start_date else "",
        "candidates": " | ".join(
            f"{c.pattern_name}@{float(c.candidate_pivot):.4f}[{c.pattern_start_date.isoformat() if c.pattern_start_date else ''}]"
            for c in candidates if c.candidate_pivot is not None
        ),
    }


def _episode_starts(frame: pd.DataFrame, threshold: float = 0.10) -> list[int]:
    high20 = frame["High"].rolling(20, min_periods=20).max()
    dd = frame["Close"] / high20 - 1.0
    active = (dd <= -threshold).fillna(False)
    starts: list[int] = []
    for idx in range(len(frame)):
        if bool(active.iloc[idx]) and (idx == 0 or not bool(active.iloc[idx - 1])):
            starts.append(idx)
    return starts


def build_review_rows(symbol: str, raw_frame: pd.DataFrame, snapshots: tuple[int, ...] = SNAPSHOT_OFFSETS) -> list[dict[str, Any]]:
    frame = normalize_ohlcv(raw_frame)
    if frame.empty or len(frame) < 20:
        return []
    starts = _episode_starts(frame)
    rows: list[dict[str, Any]] = []
    for episode_number, start_idx in enumerate(starts, start=1):
        next_start_idx = starts[episode_number] if episode_number < len(starts) else len(frame)
        rebase_date = frame.iloc[start_idx]["Date"].date().isoformat()
        for offset in snapshots:
            obs_idx = start_idx + offset
            if obs_idx >= len(frame) or obs_idx >= next_start_idx:
                continue
            bars = frame.iloc[:obs_idx + 1].copy().reset_index(drop=True)
            current = bars.iloc[-1]
            high20 = float(bars["High"].tail(20).max()) if len(bars) >= 20 else None
            dd_pct = (float(current["Close"]) / high20 - 1.0) * 100 if high20 and high20 > 0 else None
            no_gate = _primary_observation(bars, None)
            gated = _primary_observation(bars, rebase_date)
            rows.append({
                "ticker": symbol.upper(),
                "cycle_id": f"{symbol.upper()}_RB01_{episode_number:03d}",
                "rebase_started_at_rb01_proxy": rebase_date,
                "observation_date": current["Date"].date().isoformat(),
                "offset_bars_from_trigger": offset,
                "close": round(float(current["Close"]), 4),
                "rolling_20d_high": round(high20, 4) if high20 is not None else "",
                "close_vs_20d_high_pct": round(dd_pct, 2) if dd_pct is not None else "",
                "ungated_pattern": no_gate["pattern"],
                "ungated_candidate_pivot": no_gate["pivot"],
                "ungated_pattern_start": no_gate["start"],
                "rebase_gated_pattern": gated["pattern"],
                "rebase_gated_candidate_pivot": gated["pivot"],
                "rebase_gated_pattern_start": gated["start"],
                "ungated_all_candidates_debug": no_gate["candidates"],
                "rebase_gated_all_candidates_debug": gated["candidates"],
                "review_status": "REVIEW_REQUIRED",
                "limitation": "RB-01 price-only proxy; does not reconstruct historical candidate pivot or breakout-event state (RB-02/RB-03). Not ground truth.",
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ohlcv-dir", type=Path, default=ROOT / "data/regression/ohlcv")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--output", type=Path, default=ROOT / "data/regression/rebase_review_candidates.csv")
    args = parser.parse_args()
    all_rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for sym in args.symbols:
        path = args.ohlcv_dir / f"{sym.upper()}.csv"
        if not path.exists():
            missing.append(sym.upper())
            continue
        bars = pd.read_csv(path)
        all_rows.extend(build_review_rows(sym, bars))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "ticker", "cycle_id", "rebase_started_at_rb01_proxy", "observation_date", "offset_bars_from_trigger",
        "close", "rolling_20d_high", "close_vs_20d_high_pct", "ungated_pattern", "ungated_candidate_pivot",
        "ungated_pattern_start", "rebase_gated_pattern", "rebase_gated_candidate_pivot", "rebase_gated_pattern_start",
        "ungated_all_candidates_debug", "rebase_gated_all_candidates_debug", "review_status", "limitation",
    ]
    pd.DataFrame(all_rows, columns=cols).to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"Rebase review candidates: {len(all_rows)} snapshots across {len(args.symbols) - len(missing)} tickers")
    if missing:
        print(f"Missing OHLCV CSVs: {', '.join(missing)}")
    print(f"Output: {args.output}")
    print("Important: review suggestions only; do not mark PASS or auto-fill manifest labels from these outputs.")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
