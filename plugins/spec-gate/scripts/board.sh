#!/usr/bin/env bash
# spec-gate: desenha o quadro do lote no terminal (cores ANSI + box drawing).
# Uso: board.sh [/caminho/do/projeto]
set -euo pipefail
CWD="${1:-.}"
B="$CWD/.specgate/batch.json"
if [ ! -f "$B" ]; then echo "spec-gate: sem lote ativo (rode /spec-gate)"; exit 0; fi
python3 - "$B" <<'PY'
import json, sys, re

d = json.load(open(sys.argv[1]))
items = d.get("items", [])
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
head = f" SPEC-GATE  {done}/{total} entregues · {n('skipped')} pulados · {n('failed')} falhas"
print(f"│{BOLD}{head[:W]:<{W}}{R}│")
print(f"│ \033[34m{bar}{R}{' '*(W-len(bar)-1)}│")
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
print(f"└{'─'*W}┘")
PY
