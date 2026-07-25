import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import specgate_state as st


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, ".specgate"), exist_ok=True)

    def _write(self, name, text):
        with open(os.path.join(self.tmp, ".specgate", name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_read_seq_ausente_devolve_zero(self):
        self.assertEqual(st.read_seq(self.tmp), 0)

    def test_read_seq_corrompido_devolve_zero(self):
        self._write("seq", "nao-e-numero")
        self.assertEqual(st.read_seq(self.tmp), 0)

    def test_bump_seq_incrementa_e_persiste(self):
        self.assertEqual(st.bump_seq(self.tmp), 1)
        self.assertEqual(st.bump_seq(self.tmp), 2)
        self.assertEqual(st.read_seq(self.tmp), 2)

    def test_bump_seq_nao_corrompe_arquivo_se_replace_falhar(self):
        """Escrita atômica: uma falha no meio do rename não deve deixar o
        arquivo "seq" truncado/regredido — o valor anterior tem que sobreviver.

        Isso prova o que uma escrita direta (open "w" + write) não garante:
        com kill -9/disco cheio no meio, "seq" acabaria truncado, read_seq
        devolveria 0 e o gate travaria para sempre (nunca mais alcança o
        opened_at_seq antigo).
        """
        self.assertEqual(st.bump_seq(self.tmp), 1)
        with mock.patch("specgate_state.os.replace", side_effect=OSError("disco cheio")):
            resultado = st.bump_seq(self.tmp)
        # bump_seq falha graciosamente (fail-open) e devolve o valor persistido
        self.assertEqual(resultado, 1)
        # o arquivo "seq" original não foi tocado pela escrita que falhou
        self.assertEqual(st.read_seq(self.tmp), 1)
        # nenhum temporário sobra no diretório de estado
        restantes = os.listdir(os.path.join(self.tmp, ".specgate"))
        self.assertEqual(restantes, ["seq"])

    def test_read_gates_tolera_dict_unico(self):
        self._write("gate.json", json.dumps({"checkpoint": "testes", "status": "aguardando-po"}))
        self.assertEqual(len(st.read_gates(self.tmp)), 1)

    def test_read_gates_corrompido_devolve_lista_vazia(self):
        self._write("gate.json", "{lixo")
        self.assertEqual(st.read_gates(self.tmp), [])

    def test_open_gates_filtra_por_status(self):
        self._write("gate.json", json.dumps([
            {"checkpoint": "backlog", "status": "aprovado"},
            {"checkpoint": "testes", "status": "aguardando-po"},
        ]))
        abertos = st.open_gates(self.tmp)
        self.assertEqual([g["checkpoint"] for g in abertos], ["testes"])

    def test_open_gates_ignora_rodada_anterior_decidida_reprovado(self):
        # Task 9: rodada 1 reprovada, rodada 2 aberta — o gate vigente é
        # SÓ a rodada 2. A rodada 1 (registro histórico) não pode aparecer
        # como pendente.
        self._write("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1, "status": "reprovado"},
            {"checkpoint": "testes", "pbi": "03", "rodada": 2, "status": "aguardando-po"},
        ]))
        abertos = st.open_gates(self.tmp)
        self.assertEqual(len(abertos), 1)
        self.assertEqual(abertos[0]["rodada"], 2)

    def test_open_gates_rodada_anterior_decidida_nao_conta_mesmo_com_status_stale(self):
        # Robustez: mesmo que uma rodada ANTERIOR ainda traga
        # "aguardando-po" em disco (não deveria acontecer sob o guard, mas
        # a leitura não depende dessa garantia), só a rodada de MAIOR
        # número decide se há algo pendente.
        self._write("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1, "status": "aguardando-po"},
            {"checkpoint": "testes", "pbi": "03", "rodada": 2, "status": "aprovado"},
        ]))
        self.assertEqual(st.open_gates(self.tmp), [])

    def test_gates_vigentes_seleciona_maior_rodada_por_serie(self):
        self._write("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1, "status": "reprovado"},
            {"checkpoint": "testes", "pbi": "03", "rodada": 2, "status": "reprovado"},
            {"checkpoint": "testes", "pbi": "03", "rodada": 3, "status": "aguardando-po"},
        ]))
        vigentes = st.gates_vigentes(self.tmp)
        self.assertEqual(len(vigentes), 1)
        self.assertEqual(vigentes[0]["rodada"], 3)

    def test_gates_vigentes_rodada_ausente_e_tratada_como_1(self):
        # Compatibilidade: gate legado sem "rodada" (rodada implícita 1)
        # não pode ser eclipsado por engano.
        self._write("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": "03", "status": "aguardando-po"},
        ]))
        abertos = st.open_gates(self.tmp)
        self.assertEqual(len(abertos), 1)

    def test_has_human_turn_since(self):
        self._write("seq", "15")
        self.assertTrue(st.has_human_turn_since(self.tmp, 10))
        self.assertFalse(st.has_human_turn_since(self.tmp, 15))
        self.assertFalse(st.has_human_turn_since(self.tmp, 20))

    def test_has_human_turn_since_seq_invalido_e_falso(self):
        self._write("seq", "15")
        self.assertFalse(st.has_human_turn_since(self.tmp, None))

    def test_gate_po_1_passed(self):
        self.assertFalse(st.gate_po_1_passed(self.tmp))
        self._write("batch.json", json.dumps({"backlog_aprovado": True}))
        self.assertTrue(st.gate_po_1_passed(self.tmp))


if __name__ == "__main__":
    unittest.main()
