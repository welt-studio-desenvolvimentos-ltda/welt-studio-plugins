#!/usr/bin/env bash
# spec-gate: desenha o quadro do lote no terminal (cores ANSI + box drawing).
# Uso: board.sh [/caminho/do/projeto]
set -euo pipefail
CWD="${1:-.}"
B="$CWD/.specgate/batch.json"
G="$CWD/.specgate/gate.json"
python3 - "$B" "$G" <<'PY'
import json, sys, re

def carrega(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default

d = carrega(sys.argv[1], {})
items = d.get("items", []) if isinstance(d, dict) else []

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

# Gate VIGENTE de cada (checkpoint, pbi): só o de MAIOR rodada. Uma rodada
# anterior já decidida (reprovada, por exemplo) nunca pode aparecer aqui
# como se ainda estivesse pendente.
melhor = {}
for g in gates_raw:
    if not isinstance(g, dict):
        continue
    chave = (g.get("checkpoint"), g.get("pbi"))
    if chave not in melhor or rodada(g) > rodada(melhor[chave]):
        melhor[chave] = g
gates_abertos = [g for g in melhor.values() if g.get("status") == "aguardando-po"]

R="\033[0m"; DIM="\033[2m"; BOLD="\033[1m"
C = {"delivered":"\033[32m","running":"\033[34m","skipped":"\033[33m",
     "failed":"\033[31m","pending":"\033[90m"}
ICO = {"delivered":"✔","running":"▶","skipped":"⏭","failed":"✖","pending":"·"}

def title(i):
    t = i.get("title")
    if t: return t
    b = (i.get("spec","") or "").split("/")[-1]
    return re.sub(r"^\d+-|\.md$","",b).replace("-"," ")

W = 62
n = lambda s: sum(1 for i in items if i.get("status","pending")==s)
done, total = n("delivered"), len(items)
filled = int(W*0.6*done/total) if total else 0
bar = "█"*filled + "░"*(int(W*0.6)-filled)

print(f"┌{'─'*W}┐")
if total:
    head = f" SPEC-GATE  {done}/{total} entregues · {n('skipped')} pulados · {n('failed')} falhas"
else:
    head = " SPEC-GATE  sem lote ativo (rode /spec-gate)"
print(f"│{BOLD}{head[:W]:<{W}}{R}│")
print(f"│ \033[34m{bar}{R}{' '*(W-len(bar)-1)}│")

if total:
    print(f"├{'─'*W}┤")
    for i in items:
        st = i.get("status","pending"); c = C.get(st, C["pending"])
        att = i.get("attempts",0); com = (i.get("commit") or "")[:7]
        meta = f"t{att}" + (f" {com}" if com else "")
        line = f" {ICO.get(st,'·')} {title(i)[:38]:<38} {st:<9} {meta}"
        print(f"│{c}{line[:W]:<{W}}{R}│")

qs = [(i.get('spec','').split('/')[-1], q) for i in items for q in i.get("questions",[])]
if qs:
    print(f"├{'─'*W}┤")
    print(f"│{BOLD}{' PERGUNTAS AGUARDANDO O PO':<{W}}{R}│")
    for spec, q in qs:
        txt = f" ? {spec[:18]} · {q}"
        print(f"│\033[33m{txt[:W]:<{W}}{R}│")

if gates_abertos:
    print(f"├{'─'*W}┤")
    print(f"│{BOLD}{' GATES AGUARDANDO O PO':<{W}}{R}│")
    for g in gates_abertos:
        checkpoint = g.get("checkpoint", "?")
        pbi = g.get("pbi")
        label = f"{checkpoint}/{pbi}" if pbi else str(checkpoint)
        head_g = f" ● {label} · rodada {rodada(g)}"
        print(f"│\033[33m{head_g[:W]:<{W}}{R}│")
        for q in g.get("questions", []) or []:
            txt = f"   ? {q}"
            print(f"│\033[33m{txt[:W]:<{W}}{R}│")

print(f"└{'─'*W}┘")
PY
