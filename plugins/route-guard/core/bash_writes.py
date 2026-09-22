"""Heurística: quais caminhos um comando Bash vai escrever.

Pega as formas comuns (redirecionamento, tee, sed -i, mv, cp, rm, touch, mkdir, git checkout/restore/
reset --hard/stash), seguindo `cd` e expandindo `~`.
Não é completa por natureza — um script Python que escreve arquivo passa. A auditoria do diff no
Stop é a segunda camada que pega o que escapar daqui.
"""

import os
import re
import shlex
from collections import namedtuple

WRITE = "write"
REMOVE = "remove"    # rm/rmdir: pode ser a reversão de um arquivo criado durante a rota
RESTORE = "restore"  # git restore / git checkout --: volta ao conteúdo commitado

Target = namedtuple("Target", "path kind")

SEPARATOR_CHARS = set(";&|\n")
REDIRECT = re.compile(r"^(?:\d|&)?>>?$")
# `>arq`, `2>>arq`, `&>arq` e `>&arq`; `>&2`, `2>&1` e `>&-` só duplicam/fecham descritores.
REDIRECT_JOINED = re.compile(r"^(?:\d|&)?>>?(&?)([^>&].*)$")
FD_DUP = re.compile(r"^(?:\d+|-)$")
# `<<EOF`, `<<-EOF`, `<<'EOF'`, `<<"EOF"`; `<<<` é here-string, não heredoc.
HEREDOC = re.compile(r"(?<!<)<<(-?)(?!<)\s*(['\"]?)([A-Za-z_]\w*)\2")
DEVICES = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty")
CHDIR = ("cd", "pushd")


def _strip_heredocs(command):
    """Remove o corpo dos heredocs: é texto, não comando (e um apóstrofo nele quebraria o lexer)."""
    lines, out, i = command.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        for match in HEREDOC.finditer(line):
            strip_tabs, word = match.group(1) == "-", match.group(3)
            while i < len(lines):
                body = lines[i]
                i += 1
                if (body.lstrip("\t") if strip_tabs else body) == word:
                    break
    return "\n".join(out)


def _segments(command):
    # `&` fora dos punctuation_chars para não partir `2>&1`; `&&` chega como token próprio entre espaços.
    # A quebra de linha separa comandos como o `;`, então sai do whitespace e vira pontuação.
    lexer = shlex.shlex(command.replace("\\\n", " "), posix=True, punctuation_chars=";|\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    segment = []
    for token in lexer:
        if token == "|" and segment and REDIRECT.match(segment[-1]):
            continue  # `>|` (noclobber): o alvo é o próximo token, não um pipe
        if set(token) <= SEPARATOR_CHARS:
            if segment:
                yield segment
            segment = []
        else:
            segment.append(token)
    if segment:
        yield segment


def _operands(args):
    """Argumentos que não são opção; depois de `--` tudo é operando."""
    out, after_dashdash = [], False
    for arg in args:
        if after_dashdash:
            out.append(arg)
        elif arg == "--":
            after_dashdash = True
        elif not arg.startswith("-"):
            out.append(arg)
    return out


SED_SCRIPT_FLAGS = ("-e", "-f", "--expression", "--file")


def _sed_files(args):
    """`sed -i 's/a/b/' arquivos...`: o primeiro operando é o script, salvo quando ele veio por -e/-f."""
    files, script_given, skip = [], False, False
    for arg in args:
        if skip:
            skip = False
        elif arg in SED_SCRIPT_FLAGS:
            script_given, skip = True, True
        elif arg.startswith(("--expression=", "--file=")):
            script_given = True
        elif not arg.startswith("-"):
            files.append(arg)
    return files if script_given else files[1:]


def _strip_redirects(tokens, targets):
    rest, i = [], 0
    while i < len(tokens):
        token = tokens[i]
        if REDIRECT.match(token) and i + 1 < len(tokens):
            targets.append(tokens[i + 1])
            i += 2
            continue
        joined = REDIRECT_JOINED.match(token)
        if joined and not (joined.group(1) and FD_DUP.match(joined.group(2))):
            targets.append(joined.group(2))
            i += 1
            continue
        rest.append(token)
        i += 1
    return rest


def _command_targets(tokens):
    # Prefixos que só envolvem o comando real.
    while tokens and (tokens[0] in ("sudo", "env", "command", "nohup", "time") or re.match(r"^\w+=", tokens[0])):
        tokens = tokens[1:]
    if not tokens:
        return []
    name, args = tokens[0].rsplit("/", 1)[-1], tokens[1:]
    operands = _operands(args)
    if name in ("rm", "rmdir", "unlink", "shred"):
        return _as(REMOVE, operands)
    if name in ("touch", "mkdir", "truncate", "tee", "mv"):
        return _as(WRITE, operands)
    if name in ("cp", "install", "ln", "rsync"):
        return _as(WRITE, operands[-1:])
    if name == "sed" and any(a.startswith("-i") or a.startswith("--in-place") for a in args):
        return _as(WRITE, _sed_files(args))
    if name == "dd":
        return _as(WRITE, [a[3:] for a in args if a.startswith("of=")])
    if name == "git" and args:
        return _git_targets(args[0], args[1:])
    return []


def _after_dashdash(args):
    return args[args.index("--") + 1:] if "--" in args else []


def _git_targets(sub, args):
    # Só volta ao commitado o que vem do HEAD/índice; de outra ref é escrita de conteúdo novo.
    if sub == "checkout" and "--" in args:
        from_ref = bool(_operands(args[:args.index("--")]))
        return _as(WRITE if from_ref else RESTORE, _after_dashdash(args))
    if sub == "restore":
        from_ref = any(a in ("-s", "--source") or a.startswith(("--source=", "-s")) for a in args)
        paths = [a for a in _operands(args) if a not in _source_values(args)] or ["."]
        return _as(WRITE if from_ref else RESTORE, paths)
    if sub == "reset" and "--hard" in args:
        return _as(RESTORE, ["."])
    if sub == "stash":
        action = args[0] if args and not args[0].startswith("-") else "push"
        if action in ("push", "save"):
            return _as(RESTORE, _after_dashdash(args) or ["."])
        if action in ("pop", "apply"):
            return _as(WRITE, ["."])
        return []
    if sub in ("rm", "mv", "clean"):
        return _as(WRITE, _operands(args) or ["."])
    return []


def _source_values(args):
    """Valores de `-s <ref>`/`--source <ref>`, que `_operands` não sabe que pertencem à opção."""
    return {args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-s", "--source")}


def _as(kind, paths):
    return [Target(p, kind) for p in paths]


def write_targets(command):
    """Alvos (caminho + tipo de escrita) que o comando parece tocar. None quando o comando é ilegível."""
    try:
        segments = list(_segments(_strip_heredocs(command)))
    except ValueError:
        return None
    # Um `cd` no meio do comando muda a base dos caminhos relativos dos segmentos seguintes.
    targets, workdir = [], ""
    for tokens in segments:
        redirect_targets = []
        rest = _strip_redirects(tokens, redirect_targets)
        for target in _as(WRITE, redirect_targets) + _command_targets(rest):
            if target.path and target.path not in DEVICES:
                targets.append(Target(_resolve(workdir, target.path), target.kind))
        if rest and rest[0] in CHDIR:
            destination = _operands(rest[1:])
            workdir = _resolve(workdir, destination[0] if destination else "~")
    return targets


def _resolve(workdir, path):
    """`~` expandido e caminho relativo ancorado no último `cd`; absoluto fica como está."""
    return os.path.join(workdir, os.path.expanduser(path))
