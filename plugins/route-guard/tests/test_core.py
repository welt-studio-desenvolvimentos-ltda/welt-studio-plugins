import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from core import bash_writes, route, state  # noqa: E402


def step(step_id, scope=("src/**",), done_when=("python3 -c pass",), depends_on=()):
    return {"id": step_id, "title": "step " + step_id, "scope": list(scope),
            "done_when": list(done_when), "depends_on": list(depends_on)}


class ValidateTest(unittest.TestCase):
    def test_valid_route(self):
        self.assertEqual(route.validate({"steps": [step("1"), step("2", depends_on=["1"])]}), [])

    def test_empty_steps(self):
        self.assertTrue(route.validate({"steps": []}))
        self.assertTrue(route.validate([]))

    def test_duplicate_ids(self):
        errors = route.validate({"steps": [step("1"), step("1")]})
        self.assertTrue(any("duplicated" in e for e in errors))

    def test_empty_scope_and_criteria(self):
        errors = route.validate({"steps": [step("1", scope=(), done_when=())]})
        self.assertTrue(any(".scope" in e for e in errors))
        self.assertTrue(any(".done_when" in e for e in errors))

    def test_trivial_criteria_rejected(self):
        for command in ("true", ":", "exit 0", "echo done", "  printf ok "):
            errors = route.validate({"steps": [step("1", done_when=(command,))]})
            self.assertTrue(any("always passes" in e for e in errors), command)

    def test_real_criteria_accepted(self):
        for command in ("pytest -q tests/test_a.py", "npm test", "grep -q foo src/a.py", "true_story.sh"):
            self.assertEqual(route.validate({"steps": [step("1", done_when=(command,))]}), [], command)

    def test_scope_must_stay_inside_repo(self):
        for pattern in ("/etc/passwd", "../other/**", "src/../../x"):
            errors = route.validate({"steps": [step("1", scope=(pattern,))]})
            self.assertTrue(any("relative to the repo root" in e for e in errors), pattern)

    def test_unknown_dependency(self):
        errors = route.validate({"steps": [step("1", depends_on=["9"])]})
        self.assertTrue(any("unknown step" in e for e in errors))

    def test_cycle(self):
        errors = route.validate({"steps": [step("1", depends_on=["2"]), step("2", depends_on=["1"])]})
        self.assertIn("depends_on has a cycle", errors)

    def test_generated_field(self):
        self.assertEqual(route.validate({"steps": [step("1")], "generated": ["build/**"]}), [])
        self.assertTrue(route.validate({"steps": [step("1")], "generated": "build"}))
        self.assertTrue(route.validate({"steps": [step("1")], "generated": ["../x"]}))
        self.assertIn("build/**", route.generated_patterns({"steps": [], "generated": ["build/**"]}))
        self.assertIn("**/__pycache__/**", route.generated_patterns(None))

    def test_bad_id(self):
        self.assertTrue(route.validate({"steps": [step("a b")]}))


class GlobTest(unittest.TestCase):
    def test_patterns(self):
        cases = [
            ("src/**", "src/a.py", True),
            ("src/**", "src/deep/b/c.py", True),
            ("src/**", "srcx/a.py", False),
            ("src/*.py", "src/a.py", True),
            ("src/*.py", "src/deep/a.py", False),
            ("**/test_*.py", "test_a.py", True),
            ("**/test_*.py", "a/b/test_a.py", True),
            ("docs/", "docs/x/y.md", True),
            ("README.md", "README.md", True),
            ("README.md", "docs/README.md", False),
            ("src/?.py", "src/a.py", True),
            ("src/?.py", "src/ab.py", False),
            ("a.b/c", "aXb/c", False),
            ("src/**", "src", True),
            ("**/__pycache__/**", "pkg/__pycache__", True),
            ("**/__pycache__/**", "pkg/__pycache__/m.pyc", True),
        ]
        for pattern, path, expected in cases:
            self.assertEqual(route.matches(path, [pattern]), expected, (pattern, path))


class StepsTest(unittest.TestCase):
    def setUp(self):
        self.route = {"steps": [step("1"), step("2", depends_on=["1"]), step("3")]}

    def test_unlocked(self):
        self.assertEqual([s["id"] for s in route.unlocked_steps(self.route, [])], ["1", "3"])
        self.assertEqual([s["id"] for s in route.unlocked_steps(self.route, ["1"])], ["2", "3"])

    def test_subject_roundtrip(self):
        subject = route.task_subject(self.route["steps"][1])
        self.assertEqual(subject, "[R2] step 2")
        self.assertEqual(route.step_id_from_subject(subject), "2")
        self.assertIsNone(route.step_id_from_subject("Fix the bug"))
        self.assertIsNone(route.step_id_from_subject(None))

    def test_session_id_cannot_escape(self):
        for bad in ("../x", "a/b", "", None, "x" * 200):
            with self.assertRaises(route.RouteError):
                route.route_path("/data", bad)


class StateTest(unittest.TestCase):
    def test_failures_escalate_at_limit(self):
        st = state.new_draft("plan.md", "/repo")
        state.activate(st, ["1"], {"head": None, "dirty": {}})
        for _ in range(state.MAX_ATTEMPTS - 1):
            self.assertFalse(state.record_failure(st, "1"))
        self.assertTrue(state.record_failure(st, "1"))
        self.assertEqual(st["status"], state.ESCALATED)

    def test_limit_below_stop_block_cap(self):
        # O Claude Code força a parada depois de 8 bloqueios seguidos do Stop.
        self.assertLess(state.MAX_ATTEMPTS, 8)

    def test_amendment_keeps_baseline_and_done_steps_still_present(self):
        st = state.new_draft("plan.md", "/repo")
        state.activate(st, ["1", "2"], {"head": "abc", "dirty": {}})
        state.mark_done(st, "1", ["1", "2"])
        amended = state.new_draft("plan2.md", "/repo", st)
        self.assertEqual(amended["status"], state.DRAFT)
        state.activate(amended, ["2", "3"], {"head": "zzz", "dirty": {}})
        self.assertEqual(amended["baseline"]["head"], "abc")
        self.assertEqual(amended["done"], [])

    def test_draft_after_closed_route_starts_clean(self):
        st = state.new_draft("plan.md", "/repo")
        state.activate(st, ["1"], {"head": "abc", "dirty": {}})
        st["status"] = state.CLOSED
        fresh = state.new_draft("plan2.md", "/repo", st)
        self.assertIsNone(fresh["baseline"])

    def test_all_done(self):
        st = state.new_draft("plan.md", "/repo")
        state.activate(st, ["1", "2"], None)
        state.mark_done(st, "1", ["1", "2"])
        self.assertEqual(st["status"], state.ACTIVE)
        state.mark_done(st, "2", ["1", "2"])
        self.assertEqual(st["status"], state.DONE)


class BashWritesTest(unittest.TestCase):
    def test_detected(self):
        cases = {
            "echo hi > out.txt": ["out.txt"],
            "echo hi >> logs/a.log": ["logs/a.log"],
            "echo hi >out.txt": ["out.txt"],
            "cat a | tee b.txt": ["b.txt"],
            "sed -i 's/a/b/' src/x.py": ["src/x.py"],
            "sed -i.bak -e 's/a/b/' src/x.py": ["src/x.py"],
            "mv a.py b.py": ["a.py", "b.py"],
            "cp -r src dst": ["dst"],
            "rm -rf build": ["build"],
            "touch new.py && mkdir -p pkg": ["new.py", "pkg"],
            "git checkout -- src/a.py": ["src/a.py"],
            "git restore src/a.py": ["src/a.py"],
            "dd if=/dev/zero of=disk.img": ["disk.img"],
            "FOO=1 sudo rm x": ["x"],
            "echo start\nrm src/core.py": ["src/core.py"],
            "cat > src/x.py <<'EOF'\n# don't\nEOF\nrm z": ["src/x.py", "z"],
            "echo x >| src/f": ["src/f"],
            "cmd >&src/f": ["src/f"],
            "cd src && rm old.py": ["src/old.py"],
            "cd /tmp/w && rm out.txt": ["/tmp/w/out.txt"],
        }
        for command, expected in cases.items():
            self.assertEqual([t.path for t in bash_writes.write_targets(command) or []], expected, command)

    def test_not_writes(self):
        for command in ("git status", "ls -la", "python3 -m unittest", "grep -r foo src 2>/dev/null",
                        "make test 2>&1 | tail -5", "echo hi > /dev/null", "git diff > /dev/null 2>&1",
                        "echo x >&2", "cmd 2>&-", "git stash list"):
            self.assertEqual(bash_writes.write_targets(command), [], command)

    def test_kinds(self):
        kinds = {t.path: t.kind for t in bash_writes.write_targets(
            "rm a.txt && git restore b.py && git checkout -- c.py && echo x > d.txt") or []}
        self.assertEqual(kinds, {"a.txt": bash_writes.REMOVE, "b.py": bash_writes.RESTORE,
                                 "c.py": bash_writes.RESTORE, "d.txt": bash_writes.WRITE})

    def test_git_kinds(self):
        def kinds(command):
            return [(t.path, t.kind) for t in bash_writes.write_targets(command)]
        self.assertEqual(kinds("git checkout main -- a.py"), [("a.py", bash_writes.WRITE)])
        self.assertEqual(kinds("git restore -s main a.py"), [("a.py", bash_writes.WRITE)])
        self.assertEqual(kinds("git reset --hard"), [(".", bash_writes.RESTORE)])
        self.assertEqual(kinds("git stash push -m wip"), [(".", bash_writes.RESTORE)])

    def test_home_expanded(self):
        self.assertEqual([t.path for t in bash_writes.write_targets("cp a ~/x")], [os.path.expanduser("~/x")])

    def test_unparseable(self):
        self.assertIsNone(bash_writes.write_targets("echo 'unterminated"))


if __name__ == "__main__":
    unittest.main()
