#!/usr/bin/env python3
"""Monta o prompt de revisão e a instrução que dispara o reviewer em background.

Porta de `ps()` do /code-review embutido (Claude Code 2.1.278), restrita às
células do Sonnet 5 — as receitas com fan-out de subagentes em todos os níveis.
O texto de cada trecho mora em `recipe/`; aqui só se decide o que entra.

A receita vai para um arquivo, não para a sessão principal: ela recebe só a instrução
de disparar o agente `reviewer` em background, que lê o arquivo e devolve o relatório final.

Uso (via injeção `!` do SKILL.md):
    build_prompt.py '<CLAUDE_PLUGIN_DATA>' '<CLAUDE_EFFORT>' '<CLAUDE_SESSION_ID>' '<argumentos crus do usuário>'
"""

import os
import re
import subprocess
import sys
import tempfile
import time
from collections import namedtuple

from level import LEVELS, KNOWN_FLAGS, REVIEWER_AGENT, SKILL_NAME, clean_raw, notice, resolve, write_last_level

RECIPE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "recipe")

CORRECTNESS_3 = ("angles/a_line_by_line", "angles/b_removed_behavior", "angles/c_cross_file")
CORRECTNESS_5 = CORRECTNESS_3 + ("angles/d_language_pitfall", "angles/e_wrapper_proxy")
CLEANUP = ("angles/reuse", "angles/simplification", "angles/efficiency", "angles/altitude", "angles/conventions")

Args = namedtuple("Args", "fix comment post target")


class PromptBuildError(Exception):
    pass


def load(name):
    path = os.path.join(RECIPE_DIR, name + ".md")
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise PromptBuildError("recipe fragment missing: {}".format(path)) from exc


def fragments(names):
    return "\n".join(load(n) for n in names)


def parse_args(raw):
    """Porta de `nn()` + `nt()` + `no()`: extrai flags e normaliza o alvo."""
    rest = clean_raw(raw)
    flags = set()
    for flag in KNOWN_FLAGS:
        stripped = re.sub(r"(?:^|\s)--{}(?=\s|$)".format(re.escape(flag)), "", rest)
        if stripped != rest:
            flags.add(flag)
            rest = stripped.strip()
    tokens = rest.split()
    if tokens:
        tokens[0] = tokens[0].replace("`", "")
        tokens[0] = re.sub(r"^#", "", tokens[0])
    target = " ".join(t for t in tokens if t)
    return Args(fix="fix" in flags, comment="comment" in flags, post="post" in flags, target=target)


def output(cap):
    return load("output").replace("{{cap}}", str(cap))


def one_agent_per_angle(angles):
    """Desvio deliberado do binário: o texto original permite agrupar ângulos, e o modelo agrupa."""
    return load("one_agent_per_angle").replace("{{count}}", str(len(angles)))


def recipe_medium_high(level):
    """`fn` (medium) e `_n` (high) com o Agent disponível."""
    if level == "medium":
        tag = "`medium effort → 3+5 angles × 6 candidates → 1-vote verify → ≤8 findings`"
        lead_in = ("You are reviewing for **precision** at medium effort: every finding you surface\n"
                   "should be one a maintainer would act on.")
        verify, cap = load("verify_precision"), 8
    else:
        tag = "`high effort → 3+5 angles × 6 candidates → 1-vote verify (recall-biased) → ≤10 findings`"
        lead_in = ("You are reviewing for **recall** at high effort: catch every real bug a careful\n"
                   "reviewer would catch in one sitting. At this level, catching real bugs matters\n"
                   "more than avoiding false positives. Err on the side of surfacing.")
        verify, cap = load("verify_recall"), 10
    return (
        "{tag}\n\n{lead_in}\n\n{phase0}\n"
        "## Phase 1 — Find candidates (3 correctness angles + 3 cleanup angles + 1 altitude angle + 1 conventions angle, up to 6 each)\n\n"
        "Run **8 independent finder angles** via the `Agent` tool. Each\n"
        "surfaces **up to 6 candidate findings** with `file`, `line`, a one-line\n"
        "`summary`, and a concrete `failure_scenario`. {fallback}\n\n"
        "{per_angle}\n"
        "{angles}\n{cleanup_shape}\n{pass_through}\n{verify}\n{output}"
    ).format(
        tag=tag, lead_in=lead_in, phase0=load("phase0_gather"), fallback=load("agent_fallback"),
        per_angle=one_agent_per_angle(CORRECTNESS_3 + CLEANUP), angles=fragments(CORRECTNESS_3 + CLEANUP),
        cleanup_shape=load("cleanup_shape"),
        pass_through=load("pass_through"), verify=verify, output=output(cap),
    )


def recipe_xhigh_max(level):
    """`bn("xhigh")` e `bn("max")` com o Agent disponível."""
    intensity = "maximum" if level == "max" else "extra-high"
    return (
        "`{level} effort → 5+5 angles × 8 candidates → 1-vote verify → sweep → ≤15 findings`\n\n"
        "You are reviewing for **recall** at {intensity} effort: catch every real bug. At\n"
        "this level, catching real bugs matters more than avoiding false positives — a\n"
        "missed bug ships. Err on the side of surfacing.\n\n{phase0}\n"
        "## Phase 1 — Find candidates (5 correctness angles + 3 cleanup angles + 1 altitude angle + 1 conventions angle, up to 8 each)\n\n"
        "Run **10 independent finder angles** via the `Agent` tool. Each\n"
        "surfaces **up to 8 candidate findings**. Do NOT let one angle's conclusions\n"
        "suppress another's — if two angles flag the same line for different reasons,\n"
        "record both. {fallback}\n\n"
        "{per_angle}\n"
        "{angles}\n{cleanup_shape}\n{verify}\n{single_vote}\n{sweep}\n{output}"
    ).format(
        level=level, intensity=intensity, phase0=load("phase0_gather"), fallback=load("agent_fallback"),
        per_angle=one_agent_per_angle(CORRECTNESS_5 + CLEANUP), angles=fragments(CORRECTNESS_5 + CLEANUP),
        cleanup_shape=load("cleanup_shape"),
        verify=load("verify_precision"), single_vote=load("recall_single_vote"),
        sweep=load("sweep"), output=output(15),
    )


def recipe(level):
    if level == "low":
        return load("low")
    if level in ("medium", "high"):
        return recipe_medium_high(level)
    return recipe_xhigh_max(level)


GITLAB_MR_URL = re.compile(r"^(https?://[^/\s]+(?::\d{1,5})?(?:/[^/\s]+)+)/-/merge_requests/\d")


def origin_host():
    try:
        proc = subprocess.run(["git", "remote", "get-url", "origin"], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=5, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    match = re.match(r"^(?:[\w+]+://)?(?:[^@/]*@)?([^/:]+)", proc.stdout.strip())
    return match.group(1).lower() if match else None


def is_gitlab_target(target_head, host=None):
    """Porta de `yfr()`: URL de MR ou `!N` é GitLab; outra URL não; senão, olha o origin."""
    if GITLAB_MR_URL.match(target_head) or re.match(r"^!\d+$", target_head):
        return True
    if re.match(r"^https?://", target_head, re.IGNORECASE):
        return False
    host = host if host is not None else origin_host()
    return host is not None and "gitlab" in host


def comment_section(target, host=None):
    head = target.split(" ")[0] if target else ""
    if not is_gitlab_target(head, host):
        return load("comment_github")
    mr_match = re.search(r"/-/merge_requests/(\d+)", head)
    mr = mr_match.group(1) if mr_match else re.sub(r"^!(\d+)$", r"\1", head)
    mr = mr if re.match(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$", mr) else ""
    project_match = GITLAB_MR_URL.match(head)
    project = project_match.group(1) if project_match else None
    command = "glab mr note" + (" " + mr if mr else "") + (" -R " + project if project else "") + ' -m "<body>"'
    clause = "" if project else " from inside that project's checkout"
    return load("comment_gitlab").replace("{{glab_command}}", command).replace("{{checkout_clause}}", clause)


def post_notice(args):
    """Porta do aviso de `bs()` para `--post`, que só vale no ultra."""
    if not args.post:
        return ""
    if args.comment:
        consequence = "when the target is a GitHub PR, your `--comment` is what posts the findings as inline PR comments"
    else:
        consequence = ("this local review will not post to GitHub; `--comment` is the flag that posts local findings "
                       "as inline PR comments")
    return ("(The typed `--post` applies only to the `/code-review ultra` cloud review and was ignored — {}. "
            "Tell the user this in one short line.)\n\n").format(consequence)


def build(level, raw_args, host=None):
    if level not in LEVELS:
        raise PromptBuildError("unknown level '{}'; valid: {}".format(level, ", ".join(LEVELS)))
    args = parse_args(raw_args)
    target_line = "Review target: `{}`\n\n".format(args.target) if args.target else ""
    return "".join((
        post_notice(args),
        target_line,
        recipe(level),
        comment_section(args.target, host) if args.comment else "",
        load("fix") if args.fix else "",
    ))


PROMPTS_DIR = "prompts"
# Um prompt só é lido pelo reviewer logo depois de escrito; um dia de folga cobre qualquer review.
PROMPT_TTL = 24 * 3600
SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def write_prompt(data_dir, session_id, text):
    """Grava o prompt num arquivo novo e apaga os de reviews antigos. Devolve o caminho."""
    directory = os.path.join(data_dir or os.path.join(tempfile.gettempdir(), SKILL_NAME), PROMPTS_DIR)
    os.makedirs(directory, exist_ok=True)
    now = time.time()
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        try:
            if now - os.path.getmtime(path) > PROMPT_TTL:
                os.unlink(path)
        except OSError:
            pass
    prefix = (session_id if SESSION_ID.match(session_id or "") else "session") + "-"
    fd, path = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def launcher(level, prompt_path, args):
    """O que a sessão principal recebe: disparar o reviewer em background e esperar o relatório."""
    extras = [name for name, on in (("--fix", args.fix), ("--comment", args.comment)) if on]
    what = "{} review{}".format(level, " with " + " and ".join(extras) if extras else "")
    return (
        "Launch the code review in the background now, with exactly one `Agent` call:\n"
        "- subagent_type: `{agent}`\n"
        "- description: `Code review ({level})`\n"
        "- run_in_background: true\n"
        "- prompt: `Read the file {path} with the Read tool and carry out the code review it describes, "
        "exactly as written. Your final message is the report the user will see.`\n\n"
        "Do not review, read the diff or open that file yourself. After launching, tell the user in one "
        "short line that the {what} is running in the background, then end your turn. When the agent's "
        "result arrives, relay its report to the user."
    ).format(agent=REVIEWER_AGENT, level=level, path=prompt_path, what=what)


def main(argv):
    if len(argv) != 5:
        print("Could not build the review prompt: expected plugin data dir, session effort, session id and "
              "arguments. Do not review; tell the user the code-review-legacy skill is misconfigured.")
        return 0
    # Placeholder que o Claude Code não substituiu chega cru; tratar como ausente, não como caminho/valor.
    # Só nos valores de ambiente: os argumentos do usuário são texto livre.
    data_dir, session_effort, session_id = (value if not value.startswith("${") else "" for value in argv[1:4])
    route = resolve(argv[4], data_dir, session_effort)
    try:
        prompt = notice(route) + build(route.level, route.args)
        prompt_path = write_prompt(data_dir, session_id, prompt)
    except (PromptBuildError, OSError) as exc:
        # Sem prompt válido não há revisão: a sessão relata a falha em vez de revisar com texto errado.
        print("Could not build the review prompt: {}. Do not review; tell the user this.".format(exc))
        return 0
    # Como o `onUserTypedArgs()` do embutido: só um nível digitado vira o "último".
    if route.source == "explicit":
        write_last_level(data_dir, route.level)
    sys.stdout.write(launcher(route.level, prompt_path, parse_args(route.args)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
