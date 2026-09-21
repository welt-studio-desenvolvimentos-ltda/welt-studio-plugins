import inspect
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_prompt as bp
import route

PLUGIN_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SKILLS_DIR = os.path.join(PLUGIN_ROOT, "skills")

FIX_MARKER = "## Applying fixes (--fix)"
GITHUB_MARKER = "## Posting to GitHub (--comment)"
GITLAB_MARKER = "## Posting to GitLab (--comment)"


def build(level, raw="", host="github.com"):
    # Sem contagem de diff e com host fixo: o teste não depende do repositório onde roda.
    return bp.build(level, raw, count_lines=lambda _target: None, host=host)


def must_not_count(_target):
    raise AssertionError("diff counted for a level without the finder budget hint")


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

    def test_finder_hint_comes_before_recipe(self):
        text = bp.build("high", "", count_lines=lambda _target: 300, host="github.com")
        self.assertTrue(text.startswith("The committed diff (@{upstream}...HEAD) is about 300 lines"))

    def test_invalid_level(self):
        with self.assertRaises(bp.PromptBuildError):
            bp.build("ultra", "")

    def test_target_enters_prompt(self):
        self.assertTrue(build("medium", "42").startswith("Review target: `42`"))


class FinderBudgetTest(unittest.TestCase):
    def test_bounds(self):
        self.assertIn("about 2 finder subagents", bp.finder_budget("high", "", lambda _target: 10))
        self.assertIn("about 8 finder subagents", bp.finder_budget("high", "", lambda _target: 5000))
        self.assertIn("about 4 finder subagents", bp.finder_budget("max", "", lambda _target: 600))

    def test_without_target_is_a_floor(self):
        self.assertIn("treat this as a floor", bp.finder_budget("xhigh", "", lambda _target: 300))

    def test_with_target(self):
        self.assertIn("This diff is about 300 lines", bp.finder_budget("xhigh", "main...HEAD", lambda _target: 300))

    def test_unknown_size_gives_no_hint(self):
        self.assertEqual(bp.finder_budget("high", "", lambda _target: None), "")

    def test_only_high_and_above_and_without_running_git(self):
        self.assertEqual(bp.finder_budget("medium", "", must_not_count), "")
        self.assertEqual(bp.finder_budget("low", "", must_not_count), "")

    def test_target_that_is_not_a_range_is_not_counted(self):
        self.assertIsNone(bp.count_diff_lines("123"))


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


class SkillFilesTest(unittest.TestCase):
    """Cada SKILL.md repete o nível e o script em vários lugares; este teste é a trava de que batem."""

    def allowed_prefix(self, fields, label):
        allowed = re.match(r"^Bash\((.*):\*\)$", fields["allowed-tools"])
        if allowed is None:
            self.fail("{}: allowed-tools is not a Bash(<prefix>:*) rule".format(label))
        return allowed.group(1)

    def test_one_visible_skill_plus_one_hidden_per_level(self):
        expected = [route.LEVEL_SKILL_PREFIX.rstrip("-")] + [route.LEVEL_SKILL_PREFIX + level for level in bp.LEVELS]
        self.assertEqual(sorted(os.listdir(SKILLS_DIR)), sorted(expected))

    def test_visible_skill_routes_inline(self):
        name = route.LEVEL_SKILL_PREFIX.rstrip("-")
        fields, body = read_frontmatter(os.path.join(SKILLS_DIR, name, "SKILL.md"))
        self.assertEqual(fields["name"], name)
        self.assertNotIn("context", fields)
        self.assertNotIn("user-invocable", fields)
        # Esforço fixo aqui mascararia o `${CLAUDE_EFFORT}` da sessão, que é o fallback do nível.
        self.assertNotIn("effort", fields)
        prefix = self.allowed_prefix(fields, name)
        self.assertTrue(prefix.endswith("/" + script_path(route)))
        self.assertEqual(body, "!`{} '${{CLAUDE_PLUGIN_DATA}}' '${{CLAUDE_EFFORT}}' '$ARGUMENTS'`".format(prefix))

    def test_level_skills_are_hidden_forks_with_their_own_effort(self):
        hints = set()
        for level in bp.LEVELS:
            name = route.LEVEL_SKILL_PREFIX + level
            fields, body = read_frontmatter(os.path.join(SKILLS_DIR, name, "SKILL.md"))
            self.assertEqual(fields["name"], name)
            self.assertEqual(fields["effort"], level)
            self.assertEqual(fields["context"], "fork")
            self.assertEqual(fields["user-invocable"], "false")
            hints.add(fields["argument-hint"])
            prefix = self.allowed_prefix(fields, level)
            self.assertTrue(prefix.endswith("/" + script_path(bp)), level)
            # O comando `!` precisa começar pelo prefixo liberado, senão a checagem de permissão aborta a skill.
            self.assertEqual(body, "!`{} {} '$ARGUMENTS'`".format(prefix, level))
        self.assertEqual(len(hints), 1, hints)

    def test_router_targets_plugin_name(self):
        with open(os.path.join(PLUGIN_ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            name = json.load(fh)["name"]
        self.assertEqual(route.level_skill("high"), "{}:{}high".format(name, route.LEVEL_SKILL_PREFIX))


if __name__ == "__main__":
    unittest.main()
