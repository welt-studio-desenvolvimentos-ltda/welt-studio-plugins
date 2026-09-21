---
name: code-review-legacy-max
description: Revisão de código no nível max com a receita legada (Sonnet 5) do /code-review — 10 ângulos em subagentes, verificação e varredura, até 15 achados — a mais cara. Use quando o usuário pedir /code-review-legacy-max. Aceita --fix, --comment e alvo (<pr#>|<branch>|<path>).
argument-hint: "[--fix] [--comment] [<pr#>|<branch>|<path>]"
context: fork
effort: max
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/build_prompt.py:*)
---

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/build_prompt.py max '$ARGUMENTS'`
