import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPTS)
import specgate_state as st

LOG_EVENT = os.path.join(SCRIPTS, "log_event.py")


def run_hook(payload, cwd):
    return subprocess.run(
        [sys.executable, LOG_EVENT], input=json.dumps(payload),
        capture_output=True, text=True, cwd=cwd,
    )


class LogEventTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, ".specgate.json"), "w", encoding="utf-8") as fh:
            fh.write('{"test_command": "true"}')

    def test_user_prompt_incrementa_seq(self):
        run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "oi", "cwd": self.tmp}, self.tmp)
        self.assertEqual(st.read_seq(self.tmp), 1)
        run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "de novo", "cwd": self.tmp}, self.tmp)
        self.assertEqual(st.read_seq(self.tmp), 2)

    def test_outros_eventos_nao_incrementam_seq(self):
        run_hook({"hook_event_name": "Stop", "cwd": self.tmp}, self.tmp)
        run_hook({"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": self.tmp}, self.tmp)
        self.assertEqual(st.read_seq(self.tmp), 0)

    def test_sem_specgate_json_nao_cria_estado(self):
        vazio = tempfile.mkdtemp()
        run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "oi", "cwd": vazio}, vazio)
        self.assertFalse(os.path.exists(os.path.join(vazio, ".specgate", "seq")))

    def test_entrada_do_log_carrega_seq(self):
        run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "oi", "cwd": self.tmp}, self.tmp)
        with open(os.path.join(self.tmp, ".specgate", "events.jsonl"), encoding="utf-8") as fh:
            entrada = json.loads(fh.readline())
        self.assertEqual(entrada["seq"], 1)


if __name__ == "__main__":
    unittest.main()
