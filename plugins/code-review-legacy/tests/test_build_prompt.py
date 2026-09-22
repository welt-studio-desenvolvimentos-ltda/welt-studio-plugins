import inspect
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_prompt as bp
import level

PLUGIN_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SKILLS_DIR = os.path.join(PLUGIN_ROOT, "skills")
SKILL_MD = os.path.join(SKILLS_DIR, level.SKILL_NAME, "SKILL.md")

with open(os.path.join(PLUGIN_ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as _fh:
    # O Claude Code namespaceia os agentes de plugin pelo nome do plugin, não pelo da skill.
    PLUGIN_NAME = json.load(_fh)["name"]

FIX_MARKER = "## Applying fixes (--fix)"
GITHUB_MARKER = "## Posting to GitHub (--comment)"
GITLAB_MARKER = "## Posting to GitLab (--comment)"


def build(level, raw="", host="github.com"):
    # Host fixo: o teste não depende do origin do repositório onde roda.
    return bp.build(level, raw, host=host)


class ParseArgsTest(unittest.TestCase):
    def test_no_arguments(self):
        self.assertEqual(bp.parse_args(""), bp.Args(False, False, False, ""))

    def test_unsubstituted_placeholder_counts_as_empty(self):
        self.assertEqual(bp.parse_args("$ARGUMENTS").target, "")

    def test_flags_and_target(self):
        args = bp.parse_args("--fix --comment 123")
        self.assertEqual(args, bp.Args(True, True, False, "123"))

    def test_flag_after_target(self):
        self.assertEqual(bp.parse_args("feature/x --fix").target, "feature/x")

    def test_glued_flag_does_not_count(self):
        args = bp.parse_args("--fixed")
        self.assertFalse(args.fix)
        self.assertEqual(args.target, "--fixed")

    def test_target_loses_hash_and_backticks(self):
        self.assertEqual(bp.parse_args("#42").target, "42")
        self.assertEqual(bp.parse_args("`main`").target, "main")

    def test_bang_escaped_by_claude_code_is_restored(self):
        # É assim que `!9` chega pelo SKILL.md: `uw()` do Claude Code troca `!` em início de palavra por `\!`.
        self.assertEqual(bp.parse_args("--comment \\!9").target, "!9")
        self.assertEqual(bp.parse_args("\\!9 --fix").target, "!9")


class FlagSectionsTest(unittest.TestCase):
    def test_no_flags_neither_fixes_nor_posts(self):
        for level in bp.LEVELS:
            text = build(level)
            self.assertNotIn(FIX_MARKER, text, level)
            self.assertNotIn(GITHUB_MARKER, text, level)
            self.assertNotIn(GITLAB_MARKER, text, level)

    def test_fix_only_with_flag(self):
        text = build("medium", "--fix")
        self.assertIn(FIX_MARKER, text)
        self.assertIn("Finish with a brief summary", text)
        self.assertNotIn("ReportFindings again", text)

    def test_comment_defaults_to_github(self):
        text = build("high", "--comment 12")
        self.assertIn(GITHUB_MARKER, text)
        self.assertNotIn(GITLAB_MARKER, text)

    def test_comment_gitlab_from_mr_url(self):
        text = build("high", "--comment https://gitlab.example.com/g/p/-/merge_requests/7")
        self.assertIn(GITLAB_MARKER, text)
        self.assertIn('glab mr note 7 -R https://gitlab.example.com/g/p -m "<body>"', text)
        self.assertNotIn(GITHUB_MARKER, text)

    def test_comment_gitlab_from_bang_number(self):
        text = build("high", "--comment !9")
        self.assertIn('glab mr note 9 -m "<body>"` from inside that project\'s checkout', text)

    def test_comment_gitlab_from_bang_number_as_escaped_by_skill(self):
        text = build("high", "--comment \\!9")
        self.assertIn(GITLAB_MARKER, text)
        self.assertIn('glab mr note 9 -m "<body>"', text)

    def test_comment_gitlab_from_origin(self):
        self.assertIn(GITLAB_MARKER, build("high", "--comment 5", host="gitlab.com"))

    def test_github_url_is_not_gitlab_even_with_gitlab_origin(self):
        text = build("high", "--comment https://github.com/o/r/pull/3", host="gitlab.com")
        self.assertIn(GITHUB_MARKER, text)

    def test_post_warns_it_was_ignored(self):
        self.assertIn("The typed `--post` applies only", build("medium", "--post"))
        self.assertNotIn("--post", build("medium"))


class RecipeTest(unittest.TestCase):
    def test_findings_cap_per_level(self):
        caps = {"medium": 8, "high": 10, "xhigh": 15, "max": 15}
        for level, cap in caps.items():
            self.assertIn("at most {} objects".format(cap), build(level), level)
        self.assertIn("at most **4 findings**", build("low"))

    def test_output_is_text_not_report_findings(self):
        for level in bp.LEVELS:
            text = build(level)
            self.assertIn("ReportFindings tool even if it is available", text, level)
            self.assertNotIn("Call the ReportFindings tool", text, level)

    def test_fan_out_only_above_low(self):
        self.assertIn("No subagents", build("low"))
        for level in ("medium", "high", "xhigh", "max"):
            self.assertIn("via the `Agent` tool", build(level), level)

    def test_sweep_only_in_xhigh_and_max(self):
        self.assertNotIn("## Phase 3", build("high"))
        self.assertIn("## Phase 3 — Sweep for gaps", build("xhigh"))
        self.assertIn("## Phase 3 — Sweep for gaps", build("max"))

    def test_max_intensity(self):
        self.assertIn("at maximum effort", build("max"))
        self.assertIn("at extra-high effort", build("xhigh"))

    def test_one_agent_per_angle_on_fan_out_levels(self):
        # Desvio deliberado: sem isso o modelo agrupa os ângulos em poucos agentes.
        for level, count in (("medium", 8), ("high", 8), ("xhigh", 10), ("max", 10)):
            text = build(level)
            self.assertIn("Spawn exactly one `Agent` call (subagent_type `general-purpose`) per angle below — "
                          "{} calls in total".format(count), text, level)
            self.assertEqual(text.count("\n### "), count, level)
        self.assertNotIn("per angle below", build("low"))

    def test_no_finder_budget_hint(self):
        # A dica "spawn about N" contradiria a instrução de um agente por ângulo.
        for level in bp.LEVELS:
            self.assertNotIn("finder subagents (min 2, max 8)", build(level), level)

    def test_invalid_level(self):
        with self.assertRaises(bp.PromptBuildError):
            bp.build("ultra", "")

    def test_target_enters_prompt(self):
        self.assertTrue(build("medium", "42").startswith("Review target: `42`"))


def read_frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not match:
        raise AssertionError("no frontmatter in {}".format(path))
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields, match.group(2).strip()


def script_path(module):
    return os.path.relpath(os.path.abspath(inspect.getfile(module)), PLUGIN_ROOT).replace(os.sep, "/")


class ReviewerAgentTest(unittest.TestCase):
    """O reviewer é o `general-purpose` do Claude Code sem a diretriz que desencoraja delegar."""

    def test_reviewer_agent_exists(self):
        plugin, _, agent = level.REVIEWER_AGENT.partition(":")
        self.assertEqual(plugin, PLUGIN_NAME)
        agent_fields, body = read_frontmatter(os.path.join(PLUGIN_ROOT, "agents", agent + ".md"))
        self.assertEqual(agent_fields["name"], agent)
        # Sem `model:` o agente escolhe o modelo como o `general-purpose` (CLAUDE_CODE_SUBAGENT_MODEL, depois o da sessão).
        self.assertNotIn("model", agent_fields)
        # Sem `tools:` o agente herda todas as ferramentas, incluindo a `Agent` que a receita usa.
        self.assertNotIn("tools", agent_fields)
        self.assertNotIn("re-delegate", body)
        self.assertIn("You are an agent for Claude Code", body)


class SkillFilesTest(unittest.TestCase):
    """A skill repete o nome do script no `allowed-tools` e no comando `!`; este teste é a trava de que batem."""

    def test_single_inline_skill(self):
        self.assertEqual(os.listdir(SKILLS_DIR), [level.SKILL_NAME])
        fields, body = read_frontmatter(SKILL_MD)
        self.assertEqual(fields["name"], level.SKILL_NAME)
        # Sem fork: no VS Code o fork roda em primeiro plano e termina antes dos finders voltarem.
        # A skill só dispara o reviewer em background.
        self.assertNotIn("context", fields)
        self.assertNotIn("agent", fields)
        # Sem `effort:` o reviewer herda o esforço da sessão, e `${CLAUDE_EFFORT}` é o fallback do nível.
        self.assertNotIn("effort", fields)
        allowed = re.match(r"^Bash\((.*):\*\)$", fields["allowed-tools"])
        if allowed is None:
            self.fail("allowed-tools is not a Bash(<prefix>:*) rule")
        prefix = allowed.group(1)
        self.assertTrue(prefix.endswith("/" + script_path(bp)))
        # O comando `!` precisa começar pelo prefixo liberado, senão a checagem de permissão aborta a skill.
        self.assertEqual(body, "!`{} '${{CLAUDE_PLUGIN_DATA}}' '${{CLAUDE_EFFORT}}' '${{CLAUDE_SESSION_ID}}' "
                               "'$ARGUMENTS'`".format(prefix))

if __name__ == "__main__":
    unittest.main()
