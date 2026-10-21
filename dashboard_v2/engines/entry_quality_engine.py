"""Entry-quality score normalization. Risk and stop distance are intentionally absent."""
from __future__ import annotations
from typing import Mapping
from ..config.settings import DEFAULT_SETTINGS
from ..models.schemas import ComponentScore, EntryQualityResult, QualityAction, QualityLabel, OfficialStage

EOD_WEIGHTS: dict[str, float] = {
    "pattern": 20.0,
    "breakout_readiness": 25.0,
    "relative_strength": 15.0,
    "momentum": 10.0,
    "ma_structure": 10.0,
}
LIVE_WEIGHTS: dict[str, float] = {
    "pattern": 15.0,
    "setup_quality": 20.0,
    "relative_strength": 15.0,
    "live_momentum": 10.0,
    "live_volume_quality": 10.0,
    "ma_structure": 10.0,
}


def _label(score: float) -> QualityLabel:
    if score >= DEFAULT_SETTINGS.entry_quality_good_min:
        return QualityLabel.GOOD
    if score >= DEFAULT_SETTINGS.entry_quality_caution_min:
        return QualityLabel.CAUTION
    return QualityLabel.WAIT


def score_entry_quality(components: Mapping[str, float | None], *, mode: str = "EOD", stage: OfficialStage | str | None = None, required_components: tuple[str, ...] = ("pattern",)) -> EntryQualityResult:
    """Weighted average of available component scores, normalized to 0..100.

    `None` values are omitted from both numerator and denominator (they are not
    silently converted to zero). During the official BREAKOUT stage EOD
    Breakout Readiness is not applicable, as agreed in the functional spec.
    Initial Risk is neither a component nor a parameter to this function.
    """
    mode = mode.upper()
    if mode == "EOD":
        weights = EOD_WEIGHTS.copy()
    elif mode == "LIVE":
        weights = LIVE_WEIGHTS.copy()
    else:
        raise ValueError("mode must be 'EOD' or 'LIVE'")
    if mode == "EOD" and str(stage) in {OfficialStage.BREAKOUT.value, OfficialStage.BREAKOUT_CONFIRMED.value, "BREAKOUT", "BREAKOUT CONFIRMED"}:
        weights.pop("breakout_readiness", None)

    missing_required = [name for name in required_components if components.get(name) is None or name not in weights]
    if missing_required:
        return EntryQualityResult(
            score=None,
            label=QualityLabel.DATA_INCOMPLETE,
            reason_codes=tuple(f"REQUIRED_COMPONENT_MISSING:{name}" for name in missing_required),
            available_weight=0.0,
            applicable_weight=float(sum(weights.values())),
            component_scores={name: components.get(name) for name in weights},
        )

    weighted_sum = 0.0
    available_weight = 0.0
    for name, weight in weights.items():
        score = components.get(name)
        if score is None:
            continue
        score = float(score)
        if not 0.0 <= score <= 100.0:
            raise ValueError(f"Component '{name}' score must be within 0..100")
        weighted_sum += score * weight
        available_weight += weight
    if available_weight <= 0:
        return EntryQualityResult(None, QualityLabel.DATA_INCOMPLETE, ("NO_VALID_COMPONENTS",), 0.0, float(sum(weights.values())), {})

    normalized = weighted_sum / available_weight
    reasons = [f"COMPONENT_USED:{name}" for name in weights if components.get(name) is not None]
    omitted = [name for name in weights if components.get(name) is None]
    reasons.extend(f"COMPONENT_OMITTED_NORMALIZED:{name}" for name in omitted)
    return EntryQualityResult(
        score=round(normalized, 2),
        label=_label(normalized),
        reason_codes=tuple(reasons),
        available_weight=available_weight,
        applicable_weight=float(sum(weights.values())),
        component_scores={name: components.get(name) for name in weights},
    )


def apply_price_extension_gate(label: QualityLabel, *, candidate_distance_pct: float | None = None, breakout_distance_pct: float | None = None) -> tuple[QualityLabel, QualityAction, tuple[str, ...]]:
    """Apply a separate price-extension gate without altering the raw score."""
    distances = [float(v) for v in (candidate_distance_pct, breakout_distance_pct) if v is not None]
    if not distances:
        return label, QualityAction.NORMAL, ()
    max_distance = max(distances)
    if max_distance >= DEFAULT_SETTINGS.extension_wait_pct:
        return QualityLabel.WAIT, QualityAction.BLOCK_NEW_ENTRY_OR_ADD, ("PRICE_EXTENSION_GE_7_PCT",)
    if max_distance > DEFAULT_SETTINGS.extension_caution_pct:
        final_label = QualityLabel.CAUTION if label == QualityLabel.GOOD else label
        return final_label, QualityAction.CAUTION, ("PRICE_EXTENSION_GT_5_PCT",)
    return label, QualityAction.NORMAL, ()
