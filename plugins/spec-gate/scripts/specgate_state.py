#!/usr/bin/env python3
"""spec-gate: leitura e escrita do estado compartilhado em .specgate/.

Usado por gate_guard.py (PreToolUse) e log_event.py (demais eventos).
Zero dependências: stdlib apenas. Toda função devolve um default seguro em
caso de erro — nenhum problema de estado pode travar a sessão do Claude.
"""
import json
import os
import tempfile
import time

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


def _atomic_write(cwd, name, text):
    """Escrita atômica de um arquivo de estado. True se persistiu.

    Grava num temporário no mesmo diretório e substitui por cima com
    os.replace (atômico em POSIX e Windows). Uma interrupção no meio
    (kill -9, disco cheio) nunca deixa o arquivo truncado — ou o rename
    acontece inteiro, ou o arquivo antigo permanece intacto. Sem isso, um
    "seq" truncado vira ValueError em read_seq, o contador volta a 0 e
    regride, travando gates para sempre; e um "attempts.json" truncado
    zeraria o contador de tentativas no meio do teto.
    """
    state_dir = _p(cwd)
    try:
        os.makedirs(state_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=state_dir, prefix="." + name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp_path, _p(cwd, name))
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    except OSError:
        return False
    return True


def bump_seq(cwd):
    """Incrementa e devolve o novo valor. Só o UserPromptSubmit chama isto."""
    n = read_seq(cwd) + 1
    if not _atomic_write(cwd, "seq", str(n)):
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


def _round_number(g):
    """Número da rodada de um gate, como int >= 1. Nunca levanta: valor
    ausente ou malformado (string não numérica, float, negativo, tipos
    mistos) vira 1 — o mesmo default de compatibilidade usado por
    `gate_guard.py` para gates gravados antes da rodada existir. Esta
    função é usada só para ESCOLHER o gate vigente (maior rodada) na
    leitura; a validação de verdade de quem pode escrever cada rodada é
    do guard, não daqui.
    """
    v = g.get("rodada", 1)
    if isinstance(v, bool):
        return 1
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 1
    return n if n >= 1 else 1


def current_gates(cwd):
    """Um gate por (checkpoint, pbi): sempre o de MAIOR rodada.

    Uma vez que a chave de um gate passou a incluir a rodada (Task 9),
    gate.json acumula uma entrada por rodada de cada (checkpoint, pbi) —
    o histórico de rework é o ponto (ver `.specgate/gate.json`: rodadas
    anteriores, já decididas, continuam no array como registro). Nenhum
    consumidor de "qual é o gate deste PBI agora" deve olhar uma rodada
    antiga: ela já foi decidida e não representa mais nada pendente.
    """
    best = {}
    for g in read_gates(cwd):
        key = (g.get("checkpoint"), g.get("pbi"))
        if key not in best or _round_number(g) > _round_number(best[key]):
            best[key] = g
    return list(best.values())


def open_gates(cwd):
    """Gates aguardando decisão do PO — só o gate VIGENTE (maior rodada) de
    cada (checkpoint, pbi). Uma rodada anterior já decidida nunca conta
    como pendente, mesmo que apareça em disco com o status antigo.
    """
    return [g for g in current_gates(cwd) if g.get("status") == "aguardando-po"]


def has_human_turn_since(cwd, opened_at_seq):
    """Houve turno REAL do usuário depois do gate abrir?

    Esta comparação (`seq_atual > opened_at_seq`) é só aritmética sobre o
    contador em `.specgate/seq`; o arquivo em si é protegido apenas pela
    camada de fricção do gate_guard (ver `guard_seq_lock`), não é à prova
    de qualquer escrita via Bash. A propriedade genuinamente forte está em
    QUEM incrementa esse contador: só `log_event.py`, rodando como hook no
    evento `UserPromptSubmit`, que o Claude não consegue fabricar — por
    isso o caminho honesto (esperar a fala real do usuário) é sempre mais
    barato do que qualquer tentativa de forjar o contador.
    """
    try:
        return read_seq(cwd) > int(opened_at_seq)
    except (TypeError, ValueError):
        return False


def same_spec(cwd, a, b):
    """Dois identificadores de PBI apontam para o MESMO arquivo de spec?

    Comparação por caminho resolvido, igual à de `_same_file` no gate_guard —
    e não por igualdade de string. O identificador chega de duas fontes que
    ninguém obriga a escrever igual: o campo `pbi` dos gates e o sufixo de
    `.specgate/phase`. Comparar como texto faria `docs/backlog/02-x.md` e
    `./docs/backlog/02-x.md` valerem como PBIs DIFERENTES — o que zeraria o
    contador de tentativas (o registro passaria a ser "de outro PBI") sem que
    o PO tivesse decidido nada, transformando o teto num mecanismo que quem
    está sendo medido desliga trocando a grafia da própria fase.
    """
    if not isinstance(a, str) or not isinstance(b, str) or not a or not b:
        return False
    if a == b:
        return True
    try:
        return os.path.realpath(os.path.join(cwd, os.path.expanduser(a))) == \
            os.path.realpath(os.path.join(cwd, os.path.expanduser(b)))
    except (OSError, ValueError):
        return False


def round_for(cwd, pbi, checkpoint):
    """Rodada vigente da série (checkpoint, pbi) — 0 se a série não existe.

    Usada para dar validade LIMITADA a fatos que um gate específico invalida.
    A prova de RED, por exemplo, vale enquanto os testes daquele PBI forem os
    mesmos: reprovar o gate de TESTES abre rodada nova e a prova caduca (os
    testes mudaram, o vermelho precisa ser provado de novo), mas reprovar o
    gate de ACEITE não mexe nela — os testes continuam os mesmos, e exigir
    RED ali seria pedir o impossível, porque nesse ponto o código já existe.
    """
    best = 0
    for g in read_gates(cwd):
        if g.get("checkpoint") != checkpoint or not same_spec(cwd, g.get("pbi"), pbi):
            continue
        best = max(best, _round_number(g))
    return best


def max_round_for_pbi(cwd, pbi):
    """Maior rodada entre TODAS as séries de um PBI — 0 se não há nenhuma.

    Usada pelo contador de tentativas: qualquer gate daquele PBI ganhando
    rodada nova é evidência de que o PO falou de novo sobre ele (abrir rodada
    seguinte exige turno humano real, ver `guard_gate_clear`), e é isso que
    legitima devolver as tentativas ao implementador. Aqui, ao contrário da
    prova de RED, resetar a mais é o lado seguro: o custo de uma tentativa
    extra é uma rodada de trabalho; o de travar um PBI que o PO acabou de
    mandar retomar é o fluxo parado.
    """
    best = 0
    for g in read_gates(cwd):
        if not same_spec(cwd, g.get("pbi"), pbi):
            continue
        best = max(best, _round_number(g))
    return best


def _read_json_obj(cwd, name):
    """Objeto JSON de um arquivo de estado. {} se ausente, ilegível ou não-objeto."""
    try:
        with open(_p(cwd, name), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_red(cwd):
    """Provas de RED registradas, por PBI. {} se ausente ou ilegível."""
    return _read_json_obj(cwd, "red.json")


def red_proven(cwd, pbi, round_):
    """Já existe prova de que a suíte deste PBI falhava antes de implementar?

    A prova é POR RODADA do gate de testes (ver `round_for`): enquanto os
    testes aprovados forem os mesmos, o vermelho já provado continua valendo,
    e o guard não roda a suíte de novo a cada reentrada em implementação —
    o que, depois que o código existe, bloquearia o fluxo por encontrar verde.
    """
    entry = read_red(cwd).get(pbi)
    if not isinstance(entry, dict) or not entry.get("proven"):
        return False
    try:
        return int(entry.get("round", 0)) == int(round_)
    except (TypeError, ValueError):
        return False


def record_red(cwd, pbi, round_, exit_code, waived=False):
    """Registra a prova de RED de um PBI. Devolve True se persistiu.

    `waived=True` marca a prova como dispensada pelo PO (gate 'red' aprovado):
    o vermelho não foi observado, o PO decidiu que o verde era legítimo. O
    campo existe para o board não mostrar as duas situações como iguais.
    """
    data = read_red(cwd)
    data[pbi] = {
        "proven": True,
        "waived": bool(waived),
        "exit": exit_code,
        "round": round_,
        "seq": read_seq(cwd),
        "ts": time.strftime("%H:%M:%S"),
    }
    return _atomic_write(cwd, "red.json", json.dumps(data, ensure_ascii=False))


def read_attempts(cwd):
    """Estado do contador de tentativas. {} se ausente ou ilegível."""
    return _read_json_obj(cwd, "attempts.json")


def attempt_count(cwd, pbi, round_):
    """Tentativas já gastas neste PBI nesta rodada. 0 se o registro é de outro
    PBI ou de uma rodada anterior — nesses casos o contador está zerado de fato,
    e devolver o número velho seria contar contra o trabalho errado.

    "Outro PBI" é decidido por `same_spec` (caminho resolvido), nunca por
    igualdade de string: senão reescrever `.specgate/phase` com outra grafia
    do MESMO arquivo (`./docs/backlog/02-x.md`) zeraria o teto sem decisão
    nenhuma do PO.
    """
    data = read_attempts(cwd)
    if not same_spec(cwd, data.get("pbi"), pbi):
        return 0
    try:
        if int(data.get("round", 0)) != int(round_):
            return 0
        return int(data.get("count", 0))
    except (TypeError, ValueError):
        return 0


MAX_ATTEMPT_HISTORY = 20


def bump_attempt(cwd, pbi, round_, cmd):
    """Conta mais uma execução da suíte na fase de implementação deste PBI.

    Devolve o total EM DISCO depois da chamada. Só o guard (PreToolUse) chama
    isto — é o mesmo princípio do contador de turnos: quem conta é o hook, não
    o agente que está sendo contado, e por isso o número não é autodeclarável.

    Se a escrita não persistir (disco cheio, diretório read-only), devolve o
    total ANTERIOR em vez do incrementado: quem chama usa esse número para
    avisar o PO e para comparar com o teto, e devolver um valor que o disco
    não guardou faria o aviso sair sobre uma tentativa que nunca foi contada.
    """
    used = attempt_count(cwd, pbi, round_)
    # `used == 0` significa registro de outro PBI/rodada (ou ausente): o
    # histórico anterior é de outro trabalho e não deve ser herdado.
    data = read_attempts(cwd) if used else {}
    history = data.get("history")
    if not isinstance(history, list) or not used:
        history = []
    history.append({"seq": read_seq(cwd), "ts": time.strftime("%H:%M:%S"), "cmd": str(cmd)[:120]})
    data.update({
        "pbi": pbi,
        "round": round_,
        "count": used + 1,
        "history": history[-MAX_ATTEMPT_HISTORY:],
    })
    if not _atomic_write(cwd, "attempts.json", json.dumps(data, ensure_ascii=False)):
        return used
    return data["count"]


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
