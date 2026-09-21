## Output

Return findings as a JSON array of at most {{cap}} objects:

```json
[
  {
    "file": "path/to/file.ext",
    "line": 123,
    "summary": "one-sentence statement of the bug",
    "failure_scenario": "concrete inputs/state → wrong output/crash"
  }
]
```

Ranked most-severe first. If more than {{cap}} survive, keep the {{cap}} most
severe. If nothing survives verification, return `[]`. Do not call the
ReportFindings tool even if it is available - this review's
output contract is the JSON block above.
