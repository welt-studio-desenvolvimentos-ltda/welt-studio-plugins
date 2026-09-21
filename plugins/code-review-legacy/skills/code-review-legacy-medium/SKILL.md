---
name: code-review-legacy-medium
description: Revisão de código no nível medium com a receita legada (Sonnet 5) do /code-review — 8 ângulos em subagentes, verificação de 1 voto com viés de precisão, até 8 achados. Chamada pelo /code-review-legacy, que escolhe o nível; não invocar direto.
argument-hint: "[--fix] [--comment] [<pr#>|<branch>|<path>]"
context: fork
user-invocable: false
effort: medium
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/build_prompt.py:*)
---

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/build_prompt.py medium '$ARGUMENTS'`
