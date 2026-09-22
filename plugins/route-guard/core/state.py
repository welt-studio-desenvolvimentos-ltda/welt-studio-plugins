"""Estado da rota por sessão: fica fora do arquivo da rota para o Claude não mexer nele ao editar a rota."""

import json
import os
import tempfile
import time

from core.route import ROUTE_SUFFIX, STATE_SUFFIX

DRAFT = "draft"          # plano aprovado; rota escrita pelo Claude, esperando o /route-guard:approve
ACTIVE = "active"        # rota aprovada; escopo e critérios valendo
ESCALATED = "escalated"  # limite de tentativas ou desvio; edições barradas até o usuário decidir
DONE = "done"            # todos os passos concluídos; falta a auditoria final do Stop
CLOSED = "closed"        # auditoria final limpa; hooks inertes
ABANDONED = "abandoned"  # /route-guard:off

# Estados em que o guard age sobre edições e paradas.
GUARDED = (DRAFT, ACTIVE, ESCALATED, DONE)
# Estados em que o Claude pode (re)escrever o arquivo da rota.
ROUTE_EDITABLE = (DRAFT, ESCALATED)

# Estados finais: a rota não protege mais nada.
FINISHED = (CLOSED, ABANDONED)

# Limpeza: rota encerrada some depois de 7 dias; rota em andamento parada, depois de 30 — mas só pela
# limpeza do mesmo projeto, para um projeto nunca apagar o trabalho em curso de outro.
FINISHED_TTL = 7 * 24 * 3600
STALE_TTL = 30 * 24 * 3600

# Falhas de critério no mesmo passo antes de escalar. Abaixo do teto de 8 bloqueios do Stop do Claude Code.
MAX_ATTEMPTS = 3


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def save(path, state):
    """Escrita atômica: hooks concorrentes nunca leem um JSON pela metade."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def new_draft(plan_file, repo_root, previous=None):
    """Plano (re)aprovado: volta para rascunho. Baseline e passos já feitos sobrevivem a uma emenda."""
    previous = previous if previous and previous.get("status") in GUARDED else {}
    return {
        "status": DRAFT,
        "plan_file": plan_file,
        "repo_root": repo_root,
        "baseline": previous.get("baseline"),
        "done": list(previous.get("done", [])),
        "attempts": {},
        "escalation": None,
    }


def activate(state, route_step_ids, baseline):
    state["status"] = ACTIVE
    if state.get("baseline") is None:
        state["baseline"] = baseline
    # Passo removido na emenda não conta mais como feito.
    state["done"] = [s for s in state.get("done", []) if s in route_step_ids]
    state["attempts"] = {}
    state["escalation"] = None
    if all(s in state["done"] for s in route_step_ids):
        state["status"] = DONE
    return state


def escalate(state, reason):
    state["status"] = ESCALATED
    state["escalation"] = reason
    return state


def record_failure(state, step_id):
    """Soma uma falha de critério; devolve True quando o limite foi atingido e a rota escalou."""
    attempts = state.setdefault("attempts", {})
    attempts[step_id] = attempts.get(step_id, 0) + 1
    if attempts[step_id] >= MAX_ATTEMPTS:
        escalate(state, "step {} failed its done_when criteria {} times".format(step_id, attempts[step_id]))
        return True
    return False


def mark_done(state, step_id, all_step_ids):
    if step_id not in state["done"]:
        state["done"].append(step_id)
    state.get("attempts", {}).pop(step_id, None)
    if all(s in state["done"] for s in all_step_ids):
        state["status"] = DONE
    return state


def others(directory, session_id):
    """(session_id, estado) das outras sessões com rota, lidos do diretório de rotas."""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    found = []
    for name in names:
        if not name.endswith(STATE_SUFFIX) or name.startswith("."):
            continue
        other = name[:-len(STATE_SUFFIX)]
        if other == session_id:
            continue
        current = load(os.path.join(directory, name))
        if current is not None:
            found.append((other, current))
    return found


def prune(directory, session_id, repo_root, now=None):
    """Apaga rotas velhas. Nunca a da sessão atual; em andamento, só as do mesmo projeto."""
    now = time.time() if now is None else now
    removed = []
    for other, current in others(directory, session_id):
        state_file = os.path.join(directory, other + STATE_SUFFIX)
        try:
            age = now - os.path.getmtime(state_file)
        except OSError:
            continue
        finished = current.get("status") in FINISHED
        same_project = current.get("repo_root") == repo_root
        if (finished and age > FINISHED_TTL) or (not finished and same_project and age > STALE_TTL):
            for path in (state_file, os.path.join(directory, other + ROUTE_SUFFIX)):
                try:
                    os.unlink(path)
                except OSError:
                    pass
            removed.append(other)
    return removed
