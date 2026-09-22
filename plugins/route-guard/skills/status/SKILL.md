---
name: status
description: Mostra a rota da sessão: passos, escopo, critérios, tentativas e estado.
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/route_guard_cli.py:*)
---

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/route_guard_cli.py status '${CLAUDE_PLUGIN_DATA}' '${CLAUDE_SESSION_ID}'`
