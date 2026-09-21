#!/usr/bin/env python3
"""Resolve o nível do /code-review-legacy e diz à sessão qual skill de nível chamar.

O esforço do fork só vem do `effort:` fixo do frontmatter numa skill de plugin — o
`getEffort()` que o /code-review embutido usa para variar o esforço pelo nível não
existe para skills do usuário. Por isso o nível escolhe uma das 5 skills ocultas,
cada uma com o próprio `effort:`, em vez de ser só um argumento da receita.

Porta de `nt()` (nível no primeiro token que não é flag), `Dn()` (digitado → último
digitado → esforço da sessão → medium) e dos avisos de `bs()` do embutido (2.1.278).

Uso (via injeção `!` do SKILL.md):
    route.py '<CLAUDE_PLUGIN_DATA>' '<CLAUDE_EFFORT>' '<argumentos crus do usuário>'
"""

import json
import os
import re
import sys
from collections import namedtuple

from build_prompt import LEVELS, clean_raw, is_flag

PLUGIN_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
LEVEL_SKILL_PREFIX = "code-review-legacy-"
LAST_LEVEL_FILE = "last_level"

# Porta de `cs`: token com cara de nível (3 primeiras letras de algum) que não é um nível válido.
LOOKS_LIKE_LEVEL = re.compile(r"^({})[a-z]*$".format("|".join(level[:3] for level in LEVELS)), re.IGNORECASE)

Route = namedtuple("Route", "level args source unrecognized")


def plugin_name():
    with open(os.path.join(PLUGIN_ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
        return json.load(fh)["name"]


def level_skill(level):
    return "{}:{}{}".format(plugin_name(), LEVEL_SKILL_PREFIX, level)


def read_last_level(data_dir):
    if not data_dir:
        return None
    try:
        with open(os.path.join(data_dir, LAST_LEVEL_FILE), encoding="utf-8") as fh:
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
    """Porta dos avisos de `bs()` na variante inline (quem fala com o usuário é a sessão principal)."""
    change_hint = "typing a level (for example `/code-review-legacy high`) changes it"
    tell = "({} Tell the user this in one short line as you begin, including that {}.)\n\n"
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


def instruction(route):
    args = "args `{}`, exactly as shown".format(route.args) if route.args else "no args"
    return (
        "{notice}Invoke the Skill tool now with skill `{skill}` and {args}. Do nothing else in this turn: "
        "do not read the diff or review anything yourself. That skill runs the {level}-effort review in a forked "
        "background agent, and its findings arrive later as a task notification.\n"
    ).format(notice=notice(route), skill=level_skill(route.level), args=args, level=route.level)


def main(argv):
    if len(argv) != 4:
        print("Could not route the review: expected plugin data dir, session effort and arguments. "
              "Stop and tell the user the code-review-legacy skill is misconfigured.")
        return 0
    # Placeholder que o Claude Code não substituiu chega cru; tratar como ausente, não como caminho/valor.
    _, data_dir, session_effort, raw = (value if not value.startswith("${") else "" for value in argv)
    route = resolve(raw, data_dir, session_effort)
    if route.source == "explicit":
        write_last_level(data_dir, route.level)
    sys.stdout.write(instruction(route))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
