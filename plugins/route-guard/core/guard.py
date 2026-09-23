"""Decisões dos hooks, separadas do I/O para serem testáveis.

Cada função recebe o JSON de entrada do hook e o diretório de dados do plugin e devolve o que o
hook deve emitir. Sem rota na sessão (ou rota encerrada), nada é emitido.
"""

import locale
import os
import subprocess
import time
from collections import namedtuple

from core import bash_writes, commands, repo, route, state

# Script dos comandos /route-guard:*. Só o usuário o dispara, pela injeção `!` da skill, que não
# passa pelo PreToolUse; se o Claude tentar rodá-lo pelo Bash, o PreToolUse barra.
CLI_SCRIPT = "route_guard_cli.py"

CRITERION_TIMEOUT = 300
# Tempo total dos critérios de um passo: abaixo do timeout do hook TaskCompleted (hooks.json), para o
# hook nunca ser morto no meio e a conclusão passar sem veredito.
CRITERIA_BUDGET = 1500
OUTPUT_TAIL = 1500

# exit_code 2 + stderr barra o evento; stdout vai como JSON quando houver.
Result = namedtuple("Result", "exit_code stdout stderr")
ALLOW = Result(0, None, None)

Session = namedtuple("Session", "session_id route_file state_file state")


def _session(inp, data_dir):
    session_id = inp.get("session_id")
    try:
        route_file = route.route_path(data_dir, session_id)
        state_file = route.state_path(data_dir, session_id)
    except route.RouteError:
        return None
    return Session(session_id, route_file, state_file, state.load(state_file))


def _guarded_session(inp, data_dir):
    """A sessão quando há rota sob guarda; None quando não há rota ou ela já foi encerrada."""
    session = _session(inp, data_dir)
    if session is None or session.state is None or session.state.get("status") not in state.GUARDED:
        return None
    return session


def _route_format(route_file, repo_root):
    return (
        "Write it with the Write tool to `{route_file}` as JSON:\n"
        '{{"steps": [{{"id": "1", "title": "...", "scope": ["src/foo/**", "tests/test_foo.py"], '
        '"done_when": ["python3 -m unittest tests.test_foo"], "depends_on": []}}]}}\n'
        "- `scope`: globs relative to the repo root `{repo_root}` (`**` crosses directories, a trailing `/` "
        "means everything inside) covering every file the step may create, modify or delete, tests and docs included.\n"
        "- `done_when`: shell commands, run from the repo root, that exit 0 only when the step is really done "
        "(tests, build, lint, a grep that proves the change). `true`/`echo` are rejected.\n"
        "- `depends_on`: ids of steps that must be done first.\n"
        "- optional top-level `generated`: globs of build/test outputs this project produces (common caches "
        "like `__pycache__` and `node_modules` are already ignored).\n"
        "Cover the whole plan and nothing beyond it."
    ).format(route_file=route_file, repo_root=repo_root)


# --- PostToolUse(ExitPlanMode) ---------------------------------------------------------------

def on_plan_approved(inp, data_dir):
    session = _session(inp, data_dir)
    if session is None:
        return ALLOW
    tool_input = inp.get("tool_input") or {}
    root = repo.repo_root(inp.get("cwd") or os.getcwd())
    draft = state.new_draft(tool_input.get("planFilePath"), root, session.state)
    state.save(session.state_file, draft)
    state.prune(route.routes_dir(data_dir), session.session_id, root)
    lines = [
        "route-guard: the plan was approved. Before editing any project file, turn it into a route.",
        "1. " + _route_format(session.route_file, root),
        "2. Create one task per step with TaskCreate, subject exactly `{}`.".format(route.SUBJECT_HINT),
        "3. Show the route to the user and ask them to run `{}`. Edits to project files "
        "are blocked until they do; you cannot approve it yourself.".format(commands.slash("approve")),
        "While executing: mark a step's task completed only when the step is done — route-guard runs its "
        "`done_when` commands and rejects the completion if any fails. Editing files outside the scope of "
        "an unlocked step is blocked.",
    ]
    if os.path.exists(session.route_file):
        lines.append("A route from an earlier plan already exists at that path: update it to match the new "
                     "plan. Steps already done: {}.".format(", ".join(draft["done"]) or "none"))
    return Result(0, {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                             "additionalContext": "\n".join(lines)}}, None)


# --- PreToolUse -------------------------------------------------------------------------------

FILE_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}


def _deny(reason):
    return Result(0, {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": "route-guard: " + reason}}, None)


def _same_path(a, b):
    return os.path.realpath(a) == os.path.realpath(b)


def pre_tool(inp, data_dir):
    session = _guarded_session(inp, data_dir)
    if session is None:
        return ALLOW
    tool = inp.get("tool_name")
    tool_input = inp.get("tool_input") or {}
    cwd = inp.get("cwd") or os.getcwd()
    st = session.state
    status = st["status"]

    if tool == "Bash":
        command = tool_input.get("command") or ""
        if CLI_SCRIPT in command:
            return _deny("the route commands are for the user only; ask them to run the slash command.")
        if os.path.realpath(route.routes_dir(data_dir)) in command or route.routes_dir(data_dir) in command:
            return _deny("do not touch the route files from Bash; write the route with the Write tool "
                         "and read it with the Read tool.")
        targets = bash_writes.write_targets(command)
        if not targets:
            # Sem escrita reconhecível (ou comando ilegível): a auditoria do Stop cobre.
            return ALLOW
    elif tool in FILE_TOOLS:
        targets = [bash_writes.Target(tool_input.get(FILE_TOOLS[tool]) or "", bash_writes.WRITE)]
    else:
        return ALLOW

    for target in targets:
        absolute = target.path if os.path.isabs(target.path) else os.path.join(cwd, target.path)
        if _same_path(absolute, session.state_file):
            return _deny("the route state is managed by route-guard.")
        if _same_path(absolute, session.route_file):
            if status in state.ROUTE_EDITABLE:
                continue
            return _deny("the approved route is locked. If the plan must change, stop and ask the user "
                         "(re-entering plan mode starts a new route).")
        if os.path.realpath(absolute).startswith(os.path.realpath(route.routes_dir(data_dir)) + os.sep):
            return _deny("only the route file of this session can be written.")
        denial = _check_project_path(absolute, target.kind, cwd, st, session)
        if denial:
            return denial
    return ALLOW


def _load_route_or_none(session):
    try:
        return route.load(session.route_file)
    except route.RouteError:
        return None


def _is_revert(rel, kind, st):
    """Desfazer o que a rota mudou volta à baseline, então é sempre permitido.

    `git restore`/`checkout --` e `rm` de algo não rastreado, desde que nada ali estivesse sujo antes
    da rota: isso seria trabalho do usuário, não da rota. O que o git ignora (e o próprio `.git`) não
    entra na baseline, então remover isso nunca conta como reversão.
    """
    baseline = st.get("baseline")
    if kind not in (bash_writes.RESTORE, bash_writes.REMOVE) or not baseline or rel == ".":
        return False
    if any(d == rel or d.startswith(rel + "/") for d in baseline.get("dirty", {})):
        return False
    if kind == bash_writes.RESTORE:
        return True
    if rel == ".git" or rel.startswith(".git/"):
        return False
    root = st["repo_root"]
    return not repo.is_tracked(root, rel) and not repo.is_ignored(root, rel)


def _check_project_path(absolute, kind, cwd, st, session):
    rel = repo.relative_to(st["repo_root"], absolute, cwd)
    if rel is None:
        return None  # fora do repo: fora do alcance da rota
    current = _load_route_or_none(session)
    if route.matches(rel, route.generated_patterns(current)) or _is_revert(rel, kind, st):
        return None
    status = st["status"]
    if status == state.DRAFT:
        return _deny("the route is not approved yet. Write it to `{}`, show it to the user and ask them "
                     "to run `{}`.".format(session.route_file, commands.slash("approve")))
    if status == state.ESCALATED:
        return _deny("the route is escalated ({}). Stop and report to the user; edits resume after they "
                     "run `{}`.".format(st.get("escalation"), commands.slash("approve")))
    if status == state.DONE:
        return _deny("every route step is done. Further changes are outside the approved plan; ask the user.")
    if current is None:
        state.escalate(st, "route file unreadable")
        state.save(session.state_file, st)
        return _deny("the route file became unreadable; the route is escalated. Report to the user.")
    done = st.get("done", [])
    unlocked = route.unlocked_steps(current, done)
    finished = [s for s in current["steps"] if s["id"] in done]
    if route.matches(rel, route.scope_of(unlocked + finished)):
        return None
    locked = [s for s in route.open_steps(current, done) if s not in unlocked and route.matches(rel, s["scope"])]
    if locked:
        step = locked[0]
        pending = [d for d in step.get("depends_on", []) if d not in done]
        return _deny("`{}` belongs to step {} ({}), which waits for step(s) {}. Finish those first.".format(
            rel, step["id"], step["title"], ", ".join(pending)))
    return _deny("`{}` is outside the scope of every route step. Do not add work the plan does not "
                 "ask for; if the plan really needs this file, stop and ask the user to amend the route.".format(rel))


# --- TaskCompleted ----------------------------------------------------------------------------

def decode_output(data, fallback=None):
    """Saída de um critério em texto: UTF-8 quando válida; senão, o encoding do sistema.

    No Windows as ferramentas escrevem no encoding do sistema (cp1252), não em UTF-8.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(fallback or locale.getpreferredencoding(False), errors="replace")


def _run_criterion(command, root, timeout):
    try:
        proc = subprocess.run(command, shell=True, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "timed out after {:.0f}s".format(timeout)
    return proc.returncode == 0, "exit {}\n{}".format(proc.returncode, decode_output(proc.stdout)[-OUTPUT_TAIL:])


def _first_failing_criterion(commands, root):
    """(comando, saída) do primeiro critério que falha, ou None quando todos passam dentro do orçamento."""
    deadline = time.monotonic() + CRITERIA_BUDGET
    for command in commands:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return command, "not run: the step's criteria exceeded the {}s budget".format(CRITERIA_BUDGET)
        passed, output = _run_criterion(command, root, min(CRITERION_TIMEOUT, remaining))
        if not passed:
            return command, output
    return None


def _status_gate(st):
    """Bloqueio do TaskCompleted pelo status da rota; None quando o passo pode ser avaliado."""
    if st["status"] == state.DRAFT:
        return Result(2, None, "route-guard: the route is not approved yet; the user must run "
                               "`{}` before steps can be completed.".format(commands.slash("approve")))
    if st["status"] == state.ESCALATED:
        return Result(2, None, "route-guard: the route is escalated ({}). Stop and report to the user.".format(
            st.get("escalation")))
    return None


def task_completed(inp, data_dir):
    step_id = route.step_id_from_subject(inp.get("task_subject"))
    if step_id is None:
        return ALLOW
    session = _guarded_session(inp, data_dir)
    if session is None:
        return ALLOW
    st = session.state
    blocked = _status_gate(st)
    if blocked:
        return blocked
    if step_id in st.get("done", []):
        return ALLOW
    try:
        current = route.load(session.route_file)
    except route.RouteError as exc:
        return Result(2, None, "route-guard: cannot read the route ({}).".format(exc))
    steps = route.steps_by_id(current)
    if step_id not in steps:
        return Result(2, None, "route-guard: there is no step {} in the route.".format(step_id))
    step = steps[step_id]
    pending = [d for d in step.get("depends_on", []) if d not in st.get("done", [])]
    if pending:
        return Result(2, None, "route-guard: step {} depends on step(s) {}, not done yet.".format(
            step_id, ", ".join(pending)))
    failure = _first_failing_criterion(step["done_when"], st["repo_root"])
    # Os critérios podem levar minutos: nesse meio-tempo outra conclusão ou um /route-guard:off pode ter
    # gravado o estado. O veredito é aplicado sobre o estado relido, não sobre o lido antes.
    st = state.load(session.state_file)
    if st is None or st.get("status") not in state.GUARDED:
        return ALLOW
    blocked = _status_gate(st)
    if blocked:
        return blocked
    if failure:
        command, output = failure
        escalated = state.record_failure(st, step_id)
        state.save(session.state_file, st)
        message = "route-guard: step {} is not done — `{}` failed:\n{}".format(step_id, command, output)
        if escalated:
            message += ("\nAttempt limit reached ({}). Stop, report to the user what is failing and why; "
                        "edits are blocked until they run `{}`.".format(state.MAX_ATTEMPTS, commands.slash("approve")))
        else:
            message += "\nFix it and complete the task again (attempt {} of {}).".format(
                st["attempts"][step_id], state.MAX_ATTEMPTS)
        return Result(2, None, message)
    state.mark_done(st, step_id, list(steps))
    state.save(session.state_file, st)
    return ALLOW


# --- Stop -------------------------------------------------------------------------------------

def _block(reason):
    return Result(0, {"decision": "block", "reason": "route-guard: " + reason}, None)


def _notify(message):
    return Result(0, {"systemMessage": "route-guard: " + message}, None)


def _parallel_routes_scope(data_dir, session, repo_root):
    """Escopo das rotas de outras sessões em andamento no mesmo repo.

    Duas sessões no mesmo repo veem o diff uma da outra; o que cabe na rota da outra é trabalho dela,
    e a auditoria dela é que confere.
    """
    patterns = []
    for other, current in state.others(route.routes_dir(data_dir), session.session_id):
        if current.get("status") not in state.GUARDED or current.get("repo_root") != repo_root:
            continue
        try:
            other_route = route.load(route.route_path(data_dir, other))
        except route.RouteError:
            continue
        patterns.extend(route.scope_of(other_route["steps"]))
    return patterns


def stop(inp, data_dir):
    session = _guarded_session(inp, data_dir)
    if session is None:
        return ALLOW
    st = session.state
    status = st["status"]
    retrying = bool(inp.get("stop_hook_active"))

    if status == state.DRAFT:
        if not os.path.exists(session.route_file) and not retrying:
            return _block("the plan was approved but the route was not written. " +
                          _route_format(session.route_file, st["repo_root"]) +
                          "\nThen ask the user to run `{}`.".format(commands.slash("approve")))
        return ALLOW
    if status == state.ESCALATED:
        return _notify("route escalated — {}. Review it and run {} to resume, or {} to drop the "
                       "route.".format(st.get("escalation"), commands.slash("approve"), commands.slash("off")))

    try:
        current = route.load(session.route_file)
    except route.RouteError as exc:
        state.escalate(st, "route file unreadable: {}".format(exc))
        state.save(session.state_file, st)
        return _notify("the route file became unreadable ({}); route escalated.".format(exc))

    allowed = (route.scope_of(current["steps"]) + route.generated_patterns(current)
               + _parallel_routes_scope(data_dir, session, st["repo_root"]))
    outside = [rel for rel in repo.changed_since(st["repo_root"], st.get("baseline"))
               if not route.matches(rel, allowed)]
    if outside:
        if not retrying:
            return _block("files changed outside the route: {}. Revert them (`git restore <file>`, or `rm` "
                          "for files the route created); if the plan really needs them, stop and ask the "
                          "user to amend the route.".format(", ".join(outside)))
        state.escalate(st, "files changed outside the route: {}".format(", ".join(outside)))
        state.save(session.state_file, st)
        return _notify("files changed outside the route ({}); route escalated. Review and run {} to "
                       "resume or {}.".format(", ".join(outside), commands.slash("approve"), commands.slash("off")))

    if status == state.DONE:
        st["status"] = state.CLOSED
        state.save(session.state_file, st)
        return _notify("every step passed its criteria and no file changed outside the route. Route closed.")

    remaining = route.open_steps(current, st.get("done", []))
    asking_user = (inp.get("last_assistant_message") or "").rstrip().endswith("?")
    if remaining and not retrying and not asking_user:
        return _block("route steps still open: {}. Continue with the next unlocked step; if you need a "
                      "decision from the user, ask it as a question.".format(
                          "; ".join(route.task_subject(s) for s in remaining)))
    return ALLOW
