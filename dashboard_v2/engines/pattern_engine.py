"""Pattern candidate contract and single-primary-pattern selection.

Chart detectors are deliberately added one by one against historical fixtures;
this module centralizes their shared output contract and overlap priority now,
so screens never independently choose patterns or candidate pivots.
"""
from __future__ import annotations
from math import isfinite
from ..models.schemas import PatternCandidate

# Priority order adopted in Functional Spec v0.12. VCP is a setup type distinct
# from classic bases, but it wins the single primary label/pivot when valid.
PATTERN_PRIORITY = {
    "VCP": 0,
    "HIGH TIGHT FLAG": 1,
    "BASE-ON-BASE": 2,
    "CUP & HANDLE": 3,
    "DOUBLE BOTTOM": 4,
    "FLAT BASE": 5,
    "CUP WITHOUT HANDLE": 6,
    "SAUCER BASE": 7,
    "ASCENDING BASE": 8,
}


def _valid_candidate(candidate: PatternCandidate) -> bool:
    pivot = candidate.candidate_pivot
    if not candidate.valid or pivot is None:
        return False
    try:
        if not isfinite(float(pivot)) or float(pivot) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    if candidate.pattern_start_date is None:
        return False
    return True


def select_primary_pattern(candidates: list[PatternCandidate] | tuple[PatternCandidate, ...]) -> PatternCandidate | None:
    """Return exactly one valid candidate, preserving configured stable priority.

    A missing/invalid VCP cannot mask a valid classic pattern. This function
    does not scan OHLCV or certify a pattern; each detector must satisfy its
    pattern-specific timeframe and structural conditions before `valid=True`.
    """
    valid = [candidate for candidate in candidates if _valid_candidate(candidate)]
    if not valid:
        return None
    # Python sorting is stable, which preserves detector ordering for tied names.
    return sorted(valid, key=lambda item: PATTERN_PRIORITY.get(item.pattern_name.upper().strip(), 100))[0]
