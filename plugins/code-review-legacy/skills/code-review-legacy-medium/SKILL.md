---
name: code-review-legacy-medium
description: Revisão de código no nível medium com a receita legada (Sonnet 5) do /code-review — 8 ângulos em subagentes, verificação de 1 voto com viés de precisão, até 8 achados. Use quando o usuário pedir /code-review-legacy-medium. Aceita --fix, --comment e alvo (<pr#>|<branch>|<path>).
argument-hint: "[--fix] [--comment] [<pr#>|<branch>|<path>]"
context: fork
effort: medium
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/build_prompt.py:*)
---

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/build_prompt.py medium '$ARGUMENTS'`
