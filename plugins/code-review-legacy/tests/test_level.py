import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import build_prompt
import level


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data_dir)

    def remember(self, name):
        level.write_last_level(self.data_dir, name)

    def test_explicit_level_is_removed_from_args(self):
        self.assertEqual(level.resolve("high --fix 42", self.data_dir, "xhigh"),
                         level.Route("high", "--fix 42", "explicit", None))

    def test_level_after_flags_counts(self):
        self.assertEqual(level.resolve("--fix XHIGH main", self.data_dir, ""),
                         level.Route("xhigh", "--fix main", "explicit", None))

    def test_level_only_in_first_non_flag_position(self):
        self.assertEqual(level.resolve("42 high", self.data_dir, "low").level, "low")

    def test_reuses_last_level(self):
        self.remember("max")
        self.assertEqual(level.resolve("--fix", self.data_dir, "low"),
                         level.Route("max", "--fix", "last_used", None))

    def test_session_effort_when_nothing_remembered(self):
        self.assertEqual(level.resolve("", self.data_dir, "xhigh"), level.Route("xhigh", "", "session", None))

    def test_session_effort_empty_is_medium_and_invalid_is_high(self):
        self.assertEqual(level.resolve("", self.data_dir, "").level, "medium")
        self.assertEqual(level.resolve("", self.data_dir, "ultracode").level, "high")

    def test_corrupt_last_level_is_ignored(self):
        self.remember("ultra")
        self.assertEqual(level.resolve("", self.data_dir, "low").source, "session")

    def test_non_utf8_last_level_is_ignored(self):
        with open(os.path.join(self.data_dir, level.LAST_LEVEL_FILE), "wb") as fh:
            fh.write(b"\xff\xfe")
        self.assertEqual(level.resolve("", self.data_dir, "low").source, "session")

    def test_unrecognized_level_stays_in_target(self):
        result = level.resolve("mediun 42", self.data_dir, "low")
        self.assertEqual((result.level, result.args, result.unrecognized), ("low", "mediun 42", "mediun"))

    def test_typo_without_level_prefix_is_just_target(self):
        # `cs` do embutido exige as 3 primeiras letras de um nível: `hgh` é alvo, não nível errado.
        self.assertIsNone(level.resolve("hgh", self.data_dir, "low").unrecognized)

    def test_plain_target_is_not_unrecognized_level(self):
        self.assertIsNone(level.resolve("feature/x", self.data_dir, "medium").unrecognized)

    def test_unsubstituted_arguments_and_escaped_bang(self):
        self.assertEqual(level.resolve("$ARGUMENTS", self.data_dir, "low"), level.Route("low", "", "session", None))
        self.assertEqual(level.resolve("high --comment \\!9", self.data_dir, "").args, "--comment !9")


class MainTest(unittest.TestCase):
    """`build_prompt.main`: resolve o nível, grava o último e monta o prompt do fork."""

    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data_dir)

    def run_main(self, raw, effort="medium", data_dir=None):
        with mock.patch("sys.stdout", new=StringIO()) as out, \
                mock.patch.object(build_prompt, "origin_host", lambda: "github.com"):
            build_prompt.main(["build_prompt.py", self.data_dir if data_dir is None else data_dir, effort, raw])
        return out.getvalue()

    def test_only_explicit_level_is_remembered(self):
        self.run_main("--fix", effort="low")
        self.assertIsNone(level.read_last_level(self.data_dir))
        self.run_main("xhigh")
        self.assertEqual(level.read_last_level(self.data_dir), "xhigh")

    def test_level_picks_recipe_and_rest_goes_to_prompt(self):
        text = self.run_main("high --fix 42")
        self.assertTrue(text.startswith("Review target: `42`"))
        self.assertIn("`high effort → 3+5 angles", text)
        self.assertIn("## Applying fixes (--fix)", text)

    def test_no_level_uses_session_effort(self):
        self.assertIn("`xhigh effort → 5+5 angles", self.run_main("", effort="xhigh"))

    def test_reused_level_is_announced_in_fork_variant(self):
        self.run_main("max")
        text = self.run_main("")
        self.assertTrue(text.startswith("(No effort level given — reusing max, the level the user typed last time. "
                                        "Open your report with one short line telling the user this"))
        self.assertIn("`max effort → 5+5 angles", text)

    def test_unrecognized_level_notice_matches_builtin(self):
        # `bs()` do embutido: sem nível reaproveitado, o aviso vai sem pedido de avisar o usuário.
        text = self.run_main("mediun 42", effort="low")
        self.assertTrue(text.startswith('(Ignoring unrecognized effort "mediun"; valid: low, medium, high, xhigh, max. Using low.)'))
        self.assertNotIn("Open your report", text)
        self.run_main("max")
        self.assertIn("Open your report with one short line", self.run_main("mediun 42"))

    def test_explicit_level_has_no_notice(self):
        self.assertTrue(self.run_main("low").startswith("`low effort"))

    def test_unsubstituted_placeholders_are_ignored(self):
        text = self.run_main("", effort="${CLAUDE_EFFORT}", data_dir="${CLAUDE_PLUGIN_DATA}")
        self.assertIn("`medium effort", text)
        self.assertFalse(os.path.exists("${CLAUDE_PLUGIN_DATA}"))

    def test_user_arguments_are_not_treated_as_placeholders(self):
        self.assertIn("Review target: `${HOME}`", self.run_main("${HOME}"))

    def test_wrong_arity_reports_misconfiguration(self):
        with mock.patch("sys.stdout", new=StringIO()) as out:
            build_prompt.main(["build_prompt.py"])
        self.assertIn("misconfigured", out.getvalue())


class WindowsConsoleTest(unittest.TestCase):
    """No Windows o console usa cp1252; a receita tem `→`, que não existe nessa tabela."""

    def test_script_writes_utf8_even_on_cp1252_console(self):
        data_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, data_dir)
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "build_prompt.py")
        env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
        proc = subprocess.run([sys.executable, script, data_dir, "low", "low"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertIn("low effort → 1 diff pass", proc.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
