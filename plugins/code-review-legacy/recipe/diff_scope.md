## Diff scope

The review judges what this diff changed. A line is **in the diff** when the
unified diff adds or modifies it (a `+` line). Apply this boundary everywhere:

- **Correctness angles** keep their rule: a bug in an unchanged line of a
  function the diff touches is in scope, and so is a call site or callee the
  diff breaks, wherever it lives (the cross-file angle exists for these).
- **Reuse, simplification, efficiency, altitude, and conventions** candidates
  must cite a line in the diff. Duplication, waste, or a design question that
  lives entirely in code the diff did not touch is out of scope, even when a
  finder notices it while reading context. For reuse, cite the new line that
  re-implements the helper, not the existing helper. Dead code the diff leaves
  behind (its last use was a `-` line) is in scope: cite the dead code.

When you run each verifier, pass it this boundary. Besides its vote, the
verifier labels the candidate `diff` or `pre_existing` by checking the cited
line against the diff: a correctness candidate is `diff` when it sits in a
function the diff touches or the diff's change is what breaks it; any other
candidate is `diff` only when the cited line is a `+` line or is dead code a
`-` line left behind. Uncertainty does not keep a candidate in scope — the
diff settles it. Candidates that reach the output without a verifier (a
sweep's) get the same label from you, by the same check, before they are
listed.

`pre_existing` candidates that survive the vote are not findings: leave them
out of the JSON output, do not count them against the cap, and do not fix
them. Record them instead, after the review and before any fixes:

- If the `tech-debt-tracker`, `parking-lot`, or `roadmap-tracker` skills are
  available, invoke them and file each item where it belongs: a problem in
  existing code → `tech-debt-tracker`; a deferred decision or open question →
  `parking-lot`; a feature to build → `roadmap-tracker`. Follow the skill's
  format. Before filing, check the target file for an entry on the same file
  and mechanism, and skip the item if one exists.
- If those skills are not available, list the items after the JSON block under
  `Pre-existing (not reviewed):`, one line each:
  `path/to/file.ext:123 — the issue`.

After recording, and before any fixes, add one line saying how many
pre-existing items were recorded, where, and how many were skipped as already
recorded.
