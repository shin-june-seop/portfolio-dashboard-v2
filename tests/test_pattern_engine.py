from datetime import date
from dashboard_v2.engines.pattern_engine import select_primary_pattern
from dashboard_v2.models.schemas import PatternCandidate

D = date(2026, 10, 19)


def candidate(name, pivot, valid=True):
    return PatternCandidate(pattern_name=name, valid=valid, candidate_pivot=pivot, pattern_start_date=date(2026, 8, 1))


def test_vcp_is_primary_when_vcp_and_classic_pattern_are_valid():
    selected = select_primary_pattern([candidate("Double Bottom", 100), candidate("VCP", 105), candidate("Flat Base", 101)])
    assert selected is not None
    assert selected.pattern_name == "VCP"
    assert selected.candidate_pivot == 105


def test_invalid_vcp_does_not_hide_valid_classic_pattern():
    selected = select_primary_pattern([candidate("VCP", 105, valid=False), candidate("Cup & Handle", 101)])
    assert selected is not None
    assert selected.pattern_name == "Cup & Handle"


def test_invalid_pivot_or_missing_start_date_is_not_selected():
    bad = PatternCandidate("VCP", True, None, D)
    missing_start = PatternCandidate("Double Bottom", True, 100, None)
    assert select_primary_pattern([bad, missing_start]) is None


def test_classic_pattern_priority_matches_spec():
    selected = select_primary_pattern([candidate("Saucer Base", 95), candidate("High Tight Flag", 120), candidate("Double Bottom", 100)])
    assert selected is not None
    assert selected.pattern_name == "High Tight Flag"
