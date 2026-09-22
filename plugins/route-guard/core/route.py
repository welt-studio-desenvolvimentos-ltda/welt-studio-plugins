"""A rota: passos derivados do plano aprovado, cada um com escopo, critério de pronto e dependências.

Única declaração do formato da rota, dos caminhos por sessão e da etiqueta `[R<id>]` que liga
um passo à tarefa do `TaskCreate`.
"""

import json
import os
import re

ROUTES_DIR = "routes"
ROUTE_SUFFIX = ".json"
STATE_SUFFIX = ".state.json"

STEP_ID = r"[A-Za-z0-9._-]+"
# Assunto da tarefa que representa um passo: `[R<id>] <título>`. O formato, a dica mostrada ao Claude
# e o regex que reconhece a etiqueta saem todos daqui.
SUBJECT_FORMAT = "[R{id}] {title}"
SUBJECT_HINT = SUBJECT_FORMAT.format(id="<id>", title="<title>")
_TAG_OPEN, _TAG_REST = SUBJECT_FORMAT.split("{id}")
STEP_TAG = re.compile(r"^\s*{}({}){}".format(
    re.escape(_TAG_OPEN), STEP_ID, re.escape(_TAG_REST.split("{title}")[0].rstrip())))
STEP_ID_ONLY = re.compile(r"^{}$".format(STEP_ID))
# Critério que passa sempre não prova nada.
TRIVIAL_CRITERION = re.compile(r"^\s*(?:true|:|exit\s+0|echo\b.*|printf\b.*)\s*$")
# Artefatos que ferramentas geram ao rodar código e testes: não são trabalho, nem dentro nem fora da rota.
# O que for específico do projeto vai no campo opcional `generated` da rota.
DEFAULT_GENERATED = (
    "**/__pycache__/**", "**/*.pyc", "**/.pytest_cache/**", "**/.mypy_cache/**", "**/.ruff_cache/**",
    "**/node_modules/**", "**/.coverage", "**/coverage/**", "**/.tox/**", "**/*.egg-info/**",
)
# session_id vira nome de arquivo: nada que escape do diretório.
SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class RouteError(Exception):
    pass


def routes_dir(data_dir):
    return os.path.join(data_dir, ROUTES_DIR)


def route_path(data_dir, session_id):
    return os.path.join(routes_dir(data_dir), _checked_session(session_id) + ROUTE_SUFFIX)


def state_path(data_dir, session_id):
    return os.path.join(routes_dir(data_dir), _checked_session(session_id) + STATE_SUFFIX)


def _checked_session(session_id):
    if not isinstance(session_id, str) or not SESSION_ID.match(session_id):
        raise RouteError("invalid session id: {!r}".format(session_id))
    return session_id


def task_subject(step):
    return SUBJECT_FORMAT.format(id=step["id"], title=step["title"])


def step_id_from_subject(subject):
    match = STEP_TAG.match(subject or "")
    return match.group(1) if match else None


def validate(route):
    """Lista de problemas do formato; vazia quando a rota é aceitável."""
    if not isinstance(route, dict):
        return ["route must be a JSON object"]
    steps = route.get("steps")
    if not isinstance(steps, list) or not steps:
        return ["route.steps must be a non-empty list"]
    errors = []
    ids = []
    for index, step in enumerate(steps):
        where = "steps[{}]".format(index)
        if not isinstance(step, dict):
            errors.append("{} must be an object".format(where))
            continue
        step_id = step.get("id")
        if not isinstance(step_id, str) or not STEP_ID_ONLY.match(step_id):
            errors.append("{}.id must be a string of letters, digits, '.', '_' or '-'".format(where))
        elif step_id in ids:
            errors.append("{}.id '{}' is duplicated".format(where, step_id))
        else:
            ids.append(step_id)
        if not isinstance(step.get("title"), str) or not step["title"].strip():
            errors.append("{}.title must be a non-empty string".format(where))
        errors.extend(_non_empty_strings(step, "scope", where))
        errors.extend(_non_empty_strings(step, "done_when", where))
        for command in step.get("done_when") or []:
            if isinstance(command, str) and TRIVIAL_CRITERION.match(command):
                errors.append("{}.done_when '{}' always passes; use a command that proves the step".format(where, command))
        scope = step.get("scope")
        if isinstance(scope, list):
            errors.extend(_relative_globs([p for p in scope if isinstance(p, str)], where + ".scope"))
        deps = step.get("depends_on", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            errors.append("{}.depends_on must be a list of step ids".format(where))
    generated = route.get("generated", [])
    if not isinstance(generated, list) or not all(isinstance(g, str) and g.strip() for g in generated):
        errors.append("route.generated must be a list of globs")
    else:
        errors.extend(_relative_globs(generated, "generated"))
    if errors:
        return errors
    known = set(ids)
    for step in steps:
        for dep in step.get("depends_on", []):
            if dep not in known:
                errors.append("step '{}' depends on unknown step '{}'".format(step["id"], dep))
            elif dep == step["id"]:
                errors.append("step '{}' depends on itself".format(step["id"]))
    if not errors and _has_cycle(steps):
        errors.append("depends_on has a cycle")
    return errors


def _relative_globs(patterns, where):
    return ["{} '{}' must be relative to the repo root, without '..'".format(where, p)
            for p in patterns if os.path.isabs(p) or ".." in p.split("/")]


def _non_empty_strings(step, field, where):
    value = step.get(field)
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v.strip() for v in value):
        return ["{}.{} must be a non-empty list of non-empty strings".format(where, field)]
    return []


def _has_cycle(steps):
    deps = {s["id"]: list(s.get("depends_on", [])) for s in steps}
    visiting, done = set(), set()

    def visit(node):
        if node in done:
            return False
        if node in visiting:
            return True
        visiting.add(node)
        cyclic = any(visit(d) for d in deps[node])
        visiting.discard(node)
        done.add(node)
        return cyclic

    return any(visit(n) for n in deps)


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            route = json.load(fh)
    except OSError as exc:
        raise RouteError("route file not found: {}".format(path)) from exc
    except ValueError as exc:
        raise RouteError("route file is not valid JSON: {}".format(exc)) from exc
    errors = validate(route)
    if errors:
        raise RouteError("; ".join(errors))
    return route


def steps_by_id(route):
    return {s["id"]: s for s in route["steps"]}


def open_steps(route, done):
    return [s for s in route["steps"] if s["id"] not in done]


def unlocked_steps(route, done):
    """Passos ainda abertos cujas dependências já foram concluídas."""
    return [s for s in open_steps(route, done) if all(d in done for d in s.get("depends_on", []))]


def glob_to_regex(pattern):
    """Glob estilo gitignore relativo à raiz: `**` cruza diretórios, `*` e `?` não; `dir/` casa tudo dentro."""
    if pattern.endswith("/"):
        pattern += "**"
    out, i = [], 0
    while i < len(pattern):
        char = pattern[i]
        if pattern[i:] == "/**":
            # `dir/**` casa também o próprio `dir` (um `rmdir dir` está dentro do escopo).
            out.append("(?:/.*)?")
            i += 3
        elif pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif char == "*":
            out.append("[^/]*")
            i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(char))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def matches(rel_path, patterns):
    rel_path = rel_path.replace(os.sep, "/")
    return any(glob_to_regex(p).match(rel_path) for p in patterns)


def scope_of(steps):
    return [p for s in steps for p in s["scope"]]


def generated_patterns(route):
    return list(DEFAULT_GENERATED) + list(route.get("generated", [])) if route else list(DEFAULT_GENERATED)
