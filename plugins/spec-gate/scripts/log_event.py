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
    # Captura qualquer Exception (não só ImportError): um specgate_state.py
    # truncado/corrompido levanta SyntaxError na importação, que não é
    # subclasse de ImportError e escaparia do except mais estrito.
    import specgate_state
except Exception:
    specgate_state = None

MAX_LINES = 400


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return

    if not isinstance(payload, dict):
        # Payload que não é um objeto JSON (lista, número, string, null...)
        # não tem .get(...) — qualquer acesso abaixo levantaria
        # AttributeError não capturado. Sem um payload no formato esperado
        # não há o que logar: fail-open, este hook nunca bloqueia nada.
        return

    # TUDO que roda a partir daqui — extração de campos, leitura/escrita de
    # estado, e a gravação do log — precisa estar dentro do try/except:
    # este é o hook que grava o seq (a prova de turno), e o contrato do
    # módulo é "logging jamais bloqueia nada", sem exceção de fase nenhuma
    # do processamento.
    try:
        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            # cwd ausente ou de tipo inesperado (list/int/dict/None): não dá
            # para confiar nele para os.path.join. Cai no cwd real do
            # processo, que é sempre uma string válida.
            cwd = os.getcwd()
        if not os.path.isfile(os.path.join(cwd, ".specgate.json")):
            return

        ev = payload.get("hook_event_name", "event")
        if not isinstance(ev, str) or not ev:
            ev = "event"
        entry = {
            "ts": time.strftime("%H:%M:%S"),
            "event": ev,
        }
        # O seq só avança em turno real do usuário: é o evento que o Claude
        # não fabrica, e é disso que depende o gate de PO — não do arquivo
        # .specgate/seq em si (esse é protegido só pela camada de fricção
        # do gate_guard). Nenhum outro evento pode mover o contador.
        # Sem o módulo (import falhou), seguimos logando o resto sem o
        # campo seq em vez de explodir — fail-open também aqui.
        if specgate_state is not None:
            try:
                if ev == "UserPromptSubmit":
                    entry["seq"] = specgate_state.bump_seq(cwd)
                else:
                    entry["seq"] = specgate_state.read_seq(cwd)
            except Exception:
                # Módulo presente mas desatualizado/parcial (ex.: sem
                # bump_seq/read_seq) levanta AttributeError aqui. Mesmo
                # fallback do módulo ausente: loga o resto sem o campo seq.
                entry.pop("seq", None)
        tool = payload.get("tool_name")
        if tool:
            entry["tool"] = tool
            ti = payload.get("tool_input")
            if not isinstance(ti, dict):
                # tool_input ausente ou de tipo inesperado (string/lista/
                # etc.): sem dict não há .get(...) a chamar abaixo.
                ti = {}
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
    except Exception:
        # Rede de segurança final: qualquer erro interno não previsto acima
        # (payload malformado de um jeito não coberto, etc.) não pode fazer
        # este hook quebrar a sessão do usuário — loga o quanto conseguiu
        # (ou nada) e sai limpo.
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
