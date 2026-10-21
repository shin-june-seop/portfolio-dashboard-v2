import pytest
from dashboard_v2.engines.entry_quality_engine import score_entry_quality, apply_price_extension_gate
from dashboard_v2.models.schemas import OfficialStage, QualityAction, QualityLabel


def test_quality_score_is_normalized_over_available_components():
    result = score_entry_quality({"pattern": 80, "relative_strength": 70}, mode="EOD")
    assert result.score == pytest.approx((80 * 20 + 70 * 15) / 35, abs=0.01)
    assert result.label == QualityLabel.GOOD
    assert "COMPONENT_OMITTED_NORMALIZED:breakout_readiness" in result.reason_codes


def test_breakout_readiness_is_not_applicable_in_breakout_stage():
    result = score_entry_quality({"pattern": 80, "breakout_readiness": 0, "relative_strength": 80}, mode="EOD", stage=OfficialStage.BREAKOUT)
    expected = (80 * 20 + 80 * 15) / 35
    assert result.score == pytest.approx(expected)
    assert "breakout_readiness" not in result.component_scores


def test_missing_required_core_setup_is_data_incomplete():
    result = score_entry_quality({"relative_strength": 90}, mode="EOD")
    assert result.label == QualityLabel.DATA_INCOMPLETE
    assert result.score is None


def test_score_thresholds():
    assert score_entry_quality({"pattern": 75}, mode="EOD").label == QualityLabel.GOOD
    assert score_entry_quality({"pattern": 60}, mode="EOD").label == QualityLabel.CAUTION
    assert score_entry_quality({"pattern": 59.99}, mode="EOD").label == QualityLabel.WAIT


def test_component_values_outside_scale_fail():
    with pytest.raises(ValueError):
        score_entry_quality({"pattern": 101}, mode="EOD")


def test_extension_gate_gt_five_is_caution_not_a_score_rewrite():
    label, action, reasons = apply_price_extension_gate(QualityLabel.GOOD, candidate_distance_pct=5.01)
    assert label == QualityLabel.CAUTION
    assert action == QualityAction.CAUTION
    assert reasons == ("PRICE_EXTENSION_GT_5_PCT",)


def test_extension_gate_at_seven_blocks_new_entry_and_add():
    label, action, _ = apply_price_extension_gate(QualityLabel.GOOD, candidate_distance_pct=7.0)
    assert label == QualityLabel.WAIT
    assert action == QualityAction.BLOCK_NEW_ENTRY_OR_ADD


def test_extension_gate_at_exactly_five_does_not_fire():
    label, action, reasons = apply_price_extension_gate(QualityLabel.GOOD, candidate_distance_pct=5.0)
    assert label == QualityLabel.GOOD
    assert action == QualityAction.NORMAL
    assert reasons == ()
