#!/usr/bin/env python3
"""spec-gate: registra eventos de ciclo de vida em .specgate/events.jsonl.

Alimenta o Work Log das visualizacoes. Inerte sem .specgate.json.
Sempre exit 0: logging jamais bloqueia nada.
"""
import json
import os
import sys
import time

try:
    # O import roda antes de sabermos se o projeto usa spec-gate. Uma
    # instalação corrompida ou checkout parcial não pode derrubar o hook em
    # QUALQUER evento, inclusive projetos que nem tem .specgate.json — por
    # isso o import é à prova de falha e o módulo vira None quando ausente.
    import specgate_state
except ImportError:
    specgate_state = None

MAX_LINES = 400


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    cwd = payload.get("cwd") or os.getcwd()
    if not os.path.isfile(os.path.join(cwd, ".specgate.json")):
        return

    ev = payload.get("hook_event_name", "event")
    entry = {
        "ts": time.strftime("%H:%M:%S"),
        "event": ev,
    }
    # O seq só avança em turno real do usuário: é a prova inforjável que o
    # gate de PO consome. Nenhum outro evento pode movê-lo.
    # Sem o módulo (import falhou), seguimos logando o resto sem o campo seq
    # em vez de explodir — fail-open também aqui.
    if specgate_state is not None:
        if ev == "UserPromptSubmit":
            entry["seq"] = specgate_state.bump_seq(cwd)
        else:
            entry["seq"] = specgate_state.read_seq(cwd)
    tool = payload.get("tool_name")
    if tool:
        entry["tool"] = tool
        ti = payload.get("tool_input") or {}
        if tool == "Bash" and isinstance(ti.get("command"), str):
            entry["detail"] = ti["command"][:120]
        elif tool == "Task":
            entry["detail"] = (ti.get("subagent_type") or ti.get("description") or "")[:120]
        elif isinstance(ti.get("file_path"), str):
            entry["detail"] = ti["file_path"][:120]
    for key in ("agent_name", "agent_type", "subagent_type"):
        if payload.get(key):
            entry["agent"] = str(payload[key])[:60]
            break
    if ev == "UserPromptSubmit" and isinstance(payload.get("prompt"), str):
        entry["detail"] = payload["prompt"][:120]

    d = os.path.join(cwd, ".specgate")
    path = os.path.join(d, "events.jsonl")
    try:
        os.makedirs(d, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        # cap: mantem so o final do arquivo
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        if len(lines) > MAX_LINES:
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(lines[-MAX_LINES:])
    except OSError:
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
