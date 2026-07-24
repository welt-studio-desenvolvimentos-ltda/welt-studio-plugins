import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPTS)

GUARD = os.path.join(SCRIPTS, "gate_guard.py")


def run_guard(payload, cwd):
    return subprocess.run(
        [sys.executable, GUARD], input=json.dumps(payload),
        capture_output=True, text=True, cwd=cwd,
    )


class GuardBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, ".specgate"), exist_ok=True)
        self.config({"test_command": "true", "source_paths": ["src"]})

    def config(self, data):
        with open(os.path.join(self.tmp, ".specgate.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def state(self, name, text):
        with open(os.path.join(self.tmp, ".specgate", name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def bash(self, cmd):
        return run_guard({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": self.tmp}, self.tmp)


class PoGateTest(GuardBase):
    def test_gate_aberto_bloqueia_transicao_de_fase_por_bash(self):
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "status": "aguardando-po", "opened_at_seq": 5}
        ]))
        r = self.bash("printf 'implementing' > .specgate/phase")
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DE PO ABERTO", r.stderr)

    def test_gate_aberto_bloqueia_transicao_por_write(self):
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "status": "aguardando-po", "opened_at_seq": 5}
        ]))
        r = run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": ".specgate/phase", "content": "implementing"},
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 2)

    def test_gate_fechado_permite_transicao(self):
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "status": "aprovado", "opened_at_seq": 5}
        ]))
        self.assertEqual(self.bash("printf 'implementing' > .specgate/phase").returncode, 0)

    def test_sem_gate_permite_transicao(self):
        self.assertEqual(self.bash("printf 'implementing' > .specgate/phase").returncode, 0)

    def test_gate_aberto_nao_bloqueia_outros_arquivos(self):
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "status": "aguardando-po", "opened_at_seq": 5}
        ]))
        self.assertEqual(self.bash("printf 'oi' > /tmp/qualquer-coisa.txt").returncode, 0)

    def test_gate_json_corrompido_falha_aberto(self):
        self.state("gate.json", "{lixo")
        self.assertEqual(self.bash("printf 'implementing' > .specgate/phase").returncode, 0)

    def test_sem_specgate_json_guard_inerte(self):
        os.remove(os.path.join(self.tmp, ".specgate.json"))
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "status": "aguardando-po", "opened_at_seq": 5}
        ]))
        self.assertEqual(self.bash("printf 'implementing' > .specgate/phase").returncode, 0)


class InterpreterEscapeTest(GuardBase):
    """Cobre o furo: invocação de interpretador escrevendo em arquivo de
    estado escapava por completo de BASH_WRITE_RES (sed/tee/>/mv|cp|rm/
    truncate), então `write_targets` devolvia [] e `guard_po_gate` nunca
    avaliava o alvo.
    """

    def _abre_gate(self):
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "status": "aguardando-po", "opened_at_seq": 5}
        ]))

    def test_python3_dash_c_escrevendo_phase_e_bloqueado(self):
        self._abre_gate()
        r = self.bash(
            "python3 -c \"open('.specgate/phase','w').write('implementing')\""
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DE PO ABERTO", r.stderr)

    def test_sh_dash_c_escrevendo_phase_e_bloqueado(self):
        self._abre_gate()
        r = self.bash('sh -c "echo implementing > .specgate/phase"')
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DE PO ABERTO", r.stderr)

    def test_bash_dash_c_escrevendo_phase_e_bloqueado(self):
        self._abre_gate()
        r = self.bash('bash -c "echo implementing > .specgate/phase"')
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DE PO ABERTO", r.stderr)

    def test_python3_dash_c_sem_tocar_arquivo_de_estado_passa(self):
        self._abre_gate()
        r = self.bash("python3 -c \"print('hello world')\"")
        self.assertEqual(r.returncode, 0)

    def test_node_dash_e_escrevendo_phase_e_bloqueado(self):
        self._abre_gate()
        r = self.bash(
            "node -e \"require('fs').writeFileSync('.specgate/phase','implementing')\""
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DE PO ABERTO", r.stderr)

    def test_ruby_dash_e_escrevendo_phase_e_bloqueado(self):
        self._abre_gate()
        r = self.bash(
            "ruby -e \"File.write('.specgate/phase','implementing')\""
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DE PO ABERTO", r.stderr)

    def test_php_dash_r_escrevendo_phase_e_bloqueado(self):
        self._abre_gate()
        r = self.bash(
            "php -r \"file_put_contents('.specgate/phase','implementing');\""
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DE PO ABERTO", r.stderr)

    def test_perl_dash_e_open_3_argumentos_e_bloqueado(self):
        self._abre_gate()
        r = self.bash(
            "perl -e \"open(my $fh, '>', '.specgate/phase'); print $fh 'implementing';\""
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DE PO ABERTO", r.stderr)

    def test_perl_dash_e_open_2_argumentos_com_modo_colado_e_bloqueado(self):
        # Idioma clássico do Perl: open de 2 argumentos com o modo (>) colado
        # no caminho. A string extraída vira '>.specgate/phase', que não bate
        # por igualdade de path se não for tratada.
        self._abre_gate()
        r = self.bash(
            "perl -e \"open(F,'>.specgate/phase'); print F 'implementing'; close(F);\""
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DE PO ABERTO", r.stderr)

    def test_python3_dash_c_sem_gate_aberto_passa(self):
        r = self.bash(
            "python3 -c \"open('.specgate/phase','w').write('implementing')\""
        )
        self.assertEqual(r.returncode, 0)

    def test_sh_dash_c_sem_gate_aberto_passa(self):
        r = self.bash('sh -c "echo implementing > .specgate/phase"')
        self.assertEqual(r.returncode, 0)

    def test_bash_dash_c_sem_gate_aberto_passa(self):
        r = self.bash('bash -c "echo implementing > .specgate/phase"')
        self.assertEqual(r.returncode, 0)


class PoGateFailOpenTest(GuardBase):
    """Import à prova de falha: specgate_state corrompido/incompleto não pode
    fazer o gate_guard.py (hook BLOQUEANTE) explodir. Um guard que quebra a
    sessão do usuário é pior que um guard ausente — precisa sair 0 e permitir
    a ação mesmo com o módulo em mau estado.
    """

    def _run_isolado(self, specgate_state_source):
        """Copia só o gate_guard.py para um diretório isolado e, se fornecido,
        escreve um specgate_state.py alternativo ao lado — simulando checkout
        parcial (módulo ausente) ou instalação corrompida/desatualizada.
        """
        isolado = tempfile.mkdtemp()
        copia = os.path.join(isolado, "gate_guard.py")
        shutil.copyfile(GUARD, copia)
        if specgate_state_source is not None:
            with open(os.path.join(isolado, "specgate_state.py"), "w", encoding="utf-8") as fh:
                fh.write(specgate_state_source)

        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "status": "aguardando-po", "opened_at_seq": 5}
        ]))

        return subprocess.run(
            [sys.executable, copia],
            input=json.dumps({
                "tool_name": "Bash",
                "tool_input": {"command": "printf 'implementing' > .specgate/phase"},
                "cwd": self.tmp,
            }),
            capture_output=True, text=True, cwd=self.tmp,
        )

    def test_sai_zero_mesmo_sem_specgate_state_disponivel(self):
        """specgate_state.py ausente (checkout parcial/corrompido): o import
        falha de verdade nesse processo novo, sem specgate_state.py ao lado.
        """
        result = self._run_isolado(None)
        self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")

    def test_sai_zero_com_specgate_state_com_erro_de_sintaxe(self):
        """specgate_state.py existe mas não compila (deploy incompleto/arquivo
        truncado). O import levanta SyntaxError, que não é subclasse de
        ImportError — precisa do `except Exception` para não escapar.
        """
        result = self._run_isolado("def open_gates(cwd)\n    return []\n")  # falta ':' -> SyntaxError
        self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")

    def test_sai_zero_com_specgate_state_sem_open_gates(self):
        """specgate_state.py importa, mas não tem open_gates (versão
        parcial/antiga). A chamada levanta AttributeError, que precisa estar
        protegida no ponto de uso, não só no import.
        """
        result = self._run_isolado("# versao parcial, sem open_gates\n")
        self.assertEqual(result.returncode, 0, msg=f"stderr: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
