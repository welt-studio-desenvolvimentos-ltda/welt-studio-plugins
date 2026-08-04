#!/usr/bin/env bash
# spec-gate: desenha o quadro do lote no terminal (cores ANSI + box drawing).
# Uso: board.sh [/caminho/do/projeto]
set -euo pipefail
CWD="${1:-.}"
B="$CWD/.specgate/batch.json"
G="$CWD/.specgate/gate.json"
A="$CWD/.specgate/attempts.json"
RD="$CWD/.specgate/red.json"
P="$CWD/.specgate/phase"
CFG="$CWD/.specgate.json"
python3 - "$B" "$G" "$A" "$RD" "$P" "$CFG" <<'PY'
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

# Fase corrente, tentativas e prova de RED. Estes três vêm de arquivos
# escritos pelo HOOK, não pelo agente — é por isso que o board pode mostrá-los
# como fato e não como relato: o contador de tentativas em batch.json é
# autodeclarado pelo orquestrador, o de attempts.json é contado pelo guard.
tentativas = carrega(sys.argv[3], {})
if not isinstance(tentativas, dict):
    tentativas = {}
red = carrega(sys.argv[4], {})
if not isinstance(red, dict):
    red = {}
cfg = carrega(sys.argv[6], {})
if not isinstance(cfg, dict):
    cfg = {}
try:
    teto = int(cfg.get("max_fix_attempts", 5))
except (TypeError, ValueError):
    teto = 5

try:
    with open(sys.argv[5], encoding="utf-8") as fh:
        fase_bruta = fh.read().strip()
except OSError:
    fase_bruta = ""
fase, _, fase_pbi = fase_bruta.partition(":")
fase, fase_pbi = fase.strip(), fase_pbi.strip()


def rodada_max_do_pbi(spec):
    """Maior rodada entre TODAS as séries de gate deste PBI — o mesmo número
    que o guard usa para carimbar o contador (specgate_state.max_round_for_pbi).
    """
    maior = 0
    for g in gates_raw:
        if isinstance(g, dict) and g.get("pbi") == spec:
            maior = max(maior, rodada(g))
    return maior


def tentativas_de(spec):
    """Tentativas gastas neste PBI, ou None se o contador é de outro PBI.

    O contador é por (PBI, rodada): quando o PO abre rodada nova de qualquer
    gate do item, o guard passa a contar do zero. Sem conferir a rodada aqui,
    o board seguiria mostrando o número velho — anunciando um teto que não
    está mais perto de estourar.
    """
    if not spec or tentativas.get("pbi") != spec:
        return None
    try:
        if int(tentativas.get("round", 0)) != rodada_max_do_pbi(spec):
            return 0  # rodada anterior: para o guard o contador já está zerado
        return int(tentativas.get("count", 0))
    except (TypeError, ValueError):
        return None


def selo_red(spec):
    e = red.get(spec)
    if not isinstance(e, dict) or not e.get("proven"):
        return ""
    return "RED dispensado pelo PO" if e.get("waived") else "RED provado"


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

if fase:
    partes = [f"fase: {fase}"]
    if fase_pbi:
        partes.append(fase_pbi.split("/")[-1])
        selo = selo_red(fase_pbi)
        if selo:
            partes.append(selo)
        gastas = tentativas_de(fase_pbi)
        if gastas is not None:
            partes.append(f"t{gastas}/{teto}")
    linha_fase = " " + " · ".join(partes)
    print(f"│{DIM}{linha_fase[:W]:<{W}}{R}│")

if total:
    print(f"├{'─'*W}┤")
    for i in items:
        st = i.get("status","pending"); c = C.get(st, C["pending"])
        com = (i.get("commit") or "")[:7]
        # O contador do hook tem precedência sobre o campo `attempts` de
        # batch.json: um é medido, o outro é declarado por quem foi medido.
        gastas = tentativas_de(i.get("spec"))
        att = f"t{gastas}/{teto}" if gastas is not None else f"t{i.get('attempts',0)}"
        meta = att + (f" {com}" if com else "")
        line = f" {ICO.get(st,'·')} {title(i)[:34]:<34} {st:<9} {meta}"
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
