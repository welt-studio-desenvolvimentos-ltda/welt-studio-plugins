---
name: approve
description: Aprova a rota que o Claude escreveu a partir do plano (ou retoma uma rota escalada). Só o usuário roda.
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/route_guard_cli.py:*)
---

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/route_guard_cli.py approve '${CLAUDE_PLUGIN_DATA}' '${CLAUDE_SESSION_ID}'`
