#!/usr/bin/env bash
# spec-gate: disparo headless do backlog.
# Uso: ./run-backlog.sh /caminho/do/projeto
# Roda o lote sem sessão interativa e deixa o resultado em docs/backlog/REPORT.md
set -euo pipefail

PROJ="${1:?uso: run-backlog.sh /caminho/do/projeto}"
cd "$PROJ"

[ -f .specgate.json ] || { echo "erro: .specgate.json não encontrado em $PROJ"; exit 1; }
ls docs/backlog/*.md >/dev/null 2>&1 || { echo "erro: nenhum arquivo de spec em docs/backlog/"; exit 1; }

# Sanidade: árvore limpa antes de um lote não supervisionado.
if [ -n "$(git status --porcelain)" ]; then
  echo "erro: árvore git suja. Commite ou guarde suas mudanças antes de rodar o lote sem supervisão."
  exit 1
fi

LOG="docs/backlog/run-$(date +%Y%m%d-%H%M%S).log"
echo "[spec-gate] iniciando lote headless em $PROJ (log: $LOG)"

# --permission-mode acceptEdits: aprova edições de arquivo automaticamente.
# Comandos Bash ainda respeitam as permissões do projeto; libere os necessários
# (suite de testes, git) nas allow rules do .claude/settings.json do projeto,
# em vez de usar --dangerously-skip-permissions. Os gates do spec-gate seguem
# ativos de qualquer forma: hook roda igual em modo headless.
claude -p "/spec-gate:backlog" \
  --permission-mode acceptEdits \
  --max-turns 300 \
  > "$LOG" 2>&1 || true

if [ -f docs/backlog/REPORT.md ]; then
  echo "[spec-gate] lote concluído. Relatório:"
  echo "----------------------------------------"
  cat docs/backlog/REPORT.md
else
  echo "[spec-gate] lote terminou sem gerar REPORT.md, veja o log: $LOG"
  tail -40 "$LOG"
fi
