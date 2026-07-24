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
PHASE_REL = os.path.join(".specgate", "phase")
GATE_REL = os.path.join(".specgate", "gate.json")
SEQ_REL = os.path.join(".specgate", "seq")


def guard_spec_lock(tool, tool_input, cwd, cfg):
    spec_paths = cfg.get("spec_paths", ["docs/backlog"])
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
    if current_branch(cwd).startswith("parked/"):
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
    if inline is None:
        inline = _interpreter_heredoc_code(cmd, tokens)
    if inline is not None:
        return [c for c in _inline_write_candidates(inline) if c and not c.startswith("-")]

    if not any(rx.search(cmd) for rx in BASH_WRITE_RES):
        return []
    targets = [t for t in tokens[1:] if not t.startswith("-")]
    # `dd of=arquivo` (e `if=arquivo`) colam o caminho depois do `=` num
    # único token — ele nunca aparece sozinho na lista acima. Oferecemos
    # também o valor de qualquer token `chave=valor` como candidato extra,
    # sem remover o token original (preferimos o falso positivo).
    for t in list(targets):
        _, eq, val = t.partition("=")
        if eq and val:
            targets.append(val)
    return targets


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


def _gate_po_1_passed_seguro(cwd):
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
    if not any(_same_file(t, cwd, SEQ_REL) for t in write_targets(tool, tool_input)):
        return
    block(
        "[spec-gate] ESCRITA EM .specgate/seq BLOQUEADA. Este contador de "
        "turnos é mantido pelo sistema (o hook log_event.py, disparado no "
        "evento UserPromptSubmit) e não deve ser escrito pelo agente: "
        "escrevê-lo à mão forjaria a prova de que o usuário falou, da qual "
        "depende o gate de PO. NÃO tente contornar por outro caminho — se "
        "você precisa que o usuário fale, peça e aguarde a resposta real."
    )


def guard_po_gate(tool, tool_input, cwd):
    """Chokepoint: com gate de PO aberto, a transição de fase fica travada.

    Toda transição de fase LEGÍTIMA passa por escrita em .specgate/phase
    reconhecida por `write_targets`, então bloquear esse arquivo encarece
    o fluxo de avançar sem decisão do PO — é a mesma camada de fricção
    descrita em "MODELO DE CAMADAS" acima de `write_targets`, não uma
    barreira à prova de qualquer comando Bash.
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


def _anterior_decidido_da_chave(anteriores_da_chave):
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
    for a in anteriores_da_chave:
        if a.get("status") != "aguardando-po":
            return a
    return None


def _mutacao_invalida(cwd, anteriores, novos):
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
    anteriores_por_chave = {}
    for g in anteriores:
        anteriores_por_chave.setdefault(_gate_key(g), []).append(g)

    seq_atual = _seq_atual_seguro(cwd)
    ofensores_abertura = []
    ofensores_decisao = []
    ofensores_decidido = []
    for g in novos:
        chave = _gate_key(g)
        anteriores_da_chave = anteriores_por_chave.get(chave, [])
        anterior_aberto = _anterior_aberto_da_chave(anteriores_da_chave)
        anterior_aberto_seq = (
            _opened_at_seq_int(anterior_aberto) if anterior_aberto is not None else None
        )
        anterior_decidido = _anterior_decidido_da_chave(anteriores_da_chave)
        opened_at_seq = _opened_at_seq_int(g)

        if anterior_decidido is not None:
            # Categoria 2: a chave já era um registro congelado antes desta
            # escrita. Não importa o status novo declarado (decidido de
            # novo, ou 'aguardando-po') nem o valor de `opened_at_seq` —
            # só a identidade exata sobrevive. Isto é checado ANTES e
            # independente das ramificações de abertura/decisão abaixo,
            # porque uma chave decidida não deve mais entrar nelas: seria
            # tratar uma mutação de registro congelado como se fosse uma
            # abertura ou decisão legítimas.
            anterior_decidido_seq = _opened_at_seq_int(anterior_decidido)
            if (
                g.get("status") != anterior_decidido.get("status")
                or opened_at_seq != anterior_decidido_seq
            ):
                ofensores_decidido.append(g)
            continue

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

    return ofensores_abertura, ofensores_decisao, ofensores_decidido


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

        if anterior_aberto is None and chave in anteriores_por_chave:
            continue  # já decidida antes: territorio exclusivo da categoria 2

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

    MODELO UNIFICADO — toda escrita é validada chave a chave (checkpoint,
    pbi), comparando o conteúdo NOVO contra o estado ANTERIOR completo em
    disco. Cada chave cai em exatamente uma destas 5 categorias:

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
    ofensores_abertura, ofensores_decisao_seq, ofensores_decidido = _mutacao_invalida(
        cwd, anteriores, novos
    )
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
    if ofensores_decidido:
        nomes = ", ".join(
            f"{g.get('checkpoint', '?')}" + (f"/{g['pbi']}" if g.get("pbi") else "")
            for g in ofensores_decidido
        )
        block(
            f"[spec-gate] GATE DECIDIDO É IMUTÁVEL BLOQUEADO ({nomes}). Uma "
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
        guard_po_gate(tool, tool_input, cwd)
        guard_gate_clear(tool, tool_input, cwd)
        phase = current_phase(cwd)
        if phase == "testing":
            guard_testing_phase(tool, tool_input, cwd, cfg)
        # O freeze precisa de um FATO EM DISCO, não de narrativa: o hook só
        # enxerga arquivos. Antes do Gate PO 1 a spec ainda está sendo
        # escrita pelo spec-analyst e não é contrato; depois dele, é.
        if _gate_po_1_passed_seguro(cwd):
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
