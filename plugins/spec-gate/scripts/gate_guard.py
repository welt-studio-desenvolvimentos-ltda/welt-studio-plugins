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
