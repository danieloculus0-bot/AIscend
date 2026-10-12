# BEAN research audit, 2026-10-12

PR #24 is closed and superseded by merged PRs #32 and #34.

Coinbase Actions 38153367867 and 38156393618 passed, but single-run benchmark wins are not independent untouched validation. No profitability notification.

Failure: duplicate/overlapping reports could pass the historical gate. This branch adds fail-closed chronology, cost and benchmark requirements, a provenance helper and regression tests.

Remaining: wire the helper into the batch producer and run new disjoint validation. No holdout tuning, merge, wallet access or trading occurred.
