import json
import os
import sys
import tempfile
import unittest

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
