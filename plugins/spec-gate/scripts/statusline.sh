#!/usr/bin/env bash
# spec-gate: statusline pro Claude Code. Configure em .claude/settings.json:
# "statusLine": {"type":"command","command":"bash /caminho/spec-gate/scripts/statusline.sh"}
# O Claude Code passa contexto em JSON no stdin; usamos o cwd de la.
set -euo pipefail
CWD=$(python3 -c "import json,sys;print(json.load(sys.stdin).get('cwd') or '.')" 2>/dev/null || pwd)
B="$CWD/.specgate/batch.json"
if [ -f "$B" ]; then
  python3 - "$B" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
it=d.get("items",[])
c=lambda s: sum(1 for i in it if i.get("status","pending")==s)
print(f"spec-gate {c('delivered')}/{len(it)} ok · {c('skipped')} pulados · {c('failed')} falhas")
PY
else
  echo "spec-gate: sem lote ativo"
fi
