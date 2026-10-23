"""Deterministic summaries for the immutable V2 PP trade journal."""
from __future__ import annotations
from collections import deque
from datetime import date
from typing import Any, Mapping, Sequence
import math
import pandas as pd


def _day(value: Any) -> str:
    return str(value or "")[:10]


def _num(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def summarize_trade(
    trade: Mapping[str, Any], adds: Sequence[Mapping[str, Any]], exits: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """FIFO cash P&L and remaining lots; never mutates supplied journal records."""
    events: list[tuple[str, str, int, dict[str, Any]]] = []
    initial = {
        "entry_date": _day(trade.get("initial_entry_date")),
        "entry_price": float(trade.get("initial_entry_price") or 0),
        "shares": float(trade.get("initial_shares") or 0),
        "initial_stop_price": _num(trade.get("initial_stop_price")),
        "created_at": trade.get("created_at") or "",
    }
    events.append((initial["entry_date"], str(initial["created_at"]), 0, {"kind": "ENTRY", **initial}))
    for row in adds:
        if str(row.get("trade_id")) != str(trade.get("id")):
            continue
        events.append((_day(row.get("entry_date")), str(row.get("created_at") or ""), 0, {
            "kind": "ENTRY", "entry_date": _day(row.get("entry_date")),
            "entry_price": float(row.get("entry_price") or 0),
            "shares": float(row.get("shares") or 0),
            "initial_stop_price": _num(row.get("initial_stop_price")),
            "created_at": row.get("created_at") or "",
        }))
    for row in exits:
        if str(row.get("trade_id")) != str(trade.get("id")):
            continue
        events.append((_day(row.get("exit_date")), str(row.get("created_at") or ""), 1, {
            "kind": "EXIT", "exit_date": _day(row.get("exit_date")),
            "exit_price": float(row.get("exit_price") or 0),
            "shares": float(row.get("shares") or 0),
            "created_at": row.get("created_at") or "",
        }))
    events.sort(key=lambda event: (event[0], event[1], event[2]))

    # Each open lot tracks original fill price/stop and shares not yet sold.
    lots: deque[dict[str, Any]] = deque()
    total_entered = 0.0
    total_exited = 0.0
    realized_pnl = 0.0
    realized_cost = 0.0
    realized_risk = 0.0
    sold_risk_known = True
    for _, _, _, event in events:
        quantity = float(event.get("shares") or 0)
        if event["kind"] == "ENTRY":
            if quantity <= 0 or float(event.get("entry_price") or 0) <= 0:
                continue
            lots.append({
                "entry_price": float(event["entry_price"]), "shares": quantity,
                "initial_stop_price": event.get("initial_stop_price"),
            })
            total_entered += quantity
            continue
        if quantity <= 0 or float(event.get("exit_price") or 0) <= 0:
            continue
        remaining_exit = quantity
        exit_price = float(event["exit_price"])
        while remaining_exit > 1e-8 and lots:
            lot = lots[0]
            sold = min(remaining_exit, float(lot["shares"]))
            entry_price = float(lot["entry_price"])
            realized_pnl += (exit_price - entry_price) * sold
            realized_cost += entry_price * sold
            stop = _num(lot.get("initial_stop_price"))
            if stop is None or stop >= entry_price:
                sold_risk_known = False
            else:
                realized_risk += (entry_price - stop) * sold
            lot["shares"] = float(lot["shares"]) - sold
            remaining_exit -= sold
            total_exited += sold
            if lot["shares"] <= 1e-8:
                lots.popleft()
        # An orphan exit should not be present under repository chronology checks;
        # retain a diagnostic instead of silently counting it as a valid closure.
        if remaining_exit > 1e-6:
            sold_risk_known = False

    remaining_shares = sum(max(0.0, float(lot["shares"])) for lot in lots)
    tolerance = max(1e-8, total_entered * 1e-8)
    remaining_shares = 0.0 if remaining_shares <= tolerance else remaining_shares
    if total_entered > 0 and remaining_shares == 0:
        status = "CLOSED"
    elif total_exited > tolerance:
        status = "PARTIAL"
    else:
        status = "OPEN"
    remaining_cost = sum(float(lot["entry_price"]) * float(lot["shares"]) for lot in lots)
    avg_remaining_cost = remaining_cost / remaining_shares if remaining_shares > tolerance else None
    avg_all_entry_price = (
        sum(float(e[3].get("entry_price") or 0) * float(e[3].get("shares") or 0) for e in events if e[3]["kind"] == "ENTRY") / total_entered
        if total_entered > 0 else None
    )
    realized_return_pct = (realized_pnl / realized_cost * 100.0) if realized_cost > 0 else None
    r_multiple = realized_pnl / realized_risk if realized_risk > 0 and sold_risk_known else None
    return {
        "trade_id": str(trade.get("id", "")),
        "ticker": str(trade.get("ticker", "")),
        "status": status,
        "initial_entry_date": _day(trade.get("initial_entry_date")),
        "initial_entry_stage": trade.get("initial_entry_stage") or "BASE",
        "initial_entry_price": float(trade.get("initial_entry_price") or 0),
        "total_entered_shares": total_entered,
        "total_exited_shares": total_exited,
        "remaining_shares": remaining_shares,
        "average_remaining_cost": avg_remaining_cost,
        "average_all_entry_price": avg_all_entry_price,
        "realized_pnl": realized_pnl,
        "realized_cost": realized_cost,
        "realized_return_pct": realized_return_pct,
        "r_multiple": r_multiple,
        "exits_count": sum(1 for e in events if e[3]["kind"] == "EXIT"),
        "adds_count": sum(1 for e in events if e[3]["kind"] == "ENTRY") - 1,
    }


def summarize_trades(trade_summaries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    all_rows = list(trade_summaries)
    closed = [r for r in all_rows if r.get("status") == "CLOSED"]
    p_l = [_num(r.get("realized_pnl")) or 0.0 for r in closed]
    returns = [float(_num(r.get("realized_return_pct"))) for r in closed if _num(r.get("realized_return_pct")) is not None]
    wins = [float(r["realized_return_pct"]) for r in closed if _num(r.get("realized_return_pct")) is not None and float(r["realized_return_pct"]) > 0]
    losses = [float(r["realized_return_pct"]) for r in closed if _num(r.get("realized_return_pct")) is not None and float(r["realized_return_pct"]) < 0]
    gross_profit = sum(max(0.0, val) for val in p_l)
    gross_loss = abs(sum(min(0.0, val) for val in p_l))
    r_values = [float(_num(r.get("r_multiple"))) for r in closed if _num(r.get("r_multiple")) is not None]
    return {
        "trade_count": len(all_rows),
        "open_count": sum(1 for r in all_rows if r.get("status") == "OPEN"),
        "partial_count": sum(1 for r in all_rows if r.get("status") == "PARTIAL"),
        "closed_count": len(closed),
        "wins": sum(1 for r in closed if (_num(r.get("realized_pnl")) or 0.0) > 0),
        "win_rate_pct": (sum(1 for r in closed if (_num(r.get("realized_pnl")) or 0.0) > 0) / len(closed) * 100.0) if closed else None,
        "total_realized_pnl": sum(_num(r.get("realized_pnl")) or 0.0 for r in all_rows),
        "avg_win_pct": sum(wins) / len(wins) if wins else None,
        "avg_loss_pct": sum(losses) / len(losses) if losses else None,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "expectancy_pct": sum(returns) / len(returns) if returns else None,
        "avg_r_multiple": sum(r_values) / len(r_values) if r_values else None,
    }


def analyze_pp_signal_outcome(signal: Mapping[str, Any], raw_bars: pd.DataFrame) -> dict[str, Any]:
    """Measure forward EOD price excursion; does not claim official breakout/failure events."""
    result = {
        "ticker": str(signal.get("ticker", "")),
        "signal_date": _day(signal.get("signal_date")),
        "candidate_pivot_touched": None,
        "hit_plus_10_pct": None,
        "hit_plus_20_pct": None,
        "mfe_pct": None,
        "mae_pct": None,
        "observed_bars": 0,
        "outcome_status": "NO_FUTURE_BARS",
    }
    if raw_bars is None or raw_bars.empty or "Date" not in raw_bars:
        result["outcome_status"] = "DATA_UNAVAILABLE"
        return result
    frame = raw_bars.copy()
    dates = pd.to_datetime(frame["Date"], errors="coerce")
    frame = frame.loc[dates.dt.date > date.fromisoformat(result["signal_date"])].copy()
    if frame.empty:
        return result
    for column in ("High", "Low"):
        if column not in frame:
            result["outcome_status"] = "DATA_INCOMPLETE"
            return result
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[["High", "Low"]].isna().any().any() or not frame[["High", "Low"]].apply(lambda col: col.map(math.isfinite)).all().all():
        result["outcome_status"] = "DATA_INCOMPLETE"
        return result
    price = _num(signal.get("signal_price"))
    if price is None or price <= 0:
        result["outcome_status"] = "DATA_INCOMPLETE"
        return result
    highest = float(frame["High"].max())
    lowest = float(frame["Low"].min())
    mfe = (highest / price - 1.0) * 100.0
    mae = (lowest / price - 1.0) * 100.0
    pivot = _num(signal.get("candidate_pivot"))
    result.update({
        "candidate_pivot_touched": (highest >= pivot) if pivot is not None else None,
        "hit_plus_10_pct": highest >= price * 1.10,
        "hit_plus_20_pct": highest >= price * 1.20,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "observed_bars": len(frame),
        "outcome_status": "OBSERVED_PRICE_EXCURSION",
    })
    return result
