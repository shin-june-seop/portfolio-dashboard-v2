# Phase 3.2 report

- Added OHLCV file integrity audit with capture metadata cross-checks.
- Added manual review worksheet generation for provisional RB-01 (close <= 90% of rolling 20-bar highest High) episodes, showing ungated vs rebase-gated pattern outputs on selected post-trigger bars.
- Added workflow documentation separating machine observations from manually verified historical expected values.
- Automated tests: 83 passed in local validation with Python 3.13.5.

Not completed by this package: real chart regression ground-truth labels, exact historical breakout-pivot state reconstruction, LRCX entry-time risk evidence, formal approval of Yahoo/yfinance adjustment policy, Supabase live schema inventory, and full screen migration.
