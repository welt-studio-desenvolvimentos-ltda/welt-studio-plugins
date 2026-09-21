---
name: code-review-legacy-high
description: Revisão de código no nível high com a receita legada (Sonnet 5) do /code-review — 8 ângulos em subagentes, verificação de 1 voto com viés de recall, até 10 achados. Use quando o usuário pedir /code-review-legacy-high. Aceita --fix, --comment e alvo (<pr#>|<branch>|<path>).
argument-hint: "[--fix] [--comment] [<pr#>|<branch>|<path>]"
context: fork
effort: high
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/build_prompt.py:*)
---

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/build_prompt.py high '$ARGUMENTS'`
