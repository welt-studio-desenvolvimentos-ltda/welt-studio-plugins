

## Posting to GitLab (--comment)

The `--comment` flag was passed. After producing the findings list, if the
review target is a GitLab merge request, post the findings as one general MR
note via `{{glab_command}}`{{checkout_clause}}
(every finding with its file:line, the issue, and the suggested fix). glab has no single verb for line-anchored
comments; those require `glab api projects/:id/merge_requests/:iid/discussions`,
so post the general note unless the user asks for inline threads. If glab is
not available in this session, print the findings instead. If the target is
not an MR, print the findings to the terminal and note that `--comment` was
ignored.
