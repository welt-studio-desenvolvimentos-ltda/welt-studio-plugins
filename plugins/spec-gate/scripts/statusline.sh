#!/usr/bin/env bash
# spec-gate: statusline pro Claude Code. Configure em .claude/settings.json:
# "statusLine": {"type":"command","command":"bash /caminho/spec-gate/scripts/statusline.sh"}
# O Claude Code passa contexto em JSON no stdin; usamos o cwd de la.
set -euo pipefail
CWD=$(python3 -c "import json,sys;print(json.load(sys.stdin).get('cwd') or '.')" 2>/dev/null || pwd)
B="$CWD/.specgate/batch.json"
G="$CWD/.specgate/gate.json"
A="$CWD/.specgate/attempts.json"
CFG="$CWD/.specgate.json"
python3 - "$B" "$G" "$A" "$CFG" <<'PY'
import json, sys

def carrega(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default

d = carrega(sys.argv[1], None)

gates_raw = carrega(sys.argv[2], [])
if isinstance(gates_raw, dict):
    gates_raw = [gates_raw]
if not isinstance(gates_raw, list):
    gates_raw = []

def rodada(g):
    # Rodada (Task 9): ausente ou malformada vira 1 — mesmo default de
    # compatibilidade de specgate_state.py/_rodada. Nunca lança.
    v = g.get("rodada", 1)
    if isinstance(v, bool):
        return 1
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 1
    return n if n >= 1 else 1

# Gate VIGENTE de cada (checkpoint, pbi): só o de MAIOR rodada conta como
# pendente — uma rodada anterior já decidida não é gate aberto.
melhor = {}
for g in gates_raw:
    if not isinstance(g, dict):
        continue
    chave = (g.get("checkpoint"), g.get("pbi"))
    if chave not in melhor or rodada(g) > rodada(melhor[chave]):
        melhor[chave] = g
abertos = [g for g in melhor.values() if g.get("status") == "aguardando-po"]

if not isinstance(d, dict) or not d.get("items"):
    base = "spec-gate: sem lote ativo"
else:
    it = d["items"]
    c = lambda s: sum(1 for i in it if i.get("status", "pending") == s)
    base = f"spec-gate {c('delivered')}/{len(it)} ok · {c('skipped')} pulados · {c('failed')} falhas"

if abertos:
    destaque = max(abertos, key=rodada)
    checkpoint = destaque.get("checkpoint", "?")
    pbi = destaque.get("pbi")
    label = f"{checkpoint}/{pbi}" if pbi else str(checkpoint)
    extra = f" · {len(abertos)} gate(s) aberto(s) ({label} rodada {rodada(destaque)})"
else:
    extra = ""

# Tentativas do PBI em curso: contadas pelo hook a cada execução da suíte
# durante a implementação, nunca declaradas pelo agente. Aparecem aqui para
# que o teto chegando seja visível ANTES do bloqueio, não depois dele.
#
# O contador é por (PBI, rodada): quando o PO abre rodada nova de qualquer
# gate do item, o guard passa a contar do zero. Por isso a rodada gravada é
# conferida contra a maior rodada do PBI — sem isso a statusline anunciaria
# um teto que já não está perto de estourar.
tentativas = carrega(sys.argv[3], {})
cfg = carrega(sys.argv[4], {})
if isinstance(tentativas, dict) and tentativas.get("pbi"):
    try:
        maior_rodada = max(
            [rodada(g) for g in gates_raw
             if isinstance(g, dict) and g.get("pbi") == tentativas["pbi"]] or [0]
        )
        gastas = int(tentativas.get("count", 0)) if int(tentativas.get("round", 0)) == maior_rodada else 0
        teto = int(cfg.get("max_fix_attempts", 5)) if isinstance(cfg, dict) else 5
    except (TypeError, ValueError):
        gastas = teto = 0
    if gastas > 0 and teto > 0:
        extra += f" · tentativa {gastas}/{teto}"

print(base + extra)
PY
