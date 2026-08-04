#!/usr/bin/env python3
"""spec-gate PreToolUse guard.

Gates mecânicos, todos inertes a menos que o projeto tenha .specgate.json.
Os dois que cercam o ciclo de teste — um em cada ponta, e nenhum deles
autodeclarável, porque quem executa a suíte é o hook e não o agente:

1. Prova de RED (`guard_red_evidence`): a suíte precisa estar VERMELHA na
   transição para a fase de implementação. Testes que já passam não
   capturam o comportamento da spec.

2. Gate de regressão (`guard_regression`): intercepta `git commit` e
   `git merge`, roda o test_command e bloqueia se a suíte falhar.

E os que cercam o trabalho entre eles:

3. Fase de testes (`.specgate/phase` começa com "testing"): bloqueia
   leitura de código-fonte (source_paths) por Read, Grep, Glob e por
   comandos Bash que inspecionam arquivos. Garante o black-box.

4. Teto de tentativas (`guard_attempts`): conta as execuções da suíte
   durante a implementação e bloqueia edição de código-fonte no estouro.

5. Estado do fluxo: chokepoint de gate de PO, contador de turnos,
   congelamento da spec, batch.json, os arquivos que só o hook escreve
   (`guard_hook_state_lock`: red.json e attempts.json — sem ele os dois
   mecanismos acima voltam a ser autodeclaráveis) e bloqueio de operações
   destrutivas.

Protocolo de hook do Claude Code: JSON no stdin; exit 0 permite,
exit 2 bloqueia e envia o stderr de volta ao Claude como feedback.
Qualquer erro interno do guard resulta em exit 0 (nunca quebrar a sessão).
"""
import functools
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile

try:
    # O import roda antes de sabermos se o projeto usa spec-gate, e este é um
    # hook BLOQUEANTE: se ele quebrar, trava a sessão do usuário. Uma
    # instalação corrompida ou checkout parcial não pode derrubar o guard —
    # por isso o import é à prova de falha e o módulo vira None quando
    # ausente. Captura qualquer Exception (não só ImportError): um
    # specgate_state.py truncado/corrompido levanta SyntaxError na
    # importação, que não é subclasse de ImportError e escaparia do except
    # mais estrito.
    import specgate_state
except Exception:
    specgate_state = None

READ_LIKE_TOOLS = {"Read", "Grep", "Glob"}
BASH_READ_CMDS = {
    "cat", "head", "tail", "less", "more", "grep", "rg", "ag", "sed",
    "awk", "cut", "strings", "xxd", "hexdump", "nl", "od", "bat",
}
COMMIT_RE = re.compile(r"\bgit\b.*\b(commit|merge)\b")
MERGE_RE = re.compile(r"\bgit\b.*\bmerge\b")
DESTRUCTIVE_RES = [
    (re.compile(r"\bgit\b.*\breset\b.*--hard"), "git reset --hard"),
    (re.compile(r"\bgit\b.*\bclean\b.*-[a-zA-Z]*f"), "git clean -f"),
    (re.compile(r"\bgit\b.*\bpush\b.*(--force\b|-f\b)(?!-with-lease)"), "git push --force"),
    (re.compile(r"\bgit\b.*\bbranch\b.*-D\b"), "git branch -D"),
    (re.compile(r"\bgit\b.*\bcheckout\b\s+(--\s+)?\.(\s|$)"), "git checkout ."),
    (re.compile(r"\bgit\b.*\brestore\b(?!.*--staged).*\s\.(\s|$)"), "git restore ."),
    (re.compile(r"\brm\b\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b"), "rm -rf"),
]
ALLOW_FILE = os.path.join(".specgate", "allow-destructive")
BASH_WRITE_RES = [
    re.compile(r"\bsed\b\s+-[a-zA-Z]*i"),
    re.compile(r"\btee\b"),
    re.compile(r">{1,2}\s*\S"),
    re.compile(r"\b(mv|cp|rm)\b"),
    re.compile(r"\btruncate\b"),
    re.compile(r"\bdd\b"),
    re.compile(r"\binstall\b"),
]
# LIMITE HONESTO, de propósito: esta lista NÃO é exaustiva e não pretende
# ser. Ela cobre os desvios CASUAIS mais prováveis (redirecionamento, tee,
# sed -i, mv/cp/rm, truncate, dd, install, interpretador inline/heredoc —
# ver os comentários abaixo). Continuam passíveis de contornar por design,
# porque isto é fricção e não um parser de shell de verdade: `eval "..."`,
# `base64 -d ... | sh`, `awk 'BEGIN{print > "arquivo"}'` (o `>` fica dentro
# de uma string entre aspas que o tokenizador trata como um único argumento
# opaco do awk, não como redirecionamento do shell), indireção por variável
# (`X=arquivo; echo oi > $X`) e substituição de comando no alvo
# (`echo oi > "$(...)"`). Um modelo racionalizando um atalho casual não
# chega nesses contornos; um adversário decidido sempre chega, e não é
# esse o alvo aqui.
# Invocação de interpretador com código inline: `python3 -c "..."`,
# `sh -c "..."`, etc. escapam por completo dos regexes acima porque o
# comando Bash em si não tem `>`, `tee`, `sed -i`... a escrita acontece
# DENTRO do código passado ao interpretador. Ver o comentário longo em
# cima de `write_targets` para o porquê disto ser "fechar o barato" e não
# um parser de verdade.
_INTERPRETER_INLINE_FLAGS = {
    "python": {"-c"},
    "node": {"-e", "--eval"},
    "nodejs": {"-e", "--eval"},
    "perl": {"-e"},
    "ruby": {"-e"},
    "php": {"-r"},
    "sh": {"-c"},
    "bash": {"-c"},
    "dash": {"-c"},
    "zsh": {"-c"},
    "ksh": {"-c"},
}
_QUOTED_STRING_RE = re.compile(r"(['\"])(.*?)\1")
# Heredoc de interpretador: `python3 <<EOF`, `python3 <<-'EOF'`, etc. O
# grupo com aspas (se houver) precisa casar dos dois lados (`\1` no fim)
# para o fim do match ficar depois da aspa de fechamento, não no meio dela.
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
# I4 (achado da revisão final): prefixo literal de um pattern de Glob, antes
# do primeiro caractere curinga. Usado só quando a chamada NÃO informa
# 'path' — nesse caso o Glob varre a partir de cwd, e um pattern sem
# restrição de diretório na frente ("**/*.py") alcança source_paths tanto
# quanto path="." (que já era bloqueado). "tests/**/*.py" devolve "tests",
# que não toca source_paths (passa, sem falso positivo).
_GLOB_WILDCARD_RE = re.compile(r"[*?\[{]")


def _glob_pattern_root(pattern):
    m = _GLOB_WILDCARD_RE.search(pattern)
    prefix = pattern[:m.start()] if m else pattern
    prefix = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
    return prefix or "."


def _git_show_targets(tokens):
    """Candidatos a caminho de `git show <rev>:<caminho>` — a forma de ler
    conteúdo VERSIONADO sem tocar o arquivo de trabalho, que escapa de
    BASH_READ_CMDS (que só reconhece leitores que operam sobre o arquivo em
    disco, tipo cat/head/grep). Fricção, não parser de verdade: só cobre a
    forma direta `git show <rev>:<caminho>`.
    """
    if not tokens or os.path.basename(tokens[0]) != "git":
        return []
    if len(tokens) < 2 or tokens[1] != "show":
        return []
    return [t.split(":", 1)[1] for t in tokens[2:] if not t.startswith("-") and ":" in t]


PHASE_REL = os.path.join(".specgate", "phase")
GATE_REL = os.path.join(".specgate", "gate.json")
SEQ_REL = os.path.join(".specgate", "seq")
BATCH_REL = os.path.join(".specgate", "batch.json")
RED_REL = os.path.join(".specgate", "red.json")
ATTEMPTS_REL = os.path.join(".specgate", "attempts.json")

# Amarra 2 do mecanismo de rodada (Task 9): quais status DECIDIDOS de uma
# rodada anterior legitimam abrir a rodada seguinte da mesma série
# (checkpoint, pbi). Nem todo checkpoint decide com o mesmo vocabulário —
# 'testes', 'backlog' e 'aceite' decidem com 'reprovado'/'aprovado', mas o
# checkpoint 'ambiguidade' decide com 'respondido' (não existe "aprovar"
# ou "reprovar" uma ambiguidade). Lista ÚNICA, checada por todo checkpoint
# igualmente: nenhuma condicional por nome de checkpoint em lugar nenhum
# do guard. A lição do bug relatado é exatamente essa — a convenção de
# status do checkpoint 'ambiguidade' foi assumida em um lugar (o comando
# /spec-gate) e não no outro (aqui), e a chave congelava para sempre depois
# da primeira ambiguidade respondida. 'aprovado' fica de fora de propósito:
# um PBI aprovado não está "brigando", não há o que legitimar de novo.
STATUSES_THAT_LEGITIMIZE_ROUND = ("reprovado", "respondido")

# Checkpoints cuja decisão do PO É, ela própria, uma mudança no contrato —
# e por isso os únicos que abrem a janela estreita de escrita da spec (ver
# `_spec_resume_window_open`). 'ambiguidade' resolve o que a spec não dizia;
# 'emenda' muda o que ela dizia, num PBI já entregue. Nenhum outro checkpoint
# reescreve requisito ao ser decidido, então nenhum outro precisa da janela.
SPEC_WINDOW_CHECKPOINTS = ("ambiguidade", "emenda")


def _spec_resume_window_open(cwd, candidate):
    """C1: a única exceção ao congelamento de docs/backlog/ — a janela de
    escrita que fecha o ciclo de estacionamento.

    O gate de ambiguidade estaciona um PBI; o PO responde ("respondido");
    e a retomada exige atualizar a spec DAQUELE PBI com a decisão antes de
    fazer o merge de volta (ver seção 7 do comando `/spec-gate`). Sem esta
    janela, `guard_spec_lock` bloqueava essa atualização sempre, sem
    distinguir este caso — o mesmo congelamento que protege a spec de ser
    reescrita por quem está sendo julgado também impedia o próprio PO de
    ver sua decisão registrada, e o ciclo nunca fechava.

    A promessa do congelamento continua de pé: "o agente não altera o
    contrato por conta própria". O agente escreve, sim, mas só
    TRANSCREVENDO uma decisão do PO que já está registrada em
    `.specgate/gate.json` — a fala do PO é pré-condição mecânica, não uma
    exceção de conveniência. Por isso a janela reusa o MESMO mecanismo que
    valida qualquer outra decisão de gate (`has_human_turn_since`), em vez
    de inventar um novo caminho.

    Abre só quando TODAS as condições valem para o gate VIGENTE (maior
    rodada, via `gates_vigentes` — o mesmo seletor usado pelo board, pelo
    dashboard e pela statusline) de uma série (checkpoint, pbi):

    1. `checkpoint` é `ambiguidade` ou `emenda` — os dois únicos que decidem
       com o vocabulário "respondido", e os dois únicos em que a decisão do
       PO É uma mudança no contrato: na ambiguidade ele resolve o que a spec
       não dizia; na emenda ele muda o que ela dizia, num PBI já entregue
       (0.3.0). Esta janela não é uma chave mestra para gates decididos em
       geral — backlog/testes/aceite/RED continuam sem nenhuma exceção ao
       congelamento, porque a decisão deles não reescreve requisito nenhum.
    2. `status == "respondido"` — o PO decidiu a ambiguidade.
    3. `has_human_turn_since(cwd, opened_at_seq)` — um turno REAL do PO
       aconteceu depois que este gate abriu. Esta é a mesma checagem que
       `guard_gate_clear` usa para aceitar qualquer decisão de gate; não
       há checagem nova para burlar aqui.
    4. A fase corrente não é a DESTE PBI — o retomada ainda não reativou a
       fase dele (a seção 7 do comando desativa a fase ANTES de abrir o gate
       de ambiguidade, e só a reativa DEPOIS do merge de volta). Assim que a
       fase deste PBI avança de novo, a janela fecha, mesmo que o gate
       continue "respondido" — "fecha quando a fase avança".

       A comparação é POR PBI desde 0.3.0, pelo mesmo motivo que o chokepoint
       de `guard_po_gate` passou a ser: com a fila andando enquanto um PBI
       está estacionado, a fase corrente quase sempre é de OUTRO item, e
       exigir a fase globalmente vazia deixaria a janela fechada justamente
       no cenário que a versão nova existe para permitir — o PO responde a
       ambiguidade do PBI-03 e o analista não consegue transcrever a decisão
       porque o PBI-04 está em implementação. Fase sem PBI declarado
       (`testing`, que é do lote inteiro, ou formato antigo) continua
       fechando a janela para todo mundo: não dá para dizer de quem ela é, e
       ignorância cai no lado conservador, como no resto do arquivo.
    5. `candidate` é EXATAMENTE o arquivo do campo `pbi` do gate (via
       `_same_file`, comparação de caminho resolvido) — nunca o diretório
       `docs/backlog/` inteiro, nunca a spec de outro PBI. Janela do
       tamanho do buraco, não do corredor.

    "Fecha quando o gate ganha rodada nova" não precisa de lógica extra:
    `gates_vigentes` já devolve só a rodada de MAIOR número por série. Se o
    mesmo PBI encontrar outra ambiguidade depois, a rodada nova abre como
    "aguardando-po" (ver `_rodada_invalida` em `guard_gate_clear`) e passa
    a ser a vigente — a rodada anterior "respondido" deixa de contar aqui,
    sem que esta função precise saber nada sobre rodadas.
    """
    if not candidate:
        return False
    phase, phase_pbi = parse_phase(current_phase(cwd))
    if phase and not phase_pbi:
        return False  # fase ativa sem dono declarado: fecha para todo mundo
    for g in _safe_current_gates(cwd):
        if g.get("checkpoint") not in SPEC_WINDOW_CHECKPOINTS or g.get("status") != "respondido":
            continue
        pbi = g.get("pbi")
        if not isinstance(pbi, str) or not pbi:
            continue
        if not _same_file(candidate, cwd, pbi):
            continue
        if phase and _same_file(phase_pbi, cwd, pbi):
            continue  # a fase DESTE PBI já avançou: a janela dele fechou
        if _safe_has_human_turn(cwd, g.get("opened_at_seq", 0)):
            return True
    return False


def guard_spec_lock(tool, tool_input, cwd, cfg):
    spec_paths = cfg.get("spec_paths", ["docs/backlog"])
    spec_dirs = norm_paths(cwd, spec_paths)
    if not spec_dirs:
        return
    reason = (
        "[spec-gate] SPEC CONGELADA: o Gate PO 1 já aprovou o backlog e a spec "
        "é o contrato que julga o trabalho, então ela não pode ser alterada por quem "
        "está sendo julgado ({alvo}). Se você acredita que a spec está errada, "
        "PARE o pipeline e apresente o caso ao usuário: o que a spec diz, o que "
        "você encontrou, e qual mudança propõe. Só o usuário altera o contrato. "
        "Exceção: a spec DESTE PBI abre uma janela estreita de escrita quando o "
        "gate de ambiguidade dele está 'respondido' com um turno real do PO "
        "depois da abertura (retomada de estacionamento) — se você está "
        "transcrevendo a decisão que o PO acabou de dar sobre este PBI, "
        "confira se o gate já está 'respondido' e se a fase ainda não foi "
        "reativada; se ainda estiver bloqueado, PARE e explique."
    )
    # Usa write_targets (a mesma extração de alvos dos outros guards) para
    # herdar o reconhecimento de interpretador inline/heredoc e dd/install —
    # antes deste fix, guard_spec_lock fazia seu próprio parsing direto sobre
    # BASH_WRITE_RES e não reconhecia esses caminhos, deixando a spec
    # congelada escrevível por eles. A comparação em si continua sendo
    # touches_source (casa diretório, não só arquivo exato), diferente de
    # _same_file usado pelos guards de arquivo único — spec_paths são
    # diretórios, então qualquer arquivo dentro deles precisa ser pego.
    for t in write_targets(tool, tool_input):
        if not touches_source(t, cwd, spec_dirs):
            continue
        if _spec_resume_window_open(cwd, t):
            continue
        block(reason.format(alvo=t))


def load_config(cwd):
    path = os.path.join(cwd, ".specgate.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def current_phase(cwd):
    """Conteúdo bruto de .specgate/phase ("" se ausente).

    Continua devolvendo a STRING crua, e não a tupla de `parse_phase`, porque
    quem só precisa saber "a fase está vazia?" (a janela de escrita da spec,
    em `_spec_resume_window_open`) não deve ter que saber o formato interno.
    """
    path = os.path.join(cwd, ".specgate", "phase")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def parse_phase(raw):
    """`<fase>:<pbi>` -> ("fase", "pbi"). Sem sufixo -> ("fase", "").

    O sufixo é o CAMINHO da spec do PBI, o mesmo identificador do campo `pbi`
    dos gates (`docs/backlog/02-nome.md`) — não um slug traduzido. Usar a
    mesma chave dos dois lados é o que permite casar a fase corrente com o
    gate correspondente sem nenhuma tradução no meio, que seria mais uma
    convenção para as duas pontas divergirem.

    Formato antigo (só a fase, sem sufixo) continua válido e devolve pbi "".
    Nesse caso os mecanismos que precisam do PBI — contador de tentativas e
    prova de RED — ficam inertes, em vez de contar contra o PBI errado.
    """
    raw = (raw or "").strip()
    if not raw:
        return "", ""
    phase, _, pbi = raw.partition(":")
    return phase.strip(), pbi.strip()


def norm_paths(cwd, raw_paths):
    out = []
    for p in raw_paths or []:
        ap = os.path.realpath(os.path.join(cwd, p))
        out.append(ap.rstrip(os.sep) + os.sep)
    return out


def touches_source(candidate, cwd, source_dirs):
    if not candidate:
        return False
    ap = os.path.realpath(os.path.join(cwd, os.path.expanduser(candidate)))
    ap_dir = ap.rstrip(os.sep) + os.sep
    return any(ap.startswith(d) or d.startswith(ap_dir) for d in source_dirs)


def block(msg):
    sys.stderr.write(msg)
    sys.exit(2)


# Avisos não-bloqueantes acumulados durante os guards e emitidos por main()
# no fim, em stdout + exit 0 (o canal que o Claude Code lê como mensagem de
# sistema). Acumular em vez de imprimir na hora preserva a ordem dos guards:
# um aviso no meio não pode encurtar a passagem pelos guards seguintes, que
# ainda podem BLOQUEAR a mesma chamada.
_NOTICES = []


def notice(msg):
    _NOTICES.append(msg)


def guard_testing_phase(tool, tool_input, cwd, cfg):
    source_dirs = norm_paths(cwd, cfg.get("source_paths", ["src"]))
    if not source_dirs:
        return

    reason = (
        "[spec-gate] Fase de testes black-box ativa: leitura de código-fonte "
        "bloqueada ({alvo}). Escreva os testes apenas a partir da spec do PBI "
        "em docs/backlog/. Se a spec não bastar, registre a lacuna em "
        "'Ambiguidades encontradas' em vez de inspecionar a implementação."
    )

    if tool in READ_LIKE_TOOLS:
        candidates = [
            tool_input.get("file_path"),
            tool_input.get("path"),
            tool_input.get("pattern") if tool == "Glob" else None,
        ]
        # I4: 'path' AUSENTE (não vazio, ausente mesmo) significa que a
        # busca parte de cwd inteiro — mesmo alcance de path="." (que já
        # era bloqueado antes deste fix). Grep sem 'path' é o caminho mais
        # natural de todos para ler source_paths sem disparar o guard.
        if tool == "Grep" and "path" not in tool_input:
            candidates.append(".")
        if tool == "Glob" and "path" not in tool_input:
            pattern = tool_input.get("pattern")
            if isinstance(pattern, str):
                candidates.append(_glob_pattern_root(pattern))
        for c in candidates:
            if isinstance(c, str) and touches_source(c, cwd, source_dirs):
                block(reason.format(alvo=c))

    elif tool == "Bash":
        cmd = tool_input.get("command", "")
        if not isinstance(cmd, str) or not cmd:
            return
        # I5: este é um guard de LEITURA, e a escolha do lado seguro aqui é
        # a OPOSTA à dos 4 guards de estado (ver BASH_CMD_TAMANHO_MAXIMO_
        # VERIFICAVEL): um comando grande demais para valer a pena parsear
        # é, de longe, mais provável de ser um heredoc legítimo (escrevendo
        # um arquivo qualquer) do que uma tentativa de ler source_paths por
        # esse caminho — quem quiser espiar o código não precisa de 64KB de
        # comando para isso. Bloquear todo comando grande aqui seria
        # fricção desproporcional ao risco, e reintroduziria a regressão de
        # latência que este fix corrige (o parsing abaixo é o mesmo custo
        # por token que os guards de estado tinham).
        if len(cmd) > BASH_CMD_MAX_VERIFIABLE_SIZE:
            return
        try:
            tokens = shlex.split(cmd, posix=True)
        except ValueError:
            tokens = cmd.split()
        if not tokens:
            return

        candidates = []
        if any(os.path.basename(t) in BASH_READ_CMDS for t in tokens):
            candidates.extend(t for t in tokens[1:] if not t.startswith("-"))
        # `git show <rev>:<caminho>` lê conteúdo versionado sem tocar o
        # arquivo de trabalho — "git" nunca está em BASH_READ_CMDS.
        candidates.extend(_git_show_targets(tokens))
        # Interpretador inline/heredoc (`python3 -c`, `sh -c`, heredoc):
        # reusa a MESMA extração de write_targets em vez de duplicar o
        # parsing — ela já devolve os candidatos de dentro do código
        # (strings entre aspas, tokens soltos), independente de o uso ser
        # leitura ou escrita (ver docstring de write_targets). Sem isto,
        # `python3 -c "print(open('src/x.py').read())"` escapava por
        # completo: nem "python3" está em BASH_READ_CMDS.
        candidates.extend(write_targets(tool, tool_input))

        for c in candidates:
            if touches_source(c, cwd, source_dirs):
                block(reason.format(alvo=c))


def guard_destructive(tool_input, cwd, cfg):
    if cfg.get("block_destructive") is False:
        return
    cmd = tool_input.get("command", "")
    if not isinstance(cmd, str) or not cmd:
        return
    matched = None
    for rx, label in DESTRUCTIVE_RES:
        if rx.search(cmd):
            matched = label
            break
    if matched is None:
        return
    allow_path = os.path.join(cwd, ALLOW_FILE)
    if os.path.isfile(allow_path):
        try:
            os.remove(allow_path)  # liberacao de uso unico, consumida agora
        except OSError:
            pass
        return
    block(
        f"[spec-gate] Operação destrutiva bloqueada ({matched}). Este comando "
        "descarta trabalho de forma irreversível. NÃO tente contornar o bloqueio. "
        "Explique ao usuário exatamente o que será perdido e peça confirmação "
        "explícita. Se o usuário confirmar, crie o arquivo .specgate/allow-destructive "
        "(vazio) e reexecute o comando UMA vez; a liberação é consumida na execução. "
        "Exceção: reverter APENAS os arquivos de um PBI estacionado é "
        "permitido via checkout/restore de caminhos específicos, nunca do "
        "repositório inteiro. Para limpar branch parked/* já mergeada use "
        "'git branch -d' minúsculo, que não é bloqueado."
    )


def current_branch(cwd):
    """Branch atual, ou string vazia se não for repo git / git indisponível.

    Fail-open deliberado NO SENTIDO CONTRÁRIO ao resto do módulo: aqui "" é
    o lado que MANTÉM o gate de regressão rodando (não começa com
    "parked/"), nunca o que libera. Detecção incerta não pode virar bypass.
    """
    try:
        proc = subprocess.run(
            ["git", "branch", "--show-current"], cwd=cwd,
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


TAIL_STDOUT_CHARS = 2000
TAIL_STDERR_CHARS = 1000


def _tail_file(fh, n):
    """Últimos `n` bytes de um arquivo binário aberto para leitura e
    escrita (um `tempfile.TemporaryFile`), lidos via `seek` — nunca o
    conteúdo inteiro. É metade da correção do C2: antes, `capture_output=
    True` bufferizava a saída COMPLETA do test_command em memória, embora
    só este rabo seja usado nas mensagens de bloqueio; sob teto de memória
    (container limitado, suíte verbosa), bufferizar a saída inteira podia
    levantar MemoryError sozinho, sem nenhum adversário envolvido.
    """
    fh.flush()
    fh.seek(0, os.SEEK_END)
    size = fh.tell()
    fh.seek(max(0, size - n))
    return fh.read().decode("utf-8", errors="replace")


class _SuiteTimeout(Exception):
    """A suíte estourou `test_timeout_seconds` e o grupo de processos foi morto."""


def _run_suite(cwd, test_command, timeout):
    """Roda o test_command e devolve (returncode, rabo do stdout, rabo do stderr).

    Compartilhado pelos dois guards que precisam do veredito real da suíte —
    o de regressão (antes do commit) e o de RED (antes da implementação) —
    para que ambos herdem as mesmas garantias de execução, em vez de uma
    segunda cópia deste subprocess divergir com o tempo:

    `start_new_session=True` faz de proc.pid o líder de um GRUPO DE PROCESSOS
    novo. Sem isto (o bug relatado), subprocess.run(timeout=...) mata só o
    processo do /bin/sh no TimeoutExpired — um filho que o test_command tenha
    backgroundeado (ou a própria suíte travada, se ela por sua vez tiver
    filhos) sobrevive como ÓRFÃO, continua rodando e consome recursos
    indefinidamente; tentativas repetidas empilhavam cópias da suíte travada.

    A saída vai para arquivos temporários binários (não `capture_output=True`,
    que bufferizaria TUDO em RAM) e só o rabo é lido de volta via `seek`.

    Levanta `_SuiteTimeout` no estouro de tempo; MemoryError/OSError sobem
    para quem chamou decidir a mensagem — nenhum dos dois vira "está tudo bem".
    """
    with tempfile.TemporaryFile() as out_fh, tempfile.TemporaryFile() as err_fh:
        proc = subprocess.Popen(
            test_command, shell=True, cwd=cwd,
            stdout=out_fh, stderr=err_fh, start_new_session=True,
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Mata o GRUPO INTEIRO (não só proc.pid) — killpg alcança
            # qualquer processo que o test_command tenha backgroundeado
            # dentro do mesmo grupo, não só o shell direto.
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass  # já morreu sozinho entre o timeout estourar e aqui
            proc.wait()  # reaproveita o processo (evita zumbi); já está morto, não bloqueia
            raise _SuiteTimeout()
        return (
            proc.returncode,
            _tail_file(out_fh, TAIL_STDOUT_CHARS),
            _tail_file(err_fh, TAIL_STDERR_CHARS),
        )


def guard_regression(tool_input, cwd, cfg):
    cmd = tool_input.get("command", "")
    if not isinstance(cmd, str) or not COMMIT_RE.search(cmd):
        return
    if "--dry-run" in cmd:
        return  # não altera o repo; não faz sentido rodar a suíte
    test_command = cfg.get("test_command")
    if not test_command:
        return  # sem suíte configurada, nem vale a pena checar a branch (evita subprocess git à toa)
    # Commit WIP de PBI estacionado: a suíte está vermelha POR DEFINIÇÃO
    # (trabalho incompleto), e este commit existe justamente para preservar
    # esse trabalho. Seguro porque parked/* nunca é branch de entrega — o
    # merge de volta passa pelo gate normal na branch principal.
    #
    # A isenção vale só para COMMIT: `git merge` nunca entra nela. A branch
    # lida aqui é a do momento do hook (PreToolUse roda ANTES do comando),
    # então um comando encadeado partindo da parked — `git checkout <main>
    # && git merge parked/NN` — seria lido como "estou em parked/*" e
    # puliria justamente o gate que deve validar a entrega na branch
    # principal.
    if not MERGE_RE.search(cmd) and current_branch(cwd).startswith("parked/"):
        return
    timeout = int(cfg.get("test_timeout_seconds", 600))

    # A REGRA (fixada pelo dono do produto, C2): fail-open vale para erro de
    # PARSING de estado (gate.json malformado, batch.json corrompido — ver
    # os wrappers `_*_seguro` acima) porque ali há um ARQUIVO para
    # interpretar e um default seguro óbvio. Aqui não há nada para
    # parsear: há uma verificação que precisa RODAR. "Não consegui
    # verificar" não é o mesmo veredito que "verifiquei e está tudo bem", e
    # só o segundo libera o commit — por isso qualquer falha em EXECUTAR o
    # test_command (estouro de memória, fork falhando, disco cheio) vira
    # BLOQUEIO, nunca liberação.
    #
    # Os `block()` ficam FORA do try de propósito: `block()` levanta
    # SystemExit, e um `except Exception` mais permissivo aqui embaixo (ou um
    # except que esquecesse de reerguer SystemExit) reproduziria o bug
    # relatado — MemoryError escapando até o `except Exception: sys.exit(0)`
    # de main() e liberando o commit com a suíte vermelha.
    try:
        returncode, tail_out, tail_err = _run_suite(cwd, test_command, timeout)
    except _SuiteTimeout:
        returncode, tail_out, tail_err = None, "", ""
    except (MemoryError, OSError) as exc:
        block(
            "[spec-gate] Gate de regressão: a suíte de testes NÃO PÔDE SER "
            f"EXECUTADA/AVALIADA ({exc.__class__.__name__}: {exc}). Commit "
            "bloqueado — não conseguir verificar não é o mesmo que verificar e "
            "estar tudo bem. Investigue o ambiente de execução (memória, disco, "
            "processo) antes de tentar de novo; este bloqueio não é por suíte "
            "vermelha, é por não ter sido possível rodá-la."
        )
        return
    if returncode is None:
        block(
            "[spec-gate] Gate de regressão: a suíte de testes excedeu o tempo "
            f"limite de {timeout}s. Commit bloqueado. Investigue antes de commitar."
        )
        return
    if returncode != 0:
        block(
            "[spec-gate] Gate de regressão FALHOU. Commit/merge bloqueado até a "
            f"suíte completa passar.\nComando: {test_command}\n"
            f"--- saída (final) ---\n{tail_out}\n{tail_err}\n"
            "Corrija as falhas ou, se estiver travado após várias tentativas, "
            "pare e reporte ao usuário em vez de insistir."
        )


def _interpreter_name(token):
    """Normaliza o nome do binário do interpretador.

    Aceita caminho completo (`/usr/bin/python3`) e versões coladas no nome
    (`python3.11`, `node18`) — o resto (perl, ruby, php, sh, bash, ...)
    já bate direto com a chave do dicionário de flags.
    """
    base = os.path.basename(token)
    return re.sub(r"^(python|node)[0-9.]*$", r"\1", base)


def _interpreter_inline_code(tokens):
    """Se `tokens` é uma invocação de interpretador com código inline
    (`python3 -c "..."`, `sh -c "..."`, `node -e "..."`, ...), devolve a
    string do código. Caso contrário, None.
    """
    if not tokens:
        return None
    flags = _INTERPRETER_INLINE_FLAGS.get(_interpreter_name(tokens[0]))
    if not flags:
        return None
    for i, t in enumerate(tokens[1:], start=1):
        if t in flags and i + 1 < len(tokens):
            return tokens[i + 1]
    return None


def _interpreter_heredoc_code(cmd, tokens):
    """Se `tokens` é uma invocação de interpretador com heredoc
    (`python3 <<EOF ... EOF`, `python3 <<'EOF' ... EOF`), devolve o corpo
    do heredoc. Caso contrário, None.

    Não reimplementamos o parser de heredoc do shell — não procuramos o
    delimitador de FECHAMENTO (a linha `EOF` final), então o "corpo"
    devolvido é tudo que vem depois do marcador de abertura, incluindo essa
    linha final. Isso é inofensivo para `_inline_write_candidates`: ela só
    extrai strings/tokens candidatos a caminho e ignora o resto do texto.
    """
    if not tokens:
        return None
    if _interpreter_name(tokens[0]) not in _INTERPRETER_INLINE_FLAGS:
        return None
    m = _HEREDOC_RE.search(cmd)
    if not m:
        return None
    return cmd[m.end():]


def _inline_write_candidates(code):
    """Candidatos a caminho dentro de um trecho de código-fonte arbitrário
    passado a um interpretador.

    Não interpretamos a linguagem — não há (e não vamos escrever) um parser
    de Python/JS/Perl/Ruby/PHP/shell aqui. Extraímos o conteúdo de toda
    string literal (aspas simples ou duplas), que é onde um caminho de
    arquivo aparece no uso idiomático de escrita (`open(...)`,
    `fs.writeFileSync(...)`, `File.write(...)`, etc.), e também separamos
    por espaço/pontuação para pegar o caso de `sh -c`/`bash -c`, cujo
    conteúdo é o próprio Bash e pode ter um caminho solto depois de `>`
    sem aspas nenhuma.
    """
    candidates = [m[1] for m in _QUOTED_STRING_RE.findall(code)]
    candidates.extend(t for t in re.split(r"[\s'\"();|&]+", code) if t)
    # Idioma clássico do Perl: open de 2 argumentos com o modo colado no
    # caminho (`open(F,'>arquivo')`, `'>>arquivo'` para apêndice). A string
    # extraída vem como '>arquivo'/'>>arquivo' e não bate por igualdade de
    # path contra o alvo real (`_same_file` compara caminho resolvido) — por
    # isso também oferecemos a versão sem o(s) '>' colado(s) como candidata.
    candidates.extend(
        c.lstrip(">") for c in list(candidates)
        if c.startswith(">") and c.lstrip(">")
    )
    return candidates


# MODELO DE CAMADAS — leia isto antes de "completar" a heurística abaixo ou
# de julgá-la insegura. As duas camadas têm naturezas diferentes, e é
# importante não confundir uma com a outra:
#
# 1) CAMADA DE FRICÇÃO — é esta função (write_targets) e todo o parsing de
#    comando Bash que ela faz, incluindo o reconhecimento de interpretador
#    logo acima. Ela encarece o desvio CASUAL: o caso real que ela cobre
#    é um modelo sob pressão racionalizando um atalho ("é só um `python -c`,
#    não é bem uma escrita de arquivo..."), não um adversário decidido a
#    burlar o guard de propósito. Por isso reconhecemos aqui invocação de
#    `python`/`python3 -c`, `node -e`/`--eval`, `perl -e`, `ruby -e`,
#    `php -r`, `sh -c`/`bash -c` — o desvio mais provável de todos por ser o
#    idioma natural da própria ferramenta Bash do agente —, além de
#    `dd of=`, `install` e heredoc de interpretador. Isto NÃO é sandbox e
#    não pretende ser: quem quiser burlar de propósito (eval, base64 | sh,
#    awk 'BEGIN{print > arquivo}', indireção por variável, substituição de
#    comando no alvo, escrever o caminho byte a byte, um segundo processo,
#    etc.) burla — inclusive o PRÓPRIO arquivo `.specgate/seq` só está
#    protegido por esta camada (ver `guard_seq_lock` abaixo: qualquer
#    escrita que `write_targets` reconheça é bloqueada, mas o que
#    `write_targets` NÃO reconhece passa). Não perseguimos esse alvo
#    infinito aqui.
#
# 2) A PROPRIEDADE GENUINAMENTE FORTE — não é nenhum arquivo de estado em
#    si (todos são protegidos só pela camada de fricção acima). É o EVENTO
#    `UserPromptSubmit`: o Claude não consegue fabricá-lo, e `.specgate/seq`
#    só avança dentro de `log_event.py`, rodando como HOOK nesse evento —
#    nunca como tool call do agente, então nunca passa por este guard
#    PreToolUse. `has_human_turn_since` (consumida por `guard_gate_clear`)
#    compara esse contador contra o valor registrado na abertura do gate.
#    Por isso o caminho HONESTO — pedir ao usuário e esperar a resposta
#    real — é sempre o de MENOR resistência: forjar exige Bash cada vez
#    mais contorcido (que a camada de fricção acima encarece a cada
#    rodada), enquanto esperar o turno real custa zero.
#
# Por isso "fechar o barato" (cobrir os interpretadores óbvios de escrita
# inline, mais dd/install/heredoc) é o nível de investimento certo para a
# camada de fricção — não vale a pena, e seria enganoso, tentar transformar
# isto num parser de verdade.
#
# CAMINHO FUTURO DESCARTADO (para quem for mexer aqui depois): validar a
# legitimidade da fase no PONTO DE CONSUMO (quem lê .specgate/phase) em vez
# de proteger o arquivo na escrita esbarra no modelo de estacionamento
# (parking): com um gate aberto do PBI-03 estacionado, o fluxo precisa
# continuar livre para avançar o PBI-04, então bloquear ferramentas de
# trabalho globalmente enquanto houver QUALQUER gate aberto quebraria a
# fila. A versão por-PBI disso exigiria rastrear qual arquivo pertence a
# qual PBI, que é o redesign caro. Se um dia for necessário reduzir ainda
# mais esta fricção (não uma parede impossível de furar — só mais cara de
# contornar), o caminho é esse rastreamento — não mais parsing de comando.
@functools.lru_cache(maxsize=16)
def _write_targets_bash_cached(cmd):
    """Núcleo cacheado de write_targets para Bash (I5, achado da revisão
    final): dentro de UM processo do guard, até 6 guards diferentes (seq,
    batch, phase, gate.json, spec lock, fase de testes) chamam
    write_targets com o MESMO comando. Sem cache, shlex.split e a extração
    de candidatos de código inline (que varrem o comando INTEIRO, heredoc
    incluso) rodavam do zero a cada guard — um heredoc de milhares de
    linhas virava milhares de tokens reprocessados 6 vezes.
    """
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        tokens = cmd.split()
    if not tokens:
        return ()

    targets = []

    # Código inline de interpretador (`python3 -c "..."`, `sh -c "..."`,
    # heredoc): o alvo da escrita está DENTRO do código passado, não nos
    # tokens do shell. Isto SOMA aos candidatos, nunca substitui a varredura
    # de tokens abaixo — `python3 -c "print('x')" > .specgate/phase` tem a
    # escrita no REDIRECIONAMENTO do shell, fora do código inline; devolver
    # só os candidatos inline deixaria esse alvo invisível para todos os
    # guards (fase, seq, gate.json, batch.json e spec congelada).
    inline = _interpreter_inline_code(tokens)
    if inline is None:
        inline = _interpreter_heredoc_code(cmd, tokens)
    if inline is not None:
        targets.extend(
            c for c in _inline_write_candidates(inline) if c and not c.startswith("-")
        )

    if any(rx.search(cmd) for rx in BASH_WRITE_RES):
        targets.extend(t for t in tokens[1:] if not t.startswith("-"))
    if not targets:
        return ()
    # `dd of=arquivo` (e `if=arquivo`) colam o caminho depois do `=` num
    # único token — ele nunca aparece sozinho na lista acima. Oferecemos
    # também o valor de qualquer token `chave=valor` como candidato extra,
    # sem remover o token original (preferimos o falso positivo).
    for t in list(targets):
        _, eq, val = t.partition("=")
        if eq and val:
            targets.append(val)
    return tuple(targets)


def write_targets(tool, tool_input):
    """Caminhos que esta chamada pretende escrever.

    Usado pelos guards que protegem arquivos de estado. Para Bash, devolve
    todos os tokens não-flag quando o comando tem cara de escrita — é
    grosseiro de propósito: preferimos um falso positivo (que o agente
    contorna explicando ao PO) a um falso negativo que fura o gate. O
    parsing de verdade (`_write_targets_bash_cached`) é cacheado por
    comando (I5) — ver docstring dele.
    """
    if tool in ("Write", "Edit"):
        c = tool_input.get("file_path") or tool_input.get("path")
        return [c] if isinstance(c, str) else []
    if tool != "Bash":
        return []
    cmd = tool_input.get("command", "")
    if not isinstance(cmd, str) or not cmd:
        return []
    return list(_write_targets_bash_cached(cmd))


def _same_file(candidate, cwd, rel):
    if not candidate:
        return False
    return os.path.realpath(os.path.join(cwd, os.path.expanduser(candidate))) == \
        os.path.realpath(os.path.join(cwd, rel))


# I5 (achado da revisão final): acima deste tamanho, os 4 guards de estado
# (seq/batch/phase/gate.json) não reparseiam o comando Bash por token — um
# heredoc de dezenas de milhares de linhas (comando comum e legítimo, tipo
# `cat > arquivo <<'EOF' ... EOF`) virava dezenas de milhares de candidatos,
# cada um com até 2 os.path.realpath, MULTIPLICADO por guard (cada um dos 4
# refazia esse trabalho para o MESMO comando). Acima do limite o comando é
# tratado como NÃO VERIFICÁVEL — ver `_bloqueia_se_grande_demais` para o
# lado seguro escolhido.
BASH_CMD_MAX_VERIFIABLE_SIZE = 64 * 1024


@functools.lru_cache(maxsize=16)
def _resolved_bash_targets(cmd, cwd):
    """Candidatos de escrita de um comando Bash, já resolvidos (realpath),
    calculados NO MÁXIMO uma vez por processo para o mesmo (cmd, cwd) —
    compartilhados pelos 4 guards de estado. Antes deste fix, cada guard
    chamava write_targets() de novo E resolvia (realpath) CADA candidato de
    novo (2 realpaths: candidato e alvo), então um comando com milhares de
    tokens virava milhares de realpaths × 2 × 4 guards.
    """
    return frozenset(
        os.path.realpath(os.path.join(cwd, os.path.expanduser(c)))
        for c in _write_targets_bash_cached(cmd)
    )


def _touches_state_file(tool, tool_input, cwd, rel):
    """True/False se dá para verificar se esta chamada escreve em `rel` (um
    dos 4 arquivos de estado: phase, gate.json, seq, batch.json); None se o
    comando Bash é grande demais para valer a pena parsear (ver
    BASH_CMD_TAMANHO_MAXIMO_VERIFICAVEL) — quem chama decide o lado seguro.
    """
    if tool == "Bash":
        cmd = tool_input.get("command", "")
        if not isinstance(cmd, str) or not cmd:
            return False
        if len(cmd) > BASH_CMD_MAX_VERIFIABLE_SIZE:
            return None
        alvo = os.path.realpath(os.path.join(cwd, rel))
        return alvo in _resolved_bash_targets(cmd, cwd)
    for t in write_targets(tool, tool_input):
        if _same_file(t, cwd, rel):
            return True
    return False


def _block_if_too_large(cwd, file_name):
    """Chamado por `guard_po_gate` e `guard_gate_clear` quando
    `_toca_arquivo_de_estado` devolve None (comando grande demais para
    parsear com segurança, I5).

    ACHADO DA REVISÃO: este fallback ("bloqueia se há gate aberto, libera
    se não há") era aplicado de forma UNIFORME aos 4 guards de estado
    (seq/batch/phase/gate.json), mas só é a condição normal de bloqueio
    DESTES DOIS — `guard_seq_lock` e `guard_batch_lock` têm fallback
    próprio (`_bloqueia_seq_grande_demais` e `_bloqueia_batch_grande_demais`
    logo abaixo), porque a condição normal de bloqueio deles não depende de
    gate algum. Ver a tabela completa nos docstrings de cada guard_*_lock.

    Regra do dono do produto: não conseguir verificar não é o mesmo que
    verificar e estar tudo bem. Mas aqui não há uma verificação FALHANDO
    (como no gate de regressão) — há uma decisão de CUSTO: parsear um
    comando de dezenas/centenas de KB por token, em cada guard, era
    exatamente a regressão de latência que este fix corrige. O lado seguro
    condicionado: bloqueia se existir QUALQUER gate aberto (há algo em jogo
    agora que uma escrita não inspecionada poderia comprometer); libera se
    não houver nenhum gate aberto (nada para proteger agora, e um comando
    grande é, de longe, mais provável de ser um heredoc legítimo do que uma
    tentativa de burlar o guard por meio dele).
    """
    if not _safe_open_gates(cwd):
        return
    block(
        f"[spec-gate] COMANDO GRANDE DEMAIS PARA VERIFICAR BLOQUEADO ({file_name}). "
        "Este comando Bash passa de 64KB, tamanho acima do qual o guard não "
        "reparseia o comando inteiro por token (é a regressão de latência "
        "que este fix corrige) — então não há como confirmar que ele não "
        f"escreve em {file_name}, e existe gate aberto aguardando o PO "
        "agora. Não conseguir verificar não é o mesmo que verificar e estar "
        "tudo bem: quebre a operação em comandos menores, ou escreva por um "
        "caminho que não precise de um comando Bash gigante."
    )


def _block_seq_if_too_large(cwd, tool_input):
    """Fallback de `guard_seq_lock` quando o comando Bash é grande demais
    para reparsear por token (I5, ver BASH_CMD_TAMANHO_MAXIMO_VERIFICAVEL).

    Ao contrário de `_bloqueia_se_grande_demais` (guard_po_gate e
    guard_gate_clear, cuja condição normal já depende de gate aberto), a
    condição normal de `guard_seq_lock` NÃO depende de gate nenhum: ele
    bloqueia SEMPRE que o alvo é .specgate/seq (ver docstring dele). "Não
    conseguir verificar não é o mesmo que verificar e estar tudo bem"
    aplicado aqui significa bloquear sempre que o comando grande ainda tem
    alguma chance de mirar .specgate/seq — não bloquear todo comando
    grande, relacionado ou não, o que seria fricção desproporcional e uma
    regra diferente da que o dono do produto pediu.

    Pré-checagem O(n) barata: uma busca de substring no comando BRUTO, sem
    tokenizar. Se ".specgate/seq" nem aparece em lugar nenhum do texto,
    nenhum dos mecanismos de escrita que `write_targets` reconhece
    (redirecionamento, tee, sed -i, interpretador inline, heredoc, dd,
    install) poderia estar mirando nele — e o custo pago é o de uma busca
    de substring em Python (implementação em C, análoga a memmem), não o
    de tokenizar e resolver realpath por candidato, que é a regressão de
    latência que este fix corrige. A substring aparecer não PROVA escrita
    de verdade (poderia ser comentário, leitura, parte de uma string maior)
    — mas como o lado seguro deste guard já é bloquear incondicionalmente
    quando o alvo É .specgate/seq, a presença já basta para decidir.
    """
    cmd = tool_input.get("command", "")
    if not isinstance(cmd, str) or SEQ_REL not in cmd:
        return
    block(
        "[spec-gate] COMANDO GRANDE DEMAIS PARA VERIFICAR BLOQUEADO (.specgate/seq). "
        "Este comando Bash passa de 64KB e menciona .specgate/seq — tamanho "
        "acima do qual o guard não reparseia o comando inteiro por token (é "
        "a regressão de latência que este fix corrige), então não há como "
        "confirmar que ele não escreve no contador de turnos. A condição "
        "normal deste guard já bloqueia QUALQUER escrita reconhecida em "
        ".specgate/seq, com ou sem gate aberto — não conseguir verificar "
        "não muda esse lado seguro. Quebre a operação em comandos menores, "
        "ou escreva por um caminho que não precise de um comando Bash "
        "gigante."
    )


def _block_batch_if_too_large(cwd, tool_input):
    """Fallback de `guard_batch_lock` quando o comando Bash é grande demais
    para reparsear por token (I5).

    A condição normal de `guard_batch_lock` só bloqueia quando o disco JÁ
    tem backlog_aprovado: true (congelamento ligado) — sem isso não há
    nada para proteger, gate aberto ou não (ver docstring dele). O fallback
    seguro espelha exatamente essa condição, em vez do fallback genérico de
    `_bloqueia_se_grande_demais`: usar aquele aqui bloquearia comando
    grande legítimo sempre que existisse QUALQUER gate aberto no lote,
    mesmo sem nada em jogo em batch.json, e LIBERARIA justamente o caso que
    importa (backlog_aprovado true, nenhum gate de PO aberto no momento —
    o caso comum durante o fluxo normal do lote), que é exatamente o
    bypass relatado.

    Mesma pré-checagem O(n) barata do seq (substring literal no comando
    bruto, sem tokenizar): sem ".specgate/batch.json" em lugar nenhum do
    texto, nenhum mecanismo de escrita reconhecido por `write_targets`
    poderia estar mirando nele, e um heredoc grande alheio ao spec-gate
    (ex.: escrevendo um arquivo de código legítimo) continua passando
    mesmo com backlog_aprovado true no disco — sem esta pré-checagem, TODO
    Bash grande seria bloqueado nesse caso, fricção desproporcional ao
    risco.
    """
    cmd = tool_input.get("command", "")
    if not isinstance(cmd, str) or BATCH_REL not in cmd:
        return
    if not _safe_gate_po_1_passed(cwd):
        return  # congelamento ainda não ligou: nada aqui para proteger
    block(
        "[spec-gate] COMANDO GRANDE DEMAIS PARA VERIFICAR BLOQUEADO (.specgate/batch.json). "
        "Este comando Bash passa de 64KB e menciona .specgate/batch.json — "
        "tamanho acima do qual o guard não reparseia o comando inteiro por "
        "token (é a regressão de latência que este fix corrige), então não "
        "há como confirmar que ele preserva backlog_aprovado: true, que já "
        "está ligado no disco. Não conseguir verificar não é o mesmo que "
        "verificar e estar tudo bem: quebre a operação em comandos "
        "menores, ou escreva por um caminho que não precise de um comando "
        "Bash gigante."
    )


def _safe_open_gates(cwd):
    """Wrapper fail-open sobre specgate_state.open_gates.

    Protege o ponto de USO, não só o import do topo: com o módulo ausente
    (import falhou -> None) a chamada seria AttributeError em NoneType; com
    o módulo presente mas desatualizado/parcial (sem open_gates), seria
    AttributeError no atributo. Nos dois casos um guard bloqueante não pode
    quebrar — assume-se "sem gates abertos" e a ação segue liberada.
    """
    if specgate_state is None:
        return []
    try:
        return specgate_state.open_gates(cwd)
    except Exception:
        return []


def _safe_read_gates(cwd):
    """Wrapper fail-open sobre specgate_state.read_gates.

    Mesmo raciocínio de `_open_gates_seguro`: usado para saber se uma chave
    já existia no estado anterior COMPLETO (aberta ou já decidida), não só
    nas abertas. Módulo ausente ou quebrado -> "nenhum gate anterior", que é
    o lado permissivo (fail-open); a checagem de decisão sem turno humano
    continua valendo pelos outros caminhos.
    """
    if specgate_state is None:
        return []
    try:
        return specgate_state.read_gates(cwd)
    except Exception:
        return []


def _safe_current_gates(cwd):
    """Wrapper fail-open sobre specgate_state.gates_vigentes.

    Mesmo raciocínio de `_open_gates_seguro`: módulo ausente ou quebrado ->
    lista vazia, que é o lado permissivo para quem usa isto (a janela de
    retomada da spec, `_janela_retomada_spec` abaixo) — sem saber qual gate
    é o vigente, a janela simplesmente não abre, e o congelamento normal de
    docs/backlog/ continua valendo.
    """
    if specgate_state is None:
        return []
    try:
        return specgate_state.current_gates(cwd)
    except Exception:
        return []


def _safe_gate_po_1_passed(cwd):
    """Wrapper fail-open sobre specgate_state.gate_po_1_passed.

    Mesmo raciocínio de `_open_gates_seguro`/`_has_human_turn_seguro`:
    módulo ausente (import falhou -> None) ou presente mas desatualizado/
    parcial (sem gate_po_1_passed) não pode derrubar um hook bloqueante nem
    interromper os guards seguintes em main() (destrutivo, regressão).
    False é o lado seguro aqui: freeze desligado, nunca trava trabalho
    legítimo do spec-analyst nas Fases 0/1.
    """
    if specgate_state is None:
        return False
    try:
        return specgate_state.gate_po_1_passed(cwd)
    except Exception:
        return False


def _safe_state_call(default, fn_name, *args):
    """Wrapper fail-open genérico sobre uma função de `specgate_state`.

    Mesmo raciocínio dos wrappers acima (módulo ausente porque o import
    falhou, ou presente mas desatualizado/parcial): nenhum problema de estado
    pode derrubar um hook bloqueante. Os mecanismos de RED e de tentativas
    nasceram depois destes wrappers e usam este caminho genérico em vez de
    somar mais cinco funções `_safe_*` idênticas — o default seguro vem de
    quem chama, porque ele muda por chamada (False para "já provado", 0 para
    contadores).
    """
    if specgate_state is None:
        return default
    try:
        return getattr(specgate_state, fn_name)(*args)
    except Exception:
        return default


# Intenção de transição para a fase de implementação, extraída do conteúdo
# que a chamada pretende gravar em .specgate/phase. O grupo opcional captura
# o PBI do formato `implementing:docs/backlog/02-nome.md` (ver `parse_phase`).
_IMPLEMENTING_INTENT_RE = re.compile(r"\bimplementing\b(?::([^\s'\"]+))?")


def _intended_implementing_pbi(tool, tool_input):
    """PBI da transição para `implementing` que esta chamada pretende gravar —
    ou None se ela não é uma transição para implementação.

    "" (string vazia) é um retorno DIFERENTE de None: significa "é uma
    transição para implementing, mas sem PBI declarado no formato novo".

    LIMITE HONESTO, na mesma linha do resto do arquivo: para Write e Edit o
    conteúdo pretendido está no payload e a leitura é exata. Para Bash não
    existe conteúdo a inspecionar sem interpretar o shell de verdade, então a
    busca acontece sobre o COMANDO INTEIRO — o que produz falso positivo
    (`printf '' > .specgate/phase && echo implementing`) e escapa de quem
    esconde a string (variável, base64, montagem por concatenação). Falso
    positivo custa uma execução da suíte e uma mensagem; o escape é o mesmo
    que vale para todos os guards por parsing: isto é fricção, não sandbox.
    """
    if tool == "Write":
        raw = tool_input.get("content")
    elif tool == "Edit":
        raw = tool_input.get("new_string")
    elif tool == "Bash":
        raw = tool_input.get("command")
    else:
        return None
    if not isinstance(raw, str):
        return None
    m = _IMPLEMENTING_INTENT_RE.search(raw)
    if not m:
        return None
    return m.group(1) or ""


def _red_waived_by_po(cwd, pbi):
    """O PO já decidiu que o verde deste PBI é legítimo?

    Existe falso-verde legítimo: PBI de refactor, de documentação, ou cujo
    comportamento já estava implementado. Por isso o gate de RED não é uma
    parede — é uma pergunta ao PO, e a resposta dele fica registrada como
    qualquer outra decisão de gate ('red' aprovado). A aprovação já passou
    por `guard_gate_clear` (que exige turno humano posterior à abertura), então
    aqui basta ler o veredito: não há checagem nova a burlar.

    `pbi` vazio é a fase no formato antigo (`implementing` sem sufixo), que a
    prova de RED continua verificando: sem PBI não há como casar o gate com a
    transição, e exigir esse casamento aqui deixaria o formato antigo com um
    bloqueio SEM saída nenhuma — `_same_file` é sempre falso para "", então o
    PO não conseguiria dispensar o verde por gate nenhum. Nesse caso qualquer
    gate de RED aprovado serve: é a mesma imprecisão do próprio formato antigo,
    não uma exceção nova.
    """
    for g in _safe_current_gates(cwd):
        if g.get("checkpoint") != "red" or g.get("status") != "aprovado":
            continue
        if not pbi:
            return True
        gate_pbi = g.get("pbi")
        if isinstance(gate_pbi, str) and gate_pbi and _same_file(pbi, cwd, gate_pbi):
            return True
    return False


def guard_red_evidence(tool, tool_input, cwd, cfg):
    """Prova de RED: a suíte precisa estar VERMELHA antes de implementar.

    O gate de regressão já provava o verde mecanicamente antes do commit; o
    vermelho, do outro lado do ciclo, era autodeclarado — `blackbox-tester`
    rodava a suíte só para confirmar que os testes eram executáveis. Sem esta
    prova, um teste tautológico (`assert resultado is not None`) entra verde
    desde o dia zero, o `implementer` encontra a suíte passando e "termina"
    sem escrever nada, e o único olho capaz de pegar isso é o revisor de
    conformidade — passada única, contra o código que deveria existir.

    LIMITE HONESTO: o vermelho é provado em nível de PBI (exit code da suíte),
    não requisito por requisito. Parsear nomes de teste de um runner
    arbitrário (pytest, jest, go test, cargo) seria frágil demais para virar
    guard; a cobertura por requisito é declarada e verificada por outro
    caminho, não por execução.

    A prova vale por RODADA do gate de testes (ver `round_for`): enquanto os
    testes aprovados forem os mesmos, o guard não roda a suíte de novo. Isso
    não é só economia — sem esse cache, toda reentrada em implementação
    (depois de uma conformidade reprovada, por exemplo) encontraria a suíte
    verde, porque o código já existe, e bloquearia o fluxo pedindo um
    vermelho que não pode mais existir.
    """
    if cfg.get("require_red") is False:
        return
    test_command = cfg.get("test_command")
    if not test_command:
        return  # sem suíte configurada não há vermelho a provar
    touches = _touches_state_file(tool, tool_input, cwd, PHASE_REL)
    if touches is None:
        _block_red_if_too_large(cwd, tool_input)
        return
    if not touches:
        return
    pbi = _intended_implementing_pbi(tool, tool_input)
    if pbi is None:
        return
    round_ = _safe_state_call(0, "round_for", cwd, pbi, "testes")
    if _safe_state_call(False, "red_proven", cwd, pbi, round_):
        return
    if _red_waived_by_po(cwd, pbi):
        _safe_state_call(False, "record_red", cwd, pbi, round_, None, True)
        return

    timeout = int(cfg.get("test_timeout_seconds", 600))
    # Mesma regra do gate de regressão, e pela mesma razão: aqui não há um
    # arquivo a interpretar com default seguro óbvio, há uma verificação que
    # precisa RODAR. Não conseguir rodá-la não é o mesmo veredito que
    # rodá-la e encontrar vermelho — os dois casos abaixo bloqueiam.
    try:
        returncode, _tail_out, _tail_err = _run_suite(cwd, test_command, timeout)
    except _SuiteTimeout:
        returncode = None
    except (MemoryError, OSError) as exc:
        block(
            "[spec-gate] Prova de RED: a suíte de testes NÃO PÔDE SER "
            f"EXECUTADA/AVALIADA ({exc.__class__.__name__}: {exc}). Transição "
            "para a implementação bloqueada — não conseguir verificar não é o "
            "mesmo que verificar e estar tudo bem. Investigue o ambiente de "
            "execução (memória, disco, processo) antes de tentar de novo."
        )
        return
    if returncode is None:
        block(
            "[spec-gate] Prova de RED: a suíte de testes excedeu o tempo limite "
            f"de {timeout}s antes da implementação começar. Transição bloqueada. "
            "Ajuste test_timeout_seconds ou aponte test_command para um "
            "subconjunto que termine."
        )
        return
    if returncode != 0:
        _safe_state_call(False, "record_red", cwd, pbi, round_, returncode, False)
        return
    block(
        "[spec-gate] PROVA DE RED FALHOU: a suíte JÁ PASSA INTEIRA antes de "
        f"implementar {pbi or 'este PBI'}. Testes que passam sem a "
        "implementação não capturam o comportamento da spec — ou são "
        "tautológicos (asserção vazia, 'não lança erro', valor apenas "
        "truthy), ou o comportamento já existe no código.\n"
        "Isto NÃO é uma decisão sua: abra o gate de RED e pergunte ao PO, em "
        "uma linha, se o comportamento já existe ou se os testes precisam "
        "ser refeitos:\n"
        '  {"checkpoint": "red", "pbi": "<caminho da spec>", "rodada": 1, '
        '"status": "aguardando-po", "opened_at_seq": <seq atual>, '
        '"questions": ["A suíte já passa sem implementação — o comportamento '
        'já existe, ou os testes não capturam a spec?"]}\n'
        "Aprovado o gate, esta transição libera sozinha. Para desligar a "
        'prova de RED no projeto inteiro: "require_red": false no .specgate.json.'
    )


# Identidade de um requisito dentro da spec: [C1] em Comportamentos, [E1] em
# Casos de erro. IDs são estáveis — nunca renumerados, nunca reusados —, que é
# o que permite uma referência (num teste, numa divergência do revisor, num
# commit) continuar apontando para o mesmo requisito depois que a spec muda.
_REQUIREMENT_ID_RE = re.compile(r"\[([CE]\d+)\]")
DEFAULT_TRACE_REL = os.path.join("docs", "traceability.json")
MAX_TEST_FILE_SIZE = 2 * 1024 * 1024


def _spec_requirement_ids(cwd, pbi):
    """IDs de requisito declarados na spec deste PBI, em ordem de aparição.

    Lista vazia significa spec no formato antigo (sem IDs) — e é o que mantém
    `guard_trace` inerte em projetos que ainda não migraram: a rastreabilidade
    passa a ser exigida quando a spec passa a declarar requisitos, não por uma
    data de corte.
    """
    try:
        with open(os.path.join(cwd, pbi), "r", encoding="utf-8") as fh:
            texto = fh.read()
    except OSError:
        return []
    vistos, ids = set(), []
    for m in _REQUIREMENT_ID_RE.finditer(texto):
        if m.group(1) not in vistos:
            vistos.add(m.group(1))
            ids.append(m.group(1))
    return ids


def _trace_key_prefix(pbi):
    """`docs/backlog/02-conversao.md` -> `02-conversao` (prefixo da chave)."""
    base = os.path.basename(pbi)
    return base[:-3] if base.endswith(".md") else base


def _load_trace(cwd, cfg):
    rel = cfg.get("traceability_path") or DEFAULT_TRACE_REL
    try:
        with open(os.path.join(cwd, rel), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return rel, {}
    return rel, data if isinstance(data, dict) else {}


def _test_reference_missing(cwd, ref):
    """Motivo pelo qual esta referência `arquivo::nome` não confere — ou None.

    Verificação TEXTUAL, de propósito: confirma que o arquivo existe e que o
    nome do teste aparece nele. Não executa nada e não entende a sintaxe de
    nenhum runner — o que pega é a referência inventada ou apodrecida (teste
    renomeado, arquivo movido), que é o modo de falha real de uma matriz de
    rastreabilidade escrita à mão.
    """
    if not isinstance(ref, str) or not ref.strip():
        return "referência vazia"
    path, sep, name = ref.partition("::")
    full = os.path.join(cwd, path)
    if not os.path.isfile(full):
        return f"arquivo não existe: {path}"
    if not sep or not name.strip():
        return None  # referência a arquivo inteiro: existir já basta
    try:
        if os.path.getsize(full) > MAX_TEST_FILE_SIZE:
            return None  # grande demais para valer a busca; não inventa bloqueio
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            if name.strip() not in fh.read():
                return f"teste não encontrado em {path}: {name.strip()}"
    except OSError as exc:
        return f"não foi possível ler {path}: {exc}"
    return None


def guard_trace(tool, tool_input, cwd, cfg):
    """Rastreabilidade: todo requisito da spec precisa dizer quem o cobre.

    O `blackbox-tester` sempre devolveu um "mapa de cobertura" no relatório —
    prosa, consumida uma vez pelo orquestrador e perdida com a sessão. Aqui o
    mesmo mapa vira artefato versionado (`docs/traceability.json`), verificado
    na transição para a implementação: cada `[C1]`/`[E1]` da spec precisa de
    uma entrada, e cada teste citado precisa existir de verdade.

    Requisito SEM cobertura não é bloqueado — é bloqueado o SILÊNCIO sobre
    ele. Uma entrada `{"tests": [], "status": "uncovered", "why": "..."}` passa:
    a decisão de deixar um requisito sem teste é legítima e do PO, desde que
    esteja escrita em algum lugar que sobreviva à sessão.
    """
    if cfg.get("require_trace") is False:
        return
    touches = _touches_state_file(tool, tool_input, cwd, PHASE_REL)
    # None (comando Bash grande demais para parsear) cai aqui junto com False.
    # Não há fallback próprio porque, sem conseguir extrair o PBI, também não
    # dá para saber se a spec dele declara IDs — e bloquear às cegas tornaria
    # este guard NÃO INERTE em projetos de spec no formato antigo, que é a
    # compatibilidade que ele promete. Na configuração padrão o caso já é
    # coberto por `guard_red_evidence`, que bloqueia comando grande mirando a
    # transição; com `require_red: false` ou sem `test_command`, esta é uma
    # fresta conhecida da camada de fricção, não uma parede.
    if not touches:
        return
    pbi = _intended_implementing_pbi(tool, tool_input)
    if not pbi:
        return  # sem PBI declarado não há spec para parsear
    ids = _spec_requirement_ids(cwd, pbi)
    if not ids:
        return  # spec sem IDs: formato antigo, guard inerte

    rel, trace = _load_trace(cwd, cfg)
    prefixo = _trace_key_prefix(pbi)
    faltando, quebradas = [], []
    for rid in ids:
        chave = f"{prefixo}#{rid}"
        entrada = trace.get(chave)
        if not isinstance(entrada, dict) or "tests" not in entrada:
            faltando.append(chave)
            continue
        tests = entrada.get("tests")
        if not isinstance(tests, list):
            faltando.append(chave)
            continue
        for ref in tests:
            motivo = _test_reference_missing(cwd, ref)
            if motivo:
                quebradas.append(f"{chave}: {motivo}")

    if not faltando and not quebradas:
        return
    partes = [f"[spec-gate] RASTREABILIDADE INCOMPLETA em {rel} para {pbi}."]
    if faltando:
        partes.append(
            "Requisitos da spec sem entrada na matriz: " + ", ".join(faltando) + "."
        )
    if quebradas:
        partes.append("Entradas apontando para testes que não existem: " + "; ".join(quebradas) + ".")
    partes.append(
        "Cada requisito precisa de uma entrada no formato "
        '{"<pbi>#<id>": {"tests": ["arquivo::nome_do_teste"], "status": "covered"}}. '
        "Requisito que você decidiu deixar sem teste também precisa de entrada — "
        '{"tests": [], "status": "uncovered", "why": "<motivo>"} —, porque o que este '
        "gate proíbe é o silêncio sobre o requisito, não a ausência de cobertura. "
        'Para desligar no projeto inteiro: "require_trace": false no .specgate.json.'
    )
    block("\n".join(partes))


def _block_red_if_too_large(cwd, tool_input):
    """Fallback de `guard_red_evidence` quando o comando Bash passa de 64KB
    (I5, ver BASH_CMD_MAX_VERIFIABLE_SIZE).

    Mesma pré-checagem O(n) barata do seq e do batch — substring literal no
    comando bruto, sem tokenizar. Sem ".specgate/phase" E "implementing" no
    texto, nenhum mecanismo de escrita reconhecido por `write_targets` estaria
    mirando uma transição para implementação, e um heredoc gigante alheio ao
    spec-gate continua passando. Com os dois presentes, o lado seguro é o
    mesmo do guard: a verificação não pôde rodar, então não libera.
    """
    cmd = tool_input.get("command", "")
    if not isinstance(cmd, str) or PHASE_REL not in cmd or "implementing" not in cmd:
        return
    block(
        "[spec-gate] COMANDO GRANDE DEMAIS PARA VERIFICAR BLOQUEADO (prova de RED). "
        "Este comando Bash passa de 64KB e menciona .specgate/phase e "
        "'implementing' — tamanho acima do qual o guard não reparseia o "
        "comando inteiro por token, então não há como confirmar se ele inicia "
        "a fase de implementação sem a suíte ter sido provada vermelha. "
        "Escreva a fase com Write, que é inspecionável, em vez de um comando "
        "Bash gigante."
    )


# Tokens que só embrulham o runner de verdade: `python3 -m pytest`, `npm run
# test`, `uv run pytest`. Pular estes é o que faz o token significativo do
# `test_command` ser "pytest" e não "python3" — que casaria com qualquer
# script Python rodado durante a implementação e inflaria o contador.
_RUNNER_WRAPPERS = {
    "python", "python3", "py", "uv", "uvx", "poetry", "pipenv", "pdm", "hatch",
    "npm", "npx", "yarn", "pnpm", "bun", "deno", "bundle", "rake", "make",
    "run", "exec", "-m",
}
# Tokens significativos genéricos demais para casar sozinhos: "npm test" e
# "make check" deixariam só "test"/"check", que aparecem em comandos alheios
# (`ls test`, `./check.sh`). Nesses casos exigimos o test_command inteiro
# como substring — mais restrito, mas sem inflar o contador contra o PBI.
_GENERIC_RUNNER_TOKENS = {"test", "tests", "check", "verify", "ci", "all"}


@functools.lru_cache(maxsize=8)
def _runner_token(test_command):
    """Token que identifica o runner dentro do test_command ("" se não há)."""
    try:
        tokens = shlex.split(test_command, posix=True)
    except ValueError:
        tokens = test_command.split()
    for t in tokens:
        if t.startswith("-"):
            continue
        base = os.path.basename(t)
        if base in _RUNNER_WRAPPERS:
            continue
        return base
    return ""


def _invokes_test_runner(cmd, test_command):
    """Este comando Bash é uma execução da suíte?

    Reconhecer isto é o que torna o teto de tentativas MECÂNICO: quem conta é
    o hook, no mesmo espírito do contador de turnos — o agente não declara
    quantas rodadas gastou, ele gasta e o guard vê.
    """
    if not isinstance(cmd, str) or not cmd:
        return False
    if test_command in cmd:
        return True
    token = _runner_token(test_command)
    if not token or token in _GENERIC_RUNNER_TOKENS:
        return False
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        tokens = cmd.split()
    return any(os.path.basename(t) == token for t in tokens)


def guard_attempts(tool, tool_input, cwd, cfg, pbi):
    """Teto de tentativas de correção, contado pelo hook e não pelo agente.

    `max_fix_attempts` existia só como texto no prompt do `implementer` — a
    regra "término mecânico, nunca autodeclarado" valia para o verde (o gate
    de regressão roda a suíte de verdade) e não valia para o teto. Aqui o
    contador vive em `.specgate/attempts.json`, é incrementado por este guard
    a cada execução da suíte durante a implementação, e o estouro bloqueia
    novas edições de código-fonte — o agente para e reporta, que é o
    comportamento que o prompt pedia e não conseguia impor.

    Rodar a suíte NUNCA é bloqueado, nem depois do teto: é assim que o estado
    real (quais testes ainda falham) aparece no relatório ao PO.

    RESSALVA: PreToolUse roda ANTES do comando, então a contagem é levemente
    otimista — uma execução que sequer chega a iniciar (typo no comando, dep
    faltando) já contou. A válvula é o PO: qualquer gate deste PBI ganhando
    rodada nova zera o contador, e abrir rodada nova exige turno humano real.
    """
    if not pbi:
        return  # fase no formato antigo, sem PBI: não há contra o que contar
    try:
        max_attempts = int(cfg.get("max_fix_attempts", 5))
    except (TypeError, ValueError):
        max_attempts = 5
    if max_attempts <= 0:
        return  # teto desligado no projeto
    test_command = cfg.get("test_command")
    if not test_command:
        return

    round_ = _safe_state_call(0, "max_round_for_pbi", cwd, pbi)
    if tool == "Bash" and _invokes_test_runner(tool_input.get("command"), test_command):
        agora = _safe_state_call(0, "bump_attempt", cwd, pbi, round_, tool_input.get("command"))
        # Metade do teto é onde trocar de estratégia ainda é barato. Daqui em
        # diante as tentativas acontecem no contexto mais poluído da sessão —
        # justamente quando um par de olhos novos ajuda mais, e não menos. O
        # aviso sai UMA vez, no cruzamento; repetir a cada execução viraria
        # ruído que se aprende a ignorar.
        if agora == max(1, (max_attempts + 1) // 2):
            notice(
                f"[spec-gate] Tentativa {agora}/{max_attempts} em {pbi}. Daqui em "
                "diante, insistir no MESMO contexto rende cada vez menos: encerre "
                "este implementer e delegue a um novo, em contexto limpo, passando "
                "só o diagnóstico (quais testes falham, o que já foi tentado, a "
                "hipótese do bloqueio) — não o histórico inteiro. Trocar de olhos "
                "antes do teto é mais barato que declarar o PBI failed depois dele."
            )
        return

    used = _safe_state_call(0, "attempt_count", cwd, pbi, round_)
    if used < max_attempts:
        return
    source_dirs = norm_paths(cwd, cfg.get("source_paths", ["src"]))
    if not source_dirs:
        return
    for t in write_targets(tool, tool_input):
        if not touches_source(t, cwd, source_dirs):
            continue
        block(
            f"[spec-gate] TETO DE TENTATIVAS ATINGIDO ({used}/{max_attempts}) em "
            f"{pbi}. Edição de código-fonte bloqueada ({t}).\n"
            "O teto existe porque insistir além dele produz mudanças cada vez "
            "menos informadas, no contexto mais poluído da sessão. PARE e "
            "reporte ao PO: quais testes ainda falham, o que você tentou, e "
            "sua hipótese do bloqueio — específica o bastante para decisão sem "
            "investigação extra.\n"
            "Rodar a suíte continua liberado (é assim que o estado real entra "
            "no relatório). O contador zera quando o PO abrir rodada nova de "
            "um gate deste PBI; não tente contorná-lo por outro caminho."
        )


def guard_seq_lock(tool, tool_input, cwd):
    """Bloqueia, na medida da camada de fricção, escrita do agente em
    .specgate/seq via tool call.

    `.specgate/seq` é o contador de turnos que `has_human_turn_since` usa
    para checar se um `UserPromptSubmit` real aconteceu desde a abertura do
    gate. A propriedade genuinamente forte não é este ARQUIVO — como
    qualquer arquivo, ele só é protegido pelo que `write_targets` reconhece
    (ver "MODELO DE CAMADAS" acima) — é o EVENTO: o Claude não fabrica um
    `UserPromptSubmit`, e o incremento de verdade só acontece dentro de
    `log_event.py`, disparado como HOOK nesse evento; por rodar como hook
    (e não como tool call do agente), ele nunca passa por este guard
    PreToolUse, então bloquear aqui não afeta o incremento real. Qualquer
    escrita partindo de uma tool call — Write, Edit, ou Bash reconhecido
    por `write_targets` (redirecionamento/tee/sed -i/mv/cp/rm/truncate/
    dd/install/interpretador inline ou heredoc) — forjaria essa prova de
    turno, por isso é bloqueada aqui, independente de haver gate aberto.
    """
    touches = _touches_state_file(tool, tool_input, cwd, SEQ_REL)
    if touches is None:
        _block_seq_if_too_large(cwd, tool_input)
        return
    if not touches:
        return
    block(
        "[spec-gate] ESCRITA EM .specgate/seq BLOQUEADA. Este contador de "
        "turnos é mantido pelo sistema (o hook log_event.py, disparado no "
        "evento UserPromptSubmit) e não deve ser escrito pelo agente: "
        "escrevê-lo à mão forjaria a prova de que o usuário falou, da qual "
        "depende o gate de PO. NÃO tente contornar por outro caminho — se "
        "você precisa que o usuário fale, peça e aguarde a resposta real."
    )


# Arquivos de estado que SÓ o hook escreve (nunca o fluxo do /spec-gate), com
# o que cada um prova. Ao contrário de phase/gate.json/batch.json — que o
# orquestrador precisa escrever no fluxo normal e por isso têm bloqueio
# condicionado —, estes dois não têm NENHUMA escrita legítima partindo de uma
# tool call: são gravados de dentro do próprio guard (record_red/bump_attempt).
HOOK_OWNED_STATE = (
    (RED_REL, "prova de que a suíte estava VERMELHA antes de implementar"),
    (ATTEMPTS_REL, "contagem de tentativas gastas na implementação"),
)


def _block_hook_state_if_too_large(cwd, tool_input, rel, proof):
    """Fallback de `guard_hook_state_lock` para comando Bash grande demais
    (I5). Mesma escolha de `_block_seq_if_too_large`, e pela mesma razão: a
    condição normal deste guard bloqueia SEMPRE que o alvo é um destes
    arquivos, sem depender de gate nenhum, então "não conseguir verificar"
    também bloqueia — mas só quando o comando ao menos MENCIONA o arquivo
    (pré-checagem O(n) de substring), para não penalizar heredoc legítimo.
    """
    cmd = tool_input.get("command", "")
    if not isinstance(cmd, str) or rel not in cmd:
        return
    block(
        f"[spec-gate] COMANDO GRANDE DEMAIS PARA VERIFICAR BLOQUEADO ({rel}). "
        f"Este comando Bash passa de 64KB e menciona {rel} — tamanho acima do "
        "qual o guard não reparseia o comando inteiro por token, então não há "
        f"como confirmar que ele não escreve na {proof}. Este arquivo é "
        "mantido pelo hook e não tem escrita legítima partindo do agente."
    )


def guard_hook_state_lock(tool, tool_input, cwd):
    """Bloqueia, na medida da camada de fricção, escrita do agente em
    .specgate/red.json e .specgate/attempts.json.

    Mesmo papel de `guard_seq_lock`, para os dois arquivos que 0.3.0
    introduziu. A promessa dos dois mecanismos novos é a MESMA do contador de
    turnos — "quem conta é o hook, não o agente que está sendo contado" —, e
    ela só existe se o agente não puder reescrever o resultado: sem este
    guard, um `Write .specgate/attempts.json` com `count: 0` devolve tentativas
    infinitas depois do teto, e um `Write .specgate/red.json` com
    `proven: true` forja o vermelho que a transição para a implementação
    exige, sem a suíte nunca ter falhado.

    Bloqueia SEMPRE que o alvo é um destes arquivos, com ou sem gate aberto:
    ao contrário de phase/gate.json/batch.json, aqui não existe escrita
    legítima partindo de uma tool call — o guard grava os dois de dentro do
    próprio processo (`record_red`/`bump_attempt`), que nunca passa por
    PreToolUse.
    """
    for rel, proof in HOOK_OWNED_STATE:
        touches = _touches_state_file(tool, tool_input, cwd, rel)
        if touches is None:
            _block_hook_state_if_too_large(cwd, tool_input, rel, proof)
            continue
        if not touches:
            continue
        block(
            f"[spec-gate] ESCRITA EM {rel} BLOQUEADA. Este arquivo é mantido "
            f"pelo hook (é a {proof}) e escrevê-lo à mão forja exatamente o "
            "fato que ele existe para provar. NÃO tente contornar por outro "
            "caminho: se o teto de tentativas estourou, PARE e reporte ao PO; "
            "se a suíte já passa antes de implementar, abra o gate de RED e "
            "deixe o PO decidir."
        )


def _batch_from_content(tool, tool_input):
    """Conteúdo pretendido de .specgate/batch.json; None se não der para
    saber.

    Mesmo raciocínio de `_gates_from_content`: só o Write carrega o
    conteúdo final. Edit e Bash (rm/truncate/redirecionamento/interpretador
    inline etc.) não expõem o resultado, então caem na regra conservadora
    de quem chama esta função.
    """
    if tool != "Write":
        return None
    content = tool_input.get("content")
    if not isinstance(content, str):
        return None
    try:
        data = json.loads(content)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def guard_batch_lock(tool, tool_input, cwd):
    """Bloqueia, na medida da camada de fricção, a escrita que desliga o
    congelamento da spec via .specgate/batch.json.

    Diferente de `guard_seq_lock` (que bloqueia QUALQUER escrita reconhecida
    em .specgate/seq), .specgate/batch.json PRECISA continuar escrevível
    pelo fluxo normal do comando /spec-gate — ele guarda status de PBIs,
    tentativas e commits do lote, não só o campo de aprovação. Um bloqueio
    total quebraria esse fluxo. Por isso o bloqueio aqui é mais estreito:
    só entra em jogo quando o disco JÁ tem `backlog_aprovado: true`
    (congelamento ligado) e a escrita pretendida remove ou torna falsy esse
    campo — exatamente o `echo '{}' > .specgate/batch.json` que desliga a
    própria proteção que congela docs/backlog/ (ver `guard_spec_lock`).
    Escritas que preservam `backlog_aprovado: true` (mudar status de item,
    tentativas, commit) passam livres, mesmo alterando outros campos.

    Conteúdo indisponível (Edit, ou Bash como rm/truncate/redirecionamento)
    com o disco já aprovado é tratado do mesmo jeito conservador que
    `guard_gate_clear` trata escrita não verificável no gate.json: sem ver
    o resultado final não há como confirmar que backlog_aprovado continua
    true, então bloqueia.
    """
    touches = _touches_state_file(tool, tool_input, cwd, BATCH_REL)
    if touches is None:
        _block_batch_if_too_large(cwd, tool_input)
        return
    if not touches:
        return
    if not _safe_gate_po_1_passed(cwd):
        return  # congelamento ainda não ligou: nada aqui para proteger
    new_batch = _batch_from_content(tool, tool_input)
    if isinstance(new_batch, dict) and new_batch.get("backlog_aprovado"):
        return  # preserva a aprovação: passa, mesmo mudando outros campos
    block(
        "[spec-gate] DESLIGAMENTO DE backlog_aprovado BLOQUEADO. "
        ".specgate/batch.json já registra o backlog aprovado, e esta "
        "escrita remove ou torna falso esse campo (ou não expõe o "
        "conteúdo final, como Edit ou Bash com rm/truncate/redirecionamento "
        "— sem ver o resultado não há como confirmar que backlog_aprovado "
        "continua true). Isso desligaria o congelamento de docs/backlog/ "
        "sem decisão nenhuma do PO. Se você precisa atualizar status de "
        "item, tentativas ou commit no lote, preserve o campo "
        "backlog_aprovado: true na mesma escrita."
    )


def _phase_write_pbi(tool, tool_input):
    """PBI da fase que esta chamada pretende gravar, ou None se não dá para
    saber (formato antigo, fase vazia, escrita por Bash não inspecionável).

    None significa "não sei de quem é esta transição" — e quem chama trata
    isso como transição de escopo desconhecido, sujeita ao bloqueio mais
    amplo. Saber é o que compra a liberação parcial, não o contrário.
    """
    if tool == "Write":
        raw = tool_input.get("content")
    elif tool == "Edit":
        raw = tool_input.get("new_string")
    else:
        return None
    if not isinstance(raw, str):
        return None
    _phase, pbi = parse_phase(raw)
    return pbi or None


def guard_po_gate(tool, tool_input, cwd):
    """Chokepoint: com gate de PO aberto, a transição de fase fica travada.

    Toda transição de fase LEGÍTIMA passa por escrita em .specgate/phase
    reconhecida por `write_targets`, então bloquear esse arquivo encarece
    o fluxo de avançar sem decisão do PO — é a mesma camada de fricção
    descrita em "MODELO DE CAMADAS" acima de `write_targets`, não uma
    barreira à prova de qualquer comando Bash.

    ESCOPO (0.3.0): o bloqueio é POR PBI, não global. Até 0.2.0, qualquer
    gate aberto travava qualquer transição de fase — o que fazia um único PBI
    estacionado parar a fila inteira, limite que o README documentava como
    "estacionar preserva o trabalho, mas não faz o próximo PBI andar". A
    propriedade que importa continua intacta: nada avança NAQUILO que o PO
    não decidiu. O que muda é que o resto da fila deixa de ser refém.

    Bloqueia quando:
    1. Há gate de BACKLOG aberto — o contrato do lote inteiro está em jogo,
       e nenhum PBI dele significa nada até o PO decidir; ou
    2. Há gate aberto do PBI que esta escrita quer ativar; ou
    3. Há gate aberto SEM campo `pbi` (formato legado) — não dá para dizer a
       qual item ele se refere, então ele vale para todos; ou
    4. Não dá para saber de quem é a transição (fase sem PBI declarado,
       escrita por Bash, fase sendo apagada) e existe QUALQUER gate aberto —
       o lado seguro de 0.2.0, preservado para todo caso não identificado.

    Os dois lados da identificação são simétricos de propósito: só há
    liberação parcial quando SE SABE de quem é o gate E de quem é a
    transição. Ignorância de qualquer um dos lados cai no bloqueio amplo —
    senão omitir o dado viraria a forma mais fácil de destravar.
    """
    gates = _safe_open_gates(cwd)
    if not gates:
        return
    touches = _touches_state_file(tool, tool_input, cwd, PHASE_REL)
    if touches is None:
        _block_if_too_large(cwd, ".specgate/phase")
        return
    if not touches:
        return

    alvo = _phase_write_pbi(tool, tool_input)
    if alvo is not None:
        def relevante(g):
            if g.get("checkpoint") == "backlog":
                return True
            pbi = g.get("pbi")
            if not isinstance(pbi, str) or not pbi:
                return True  # gate sem PBI: não dá para dizer de quem é
            return _same_file(alvo, cwd, pbi)

        relevantes = [g for g in gates if relevante(g)]
        if not relevantes:
            return  # gates abertos, mas nenhum deles é sobre este PBI
        gates = relevantes

    names = ", ".join(str(g.get("checkpoint", "?")) for g in gates)
    block(
        f"[spec-gate] GATE DE PO ABERTO ({names}). O fluxo não avança de fase "
        "enquanto o PO não decidir. NÃO tente contornar o bloqueio nem editar "
        "o arquivo de fase por outro caminho. Apresente ao PO a decisão "
        "pendente, em uma linha e com opções concretas, e aguarde a resposta."
    )


def _round_int(g):
    """Número da rodada de um gate, como int >= 1 — ou None se o campo
    está PRESENTE mas malformado (nunca levanta, para não gerar traceback
    num hook bloqueante).

    Ausência da chave "rodada" no dict é tratada como 1: gates gravados
    antes deste fix não têm o campo, e tratá-los como rodada 1 é o que
    permite abrir a "rodada 2" de uma chave antiga sem quebrar o estado
    existente (compatibilidade, não um caso especial de exceção).

    Presente mas malformado (string não numérica, float, negativo,
    booleano — que em Python é subclasse de int e por isso é rejeitado
    explicitamente — lista, dict, null) devolve None; o chamador decide o
    lado seguro, que em `guard_gate_clear` é sempre bloquear a escrita
    inteira antes de usar o valor para qualquer comparação.
    """
    if "rodada" not in g:
        return 1
    v = g.get("rodada")
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if v >= 1 else None
    if isinstance(v, str):
        s = v.strip()
        if not re.fullmatch(r"-?\d+", s):
            return None
        n = int(s)
        return n if n >= 1 else None
    return None


def _gate_key(g):
    """Identidade completa de uma entrada de gate: (checkpoint, pbi, rodada).

    A rodada entrou na chave (Task 9) para permitir reabrir o MESMO
    (checkpoint, pbi) numa rodada seguinte sem colidir com o registro já
    decidido da rodada anterior — sem isto, "reprovado" e "aprovado" do
    mesmo PBI seriam a mesma chave, e reabrir caía sempre na categoria 2 de
    `_mutacao_invalida` (gate decidido é imutável). Rodada malformada vira
    None aqui (nunca gera exceção); a escrita inteira é bloqueada antes de
    esta chave ser usada para agrupar seja o que for — ver
    `_rodadas_malformadas`, checada em `guard_gate_clear` antes de qualquer
    lógica que dependa de `_gate_key`.
    """
    return (str(g.get("checkpoint", "")), str(g.get("pbi", "")), _round_int(g))


def _pbi_series_key(g):
    """Identidade da SÉRIE de rodadas de um gate: (checkpoint, pbi), sem a
    rodada. Usada só pelas amarras 1 e 2 (`_rodada_invalida`), que
    precisam enxergar TODAS as rodadas históricas de um mesmo
    (checkpoint, pbi) para derivar a próxima e checar a reprovação que a
    legitima — o que `_gate_key` (com rodada embutida) não permite, porque
    cada rodada tem sua própria chave completa.
    """
    return (str(g.get("checkpoint", "")), str(g.get("pbi", "")))


def _malformed_rounds(new_gates):
    """Entradas em `novos` cujo campo "rodada" está PRESENTE mas é
    inválido (ausência é tratada como 1 em `_rodada_int`, não malformação).
    """
    return [g for g in new_gates if "rodada" in g and _round_int(g) is None]


def _invalid_round(previous, new_gates):
    """Amarras 1 e 2 do mecanismo de rodada (Task 9): valida a ABERTURA
    genuína de uma rodada nova de uma série (checkpoint, pbi).

    Só examina entradas que representam uma abertura de verdade: status
    'aguardando-po' cuja chave completa (checkpoint, pbi, rodada) nunca
    existiu no estado anterior. Preservação de uma rodada já aberta e
    decisão de uma rodada existente não passam por aqui — são território
    de `_mutacao_invalida`, que já lida com elas pela chave completa.

    Devolve (ofensores_pulo, ofensores_sem_reprovacao):

    - ofensores_pulo (amarra 1 — a rodada é DERIVADA, não escolhida): a
      rodada declarada precisa ser exatamente
      max(rodadas já existentes da série) + 1 — nunca pulada (abrir a
      rodada 3 direto de uma série que só tem rodada 1) nem repetida
      (rodada 1 nova enquanto a rodada 1 histórica já existe: esse caso
      cai aqui só quando a chave completa ainda assim é "nova", o que só
      acontece se `anteriores` tiver rodadas malformadas por baixo — o
      caso comum de repetir uma rodada já registrada é bloqueado antes
      disto, pela categoria 2 de `_mutacao_invalida`, gate decidido
      imutável).
    - ofensores_sem_reprovacao (amarra 2 — quem legitima a rodada seguinte
      é um EVENTO DE DECISÃO REGISTRADO, não a vontade de quem escreve):
      toda rodada > 1 exige que a rodada imediatamente anterior da MESMA
      série já exista no estado anterior em disco com status em
      `STATUS_QUE_LEGITIMAM_RODADA` ('reprovado' ou 'respondido' — lista
      única, a mesma para qualquer checkpoint, sem condicional por nome).
      Rodada anterior 'aprovado' (o PBI passou, não há o que reabrir) ou
      ainda 'aguardando-po' (ninguém decidiu nada ainda) não legitima nada.
    """
    previous_keys = {_gate_key(a) for a in previous}
    skip_offenders = []
    unlegitimized_offenders = []
    for g in new_gates:
        if g.get("status") != "aguardando-po":
            continue
        round_ = _round_int(g)
        if round_ is None:
            continue  # malformada: já bloqueada à parte por _rodadas_malformadas
        if _gate_key(g) in previous_keys:
            continue  # não é abertura nova desta rodada — é preservação
        series = _pbi_series_key(g)
        existing = [
            _round_int(a) for a in previous if _pbi_series_key(a) == series
        ]
        existing = [r for r in existing if r is not None]
        expected = (max(existing) if existing else 0) + 1
        if round_ != expected:
            skip_offenders.append(g)
            continue
        if round_ > 1:
            previous_round = round_ - 1
            legitimate = any(
                _pbi_series_key(a) == series
                and _round_int(a) == previous_round
                and a.get("status") in STATUSES_THAT_LEGITIMIZE_ROUND
                for a in previous
            )
            if not legitimate:
                unlegitimized_offenders.append(g)
    return skip_offenders, unlegitimized_offenders


def _gate_label(g):
    """Rótulo legível de uma entrada de gate para mensagens de bloqueio:
    checkpoint, PBI (se houver) e rodada — a rodada aparece sempre, mesmo
    quando ausente no JSON (rodada 1 implícita), porque é exatamente esse
    número que dá o dado útil de "PBI-03 está na rodada 3" (está brigando).
    """
    checkpoint = g.get("checkpoint", "?")
    pbi = g.get("pbi")
    round_ = g.get("rodada", 1)
    label = str(checkpoint)
    if pbi:
        label += f"/{pbi}"
    return f"{label} (rodada {round_})"


def _gates_from_content(tool, tool_input):
    """Gates que a escrita pretende gravar; None se não der para saber.

    Só o Write carrega o conteúdo pretendido. Edit e Bash não expõem o
    resultado final, então caem na regra conservadora.
    """
    if tool != "Write":
        return None
    content = tool_input.get("content")
    if not isinstance(content, str):
        return None
    try:
        data = json.loads(content)
    except ValueError:
        return None
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return None
    return [g for g in data if isinstance(g, dict)]


def _safe_has_human_turn(cwd, opened_at_seq):
    """Wrapper fail-open sobre specgate_state.has_human_turn_since.

    Mesmo raciocínio de `_open_gates_seguro`: módulo ausente ou presente mas
    parcial/desatualizado (sem has_human_turn_since) não pode derrubar um
    hook bloqueante. Assume-se "houve turno" — o guard não bloqueia por
    causa de um erro interno seu, só por auto-liberação de verdade.
    """
    if specgate_state is None:
        return True
    try:
        return specgate_state.has_human_turn_since(cwd, opened_at_seq)
    except Exception:
        return True


def _safe_current_seq(cwd):
    """Wrapper fail-open sobre specgate_state.read_seq.

    Mesmo raciocínio dos demais `_seguro`: módulo ausente ou incompleto não
    pode derrubar o guard. 0 é o lado permissivo aqui — exigir
    `opened_at_seq >= 0` praticamente nunca bloqueia por erro interno, só
    quando a abertura é antedatada de verdade.
    """
    if specgate_state is None:
        return 0
    try:
        return specgate_state.read_seq(cwd)
    except Exception:
        return 0


def _opened_at_seq_int(g):
    """opened_at_seq como int, ou None se ausente/malformado (string, nulo,
    etc.) — nunca levanta, para não gerar traceback num hook bloqueante."""
    try:
        return int(g.get("opened_at_seq", 0))
    except (TypeError, ValueError):
        return None


def _previous_open_for_key(previous_for_key):
    """A entrada ANTERIOR desta chave cujo status era 'aguardando-po', se
    houver — None se a chave nunca existiu ou só existia já decidida.

    É o fato que distingue PRESERVAÇÃO real de reabertura disfarçada: só
    importa que a chave estava com o MESMO status logo antes desta escrita,
    nunca "existiu alguma vez com esse valor" (que é o que a versão antiga
    checava, e é exatamente o furo da chave decidida reaberta).
    """
    for a in previous_for_key:
        if a.get("status") == "aguardando-po":
            return a
    return None


def _previous_decided_for_key(previous_for_key):
    """A entrada ANTERIOR desta chave cujo status já era decidido (≠
    'aguardando-po'), se houver — None se a chave nunca existiu ou só
    existia aberta.

    Este é o gancho da categoria 2 (o coração deste fix): uma vez que um
    gate saiu de 'aguardando-po' pela primeira vez, ele vira registro de
    auditoria congelado. Achar essa entrada aqui é o que permite bloquear
    QUALQUER mutação da chave nesta escrita — não importa se o status novo
    também é decidido (flip-flop reprovado<->aprovado) ou se é uma
    tentativa de reabrir como 'aguardando-po': as duas formas de mexer num
    gate já decidido passam por este mesmo caminho, sem depender do valor
    novo declarado (que o próprio Claude escreve e não é confiável).
    """
    for a in previous_for_key:
        if a.get("status") != "aguardando-po":
            return a
    return None


def _invalid_mutation(cwd, previous, new_gates):
    """Impõe o modelo unificado de mutação de uma chave (checkpoint, pbi)
    entre o estado ANTERIOR em disco e o conteúdo NOVO desta escrita,
    devolvendo `(ofensores_abertura, ofensores_decisao, ofensores_decidido)`.

    MODELO UNIFICADO (as 5 categorias completas vivem no docstring de
    `guard_gate_clear`; aqui só as três primeiras, que dependem do estado
    ANTERIOR da chave):

    1. Chave estava 'aguardando-po' antes: só ENTRA (abre/reabre) em
       'aguardando-po' com `opened_at_seq >= seq_atual` (nunca no
       passado), e só SAI de 'aguardando-po' mudando de status e MANTENDO
       `opened_at_seq` idêntico — nunca alterando o valor na mesma escrita
       que decide.
    2. Chave já estava DECIDIDA antes (status != 'aguardando-po') — o
       CORAÇÃO deste fix: o gate decidido é um registro de auditoria
       congelado. A ÚNICA mutação aceita é a identidade (mesmo status,
       mesmo `opened_at_seq`); QUALQUER outra diferença — inclusive trocar
       de um status decidido para outro (flip-flop reprovado<->aprovado
       sem turno novo) ou tentar reabrir como 'aguardando-po', mesmo com
       `opened_at_seq` fresco — é bloqueada aqui, sem nem olhar
       `seq_atual`. Reverter uma decisão não é reescrever a
       chave: é semanticamente abrir um gate NOVO, o que só é possível
       numa escrita SEPARADA em que a chave já não aparece no estado
       anterior (categoria 3, que aí sim exige `opened_at_seq >= seq_atual`
       — ou seja, fala do PO). Isto fecha o furo em que uma decisão
       (aprovado/reprovado) já registrada era reescrita indefinidamente
       porque `has_human_turn_since` validava contra um `opened_at_seq`
       antigo que o `seq` global já havia ultrapassado uma única vez, e
       essa comparação nunca precisava de um turno NOVO a cada reescrita.
    3. Chave nova (nunca existiu, em nenhum status): fica de fora desta
       função — cai na regra de abertura por ausência de `anterior_aberto`
       e `anterior_decidido` (ambos None), tratada abaixo como o caso "sem
       histórico algum", que ainda assim exige `opened_at_seq >= seq_atual`
       quando o novo status é 'aguardando-po' (abertura genuína) e é
       bloqueada por `_decided_without_turn` quando o novo status já vem
       decidido (aprovação fabricada do zero).

    ofensores_abertura — entradas 'aguardando-po' em `novos` cuja
    abertura/reabertura é antedatada. PRESERVAÇÃO real (a única isenta da
    exigência `>= seq_atual`) exige as DUAS coisas: a chave já estava
    'aguardando-po' no estado anterior completo (`anteriores`, de
    `read_gates` — não só as abertas) E o `opened_at_seq` é IDÊNTICO ao
    anterior. Checar só o valor (sem o status anterior) é o furo antigo:
    permitia "preservar" com o `opened_at_seq` REBAIXADO frente ao valor de
    disco corrente sem cair na checagem — mas como o `seq` é monotônico e
    já alcançou o valor original na abertura, rebaixar nunca sobrevive à
    exigência `>= seq_atual` de qualquer forma.

    ofensores_decisao — entradas DECIDIDAS (status != 'aguardando-po') em
    `novos` cuja chave estava 'aguardando-po' no estado anterior com um
    `opened_at_seq` DIFERENTE do declarado agora. O `opened_at_seq` é o
    carimbo temporal que a checagem de turno humano usa; alterá-lo (para
    baixo OU para cima) na MESMA escrita que decide reescreveria esse
    carimbo sem que o PO tenha visto o valor novo — rebaixar tornaria a
    aprovação forjável (o `has_human_turn_since` seguinte validaria contra
    um valor menor, mais fácil de satisfazer), e mesmo subir corrompe o
    registro que uma auditoria posterior confiaria.

    ofensores_decidido — entradas em `novos` cuja chave já estava DECIDIDA
    (categoria 2 acima) e que não são idênticas à entrada anterior. Cobre
    o flip-flop entre dois status decididos, alterar só o `opened_at_seq`
    mantendo o status, e reabrir como 'aguardando-po' (histórico ou
    fresco) — as três formas de mexer numa chave já congelada.
    """
    previous_by_key = {}
    for g in previous:
        previous_by_key.setdefault(_gate_key(g), []).append(g)

    current_seq = _safe_current_seq(cwd)
    opening_offenders = []
    decision_offenders = []
    decided_offenders = []
    for g in new_gates:
        key = _gate_key(g)
        previous_for_key = previous_by_key.get(key, [])
        previous_open = _previous_open_for_key(previous_for_key)
        previous_open_seq = (
            _opened_at_seq_int(previous_open) if previous_open is not None else None
        )
        previous_decided = _previous_decided_for_key(previous_for_key)
        opened_at_seq = _opened_at_seq_int(g)

        if previous_decided is not None:
            # Categoria 2: a chave já era um registro congelado antes desta
            # escrita. Não importa o status novo declarado (decidido de
            # novo, ou 'aguardando-po') nem o valor de `opened_at_seq` —
            # só a identidade exata sobrevive. Isto é checado ANTES e
            # independente das ramificações de abertura/decisão abaixo,
            # porque uma chave decidida não deve mais entrar nelas: seria
            # tratar uma mutação de registro congelado como se fosse uma
            # abertura ou decisão legítimas.
            previous_decided_seq = _opened_at_seq_int(previous_decided)
            if (
                g.get("status") != previous_decided.get("status")
                or opened_at_seq != previous_decided_seq
            ):
                decided_offenders.append(g)
            continue

        if g.get("status") == "aguardando-po":
            if opened_at_seq is None:
                # Malformado (string/nulo/etc.): não dá para confiar no
                # valor. Lado seguro é bloquear, nunca supor que está ok.
                opening_offenders.append(g)
                continue
            if previous_open is not None and previous_open_seq == opened_at_seq:
                continue  # preservação genuína: nada a exigir
            if opened_at_seq < current_seq:
                opening_offenders.append(g)
        else:
            if previous_open is not None and opened_at_seq != previous_open_seq:
                decision_offenders.append(g)

    return opening_offenders, decision_offenders, decided_offenders


def _improperly_deleted_gates(open_gates_, new_gates):
    """Gates que estavam 'aguardando-po' e desaparecem da escrita nova.

    Fecha a Aresta B: o chokepoint deste guard é "gate aberto bloqueia
    escrita na phase", mas se o gate.json for esvaziado, truncado ou tiver
    a chave simplesmente omitida, não há mais gate aberto e a phase
    destrava — sem que o PO tenha decidido nada. A decisão legítima MUDA o
    status do gate (aprovado/reprovado), preservando a entrada como
    registro; nunca a remove. Por isso isto bloqueia SEMPRE que uma chave
    aberta some, mesmo com turno humano presente — "apagar não é decidir".
    Deletar/omitir uma chave que já estava DECIDIDA antes (não em
    `abertos`) é limpeza legítima e não entra aqui.
    """
    new_keys = {_gate_key(g) for g in new_gates}
    return [g for g in open_gates_ if _gate_key(g) not in new_keys]


def _decided_without_turn(cwd, open_gates_, previous, new_gates):
    """Gates que esta escrita libera efetivamente sem turno humano posterior.

    Cobre duas formas de liberação sem turno, ambas sobre CHAVE
    (checkpoint, pbi), nunca confiando em qual entrada "sobrevive" num dict
    comum (a deleção pura de um gate aberto — a TERCEIRA forma — é a Aresta
    B e é tratada à parte por `_gates_deletados_indevidamente`, chamada
    antes desta função por `guard_gate_clear`; aqui só entram chaves que
    CONTINUAM presentes em `novos`):

    1. Uma chave tem UMA SÓ entrada em `novos` e ela não é "aguardando-po"
       -> decisão.
    2. Uma chave tem MÚLTIPLAS entradas em `novos` (chave duplicada) e nem
       todas são "aguardando-po" -> ambíguo, tratado como decisão. Isto
       fecha o smuggling por chave duplicada: um dict comum
       (`{chave: g}`) deixaria só a última entrada sobreviver e, se ela for
       "aguardando-po", uma entrada "aprovado" fabricada passaria junto sem
       jamais ser examinada.

    Toda "decisão" (item 1 ou 2) só é aceita se a MESMA chave já existia no
    estado anterior COMPLETO (`anteriores`, de read_gates — aberto ou já
    decidido antes, não só as abertas). Uma decisão sobre uma chave que
    nunca foi aberta é uma aprovação que o PO jamais pediu, e por isso é
    bloqueada mesmo com `opened_at_seq` baixo auto-declarado e turno humano
    de sobra: não existe gate anterior cujo `opened_at_seq` validar.

    A validação de turno em si usa sempre o `opened_at_seq` do gate ANTERIOR
    em disco (o de `abertos`, único caso que chega até ela) — nunca o valor
    autodeclarado no conteúdo novo (esse
    valor autodeclarado, quando a chave estava aberta, já foi validado à
    parte por `_mutacao_invalida`: se ele divergir do anterior, a escrita
    já terá sido bloqueada antes de chegar aqui).

    Chave que já estava DECIDIDA antes desta escrita (presente em
    `anteriores`, ausente de `abertos`) NUNCA entra na checagem de turno
    aqui — ela é governada por inteiro pela categoria 2 de
    `_mutacao_invalida` (gate decidido é imutável: qualquer mutação
    bloqueia, preservação idêntica passa direto, sem exigir turno). Incluí
    -la aqui duplicaria a checagem e quebraria justamente o caso legítimo
    de preservação idêntica: uma reescrita byte-a-byte do mesmo status
    decidido seria barrada por "falta de turno" mesmo não sendo decisão
    nenhuma, já que o `opened_at_seq` histórico nunca muda.
    """
    open_by_key = {_gate_key(g): g for g in open_gates_}
    previous_by_key = {_gate_key(g): g for g in previous}

    new_by_key = {}
    for g in new_gates:
        new_by_key.setdefault(_gate_key(g), []).append(g)

    offenders = []
    for key in set(open_by_key) | set(new_by_key):
        entries = new_by_key.get(key)
        previous_open = open_by_key.get(key)

        if entries is None:
            continue  # deleção: tratada por _gates_deletados_indevidamente

        if previous_open is None and key in previous_by_key:
            continue  # já decidida antes: territorio exclusivo da categoria 2

        all_waiting = all(e.get("status") == "aguardando-po" for e in entries)
        if all_waiting:
            continue  # segue aberto (ou é abertura nova): nada foi decidido

        # É decisão: entrada única com status != aguardando-po, ou chave
        # duplicada ambígua onde nem tudo é aguardando-po.
        if key not in previous_by_key:
            # Chave nunca existiu no estado anterior completo: aprovação
            # fabricada do zero. Bloqueia sempre — não há gate anterior
            # cujo turno humano possa validar isto.
            offenders.append(entries[0])
            continue

        # Aqui `anterior_aberto` é necessariamente não-None: a chave está em
        # `anteriores` (senão teria caído no ramo acima) e o caso "em
        # `anteriores` mas não aberta" já saiu por `continue` lá em cima
        # (território exclusivo da categoria 2).
        if not _safe_has_human_turn(cwd, previous_open.get("opened_at_seq", 0)):
            offenders.append(previous_open)

    return offenders


def guard_gate_clear(tool, tool_input, cwd):
    """Impede o Claude de se auto-liberar escrevendo no gate.json.

    MODELO UNIFICADO — toda escrita é validada chave a chave (checkpoint,
    pbi, rodada — ver `_gate_key`), comparando o conteúdo NOVO contra o
    estado ANTERIOR completo em disco. A rodada (Task 9) entrou na chave
    para separar cada tentativa de decisão de um mesmo (checkpoint, pbi):
    sem ela, reprovar e depois reabrir para rework era a MESMA chave, e
    "gate decidido é imutável" (categoria 2 abaixo) bloqueava o rework
    legítimo junto com o flip-flop que a regra existe para impedir. Abrir
    uma rodada nova passa por DUAS amarras adicionais, checadas antes das
    5 categorias (`_rodada_invalida`): a rodada é sempre
    max(rodada existente da série) + 1 (nunca pulada, nunca repetida), e só
    é legítima se a rodada anterior daquela série está REGISTRADA com
    status em `STATUS_QUE_LEGITIMAM_RODADA` ('reprovado' ou 'respondido',
    conforme o checkpoint — lista única, sem condicional por nome de
    checkpoint) — 'aprovado' ou ainda 'aguardando-po' não abrem porta
    nenhuma. Cada chave cai em exatamente uma destas 5 categorias:

    1. Chave estava 'aguardando-po' antes: preservada idêntica é OK;
       reaberta (mesmo status, `opened_at_seq` diferente) exige
       `opened_at_seq >= seq_atual`; decidida (status muda para !=
       'aguardando-po') exige `opened_at_seq` preservado E `seq_atual` >
       aquele valor (turno humano real); e some do conteúdo novo é sempre
       bloqueado — deleção de gate aberto é auto-liberação disfarçada.
    2. Chave já estava DECIDIDA antes (status != 'aguardando-po') — o
       CORAÇÃO deste fix: congelada. Só a identidade exata (mesmo status,
       mesmo `opened_at_seq`) sobrevive; qualquer outra mutação nesta
       MESMA escrita é bloqueada, sem depender de `seq_atual` — inclusive
       trocar de um status decidido para outro (o flip-flop
       reprovado<->aprovado que este fix fecha) e tentar reabrir como
       'aguardando-po'. Reverter uma
       decisão não é reescrever a chave: é abrir um gate NOVO, que só
       existe numa escrita SEPARADA em que a chave já não aparece no
       anterior (cai então na categoria 3, que exige fala do PO). Deletar
       uma chave decidida, por outro lado, é limpeza legítima — OK.
    3. Chave nova (nunca existiu, em nenhum status): abrir como
       'aguardando-po' exige `opened_at_seq >= seq_atual`; qualquer chave
       nova que já chega com status decidido é bloqueada — não existe
       gate anterior cujo turno humano valide uma decisão sobre algo que
       nunca foi perguntado ao PO.
    4. Chave duplicada em `novos` (duas entradas com a mesma chave) onde
       nem todas são 'aguardando-po': conteúdo ambíguo, bloqueado sempre —
       regra conservadora contra smuggling de uma entrada decidida
       fabricada ao lado de uma 'aguardando-po' legítima.
    5. Conteúdo indisponível (Edit, ou Bash tipo rm/truncate/redirect no
       gate.json): com QUALQUER gate 'aguardando-po' no anterior, bloqueia
       sempre — sem ver o conteúdo final não há como confirmar decisão vs.
       deleção disfarçada.

    Isto se desdobra em cinco checagens nesta ordem:

    1. Conteúdo indisponível (categoria 5), checado primeiro porque sem
       conteúdo novo nenhuma das checagens seguintes tem o que examinar.
    2. `_mutacao_invalida` — três resultados, cada um com sua própria
       categoria: abertura/reabertura antedatada e decisão que altera
       `opened_at_seq` de um gate que estava aberto (categoria 1), e
       mutação de uma chave já decidida (categoria 2, o coração deste
       fix).
    3. `_gates_deletados_indevidamente` (Aresta B, categoria 1): um gate
       aberto que desaparece do conteúdo novo é auto-liberação por
       deleção — SEMPRE, mesmo com turno humano. "Apagar não é decidir".
       (Deletar uma chave já DECIDIDA — categoria 2 — não passa por aqui:
       só olha os gates que estavam abertos.)
    4. `_decided_without_turn`: decisão sem turno humano REAL posterior ao
       `opened_at_seq` do gate (cobre categorias 1 e 3: gate que estava
       aberto decidido sem turno, e chave fabricada do zero já decidida —
       inclui chave duplicada ambígua, categoria 4). Esta é a propriedade
       genuinamente forte do gate: o Claude não fabrica um
       UserPromptSubmit, então o caminho honesto — pedir a decisão ao PO e
       esperar a resposta real — é sempre mais barato que forjá-la (a
       detecção da ESCRITA em si, feita por `write_targets` acima, é a
       camada de fricção, não uma parede).

    LIMITE CONHECIDO: se a fala do PO SUSTENTA a decisão daquele gate
    específico, o hook não tem como saber — isso é semântica, e hook não lê
    semântica. Com dois gates abertos e um turno posterior a ambos, decidir
    os dois passa no hook. Segurar o gate não-respondido é instrução do
    comando /spec-gate mais honestidade do modelo, igual ao gatilho de
    granularidade. Documentado assim de propósito, sem inflar.
    """
    touches = _touches_state_file(tool, tool_input, cwd, GATE_REL)
    if touches is None:
        _block_if_too_large(cwd, ".specgate/gate.json")
        return
    if not touches:
        return
    open_gates_ = _safe_open_gates(cwd)
    new_gates = _gates_from_content(tool, tool_input)

    if new_gates is None:
        # Conteúdo indisponível (Edit, ou Bash como `rm gate.json`,
        # `> gate.json`, `truncate`, `sed -i`...): o fluxo legítimo SEMPRE
        # usa Write com o JSON completo, então não há como confirmar que a
        # escrita resultante é uma decisão (mudança de status) e não uma
        # deleção/truncamento. Bloqueia sempre que existir QUALQUER gate
        # aberto — mesmo com turno humano presente, porque turno não prova
        # nada sobre o CONTEÚDO que este comando produz. Sem gate aberto,
        # não há nada aqui para proteger.
        if not open_gates_:
            return
        names = ", ".join(
            f"{g.get('checkpoint', '?')}" + (f"/{g['pbi']}" if g.get("pbi") else "")
            for g in open_gates_
        )
        block(
            f"[spec-gate] ESCRITA NÃO VERIFICÁVEL NO GATE BLOQUEADA ({names}). "
            "Este comando não expõe o conteúdo final de .specgate/gate.json "
            "(Edit, ou Bash como rm/truncate/redirecionamento), e existe gate "
            "aberto aguardando o PO. Sem ver o conteúdo resultante não há como "
            "confirmar que isto é uma decisão (mudança de status preservando "
            "opened_at_seq) e não uma deleção disfarçada. O fluxo legítimo usa "
            "Write com o JSON completo. Apresente a decisão pendente ao PO e "
            "aguarde a resposta real."
        )

    # NUNCA usar só `abertos` como gate de entrada nas checagens abaixo:
    # uma chave fabricada do zero (nunca aberta) só é pega olhando também
    # `anteriores` (estado completo em disco), então sempre computamos e
    # chamamos, mesmo com `abertos` vazio.
    previous = _safe_read_gates(cwd)

    # Rodada malformada (Task 9): checado ANTES de qualquer lógica que use
    # `_gate_key`/`_rodada_int` para agrupar ou comparar — um valor
    # presente mas inválido (string não numérica, float, negativo,
    # booleano, lista...) não pode virar identidade de gate nenhuma.
    # Ausência do campo é tratada como rodada 1 em `_rodada_int` (nunca cai
    # aqui); só a presença malformada bloqueia.
    malformed = _malformed_rounds(new_gates)
    if malformed:
        names = ", ".join(str(g.get("rodada")) for g in malformed)
        block(
            f"[spec-gate] RODADA INVÁLIDA BLOQUEADA (valor: {names}). O campo "
            "'rodada' precisa ser um inteiro >= 1 (ou string equivalente). "
            "Omitir o campo é tratado como rodada 1 por compatibilidade, mas "
            "um valor presente e malformado não pode ser usado para "
            "identificar o gate — corrija o número antes de escrever."
        )

    # Amarras 1 e 2 do mecanismo de rodada: a rodada de uma abertura nova é
    # sempre DERIVADA (max da série + 1, nunca pulada nem repetida) e só é
    # legítima quando a rodada anterior da mesma série está REGISTRADA com
    # status em STATUS_QUE_LEGITIMAM_RODADA. Checado antes de
    # `_mutacao_invalida` pelo mesmo motivo da Aresta A: é sobre a
    # identidade/abertura da chave, não sobre decisão.
    round_skip_offenders, unlegitimized_offenders = _invalid_round(previous, new_gates)
    if round_skip_offenders:
        names = ", ".join(_gate_label(g) for g in round_skip_offenders)
        block(
            f"[spec-gate] RODADA FORA DE SEQUÊNCIA BLOQUEADA ({names}). A "
            "rodada de uma abertura nova é DERIVADA, não escolhida: precisa "
            "ser exatamente max(rodada já existente daquele checkpoint+pbi) + "
            "1 — nunca pulando um número, nunca repetindo uma rodada já "
            "registrada. Abra a próxima rodada na sequência certa."
        )

    if unlegitimized_offenders:
        names = ", ".join(_gate_label(g) for g in unlegitimized_offenders)
        legitimate_statuses = " ou ".join(f"'{s}'" for s in STATUSES_THAT_LEGITIMIZE_ROUND)
        block(
            f"[spec-gate] RODADA SEM REPROVAÇÃO ANTERIOR BLOQUEADA ({names}). "
            f"Só um evento de decisão REGISTRADO (status {legitimate_statuses} — "
            "conforme o checkpoint) da rodada anterior daquele checkpoint+pbi "
            "legitima abrir a rodada seguinte — rodada anterior 'aprovado' "
            "(o PBI já passou) ou ainda 'aguardando-po' (ninguém decidiu) "
            "não abre porta nenhuma para a próxima."
        )

    # Aresta A: abertura/reabertura antedatada, e decisão que altera o
    # opened_at_seq do gate aberto. Barra ANTES de examinar deleção/turno —
    # um opened_at_seq forjado (rebaixado, herdado de uma chave decidida,
    # ou trocado ao decidir) forjaria a aprovação seguinte, então isto é
    # checado por si só, com mensagens próprias.
    opening_offenders, decision_seq_offenders, decided_offenders = _invalid_mutation(
        cwd, previous, new_gates
    )
    if opening_offenders:
        names = ", ".join(
            f"{g.get('checkpoint', '?')}" + (f"/{g['pbi']}" if g.get("pbi") else "")
            for g in opening_offenders
        )
        block(
            f"[spec-gate] ABERTURA DE GATE ANTEDATADA BLOQUEADA ({names}). "
            "Um gate recém-aberto ou reaberto não pode declarar opened_at_seq "
            "menor que o seq atual do projeto — isso tornaria a aprovação "
            "seguinte forjável, porque o seq já teria 'ultrapassado' aquele "
            "valor por turnos ANTERIORES à abertura, sem exigir nenhuma fala "
            "nova do PO depois que o gate (re)abriu. Isto vale tanto para uma "
            "chave nova quanto para reabrir uma chave já DECIDIDA carimbada "
            "com o valor histórico: opened_at_seq de uma abertura/reabertura "
            "deve ser o seq atual (ou maior), nunca herdado do passado."
        )

    if decision_seq_offenders:
        names = ", ".join(
            f"{g.get('checkpoint', '?')}" + (f"/{g['pbi']}" if g.get("pbi") else "")
            for g in decision_seq_offenders
        )
        block(
            f"[spec-gate] DECISÃO ALTERA opened_at_seq BLOQUEADA ({names}). O "
            "opened_at_seq de um gate aberto é imutável ao decidir: precisa "
            "continuar exatamente igual ao valor registrado na abertura. "
            "Mudá-lo (para baixo ou para cima) na MESMA escrita que decide "
            "reescreveria o carimbo temporal que a checagem de turno humano "
            "usa — rebaixar tornaria a aprovação forjável, e mesmo subir "
            "corrompe o registro que uma auditoria posterior confiaria."
        )

    # Categoria 2 (o coração deste fix): a chave já era um registro
    # DECIDIDO antes desta escrita, e a nova entrada não é idêntica. Isto
    # cobre tanto o flip-flop entre dois status decididos (reprovado <->
    # aprovado sem turno novo — o furo relatado, em que o `opened_at_seq`
    # antigo continuava validando contra um `seq` global que só precisou
    # ultrapassá-lo UMA vez) quanto tentar reabrir a chave como
    # 'aguardando-po', mesmo com `opened_at_seq` fresco. Bloqueado nesta
    # checagem sem depender de `seq_atual`: gate decidido é tratado como
    # imutável. Mudar de ideia exige abrir um gate NOVO numa escrita
    # separada — o que cai na categoria 3, essa sim condicionada a
    # `opened_at_seq >= seq_atual` (fala do PO).
    if decided_offenders:
        names = ", ".join(
            f"{g.get('checkpoint', '?')}" + (f"/{g['pbi']}" if g.get("pbi") else "")
            for g in decided_offenders
        )
        block(
            f"[spec-gate] GATE DECIDIDO É IMUTÁVEL BLOQUEADO ({names}). Uma "
            "vez que um gate sai de 'aguardando-po' pela primeira vez, ele "
            "vira registro de auditoria congelado: nenhuma escrita seguinte "
            "pode mudar seu status (nem entre dois status decididos, nem "
            "reabrindo como 'aguardando-po') ou seu opened_at_seq, mesmo com "
            "turno humano de sobra. Reverter uma decisão não é reescrever o "
            "gate — é semanticamente abrir um gate NOVO, o que exige uma "
            "escrita separada e opened_at_seq >= seq atual (ou seja, fala "
            "do PO de novo). Se a decisão está errada, apague esta entrada "
            "(limpeza legítima) e abra um gate novo para o PO decidir de "
            "novo; não a reescreva no lugar."
        )

    # Aresta B: gate aberto que desaparece do conteúdo novo é auto-liberação
    # por deleção, sempre — "apagar não é decidir".
    deleted = _improperly_deleted_gates(open_gates_, new_gates)
    if deleted:
        names = ", ".join(
            f"{g.get('checkpoint', '?')}" + (f"/{g['pbi']}" if g.get("pbi") else "")
            for g in deleted
        )
        block(
            f"[spec-gate] GATE ABERTO DELETADO BLOQUEADO ({names}). Apagar ou "
            "omitir do JSON um gate que aguarda o PO é auto-liberação por "
            "deleção, mesmo com turno humano presente: a decisão legítima MUDA "
            "o status do gate (aprovado/reprovado), preservando a entrada como "
            "registro — nunca a apaga. 'Apagar não é decidir'."
        )

    offenders = _decided_without_turn(cwd, open_gates_, previous, new_gates)
    if not offenders:
        return
    names = ", ".join(
        f"{g.get('checkpoint', '?')}"
        + (f"/{g['pbi']}" if g.get("pbi") else "")
        for g in offenders
    )
    block(
        f"[spec-gate] AUTO-LIBERAÇÃO BLOQUEADA ({names}). Nenhuma mensagem do "
        "PO chegou desde que este gate abriu, então a decisão dele não existe "
        "e não pode ser registrada. Este bloqueio é o sistema funcionando: "
        "apresente a decisão pendente ao PO e aguarde a resposta real."
    )


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)

    if not isinstance(payload, dict):
        # Payload que não é um objeto JSON (lista, número, string, null...)
        # não tem .get(...) — qualquer acesso abaixo levantaria AttributeError
        # não capturado. Sem um payload no formato esperado não há o que
        # avaliar: fail-open.
        sys.exit(0)

    # TUDO que roda a partir daqui — extração de campos, load_config, e os
    # guards propriamente ditos — precisa estar dentro do try/except: este é
    # um hook BLOQUEANTE, e o contrato do módulo é "qualquer erro interno
    # resulta em exit 0", sem exceção de fase nenhuma do processamento.
    try:
        tool = payload.get("tool_name", "")
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}

        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            # cwd ausente ou de tipo inesperado (list/int/dict/None): não dá
            # para confiar nele para os.path.join. Cai no cwd real do
            # processo, que é sempre uma string válida.
            cwd = os.getcwd()

        cfg = load_config(cwd)
        if cfg is None:
            sys.exit(0)  # projeto não usa spec-gate; guard totalmente inerte

        guard_seq_lock(tool, tool_input, cwd)
        guard_hook_state_lock(tool, tool_input, cwd)
        guard_batch_lock(tool, tool_input, cwd)
        guard_po_gate(tool, tool_input, cwd)
        guard_gate_clear(tool, tool_input, cwd)
        phase, phase_pbi = parse_phase(current_phase(cwd))
        if phase == "testing":
            guard_testing_phase(tool, tool_input, cwd, cfg)
        if phase == "implementing":
            guard_attempts(tool, tool_input, cwd, cfg, phase_pbi)
        # Os dois guards da entrada da implementação, na ordem do mais barato
        # para o mais caro. Ambos vêm depois de `guard_po_gate` de propósito:
        # com gate aberto a transição já está bloqueada, e rodar a suíte
        # inteira para descobrir isso seria pagar o custo mais caro do guard
        # por uma transição que não vai acontecer de qualquer jeito.
        guard_trace(tool, tool_input, cwd, cfg)
        guard_red_evidence(tool, tool_input, cwd, cfg)
        # O freeze precisa de um FATO EM DISCO, não de narrativa: o hook só
        # enxerga arquivos. Antes do Gate PO 1 a spec ainda está sendo
        # escrita pelo spec-analyst e não é contrato; depois dele, é.
        if _safe_gate_po_1_passed(cwd):
            guard_spec_lock(tool, tool_input, cwd, cfg)
        if tool == "Bash":
            guard_destructive(tool_input, cwd, cfg)
            guard_regression(tool_input, cwd, cfg)
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)

    # Avisos só saem quando nenhum guard bloqueou: um `block()` já levou a
    # mensagem mais importante ao agente por stderr, e somar um aviso a ela
    # seria competir com o próprio bloqueio pela atenção.
    if _NOTICES:
        try:
            sys.stdout.write(json.dumps({"systemMessage": "\n".join(_NOTICES)}))
        except Exception:
            pass
    sys.exit(0)


if __name__ == "__main__":
    main()
