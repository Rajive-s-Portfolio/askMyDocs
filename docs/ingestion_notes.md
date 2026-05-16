# Ingestion Phase — Run Notes

## Run Date
2026-05-16

## Source
- URL: https://docs.stripe.com/llms.txt
- Format: llms.txt convention (markdown index)

## Hard Numbers
| Metric | Value |
|---|---|
| Total entries in index | 457 |
| Successfully downloaded | 449 |
| Failed (404) | 8 |
| Success rate | 98.2% |
| Sections covered | <fill in> |
| Total download time | ~<fill in> seconds |
| Concurrency setting | 5 (semaphore-bounded) |

## Failure Analysis
All 8 failures returned HTTP 404. Manual verification confirmed these
are stale entries in Stripe's `llms.txt` — the linked `.md` files no
longer exist at the source. This is an upstream data quality issue,
not a pipeline defect.

**Decision:** Failures are logged to `data/processed/failed_downloads.json`
for visibility and excluded from the downstream corpus. No retry logic
is warranted (404 is permanent). The 1.8% gap is documented rather than
masked.

## Failed URLs
```{
  "count": 8,
  "failures": [
    {
      "url": "https://docs.stripe.com/issuing/compliance-us/disclosure.md",
      "title": "Issuing Disclosure Component",
      "section": "Issuing",
      "error": "HTTP 404",
      "attempted_at": "2026-05-16T16:26:09.926896Z",
      "attempt_number": 1
    },
    {
      "url": "https://docs.stripe.com/revenue-recognition/reports/period-summary.md",
      "title": "Period summary",
      "section": "Revenue Recognition",
      "error": "HTTP 404",
      "attempted_at": "2026-05-16T16:25:59.603959Z",
      "attempt_number": 1
    },
    {
      "url": "https://docs.stripe.com/treasury/connect/compliance/disclosure.md",
      "title": "Treasury Disclosure Component",
      "section": "Treasury",
      "error": "HTTP 404",
      "attempted_at": "2026-05-16T16:24:26.988293Z",
      "attempt_number": 1
    },
    {
      "url": "https://docs.stripe.com/treasury/connect/fifth-third-migration.md",
      "title": "Customize your migration to Fifth Third Bank",
      "section": "Treasury",
      "error": "HTTP 404",
      "attempted_at": "2026-05-16T16:24:36.643809Z",
      "attempt_number": 1
    },
    {
      "url": "https://docs.stripe.com/reports/administrative-facilitation-fee-report.md",
      "title": "Administrative facilitation fee report",
      "section": "Treasury",
      "error": "HTTP 404",
      "attempted_at": "2026-05-16T16:24:38.033836Z",
      "attempt_number": 1
    },
    {
      "url": "https://docs.stripe.com/treasury/connect/money-movement/remote-check-acceptance.md",
      "title": "Remote check acceptance",
      "section": "Treasury",
      "error": "HTTP 404",
      "attempted_at": "2026-05-16T16:24:53.016072Z",
      "attempt_number": 1
    },
    {
      "url": "https://docs.stripe.com/treasury/connect/money-movement/remote-check-acceptance-testing.md",
      "title": "Test a remote check acceptance integration",
      "section": "Treasury",
      "error": "HTTP 404",
      "attempted_at": "2026-05-16T16:24:55.508306Z",
      "attempt_number": 1
    },
    {
      "url": "https://docs.stripe.com/treasury/connect/money-movement/remote-check-acceptance-review-guide.md",
      "title": "Remote check acceptance review guidelines",
      "section": "Treasury",
      "error": "HTTP 404",
      "attempted_at": "2026-05-16T16:24:56.698497Z",
      "attempt_number": 1
    }
  ]
}
```

## Observations
- Download took 90 Seconds
- 8 Failed due to upstream data problems (Validated manually)
- No Issues in the pipeline