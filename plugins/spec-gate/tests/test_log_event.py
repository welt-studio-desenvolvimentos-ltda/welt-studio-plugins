import json
import os
import shutil
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


def run_hook_raw(raw_stdin, cwd):
    return subprocess.run(
        [sys.executable, LOG_EVENT], input=raw_stdin,
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
        # Baseline não-zero: se a implementação zerasse o contador a cada
        # evento, a asserção final (comparada com 0) passaria mesmo estando
        # errada. Estabelecer um valor != 0 antes garante que a asserção só
        # passa se Stop/PostToolUse de fato preservarem o valor.
        run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "baseline", "cwd": self.tmp}, self.tmp)
        baseline = st.read_seq(self.tmp)
        self.assertNotEqual(baseline, 0)

        run_hook({"hook_event_name": "Stop", "cwd": self.tmp}, self.tmp)
        run_hook({"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": self.tmp}, self.tmp)
        self.assertEqual(st.read_seq(self.tmp), baseline)

    def test_sem_specgate_json_nao_cria_estado(self):
        vazio = tempfile.mkdtemp()
        run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "oi", "cwd": vazio}, vazio)
        self.assertFalse(os.path.exists(os.path.join(vazio, ".specgate", "seq")))

    def test_entrada_do_log_carrega_seq(self):
        run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "oi", "cwd": self.tmp}, self.tmp)
        with open(os.path.join(self.tmp, ".specgate", "events.jsonl"), encoding="utf-8") as fh:
            entrada = json.loads(fh.readline())
        self.assertEqual(entrada["seq"], 1)

    def test_sai_zero_mesmo_sem_specgate_state_disponivel(self):
        """Import à prova de falha: instalação corrompida não pode derrubar o hook.

        Copiamos só o log_event.py para um diretório isolado, sem
        specgate_state.py ao lado, simulando um checkout parcial/corrompido.
        Python inclui o diretório do próprio script em sys.path, então o
        import falhará de verdade nesse processo novo.
        """
        isolado = tempfile.mkdtemp()
        copia = os.path.join(isolado, "log_event.py")
        shutil.copyfile(LOG_EVENT, copia)
        self.assertFalse(os.path.exists(os.path.join(isolado, "specgate_state.py")))

        result = subprocess.run(
            [sys.executable, copia],
            input=json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "oi", "cwd": self.tmp}),
            capture_output=True, text=True, cwd=self.tmp,
        )
        self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")

        # o resto do logging continua funcionando mesmo sem o campo seq
        with open(os.path.join(self.tmp, ".specgate", "events.jsonl"), encoding="utf-8") as fh:
            entrada = json.loads(fh.readline())
        self.assertNotIn("seq", entrada)
        self.assertEqual(entrada["event"], "UserPromptSubmit")

    def test_sai_zero_com_specgate_state_com_erro_de_sintaxe(self):
        """Instalação corrompida: specgate_state.py existe mas não compila.

        Simula um deploy incompleto/arquivo truncado. O import levanta
        SyntaxError, que não é subclasse de ImportError — precisa do
        `except Exception` para não escapar e derrubar o hook.
        """
        isolado = tempfile.mkdtemp()
        copia = os.path.join(isolado, "log_event.py")
        shutil.copyfile(LOG_EVENT, copia)
        with open(os.path.join(isolado, "specgate_state.py"), "w", encoding="utf-8") as fh:
            fh.write("def bump_seq(cwd)\n    return 1\n")  # falta ':' -> SyntaxError

        result = subprocess.run(
            [sys.executable, copia],
            input=json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "oi", "cwd": self.tmp}),
            capture_output=True, text=True, cwd=self.tmp,
        )
        self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")

        # o resto do logging continua funcionando mesmo sem o campo seq
        with open(os.path.join(self.tmp, ".specgate", "events.jsonl"), encoding="utf-8") as fh:
            entrada = json.loads(fh.readline())
        self.assertNotIn("seq", entrada)
        self.assertEqual(entrada["event"], "UserPromptSubmit")

    def test_payload_lista_sai_zero_sem_traceback(self):
        """Brecha fail-open: `cwd = payload.get("cwd") or os.getcwd()` (e os
        outros acessos a `.get`) rodavam ANTES de qualquer try/except. Um
        stdin JSON válido mas não-objeto (lista/número/string/null) não tem
        `.get(...)` — levantaria AttributeError e exit != 0, justamente no
        hook que grava o seq (a prova de turno).
        """
        r = run_hook_raw("[1, 2, 3]", self.tmp)
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")
        self.assertNotIn("Traceback", r.stderr)

    def test_cwd_como_lista_sai_zero_sem_traceback(self):
        """cwd de tipo inesperado (lista) dentro de um payload válido: o
        `or` original não pega isso (lista não-vazia é truthy), então
        `os.path.join(cwd, ...)` com `cwd` sendo uma lista levantava
        TypeError não capturado.
        """
        r = run_hook({
            "hook_event_name": "UserPromptSubmit",
            "prompt": "oi",
            "cwd": [1, 2],
        }, self.tmp)
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")
        self.assertNotIn("Traceback", r.stderr)

    def test_tool_input_nao_dict_sai_zero_sem_traceback(self):
        r = run_hook({
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": "nao é um objeto",
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")
        self.assertNotIn("Traceback", r.stderr)

    def test_sai_zero_com_specgate_state_sem_bump_seq(self):
        """Instalação desatualizada: specgate_state.py importa, mas não tem
        bump_seq/read_seq (versão parcial/antiga). A chamada levanta
        AttributeError, que precisa estar protegida no ponto de uso, não só
        no import.
        """
        isolado = tempfile.mkdtemp()
        copia = os.path.join(isolado, "log_event.py")
        shutil.copyfile(LOG_EVENT, copia)
        with open(os.path.join(isolado, "specgate_state.py"), "w", encoding="utf-8") as fh:
            fh.write("# versao parcial, sem bump_seq nem read_seq\n")

        result = subprocess.run(
            [sys.executable, copia],
            input=json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "oi", "cwd": self.tmp}),
            capture_output=True, text=True, cwd=self.tmp,
        )
        self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")

        # o resto do logging continua funcionando mesmo sem o campo seq
        with open(os.path.join(self.tmp, ".specgate", "events.jsonl"), encoding="utf-8") as fh:
            entrada = json.loads(fh.readline())
        self.assertNotIn("seq", entrada)
        self.assertEqual(entrada["event"], "UserPromptSubmit")


if __name__ == "__main__":
    unittest.main()
