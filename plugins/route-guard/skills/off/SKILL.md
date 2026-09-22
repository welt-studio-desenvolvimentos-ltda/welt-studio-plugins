---
name: off
description: Abandona a rota da sessão; edições deixam de ser conferidas.
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/route_guard_cli.py:*)
---

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/route_guard_cli.py off '${CLAUDE_PLUGIN_DATA}' '${CLAUDE_SESSION_ID}'`
