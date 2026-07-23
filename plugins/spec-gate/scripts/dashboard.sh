#!/usr/bin/env bash
# spec-gate: sobe o dashboard do lote em http://localhost:PORT/dashboard.html
# Uso: ./dashboard.sh [/caminho/do/projeto] [porta]
set -euo pipefail

PROJ="${1:-.}"
PORT="${2:-8437}"
cd "$PROJ"

[ -f .specgate.json ] || { echo "aviso: .specgate.json não encontrado, o dashboard mostrará estado vazio"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp -f "$SCRIPT_DIR/dashboard.html" ./.specgate-dashboard.html 2>/dev/null || true

URL="http://localhost:${PORT}/.specgate-dashboard.html"
echo "[spec-gate] dashboard em ${URL} (Ctrl+C encerra)"

# Abre o navegador: WSL2 usa o Windows, senão xdg-open/open.
( sleep 1
  if command -v wslview >/dev/null 2>&1; then wslview "$URL"
  elif command -v explorer.exe >/dev/null 2>&1; then explorer.exe "$URL" || true
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
  elif command -v open >/dev/null 2>&1; then open "$URL"
  fi ) &

exec python3 -m http.server "$PORT" --bind 127.0.0.1
