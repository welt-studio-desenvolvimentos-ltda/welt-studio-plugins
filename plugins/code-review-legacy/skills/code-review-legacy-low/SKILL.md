---
name: code-review-legacy-low
description: Revisão de código no nível low com a receita legada (Sonnet 5) do /code-review — 1 passada no diff, sem verificação, até 4 achados — a mais barata. Use quando o usuário pedir /code-review-legacy-low. Aceita --fix, --comment e alvo (<pr#>|<branch>|<path>).
argument-hint: "[--fix] [--comment] [<pr#>|<branch>|<path>]"
context: fork
effort: low
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/build_prompt.py:*)
---

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/build_prompt.py low '$ARGUMENTS'`
