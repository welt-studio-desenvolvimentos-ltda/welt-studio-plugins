"""Argumentos e nível do /code-review-legacy.

Porta de `nn()`/`nt()` (flags e nível no primeiro token que não é flag), `Dn()`
(digitado → último digitado → esforço da sessão → medium) e dos avisos de `bs()`
do /code-review embutido (2.1.278).

O nível escolhe só a receita. O esforço de raciocínio do fork é o da sessão: numa
skill de plugin ele só viria de um `effort:` fixo no frontmatter, e o `getEffort()`
que o embutido usa para variá-lo pelo nível não existe para skills do usuário.
"""

import os
import re
from collections import namedtuple

LEVELS = ("low", "medium", "high", "xhigh", "max")

# Flags que `nt()` reconhece; o resto dos argumentos é o alvo.
KNOWN_FLAGS = ("comment", "fix", "post", "no-post")

SKILL_NAME = "code-review-legacy"
LAST_LEVEL_FILE = "last_level"

# Porta de `cs`: token com cara de nível (3 primeiras letras de algum) que não é um nível válido.
LOOKS_LIKE_LEVEL = re.compile(r"^({})[a-z]*$".format("|".join(level[:3] for level in LEVELS)), re.IGNORECASE)

Route = namedtuple("Route", "level args source unrecognized")


def clean_raw(raw):
    """Desfaz o que o Claude Code faz com `$ARGUMENTS` antes de rodar o comando `!` do SKILL.md."""
    rest = raw.strip()
    # `Ffe()` só substitui `$ARGUMENTS` quando a invocação traz args; sem eles o placeholder chega cru.
    if rest == "$ARGUMENTS":
        return ""
    # `uw()` escapa `!` em início de palavra: `!9` chega como `\!9`.
    # Desfazer aqui devolve o alvo digitado (o atalho `!N` de MR do GitLab depende disso).
    return re.sub(r"(^|\s)\\!", r"\1!", rest)


def is_flag(token):
    return token.startswith("--") and token[2:] in KNOWN_FLAGS


def read_last_level(data_dir):
    if not data_dir:
        return None
    try:
        # `errors="replace"`: arquivo corrompido com bytes inválidos vira valor fora de LEVELS, não exceção.
        with open(os.path.join(data_dir, LAST_LEVEL_FILE), encoding="utf-8", errors="replace") as fh:
            level = fh.read().strip()
    except OSError:
        return None
    return level if level in LEVELS else None


def write_last_level(data_dir, level):
    # Conveniência, como o `codeReviewLastEffort` do embutido: se não der para gravar, o review segue.
    if not data_dir:
        return
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, LAST_LEVEL_FILE), "w", encoding="utf-8") as fh:
            fh.write(level)
    except OSError:
        pass


def session_level(effort):
    """Porta de `Dn()` + `LN()`: sem esforço → medium; esforço que não é nível → high."""
    effort = effort.strip().lower()
    if not effort:
        return "medium"
    return effort if effort in LEVELS else "high"


def resolve(raw, data_dir, session_effort):
    tokens = clean_raw(raw).split()
    first = next((i for i, token in enumerate(tokens) if not is_flag(token)), None)
    if first is not None and tokens[first].lower() in LEVELS:
        level = tokens.pop(first).lower()
        return Route(level, " ".join(tokens), "explicit", None)
    word = tokens[first] if first is not None else ""
    unrecognized = word if word and LOOKS_LIKE_LEVEL.match(word) else None
    last = read_last_level(data_dir)
    if last is not None:
        return Route(last, " ".join(tokens), "last_used", unrecognized)
    return Route(session_level(session_effort), " ".join(tokens), "session", unrecognized)


def notice(route):
    """Porta dos avisos de `bs()` na variante fork (`willRunAsFork`): quem avisa o usuário é o relatório."""
    change_hint = "typing a level (for example `/{} high`) changes it".format(SKILL_NAME)
    tell = ("({} Open your report with one short line telling the user this, and that {}; "
            "that opening line reaches them with the findings.)\n\n")
    if route.unrecognized is not None:
        message = 'Ignoring unrecognized effort "{}"; valid: {}. Using {}{}.'.format(
            route.unrecognized, ", ".join(LEVELS), route.level,
            ", the level the user typed last time" if route.source == "last_used" else "")
        # Como no `bs()`: só pede para avisar o usuário quando há nível reaproveitado.
        return tell.format(message, change_hint) if route.source == "last_used" else "({})\n\n".format(message)
    if route.source == "last_used":
        message = "No effort level given — reusing {}, the level the user typed last time.".format(route.level)
        return tell.format(message, change_hint)
    return ""
