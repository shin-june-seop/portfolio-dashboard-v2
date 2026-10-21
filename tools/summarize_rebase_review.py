"""Roll sampled RB-01 rebase snapshots into a cycle-level manual-review worksheet."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from dashboard_v2.services.historical_review import summarize_rebase_review

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/regression/rebase_review_candidates.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "data/regression/rebase_review_summary.csv")
    args = parser.parse_args()
    if not args.input.exists():
        print(f"Missing review worksheet: {args.input}")
        return 1
    rows = summarize_rebase_review(pd.read_csv(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    columns = ["ticker", "cycle_id", "rebase_started_at_rb01_proxy", "trigger_observation_date", "trigger_close_vs_20d_high_pct",
               "ungated_pattern_at_trigger", "ungated_pattern_start_at_trigger", "ungated_pattern_predates_trigger",
               "gated_pattern_at_trigger", "snapshot_count", "first_sampled_valid_post_rebase_pattern_offset",
               "first_sampled_valid_post_rebase_pattern_date", "first_sampled_valid_post_rebase_pattern",
               "first_sampled_candidate_pivot", "first_sampled_pattern_start", "review_status",
               "expected_pattern_manual", "expected_candidate_pivot_manual", "evidence_reference", "review_note"]
    pd.DataFrame(rows, columns=columns).to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"Rebase review summary: {len(rows)} cycles")
    print(f"Cycles with pre-trigger pattern at trigger: {sum(bool(r['ungated_pattern_predates_trigger']) for r in rows)}")
    print(f"Cycles with sampled post-rebase candidate: {sum(bool(r['first_sampled_valid_post_rebase_pattern']) for r in rows)}")
    print(f"Output: {args.output}")
    print("This is a triage worksheet, not a ground-truth labeler. Manual evidence is required.")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
