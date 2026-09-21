import os
import shutil
import sys
import tempfile
import unittest
from io import StringIO
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import route


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data_dir)

    def remember(self, level):
        route.write_last_level(self.data_dir, level)

    def test_explicit_level_is_removed_from_args(self):
        self.assertEqual(route.resolve("high --fix 42", self.data_dir, "xhigh"),
                         route.Route("high", "--fix 42", "explicit", None))

    def test_level_after_flags_counts(self):
        self.assertEqual(route.resolve("--fix XHIGH main", self.data_dir, ""),
                         route.Route("xhigh", "--fix main", "explicit", None))

    def test_level_only_in_first_non_flag_position(self):
        self.assertEqual(route.resolve("42 high", self.data_dir, "low").level, "low")

    def test_reuses_last_level(self):
        self.remember("max")
        self.assertEqual(route.resolve("--fix", self.data_dir, "low"),
                         route.Route("max", "--fix", "last_used", None))

    def test_session_effort_when_nothing_remembered(self):
        self.assertEqual(route.resolve("", self.data_dir, "xhigh"), route.Route("xhigh", "", "session", None))

    def test_session_effort_empty_is_medium_and_invalid_is_high(self):
        self.assertEqual(route.resolve("", self.data_dir, "").level, "medium")
        self.assertEqual(route.resolve("", self.data_dir, "ultracode").level, "high")

    def test_corrupt_last_level_is_ignored(self):
        self.remember("ultra")
        self.assertEqual(route.resolve("", self.data_dir, "low").source, "session")

    def test_unrecognized_level_stays_in_target(self):
        result = route.resolve("mediun 42", self.data_dir, "low")
        self.assertEqual((result.level, result.args, result.unrecognized), ("low", "mediun 42", "mediun"))

    def test_typo_without_level_prefix_is_just_target(self):
        # `cs` do embutido exige as 3 primeiras letras de um nível: `hgh` é alvo, não nível errado.
        self.assertIsNone(route.resolve("hgh", self.data_dir, "low").unrecognized)

    def test_plain_target_is_not_unrecognized_level(self):
        self.assertIsNone(route.resolve("feature/x", self.data_dir, "medium").unrecognized)

    def test_unsubstituted_arguments_and_escaped_bang(self):
        self.assertEqual(route.resolve("$ARGUMENTS", self.data_dir, "low"), route.Route("low", "", "session", None))
        self.assertEqual(route.resolve("high --comment \\!9", self.data_dir, "").args, "--comment !9")


class MainTest(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data_dir)

    def run_main(self, raw, effort="medium", data_dir=None):
        with mock.patch("sys.stdout", new=StringIO()) as out:
            route.main(["route.py", self.data_dir if data_dir is None else data_dir, effort, raw])
        return out.getvalue()

    def test_only_explicit_level_is_remembered(self):
        self.run_main("--fix", effort="low")
        self.assertIsNone(route.read_last_level(self.data_dir))
        self.run_main("xhigh")
        self.assertEqual(route.read_last_level(self.data_dir), "xhigh")

    def test_instruction_names_level_skill_and_exact_args(self):
        text = self.run_main("high --fix 42")
        self.assertIn("skill `code-review-legacy:code-review-legacy-high`", text)
        self.assertIn("args `--fix 42`, exactly as shown", text)
        self.assertIn("do not read the diff or review anything yourself", text)

    def test_instruction_without_args(self):
        self.assertIn("and no args.", self.run_main("low"))

    def test_reused_level_is_announced(self):
        self.run_main("max")
        self.assertIn("No effort level given — reusing max", self.run_main(""))

    def test_unrecognized_level_notice_matches_builtin(self):
        # `bs()` do embutido: sem nível reaproveitado, o aviso vai sem pedido de avisar o usuário.
        text = self.run_main("mediun 42", effort="low")
        self.assertTrue(text.startswith('(Ignoring unrecognized effort "mediun"; valid: low, medium, high, xhigh, max. Using low.)'))
        self.assertNotIn("Tell the user", text)
        self.run_main("max")
        self.assertIn("Tell the user this in one short line", self.run_main("mediun 42"))

    def test_explicit_level_has_no_notice(self):
        self.assertTrue(self.run_main("low").startswith("Invoke the Skill tool"))

    def test_unsubstituted_placeholders_are_ignored(self):
        text = self.run_main("", effort="${CLAUDE_EFFORT}", data_dir="${CLAUDE_PLUGIN_DATA}")
        self.assertIn("code-review-legacy-medium", text)
        self.assertFalse(os.path.exists("${CLAUDE_PLUGIN_DATA}"))

    def test_wrong_arity_reports_misconfiguration(self):
        with mock.patch("sys.stdout", new=StringIO()) as out:
            route.main(["route.py"])
        self.assertIn("misconfigured", out.getvalue())


if __name__ == "__main__":
    unittest.main()
