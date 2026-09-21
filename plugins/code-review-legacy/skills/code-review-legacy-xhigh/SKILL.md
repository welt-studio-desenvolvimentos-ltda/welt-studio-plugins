---
name: code-review-legacy-xhigh
description: Revisão de código no nível xhigh com a receita legada (Sonnet 5) do /code-review — 10 ângulos em subagentes, verificação e varredura de lacunas, até 15 achados. Use quando o usuário pedir /code-review-legacy-xhigh. Aceita --fix, --comment e alvo (<pr#>|<branch>|<path>).
argument-hint: "[--fix] [--comment] [<pr#>|<branch>|<path>]"
context: fork
effort: xhigh
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/build_prompt.py:*)
---

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/build_prompt.py xhigh '$ARGUMENTS'`
