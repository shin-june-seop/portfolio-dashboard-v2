"""Fixture-testable OHLCV detectors for V2 price-pattern candidates.

These rules implement the initial automated thresholds from Functional Spec
v0.12. They are screening heuristics, not a claim that chart patterns can be
identified without ambiguity. Detectors are pure: they never write state or
create historical breakout events.
"""
from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Callable

import numpy as np
import pandas as pd

from .data_validation import normalize_ohlcv
from ..config.settings import DEFAULT_PATTERN_SETTINGS as PS
from .pattern_engine import select_primary_pattern
from ..models.schemas import PatternCandidate


# A breakout can distort the most recent bars' base geometry. Search a short
# trailing window for the most recent valid formation end; this is not a
# confirmation/stability delay. A valid structure may be recognized as soon as
# the last bar completes.
DEFAULT_FORMATION_END_LOOKBACK = PS.formation_end_lookback


def _d(value) -> date:
    return pd.Timestamp(value).date()


def _json_safe(value):
    """Convert NumPy/Pandas scalar values in diagnostics to plain serializable types."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (pd.Timestamp, date)):
        return value.isoformat()
    return value


def _prepare(frame: pd.DataFrame, *, already_validated: bool = False) -> pd.DataFrame:
    """Normalize public inputs; allow only the suite runner's private validated fast path."""
    return frame if already_validated else normalize_ohlcv(frame)


def _segment(df: pd.DataFrame, start: int, end: int) -> pd.DataFrame:
    # Positional indexers keep the original frame index but avoid materializing a reset copy.
    return df.iloc[start:end + 1]


def _local_extrema(values: np.ndarray, *, kind: str, order: int = 2) -> list[int]:
    """Return simple local maxima/minima; flat plateaus are intentionally skipped."""
    points: list[int] = []
    if len(values) < order * 2 + 1:
        return points
    for i in range(order, len(values) - order):
        value = values[i]
        if kind == "max":
            if all(value > values[j] for j in range(i - order, i)) and all(
                value >= values[j] for j in range(i + 1, i + order + 1)
            ):
                points.append(i)
        elif kind == "min":
            if all(value < values[j] for j in range(i - order, i)) and all(
                value <= values[j] for j in range(i + 1, i + order + 1)
            ):
                points.append(i)
    return points


def _valid_after_rebase(df: pd.DataFrame, start_idx: int, rebase_started_at: date | str | None) -> bool:
    if rebase_started_at is None:
        return True
    # The pattern suite calls this in tight nested loops. Cache the boundary index
    # once per normalized scan instead of converting a pandas scalar to a date for
    # every possible formation start.
    boundary = df.attrs.get("_v2_rebase_start_index")
    if boundary is not None and df.attrs.get("_v2_rebase_start_date") == str(pd.Timestamp(rebase_started_at).date()):
        return start_idx >= boundary
    start = pd.Timestamp(rebase_started_at).date()
    return _d(df["Date"].iat[start_idx]) >= start


def _candidate(name: str, df: pd.DataFrame, start_idx: int, end_idx: int, pivot: float,
               *, source: str | None = None, upper: float | None = None,
               diagnostics: dict | None = None, reasons: tuple[str, ...] = ()) -> PatternCandidate:
    return PatternCandidate(
        pattern_name=name,
        valid=True,
        candidate_pivot=float(pivot),
        pattern_start_date=_d(df.iloc[start_idx]["Date"]),
        pattern_upper=float(upper if upper is not None else pivot),
        candidate_pivot_source=source or ("VCP" if name == "VCP" else "PATTERN"),
        reason_codes=reasons,
        diagnostics=_json_safe({"formation_end_date": str(_d(df.iloc[end_idx]["Date"])), **(diagnostics or {})}),
    )


def _ends(n: int, *, max_age: int = DEFAULT_FORMATION_END_LOOKBACK) -> range:
    return range(n - 1, max(-1, n - max_age - 2), -1)


def detect_double_bottom(frame: pd.DataFrame, *, rebase_started_at: date | str | None = None, _normalized: bool = False) -> PatternCandidate | None:
    df = _prepare(frame, already_validated=_normalized)
    n = len(df)
    # Search longer structures first at each recent endpoint.
    for end in _ends(n):
        for length in range(min(PS.double_bottom_max_bars, end + 1), PS.double_bottom_min_bars - 1, -1):
            start = end - length + 1
            if start < 0 or not _valid_after_rebase(df, start, rebase_started_at):
                continue
            s = _segment(df, start, end)
            m = len(s)
            lows = s["Low"].to_numpy(float)
            highs = s["High"].to_numpy(float)
            # Require actual interior swing lows. Choosing the lowest point of each
            # half without swing validation falsely labels a monotonic rise as a W base.
            troughs = _local_extrema(lows, kind="min", order=2)
            split = int(m * 0.53)
            left_troughs = [i for i in troughs if 3 <= i < split]
            right_troughs = [i for i in troughs if split <= i < m - 3]
            best = None
            for i1 in left_troughs:
                for i2 in right_troughs:
                    if i2 - i1 < PS.double_bottom_min_trough_separation:
                        continue
                    low1, low2 = lows[i1], lows[i2]
                    if low1 <= 0 or abs(low2 / low1 - 1) > PS.double_bottom_low_tolerance:
                        continue
                    middle_high = float(np.max(highs[i1 + 1:i2]))
                    rebound = middle_high / low1 - 1
                    if not PS.double_bottom_middle_rally_min <= rebound <= PS.double_bottom_middle_rally_max:
                        continue
                    base_high = float(np.max(highs[:i1 + 1]))
                    depth = (base_high - low1) / base_high if base_high > 0 else 1.0
                    if depth > PS.double_bottom_max_depth:
                        continue
                    if float(s.iloc[-1]["Close"]) < low2 * 0.97:
                        continue
                    # Prefer the latest second trough, and within that the closer low pair.
                    rank = (i2, -abs(low2 / low1 - 1))
                    if best is None or rank > best[0]:
                        best = (rank, i1, i2, low1, low2, middle_high, rebound, depth)
            if best is None:
                continue
            _, i1, i2, low1, low2, middle_high, rebound, depth = best
            pivot = float(np.max(highs[i1 + 1:i2]))
            return _candidate("Double Bottom", df, start, end, pivot,
                              diagnostics={"low1_date": str(_d(s.iloc[i1]["Date"])),
                                           "low2_date": str(_d(s.iloc[i2]["Date"])),
                                           "low1": float(low1), "low2": float(low2),
                                           "middle_rebound_pct": round(rebound * 100, 2),
                                           "base_depth_pct": round(depth * 100, 2)})
    return None


def _cup_shape_arrays(high: np.ndarray, low: np.ndarray, *, min_depth: float,
                      max_depth: float, rim_tolerance: float = PS.cup_rim_tolerance):
    """Return cup geometry or None. Arrays contain only the cup bars."""
    n = len(high)
    if n < 30:
        return None
    left_end = max(3, int(n * 0.22))
    right_start = min(n - 3, int(n * 0.78))
    left_i = int(np.argmax(high[:left_end]))
    right_i = int(right_start + np.argmax(high[right_start:]))
    if right_i - left_i < int(n * 0.50):
        return None
    # The low must be central, with meaningful descent and recovery on both sides.
    trough_region = np.arange(max(left_i + 2, int(n * 0.22)), min(right_i - 1, int(n * 0.78)))
    if not len(trough_region):
        return None
    trough_i = int(trough_region[np.argmin(low[trough_region])])
    if not 0.25 <= trough_i / n <= 0.75:
        return None
    left_rim = high[left_i]
    right_rim = high[right_i]
    cup_low = low[trough_i]
    if left_rim <= 0 or cup_low <= 0:
        return None
    depth = (left_rim - cup_low) / left_rim
    if not min_depth <= depth <= max_depth:
        return None
    if abs(right_rim / left_rim - 1) > rim_tolerance:
        return None
    # Reject a V-shaped bottom: middle section should spend multiple bars near the trough.
    around = low[max(0, trough_i - 3):min(n, trough_i + 4)]
    if len(around) < 4 or np.mean(around) > cup_low * 1.08:
        return None
    return {"left_idx": left_i, "trough_idx": trough_i, "right_idx": right_i,
            "left_rim": float(left_rim), "right_rim": float(right_rim),
            "cup_low": float(cup_low), "depth": float(depth)}


def _cup_shape(s: pd.DataFrame, *, min_depth: float, max_depth: float, rim_tolerance: float = PS.cup_rim_tolerance):
    return _cup_shape_arrays(s["High"].to_numpy(float), s["Low"].to_numpy(float),
                             min_depth=min_depth, max_depth=max_depth,
                             rim_tolerance=rim_tolerance)


def detect_cup_handle(frame: pd.DataFrame, *, rebase_started_at: date | str | None = None, _normalized: bool = False) -> PatternCandidate | None:
    df = _prepare(frame, already_validated=_normalized)
    n = len(df)
    highs = df["High"].to_numpy(float)
    lows = df["Low"].to_numpy(float)
    for end in _ends(n):
        # Total duration >=35 bars; cup itself should be >=30 bars, and handle >=5.
        for total_len in range(min(PS.cup_handle_max_total_bars, end + 1), PS.cup_handle_min_total_bars - 1, -1):
            start = end - total_len + 1
            if start < 0 or not _valid_after_rebase(df, start, rebase_started_at):
                continue
            for handle_len in range(PS.cup_handle_min_bars, min(PS.cup_handle_max_bars, total_len - PS.cup_min_bars) + 1):
                cup_end = end - handle_len
                shape = _cup_shape_arrays(highs[start:cup_end + 1], lows[start:cup_end + 1],
                                          min_depth=PS.cup_handle_min_depth, max_depth=PS.cup_handle_max_depth)
                if shape is None:
                    continue
                handle_high = float(np.max(highs[cup_end + 1:end + 1]))
                handle_low = float(np.min(lows[cup_end + 1:end + 1]))
                depth = (handle_high - handle_low) / handle_high if handle_high > 0 else 1.0
                cup_midpoint = shape["cup_low"] + (shape["left_rim"] - shape["cup_low"]) / 2
                if depth > PS.cup_handle_max_pullback or handle_low < cup_midpoint:
                    continue
                # Avoid labelling an uninterrupted rim-to-breakout move as a handle.
                if handle_low > handle_high * (1 - PS.cup_handle_min_pullback):
                    continue
                return _candidate("Cup & Handle", df, start, end, handle_high,
                                  diagnostics={"cup_depth_pct": round(shape["depth"] * 100, 2),
                                               "handle_depth_pct": round(depth * 100, 2),
                                               "handle_bars": handle_len,
                                               "left_rim": shape["left_rim"],
                                               "right_rim": shape["right_rim"]})
    return None


def detect_cup_without_handle(frame: pd.DataFrame, *, rebase_started_at: date | str | None = None, _normalized: bool = False) -> PatternCandidate | None:
    df = _prepare(frame, already_validated=_normalized)
    n = len(df)
    for end in _ends(n):
        for length in range(min(PS.cup_without_handle_max_bars, end + 1), PS.cup_without_handle_min_bars - 1, -1):
            start = end - length + 1
            if start < 0 or not _valid_after_rebase(df, start, rebase_started_at):
                continue
            s = _segment(df, start, end)
            shape = _cup_shape(s, min_depth=PS.cup_without_handle_min_depth, max_depth=PS.cup_without_handle_max_depth)
            if shape is None:
                continue
            # A meaningful pullback from the recovered right rim is a handle candidate;
            # those should be scored by the dedicated Cup & Handle detector instead.
            post_rim_lows = s.iloc[shape["right_idx"]:]["Low"]
            right_peak = shape["right_rim"]
            pullback = (right_peak - float(post_rim_lows.min())) / right_peak
            if pullback > PS.cup_without_handle_max_rim_pullback:
                continue
            # Distinguish long/shallow rounded saucers below, leaving deeper/shorter cups here.
            if length >= PS.saucer_min_bars and shape["depth"] <= PS.saucer_max_depth:
                continue
            pivot = max(shape["left_rim"], shape["right_rim"])
            return _candidate("Cup Without Handle", df, start, end, pivot,
                              diagnostics={"cup_depth_pct": round(shape["depth"] * 100, 2),
                                           "duration_bars": length,
                                           "left_rim": shape["left_rim"],
                                           "right_rim": shape["right_rim"]})
    return None


def detect_flat_base(frame: pd.DataFrame, *, rebase_started_at: date | str | None = None, _normalized: bool = False) -> PatternCandidate | None:
    df = _prepare(frame, already_validated=_normalized)
    n = len(df)
    for end in _ends(n):
        for length in range(min(PS.flat_base_max_bars, end + 1), PS.flat_base_min_bars - 1, -1):
            start = end - length + 1
            if start < 0 or not _valid_after_rebase(df, start, rebase_started_at):
                continue
            s = _segment(df, start, end)
            top = float(s["High"].max())
            bottom = float(s["Low"].min())
            if top <= 0:
                continue
            depth = (top - bottom) / top
            if depth > PS.flat_base_max_depth:
                continue
            # Require a prior advance, not an arbitrary range during a longer downtrend.
            prior_start = max(0, start - PS.flat_base_prior_lookback)
            prior = df.iloc[prior_start:start]
            if len(prior) < PS.flat_base_min_prior_bars:
                continue
            prior_low = float(prior["Low"].min())
            base_first_close = float(df.iloc[start]["Close"])
            advance = base_first_close / prior_low - 1 if prior_low > 0 else -1
            if advance < PS.flat_base_prior_advance_min:
                continue
            # Suppress a steadily trending base: range must be relatively balanced around a band.
            closes = s["Close"].to_numpy(float)
            x = np.arange(len(closes), dtype=float)
            slope = float(np.polyfit(x, closes, 1)[0]) if len(closes) > 2 else 0.0
            normalized_slope = abs(slope) * (len(closes) - 1) / max(top, 1e-9)
            if normalized_slope > PS.flat_base_max_normalized_slope:
                continue
            return _candidate("Flat Base", df, start, end, top,
                              diagnostics={"depth_pct": round(depth * 100, 2),
                                           "prior_advance_pct": round(advance * 100, 2),
                                           "duration_bars": length})
    return None


def detect_saucer_base(frame: pd.DataFrame, *, rebase_started_at: date | str | None = None, _normalized: bool = False) -> PatternCandidate | None:
    df = _prepare(frame, already_validated=_normalized)
    n = len(df)
    for end in _ends(n):
        for length in range(min(PS.saucer_max_bars, end + 1), PS.saucer_min_bars - 1, -1):
            start = end - length + 1
            if start < 0 or not _valid_after_rebase(df, start, rebase_started_at):
                continue
            s = _segment(df, start, end)
            high = s["High"].to_numpy(float)
            low = s["Low"].to_numpy(float)
            top = float(max(np.max(high[:max(3, int(length * .20))]), np.max(high[int(length * .80):])))
            bottom = float(np.min(low))
            if top <= 0:
                continue
            depth = (top - bottom) / top
            if depth > PS.saucer_max_depth or depth < PS.saucer_min_depth:
                continue
            trough = int(np.argmin(low))
            if not 0.30 <= trough / length <= 0.70:
                continue
            left_mean = float(np.mean(low[:max(3, int(length * .18))]))
            middle_mean = float(np.mean(low[int(length * .35):int(length * .65)]))
            right_mean = float(np.mean(low[int(length * .82):]))
            # Broad and smooth: center meaningfully lower, both rims recovering.
            if middle_mean > min(left_mean, right_mean) * 0.94:
                continue
            if abs(right_mean / left_mean - 1) > PS.saucer_rim_tolerance:
                continue
            # A one- or two-week V snap-back is not a saucer.
            low_local = _local_extrema(low, kind="min", order=3)
            if not low_local or min(abs(i - trough) for i in low_local) > 5:
                continue
            return _candidate("Saucer Base", df, start, end, top,
                              diagnostics={"depth_pct": round(depth * 100, 2),
                                           "duration_bars": length,
                                           "trough_fraction": round(trough / length, 3)})
    return None


def detect_ascending_base(frame: pd.DataFrame, *, rebase_started_at: date | str | None = None, _normalized: bool = False) -> PatternCandidate | None:
    df = _prepare(frame, already_validated=_normalized)
    n = len(df)
    for end in _ends(n):
        for length in range(min(PS.ascending_base_max_bars, end + 1), PS.ascending_base_min_bars - 1, -1):
            start = end - length + 1
            if start < 0 or not _valid_after_rebase(df, start, rebase_started_at):
                continue
            s = _segment(df, start, end)
            highs = s["High"].to_numpy(float)
            lows = s["Low"].to_numpy(float)
            peaks = _local_extrema(highs, kind="max", order=2)
            troughs = _local_extrema(lows, kind="min", order=2)
            # Pair each peak with the next trough. Require three higher-high/higher-low pullbacks.
            pullbacks: list[tuple[int, int, float, float]] = []
            for peak in peaks:
                subsequent = [t for t in troughs if t > peak]
                if not subsequent:
                    continue
                trough = subsequent[0]
                depth = (highs[peak] - lows[trough]) / highs[peak]
                if PS.ascending_base_min_pullback <= depth <= PS.ascending_base_max_pullback:
                    pullbacks.append((peak, trough, highs[peak], lows[trough]))
            # Avoid double-counting overlapping peak-to-trough pairs.
            selected: list[tuple[int, int, float, float]] = []
            last_trough = -1
            for item in pullbacks:
                if item[1] > last_trough:
                    selected.append(item)
                    last_trough = item[1]
            if len(selected) < PS.ascending_base_required_pullbacks:
                continue
            last3 = selected[-3:]
            if not all(last3[i][2] < last3[i + 1][2] and last3[i][3] < last3[i + 1][3] for i in range(2)):
                continue
            last_trough_idx = last3[-1][1]
            pivot = float(np.max(highs[last_trough_idx:]))
            return _candidate("Ascending Base", df, start, end, pivot,
                              diagnostics={"pullbacks": [{"peak": p, "trough": t,
                                                          "depth_pct": round((peak - trough_low) / peak * 100, 2)}
                                                         for p, t, peak, trough_low in last3],
                                           "duration_bars": length})
    return None


def detect_high_tight_flag(frame: pd.DataFrame, *, rebase_started_at: date | str | None = None, _normalized: bool = False) -> PatternCandidate | None:
    df = _prepare(frame, already_validated=_normalized)
    n = len(df)
    for end in _ends(n):
        for flag_len in range(PS.high_tight_flag_max_bars, PS.high_tight_flag_min_bars - 1, -1):
            flag_start = end - flag_len + 1
            if flag_start < 1:
                continue
            flag = _segment(df, flag_start, end)
            flag_high = float(flag["High"].max())
            flag_low = float(flag["Low"].min())
            if flag_high <= 0 or (flag_high - flag_low) / flag_high > PS.high_tight_flag_max_depth:
                continue
            # Locate a sharp pole ending just before flag start. Pole may pre-date a rebase cycle,
            # but its gain and the complete flag are always revalidated.
            pole_start_min = max(0, flag_start - PS.high_tight_flag_max_pole_bars)
            pole_closes = df["Close"].to_numpy(float)[pole_start_min:flag_start]
            if len(pole_closes) < 5:
                continue
            eligible = np.flatnonzero((pole_closes > 0) & (flag_high / np.maximum(pole_closes, 1e-9) - 1 >= PS.high_tight_flag_min_pole_gain))
            if not len(eligible):
                continue
            pole_start = pole_start_min + int(eligible[0])
            if rebase_started_at is not None:
                rebase_date = pd.Timestamp(rebase_started_at).date()
                if _d(df.iloc[flag_start]["Date"]) < rebase_date:
                    continue
            # Flag should be a sideways/downward consolidation after the pole, not a new vertical advance.
            fclose = flag["Close"].to_numpy(float)
            trend = (fclose[-1] / fclose[0] - 1) if fclose[0] > 0 else 1.0
            if trend > PS.high_tight_flag_max_flag_advance:
                continue
            return _candidate("High Tight Flag", df, pole_start, end, flag_high,
                              diagnostics={"pole_start_date": str(_d(df.iloc[pole_start]["Date"])),
                                           "pole_gain_pct": round((flag_high / float(df.iloc[pole_start]["Close"]) - 1) * 100, 2),
                                           "flag_depth_pct": round((flag_high - flag_low) / flag_high * 100, 2),
                                           "flag_bars": flag_len})
    return None


def detect_vcp(frame: pd.DataFrame, *, rebase_started_at: date | str | None = None, _normalized: bool = False) -> PatternCandidate | None:
    df = _prepare(frame, already_validated=_normalized)
    n = len(df)
    for end in _ends(n):
        for length in range(min(PS.vcp_max_bars, end + 1), PS.vcp_min_bars - 1, -1):
            start = end - length + 1
            if start < 0 or not _valid_after_rebase(df, start, rebase_started_at):
                continue
            s = _segment(df, start, end)
            high = s["High"].to_numpy(float)
            low = s["Low"].to_numpy(float)
            vol = s["Volume"].to_numpy(float)
            peaks = _local_extrema(high, kind="max", order=2)
            troughs = _local_extrema(low, kind="min", order=2)
            swings: list[dict] = []
            for pi, peak in enumerate(peaks):
                next_peak = peaks[pi + 1] if pi + 1 < len(peaks) else len(s) - 1
                cand_troughs = [t for t in troughs if peak < t < next_peak]
                if not cand_troughs:
                    continue
                trough = min(cand_troughs, key=lambda t: low[t])
                depth = (high[peak] - low[trough]) / high[peak] if high[peak] > 0 else 0
                if depth >= PS.vcp_min_contraction:
                    swings.append({"peak": peak, "trough": trough, "depth": depth,
                                   "volume": float(np.mean(vol[peak:trough + 1]))})
            # Select the most recent contiguous run of 2..6 descending contractions.
            found = None
            for count in range(min(PS.vcp_max_contractions, len(swings)), PS.vcp_min_contractions - 1, -1):
                run = swings[-count:]
                depths = [x["depth"] for x in run]
                if not (PS.vcp_first_contraction_min <= depths[0] <= PS.vcp_first_contraction_max and depths[-1] <= PS.vcp_last_contraction_max):
                    continue
                if any(depths[i] > PS.vcp_contraction_ratio * depths[i - 1] for i in range(1, len(depths))):
                    continue
                if any(low[run[i]["trough"]] <= low[run[i - 1]["trough"]] for i in range(1, len(run))):
                    continue
                if run[-1]["volume"] > PS.vcp_max_final_to_initial_volume * run[0]["volume"]:
                    continue
                first_peak = run[0]["peak"]
                pre_low = float(np.min(low[:first_peak + 1]))
                prior_advance = high[first_peak] / pre_low - 1 if pre_low > 0 else 0
                if prior_advance < PS.vcp_min_prior_advance:
                    continue
                found = (run, prior_advance)
                break
            if found is None:
                continue
            run, prior_advance = found
            last = run[-1]
            # Highest high in the narrowest/latest contraction is the active VCP pivot.
            last_peak_idx = last["peak"]
            last_trough_idx = last["trough"]
            pivot = float(np.max(high[last_peak_idx:last_trough_idx + 1]))
            return _candidate("VCP", df, start, end, pivot, source="VCP",
                              diagnostics={"contractions_pct": [round(x["depth"] * 100, 2) for x in run],
                                           "contraction_volumes": [round(x["volume"], 2) for x in run],
                                           "prior_advance_pct": round(prior_advance * 100, 2),
                                           "duration_bars": length})
    return None


def detect_base_on_base(frame: pd.DataFrame, *, prior_breakout_pivot: float | None,
                        prior_breakout_date: date | str | None = None,
                        rebase_started_at: date | str | None = None,
                        _normalized: bool = False) -> PatternCandidate | None:
    """Detect a second flat-style base above a known first-base breakout pivot.

    The prior breakout pivot must be supplied from preserved event state; this
    detector does not infer or overwrite historical breakout records.
    """
    if prior_breakout_pivot is None or not isfinite(float(prior_breakout_pivot)) or prior_breakout_pivot <= 0:
        return None
    df = _prepare(frame, already_validated=_normalized)
    n = len(df)
    closes = df["Close"].to_numpy(float)
    highs = df["High"].to_numpy(float)
    breakout_idx = None
    if prior_breakout_date is not None:
        event_date = pd.Timestamp(prior_breakout_date).date()
        matches = [i for i, value in enumerate(df["Date"]) if _d(value) == event_date]
        if matches:
            breakout_idx = matches[-1]
    else:
        # Infer only an actual observed close-cross above the preserved historical pivot.
        crosses = [i for i in range(1, n) if closes[i - 1] <= prior_breakout_pivot < closes[i]]
        if crosses:
            breakout_idx = crosses[-1]
    if breakout_idx is None:
        return None

    for end in _ends(n):
        # A candidate ending before the first breakout cannot be a second base.
        # Guard this before slicing: highs[breakout_idx:end + 1] is empty when
        # end < breakout_idx, and np.max(empty) raises instead of rejecting it.
        if end < breakout_idx:
            continue
        # A second base must follow the first breakout, and the advance from that pivot
        # must remain below +20% through the candidate's formation end, not only in the
        # final consolidation window. Use High to avoid missing intraperiod extensions.
        if float(np.max(highs[breakout_idx:end + 1])) / prior_breakout_pivot - 1 >= PS.base_on_base_max_advance_from_prior_pivot:
            continue
        for length in range(min(PS.base_on_base_max_bars, end + 1), PS.base_on_base_min_bars - 1, -1):
            start = end - length + 1
            if start <= breakout_idx or not _valid_after_rebase(df, start, rebase_started_at):
                continue
            s = _segment(df, start, end)
            top = float(s["High"].max())
            bottom = float(s["Low"].min())
            if top <= 0 or (top - bottom) / top > PS.base_on_base_max_depth:
                continue
            above_ratio = float((s["Close"] > prior_breakout_pivot).mean())
            if above_ratio < PS.base_on_base_min_days_above_prior_pivot:
                continue
            max_advance_pct = float(np.max(highs[breakout_idx:end + 1])) / prior_breakout_pivot - 1
            return _candidate("Base-on-Base", df, start, end, top,
                              diagnostics={"second_base_type": "Flat Base",
                                           "first_breakout_pivot": float(prior_breakout_pivot),
                                           "first_breakout_date": str(_d(df.iloc[breakout_idx]["Date"])),
                                           "second_base_days_above_first_pivot_pct": round(above_ratio * 100, 2),
                                           "max_advance_since_first_pivot_pct": round(max_advance_pct * 100, 2)})
    return None


def detect_pattern_candidates(
    frame: pd.DataFrame,
    *,
    rebase_started_at: date | str | None = None,
    prior_breakout_pivot: float | None = None,
    prior_breakout_date: date | str | None = None,
) -> list[PatternCandidate]:
    """Run available V2 detector set and return every valid candidate for diagnostics.

    UI consumers must call ``select_primary_pattern`` and display only that one
    main pattern/pivot. Candidate detector diagnostics are for test/debug only.
    """
    df = normalize_ohlcv(frame)
    if df.empty:
        return []
    if rebase_started_at is not None:
        rebase_date = pd.Timestamp(rebase_started_at).date()
        date_values = pd.to_datetime(df["Date"]).to_numpy(dtype="datetime64[ns]")
        df.attrs["_v2_rebase_start_index"] = int(np.searchsorted(
            date_values, np.datetime64(rebase_date, "ns"), side="left"
        ))
        df.attrs["_v2_rebase_start_date"] = str(rebase_date)
    detectors: tuple[Callable[..., PatternCandidate | None], ...] = (
        detect_high_tight_flag,
        detect_vcp,
        detect_base_on_base,
        detect_cup_handle,
        detect_double_bottom,
        detect_flat_base,
        detect_cup_without_handle,
        detect_saucer_base,
        detect_ascending_base,
    )
    found: list[PatternCandidate] = []
    for detector in detectors:
        kwargs = {"rebase_started_at": rebase_started_at, "_normalized": True}
        if detector is detect_base_on_base:
            kwargs["prior_breakout_pivot"] = prior_breakout_pivot
            kwargs["prior_breakout_date"] = prior_breakout_date
        candidate = detector(df, **kwargs)
        if candidate is not None:
            found.append(candidate)
    return found


def detect_primary_pattern(frame: pd.DataFrame, *, rebase_started_at: date | str | None = None,
                           prior_breakout_pivot: float | None = None,
                           prior_breakout_date: date | str | None = None) -> PatternCandidate | None:
    """Return the single pattern and candidate pivot used by downstream engines."""
    return select_primary_pattern(detect_pattern_candidates(
        frame, rebase_started_at=rebase_started_at,
        prior_breakout_pivot=prior_breakout_pivot,
        prior_breakout_date=prior_breakout_date,
    ))
