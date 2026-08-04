import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import specgate_state as st


class StateBase(unittest.TestCase):
    """Só fixture, sem testes: herdar de uma classe COM testes faria cada
    subclasse reexecutar a suíte inteira da base.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, ".specgate"), exist_ok=True)

    def _write(self, name, text):
        with open(os.path.join(self.tmp, ".specgate", name), "w", encoding="utf-8") as fh:
            fh.write(text)


class StateTest(StateBase):
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
        vigentes = st.current_gates(self.tmp)
        self.assertEqual(len(vigentes), 1)
        self.assertEqual(vigentes[0]["rodada"], 3)

    def test_gates_vigentes_rodada_ausente_e_tratada_como_1(self):
        # Auditoria: a versão anterior deste teste chamava open_gates (não a
        # própria gates_vigentes que o nome promete) com uma fixture de UMA
        # entrada só — isso nunca exercitava eclipsamento nenhum, só provava
        # que uma entrada sem "rodada" aparece como aberta, algo que já é
        # coberto por outros testes. A versão corrigida chama gates_vigentes
        # diretamente e usa DUAS rodadas da mesma série: a entrada legada
        # sem "rodada" (implícita 1) precisa ser eclipsada pela rodada 2
        # explícita — provando que a ausência do campo é tratada como 1 DE
        # VERDADE (nem 0, nem um valor que escaparia da comparação por
        # maior rodada e "vazaria" as duas entradas como vigentes).
        self._write("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": "03", "status": "reprovado"},
            {"checkpoint": "testes", "pbi": "03", "rodada": 2, "status": "aguardando-po"},
        ]))
        vigentes = st.current_gates(self.tmp)
        self.assertEqual(len(vigentes), 1)
        self.assertEqual(vigentes[0]["rodada"], 2)

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


PBI = "docs/backlog/02-conversao.md"


class RoundLookupTest(StateBase):
    def _gates(self, *entries):
        self._write("gate.json", json.dumps(list(entries)))

    def test_round_for_devolve_zero_sem_a_serie(self):
        self.assertEqual(st.round_for(self.tmp, PBI, "testes"), 0)

    def test_round_for_devolve_a_maior_rodada_da_serie(self):
        self._gates(
            {"checkpoint": "testes", "pbi": PBI, "rodada": 1, "status": "reprovado"},
            {"checkpoint": "testes", "pbi": PBI, "rodada": 2, "status": "aprovado"},
            {"checkpoint": "aceite", "pbi": PBI, "rodada": 5, "status": "aprovado"},
        )
        self.assertEqual(st.round_for(self.tmp, PBI, "testes"), 2)

    def test_round_for_ignora_outro_pbi(self):
        self._gates({"checkpoint": "testes", "pbi": "docs/backlog/09-x.md",
                     "rodada": 3, "status": "aprovado"})
        self.assertEqual(st.round_for(self.tmp, PBI, "testes"), 0)

    def test_max_round_for_pbi_atravessa_checkpoints(self):
        self._gates(
            {"checkpoint": "testes", "pbi": PBI, "rodada": 1, "status": "aprovado"},
            {"checkpoint": "aceite", "pbi": PBI, "rodada": 4, "status": "reprovado"},
        )
        self.assertEqual(st.max_round_for_pbi(self.tmp, PBI), 4)

    def test_gate_json_corrompido_devolve_zero(self):
        self._write("gate.json", "{lixo")
        self.assertEqual(st.round_for(self.tmp, PBI, "testes"), 0)
        self.assertEqual(st.max_round_for_pbi(self.tmp, PBI), 0)


class RedTest(StateBase):
    def test_sem_registro_nao_ha_prova(self):
        self.assertFalse(st.red_proven(self.tmp, PBI, 1))

    def test_registro_da_mesma_rodada_conta_como_prova(self):
        st.record_red(self.tmp, PBI, 1, 1)
        self.assertTrue(st.red_proven(self.tmp, PBI, 1))

    def test_registro_de_rodada_anterior_nao_vale_para_a_seguinte(self):
        """Testes reprovados e reescritos abrem rodada nova: o vermelho da
        rodada passada foi provado contra OUTROS testes.
        """
        st.record_red(self.tmp, PBI, 1, 1)
        self.assertFalse(st.red_proven(self.tmp, PBI, 2))

    def test_registro_de_outro_pbi_nao_vale(self):
        st.record_red(self.tmp, "docs/backlog/09-x.md", 1, 1)
        self.assertFalse(st.red_proven(self.tmp, PBI, 1))

    def test_waived_fica_distinguivel_do_vermelho_observado(self):
        st.record_red(self.tmp, PBI, 1, None, waived=True)
        self.assertTrue(st.red_proven(self.tmp, PBI, 1))
        self.assertTrue(st.read_red(self.tmp)[PBI]["waived"])

    def test_red_json_corrompido_nao_conta_como_prova(self):
        self._write("red.json", "{lixo")
        self.assertFalse(st.red_proven(self.tmp, PBI, 1))

    def test_registro_de_um_pbi_nao_apaga_o_de_outro(self):
        st.record_red(self.tmp, PBI, 1, 1)
        st.record_red(self.tmp, "docs/backlog/03-outro.md", 1, 1)
        self.assertTrue(st.red_proven(self.tmp, PBI, 1))


class AttemptsTest(StateBase):
    def test_contador_comeca_em_zero(self):
        self.assertEqual(st.attempt_count(self.tmp, PBI, 1), 0)

    def test_bump_incrementa_e_persiste(self):
        self.assertEqual(st.bump_attempt(self.tmp, PBI, 1, "pytest -q"), 1)
        self.assertEqual(st.bump_attempt(self.tmp, PBI, 1, "pytest -q"), 2)
        self.assertEqual(st.attempt_count(self.tmp, PBI, 1), 2)

    def test_rodada_nova_zera_o_contador(self):
        st.bump_attempt(self.tmp, PBI, 1, "pytest")
        st.bump_attempt(self.tmp, PBI, 1, "pytest")
        self.assertEqual(st.attempt_count(self.tmp, PBI, 2), 0)
        self.assertEqual(st.bump_attempt(self.tmp, PBI, 2, "pytest"), 1)

    def test_pbi_novo_zera_o_contador(self):
        st.bump_attempt(self.tmp, PBI, 1, "pytest")
        self.assertEqual(st.attempt_count(self.tmp, "docs/backlog/03-outro.md", 1), 0)

    def test_historico_registra_o_comando_e_e_limitado(self):
        for i in range(st.MAX_ATTEMPT_HISTORY + 5):
            st.bump_attempt(self.tmp, PBI, 1, f"pytest -k caso{i}")
        data = st.read_attempts(self.tmp)
        self.assertEqual(data["count"], st.MAX_ATTEMPT_HISTORY + 5)
        self.assertEqual(len(data["history"]), st.MAX_ATTEMPT_HISTORY)
        self.assertIn("caso24", data["history"][-1]["cmd"])

    def test_attempts_json_corrompido_devolve_zero(self):
        self._write("attempts.json", "{lixo")
        self.assertEqual(st.attempt_count(self.tmp, PBI, 1), 0)

    def test_rodada_malformada_no_disco_nao_levanta(self):
        self._write("attempts.json", json.dumps({"pbi": PBI, "round": "x", "count": 3}))
        self.assertEqual(st.attempt_count(self.tmp, PBI, 1), 0)


if __name__ == "__main__":
    unittest.main()
