"""Comandos do usuário: /route-guard:approve, /route-guard:status, /route-guard:off.

Chegam pela injeção `!` das skills (que têm `disable-model-invocation`), então só o usuário dispara.
O que é impresso vira o prompt que o Claude recebe.
"""

from core import repo, route, state

# Nome do plugin (igual ao de .claude-plugin/plugin.json, conferido em teste): prefixo dos comandos.
PLUGIN_NAME = "route-guard"

RELAY = "Relay this to the user in one or two short lines:\n\n"


def slash(name):
    """`/route-guard:<name>` de um comando de COMMANDS (cada um é uma skill em skills/<name>/)."""
    if name not in COMMANDS:
        raise KeyError("unknown route-guard command: {}".format(name))
    return "/{}:{}".format(PLUGIN_NAME, name)


def _describe(current, st):
    done = st.get("done", [])
    attempts = st.get("attempts", {})
    lines = []
    for step in current["steps"]:
        mark = "x" if step["id"] in done else " "
        extra = " ({} failed attempt(s))".format(attempts[step["id"]]) if attempts.get(step["id"]) else ""
        deps = " after {}".format(", ".join(step["depends_on"])) if step.get("depends_on") else ""
        lines.append("- [{}] {}{}{}".format(mark, route.task_subject(step), deps, extra))
        lines.append("  scope: {}".format(", ".join(step["scope"])))
        lines.append("  done when: {}".format(" && ".join(step["done_when"])))
    return "\n".join(lines)


def approve(route_file, state_file):
    st = state.load(state_file)
    if st is None or st.get("status") not in (state.DRAFT, state.ESCALATED):
        status = st.get("status") if st else "no route"
        return RELAY + "route-guard: nothing to approve (status: {}).".format(status)
    try:
        current = route.load(route_file)
    except route.RouteError as exc:
        return ("route-guard: the route was NOT approved — {}\n\nFix the route file `{}` and ask the user "
                "to run {} again.".format(exc, route_file, slash("approve")))
    step_ids = [s["id"] for s in current["steps"]]
    was_escalated = st["status"] == state.ESCALATED
    state.activate(st, step_ids, repo.snapshot(st["repo_root"]))
    state.save(state_file, st)
    unlocked = route.unlocked_steps(current, st["done"])
    next_step = route.task_subject(unlocked[0]) if unlocked else "none"
    header = "resumed after escalation" if was_escalated else "approved"
    return ("route-guard: route {}. Tell the user in one line, then continue with the next unlocked step "
            "({}). Make sure there is one task per step (`{}`).\n\n{}").format(
                header, next_step, route.SUBJECT_HINT, _describe(current, st))


def status(route_file, state_file):
    st = state.load(state_file)
    if st is None:
        return RELAY + "route-guard: no route in this session."
    lines = ["route-guard status: {}".format(st["status"])]
    if st.get("escalation"):
        lines.append("escalation: {}".format(st["escalation"]))
    try:
        lines.append(_describe(route.load(route_file), st))
    except route.RouteError as exc:
        lines.append("route: {}".format(exc))
    return "Show this to the user as is:\n\n" + "\n".join(lines)


def off(_route_file, state_file):
    st = state.load(state_file)
    if st is None or st.get("status") not in state.GUARDED:
        return RELAY + "route-guard: no active route to drop."
    st["status"] = state.ABANDONED
    state.save(state_file, st)
    return RELAY + "route-guard: route dropped; edits are no longer checked in this session."


COMMANDS = {"approve": approve, "status": status, "off": off}


def main(argv):
    if len(argv) != 4 or argv[1] not in COMMANDS or any(a.startswith("${") or not a for a in argv[2:]):
        print("route-guard: misconfigured command (expected <{}> <data dir> <session id>). "
              "Tell the user.".format("|".join(COMMANDS)))
        return 0
    command, data_dir, session_id = argv[1:]
    try:
        route_file = route.route_path(data_dir, session_id)
        state_file = route.state_path(data_dir, session_id)
    except route.RouteError as exc:
        print("route-guard: {}. Tell the user.".format(exc))
        return 0
    try:
        print(COMMANDS[command](route_file, state_file))
    except Exception as exc:  # noqa: BLE001 — git lento/ausente no snapshot não pode virar traceback no prompt
        print("route-guard: the command failed ({}: {}). Tell the user.".format(type(exc).__name__, exc))
    return 0
