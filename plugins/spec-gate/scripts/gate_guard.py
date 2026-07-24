#!/usr/bin/env python3
"""spec-gate PreToolUse guard.

Dois gates mecânicos, ambos inertes a menos que o projeto tenha .specgate.json:

1. Fase de testes (arquivo .specgate/phase contém "testing"):
   bloqueia leitura de código-fonte (source_paths) por Read, Grep, Glob
   e por comandos Bash que inspecionam arquivos. Garante o black-box.

2. Gate de regressão: intercepta `git commit` e `git merge` no Bash,
   roda o test_command configurado e bloqueia se a suíte falhar.

Protocolo de hook do Claude Code: JSON no stdin; exit 0 permite,
exit 2 bloqueia e envia o stderr de volta ao Claude como feedback.
Qualquer erro interno do guard resulta em exit 0 (nunca quebrar a sessão).
"""
import json
import os
import re
import shlex
import subprocess
import sys

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
]
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
PHASE_REL = os.path.join(".specgate", "phase")
GATE_REL = os.path.join(".specgate", "gate.json")


def guard_spec_lock(tool, tool_input, cwd, cfg):
    spec_paths = cfg.get("spec_paths", ["SPEC.md", "docs/backlog"])
    spec_dirs = norm_paths(cwd, spec_paths)
    if not spec_dirs:
        return
    reason = (
        "[spec-gate] SPEC CONGELADA: o pipeline está em execução e a spec é o "
        "contrato que julga o trabalho, então ela não pode ser alterada por quem "
        "está sendo julgado ({alvo}). Se você acredita que a spec está errada, "
        "PARE o pipeline e apresente o caso ao usuário: o que a spec diz, o que "
        "você encontrou, e qual mudança propõe. Só o usuário altera o contrato."
    )
    if tool in ("Write", "Edit"):
        candidate = tool_input.get("file_path") or tool_input.get("path")
        if isinstance(candidate, str) and touches_source(candidate, cwd, spec_dirs):
            block(reason.format(alvo=candidate))
    elif tool == "Bash":
        cmd = tool_input.get("command", "")
        if not isinstance(cmd, str) or not cmd:
            return
        if not any(rx.search(cmd) for rx in BASH_WRITE_RES):
            return
        try:
            tokens = shlex.split(cmd, posix=True)
        except ValueError:
            tokens = cmd.split()
        for t in tokens[1:]:
            if t.startswith("-"):
                continue
            if touches_source(t, cwd, spec_dirs):
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
    path = os.path.join(cwd, ".specgate", "phase")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


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


def guard_testing_phase(tool, tool_input, cwd, cfg):
    source_dirs = norm_paths(cwd, cfg.get("source_paths", ["src"]))
    if not source_dirs:
        return

    reason = (
        "[spec-gate] Fase de testes black-box ativa: leitura de código-fonte "
        "bloqueada ({alvo}). Escreva os testes apenas a partir do SPEC.md. "
        "Se a spec não bastar, registre a lacuna em 'Ambiguidades encontradas' "
        "em vez de inspecionar a implementação."
    )

    if tool in READ_LIKE_TOOLS:
        candidates = [
            tool_input.get("file_path"),
            tool_input.get("path"),
            tool_input.get("pattern") if tool == "Glob" else None,
        ]
        for c in candidates:
            if isinstance(c, str) and touches_source(c, cwd, source_dirs):
                block(reason.format(alvo=c))

    elif tool == "Bash":
        cmd = tool_input.get("command", "")
        if not isinstance(cmd, str) or not cmd:
            return
        try:
            tokens = shlex.split(cmd, posix=True)
        except ValueError:
            tokens = cmd.split()
        if not tokens:
            return
        has_reader = any(os.path.basename(t) in BASH_READ_CMDS for t in tokens)
        if not has_reader:
            return
        for t in tokens[1:]:
            if t.startswith("-"):
                continue
            if touches_source(t, cwd, source_dirs):
                block(reason.format(alvo=t))


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
        "Exceção: no modo backlog, reverter APENAS os arquivos do item pulado é "
        "permitido via checkout/restore de caminhos específicos, nunca do repositório "
        "inteiro."
    )


def guard_regression(tool_input, cwd, cfg):
    cmd = tool_input.get("command", "")
    if not isinstance(cmd, str) or not COMMIT_RE.search(cmd):
        return
    if "--dry-run" in cmd:
        return  # não altera o repo; não faz sentido rodar a suíte
    test_command = cfg.get("test_command")
    if not test_command:
        return
    timeout = int(cfg.get("test_timeout_seconds", 600))
    try:
        proc = subprocess.run(
            test_command, shell=True, cwd=cwd, capture_output=True,
            text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        block(
            "[spec-gate] Gate de regressão: a suíte de testes excedeu o tempo "
            f"limite de {timeout}s. Commit bloqueado. Investigue antes de commitar."
        )
        return
    if proc.returncode != 0:
        tail_out = (proc.stdout or "")[-2000:]
        tail_err = (proc.stderr or "")[-1000:]
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


# MODELO DE CAMADAS, com garantias diferentes — leia isto antes de "completar"
# a heurística abaixo ou de julgá-la insegura:
#
# 1) CAMADA DE FRICÇÃO — é esta função (write_targets) e todo o parsing de
#    comando Bash que ela faz, incluindo o reconhecimento de interpretador
#    logo acima. Ela encarece o desvio ACIDENTAL: o caso real que ela cobre
#    é um modelo sob pressão racionalizando um atalho ("é só um `python -c`,
#    não é bem uma escrita de arquivo..."), não um adversário decidido a
#    burlar o guard de propósito. Por isso reconhecemos aqui invocação de
#    `python`/`python3 -c`, `node -e`/`--eval`, `perl -e`, `ruby -e`,
#    `php -r` e — os mais importantes — `sh -c`/`bash -c`, que são o desvio
#    mais provável de todos por serem o idioma natural da própria
#    ferramenta Bash do agente. Isto NÃO é sandbox e não pretende ser: quem
#    quiser burlar (escrever o caminho byte a byte, base64, um segundo
#    processo, etc.) burla. Não perseguimos esse alvo infinito aqui.
#
# 2) CAMADA FORTE — é outra: a validação do `seq` na escrita do gate.json
#    (`guard_gate_clear`, que consome `specgate_state.has_human_turn_since`),
#    que não depende de parsing nenhum, e sim de existir um turno REAL do
#    usuário registrado no log de eventos desde que o gate abriu. Nenhum
#    interpretador, disfarce de comando ou codificação contorna aquela,
#    porque o Claude não fabrica um UserPromptSubmit.
#
# Por isso "fechar o barato" (cobrir os interpretadores óbvios de escrita
# inline) é o nível de investimento certo para esta camada — não vale a
# pena, e seria enganoso, tentar transformar isto num parser de verdade.
#
# CAMINHO FUTURO DESCARTADO (para quem for mexer aqui depois): validar a
# legitimidade da fase no PONTO DE CONSUMO (quem lê .specgate/phase) em vez
# de proteger o arquivo na escrita esbarra no modelo de estacionamento
# (parking): com um gate aberto do PBI-03 estacionado, o fluxo precisa
# continuar livre para avançar o PBI-04, então bloquear ferramentas de
# trabalho globalmente enquanto houver QUALQUER gate aberto quebraria a
# fila. A versão por-PBI disso exigiria rastrear qual arquivo pertence a
# qual PBI, que é o redesign caro. Se um dia for necessário fechar essa
# parede de verdade (camada forte de verdade, não fricção), o caminho é
# esse rastreamento — não mais parsing de comando.
def write_targets(tool, tool_input):
    """Caminhos que esta chamada pretende escrever.

    Usado pelos guards que protegem arquivos de estado. Para Bash, devolve
    todos os tokens não-flag quando o comando tem cara de escrita — é
    grosseiro de propósito: preferimos um falso positivo (que o agente
    contorna explicando ao PO) a um falso negativo que fura o gate.
    """
    if tool in ("Write", "Edit"):
        c = tool_input.get("file_path") or tool_input.get("path")
        return [c] if isinstance(c, str) else []
    if tool != "Bash":
        return []
    cmd = tool_input.get("command", "")
    if not isinstance(cmd, str) or not cmd:
        return []
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        tokens = cmd.split()
    if not tokens:
        return []

    inline = _interpreter_inline_code(tokens)
    if inline is not None:
        return [c for c in _inline_write_candidates(inline) if c and not c.startswith("-")]

    if not any(rx.search(cmd) for rx in BASH_WRITE_RES):
        return []
    return [t for t in tokens[1:] if not t.startswith("-")]


def _same_file(candidate, cwd, rel):
    if not candidate:
        return False
    return os.path.realpath(os.path.join(cwd, os.path.expanduser(candidate))) == \
        os.path.realpath(os.path.join(cwd, rel))


def _open_gates_seguro(cwd):
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


def _read_gates_seguro(cwd):
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


def guard_po_gate(tool, tool_input, cwd):
    """Chokepoint: com gate de PO aberto, a transição de fase fica travada.

    Toda transição de fase passa por escrita em .specgate/phase, então
    bloquear esse arquivo impede fisicamente o fluxo de avançar.
    """
    gates = _open_gates_seguro(cwd)
    if not gates:
        return
    if not any(_same_file(t, cwd, PHASE_REL) for t in write_targets(tool, tool_input)):
        return
    nomes = ", ".join(str(g.get("checkpoint", "?")) for g in gates)
    block(
        f"[spec-gate] GATE DE PO ABERTO ({nomes}). O fluxo não avança de fase "
        "enquanto o PO não decidir. NÃO tente contornar o bloqueio nem editar "
        "o arquivo de fase por outro caminho. Apresente ao PO a decisão "
        "pendente, em uma linha e com opções concretas, e aguarde a resposta."
    )


def _gate_key(g):
    return (str(g.get("checkpoint", "")), str(g.get("pbi", "")))


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


def _has_human_turn_seguro(cwd, opened_at_seq):
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


def _seq_atual_seguro(cwd):
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


def _anterior_aberto_da_chave(anteriores_da_chave):
    """A entrada ANTERIOR desta chave cujo status era 'aguardando-po', se
    houver — None se a chave nunca existiu ou só existia já decidida.

    É o fato que distingue PRESERVAÇÃO real de reabertura disfarçada: só
    importa que a chave estava com o MESMO status logo antes desta escrita,
    nunca "existiu alguma vez com esse valor" (que é o que a versão antiga
    checava, e é exatamente o furo da chave decidida reaberta).
    """
    for a in anteriores_da_chave:
        if a.get("status") == "aguardando-po":
            return a
    return None


def _mutacao_invalida(cwd, anteriores, novos):
    """Impõe a imutabilidade de `opened_at_seq` de um gate aberto nas DUAS
    direções em que ele pode mudar de forma, devolvendo
    `(ofensores_abertura, ofensores_decisao)`.

    MODELO UNIFICADO: um gate aberto com `opened_at_seq = S` só ENTRA
    (abre/reabre) em 'aguardando-po' com `opened_at_seq >= seq_atual`
    (nunca no passado), e só SAI de 'aguardando-po' mudando de status e
    MANTENDO `opened_at_seq = S` — nunca alterando o valor na mesma escrita
    que decide.

    ofensores_abertura — entradas 'aguardando-po' em `novos` cuja
    abertura/reabertura é antedatada. PRESERVAÇÃO real (a única isenta da
    exigência `>= seq_atual`) exige as DUAS coisas: a chave já estava
    'aguardando-po' no estado anterior completo (`anteriores`, de
    `read_gates` — não só as abertas) E o `opened_at_seq` é IDÊNTICO ao
    anterior. Checar só o valor (sem o status anterior) é o furo antigo:
    permitia "preservar" com o `opened_at_seq` REBAIXADO frente ao valor de
    disco corrente sem cair na checagem — mas como o `seq` é monotônico e
    já alcançou o valor original na abertura, rebaixar nunca sobrevive à
    exigência `>= seq_atual` de qualquer forma. O furo que ISTO fecha de
    verdade é outro: uma chave já DECIDIDA (ex.: aprovado,
    opened_at_seq=10) reescrita de volta como 'aguardando-po' com o MESMO
    opened_at_seq=10 herdado do valor histórico — a checagem antiga via
    "valor bate com alguma entrada anterior da chave" e deixava passar como
    preservação, mesmo a entrada anterior estando DECIDIDA, não aberta.
    Isso reabria a janela sem exigir turno novo do PO, porque uma decisão
    seguinte validaria contra aquele `opened_at_seq` já ultrapassado.
    Qualquer caso que não seja a preservação exata cai na mesma regra de
    abertura: `opened_at_seq >= seq_atual`.

    ofensores_decisao — entradas DECIDIDAS (status != 'aguardando-po') em
    `novos` cuja chave estava 'aguardando-po' no estado anterior com um
    `opened_at_seq` DIFERENTE do declarado agora. O `opened_at_seq` é o
    carimbo temporal que a checagem de turno humano usa; alterá-lo (para
    baixo OU para cima) na MESMA escrita que decide reescreveria esse
    carimbo sem que o PO tenha visto o valor novo — rebaixar tornaria a
    aprovação forjável (o `has_human_turn_since` seguinte validaria contra
    um valor menor, mais fácil de satisfazer), e mesmo subir corrompe o
    registro que uma auditoria posterior confiaria.
    """
    anteriores_por_chave = {}
    for g in anteriores:
        anteriores_por_chave.setdefault(_gate_key(g), []).append(g)

    seq_atual = _seq_atual_seguro(cwd)
    ofensores_abertura = []
    ofensores_decisao = []
    for g in novos:
        chave = _gate_key(g)
        anteriores_da_chave = anteriores_por_chave.get(chave, [])
        anterior_aberto = _anterior_aberto_da_chave(anteriores_da_chave)
        anterior_aberto_seq = (
            _opened_at_seq_int(anterior_aberto) if anterior_aberto is not None else None
        )
        opened_at_seq = _opened_at_seq_int(g)

        if g.get("status") == "aguardando-po":
            if opened_at_seq is None:
                # Malformado (string/nulo/etc.): não dá para confiar no
                # valor. Lado seguro é bloquear, nunca supor que está ok.
                ofensores_abertura.append(g)
                continue
            if anterior_aberto is not None and anterior_aberto_seq == opened_at_seq:
                continue  # preservação genuína: nada a exigir
            if opened_at_seq < seq_atual:
                ofensores_abertura.append(g)
        else:
            if anterior_aberto is not None and opened_at_seq != anterior_aberto_seq:
                ofensores_decisao.append(g)

    return ofensores_abertura, ofensores_decisao


def _gates_deletados_indevidamente(abertos, novos):
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
    novos_chaves = {_gate_key(g) for g in novos}
    return [g for g in abertos if _gate_key(g) not in novos_chaves]


def _decided_without_turn(cwd, abertos, anteriores, novos):
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
    em disco (do `abertos`, ou de `anteriores` se a chave já existia mas não
    estava aberta) — nunca o valor autodeclarado no conteúdo novo (esse
    valor autodeclarado, quando a chave estava aberta, já foi validado à
    parte por `_mutacao_invalida`: se ele divergir do anterior, a escrita
    já terá sido bloqueada antes de chegar aqui).
    """
    abertos_por_chave = {_gate_key(g): g for g in abertos}
    anteriores_por_chave = {_gate_key(g): g for g in anteriores}

    novos_por_chave = {}
    for g in novos:
        novos_por_chave.setdefault(_gate_key(g), []).append(g)

    ofensores = []
    for chave in set(abertos_por_chave) | set(novos_por_chave):
        entradas = novos_por_chave.get(chave)
        anterior_aberto = abertos_por_chave.get(chave)

        if entradas is None:
            continue  # deleção: tratada por _gates_deletados_indevidamente

        todas_aguardando = all(e.get("status") == "aguardando-po" for e in entradas)
        if todas_aguardando:
            continue  # segue aberto (ou é abertura nova): nada foi decidido

        # É decisão: entrada única com status != aguardando-po, ou chave
        # duplicada ambígua onde nem tudo é aguardando-po.
        if chave not in anteriores_por_chave:
            # Chave nunca existiu no estado anterior completo: aprovação
            # fabricada do zero. Bloqueia sempre — não há gate anterior
            # cujo turno humano possa validar isto.
            ofensores.append(entradas[0])
            continue

        referencia = anterior_aberto if anterior_aberto is not None else anteriores_por_chave[chave]
        if not _has_human_turn_seguro(cwd, referencia.get("opened_at_seq", 0)):
            ofensores.append(referencia)

    return ofensores


def guard_gate_clear(tool, tool_input, cwd):
    """Impede o Claude de se auto-liberar escrevendo no gate.json.

    MODELO UNIFICADO: um gate aberto com `opened_at_seq = S` só sai de
    'aguardando-po' MUDANDO para um status decidido, MANTENDO
    `opened_at_seq = S`, e apenas se houve turno humano com `seq > S`.
    Nenhuma outra mutação de um gate aberto é permitida. E só ENTRA (abre
    ou reabre) em 'aguardando-po' com `opened_at_seq >= seq_atual` — nunca
    no passado. Isto se desdobra em quatro checagens, nesta ordem:

    1. Conteúdo indisponível (Edit/Bash) com QUALQUER gate aberto: bloqueia
       sempre — o fluxo legítimo usa Write com o JSON completo, e sem ver
       o conteúdo não há como confirmar decisão vs. deleção disfarçada.
    2. `_mutacao_invalida` (Aresta A): abertura/reabertura antedatada, e
       decisão que altera o `opened_at_seq` do gate que estava aberto.
    3. `_gates_deletados_indevidamente` (Aresta B): um gate aberto que
       desaparece do conteúdo novo é auto-liberação por deleção — SEMPRE,
       mesmo com turno humano. "Apagar não é decidir".
    4. `_decided_without_turn`: decisão sem turno humano REAL posterior ao
       `opened_at_seq` do gate (inclui chave fabricada do zero e chave
       duplicada ambígua). PAREDE mecânica: o Claude não fabrica um
       UserPromptSubmit, então isto é inviolável.

    LIMITE CONHECIDO: se a fala do PO SUSTENTA a decisão daquele gate
    específico, o hook não tem como saber — isso é semântica, e hook não lê
    semântica. Com dois gates abertos e um turno posterior a ambos, decidir
    os dois passa no hook. Segurar o gate não-respondido é instrução do
    comando /spec-gate mais honestidade do modelo, igual ao gatilho de
    granularidade. Documentado assim de propósito, sem inflar.
    """
    if not any(_same_file(t, cwd, GATE_REL) for t in write_targets(tool, tool_input)):
        return
    abertos = _open_gates_seguro(cwd)
    novos = _gates_from_content(tool, tool_input)

    if novos is None:
        # Conteúdo indisponível (Edit, ou Bash como `rm gate.json`,
        # `> gate.json`, `truncate`, `sed -i`...): o fluxo legítimo SEMPRE
        # usa Write com o JSON completo, então não há como confirmar que a
        # escrita resultante é uma decisão (mudança de status) e não uma
        # deleção/truncamento. Bloqueia sempre que existir QUALQUER gate
        # aberto — mesmo com turno humano presente, porque turno não prova
        # nada sobre o CONTEÚDO que este comando produz. Sem gate aberto,
        # não há nada aqui para proteger.
        if not abertos:
            return
        nomes = ", ".join(
            f"{g.get('checkpoint', '?')}" + (f"/{g['pbi']}" if g.get("pbi") else "")
            for g in abertos
        )
        block(
            f"[spec-gate] ESCRITA NÃO VERIFICÁVEL NO GATE BLOQUEADA ({nomes}). "
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
    anteriores = _read_gates_seguro(cwd)

    # Aresta A: abertura/reabertura antedatada, e decisão que altera o
    # opened_at_seq do gate aberto. Barra ANTES de examinar deleção/turno —
    # um opened_at_seq forjado (rebaixado, herdado de uma chave decidida,
    # ou trocado ao decidir) forjaria a aprovação seguinte, então isto é
    # checado por si só, com mensagens próprias.
    ofensores_abertura, ofensores_decisao_seq = _mutacao_invalida(cwd, anteriores, novos)
    if ofensores_abertura:
        nomes = ", ".join(
            f"{g.get('checkpoint', '?')}" + (f"/{g['pbi']}" if g.get("pbi") else "")
            for g in ofensores_abertura
        )
        block(
            f"[spec-gate] ABERTURA DE GATE ANTEDATADA BLOQUEADA ({nomes}). "
            "Um gate recém-aberto ou reaberto não pode declarar opened_at_seq "
            "menor que o seq atual do projeto — isso tornaria a aprovação "
            "seguinte forjável, porque o seq já teria 'ultrapassado' aquele "
            "valor por turnos ANTERIORES à abertura, sem exigir nenhuma fala "
            "nova do PO depois que o gate (re)abriu. Isto vale tanto para uma "
            "chave nova quanto para reabrir uma chave já DECIDIDA carimbada "
            "com o valor histórico: opened_at_seq de uma abertura/reabertura "
            "deve ser o seq atual (ou maior), nunca herdado do passado."
        )

    if ofensores_decisao_seq:
        nomes = ", ".join(
            f"{g.get('checkpoint', '?')}" + (f"/{g['pbi']}" if g.get("pbi") else "")
            for g in ofensores_decisao_seq
        )
        block(
            f"[spec-gate] DECISÃO ALTERA opened_at_seq BLOQUEADA ({nomes}). O "
            "opened_at_seq de um gate aberto é imutável ao decidir: precisa "
            "continuar exatamente igual ao valor registrado na abertura. "
            "Mudá-lo (para baixo ou para cima) na MESMA escrita que decide "
            "reescreveria o carimbo temporal que a checagem de turno humano "
            "usa — rebaixar tornaria a aprovação forjável, e mesmo subir "
            "corrompe o registro que uma auditoria posterior confiaria."
        )

    # Aresta B: gate aberto que desaparece do conteúdo novo é auto-liberação
    # por deleção, sempre — "apagar não é decidir".
    deletados = _gates_deletados_indevidamente(abertos, novos)
    if deletados:
        nomes = ", ".join(
            f"{g.get('checkpoint', '?')}" + (f"/{g['pbi']}" if g.get("pbi") else "")
            for g in deletados
        )
        block(
            f"[spec-gate] GATE ABERTO DELETADO BLOQUEADO ({nomes}). Apagar ou "
            "omitir do JSON um gate que aguarda o PO é auto-liberação por "
            "deleção, mesmo com turno humano presente: a decisão legítima MUDA "
            "o status do gate (aprovado/reprovado), preservando a entrada como "
            "registro — nunca a apaga. 'Apagar não é decidir'."
        )

    ofensores = _decided_without_turn(cwd, abertos, anteriores, novos)
    if not ofensores:
        return
    nomes = ", ".join(
        f"{g.get('checkpoint', '?')}"
        + (f"/{g['pbi']}" if g.get("pbi") else "")
        for g in ofensores
    )
    block(
        f"[spec-gate] AUTO-LIBERAÇÃO BLOQUEADA ({nomes}). Nenhuma mensagem do "
        "PO chegou desde que este gate abriu, então a decisão dele não existe "
        "e não pode ser registrada. Este bloqueio é o sistema funcionando: "
        "apresente a decisão pendente ao PO e aguarde a resposta real."
    )


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)

    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()

    cfg = load_config(cwd)
    if cfg is None:
        sys.exit(0)  # projeto não usa spec-gate; guard totalmente inerte

    try:
        guard_po_gate(tool, tool_input, cwd)
        guard_gate_clear(tool, tool_input, cwd)
        phase = current_phase(cwd)
        if phase == "testing":
            guard_testing_phase(tool, tool_input, cwd, cfg)
        if phase:
            guard_spec_lock(tool, tool_input, cwd, cfg)
        if tool == "Bash":
            guard_destructive(tool_input, cwd, cfg)
            guard_regression(tool_input, cwd, cfg)
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
