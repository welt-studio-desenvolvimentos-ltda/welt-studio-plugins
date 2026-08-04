import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, SCRIPTS)

import gate_guard

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
        # require_red desligado na base de propósito: o test_command da
        # fixture é "true" (suíte sempre verde), então a prova de RED
        # bloquearia toda transição para 'implementing' e contaminaria os
        # testes dos OUTROS guards, que usam essa transição só como uma
        # escrita qualquer em .specgate/phase. Quem testa o RED liga o flag.
        self.config({"test_command": "true", "source_paths": ["src"], "require_red": False})

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

    def test_redirecionamento_do_shell_fora_do_codigo_inline_e_bloqueado(self):
        # O alvo está no REDIRECIONAMENTO do shell, não dentro do código
        # passado ao interpretador. Devolver só os candidatos extraídos do
        # código inline (e ignorar a varredura de tokens) deixaria este alvo
        # invisível para todos os guards.
        self._abre_gate()
        r = self.bash("python3 -c \"print('implementing')\" > .specgate/phase")
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DE PO ABERTO", r.stderr)

    def test_sh_dash_c_com_redirecionamento_do_shell_e_bloqueado(self):
        self._abre_gate()
        r = self.bash("sh -c 'printf implementing' > .specgate/phase")
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DE PO ABERTO", r.stderr)


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


class GateClearTest(GuardBase):
    def _abre(self, opened_at_seq=10, checkpoint="testes"):
        self.state("gate.json", json.dumps([
            {"checkpoint": checkpoint, "status": "aguardando-po",
             "opened_at_seq": opened_at_seq}
        ]))

    def _dois_gates(self):
        self.state("gate.json", json.dumps([
            {"checkpoint": "aceite", "pbi": "03", "status": "aguardando-po", "opened_at_seq": 10},
            {"checkpoint": "aceite", "pbi": "05", "status": "aguardando-po", "opened_at_seq": 20},
        ]))

    def _escreve_gate(self, content="[]"):
        return run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": ".specgate/gate.json", "content": content},
            "cwd": self.tmp,
        }, self.tmp)

    def test_sem_turno_humano_bloqueia_escrita_no_gate(self):
        # MUDANÇA DE CONTRATO: sob a semântica antiga este teste usava
        # esvaziar ([]) como veículo para testar "falta de turno". Sob a
        # nova semântica, esvaziar um gate aberto é SEMPRE bloqueado (Aresta
        # B, ver test_esvaziar_gate_aberto_com_turno_e_bloqueado acima),
        # independente de turno — então não serve mais para exercitar
        # especificamente a falta de turno. O veículo correto agora é uma
        # DECISÃO (mudança de status preservando opened_at_seq) sem turno
        # humano posterior.
        self._abre(opened_at_seq=10)
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "status": "aprovado", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("AUTO-LIBERAÇÃO BLOQUEADA", r.stderr)

    def test_com_turno_humano_permite_decidir_gate_mantendo_opened_at_seq(self):
        # MUDANÇA DE CONTRATO: sob a semântica antiga este teste liberava o
        # gate ESVAZIANDO o gate.json ([]). Isso não é mais "decidir" — a
        # liberação legítima muda o STATUS do gate para decidido preservando
        # opened_at_seq, nunca apaga a entrada. Ver
        # test_esvaziar_gate_aberto_com_turno_e_bloqueado logo abaixo para o
        # contrato novo do caso "esvaziar".
        self._abre(opened_at_seq=10)
        self.state("seq", "11")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "status": "aprovado", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_esvaziar_gate_aberto_com_turno_e_bloqueado(self):
        # Aresta B: a decisão legítima MUDA o status do gate (preservando a
        # entrada como registro), nunca o apaga. Esvaziar o gate.json ([])
        # com gate aberto é auto-liberação por deleção — "apagar não é
        # decidir" — e precisa ser bloqueado MESMO com turno humano
        # presente, ao contrário do contrato antigo que este teste
        # substitui.
        self._abre(opened_at_seq=10)
        self.state("seq", "11")
        r = self._escreve_gate("[]")
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE ABERTO DELETADO BLOQUEADO", r.stderr)

    def test_deletar_gate_ja_decidido_e_limpeza_legitima(self):
        # Não-regressão: deletar/omitir uma chave que já estava DECIDIDA
        # (não em `aguardando-po`) é limpeza legítima do gate.json, mesmo
        # sem turno humano novo — não há gate aberto em jogo.
        self.state("gate.json", json.dumps([
            {"checkpoint": "x", "status": "aprovado", "opened_at_seq": 10},
        ]))
        self.state("seq", "10")
        r = self._escreve_gate("[]")
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_rm_gate_json_via_bash_com_gate_aberto_e_turno_e_bloqueado(self):
        # Aresta B via Bash: `rm` não expõe o conteúdo final (Edit/Bash já
        # caem na regra conservadora), e com gate aberto isso agora bloqueia
        # SEMPRE, mesmo com turno humano — não há como confirmar que o
        # resultado é uma decisão e não uma deleção.
        self._abre(opened_at_seq=10)
        self.state("seq", "11")
        r = self.bash("rm .specgate/gate.json")
        self.assertEqual(r.returncode, 2)

    def test_truncar_gate_json_via_bash_com_gate_aberto_e_turno_e_bloqueado(self):
        self._abre(opened_at_seq=10)
        self.state("seq", "11")
        r = self.bash("> .specgate/gate.json")
        self.assertEqual(r.returncode, 2)

    def test_decidir_gate_aberto_alterando_opened_at_seq_e_bloqueado(self):
        # Aresta A: o opened_at_seq de um gate aberto é imutável ao decidir.
        # Rebaixá-lo (ou alterá-lo de qualquer forma) na MESMA escrita que
        # decide reescreveria o carimbo temporal que a checagem de turno
        # usa — antes deste fix, `_decided_without_turn` validava contra o
        # valor ANTERIOR em disco e ignorava por completo o valor
        # fabricado aqui, então esta escrita passava (exit 0) mesmo
        # forjando o campo.
        self._abre(opened_at_seq=10)
        self.state("seq", "11")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "status": "aprovado", "opened_at_seq": 0},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("DECISÃO ALTERA opened_at_seq BLOQUEADA", r.stderr)

    def test_bloqueia_tambem_via_bash(self):
        self._abre(opened_at_seq=10)
        self.state("seq", "10")
        r = self.bash("printf '[]' > .specgate/gate.json")
        self.assertEqual(r.returncode, 2)

    def test_sem_gate_aberto_escrita_livre(self):
        self.assertEqual(self._escreve_gate().returncode, 0)

    def test_decidir_gate_sem_turno_e_bloqueado_mesmo_com_outro_liberado(self):
        # PBI-03 abriu em 10 (tem turno: seq=15). PBI-05 abriu em 20 (não tem).
        # Marcar o 05 como aprovado é auto-liberação: PAREDE.
        self._dois_gates()
        self.state("seq", "15")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "aceite", "pbi": "03", "status": "aguardando-po", "opened_at_seq": 10},
            {"checkpoint": "aceite", "pbi": "05", "status": "aprovado", "opened_at_seq": 20},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("05", r.stderr)

    def test_decidir_so_o_gate_com_turno_e_permitido(self):
        # O 03 tem turno posterior; decidir SÓ ele passa. O 05 fica aberto.
        self._dois_gates()
        self.state("seq", "15")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "aceite", "pbi": "03", "status": "aprovado", "opened_at_seq": 10},
            {"checkpoint": "aceite", "pbi": "05", "status": "aguardando-po", "opened_at_seq": 20},
        ]))
        self.assertEqual(r.returncode, 0)

    def test_ambos_com_turno_hook_permite_decidir_os_dois(self):
        # LIMITE CONHECIDO: com turno posterior aos dois, o hook não distingue
        # se a fala do PO cobre ambos. Isso é semântica, e hook não lê
        # semântica — segurar o 05 aqui é instrução do comando, não parede.
        self._dois_gates()
        self.state("seq", "21")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "aceite", "pbi": "03", "status": "aprovado", "opened_at_seq": 10},
            {"checkpoint": "aceite", "pbi": "05", "status": "aprovado", "opened_at_seq": 20},
        ]))
        self.assertEqual(r.returncode, 0)

    def test_conteudo_indisponivel_cai_na_regra_conservadora(self):
        # Bash não expõe o conteúdo pretendido: barra a escrita inteira.
        self._dois_gates()
        self.state("seq", "15")
        self.assertEqual(self.bash("printf '[]' > .specgate/gate.json").returncode, 2)

    def test_chave_duplicada_com_aprovado_e_aguardando_po_e_bloqueada(self):
        # Critical 1: duas entradas com a MESMA chave em `novos`, uma
        # "aprovado" e outra "aguardando-po". Um dict comum (`{chave: g}`)
        # deixaria a última sobrevivver e, se ela for a "aguardando-po", a
        # escrita passaria carregando junto a entrada "aprovado" fabricada.
        self._abre(opened_at_seq=10, checkpoint="testes")
        self.state("seq", "10")  # sem turno humano desde a abertura
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "status": "aprovado", "opened_at_seq": 10},
            {"checkpoint": "testes", "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("AUTO-LIBERAÇÃO BLOQUEADA", r.stderr)

    def test_gate_novo_fabricado_aprovado_com_gate_real_aberto_ao_lado(self):
        # Critical 2: chave NUNCA vista antes, escrita direto como "aprovado".
        # `_decided_without_turn` só iterava sobre `open_gates`, então uma
        # chave nova nunca era examinada. O gate real fica intocado ao lado
        # (aguardando-po) para provar que o bloqueio é sobre a chave nova, não
        # um efeito colateral de proteger o gate real.
        self._abre(opened_at_seq=5, checkpoint="testes")
        self.state("seq", "99")  # turno humano de sobra — não é isso que falta
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "status": "aguardando-po", "opened_at_seq": 5},
            {"checkpoint": "checkpoint-fabricado", "status": "aprovado", "opened_at_seq": 1},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("AUTO-LIBERAÇÃO BLOQUEADA", r.stderr)
        self.assertIn("checkpoint-fabricado", r.stderr)

    def test_gate_novo_fabricado_aprovado_opened_at_seq_baixo_sem_outros_gates(self):
        # Critical 2, forma mais nua: NENHUM gate aberto em disco (abertos
        # vazio), então o `if not abertos: return` antigo nunca chegava a
        # chamar `_decided_without_turn`. `opened_at_seq` baixo (0/1) é
        # auto-declarado por quem escreve — mesmo turno humano de sobra não
        # pode validar uma chave que nunca existiu no estado anterior.
        self.state("seq", "5")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "checkpoint-fabricado", "status": "aprovado", "opened_at_seq": 0},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("AUTO-LIBERAÇÃO BLOQUEADA", r.stderr)

    def test_abrir_gate_novo_aguardando_po_continua_permitido(self):
        # Não-regressão: abrir gate novo (chave nunca vista, status
        # aguardando-po) NÃO é decisão — precisa continuar liberado mesmo sem
        # nenhum gate aberto em disco e sem turno humano.
        self.state("seq", "0")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "feature-nova", "status": "aguardando-po", "opened_at_seq": 1},
        ]))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")


class GateDecididoImutavelTest(GuardBase):
    """Fecha a ÚLTIMA brecha: uma vez decidido, um gate vira registro de
    auditoria congelado (categoria 2 do modelo unificado). Antes deste fix,
    a proteção de imutabilidade só cobria gates 'aguardando-po' no estado
    anterior — assim que a chave saía dessa lista pela primeira vez (uma
    decisão legítima), ela ficava livre para ser reescrita à vontade, porque
    `_decided_without_turn` validava contra o `opened_at_seq` ORIGINAL
    registrado na abertura, e o `seq` global só precisava ultrapassar aquele
    valor UMA vez — não a cada reescrita. Reproduzido ao vivo: 8 flip-flops
    seguidos entre 'aprovado' e 'reprovado', seq nunca avança, todos rc=0.
    """

    def _escreve_gate(self, content):
        return run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": ".specgate/gate.json", "content": content},
            "cwd": self.tmp,
        }, self.tmp)

    def test_flip_flop_reprovado_para_aprovado_sem_turno_novo_e_bloqueado(self):
        # O FURO relatado pela auditoria, na forma mais direta: gate abre em
        # 5, decide REPROVADO com turno real (seq=10) — decisão legítima.
        # Em seguida, SEM nenhum UserPromptSubmit novo (seq continua 10), o
        # Claude tenta reescrever a MESMA chave como APROVADO. Isto precisa
        # ser bloqueado: gate decidido é imutável, e inverter o veredito sem
        # fala nova do PO é exatamente a auto-liberação que este fix fecha.
        self.state("gate.json", json.dumps([
            {"checkpoint": "x", "status": "reprovado", "opened_at_seq": 5},
        ]))
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "x", "status": "aprovado", "opened_at_seq": 5},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DECIDIDO É IMUTÁVEL BLOQUEADO", r.stderr)

    def test_flip_flop_repetido_nunca_passa_mesmo_sem_seq_avancar(self):
        # Reprodução fiel do relato da auditoria: 8 tentativas alternadas de
        # inverter o veredito, todas na mesma sessão, seq nunca avança. Como
        # o Claude Code só persiste a escrita quando o hook libera (rc=0),
        # simulamos isso aqui: só reescrevemos gate.json em disco quando o
        # guard permite. O invariante de verdade não é "toda tentativa
        # individual bloqueia" (uma tentativa que repete o valor já em
        # disco é uma reescrita idêntica, legitimamente permitida sem
        # turno) — é que o STATUS EM DISCO nunca sai do valor decidido
        # originalmente ("reprovado"): nenhum flip real e efetivo acontece.
        self.state("gate.json", json.dumps([
            {"checkpoint": "x", "status": "reprovado", "opened_at_seq": 5},
        ]))
        self.state("seq", "10")
        status = "aprovado"
        gate_path = os.path.join(self.tmp, ".specgate", "gate.json")
        for i in range(8):
            content = json.dumps([
                {"checkpoint": "x", "status": status, "opened_at_seq": 5},
            ])
            r = self._escreve_gate(content)
            if r.returncode == 0:
                self.state("gate.json", content)  # Write real teria persistido
            # Invariante checado A CADA iteração (não só no final): o status
            # em disco nunca pode ser diferente de "reprovado", senão um
            # flip de verdade escapou. Checar só ao final deixaria passar um
            # flip que fosse desfeito por coincidência de paridade do loop.
            with open(gate_path, encoding="utf-8") as fh:
                gates_em_disco = json.load(fh)
            self.assertEqual(
                gates_em_disco[0]["status"], "reprovado",
                msg=f"iteração {i}: status em disco mudou para "
                    f"{gates_em_disco[0]['status']!r} sem turno novo (rc={r.returncode})",
            )
            status = "reprovado" if status == "aprovado" else "aprovado"

    def test_alterar_opened_at_seq_de_gate_decidido_mantendo_status_e_bloqueado(self):
        # Segunda forma do mesmo furo: manter o status decidido idêntico mas
        # forjar um `opened_at_seq` novo. Isto corrompe o carimbo temporal
        # que qualquer auditoria futura usaria para saber quando a decisão
        # foi tomada, sem exigir turno humano nenhum.
        self.state("gate.json", json.dumps([
            {"checkpoint": "x", "status": "aprovado", "opened_at_seq": 10},
        ]))
        self.state("seq", "15")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "x", "status": "aprovado", "opened_at_seq": 99},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DECIDIDO É IMUTÁVEL BLOQUEADO", r.stderr)

    def test_reabrir_gate_decidido_com_opened_at_seq_fresco_e_bloqueado(self):
        # Forma mais sutil: reabrir como 'aguardando-po' usando um
        # opened_at_seq FRESCO (igual ao seq_atual, não herdado do passado)
        # na MESMA escrita que muda o status. Mesmo satisfazendo a exigência
        # `>= seq_atual` da categoria de abertura, isto ainda é mutação de um
        # registro congelado — a única forma legítima de "mudar de ideia" é
        # deletar a chave (limpeza) numa escrita e abri-la de novo como
        # chave nova (sem histórico) em outra.
        self.state("gate.json", json.dumps([
            {"checkpoint": "x", "status": "aprovado", "opened_at_seq": 10},
        ]))
        self.state("seq", "15")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "x", "status": "aguardando-po", "opened_at_seq": 15},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DECIDIDO É IMUTÁVEL BLOQUEADO", r.stderr)

    def test_gate_decidido_reescrito_identico_continua_permitido(self):
        # Não-regressão/sanidade: reescrever a MESMA chave decidida com
        # status e opened_at_seq idênticos (ex.: rewrite do arquivo inteiro
        # que só re-serializa o JSON) não é mutação nenhuma — precisa
        # continuar liberado.
        self.state("gate.json", json.dumps([
            {"checkpoint": "x", "status": "aprovado", "opened_at_seq": 10},
        ]))
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "x", "status": "aprovado", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_rewrite_preserva_gate_decidido_idêntico_enquanto_decide_outro_aberto(self):
        # Não-regressão central: um gate JÁ decidido (aprovado) fica
        # intocado e idêntico na mesma escrita em que OUTRO gate, aberto,
        # é decidido com turno humano real. A imutabilidade do primeiro não
        # pode travar a decisão legítima do segundo.
        self.state("gate.json", json.dumps([
            {"checkpoint": "ja-decidido", "status": "aprovado", "opened_at_seq": 3},
            {"checkpoint": "aberto", "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.state("seq", "11")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "ja-decidido", "status": "aprovado", "opened_at_seq": 3},
            {"checkpoint": "aberto", "status": "aprovado", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")


class AberturaAntedatadaTest(GuardBase):
    """Fecha o furo: opened_at_seq é escrito pelo próprio agente ao abrir um
    gate, e nada validava esse valor. Abrir com opened_at_seq no passado
    (menor que o seq atual) tornaria a aprovação seguinte forjável — o seq já
    teria "ultrapassado" aquele valor por turnos ANTERIORES à abertura, sem
    exigir nenhuma fala nova do PO depois que o gate abriu.
    """

    def _escreve_gate(self, content):
        return run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": ".specgate/gate.json", "content": content},
            "cwd": self.tmp,
        }, self.tmp)

    def test_abrir_gate_novo_com_opened_at_seq_menor_que_seq_atual_e_bloqueado(self):
        self.state("seq", "5")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "novo", "status": "aguardando-po", "opened_at_seq": 0},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("ABERTURA DE GATE ANTEDATADA BLOQUEADA", r.stderr)

    def test_abrir_gate_com_opened_at_seq_igual_ao_seq_atual_e_permitido(self):
        self.state("seq", "5")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "novo", "status": "aguardando-po", "opened_at_seq": 5},
        ]))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_abrir_gate_com_opened_at_seq_maior_que_seq_atual_e_permitido(self):
        # Carimbar com um seq maior que o atual não é ataque — é conservador
        # para cima. O comando pode preferir "reservar" um seq futuro.
        self.state("seq", "5")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "novo", "status": "aguardando-po", "opened_at_seq": 6},
        ]))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_rewrite_que_preserva_gate_aberto_com_seq_atual_avancado_e_permitido(self):
        # O gate abriu com opened_at_seq=5. Depois disso o usuário falou (seq
        # sobe para 7). Um rewrite que apenas ACRESCENTA outro gate e mantém
        # este idêntico (mesma chave, mesmo opened_at_seq) não é abertura
        # nova — é preservação, e precisa continuar permitido mesmo com
        # seq_atual > opened_at_seq daquele gate preservado.
        self.state("gate.json", json.dumps([
            {"checkpoint": "existente", "status": "aguardando-po", "opened_at_seq": 5},
        ]))
        self.state("seq", "7")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "existente", "status": "aguardando-po", "opened_at_seq": 5},
            {"checkpoint": "novo", "status": "aguardando-po", "opened_at_seq": 7},
        ]))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_reabrir_chave_decidida_com_opened_at_seq_historico_e_bloqueada(self):
        # Ataque real: uma chave já DECIDIDA (aprovado, opened_at_seq=10) é
        # reescrita de volta como aguardando-po com o MESMO opened_at_seq=10,
        # herdado do valor histórico, quando o seq atual já passou de 10.
        # Antes deste fix isto "passava como preservação" — a checagem
        # antiga só olhava se o VALOR batia com QUALQUER entrada anterior da
        # chave, ignorando que aquela entrada estava DECIDIDA, não aberta —
        # e reabria a janela: uma decisão seguinte validaria contra o
        # opened_at_seq=10 já ultrapassado, sem exigir turno novo do PO.
        #
        # RECLASSIFICADO sob o modelo unificado (categoria 2, o coração
        # deste fix): reabrir uma chave decidida é bloqueado incondicional a
        # seq_atual, pela mesma regra que congela qualquer outra mutação da
        # chave (não só a antedatada) — por isso a mensagem agora é a de
        # imutabilidade do gate decidido, não mais a de abertura antedatada.
        self.state("gate.json", json.dumps([
            {"checkpoint": "x", "status": "aprovado", "opened_at_seq": 10},
        ]))
        self.state("seq", "50")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "x", "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DECIDIDO É IMUTÁVEL BLOQUEADO", r.stderr)

    def test_rewrite_com_opened_at_seq_rebaixado_em_gate_aberto_e_bloqueado(self):
        # Aresta A, item 1 (robustez/não-regressão): mesmo mantendo o status
        # aguardando-po, rebaixar o opened_at_seq de um gate já aberto não é
        # preservação — é reabertura disfarçada, e cai na mesma exigência
        # >= seq_atual. Como seq é monotônico e já alcançou o valor original
        # na abertura (142), um valor rebaixado nunca consegue satisfazer
        # >= seq_atual; isto já era bloqueado mesmo antes deste fix pela
        # checagem de abertura antedatada — este teste documenta o
        # invariante e prova que o fix (que agora exige explicitamente que
        # a preservação real exija o anterior ter estado aguardando-po) não
        # o regride.
        self.state("gate.json", json.dumps([
            {"checkpoint": "x", "status": "aguardando-po", "opened_at_seq": 142},
        ]))
        self.state("seq", "142")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "x", "status": "aguardando-po", "opened_at_seq": 0},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("ABERTURA DE GATE ANTEDATADA BLOQUEADA", r.stderr)

    def test_furo_completo_abrir_antedatado_e_aprovar_na_mesma_sessao_e_barrado_na_abertura(self):
        # Reprodução do furo relatado: sem NENHUM UserPromptSubmit novo desde
        # o início da sessão (seq já em 3 por turnos anteriores), o agente
        # tenta abrir um gate com opened_at_seq=0 (antedatado) para, em
        # seguida, aprová-lo sem turno. A barreira precisa agir já na
        # ABERTURA, antes mesmo de chegar a segunda escrita (aprovação).
        self.state("seq", "3")
        abertura = self._escreve_gate(json.dumps([
            {"checkpoint": "furo", "status": "aguardando-po", "opened_at_seq": 0},
        ]))
        self.assertEqual(abertura.returncode, 2)
        self.assertIn("ABERTURA DE GATE ANTEDATADA BLOQUEADA", abertura.stderr)

        # Prova adicional: como a abertura nunca é persistida de verdade pelo
        # Claude Code quando o hook bloqueia (exit 2 impede a escrita), uma
        # tentativa de "aprovar" o gate antedatado não encontra o gate aberto
        # em disco — cai na regra de chave nunca vista, também bloqueada.
        aprovacao = self._escreve_gate(json.dumps([
            {"checkpoint": "furo", "status": "aprovado", "opened_at_seq": 0},
        ]))
        self.assertEqual(aprovacao.returncode, 2)
        self.assertIn("AUTO-LIBERAÇÃO BLOQUEADA", aprovacao.stderr)


class RodadaTest(GuardBase):
    """Task 9: a chave de um gate passa a ser (checkpoint, pbi, rodada) —
    reprovar um PBI não pode mais colidir com "gate decidido é imutável"
    quando o rework precisa reabrir o MESMO (checkpoint, pbi). A rodada
    seguinte só é legítima sob duas amarras: (1) é sempre
    max(rodada existente da série) + 1 — nunca pulada, nunca repetida — e
    (2) só existe se a rodada anterior daquela mesma série estiver
    REGISTRADA como 'reprovado' (não 'aprovado', não ainda 'aguardando-po').
    Rodada ausente no JSON é tratada como 1, para não quebrar gates antigos
    gravados antes deste fix.
    """

    def _escreve_gate(self, content):
        return run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": ".specgate/gate.json", "content": content},
            "cwd": self.tmp,
        }, self.tmp)

    def test_rodada_seguinte_apos_reprovacao_registrada_e_permitida(self):
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1,
             "status": "reprovado", "opened_at_seq": 5},
        ]))
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1,
             "status": "reprovado", "opened_at_seq": 5},
            {"checkpoint": "testes", "pbi": "03", "rodada": 2,
             "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_rodada_seguinte_apos_aprovado_e_bloqueada(self):
        # A rodada anterior foi APROVADA, não reprovada: nada legitima uma
        # rodada seguinte — o PBI já passou, não está "brigando".
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1,
             "status": "aprovado", "opened_at_seq": 5},
        ]))
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1,
             "status": "aprovado", "opened_at_seq": 5},
            {"checkpoint": "testes", "pbi": "03", "rodada": 2,
             "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 2)

    def test_rodada_seguinte_com_anterior_ainda_aguardando_po_e_bloqueada(self):
        # A rodada anterior ainda não foi decidida: abrir a "próxima" agora
        # seria reabrir um gate pendente por outro caminho.
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1,
             "status": "aguardando-po", "opened_at_seq": 5},
        ]))
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1,
             "status": "aguardando-po", "opened_at_seq": 5},
            {"checkpoint": "testes", "pbi": "03", "rodada": 2,
             "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 2)

    def test_pular_direto_para_rodada_3_e_bloqueado(self):
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1,
             "status": "reprovado", "opened_at_seq": 5},
        ]))
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1,
             "status": "reprovado", "opened_at_seq": 5},
            {"checkpoint": "testes", "pbi": "03", "rodada": 3,
             "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 2)

    def test_repetir_rodada_ja_decidida_e_bloqueado_por_gate_imutavel(self):
        # Nome anterior deste teste ("test_repetir_rodada_existente_e_
        # bloqueado") prometia exercitar a amarra 1 (repetir número de
        # rodada). Auditoria mostrou que isso é falso: a chave completa
        # (checkpoint, pbi, rodada=1) que esta escrita declara é EXATAMENTE
        # a mesma da entrada já decidida em disco, então `_rodada_invalida`
        # a trata como preservação (não como abertura nova) e nem chega a
        # avaliar amarra nenhuma — quem bloqueia é a categoria 2 de
        # `_mutacao_invalida` (gate decidido é imutável). O teste antigo
        # passava mesmo com `_rodada_invalida` inteiramente desligada; por
        # isso o nome foi trocado e a asserção agora confirma o mecanismo
        # que de fato bloqueia.
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1,
             "status": "reprovado", "opened_at_seq": 5},
        ]))
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1,
             "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DECIDIDO É IMUTÁVEL BLOQUEADO", r.stderr)

    def test_rodada_seguinte_com_opened_at_seq_antedatado_e_bloqueada(self):
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1,
             "status": "reprovado", "opened_at_seq": 5},
        ]))
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1,
             "status": "reprovado", "opened_at_seq": 5},
            {"checkpoint": "testes", "pbi": "03", "rodada": 2,
             "status": "aguardando-po", "opened_at_seq": 3},
        ]))
        self.assertEqual(r.returncode, 2)

    def test_gate_sem_campo_rodada_e_tratado_como_rodada_1(self):
        # Estado legado, gravado antes deste fix: sem "rodada" no JSON.
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": "03",
             "status": "reprovado", "opened_at_seq": 5},
        ]))
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "pbi": "03",
             "status": "reprovado", "opened_at_seq": 5},
            {"checkpoint": "testes", "pbi": "03", "rodada": 2,
             "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_primeira_rodada_sem_historico_continua_permitida(self):
        # Não-regressão: abrir a rodada 1 (ou nem declarar "rodada") de uma
        # chave nunca vista continua funcionando como antes deste fix.
        self.state("seq", "0")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "pbi": "07",
             "status": "aguardando-po", "opened_at_seq": 1},
        ]))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_rodada_string_numerica_e_aceita_como_equivalente_ao_inteiro(self):
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1,
             "status": "reprovado", "opened_at_seq": 5},
        ]))
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 1,
             "status": "reprovado", "opened_at_seq": 5},
            {"checkpoint": "testes", "pbi": "03", "rodada": "2",
             "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_rodada_string_nao_numerica_bloqueia_sem_traceback(self):
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": "abc",
             "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_rodada_float_bloqueia_sem_traceback(self):
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": 2.5,
             "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_rodada_negativa_bloqueia_sem_traceback(self):
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": -1,
             "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_rodada_tipo_lista_bloqueia_sem_traceback(self):
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": [1, 2],
             "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_rodada_booleana_bloqueia_sem_traceback(self):
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "testes", "pbi": "03", "rodada": True,
             "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)


class RodadaStatusQueLegitimamTest(GuardBase):
    """Task 9 (bug reportado): o checkpoint 'ambiguidade' não decide com
    'reprovado' — a convenção dele é 'respondido'. Antes deste fix, a
    amarra 2 só reconhecia 'reprovado' como legitimador de rodada seguinte,
    então uma vez que (ambiguidade, pbi) saía de 'aguardando-po' com status
    'respondido', a chave congelava para sempre: uma ambiguidade NOVA do
    MESMO PBI não conseguia abrir rodada 2 nem omitindo `rodada` (cai na
    imutabilidade do gate decidido) nem declarando `rodada: 2` (bloqueada
    por falta de reprovação — que nunca existiu para este checkpoint).

    A correção é uma lista única, `STATUS_QUE_LEGITIMAM_RODADA`, com
    'reprovado' e 'respondido' lado a lado — nenhuma condicional por
    checkpoint. Os dois primeiros testes abaixo são o caso central: o
    positivo (mesmo PBI, ambiguidade Q1 respondida, Q2 diferente surge,
    reestacionar precisa abrir rodada 2) e o negativo que prova que a
    amarra não afrouxou (rodada 1 ainda 'aguardando-po', sem decisão
    nenhuma registrada, não libera rodada 2 nenhuma).
    """

    def _escreve_gate(self, content):
        return run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": ".specgate/gate.json", "content": content},
            "cwd": self.tmp,
        }, self.tmp)

    def test_ambiguidade_respondida_libera_rodada_seguinte_do_mesmo_pbi(self):
        # Caso real do bug: PBI-05 estacionado por ambiguidade Q1, rodada 1
        # decidida como 'respondido'. Trabalho retoma, uma ambiguidade
        # DIFERENTE (Q2) surge depois — reestacionar o mesmo PBI precisa
        # abrir a rodada 2 do gate de ambiguidade, com opened_at_seq = seq
        # atual. Isto tem que ser PERMITIDO.
        self.state("gate.json", json.dumps([
            {"checkpoint": "ambiguidade", "pbi": "05", "rodada": 1,
             "status": "respondido", "opened_at_seq": 5,
             "questions": ["Q1?"]},
        ]))
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "ambiguidade", "pbi": "05", "rodada": 1,
             "status": "respondido", "opened_at_seq": 5,
             "questions": ["Q1?"]},
            {"checkpoint": "ambiguidade", "pbi": "05", "rodada": 2,
             "status": "aguardando-po", "opened_at_seq": 10,
             "questions": ["Q2?"]},
        ]))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_ambiguidade_ainda_aguardando_po_nao_libera_rodada_seguinte(self):
        # Teste negativo que prova que a amarra NÃO afrouxou: a rodada 1
        # ainda está 'aguardando-po' (ninguém decidiu nada, nem
        # 'respondido' nem qualquer outro status) — abrir a rodada 2 tem
        # que continuar BLOQUEADO. Sem esta distinção, a correção do bug
        # viraria uma porta de escape para reabrir gate pendente a
        # qualquer momento.
        self.state("gate.json", json.dumps([
            {"checkpoint": "ambiguidade", "pbi": "05", "rodada": 1,
             "status": "aguardando-po", "opened_at_seq": 5,
             "questions": ["Q1?"]},
        ]))
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "ambiguidade", "pbi": "05", "rodada": 1,
             "status": "aguardando-po", "opened_at_seq": 5,
             "questions": ["Q1?"]},
            {"checkpoint": "ambiguidade", "pbi": "05", "rodada": 2,
             "status": "aguardando-po", "opened_at_seq": 10,
             "questions": ["Q2?"]},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("RODADA SEM REPROVAÇÃO ANTERIOR BLOQUEADA", r.stderr)

    def test_ambiguidade_aprovada_nao_libera_rodada_seguinte(self):
        # Não-regressão: 'aprovado' continua fora da lista que legitima
        # rodada seguinte, mesmo para o checkpoint 'ambiguidade' — não faz
        # sentido semântico real (ambiguidade não se "aprova"), mas prova
        # que a lista não virou um "qualquer status decidido libera".
        self.state("gate.json", json.dumps([
            {"checkpoint": "ambiguidade", "pbi": "05", "rodada": 1,
             "status": "aprovado", "opened_at_seq": 5},
        ]))
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "ambiguidade", "pbi": "05", "rodada": 1,
             "status": "aprovado", "opened_at_seq": 5},
            {"checkpoint": "ambiguidade", "pbi": "05", "rodada": 2,
             "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 2)
        self.assertIn("RODADA SEM REPROVAÇÃO ANTERIOR BLOQUEADA", r.stderr)

    def test_aceite_reprovado_continua_legitimando_rodada_seguinte(self):
        # Não-regressão: o checkpoint 'aceite' (que decide com 'reprovado',
        # igual a 'testes' e 'backlog') precisa continuar funcionando
        # exatamente como antes — a lista ganhou 'respondido' ao lado de
        # 'reprovado', não substituiu nada.
        self.state("gate.json", json.dumps([
            {"checkpoint": "aceite", "pbi": "05", "rodada": 1,
             "status": "reprovado", "opened_at_seq": 5},
        ]))
        self.state("seq", "10")
        r = self._escreve_gate(json.dumps([
            {"checkpoint": "aceite", "pbi": "05", "rodada": 1,
             "status": "reprovado", "opened_at_seq": 5},
            {"checkpoint": "aceite", "pbi": "05", "rodada": 2,
             "status": "aguardando-po", "opened_at_seq": 10},
        ]))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")


class MainFailOpenPayloadTest(GuardBase):
    """Brecha 1 (Critical, fail-open): main() fazia o parsing do payload e
    chamava load_config(cwd) FORA do try/except que garante fail-open. Só
    o json.load estava protegido, e só contra ValueError. Um payload JSON
    válido mas não-objeto (list/int/str/null), ou um cwd de tipo não-string
    dentro de um payload válido, gerava traceback e exit 1 — violando o
    contrato "qualquer erro interno resulta em exit 0".
    """

    def _run_raw(self, raw_stdin):
        return subprocess.run(
            [sys.executable, GUARD], input=raw_stdin,
            capture_output=True, text=True, cwd=self.tmp,
        )

    def test_payload_lista_sai_zero_sem_traceback(self):
        r = self._run_raw("[1, 2, 3]")
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")
        self.assertNotIn("Traceback", r.stderr)

    def test_payload_numero_sai_zero_sem_traceback(self):
        r = self._run_raw("42")
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")
        self.assertNotIn("Traceback", r.stderr)

    def test_payload_string_sai_zero_sem_traceback(self):
        r = self._run_raw('"hi"')
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")
        self.assertNotIn("Traceback", r.stderr)

    def test_payload_null_sai_zero_sem_traceback(self):
        r = self._run_raw("null")
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")
        self.assertNotIn("Traceback", r.stderr)

    def test_cwd_como_lista_sai_zero_sem_traceback(self):
        r = self._run_raw(json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "cwd": [1, 2],
        }))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")
        self.assertNotIn("Traceback", r.stderr)

    def test_cwd_como_int_sai_zero_sem_traceback(self):
        r = self._run_raw(json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "cwd": 42,
        }))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")
        self.assertNotIn("Traceback", r.stderr)

    def test_cwd_como_dict_sai_zero_sem_traceback(self):
        r = self._run_raw(json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "cwd": {"a": 1},
        }))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")
        self.assertNotIn("Traceback", r.stderr)

    def test_tool_input_nao_dict_sai_zero_sem_traceback(self):
        r = self._run_raw(json.dumps({
            "tool_name": "Bash",
            "tool_input": "nao é um objeto",
            "cwd": self.tmp,
        }))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")
        self.assertNotIn("Traceback", r.stderr)


class ParkedBranchTest(GuardBase):
    """Branch `parked/*`: commit WIP que preserva um PBI estacionado não
    pode ser barrado pelo gate de regressão, porque a suíte está vermelha
    POR DEFINIÇÃO (trabalho incompleto). Seguro porque parked/* nunca é
    branch de entrega — o merge de volta passa pelo gate normal.
    """

    def setUp(self):
        super().setUp()
        self.config({"test_command": "false"})  # suíte sempre vermelha
        for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git"] + cmd, cwd=self.tmp, capture_output=True)
        open(os.path.join(self.tmp, "a.txt"), "w").close()
        subprocess.run(["git", "add", "."], cwd=self.tmp, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "init", "--no-verify"],
                       cwd=self.tmp, capture_output=True)

    def _branch(self, nome):
        subprocess.run(["git", "checkout", "-q", "-b", nome], cwd=self.tmp, capture_output=True)

    def test_branch_normal_com_suite_vermelha_bloqueia(self):
        r = self.bash("git commit -m wip")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Gate de regressão FALHOU", r.stderr)

    def test_branch_parked_com_suite_vermelha_passa(self):
        self._branch("parked/03-conversor")
        self.assertEqual(self.bash("git commit -m wip").returncode, 0)

    def test_branch_parecida_mas_nao_parked_bloqueia(self):
        self._branch("parked-nao-e-prefixo")
        self.assertEqual(self.bash("git commit -m wip").returncode, 2)

    def test_merge_partindo_da_parked_ainda_roda_o_gate(self):
        # A branch lida aqui é a do MOMENTO DO HOOK (PreToolUse roda antes do
        # comando), então um encadeado que sai da parked no próprio comando
        # seria lido como "estou em parked/*". A isenção vale só para commit:
        # o merge de volta é a entrega, e precisa passar pelo gate.
        self._branch("parked/03-conversor")
        r = self.bash("git checkout main && git merge parked/03-conversor")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Gate de regressão FALHOU", r.stderr)


class ParkedBranchDeteccaoFalhaTest(GuardBase):
    """Falha na detecção de branch (ex.: cwd não é repo git, ou git
    indisponível) NÃO pode virar bypass do gate de regressão. Na dúvida, o
    gate de regressão deve RODAR — current_branch() volta "" e "" não
    começa com "parked/", então o fluxo cai no comportamento normal
    (executa a suíte e bloqueia se falhar).
    """

    def test_diretorio_sem_git_ainda_roda_gate_e_bloqueia(self):
        # GuardBase.setUp NÃO inicializa um repositório git em self.tmp,
        # então `git branch --show-current` falha (não é repo). Isto não
        # pode ser lido como "está em parked/*" — o gate de regressão
        # precisa continuar rodando normalmente.
        self.config({"test_command": "false"})
        r = self.bash("git commit -m wip")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Gate de regressão FALHOU", r.stderr)


class GuardRegressionNaoRegressaoTest(GuardBase):
    """C2: a correção (redirecionar a saída do test_command para arquivo
    temporário em vez de bufferizar em RAM, e bloquear em falha de
    execução) não pode mudar nenhum destes comportamentos já existentes.
    """

    def setUp(self):
        super().setUp()
        for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git"] + cmd, cwd=self.tmp, capture_output=True)
        open(os.path.join(self.tmp, "a.txt"), "w").close()
        subprocess.run(["git", "add", "."], cwd=self.tmp, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "init", "--no-verify"],
                       cwd=self.tmp, capture_output=True)

    def _stage_change(self, name="b.txt"):
        with open(os.path.join(self.tmp, name), "w", encoding="utf-8") as fh:
            fh.write("x")
        subprocess.run(["git", "add", "."], cwd=self.tmp, capture_output=True)

    def test_dry_run_nao_roda_a_suite(self):
        self.config({"test_command": "false"})  # se rodasse, bloquearia
        self._stage_change()
        r = self.bash("git commit --dry-run -m wip")
        self.assertEqual(r.returncode, 0)

    def test_suite_verde_passa(self):
        self.config({"test_command": "true"})
        self._stage_change()
        r = self.bash("git commit -m wip")
        self.assertEqual(r.returncode, 0)

    def test_suite_vermelha_bloqueia(self):
        self.config({"test_command": "false"})
        self._stage_change()
        r = self.bash("git commit -m wip")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Gate de regressão FALHOU", r.stderr)

    def test_timeout_ainda_bloqueia(self):
        self.config({"test_command": "sleep 2", "test_timeout_seconds": 1})
        self._stage_change()
        r = self.bash("git commit -m wip")
        self.assertEqual(r.returncode, 2)
        self.assertIn("excedeu o tempo", r.stderr)

    def test_parked_ainda_isenta_commit_da_suite(self):
        subprocess.run(["git", "checkout", "-q", "-b", "parked/09-x"], cwd=self.tmp, capture_output=True)
        self.config({"test_command": "false"})
        self._stage_change()
        r = self.bash("git commit -m wip")
        self.assertEqual(r.returncode, 0)

    def test_merge_ainda_roda_o_gate_mesmo_partindo_de_parked(self):
        subprocess.run(["git", "checkout", "-q", "-b", "parked/09-x"], cwd=self.tmp, capture_output=True)
        self._stage_change()
        subprocess.run(["git", "commit", "-q", "-m", "wip", "--no-verify"],
                       cwd=self.tmp, capture_output=True)
        self.config({"test_command": "false"})
        r = self.bash("git checkout main && git merge parked/09-x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Gate de regressão FALHOU", r.stderr)


class GuardRegressionTimeoutOrfaoTest(GuardBase):
    """I6 (achado da revisão final): guard_regression rodava
    subprocess.run(test_command, shell=True, timeout=...) sem
    start_new_session=True. No TimeoutExpired, a stdlib mata só o processo
    do /bin/sh — um filho desse shell (a suíte de verdade travada, ou
    qualquer processo que ele tenha backgroundeado) não é atingido e
    continua rodando, órfão, consumindo recursos indefinidamente. Tentativas
    repetidas de commit empilhavam cópias da suíte travada.
    """

    MARCADOR = "spec-gate-i6-heartbeat-marker"

    def setUp(self):
        super().setUp()
        for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git"] + cmd, cwd=self.tmp, capture_output=True)
        open(os.path.join(self.tmp, "a.txt"), "w").close()
        subprocess.run(["git", "add", "."], cwd=self.tmp, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "init", "--no-verify"],
                       cwd=self.tmp, capture_output=True)

    def tearDown(self):
        # Limpeza best-effort: mesmo que o fix falhe (ou o teste seja
        # interrompido no meio), não deixar o loop de heartbeat rodando de
        # verdade na máquina depois que o teste termina.
        subprocess.run(["pkill", "-f", self.MARCADOR], capture_output=True)
        super().tearDown()

    def _stage_change(self, name="b.txt"):
        with open(os.path.join(self.tmp, name), "w", encoding="utf-8") as fh:
            fh.write("x")
        subprocess.run(["git", "add", "."], cwd=self.tmp, capture_output=True)

    def test_timeout_mata_processo_filho_do_shell_nao_so_o_shell(self):
        # O `: marcador` embutido no comando é um no-op (builtin `:` do
        # shell) — só serve para o cmdline dos processos filhos (que
        # herdam o mesmo argv do shell, sem re-exec) conter uma string
        # única que o tearDown usa para limpar via `pkill -f`.
        heartbeat = os.path.join(self.tmp, "heartbeat.txt")
        self.config({
            "test_command": (
                f": {self.MARCADOR}; while true; do echo x >> heartbeat.txt; "
                "sleep 0.05; done & sleep 3600"
            ),
            "test_timeout_seconds": 1,
        })
        self._stage_change()
        r = self.bash("git commit -m wip")
        self.assertEqual(r.returncode, 2)
        self.assertIn("excedeu o tempo", r.stderr)

        # Se o processo em background sobreviveu ao timeout (o bug: só o
        # /bin/sh era morto), heartbeat.txt continua CRESCENDO depois que o
        # guard já retornou. Se foi morto de verdade (grupo inteiro), o
        # tamanho do arquivo para de mudar.
        self.assertTrue(os.path.exists(heartbeat), "a suíte nem chegou a rodar")
        tamanho_logo_apos = os.path.getsize(heartbeat)
        time.sleep(0.5)
        tamanho_depois = os.path.getsize(heartbeat)
        self.assertEqual(
            tamanho_logo_apos, tamanho_depois,
            msg="processo em background continuou escrevendo após o timeout "
                "— ficou órfão em vez de morto junto com o shell",
        )


class GuardRegressionSaidaGiganteTest(GuardBase):
    """C2 — o achado: `guard_regression` usava `capture_output=True`, que
    bufferiza a saída INTEIRA do test_command em RAM, embora só o rabo
    (2000/1000 chars) seja usado nas mensagens. Sob teto de memória
    (container limitado, suíte verbosa), isso podia levantar MemoryError
    sozinho — e esse erro, sem tratamento dedicado, era engolido pelo
    `except Exception: sys.exit(0)` de main(), liberando o commit com a
    suíte VERMELHA.

    Não é portável forçar um teto de memória do SO num teste unitário, mas
    dá para prová-lo pelo comportamento observável: uma saída GRANDE de
    verdade (~5 MB) não pode travar nem, pior, contornar o bloqueio — que é
    exatamente o que a correção (redirecionar para arquivo temporário, ler
    só o rabo com seek) precisa preservar.
    """

    def setUp(self):
        super().setUp()
        for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git"] + cmd, cwd=self.tmp, capture_output=True)
        open(os.path.join(self.tmp, "a.txt"), "w").close()
        subprocess.run(["git", "add", "."], cwd=self.tmp, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "init", "--no-verify"],
                       cwd=self.tmp, capture_output=True)

    def _stage_change(self, name="b.txt"):
        with open(os.path.join(self.tmp, name), "w", encoding="utf-8") as fh:
            fh.write("x")
        subprocess.run(["git", "add", "."], cwd=self.tmp, capture_output=True)

    def test_saida_gigante_com_suite_vermelha_ainda_bloqueia(self):
        self.config({"test_command": (
            "python3 -c \"import sys; sys.stdout.write('a' * 5_000_000); "
            "sys.exit(1)\""
        )})
        self._stage_change()
        r = self.bash("git commit -m wip")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Gate de regressão FALHOU", r.stderr)
        # A mensagem de bloqueio traz só o RABO, nunca a saída inteira de 5 MB.
        self.assertLess(len(r.stderr), 10_000)

    def test_saida_gigante_com_suite_verde_passa(self):
        self.config({"test_command": (
            "python3 -c \"import sys; sys.stdout.write('a' * 5_000_000); "
            "sys.exit(0)\""
        )})
        self._stage_change()
        r = self.bash("git commit -m wip")
        self.assertEqual(r.returncode, 0)


class GuardRegressionFalhaDeRecursoTest(GuardBase):
    """C2 — a regra do PO, registrada aqui em teste: fail-open vale para
    erro de PARSING de estado, nunca para falha em EXECUTAR a verificação.
    Se o test_command não pôde ser rodado/avaliado (MemoryError, OSError —
    estouro de recurso, fork falhando, disco cheio), o commit precisa
    BLOQUEAR: não conseguir verificar não é o mesmo que verificar e estar
    tudo bem.

    Chama `gate_guard.guard_regression` diretamente, em processo, com
    `subprocess.Popen` mockado (I6: guard_regression passou a usar Popen +
    start_new_session=True em vez de subprocess.run, para poder matar o
    GRUPO de processos inteiro no timeout, não só o /bin/sh) — forçar um
    MemoryError de verdade via limite de memória do SO não é portável num
    teste unitário.
    """

    def _fake_popen_falha_no_test_command(self, exc):
        def fake(cmd, **kwargs):
            if isinstance(cmd, list):
                # subprocess.run (usado por current_branch(), chamada de
                # `git branch --show-current`) invoca Popen por baixo dos
                # panos — mockar Popen intercepta as DUAS chamadas. Esta é
                # a de current_branch(): deixa falhar "normal" (fail-safe
                # na direção certa, current_branch já cobre isso) para não
                # confundir com a falha do test_command em si, que é o
                # alvo deste teste (test_command é sempre uma string, por
                # rodar com shell=True).
                raise OSError("git indisponível neste teste")
            raise exc
        return fake

    def test_memory_error_ao_executar_test_command_bloqueia(self):
        cfg = {"test_command": "true"}
        tool_input = {"command": "git commit -m x"}
        with mock.patch.object(
            gate_guard.subprocess, "Popen",
            side_effect=self._fake_popen_falha_no_test_command(MemoryError("boom")),
        ):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as cm:
                    gate_guard.guard_regression(tool_input, self.tmp, cfg)
            self.assertEqual(cm.exception.code, 2)
        self.assertIn("NÃO PÔDE SER EXECUTADA", stderr.getvalue())

    def test_oserror_ao_executar_test_command_bloqueia(self):
        cfg = {"test_command": "true"}
        tool_input = {"command": "git commit -m x"}
        with mock.patch.object(
            gate_guard.subprocess, "Popen",
            side_effect=self._fake_popen_falha_no_test_command(
                OSError("Cannot allocate memory")
            ),
        ):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as cm:
                    gate_guard.guard_regression(tool_input, self.tmp, cfg)
            self.assertEqual(cm.exception.code, 2)
        self.assertIn("NÃO PÔDE SER EXECUTADA", stderr.getvalue())

    def test_falha_de_recurso_nao_e_engolida_por_main(self):
        # Ponta a ponta: main() precisa deixar o SystemExit(2) escapar do
        # seu próprio `except Exception: sys.exit(0)` — é exatamente esse
        # engolimento que causava o bug relatado (bypass silencioso).
        self.config({"test_command": "true"})
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m x"},
            "cwd": self.tmp,
        }
        with mock.patch.object(
            gate_guard.subprocess, "Popen",
            side_effect=self._fake_popen_falha_no_test_command(MemoryError("boom")),
        ):
            with mock.patch.object(
                gate_guard.sys, "stdin", io.StringIO(json.dumps(payload))
            ):
                with self.assertRaises(SystemExit) as cm:
                    gate_guard.main()
        self.assertEqual(cm.exception.code, 2)


class SeqLockTest(GuardBase):
    """Brecha 2: `.specgate/seq` é a prova inforjável de que o usuário
    falou (`has_human_turn_since` compara seq_atual > opened_at_seq), e
    toda a camada forte do gate de PO repousa nisso. Nada impedia o agente
    de escrever o arquivo diretamente — o único caminho legítimo é
    log_event.py rodando como HOOK no UserPromptSubmit, que não passa por
    este guard PreToolUse.
    """

    def test_write_em_seq_e_bloqueado(self):
        r = run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": ".specgate/seq", "content": "9999"},
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/seq BLOQUEADA", r.stderr)

    def test_edit_em_seq_e_bloqueado(self):
        self.state("seq", "1")
        r = run_guard({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": ".specgate/seq",
                "old_string": "1",
                "new_string": "9999",
            },
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/seq BLOQUEADA", r.stderr)

    def test_echo_redirect_para_seq_via_bash_e_bloqueado(self):
        r = self.bash("echo 9999 > .specgate/seq")
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/seq BLOQUEADA", r.stderr)

    def test_python_dash_c_escrevendo_seq_e_bloqueado(self):
        r = self.bash(
            "python3 -c \"open('.specgate/seq','w').write('9999')\""
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/seq BLOQUEADA", r.stderr)

    def test_sh_dash_c_escrevendo_seq_e_bloqueado(self):
        r = self.bash('sh -c "echo 9999 > .specgate/seq"')
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/seq BLOQUEADA", r.stderr)

    def test_tee_para_seq_via_bash_e_bloqueado(self):
        r = self.bash("echo 9999 | tee .specgate/seq")
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/seq BLOQUEADA", r.stderr)

    def test_dd_of_para_seq_via_bash_e_bloqueado(self):
        r = self.bash("dd of=.specgate/seq if=/dev/null")
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/seq BLOQUEADA", r.stderr)

    def test_install_para_seq_via_bash_e_bloqueado(self):
        r = self.bash("install origem .specgate/seq")
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/seq BLOQUEADA", r.stderr)

    def test_heredoc_python3_escrevendo_seq_e_bloqueado(self):
        r = self.bash(
            "python3 <<EOF\nopen('.specgate/seq','w').write('9999')\nEOF"
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/seq BLOQUEADA", r.stderr)

    def test_heredoc_python3_com_delimitador_entre_aspas_e_bloqueado(self):
        r = self.bash(
            "python3 <<'EOF'\nopen('.specgate/seq','w').write('9999')\nEOF"
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/seq BLOQUEADA", r.stderr)

    def test_awk_begin_print_redirect_nao_e_bloqueado_limite_conhecido(self):
        # LIMITE CONHECIDO E ACEITO, não um bug: dentro de `awk 'BEGIN{...}'`
        # o `>` fica DENTRO da string entre aspas simples que delimita o
        # programa awk inteiro. O shlex (que não entende a semântica do
        # awk) tokeniza o programa como um único argumento opaco, então o
        # "alvo" nunca aparece como um caminho isolado para `write_targets`
        # comparar — isto é fricção, não um parser de shell/awk de
        # verdade, e este teste documenta honestamente o contorno, em vez
        # de escondê-lo.
        r = self.bash('awk \'BEGIN{print 9 > ".specgate/seq"}\'')
        self.assertEqual(r.returncode, 0)

    def test_sem_specgate_json_escrita_em_seq_e_inerte(self):
        # Não-regressão: sem .specgate.json o guard inteiro é inerte,
        # inclusive este — o projeto simplesmente não usa spec-gate.
        os.remove(os.path.join(self.tmp, ".specgate.json"))
        r = self.bash("echo 9999 > .specgate/seq")
        self.assertEqual(r.returncode, 0)

    def test_escrita_em_outro_arquivo_qualquer_nao_e_afetada(self):
        # Não-regressão: o guard novo não deve bloquear escritas que não
        # tocam .specgate/seq.
        r = self.bash("echo oi > outro-arquivo.txt")
        self.assertEqual(r.returncode, 0)

    def test_write_em_outro_arquivo_dentro_de_specgate_nao_e_afetado(self):
        r = run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": ".specgate/phase", "content": "testing"},
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 0)


class HookStateLockTest(GuardBase):
    """0.3.0: `.specgate/red.json` e `.specgate/attempts.json` são a prova de
    que a suíte estava vermelha e a contagem de tentativas — os dois fatos que
    os mecanismos novos prometem não ser autodeclaráveis. Sem este guard o
    agente reescreve os dois por tool call: `count: 0` devolve tentativas
    infinitas depois do teto, `proven: true` forja o vermelho que a transição
    para a implementação exige. Mesmo papel de `guard_seq_lock`, mesma regra:
    bloqueia sempre, com ou sem gate aberto, porque não existe escrita
    legítima destes arquivos partindo do agente.
    """

    def test_write_em_attempts_e_bloqueado(self):
        r = run_guard({
            "tool_name": "Write",
            "tool_input": {
                "file_path": ".specgate/attempts.json",
                "content": '{"pbi": "docs/backlog/02-x.md", "round": 1, "count": 0}',
            },
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/attempts.json BLOQUEADA", r.stderr)

    def test_write_em_red_e_bloqueado(self):
        r = run_guard({
            "tool_name": "Write",
            "tool_input": {
                "file_path": ".specgate/red.json",
                "content": '{"docs/backlog/02-x.md": {"proven": true, "round": 1}}',
            },
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/red.json BLOQUEADA", r.stderr)

    def test_edit_em_attempts_e_bloqueado(self):
        self.state("attempts.json", '{"count": 5}')
        r = run_guard({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": ".specgate/attempts.json",
                "old_string": "5", "new_string": "0",
            },
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/attempts.json BLOQUEADA", r.stderr)

    def test_redirect_via_bash_para_red_e_bloqueado(self):
        r = self.bash("echo '{}' > .specgate/red.json")
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/red.json BLOQUEADA", r.stderr)

    def test_rm_de_attempts_via_bash_e_bloqueado(self):
        # Apagar o contador é a forma mais direta de zerá-lo.
        r = self.bash("rm .specgate/attempts.json")
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/attempts.json BLOQUEADA", r.stderr)

    def test_python_dash_c_escrevendo_red_e_bloqueado(self):
        r = self.bash(
            "python3 -c \"open('.specgate/red.json','w').write('{}')\""
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/red.json BLOQUEADA", r.stderr)

    def test_comando_grande_mencionando_attempts_e_bloqueado(self):
        recheio = "x" * (64 * 1024 + 10)
        r = self.bash(f"echo '{{}}' > .specgate/attempts.json # {recheio}")
        self.assertEqual(r.returncode, 2)
        self.assertIn("COMANDO GRANDE DEMAIS PARA VERIFICAR", r.stderr)

    def test_comando_grande_alheio_nao_e_bloqueado(self):
        recheio = "x" * (64 * 1024 + 10)
        r = self.bash(f"echo oi > outro.txt # {recheio}")
        self.assertEqual(r.returncode, 0)

    def test_leitura_de_attempts_continua_liberada(self):
        # O implementer é instruído a `cat .specgate/attempts.json` para saber
        # em que tentativa está: o guard tranca a ESCRITA, nunca a leitura.
        self.state("attempts.json", '{"count": 3}')
        r = self.bash("cat .specgate/attempts.json")
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_sem_specgate_json_e_inerte(self):
        os.remove(os.path.join(self.tmp, ".specgate.json"))
        r = self.bash("echo '{}' > .specgate/attempts.json")
        self.assertEqual(r.returncode, 0)


class SpecFreezeTest(GuardBase):
    """Task 6: a janela do freeze da spec começa só DEPOIS do Gate PO 1
    (backlog aprovado), nunca "sempre que há fase ativa". As Fases 0 e 1
    (concepção/refinamento) precisam que o spec-analyst escreva livremente
    em docs/backlog/ — com a regra antiga ("existe fase ativa"), esse
    trabalho legítimo seria bloqueado pelo próprio sistema.
    """

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.tmp, "docs", "backlog"), exist_ok=True)
        self.state("phase", "implementing")

    def _escreve_spec(self):
        return run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": "docs/backlog/01-x.md", "content": "# spec"},
            "cwd": self.tmp,
        }, self.tmp)

    def test_antes_do_gate_po_1_spec_editavel(self):
        # Fases 0 e 1: o spec-analyst PRECISA escrever o backlog.
        self.assertEqual(self._escreve_spec().returncode, 0)

    def test_depois_do_gate_po_1_spec_congelada(self):
        with open(os.path.join(self.tmp, ".specgate", "batch.json"), "w", encoding="utf-8") as fh:
            json.dump({"backlog_aprovado": True}, fh)
        r = self._escreve_spec()
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPEC CONGELADA", r.stderr)

    def test_spec_md_nao_e_mais_protegido_por_default(self):
        with open(os.path.join(self.tmp, ".specgate", "batch.json"), "w", encoding="utf-8") as fh:
            json.dump({"backlog_aprovado": True}, fh)
        r = run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": "SPEC.md", "content": "x"},
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 0)

    def test_batch_json_corrompido_falha_aberto_sem_traceback(self):
        # batch.json corrompido/malformado não pode virar exceção não
        # tratada num hook BLOQUEANTE: gate_po_1_passed devolve False (lado
        # seguro, freeze desligado) e o guard sai limpo, exit 0 ou 2, nunca
        # 1 com stack trace no stderr.
        with open(os.path.join(self.tmp, ".specgate", "batch.json"), "w", encoding="utf-8") as fh:
            fh.write("{lixo")
        r = self._escreve_spec()
        self.assertIn(r.returncode, (0, 2))
        self.assertNotIn("Traceback", r.stderr)


class SpecLockInterpreterEscapeTest(GuardBase):
    """guard_spec_lock fazia parsing próprio direto sobre BASH_WRITE_RES em
    vez de usar `write_targets`, então não herdava o reconhecimento de
    invocação de interpretador inline (`python3 -c`, `sh -c`, `bash -c`,
    heredoc) nem os padrões `dd`/`install`. Consequência: a spec congelada
    podia ser escrita por esses caminhos sem o guard perceber.
    """

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.tmp, "docs", "backlog"), exist_ok=True)
        with open(os.path.join(self.tmp, ".specgate", "batch.json"), "w", encoding="utf-8") as fh:
            json.dump({"backlog_aprovado": True}, fh)

    def test_python3_dash_c_escrevendo_spec_congelada_e_bloqueado(self):
        r = self.bash(
            "python3 -c \"open('docs/backlog/01-x.md','w').write('hackeado')\""
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPEC CONGELADA", r.stderr)

    def test_sh_dash_c_escrevendo_spec_congelada_e_bloqueado(self):
        r = self.bash('sh -c "echo hackeado > docs/backlog/01-x.md"')
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPEC CONGELADA", r.stderr)

    def test_bash_dash_c_escrevendo_spec_congelada_e_bloqueado(self):
        r = self.bash('bash -c "echo hackeado > docs/backlog/01-x.md"')
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPEC CONGELADA", r.stderr)

    def test_escrita_legitima_fora_de_docs_backlog_continua_permitida(self):
        r = self.bash("echo oi > outro-arquivo.txt")
        self.assertEqual(r.returncode, 0)

    def test_write_legitimo_fora_de_docs_backlog_continua_permitido(self):
        r = run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": "src/app.py", "content": "x"},
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 0)


class SpecWindowBase(GuardBase):
    """Fixture da janela de escrita da spec (sem testes, para que herdar
    daqui não reexecute a suíte da base): backlog aprovado no disco, duas
    specs em docs/backlog/ e os helpers de gate/seq/escrita.
    """

    PBI_PATH = "docs/backlog/03-x.md"
    OUTRO_PBI_PATH = "docs/backlog/04-y.md"

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.tmp, "docs", "backlog"), exist_ok=True)
        with open(os.path.join(self.tmp, self.PBI_PATH), "w", encoding="utf-8") as fh:
            fh.write("# spec original")
        with open(os.path.join(self.tmp, self.OUTRO_PBI_PATH), "w", encoding="utf-8") as fh:
            fh.write("# outra spec")
        with open(os.path.join(self.tmp, ".specgate", "batch.json"), "w", encoding="utf-8") as fh:
            json.dump({"backlog_aprovado": True}, fh)

    def _seq(self, valor):
        self.state("seq", str(valor))

    def _escreve_spec(self, path=None):
        return run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": path or self.PBI_PATH, "content": "# spec atualizada"},
            "cwd": self.tmp,
        }, self.tmp)


class SpecLockRetomadaAmbiguidadeTest(SpecWindowBase):
    """C1: o ciclo de estacionamento precisa fechar. O gate de ambiguidade
    estaciona um PBI; o PO responde; e a retomada exige atualizar a spec
    DAQUELE PBI com a decisão — mas antes desta correção, guard_spec_lock
    bloqueava toda escrita em docs/backlog/ depois do backlog aprovado,
    sem distinguir esse caso, mesmo essa.

    A solução: uma janela de escrita ESTREITA, amarrada ao turno do PO —
    reusa o mesmo `has_human_turn_since` dos outros gates, em vez de um
    mecanismo novo. Abre só quando o gate VIGENTE (maior rodada) daquele
    PBI é (checkpoint="ambiguidade", status="respondido") E há turno
    humano real depois de `opened_at_seq`; abre só para o arquivo exato do
    campo `pbi` do gate, nunca o diretório inteiro; e fecha quando o gate
    ganha rodada nova (a rodada anterior deixa de ser vigente) ou quando a
    fase avança de novo (o retomada reativou `.specgate/phase`).
    """

    def _abre_gate_respondido(self, opened_at_seq=3, rodada=1, pbi=None, extra=None):
        gates = [{
            "checkpoint": "ambiguidade",
            "pbi": pbi or self.PBI_PATH,
            "rodada": rodada,
            "status": "respondido",
            "opened_at_seq": opened_at_seq,
            "questions": ["decisão registrada"],
        }]
        if extra:
            gates.extend(extra)
        self.state("gate.json", json.dumps(gates))

    def test_respondido_com_turno_posterior_edita_a_spec_daquele_pbi(self):
        self._abre_gate_respondido(opened_at_seq=3)
        self._seq(5)  # turno humano depois da abertura (seq_atual > opened_at_seq)
        r = self._escreve_spec()
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_respondido_sem_turno_novo_bloqueia(self):
        self._abre_gate_respondido(opened_at_seq=3)
        self._seq(3)  # nenhum turno depois da abertura (seq_atual == opened_at_seq)
        r = self._escreve_spec()
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPEC CONGELADA", r.stderr)

    def test_janela_aberta_nao_libera_spec_de_outro_pbi(self):
        self._abre_gate_respondido(opened_at_seq=3, pbi=self.PBI_PATH)
        self._seq(5)
        r = self._escreve_spec(path=self.OUTRO_PBI_PATH)
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPEC CONGELADA", r.stderr)

    def test_gate_ainda_aguardando_po_nao_abre_janela(self):
        # Status errado (ainda não respondido): não é a exceção, é a regra
        # normal de congelamento.
        self.state("gate.json", json.dumps([
            {"checkpoint": "ambiguidade", "pbi": self.PBI_PATH, "rodada": 1,
             "status": "aguardando-po", "opened_at_seq": 3}
        ]))
        self._seq(5)
        r = self._escreve_spec()
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPEC CONGELADA", r.stderr)

    def test_rodada_nova_fecha_a_janela_da_rodada_anterior(self):
        # Gate vigente agora é a rodada 2 (aguardando-po de novo) — a
        # rodada 1 respondida virou histórico, não é mais vigente. "Fecha
        # quando o gate ganha rodada nova."
        self._abre_gate_respondido(opened_at_seq=3, extra=[{
            "checkpoint": "ambiguidade", "pbi": self.PBI_PATH, "rodada": 2,
            "status": "aguardando-po", "opened_at_seq": 6,
        }])
        self._seq(9)
        r = self._escreve_spec()
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPEC CONGELADA", r.stderr)

    def test_fase_ja_avancada_fecha_a_janela_mesmo_com_turno_novo(self):
        # "Fecha quando a fase avança": o retomada já reativou a fase (o
        # PBI seguiu adiante) antes desta escrita — a janela já não é mais
        # para isto.
        self._abre_gate_respondido(opened_at_seq=3)
        self._seq(5)
        self.state("phase", "testing")
        r = self._escreve_spec()
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPEC CONGELADA", r.stderr)

    def test_checkpoint_diferente_de_ambiguidade_nao_abre_janela(self):
        # A janela é uma exceção NARROW ao congelamento — só para o gate de
        # ambiguidade respondido, não para qualquer gate decidido.
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": self.PBI_PATH, "rodada": 1,
             "status": "aprovado", "opened_at_seq": 3}
        ]))
        self._seq(5)
        r = self._escreve_spec()
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPEC CONGELADA", r.stderr)

    def test_janela_nao_libera_bash_write_fora_do_arquivo_do_pbi(self):
        self._abre_gate_respondido(opened_at_seq=3)
        self._seq(5)
        r = self.bash(f"echo hackeado > {self.OUTRO_PBI_PATH}")
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPEC CONGELADA", r.stderr)

    def test_janela_libera_tambem_via_bash_write_no_arquivo_certo(self):
        # A janela usa write_targets (o mesmo reconhecimento de escrita dos
        # outros guards), então cobre Bash reconhecido, não só Write.
        self._abre_gate_respondido(opened_at_seq=3)
        self._seq(5)
        r = self.bash(f"echo atualizado > {self.PBI_PATH}")
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_sem_seq_no_disco_e_sem_turno_bloqueia(self):
        # Ausência de .specgate/seq (nunca houve turno algum) não pode ser
        # lida como "turno aconteceu" — has_human_turn_since já cobre isso
        # (read_seq ausente devolve 0), este teste prova que a janela nova
        # não reabre esse furo.
        self._abre_gate_respondido(opened_at_seq=3)
        r = self._escreve_spec()
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPEC CONGELADA", r.stderr)


class SpecLockEmendaTest(SpecWindowBase):
    """0.3.0: emenda de spec num PBI já entregue reusa a MESMA janela da
    retomada de estacionamento, com as mesmas cinco pré-condições.

    Sem isto, mudar um requisito depois da entrega não tinha caminho nenhum:
    `docs/backlog/` fica congelado desde o gate de backlog, e a única exceção
    era a retomada de um PBI estacionado. A spec era o ponto de partida, não
    a fonte contínua.
    """

    def _abre_emenda(self, opened_at_seq=3, rodada=1, status="respondido", pbi=None, extra=None):
        gates = [{
            "checkpoint": "emenda",
            "pbi": pbi or self.PBI_PATH,
            "rodada": rodada,
            "status": status,
            "opened_at_seq": opened_at_seq,
            "questions": ["mudar o limite de 50 para 100?"],
        }]
        if extra:
            gates.extend(extra)
        self.state("gate.json", json.dumps(gates))

    def test_emenda_respondida_com_turno_posterior_edita_a_spec(self):
        self._abre_emenda(opened_at_seq=3)
        self._seq(5)
        r = self._escreve_spec()
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_emenda_sem_turno_novo_bloqueia(self):
        self._abre_emenda(opened_at_seq=3)
        self._seq(3)
        r = self._escreve_spec()
        self.assertEqual(r.returncode, 2)
        self.assertIn("SPEC CONGELADA", r.stderr)

    def test_emenda_ainda_aguardando_po_nao_abre_janela(self):
        self._abre_emenda(opened_at_seq=3, status="aguardando-po")
        self._seq(5)
        self.assertEqual(self._escreve_spec().returncode, 2)

    def test_emenda_de_um_pbi_nao_libera_a_spec_de_outro(self):
        self._abre_emenda(opened_at_seq=3)
        self._seq(5)
        self.assertEqual(self._escreve_spec(path=self.OUTRO_PBI_PATH).returncode, 2)

    def test_emenda_com_fase_ativa_nao_abre_janela(self):
        self._abre_emenda(opened_at_seq=3)
        self._seq(5)
        self.state("phase", f"implementing:{self.PBI_PATH}")
        self.assertEqual(self._escreve_spec().returncode, 2)

    def test_fase_de_outro_pbi_nao_fecha_a_janela_deste(self):
        # 0.3.0: com o chokepoint por PBI, a fila anda enquanto um item está
        # estacionado — então a fase corrente quase sempre é de OUTRO PBI.
        # Exigir a fase globalmente vazia fecharia a janela justamente no
        # cenário que a versão nova existe para permitir.
        self._abre_emenda(opened_at_seq=3)
        self._seq(5)
        self.state("phase", f"implementing:{self.OUTRO_PBI_PATH}")
        r = self._escreve_spec()
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_fase_sem_pbi_declarado_fecha_a_janela_para_todos(self):
        # `testing` é do lote inteiro (e o formato antigo não diz de quem é):
        # sem saber o dono da fase, o lado conservador é fechar.
        self._abre_emenda(opened_at_seq=3)
        self._seq(5)
        self.state("phase", "testing")
        self.assertEqual(self._escreve_spec().returncode, 2)

    def test_rodada_nova_de_emenda_fecha_a_janela_anterior(self):
        self._abre_emenda(opened_at_seq=3, extra=[{
            "checkpoint": "emenda", "pbi": self.PBI_PATH, "rodada": 2,
            "status": "aguardando-po", "opened_at_seq": 6,
        }])
        self._seq(9)
        self.assertEqual(self._escreve_spec().returncode, 2)


class BatchLockTest(GuardBase):
    """Pendência 5 (quinta instância do padrão de design): .specgate/batch.json
    guarda o campo backlog_aprovado que liga o congelamento da spec
    (guard_spec_lock), mas não tinha guard nenhum — `echo '{}' >
    .specgate/batch.json` desligava a própria proteção. Diferente de
    .specgate/seq (guard_seq_lock bloqueia QUALQUER escrita reconhecida),
    batch.json PRECISA continuar escrevível pelo fluxo normal do /spec-gate
    (status de PBI, tentativas, commits do lote), então o bloqueio aqui é
    estreito: só a escrita que remove ou torna falsy backlog_aprovado
    quando ele já era true no disco.
    """

    def _aprova_backlog(self, extra=None):
        data = {"backlog_aprovado": True}
        if extra:
            data.update(extra)
        with open(os.path.join(self.tmp, ".specgate", "batch.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def _escreve_batch(self, content):
        return run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": ".specgate/batch.json", "content": content},
            "cwd": self.tmp,
        }, self.tmp)

    def test_esvaziar_batch_json_com_backlog_aprovado_e_bloqueado(self):
        self._aprova_backlog()
        r = self._escreve_batch("{}")
        self.assertEqual(r.returncode, 2)
        self.assertIn("backlog_aprovado", r.stderr)

    def test_desligar_backlog_aprovado_para_false_e_bloqueado(self):
        self._aprova_backlog()
        r = self._escreve_batch(json.dumps({"backlog_aprovado": False, "itens": {}}))
        self.assertEqual(r.returncode, 2)

    def test_rm_batch_json_via_bash_e_bloqueado(self):
        self._aprova_backlog()
        r = self.bash("rm .specgate/batch.json")
        self.assertEqual(r.returncode, 2)

    def test_truncar_batch_json_via_bash_e_bloqueado(self):
        self._aprova_backlog()
        r = self.bash("> .specgate/batch.json")
        self.assertEqual(r.returncode, 2)

    def test_edit_em_batch_json_com_backlog_aprovado_e_bloqueado(self):
        # Edit não expõe o conteúdo final (mesma regra conservadora de
        # _gates_from_content): sem ver o resultado não há como confirmar
        # que backlog_aprovado continua true.
        self._aprova_backlog()
        r = run_guard({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": ".specgate/batch.json",
                "old_string": "true",
                "new_string": "false",
            },
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 2)

    def test_preservar_backlog_aprovado_mudando_outros_campos_e_permitido(self):
        self._aprova_backlog({"itens": {"01": "pendente"}})
        r = self._escreve_batch(json.dumps({
            "backlog_aprovado": True,
            "itens": {"01": "concluido"},
            "tentativas": 2,
        }))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_sem_backlog_aprovado_no_disco_arquivo_ausente_tudo_permitido(self):
        # Antes do Gate PO 1 (batch.json ainda não existe): nada aqui para
        # proteger.
        r = self._escreve_batch("{}")
        self.assertEqual(r.returncode, 0)

    def test_backlog_aprovado_false_no_disco_tudo_permitido(self):
        self._aprova_backlog({"backlog_aprovado": False})
        r = self._escreve_batch("{}")
        self.assertEqual(r.returncode, 0)

    def test_batch_json_corrompido_no_disco_falha_aberto_permitido(self):
        with open(os.path.join(self.tmp, ".specgate", "batch.json"), "w", encoding="utf-8") as fh:
            fh.write("{lixo")
        r = self._escreve_batch("{}")
        self.assertEqual(r.returncode, 0)

    def test_escrita_em_outro_arquivo_nao_e_afetada(self):
        self._aprova_backlog()
        r = self.bash("echo oi > outro-arquivo.txt")
        self.assertEqual(r.returncode, 0)


class GatesLegadoTest(GuardBase):
    """Task 7: os 4 gates de 0.1.0 (guard_testing_phase, guard_destructive,
    guard_spec_lock, guard_regression) não tinham classe de teste dedicada —
    cobertura só indireta, espalhada por outras classes. Esta classe prova,
    para cada um, que ele ainda bloqueia o que deve bloquear no fluxo novo, e
    que as mensagens não citam mais conceitos extintos (SPEC.md, "modo
    backlog", "o pipeline está em execução").
    """

    def test_blackbox_ainda_bloqueia_leitura_de_fonte(self):
        self.state("phase", "testing")
        os.makedirs(os.path.join(self.tmp, "src"), exist_ok=True)
        r = run_guard({
            "tool_name": "Read",
            "tool_input": {"file_path": "src/app.py"},
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 2)
        # A mensagem não pode mais mandar ler SPEC.md: ele não existe mais.
        self.assertNotIn("SPEC.md", r.stderr)

    def test_destrutivo_ainda_bloqueia_rm_rf(self):
        self.assertEqual(self.bash("rm -rf /tmp/x").returncode, 2)

    def test_destrutivo_bloqueia_branch_D_maiusculo(self):
        # -D numa parked/* não mergeada é o descarte que o gate existe
        # para impedir. O bloqueio aqui está CORRETO.
        r = self.bash("git branch -D parked/03-x")
        self.assertEqual(r.returncode, 2)

    def test_mensagem_destrutivo_nao_cita_modo_backlog(self):
        r = self.bash("rm -rf /tmp/x")
        self.assertNotIn("modo backlog", r.stderr)

    def test_spec_lock_ainda_bloqueia_escrita_na_spec_congelada(self):
        os.makedirs(os.path.join(self.tmp, "docs", "backlog"), exist_ok=True)
        with open(os.path.join(self.tmp, ".specgate", "batch.json"), "w", encoding="utf-8") as fh:
            json.dump({"backlog_aprovado": True}, fh)
        r = run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": "docs/backlog/01-x.md", "content": "# spec"},
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("o pipeline está em execução", r.stderr)

    def test_regressao_ainda_bloqueia_commit_com_suite_vermelha(self):
        self.config({"test_command": "false"})
        for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            subprocess.run(["git"] + cmd, cwd=self.tmp, capture_output=True)
        open(os.path.join(self.tmp, "a.txt"), "w").close()
        subprocess.run(["git", "add", "."], cwd=self.tmp, capture_output=True)
        r = self.bash("git commit -m wip")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Gate de regressão FALHOU", r.stderr)


class TestingPhaseReadEscapeTest(GuardBase):
    """I4 (achado da revisão final): guard_testing_phase bloqueava leitura de
    source_paths só quando os candidatos batiam por igualdade direta de
    caminho (file_path/path/pattern) ou quando o primeiro token de um
    comando Bash era um nome de leitor conhecido (BASH_READ_CMDS). Isso
    deixava furos casuais e muito naturais:

    - Grep sem 'path' varre a árvore inteira a partir de cwd (mesmo
      alcance de path=".", que já bloqueava) — a AUSÊNCIA do campo era lida
      como "nada para checar".
    - Glob com um pattern amplo ("**/*.py") nunca era comparado como
      caminho de verdade: só a string do pattern inteira era testada
      contra source_dirs, e "**/*.py" nunca bate com um diretório real.
    - Leitura via interpretador inline (`python3 -c "open(...)"`) ou
      heredoc escapava por completo: nem "python3" nem "sh" estão em
      BASH_READ_CMDS, então o comando inteiro nunca era examinado.
    - `git show HEAD:src/arquivo.py` lê conteúdo versionado sem tocar o
      disco de trabalho: "git" não está em BASH_READ_CMDS.
    """

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.tmp, "src"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp, "tests"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp, "docs"), exist_ok=True)
        with open(os.path.join(self.tmp, "src", "somar.py"), "w", encoding="utf-8") as fh:
            fh.write("def somar(a, b):\n    return a + b\n")
        self.state("phase", "testing")

    def _grep(self, tool_input):
        payload = {"tool_name": "Grep", "tool_input": dict(tool_input), "cwd": self.tmp}
        payload["tool_input"].setdefault("pattern", "SEGREDO")
        return run_guard(payload, self.tmp)

    def _glob(self, tool_input):
        return run_guard(
            {"tool_name": "Glob", "tool_input": dict(tool_input), "cwd": self.tmp}, self.tmp
        )

    def test_grep_sem_path_alcanca_source_e_e_bloqueado(self):
        r = self._grep({})
        self.assertEqual(r.returncode, 2)
        self.assertIn("Fase de testes black-box ativa", r.stderr)

    def test_grep_com_path_ponto_continua_bloqueado(self):
        # Não-regressão: o caso que já funcionava antes deste fix.
        r = self._grep({"path": "."})
        self.assertEqual(r.returncode, 2)

    def test_grep_com_path_fora_de_source_continua_permitido(self):
        r = self._grep({"path": "tests"})
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_grep_sem_path_sem_source_paths_configurado_nao_trava(self):
        self.config({"test_command": "true", "source_paths": []})
        r = self._grep({})
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_glob_pattern_amplo_alcanca_source_e_e_bloqueado(self):
        r = self._glob({"pattern": "**/*.py"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("Fase de testes black-box ativa", r.stderr)

    def test_glob_pattern_restrito_a_src_e_bloqueado(self):
        r = self._glob({"pattern": "src/**/*.py"})
        self.assertEqual(r.returncode, 2)

    def test_glob_pattern_restrito_a_tests_continua_permitido(self):
        r = self._glob({"pattern": "tests/**/*.py"})
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_glob_com_path_fora_de_source_permitido_mesmo_com_pattern_amplo(self):
        r = self._glob({"pattern": "**/*.py", "path": "docs"})
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_python_dash_c_lendo_source_e_bloqueado(self):
        r = self.bash("python3 -c \"print(open('src/somar.py').read())\"")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Fase de testes black-box ativa", r.stderr)

    def test_sh_dash_c_lendo_source_e_bloqueado(self):
        r = self.bash('sh -c "cat src/somar.py"')
        self.assertEqual(r.returncode, 2)

    def test_heredoc_python_lendo_source_e_bloqueado(self):
        r = self.bash("python3 <<'EOF'\nprint(open('src/somar.py').read())\nEOF\n")
        self.assertEqual(r.returncode, 2)

    def test_git_show_lendo_source_e_bloqueado(self):
        r = self.bash("git show HEAD:src/somar.py")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Fase de testes black-box ativa", r.stderr)

    def test_python_dash_c_sem_tocar_source_continua_permitido(self):
        r = self.bash("python3 -c \"print('hello world')\"")
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_leitura_de_specgate_json_continua_permitida(self):
        r = run_guard({
            "tool_name": "Read",
            "tool_input": {"file_path": ".specgate.json"},
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 0)

    def test_leitura_de_docs_continua_permitida(self):
        r = run_guard({
            "tool_name": "Read",
            "tool_input": {"file_path": "docs/plano.md"},
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 0)


class BashGrandeDemaisTest(GuardBase):
    """I5 (achado da revisão final): shlex.split é caro por token e
    write_targets devolvia TODOS os tokens não-flag; os 4 guards de estado
    (seq/batch/phase/gate.json) refaziam esse parsing E 2 os.path.realpath
    por candidato CADA UM, para o MESMO comando. Um heredoc comum e
    legítimo (`cat > arquivo <<'EOF' ... EOF`, dezenas de milhares de
    linhas) virava uma regressão de latência sentida em todo Bash.

    Acima de ~64KB o comando passa a ser tratado como NÃO VERIFICÁVEL. Esta
    classe cobre o fallback de `guard_po_gate` e `guard_gate_clear`
    especificamente (`_bloqueia_se_grande_demais`): bloqueia se houver
    qualquer gate aberto (algo em jogo agora que uma escrita não
    inspecionada poderia comprometer), libera se não houver nada para
    proteger. `guard_seq_lock` e `guard_batch_lock` têm fallback PRÓPRIO,
    diferente deste — ver `BashGrandeDemaisPorGuardTest` logo abaixo, que
    fecha o bypass relatado (achado da revisão de otimização): um fallback
    uniforme "bloqueia se há gate aberto" está ERRADO para esses dois, cuja
    condição normal de bloqueio não depende de gate algum.
    """

    def _cmd_grande(self, alvo="algum-arquivo-qualquer.txt"):
        padding = "x" * 70_000
        return f"cat > {alvo} <<'EOF'\n{padding}\nEOF\n"

    def test_comando_grande_sem_gate_aberto_e_permitido(self):
        r = self.bash(self._cmd_grande())
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_comando_grande_com_gate_aberto_e_bloqueado(self):
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "status": "aguardando-po", "opened_at_seq": 1}
        ]))
        r = self.bash(self._cmd_grande())
        self.assertEqual(r.returncode, 2)
        self.assertIn("COMANDO GRANDE DEMAIS PARA VERIFICAR", r.stderr)

    def test_comando_pequeno_com_gate_aberto_continua_verificado_normalmente(self):
        # Não-regressão: abaixo do limite, o comportamento de sempre
        # continua valendo — o comando pequeno escrevendo phase é
        # bloqueado pela razão de sempre, não pela de "grande demais".
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "status": "aguardando-po", "opened_at_seq": 1}
        ]))
        r = self.bash("printf 'implementing' > .specgate/phase")
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DE PO ABERTO", r.stderr)
        self.assertNotIn("GRANDE DEMAIS", r.stderr)

    def test_comando_grande_e_processado_rapido(self):
        # Prova de desempenho (I5): limite generoso só para travar a
        # regressão como teste automatizado — a medição de verdade (antes/
        # depois, 250KB e 1MB) está no relatório final.
        t0 = time.perf_counter()
        r = self.bash(self._cmd_grande())
        dt = time.perf_counter() - t0
        self.assertLess(dt, 1.5, msg=f"levou {dt:.3f}s (rc={r.returncode})")

    def test_leitura_de_source_em_comando_grande_e_permitida_na_fase_de_testes(self):
        # O guard de LEITURA (fase de testes) faz a escolha oposta à dos
        # guards de ESTADO: um comando grande demais para valer a pena
        # parsear é, de longe, mais provável de ser um heredoc legítimo do
        # que uma tentativa de ler source_paths por esse caminho (quem
        # quiser espiar o código não precisa de 64KB de comando) — friction
        # desproporcional ao risco, então o lado seguro aqui é LIBERAR.
        os.makedirs(os.path.join(self.tmp, "src"), exist_ok=True)
        with open(os.path.join(self.tmp, "src", "somar.py"), "w", encoding="utf-8") as fh:
            fh.write("def somar(a, b):\n    return a + b\n")
        self.state("phase", "testing")
        padding = " " * 70_000
        r = self.bash(f"cat src/somar.py{padding}")
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")


class BashGrandeDemaisPorGuardTest(GuardBase):
    """Achado da revisão sobre a otimização de latência de BashGrandeDemaisTest
    (I5): o fallback "bloqueia se há qualquer gate aberto" foi aplicado de
    forma UNIFORME aos 4 guards de estado, mas só é a condição normal de
    bloqueio de `guard_po_gate` e `guard_gate_clear`. Para os outros dois:

    - `guard_seq_lock` bloqueia SEMPRE que o alvo é .specgate/seq,
      independente de gate aberto (o contador só é mantido pelo hook de
      eventos) — o fallback uniforme LIBERAVA um comando grande escrevendo
      em .specgate/seq sempre que não houvesse gate aberto, embora o
      comportamento normal (comando pequeno) bloqueie sempre.
    - `guard_batch_lock` bloqueia quando o disco já tem
      backlog_aprovado: true, com ou sem gate aberto — o fallback uniforme
      LIBERAVA o desligamento de backlog_aprovado via comando grande
      sempre que não houvesse gate aberto, que é justamente o caso comum
      durante o fluxo (backlog aprovado, PBIs sendo trabalhados, nenhum
      gate de PO pendente no momento).

    Confirmado ao vivo antes deste fix: os dois `echo <70KB> > .specgate/seq`
    e `echo <70KB> > .specgate/batch.json` (sem gate aberto) saíam com
    rc=0 — bastava inflar o comando acima de 64KB para escrever o contador
    de turnos ou desligar backlog_aprovado.

    O fix usa uma pré-checagem O(n) barata (substring literal no comando
    bruto, sem tokenizar) para não punir comando grande alheio a
    .specgate/ — sem ela, `guard_batch_lock` bloquearia QUALQUER Bash
    grande sempre que backlog_aprovado estivesse true no disco (o caso
    comum durante o fluxo), o que seria fricção desproporcional.
    """

    def _cmd_grande(self, alvo):
        padding = "x" * 70_000
        return f"cat > {alvo} <<'EOF'\n{padding}\nEOF\n"

    def test_comando_grande_escrevendo_seq_sem_gate_aberto_e_bloqueado(self):
        # O bypass relatado, forma 1: escrever .specgate/seq via comando
        # grande, sem nenhum gate aberto, passava (rc=0) antes deste fix.
        r = self.bash(self._cmd_grande(".specgate/seq"))
        self.assertEqual(r.returncode, 2, msg=f"stderr: {r.stderr}")
        self.assertIn("COMANDO GRANDE DEMAIS PARA VERIFICAR", r.stderr)
        self.assertIn(".specgate/seq", r.stderr)

    def test_comando_grande_escrevendo_batch_com_backlog_aprovado_sem_gate_e_bloqueado(self):
        # O bypass relatado, forma 2: com backlog_aprovado: true já no
        # disco (o caso comum durante o fluxo) e nenhum gate aberto,
        # desligar backlog_aprovado via comando grande passava (rc=0)
        # antes deste fix.
        with open(os.path.join(self.tmp, ".specgate", "batch.json"), "w", encoding="utf-8") as fh:
            json.dump({"backlog_aprovado": True}, fh)
        r = self.bash(self._cmd_grande(".specgate/batch.json"))
        self.assertEqual(r.returncode, 2, msg=f"stderr: {r.stderr}")
        self.assertIn("COMANDO GRANDE DEMAIS PARA VERIFICAR", r.stderr)
        self.assertIn(".specgate/batch.json", r.stderr)

    def test_comando_grande_escrevendo_batch_sem_backlog_aprovado_e_permitido(self):
        # Não-regressão: sem backlog_aprovado true no disco, não há nada
        # para proteger em batch.json — comando grande passa, com ou sem
        # gate aberto.
        r = self.bash(self._cmd_grande(".specgate/batch.json"))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_comando_grande_alheio_ao_specgate_com_backlog_aprovado_e_permitido(self):
        # A preocupação de falso positivo do relatório: com backlog_aprovado
        # true no disco (comum durante o fluxo), um comando grande SEM
        # NENHUMA relação com .specgate/ (heredoc legítimo escrevendo um
        # arquivo de código qualquer) precisa continuar passando — a
        # pré-checagem de substring evita bloquear todo Bash grande só
        # porque backlog_aprovado está ligado.
        with open(os.path.join(self.tmp, ".specgate", "batch.json"), "w", encoding="utf-8") as fh:
            json.dump({"backlog_aprovado": True}, fh)
        r = self.bash(self._cmd_grande("algum-arquivo-de-codigo.py"))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_comando_grande_alheio_ao_specgate_sem_nada_aprovado_e_permitido(self):
        # Teste obrigatório do relatório: comando grande sem relação com
        # .specgate/, sem gate aberto e sem backlog_aprovado, passa.
        r = self.bash(self._cmd_grande("algum-arquivo-de-codigo.py"))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_comando_grande_escrevendo_seq_com_gate_aberto_tambem_bloqueado(self):
        # Não-regressão: gate aberto não é condição para este guard, mas
        # também não isenta — continua bloqueando.
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "status": "aguardando-po", "opened_at_seq": 1}
        ]))
        r = self.bash(self._cmd_grande(".specgate/seq"))
        self.assertEqual(r.returncode, 2, msg=f"stderr: {r.stderr}")
        self.assertIn(".specgate/seq", r.stderr)

    def test_comando_pequeno_escrevendo_seq_sem_gate_continua_bloqueado_como_hoje(self):
        # Não-regressão: comportamento de hoje do caminho pequeno (não
        # passa pelo fallback de comando grande) continua intacto.
        r = self.bash("echo 9999 > .specgate/seq")
        self.assertEqual(r.returncode, 2)
        self.assertIn("ESCRITA EM .specgate/seq BLOQUEADA", r.stderr)

    def test_comando_pequeno_desligando_backlog_aprovado_continua_bloqueado_como_hoje(self):
        with open(os.path.join(self.tmp, ".specgate", "batch.json"), "w", encoding="utf-8") as fh:
            json.dump({"backlog_aprovado": True}, fh)
        r = self.bash("printf '{}' > .specgate/batch.json")
        self.assertEqual(r.returncode, 2)
        self.assertIn("backlog_aprovado", r.stderr)

    def test_sem_specgate_json_comando_grande_escrevendo_seq_e_inerte(self):
        # Inércia: sem .specgate.json o guard inteiro é inerte, inclusive
        # para o comando grande.
        os.remove(os.path.join(self.tmp, ".specgate.json"))
        r = self.bash(self._cmd_grande(".specgate/seq"))
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")


PBI = "docs/backlog/02-conversao.md"
OUTRO_PBI = "docs/backlog/03-cli.md"


class ChokepointPorPbiTest(GuardBase):
    """0.3.0: gate aberto de um PBI não trava mais a fila inteira.

    Até 0.2.0 qualquer gate aberto bloqueava qualquer transição de fase — o
    que fazia um PBI estacionado parar todos os outros (limite documentado no
    README). A propriedade que importa continua: nada avança NAQUILO que o PO
    não decidiu.
    """

    def _gate(self, checkpoint, pbi=None, status="aguardando-po"):
        entrada = {"checkpoint": checkpoint, "rodada": 1, "status": status, "opened_at_seq": 5}
        if pbi:
            entrada["pbi"] = pbi
        self.state("gate.json", json.dumps([entrada]))

    def _ativa(self, pbi, fase="testing"):
        conteudo = f"{fase}:{pbi}" if pbi else fase
        return run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": ".specgate/phase", "content": conteudo},
            "cwd": self.tmp,
        }, self.tmp)

    def test_gate_de_outro_pbi_nao_trava_este(self):
        self._gate("ambiguidade", OUTRO_PBI)
        r = self._ativa(PBI)
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_gate_do_proprio_pbi_trava(self):
        self._gate("ambiguidade", PBI)
        r = self._ativa(PBI)
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DE PO ABERTO", r.stderr)

    def test_gate_de_backlog_trava_todo_mundo(self):
        """O contrato do lote inteiro está em jogo: nenhum PBI dele significa
        nada até o PO decidir o backlog.
        """
        self._gate("backlog")
        r = self._ativa(PBI)
        self.assertEqual(r.returncode, 2)
        self.assertIn("GATE DE PO ABERTO", r.stderr)

    def test_fase_sem_pbi_declarado_cai_no_bloqueio_amplo(self):
        """Não saber de quem é a transição não pode ser mais permissivo que
        saber — senão omitir o sufixo viraria a forma de destravar.
        """
        self._gate("ambiguidade", OUTRO_PBI)
        r = self._ativa(None)
        self.assertEqual(r.returncode, 2)

    def test_limpar_a_fase_cai_no_bloqueio_amplo(self):
        self._gate("ambiguidade", OUTRO_PBI)
        r = self._ativa("", fase="")
        self.assertEqual(r.returncode, 2)

    def test_escrita_por_bash_cai_no_bloqueio_amplo(self):
        """Num comando Bash o conteúdo pretendido não é inspecionável sem
        interpretar o shell — sem saber o PBI, vale o lado seguro de 0.2.0.
        """
        self._gate("ambiguidade", OUTRO_PBI)
        r = self.bash(f"printf 'testing:{PBI}' > .specgate/phase")
        self.assertEqual(r.returncode, 2)

    def test_gate_decidido_de_outro_pbi_nao_trava_nada(self):
        self._gate("ambiguidade", OUTRO_PBI, status="respondido")
        self.assertEqual(self._ativa(PBI).returncode, 0)

    def test_dois_gates_abertos_so_o_do_pbi_alvo_conta(self):
        self.state("gate.json", json.dumps([
            {"checkpoint": "ambiguidade", "pbi": OUTRO_PBI, "rodada": 1,
             "status": "aguardando-po", "opened_at_seq": 5},
            {"checkpoint": "aceite", "pbi": "docs/backlog/07-x.md", "rodada": 1,
             "status": "aguardando-po", "opened_at_seq": 5},
        ]))
        self.assertEqual(self._ativa(PBI).returncode, 0)
        self.assertEqual(self._ativa(OUTRO_PBI).returncode, 2)

    def test_gate_sem_campo_pbi_trava_amplo(self):
        """Gate sem PBI (formato legado) não pode ser ignorado só porque não
        dá para associá-lo a um item da fila.
        """
        self._gate("testes")
        self.assertEqual(self._ativa(PBI).returncode, 2)


class RedEvidenceTest(GuardBase):
    """Prova de RED: a suíte precisa falhar ANTES de a implementação começar.

    O buraco que isto fecha: um teste tautológico passa desde o dia zero, o
    implementer encontra a suíte verde e "termina" sem escrever nada. Antes
    daqui, o único olho capaz de pegar isso era o revisor de conformidade.
    """

    def _config(self, test_command, **extra):
        data = {"test_command": test_command, "source_paths": ["src"], "require_red": True}
        data.update(extra)
        self.config(data)

    def _transiciona(self, pbi=PBI):
        return run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": ".specgate/phase", "content": f"implementing:{pbi}"},
            "cwd": self.tmp,
        }, self.tmp)

    def _red(self):
        with open(os.path.join(self.tmp, ".specgate", "red.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_suite_verde_antes_de_implementar_bloqueia(self):
        self._config("true")
        r = self._transiciona()
        self.assertEqual(r.returncode, 2)
        self.assertIn("PROVA DE RED FALHOU", r.stderr)

    def test_suite_vermelha_libera_e_registra_a_prova(self):
        self._config("false")
        r = self._transiciona()
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")
        self.assertTrue(self._red()[PBI]["proven"])
        self.assertFalse(self._red()[PBI]["waived"])

    def test_prova_registrada_dispensa_rodar_a_suite_de_novo(self):
        """O ponto crítico do cache: depois que o código existe, a suíte
        PASSA — reexigir vermelho a cada reentrada em implementação (volta de
        uma conformidade reprovada, por exemplo) travaria o fluxo pedindo algo
        que não pode mais acontecer.
        """
        self._config("false")
        self.assertEqual(self._transiciona().returncode, 0)
        self._config("true")  # implementação pronta: suíte agora passa
        self.assertEqual(self._transiciona().returncode, 0)

    def test_rodada_nova_do_gate_de_testes_invalida_a_prova(self):
        self._config("false")
        self.assertEqual(self._transiciona().returncode, 0)
        # Testes reprovados e reescritos: a prova da rodada 1 não vale para a 2.
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": PBI, "rodada": 1, "status": "reprovado"},
            {"checkpoint": "testes", "pbi": PBI, "rodada": 2, "status": "aprovado"},
        ]))
        self._config("true")
        r = self._transiciona()
        self.assertEqual(r.returncode, 2)
        self.assertIn("PROVA DE RED FALHOU", r.stderr)

    def test_rodada_de_aceite_nova_nao_invalida_a_prova(self):
        """Aceite reprovado devolve o PBI para implementação, mas os testes
        continuam os mesmos — exigir RED aqui seria pedir o impossível.
        """
        # Ordem do fluxo real: o gate de testes é aprovado ANTES da transição
        # para implementação, então a prova nasce carimbada com a rodada dele.
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": PBI, "rodada": 1, "status": "aprovado"},
        ]))
        self._config("false")
        self.assertEqual(self._transiciona().returncode, 0)
        # Aceite reprovado: o PBI volta para implementação com os MESMOS
        # testes. (Sem gate aberto na fixture — um gate 'aguardando-po'
        # barraria a transição no chokepoint de PO, antes de chegar ao RED.)
        self.state("gate.json", json.dumps([
            {"checkpoint": "testes", "pbi": PBI, "rodada": 1, "status": "aprovado"},
            {"checkpoint": "aceite", "pbi": PBI, "rodada": 1, "status": "reprovado"},
        ]))
        self._config("true")
        self.assertEqual(self._transiciona().returncode, 0)

    def test_gate_de_red_aprovado_pelo_po_libera_o_verde(self):
        """Falso-verde legítimo (comportamento já existe): não é parede, é
        pergunta — e a resposta do PO destrava.
        """
        self._config("true")
        self.state("gate.json", json.dumps([
            {"checkpoint": "red", "pbi": PBI, "rodada": 1, "status": "aprovado", "opened_at_seq": 3},
        ]))
        r = self._transiciona()
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")
        self.assertTrue(self._red()[PBI]["waived"])

    def test_gate_de_red_de_outro_pbi_nao_libera(self):
        self._config("true")
        self.state("gate.json", json.dumps([
            {"checkpoint": "red", "pbi": "docs/backlog/07-outro.md", "rodada": 1,
             "status": "aprovado", "opened_at_seq": 3},
        ]))
        self.assertEqual(self._transiciona().returncode, 2)

    def test_gate_de_red_ainda_aguardando_po_nao_libera(self):
        self._config("true")
        self.state("gate.json", json.dumps([
            {"checkpoint": "red", "pbi": PBI, "rodada": 1, "status": "aguardando-po", "opened_at_seq": 3},
        ]))
        # Com gate aberto o chokepoint de PO já barra antes, e é ele quem
        # responde — o importante é que a transição NÃO acontece.
        self.assertEqual(self._transiciona().returncode, 2)

    def test_require_red_false_desliga(self):
        self._config("true", require_red=False)
        self.assertEqual(self._transiciona().returncode, 0)

    def test_sem_test_command_nao_ha_vermelho_a_provar(self):
        self.config({"source_paths": ["src"], "require_red": True})
        self.assertEqual(self._transiciona().returncode, 0)

    def test_transicao_para_testing_nao_dispara(self):
        self._config("true")
        r = run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": ".specgate/phase", "content": "testing"},
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_limpar_a_fase_nao_dispara(self):
        self._config("true")
        r = run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": ".specgate/phase", "content": ""},
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_escrita_em_outro_arquivo_nao_dispara(self):
        self._config("true")
        r = run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": "notas.md", "content": "implementing:algo"},
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_transicao_por_bash_tambem_e_verificada(self):
        self._config("true")
        r = self.bash(f"printf 'implementing:{PBI}' > .specgate/phase")
        self.assertEqual(r.returncode, 2)
        self.assertIn("PROVA DE RED FALHOU", r.stderr)

    def test_formato_antigo_sem_pbi_ainda_e_verificado(self):
        self._config("true")
        r = self.bash("printf 'implementing' > .specgate/phase")
        self.assertEqual(r.returncode, 2)
        self.assertIn("PROVA DE RED FALHOU", r.stderr)

    def test_timeout_da_suite_bloqueia(self):
        self._config("sleep 5", test_timeout_seconds=1)
        r = self._transiciona()
        self.assertEqual(r.returncode, 2)
        self.assertIn("excedeu o tempo limite", r.stderr)

    def test_comando_grande_mencionando_phase_e_implementing_bloqueia(self):
        self._config("true")
        recheio = "x" * (64 * 1024)
        r = self.bash(f"printf 'implementing' > .specgate/phase # {recheio}")
        self.assertEqual(r.returncode, 2)
        self.assertIn("COMANDO GRANDE DEMAIS PARA VERIFICAR", r.stderr)

    def test_comando_grande_alheio_a_fase_e_permitido(self):
        self._config("true")
        recheio = "x" * (64 * 1024)
        r = self.bash(f"printf 'oi' > /tmp/qualquer.txt # {recheio}")
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_sem_specgate_json_e_inerte(self):
        self._config("true")
        os.remove(os.path.join(self.tmp, ".specgate.json"))
        self.assertEqual(self._transiciona().returncode, 0)


class TraceTest(GuardBase):
    """Rastreabilidade requisito -> teste, verificada na entrada da
    implementação. Antes daqui o mapa de cobertura era prosa no relatório do
    blackbox-tester: consumido uma vez e perdido com a sessão.
    """

    SPEC = """# Conversão
## Comportamentos
1. [C1] dado 1 metro, quando converter, então 3.28 pés
2. [C2] dado 0, quando converter, então 0
## Casos de erro
1. [E1] dado "abc", quando converter, então erro
"""

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.tmp, "docs", "backlog"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp, "tests"), exist_ok=True)
        self._escreve(PBI, self.SPEC)
        self._escreve("tests/test_conv.py", "def test_metros_para_pes():\n    pass\n")

    def _escreve(self, rel, texto):
        caminho = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write(texto)

    def _trace(self, data):
        self._escreve("docs/traceability.json", json.dumps(data))

    def _completo(self):
        return {
            "02-conversao#C1": {"tests": ["tests/test_conv.py::test_metros_para_pes"],
                                "status": "covered"},
            "02-conversao#C2": {"tests": ["tests/test_conv.py::test_metros_para_pes"],
                                "status": "covered"},
            "02-conversao#E1": {"tests": ["tests/test_conv.py::test_metros_para_pes"],
                                "status": "covered"},
        }

    def _transiciona(self):
        return run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": ".specgate/phase", "content": f"implementing:{PBI}"},
            "cwd": self.tmp,
        }, self.tmp)

    def test_matriz_completa_libera(self):
        self._trace(self._completo())
        r = self._transiciona()
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_requisito_sem_entrada_bloqueia(self):
        parcial = self._completo()
        del parcial["02-conversao#E1"]
        self._trace(parcial)
        r = self._transiciona()
        self.assertEqual(r.returncode, 2)
        self.assertIn("RASTREABILIDADE INCOMPLETA", r.stderr)
        self.assertIn("02-conversao#E1", r.stderr)

    def test_matriz_ausente_bloqueia_listando_todos(self):
        r = self._transiciona()
        self.assertEqual(r.returncode, 2)
        for rid in ("C1", "C2", "E1"):
            self.assertIn(f"02-conversao#{rid}", r.stderr)

    def test_requisito_declarado_sem_cobertura_passa(self):
        """O gate proíbe o silêncio sobre o requisito, não a ausência de
        cobertura — essa decisão é do PO, desde que fique escrita.
        """
        parcial = self._completo()
        parcial["02-conversao#E1"] = {"tests": [], "status": "uncovered",
                                      "why": "erro de entrada fica no PBI 05"}
        self._trace(parcial)
        r = self._transiciona()
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_teste_inexistente_no_arquivo_bloqueia(self):
        quebrado = self._completo()
        quebrado["02-conversao#C1"] = {"tests": ["tests/test_conv.py::test_que_nao_existe"],
                                       "status": "covered"}
        self._trace(quebrado)
        r = self._transiciona()
        self.assertEqual(r.returncode, 2)
        self.assertIn("test_que_nao_existe", r.stderr)

    def test_arquivo_de_teste_inexistente_bloqueia(self):
        quebrado = self._completo()
        quebrado["02-conversao#C1"] = {"tests": ["tests/test_sumiu.py::test_x"], "status": "covered"}
        self._trace(quebrado)
        r = self._transiciona()
        self.assertEqual(r.returncode, 2)
        self.assertIn("tests/test_sumiu.py", r.stderr)

    def test_referencia_a_arquivo_inteiro_e_aceita(self):
        so_arquivo = {k: {"tests": ["tests/test_conv.py"], "status": "covered"}
                      for k in self._completo()}
        self._trace(so_arquivo)
        self.assertEqual(self._transiciona().returncode, 0)

    def test_spec_sem_ids_deixa_o_guard_inerte(self):
        """Compatibilidade: a rastreabilidade passa a ser exigida quando a
        spec passa a declarar requisitos, não por uma data de corte.
        """
        self._escreve(PBI, "# Conversão\n## Comportamentos\n1. dado 1 metro, então 3.28 pés\n")
        self.assertEqual(self._transiciona().returncode, 0)

    def test_require_trace_false_desliga(self):
        self.config({"test_command": "true", "source_paths": ["src"],
                     "require_red": False, "require_trace": False})
        self.assertEqual(self._transiciona().returncode, 0)

    def test_matriz_corrompida_bloqueia_como_ausente(self):
        self._escreve("docs/traceability.json", "{lixo")
        r = self._transiciona()
        self.assertEqual(r.returncode, 2)
        self.assertIn("RASTREABILIDADE INCOMPLETA", r.stderr)

    def test_transicao_para_testing_nao_dispara(self):
        r = run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": ".specgate/phase", "content": "testing"},
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_caminho_customizado_da_matriz_e_respeitado(self):
        self.config({"test_command": "true", "source_paths": ["src"], "require_red": False,
                     "traceability_path": "docs/rastro.json"})
        self._escreve("docs/rastro.json", json.dumps(self._completo()))
        self.assertEqual(self._transiciona().returncode, 0)

    def test_id_repetido_na_spec_conta_uma_vez(self):
        self._escreve(PBI, self.SPEC + "\nver [C1] acima\n")
        self._trace(self._completo())
        self.assertEqual(self._transiciona().returncode, 0)


class AttemptsTest(GuardBase):
    """Teto de tentativas contado pelo hook, não declarado pelo agente.

    Antes daqui, max_fix_attempts existia só como texto no prompt do
    implementer: a regra "término mecânico, nunca autodeclarado" valia para o
    verde (gate de regressão) e não valia para o teto.
    """

    def _config(self, **extra):
        data = {"test_command": "pytest -q", "source_paths": ["src"],
                "require_red": False, "max_fix_attempts": 3}
        data.update(extra)
        self.config(data)

    def _implementando(self, pbi=PBI):
        self.state("phase", f"implementing:{pbi}")

    def _roda_suite(self, vezes=1, cmd="pytest -q"):
        for _ in range(vezes):
            self.assertEqual(self.bash(cmd).returncode, 0)

    def _edita_fonte(self):
        return run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": "src/conversao.py", "content": "x = 1"},
            "cwd": self.tmp,
        }, self.tmp)

    def _attempts(self):
        with open(os.path.join(self.tmp, ".specgate", "attempts.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def setUp(self):
        super().setUp()
        self._config()
        self._implementando()

    def test_execucao_da_suite_incrementa_o_contador(self):
        self._roda_suite(2)
        self.assertEqual(self._attempts()["count"], 2)
        self.assertEqual(self._attempts()["pbi"], PBI)

    def test_abaixo_do_teto_edicao_de_fonte_e_permitida(self):
        self._roda_suite(2)
        self.assertEqual(self._edita_fonte().returncode, 0)

    def test_no_teto_edicao_de_fonte_e_bloqueada(self):
        self._roda_suite(3)
        r = self._edita_fonte()
        self.assertEqual(r.returncode, 2)
        self.assertIn("TETO DE TENTATIVAS ATINGIDO", r.stderr)

    def test_no_teto_rodar_a_suite_continua_liberado(self):
        """É assim que o estado real (quais testes ainda falham) chega ao
        relatório do PO — bloquear a suíte no teto esconderia justamente a
        informação que a parada precisa entregar.
        """
        self._roda_suite(4)
        self.assertEqual(self.bash("pytest -q").returncode, 0)

    def test_no_teto_edicao_fora_de_source_paths_e_permitida(self):
        self._roda_suite(3)
        r = run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": "notas.md", "content": "hipótese"},
            "cwd": self.tmp,
        }, self.tmp)
        self.assertEqual(r.returncode, 0, msg=f"stderr: {r.stderr}")

    def test_rodada_nova_de_gate_do_pbi_zera_o_contador(self):
        self._roda_suite(3)
        self.assertEqual(self._edita_fonte().returncode, 2)
        self.state("gate.json", json.dumps([
            {"checkpoint": "aceite", "pbi": PBI, "rodada": 1, "status": "reprovado"},
            {"checkpoint": "aceite", "pbi": PBI, "rodada": 2, "status": "aprovado"},
        ]))
        self.assertEqual(self._edita_fonte().returncode, 0)

    def test_contador_de_outro_pbi_nao_conta_contra_este(self):
        self._roda_suite(3)
        self._implementando("docs/backlog/03-outro.md")
        self.assertEqual(self._edita_fonte().returncode, 0)

    def test_fase_sem_pbi_deixa_o_contador_inerte(self):
        self.state("phase", "implementing")
        self._roda_suite(5)
        self.assertEqual(self._edita_fonte().returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".specgate", "attempts.json")))

    def test_fora_da_fase_de_implementacao_nada_conta(self):
        self.state("phase", "")
        self._roda_suite(5)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".specgate", "attempts.json")))

    def test_max_fix_attempts_zero_desliga_o_teto(self):
        self._config(max_fix_attempts=0)
        self._roda_suite(5)
        self.assertEqual(self._edita_fonte().returncode, 0)

    def test_comando_alheio_nao_incrementa(self):
        self.assertEqual(self.bash("git status").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".specgate", "attempts.json")))

    def test_runner_reconhecido_mesmo_com_argumentos_diferentes(self):
        """`pytest tests/test_x.py -k caso` é a mesma suíte do test_command
        configurado (`pytest -q`) rodada em recorte menor — contar só a
        invocação literal deixaria o teto trivial de contornar sem querer.
        """
        self._roda_suite(1, cmd="pytest tests/test_x.py -k caso")
        self.assertEqual(self._attempts()["count"], 1)

    def test_wrapper_python_dash_m_nao_confunde_o_token(self):
        self._config(test_command="python3 -m pytest -q")
        self._roda_suite(1, cmd="python3 -m pytest -q tests/")
        self.assertEqual(self._attempts()["count"], 1)

    def test_script_python_qualquer_nao_conta_como_suite(self):
        self._config(test_command="python3 -m pytest -q")
        self.assertEqual(self.bash("python3 scripts/seed.py").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".specgate", "attempts.json")))

    def test_aviso_de_troca_de_olhos_na_metade_do_teto(self):
        """Metade do teto é onde trocar de implementer ainda é barato — o
        aviso precisa chegar ANTES do bloqueio, não junto com ele.
        """
        r1 = self.bash("pytest -q")
        self.assertNotIn("Tentativa", r1.stdout)
        r2 = self.bash("pytest -q")  # teto 3 -> aviso na 2ª
        self.assertEqual(r2.returncode, 0)
        self.assertIn("Tentativa 2/3", r2.stdout)
        self.assertIn("contexto limpo", r2.stdout)

    def test_aviso_sai_uma_vez_so(self):
        for _ in range(2):
            self.bash("pytest -q")
        r = self.bash("pytest -q")
        self.assertNotIn("Tentativa", r.stdout)

    def test_aviso_e_json_valido_com_system_message(self):
        self.bash("pytest -q")
        r = self.bash("pytest -q")
        self.assertEqual(list(json.loads(r.stdout).keys()), ["systemMessage"])

    def test_token_generico_exige_o_comando_inteiro(self):
        """`npm test` deixaria só "test" como token significativo, que casa
        com qualquer `ls test` — nesse caso o guard exige o test_command
        inteiro em vez de inflar o contador contra o PBI.
        """
        self._config(test_command="npm test")
        self.assertEqual(self.bash("ls test").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".specgate", "attempts.json")))
        self._roda_suite(1, cmd="npm test")
        self.assertEqual(self._attempts()["count"], 1)


if __name__ == "__main__":
    unittest.main()
