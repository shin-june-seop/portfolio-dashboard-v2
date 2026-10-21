"""Explicit V2 defaults. Functional Spec v0.12 remains the source of truth."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EngineSettings:
    pivot_approach_min_pct: float = -5.0
    breakout_confirmed_min_rvol: float = 1.30
    initial_stop_atr_multiple: float = 1.5
    initial_stop_atr_period: int = 14
    trailing_activation_r: float = 1.0
    trailing_stop_atr_period: int = 22
    trailing_stop_atr_multiple: float = 3.0
    entry_quality_good_min: float = 75.0
    entry_quality_caution_min: float = 60.0
    extension_caution_pct: float = 5.0
    extension_wait_pct: float = 7.0
    pocket_pivot_lookback: int = 10


@dataclass(frozen=True, slots=True)
class PatternSettings:
    """Initial automation thresholds adopted from Functional Spec v0.12.

    These are configuration defaults for empirical validation, not universal
    textbook cutoffs. Threshold updates should be paired with regression tests.
    """
    formation_end_lookback: int = 2

    # Double Bottom
    double_bottom_min_bars: int = 35
    double_bottom_max_bars: int = 100
    double_bottom_min_trough_separation: int = 20
    double_bottom_low_tolerance: float = 0.03
    double_bottom_middle_rally_min: float = 0.10
    double_bottom_middle_rally_max: float = 0.20
    double_bottom_max_depth: float = 0.40

    # Cup patterns
    cup_handle_min_total_bars: int = 35
    cup_handle_max_total_bars: int = 75
    cup_min_bars: int = 30
    cup_handle_min_bars: int = 5
    cup_handle_max_bars: int = 10
    cup_handle_min_depth: float = 0.15
    cup_handle_max_depth: float = 0.30
    cup_without_handle_min_bars: int = 30
    cup_without_handle_max_bars: int = 90
    cup_without_handle_min_depth: float = 0.15
    cup_without_handle_max_depth: float = 0.33
    cup_rim_tolerance: float = 0.05
    cup_handle_max_pullback: float = 0.15
    cup_handle_min_pullback: float = 0.015
    cup_without_handle_max_rim_pullback: float = 0.06

    # Flat and Saucer bases
    flat_base_min_bars: int = 25
    flat_base_max_bars: int = 45
    flat_base_max_depth: float = 0.15
    flat_base_prior_advance_min: float = 0.20
    flat_base_prior_lookback: int = 60
    flat_base_min_prior_bars: int = 15
    flat_base_max_normalized_slope: float = 0.10
    saucer_min_bars: int = 40
    saucer_max_bars: int = 100
    saucer_min_depth: float = 0.06
    saucer_max_depth: float = 0.25
    saucer_rim_tolerance: float = 0.08

    # Ascending Base
    ascending_base_min_bars: int = 45
    ascending_base_max_bars: int = 80
    ascending_base_min_pullback: float = 0.06
    ascending_base_max_pullback: float = 0.25
    ascending_base_required_pullbacks: int = 3

    # High Tight Flag
    high_tight_flag_min_bars: int = 15
    high_tight_flag_max_bars: int = 25
    high_tight_flag_max_pole_bars: int = 40
    high_tight_flag_min_pole_gain: float = 1.00
    high_tight_flag_max_depth: float = 0.25
    high_tight_flag_max_flag_advance: float = 0.10

    # VCP
    vcp_min_bars: int = 20
    vcp_max_bars: int = 80
    vcp_min_contractions: int = 2
    vcp_max_contractions: int = 6
    vcp_min_contraction: float = 0.03
    vcp_first_contraction_min: float = 0.10
    vcp_first_contraction_max: float = 0.35
    vcp_last_contraction_max: float = 0.08
    vcp_contraction_ratio: float = 0.75
    vcp_min_prior_advance: float = 0.30
    vcp_max_final_to_initial_volume: float = 0.75

    # Base-on-Base (second base is a flat-style base in this initial detector)
    base_on_base_min_bars: int = 25
    base_on_base_max_bars: int = 45
    base_on_base_max_depth: float = 0.15
    base_on_base_min_days_above_prior_pivot: float = 0.50
    base_on_base_max_advance_from_prior_pivot: float = 0.20


DEFAULT_SETTINGS = EngineSettings()
DEFAULT_PATTERN_SETTINGS = PatternSettings()
