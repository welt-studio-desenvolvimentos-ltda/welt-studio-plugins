#!/usr/bin/env python3
"""spec-gate: leitura e escrita do estado compartilhado em .specgate/.

Usado por gate_guard.py (PreToolUse) e log_event.py (demais eventos).
Zero dependências: stdlib apenas. Toda função devolve um default seguro em
caso de erro — nenhum problema de estado pode travar a sessão do Claude.
"""
import json
import os
import tempfile

STATE_DIR = ".specgate"


def _p(cwd, *parts):
    return os.path.join(cwd, STATE_DIR, *parts)


def read_seq(cwd):
    """Contador monotônico de turnos do usuário. 0 se ausente ou ilegível."""
    try:
        with open(_p(cwd, "seq"), "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return 0


def bump_seq(cwd):
    """Incrementa e devolve o novo valor. Só o UserPromptSubmit chama isto."""
    n = read_seq(cwd) + 1
    state_dir = _p(cwd)
    try:
        os.makedirs(state_dir, exist_ok=True)
        # Escrita atômica: grava num temporário no mesmo diretório e substitui
        # por cima com os.replace (atômico em POSIX e Windows). Uma
        # interrupção no meio (kill -9, disco cheio) nunca deixa o arquivo
        # "seq" truncado — ou o rename acontece inteiro, ou o arquivo antigo
        # permanece intacto. Sem isso, um "seq" truncado vira ValueError em
        # read_seq, o contador volta a 0 e regride, travando gates para sempre.
        fd, tmp_path = tempfile.mkstemp(dir=state_dir, prefix=".seq.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(str(n))
            os.replace(tmp_path, _p(cwd, "seq"))
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    except OSError:
        return read_seq(cwd)
    return n


def read_gates(cwd):
    """Todos os gates registrados. [] se ausente ou ilegível."""
    try:
        with open(_p(cwd, "gate.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        data = [data]  # tolera o formato de gate único
    if not isinstance(data, list):
        return []
    return [g for g in data if isinstance(g, dict)]


def open_gates(cwd):
    """Gates aguardando decisão do PO."""
    return [g for g in read_gates(cwd) if g.get("status") == "aguardando-po"]


def has_human_turn_since(cwd, opened_at_seq):
    """Camada 1 do gate: houve turno REAL do usuário depois do gate abrir?

    O Claude não consegue fabricar um UserPromptSubmit, então este é o
    único fato do sistema que ele não pode forjar.
    """
    try:
        return read_seq(cwd) > int(opened_at_seq)
    except (TypeError, ValueError):
        return False


def gate_po_1_passed(cwd):
    """Fato em disco: o Gate PO 1 (backlog) já foi aprovado?

    O congelamento de docs/backlog/ só liga daqui em diante. Antes disso o
    spec-analyst ainda está escrevendo as specs e precisa de acesso.
    """
    try:
        with open(_p(cwd, "batch.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    return bool(isinstance(data, dict) and data.get("backlog_aprovado"))
