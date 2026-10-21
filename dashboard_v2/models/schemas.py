"""Shared, serializable domain contracts used by every screen/service."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any, Mapping


class OfficialStage(StrEnum):
    BASE = "BASE"
    BASE_PLUS_PP = "BASE + PP"
    PIVOT_APPROACH = "PIVOT APPROACH"
    BREAKOUT = "BREAKOUT"
    BREAKOUT_CONFIRMED = "BREAKOUT CONFIRMED"
    BREAKOUT_FAILURE = "BREAKOUT FAILURE"


class QualityLabel(StrEnum):
    GOOD = "GOOD"
    CAUTION = "CAUTION"
    WAIT = "WAIT"
    DATA_INCOMPLETE = "DATA INCOMPLETE"


class QualityAction(StrEnum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    BLOCK_NEW_ENTRY_OR_ADD = "WAIT"


class DataStatus(StrEnum):
    OK = "OK"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INVALID_DATA = "INVALID_DATA"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class StageInput:
    as_of_date: date
    close: float
    previous_close: float | None
    candidate_pivot: float | None = None
    candidate_valid: bool = False
    candidate_source: str | None = None
    rebase_forming: bool = False
    active_breakout_pivot: float | None = None
    breakout_cycle_active: bool = False
    next_pivot: float | None = None
    next_pivot_valid: bool = False
    eod_rvol: float | None = None
    base_context_valid: bool = False
    pocket_pivot_valid: bool = False


@dataclass(frozen=True, slots=True)
class StageResult:
    as_of_date: date
    stage: OfficialStage
    reason_codes: tuple[str, ...] = ()
    breakout_event: bool = False
    breakout_event_pivot: float | None = None
    breakout_confirmed: bool = False
    breakout_failure: bool = False
    activate_breakout_pivot: float | None = None


@dataclass(frozen=True, slots=True)
class ComponentScore:
    name: str
    score: float | None  # 0..100 within that component
    weight: float
    applicable: bool = True


@dataclass(frozen=True, slots=True)
class EntryQualityResult:
    score: float | None
    label: QualityLabel
    reason_codes: tuple[str, ...] = ()
    available_weight: float = 0.0
    applicable_weight: float = 0.0
    component_scores: Mapping[str, float | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InitialRiskResult:
    reference_price: float | None
    stop_price: float | None
    risk_pct: float | None
    source: str | None
    reason_code: str


@dataclass(frozen=True, slots=True)
class StopState:
    entry_date: date
    entry_price: float
    initial_stop_price: float
    trailing_stop_price: float | None = None
    trailing_active: bool = False
    effective_stop_price: float | None = None
    highest_high_since_entry: float | None = None


@dataclass(frozen=True, slots=True)
class ExitSignal:
    as_of_date: date
    triggered: bool
    close: float
    effective_stop_price: float
    reason_code: str | None
    order_submitted: bool = False
    position_closed: bool = False


@dataclass(frozen=True, slots=True)
class PocketPivotResult:
    as_of_date: date | None
    valid_signal: bool
    reason_codes: tuple[str, ...]
    signal_volume: float | None = None
    max_prior_down_day_volume: float | None = None
    reference_ma: str | None = None
    context_valid: bool = False


@dataclass(frozen=True, slots=True)
class PatternResult:
    as_of_date: date
    pattern_name: str
    pattern_status: str
    candidate_pivot: float | None
    candidate_pivot_source: str | None
    pattern_upper: float | None = None
    pattern_start_date: date | None = None
    twenty_day_reference: float | None = None
    rebase_started_at: date | None = None
    rebase_low: float | None = None
    rebase_low_date: date | None = None
    data_status: DataStatus = DataStatus.OK
    reason_codes: tuple[str, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class RebaseState:
    active: bool
    started_at: date | None = None
    low: float | None = None
    low_date: date | None = None
    primary_reason: str | None = None
    secondary_reasons: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class PatternCandidate:
    """One structurally-validated detector result before main-pattern selection."""
    pattern_name: str
    valid: bool
    candidate_pivot: float | None
    pattern_start_date: date | None
    pattern_upper: float | None = None
    candidate_pivot_source: str = "PATTERN"
    reason_codes: tuple[str, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
