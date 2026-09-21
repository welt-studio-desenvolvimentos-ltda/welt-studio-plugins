#!/usr/bin/env python3
"""Monta o prompt de revisão que a skill forkada vai executar.

Porta de `ps()` do /code-review embutido (Claude Code 2.1.278), restrita às
células do Sonnet 5 — as receitas com fan-out de subagentes em todos os níveis.
O texto de cada trecho mora em `recipe/`; aqui só se decide o que entra.

Uso (via injeção `!` do SKILL.md):
    build_prompt.py '<CLAUDE_PLUGIN_DATA>' '<CLAUDE_EFFORT>' '<argumentos crus do usuário>'
"""

import math
import os
import re
import subprocess
import sys
from collections import namedtuple

from level import LEVELS, KNOWN_FLAGS, clean_raw, notice, resolve, write_last_level

RECIPE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "recipe")

# Níveis cuja célula Sonnet 5 tem finderBudgetHint.
FINDER_BUDGET_LEVELS = ("high", "xhigh", "max")

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
        "{angles}\n{cleanup_shape}\n{pass_through}\n{verify}\n{output}"
    ).format(
        tag=tag, lead_in=lead_in, phase0=load("phase0_gather"), fallback=load("agent_fallback"),
        angles=fragments(CORRECTNESS_3 + CLEANUP), cleanup_shape=load("cleanup_shape"),
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
        "{angles}\n{cleanup_shape}\n{verify}\n{single_vote}\n{sweep}\n{output}"
    ).format(
        level=level, intensity=intensity, phase0=load("phase0_gather"), fallback=load("agent_fallback"),
        angles=fragments(CORRECTNESS_5 + CLEANUP), cleanup_shape=load("cleanup_shape"),
        verify=load("verify_precision"), single_vote=load("recall_single_vote"),
        sweep=load("sweep"), output=output(15),
    )


def recipe(level):
    if level == "low":
        return load("low")
    if level in ("medium", "high"):
        return recipe_medium_high(level)
    return recipe_xhigh_max(level)


def count_diff_lines(target):
    """Porta de `ys()`: linhas adicionadas+removidas do range, ou None."""
    if not target:
        rev = "@{upstream}...HEAD"
    elif len(target) <= 256 and re.match(r"^[@\w][@\w./~^-]*\.\.\.?[@\w][@\w./~^-]*$", target):
        rev = target
    else:
        return None
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="none", GIT_NO_LAZY_FETCH="1",
               GIT_SSH_COMMAND="ssh -o BatchMode=yes")
    try:
        # Nomes de arquivo não são necessariamente UTF-8 nem da codificação do locale; só os números importam.
        proc = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=", "-c", "core.askPass=", "diff",
             "--no-ext-diff", "--no-textconv", "--numstat", "--end-of-options", rev, "--"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, timeout=5,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    total = 0
    for line in proc.stdout.splitlines():
        match = re.match(r"^(\d+)\t(\d+)\t", line)
        if match:
            total += int(match.group(1)) + int(match.group(2))
    return total or None


def finder_budget(level, target, count_lines):
    """Porta de `gs()`: dica de quantos finders disparar, dado o tamanho do diff.

    Só conta o diff nos níveis que têm a dica, para não rodar git à toa em low e medium.
    """
    if level not in FINDER_BUDGET_LEVELS:
        return ""
    lines = count_lines(target)
    if lines is None:
        return ""
    budget = max(2, min(8, math.ceil(lines / 150)))
    if not target:
        return ("The committed diff (@{{upstream}}...HEAD) is about {} lines. Uncommitted changes aren't counted here, "
                "so treat this as a floor — start with about {} finder subagents (min 2, max 8) and scale up if "
                "Phase 0 finds additional working-tree scope.\n\n").format(lines, budget)
    return ("This diff is about {} lines. Spawn about {} finder subagents (min 2, max 8) — scale your investigation "
            "depth to the diff size rather than using a fixed large fleet.\n\n").format(lines, budget)


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


def build(level, raw_args, count_lines=count_diff_lines, host=None):
    if level not in LEVELS:
        raise PromptBuildError("unknown level '{}'; valid: {}".format(level, ", ".join(LEVELS)))
    args = parse_args(raw_args)
    target_line = "Review target: `{}`\n\n".format(args.target) if args.target else ""
    return "".join((
        post_notice(args),
        target_line,
        finder_budget(level, args.target, count_lines),
        recipe(level),
        comment_section(args.target, host) if args.comment else "",
        load("fix") if args.fix else "",
    ))


def main(argv):
    if len(argv) != 4:
        print("Could not build the review prompt: expected plugin data dir, session effort and arguments. "
              "Do not review; stop and tell the user the code-review-legacy skill is misconfigured.")
        return 0
    # Placeholder que o Claude Code não substituiu chega cru; tratar como ausente, não como caminho/valor.
    # Só nos valores de ambiente: os argumentos do usuário são texto livre.
    data_dir, session_effort = (value if not value.startswith("${") else "" for value in argv[1:3])
    route = resolve(argv[3], data_dir, session_effort)
    try:
        prompt = notice(route) + build(route.level, route.args)
    except PromptBuildError as exc:
        # Sem prompt válido não há revisão: o fork relata a falha em vez de revisar com texto errado.
        print("Could not build the review prompt: {}. Do not review; stop and tell the user this.".format(exc))
        return 0
    # Como o `onUserTypedArgs()` do embutido: só um nível digitado vira o "último".
    if route.source == "explicit":
        write_last_level(data_dir, route.level)
    sys.stdout.write(prompt)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
