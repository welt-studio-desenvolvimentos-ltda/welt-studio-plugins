import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

PLUGIN_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, PLUGIN_ROOT)
from core import commands, guard, route, state  # noqa: E402

SESSION = "sess-1"


def git(root, *args):
    subprocess.run(["git", "-C", root, "-c", "user.email=t@t", "-c", "user.name=t"] + list(args),
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class GuardTestCase(unittest.TestCase):
    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp())
        self.data = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo)
        self.addCleanup(shutil.rmtree, self.data)
        git(self.repo, "init", "-q")
        self.write("README.md", "hi\n")
        self.write("src/a.py", "a = 1\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "init")
        self.route_file = route.route_path(self.data, SESSION)
        self.state_file = route.state_path(self.data, SESSION)

    def write(self, rel, content):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    def inp(self, **extra):
        return dict({"session_id": SESSION, "cwd": self.repo}, **extra)

    def approve_plan(self):
        return guard.on_plan_approved(self.inp(hook_event_name="PostToolUse", tool_name="ExitPlanMode",
                                               tool_input={"plan": "p", "planFilePath": "/plans/p.md"}), self.data)

    def write_route(self, steps):
        os.makedirs(os.path.dirname(self.route_file), exist_ok=True)
        with open(self.route_file, "w", encoding="utf-8") as fh:
            json.dump({"steps": steps}, fh)

    def activate(self, steps):
        self.approve_plan()
        self.write_route(steps)
        out = commands.approve(self.route_file, self.state_file)
        self.assertIn("route approved", out)

    def state(self):
        current = state.load(self.state_file)
        if current is None:
            self.fail("no route state for the session")
        return current

    def edit(self, rel):
        return guard.pre_tool(self.inp(tool_name="Edit", tool_input={"file_path": os.path.join(self.repo, rel)}),
                              self.data)

    def bash(self, command):
        return guard.pre_tool(self.inp(tool_name="Bash", tool_input={"command": command}), self.data)

    def complete(self, subject):
        return guard.task_completed(self.inp(hook_event_name="TaskCompleted", task_id="1", task_subject=subject),
                                    self.data)

    def stop(self, **extra):
        return guard.stop(self.inp(hook_event_name="Stop", **extra), self.data)

    def assertDenied(self, result, fragment=""):
        self.assertIsNotNone(result.stdout, "expected a denial")
        decision = result.stdout["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn(fragment, decision["permissionDecisionReason"])

    def assertAllowed(self, result):
        self.assertEqual(result, guard.ALLOW)


STEPS = [
    {"id": "1", "title": "core", "scope": ["src/**"], "done_when": ["test -f src/b.py"], "depends_on": []},
    {"id": "2", "title": "docs", "scope": ["docs/**"], "done_when": ["test -f docs/x.md"], "depends_on": ["1"]},
]


class NoRouteTest(GuardTestCase):
    def test_everything_passes_without_route(self):
        self.assertAllowed(self.edit("anything.py"))
        self.assertAllowed(self.bash("rm -rf src"))
        self.assertAllowed(self.complete("[R1] x"))
        self.assertAllowed(self.stop())


class PlanApprovedTest(GuardTestCase):
    def test_injects_route_instructions_and_drafts(self):
        result = self.approve_plan()
        context = result.stdout["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(result.stdout["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertIn(self.route_file, context)
        self.assertIn("/route-guard:approve", context)
        self.assertIn("[R<id>] <title>", context)
        self.assertEqual(self.state()["status"], state.DRAFT)
        self.assertEqual(self.state()["repo_root"], self.repo)
        self.assertEqual(self.state()["plan_file"], "/plans/p.md")


class DraftTest(GuardTestCase):
    def setUp(self):
        super().setUp()
        self.approve_plan()

    def test_project_edits_blocked(self):
        self.assertDenied(self.edit("src/a.py"), "not approved yet")
        self.assertDenied(self.bash("echo x > src/a.py"), "not approved yet")

    def test_route_file_writable_with_write_tool(self):
        result = guard.pre_tool(self.inp(tool_name="Write", tool_input={"file_path": self.route_file}), self.data)
        self.assertAllowed(result)

    def test_state_file_never_writable(self):
        result = guard.pre_tool(self.inp(tool_name="Write", tool_input={"file_path": self.state_file}), self.data)
        self.assertDenied(result, "managed by route-guard")

    def test_bash_cannot_touch_route_dir_or_cli(self):
        self.assertDenied(self.bash("cat {}".format(self.route_file)), "from Bash")
        self.assertDenied(self.bash("python3 /x/scripts/{} approve d s".format(guard.CLI_SCRIPT)), "user only")

    def test_outside_repo_allowed(self):
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other)
        result = guard.pre_tool(self.inp(tool_name="Write", tool_input={"file_path": os.path.join(other, "x")}),
                                self.data)
        self.assertAllowed(result)

    def test_stop_asks_for_route_once(self):
        result = self.stop()
        self.assertEqual(result.stdout["decision"], "block")
        self.assertIn(self.route_file, result.stdout["reason"])
        self.assertAllowed(self.stop(stop_hook_active=True))
        self.write_route(STEPS)
        self.assertAllowed(self.stop())

    def test_completion_blocked(self):
        self.assertEqual(self.complete("[R1] core").exit_code, 2)

    def test_invalid_route_not_approved(self):
        self.write_route([{"id": "1", "title": "x", "scope": ["src/**"], "done_when": ["true"]}])
        out = commands.approve(self.route_file, self.state_file)
        self.assertIn("NOT approved", out)
        self.assertEqual(self.state()["status"], state.DRAFT)


class ActiveTest(GuardTestCase):
    def setUp(self):
        super().setUp()
        self.activate(STEPS)

    def test_scope_of_unlocked_step_allowed(self):
        self.assertAllowed(self.edit("src/new.py"))
        self.assertAllowed(self.bash("sed -i 's/1/2/' src/a.py"))

    def test_locked_step_blocked_with_dependency(self):
        self.assertDenied(self.edit("docs/x.md"), "waits for step(s) 1")

    def test_out_of_scope_blocked(self):
        self.assertDenied(self.edit("README.md"), "outside the scope of every route step")
        self.assertDenied(self.bash("echo x >> README.md"), "outside the scope")

    def test_route_locked_after_approval(self):
        result = guard.pre_tool(self.inp(tool_name="Edit", tool_input={"file_path": self.route_file}), self.data)
        self.assertDenied(result, "locked")

    def test_criterion_failure_then_success(self):
        result = self.complete("[R1] core")
        self.assertEqual(result.exit_code, 2)
        self.assertIn("test -f src/b.py", result.stderr)
        self.assertIn("attempt 1 of", result.stderr)
        self.write("src/b.py", "b = 2\n")
        self.assertAllowed(self.complete("[R1] core"))
        self.assertEqual(self.state()["done"], ["1"])
        # Passo 1 feito libera o escopo do 2.
        self.assertAllowed(self.edit("docs/x.md"))

    def test_dependency_enforced_on_completion(self):
        result = self.complete("[R2] docs")
        self.assertEqual(result.exit_code, 2)
        self.assertIn("depends on step(s) 1", result.stderr)

    def test_unknown_step_and_untagged_task(self):
        self.assertEqual(self.complete("[R9] ghost").exit_code, 2)
        self.assertAllowed(self.complete("Refactor something"))

    def test_escalation_after_limit(self):
        result = [self.complete("[R1] core") for _ in range(state.MAX_ATTEMPTS)][-1]
        self.assertIn("Attempt limit reached", result.stderr)
        self.assertEqual(self.state()["status"], state.ESCALATED)
        self.assertDenied(self.edit("src/new.py"), "escalated")
        self.assertIn("escalated", self.stop().stdout["systemMessage"])
        self.assertIn("resumed after escalation", commands.approve(self.route_file, self.state_file))
        self.assertAllowed(self.edit("src/new.py"))

    def test_stop_blocks_open_steps_once(self):
        result = self.stop(last_assistant_message="Done with part of it.")
        self.assertEqual(result.stdout["decision"], "block")
        self.assertIn("[R1] core", result.stdout["reason"])
        self.assertAllowed(self.stop(stop_hook_active=True, last_assistant_message="Still working."))

    def test_stop_allows_question_to_user(self):
        self.assertAllowed(self.stop(last_assistant_message="Should I use approach A or B?"))

    def test_stop_catches_out_of_scope_changes(self):
        self.write("README.md", "changed behind the guard\n")
        result = self.stop(last_assistant_message="Which one?")
        self.assertEqual(result.stdout["decision"], "block")
        self.assertIn("README.md", result.stdout["reason"])
        result = self.stop(stop_hook_active=True)
        self.assertIn("escalated", result.stdout["systemMessage"])
        self.assertEqual(self.state()["status"], state.ESCALATED)

    def test_route_completes_and_closes(self):
        self.write("src/b.py", "b\n")
        self.assertAllowed(self.complete("[R1] core"))
        self.write("docs/x.md", "doc\n")
        self.assertAllowed(self.complete("[R2] docs"))
        self.assertEqual(self.state()["status"], state.DONE)
        self.assertDenied(self.edit("src/other.py"), "every route step is done")
        self.assertIn("Route closed", self.stop().stdout["systemMessage"])
        self.assertEqual(self.state()["status"], state.CLOSED)
        self.assertAllowed(self.edit("README.md"))


class ArtifactsAndRevertTest(GuardTestCase):
    def setUp(self):
        super().setUp()
        self.activate(STEPS)

    def test_generated_artifacts_ignored_by_stop(self):
        self.write("src/__pycache__/b.cpython-314.pyc", "x")
        self.write("tests/__pycache__/t.cpython-314.pyc", "x")
        self.assertAllowed(self.stop(last_assistant_message="Question?"))

    def test_generated_artifacts_writable(self):
        self.assertAllowed(self.bash("rm -rf tests/__pycache__"))

    def test_route_generated_field(self):
        self.write("build/out.bin", "x")
        result = self.stop(last_assistant_message="Question?")
        self.assertIn("build/out.bin", result.stdout["reason"])

    def test_removing_file_created_by_route_is_allowed_even_when_done(self):
        self.write("src/b.py", "b\n")
        self.complete("[R1] core")
        self.write("docs/x.md", "doc\n")
        self.complete("[R2] docs")
        self.write("notes.txt", "stray\n")
        self.assertEqual(self.state()["status"], state.DONE)
        self.assertAllowed(self.bash("rm notes.txt"))
        self.assertDenied(self.bash("echo x > notes2.txt"), "every route step is done")

    def test_restore_of_route_change_allowed(self):
        self.write("README.md", "changed\n")
        self.assertAllowed(self.bash("git restore README.md"))
        self.assertAllowed(self.bash("git checkout -- README.md"))

    def test_removing_tracked_file_outside_scope_denied(self):
        self.assertDenied(self.bash("rm README.md"), "outside the scope")

    def test_removing_git_dir_or_ignored_files_is_not_a_revert(self):
        self.write(".gitignore", ".env\n")
        git(self.repo, "add", ".gitignore")
        git(self.repo, "commit", "-qm", "ignore")
        self.write(".env", "SECRET=1\n")
        self.assertDenied(self.bash("rm -rf .git"), "outside the scope")
        self.assertDenied(self.bash("rm .env"), "outside the scope")

    def test_restore_from_another_ref_is_a_write(self):
        self.assertDenied(self.bash("git checkout other -- README.md"), "outside the scope")
        self.assertDenied(self.bash("git restore --source other README.md"), "outside the scope")

    def test_worktree_wide_git_resets_denied(self):
        self.assertDenied(self.bash("git reset --hard"), "outside the scope")
        self.assertDenied(self.bash("git stash"), "outside the scope")

    def test_bash_targets_follow_cd(self):
        self.assertDenied(self.bash("cd src && echo x > ../README.md"), "outside the scope")
        self.assertAllowed(self.bash("cd docs/.. && echo x > src/new.py"))

    def test_multiline_command_checked_per_line(self):
        self.assertDenied(self.bash("echo start\nrm README.md"), "outside the scope")

    def test_verdict_applied_to_state_reread_after_criteria(self):
        # O usuário derruba a rota enquanto o critério roda: o veredito não pode ressuscitá-la.
        st = self.state()
        st["status"] = state.ABANDONED
        route_file, state_file = self.route_file, self.state_file
        original = guard._first_failing_criterion

        def drop_route_meanwhile(commands_, root):
            state.save(state_file, st)
            return original(commands_, root)

        guard._first_failing_criterion = drop_route_meanwhile
        self.addCleanup(setattr, guard, "_first_failing_criterion", original)
        self.write("src/b.py", "b\n")
        self.assertAllowed(self.complete("[R1] core"))
        self.assertEqual(state.load(state_file)["status"], state.ABANDONED)
        self.assertTrue(os.path.exists(route_file))


class BaselineRevertTest(GuardTestCase):
    def test_user_work_dirty_before_route_is_protected(self):
        self.write("README.md", "user work in progress\n")
        self.write("scratch.txt", "user notes\n")
        self.activate(STEPS)
        self.assertDenied(self.bash("git restore README.md"), "outside the scope")
        self.assertDenied(self.bash("rm scratch.txt"), "outside the scope")


class BaselineTest(GuardTestCase):
    def test_dirty_before_approval(self):
        self.write("README.md", "dirty before the route\n")
        self.activate(STEPS)
        self.assertAllowed(self.stop(last_assistant_message="Question?"))
        self.write("README.md", "changed again\n")
        result = self.stop(last_assistant_message="Question?")
        self.assertEqual(result.stdout["decision"], "block")
        self.assertIn("README.md", result.stdout["reason"])

    def test_commits_during_route_still_audited(self):
        self.activate(STEPS)
        self.write("README.md", "sneaky\n")
        git(self.repo, "commit", "-qam", "sneaky")
        result = self.stop(last_assistant_message="Question?")
        self.assertIn("README.md", result.stdout["reason"])


class ParallelSessionTest(GuardTestCase):
    """Duas sessões no mesmo repo: uma não pode escalar por causa do trabalho da outra."""

    def other_session(self, session_id, steps, status=state.ACTIVE, repo_root=None):
        st = state.new_draft("other.md", repo_root or self.repo)
        state.activate(st, [s["id"] for s in steps], {"head": None, "dirty": {}})
        st["status"] = status
        state.save(route.state_path(self.data, session_id), st)
        with open(route.route_path(self.data, session_id), "w", encoding="utf-8") as fh:
            json.dump({"steps": steps}, fh)

    def test_other_sessions_route_scope_is_not_out_of_scope(self):
        self.activate(STEPS)
        self.other_session("sess-2", [{"id": "1", "title": "t", "scope": ["README.md"],
                                        "done_when": ["test -f README.md"], "depends_on": []}])
        self.write("README.md", "changed by the other session\n")
        self.assertAllowed(self.stop(last_assistant_message="Question?"))

    def test_finished_or_foreign_routes_do_not_cover(self):
        self.activate(STEPS)
        readme = [{"id": "1", "title": "t", "scope": ["README.md"], "done_when": ["test -f README.md"],
                   "depends_on": []}]
        self.other_session("sess-closed", readme, status=state.CLOSED)
        self.other_session("sess-foreign", readme, repo_root="/elsewhere")
        self.write("README.md", "changed\n")
        result = self.stop(last_assistant_message="Question?")
        self.assertIn("README.md", result.stdout["reason"])


class PruneTest(GuardTestCase):
    def make(self, session_id, status, repo_root, age_days):
        st = state.new_draft("p.md", repo_root)
        st["status"] = status
        path = route.state_path(self.data, session_id)
        state.save(path, st)
        with open(route.route_path(self.data, session_id), "w", encoding="utf-8") as fh:
            fh.write("{}")
        old = time.time() - age_days * 24 * 3600
        os.utime(path, (old, old))

    def exists(self, session_id):
        return os.path.exists(route.state_path(self.data, session_id))

    def test_prune_rules(self):
        self.make("closed-old-other", state.CLOSED, "/other", 8)
        self.make("closed-new-other", state.CLOSED, "/other", 2)
        self.make("abandoned-old", state.ABANDONED, self.repo, 8)
        self.make("active-stale-same", state.ACTIVE, self.repo, 31)
        self.make("active-stale-other", state.ACTIVE, "/other", 90)
        self.make("active-fresh-same", state.ACTIVE, self.repo, 5)
        self.make(SESSION, state.ACTIVE, self.repo, 90)
        self.approve_plan()
        self.assertFalse(self.exists("closed-old-other"))
        self.assertFalse(os.path.exists(route.route_path(self.data, "closed-old-other")))
        self.assertTrue(self.exists("closed-new-other"))
        self.assertFalse(self.exists("abandoned-old"))
        self.assertFalse(self.exists("active-stale-same"))
        # Rota em andamento de outro projeto nunca é apagada por este.
        self.assertTrue(self.exists("active-stale-other"))
        self.assertTrue(self.exists("active-fresh-same"))
        self.assertTrue(self.exists(SESSION))


class OffTest(GuardTestCase):
    def test_off_releases_guard(self):
        self.activate(STEPS)
        self.assertIn("route dropped", commands.off(self.route_file, self.state_file))
        self.assertAllowed(self.edit("README.md"))
        self.assertAllowed(self.stop())


class PluginFilesTest(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(PLUGIN_ROOT, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_skills_are_user_only_and_call_the_cli(self):
        for name in commands.COMMANDS:
            text = self.read("skills", name, "SKILL.md")
            self.assertIn("name: {}\n".format(name), text)
            self.assertIn("disable-model-invocation: true", text)
            self.assertIn("scripts/{} {} '${{CLAUDE_PLUGIN_DATA}}' '${{CLAUDE_SESSION_ID}}'".format(
                guard.CLI_SCRIPT, name), text)
            self.assertIn("Bash(python3 ${{CLAUDE_PLUGIN_ROOT}}/scripts/{}:*)".format(guard.CLI_SCRIPT), text)
        self.assertTrue(os.path.exists(os.path.join(PLUGIN_ROOT, "scripts", guard.CLI_SCRIPT)))

    def test_hooks_wiring(self):
        hooks = json.loads(self.read("hooks", "hooks.json"))["hooks"]
        self.assertEqual(hooks["PostToolUse"][0]["matcher"], "ExitPlanMode")
        self.assertEqual(set(hooks["PreToolUse"][0]["matcher"].split("|")), set(guard.FILE_TOOLS) | {"Bash"})
        for event in ("PostToolUse", "PreToolUse", "TaskCompleted", "Stop"):
            command = hooks[event][0]["hooks"][0]["command"]
            match = re.search(r"hooks/(\w+\.py)", command)
            if match is None:
                self.fail("hook command without a script: " + command)
            script = match.group(1)
            self.assertTrue(os.path.exists(os.path.join(PLUGIN_ROOT, "hooks", script)), script)
            self.assertIn('"${CLAUDE_PLUGIN_DATA}"', command)
        # O hook do critério precisa caber o orçamento inteiro dos critérios de um passo.
        self.assertGreater(hooks["TaskCompleted"][0]["hooks"][0]["timeout"], guard.CRITERIA_BUDGET)
        self.assertGreaterEqual(guard.CRITERIA_BUDGET, guard.CRITERION_TIMEOUT)

    def test_commands_match_skills_and_plugin_name(self):
        self.assertEqual(json.loads(self.read(".claude-plugin", "plugin.json"))["name"], commands.PLUGIN_NAME)
        self.assertEqual(set(os.listdir(os.path.join(PLUGIN_ROOT, "skills"))), set(commands.COMMANDS))


class HookProcessTest(GuardTestCase):
    """O hook de verdade, como o Claude Code chama: JSON no stdin, data dir no argv."""

    def run_hook(self, script, payload, data_dir=None):
        return subprocess.run([sys.executable, os.path.join(PLUGIN_ROOT, "hooks", script),
                               data_dir if data_dir is not None else self.data],
                              input=json.dumps(payload), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8", timeout=30)

    def test_deny_is_emitted_as_json(self):
        self.approve_plan()
        proc = self.run_hook("pre_tool.py", self.inp(tool_name="Edit",
                                                     tool_input={"file_path": os.path.join(self.repo, "src/a.py")}))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_task_block_is_exit_2(self):
        self.activate(STEPS)
        proc = self.run_hook("task_completed.py", self.inp(task_subject="[R1] core"))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("test -f src/b.py", proc.stderr)

    def test_fail_open_without_data_dir(self):
        proc = self.run_hook("stop.py", self.inp(), data_dir="${CLAUDE_PLUGIN_DATA}")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("guard skipped", json.loads(proc.stdout)["systemMessage"])

    def test_fail_open_on_bad_json(self):
        proc = subprocess.run([sys.executable, os.path.join(PLUGIN_ROOT, "hooks", "pre_tool.py"), self.data],
                              input="not json", stdout=subprocess.PIPE, encoding="utf-8", timeout=30)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
