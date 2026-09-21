---
name: code-review-legacy
description: Revisão de código com a receita legada (Sonnet 5) do /code-review — finders e verifiers em subagentes em todos os níveis. Use quando o usuário pedir /code-review-legacy. O nível (low|medium|high|xhigh|max) escolhe receita e esforço; sem nível, reusa o último digitado. Aceita --fix, --comment e alvo (<pr#>|<branch>|<path>).
argument-hint: "[low|medium|high|xhigh|max] [--fix] [--comment] [<pr#>|<branch>|<path>]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/route.py:*)
---

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/route.py '${CLAUDE_PLUGIN_DATA}' '${CLAUDE_EFFORT}' '$ARGUMENTS'`
