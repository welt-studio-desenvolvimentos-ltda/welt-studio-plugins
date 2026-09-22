Spawn exactly one `Agent` call (subagent_type `general-purpose`) per angle below — {{count}} calls in total, launched together in a single message. Do not merge angles into fewer agents, whatever the diff size.

Set `run_in_background: false` on every `Agent` call in this review, finders and verifiers alike. Calls in the same message still run in parallel, and you need every result in this turn: if you end your turn to wait for background agents, the review ends there and nobody verifies or applies the findings.
