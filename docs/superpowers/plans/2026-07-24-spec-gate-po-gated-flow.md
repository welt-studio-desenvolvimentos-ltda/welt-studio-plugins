# spec-gate 0.2.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transformar o spec-gate num fluxo único gateado pelo PO, onde nenhuma fase avança sem uma decisão humana comprovadamente real.

**Architecture:** Um módulo de estado compartilhado (`specgate_state.py`) centraliza a leitura/escrita de `.specgate/`. O `log_event.py` incrementa um contador monotônico `seq` a cada `UserPromptSubmit`; o `gate_guard.py` usa esse contador como prova inforjável de turno humano, bloqueando a transição de fase (escrita em `.specgate/phase`) enquanto houver gate de PO aberto sem decisão.

**Tech Stack:** Python 3.7+ stdlib apenas (json, os, re, shlex, subprocess, sys, unittest). Bash. Markdown com frontmatter YAML.

**Spec:** [docs/superpowers/specs/2026-07-24-spec-gate-po-gated-flow-design.md](docs/superpowers/specs/2026-07-24-spec-gate-po-gated-flow-design.md) (commit `f886e5d`)

## Global Constraints

- **Zero dependências externas.** Python 3.7+ stdlib only. Testes usam `unittest`, nunca pytest — pytest violaria a convenção do projeto registrada no CLAUDE.md.
- **Fail-open absoluto.** Qualquer erro interno de hook resulta em `sys.exit(0)`. Nenhum bug de estado pode travar a sessão do Claude.
- **Protocolo de hook:** JSON no stdin; `exit 0` permite; `exit 2` + stderr bloqueia e devolve o texto ao Claude.
- **Inércia:** sem `.specgate.json` na raiz do projeto, todo guard sai 0 imediatamente.
- **Idioma:** mensagens de bloqueio, docs e comentários em pt-BR.
- **Comando de teste do repo:** `python3 -m unittest discover -s plugins/spec-gate/tests -t . -v`
- **Versão alvo:** `0.2.0` em `plugin.json` **e** `marketplace.json` (os dois devem bater — houve drift em 0.1.4).

---

### Task 1: Módulo de estado + harness de teste

Cria a fundação. `log_event.py` escreve o `seq`, `gate_guard.py` lê — sem módulo comum a lógica duplicaria. Python coloca o diretório do script em `sys.path[0]`, então `import specgate_state` funciona sem manipular path.

**Files:**
- Create: `plugins/spec-gate/scripts/specgate_state.py`
- Create: `plugins/spec-gate/tests/__init__.py` (vazio)
- Create: `plugins/spec-gate/tests/test_state.py`

**Interfaces:**
- Produces: `read_seq(cwd) -> int`, `bump_seq(cwd) -> int`, `read_gates(cwd) -> list[dict]`, `open_gates(cwd) -> list[dict]`, `has_human_turn_since(cwd, opened_at_seq) -> bool`, `gate_po_1_passed(cwd) -> bool`

- [ ] **Step 1: Escrever os testes que falham**

```python
# plugins/spec-gate/tests/test_state.py
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python3 -m unittest discover -s plugins/spec-gate/tests -t . -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'specgate_state'`

- [ ] **Step 3: Implementar o módulo**

```python
# plugins/spec-gate/scripts/specgate_state.py
#!/usr/bin/env python3
"""spec-gate: leitura e escrita do estado compartilhado em .specgate/.

Usado por gate_guard.py (PreToolUse) e log_event.py (demais eventos).
Zero dependências: stdlib apenas. Toda função devolve um default seguro em
caso de erro — nenhum problema de estado pode travar a sessão do Claude.
"""
import json
import os

STATE_DIR = ".specgate"


def _p(cwd, *parts):
    return os.path.join(cwd, STATE_DIR, *parts)


def read_seq(cwd):
    """Contador monotônico de turnos do usuário. 0 se ausente ou ilegível."""
    try:
        with open(_p(cwd, "seq"), "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return 0


def bump_seq(cwd):
    """Incrementa e devolve o novo valor. Só o UserPromptSubmit chama isto."""
    n = read_seq(cwd) + 1
    try:
        os.makedirs(_p(cwd), exist_ok=True)
        with open(_p(cwd, "seq"), "w", encoding="utf-8") as fh:
            fh.write(str(n))
    except OSError:
        return read_seq(cwd)
    return n


def read_gates(cwd):
    """Todos os gates registrados. [] se ausente ou ilegível."""
    try:
        with open(_p(cwd, "gate.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        data = [data]  # tolera o formato de gate único
    if not isinstance(data, list):
        return []
    return [g for g in data if isinstance(g, dict)]


def open_gates(cwd):
    """Gates aguardando decisão do PO."""
    return [g for g in read_gates(cwd) if g.get("status") == "aguardando-po"]


def has_human_turn_since(cwd, opened_at_seq):
    """Camada 1 do gate: houve turno REAL do usuário depois do gate abrir?

    O Claude não consegue fabricar um UserPromptSubmit, então este é o
    único fato do sistema que ele não pode forjar.
    """
    try:
        return read_seq(cwd) > int(opened_at_seq)
    except (TypeError, ValueError):
        return False


def gate_po_1_passed(cwd):
    """Fato em disco: o Gate PO 1 (backlog) já foi aprovado?

    O congelamento de docs/backlog/ só liga daqui em diante. Antes disso o
    spec-analyst ainda está escrevendo as specs e precisa de acesso.
    """
    try:
        with open(_p(cwd, "batch.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    return bool(isinstance(data, dict) and data.get("backlog_aprovado"))
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python3 -m unittest discover -s plugins/spec-gate/tests -t . -v`
Expected: PASS, 9 testes

- [ ] **Step 5: Commit**

```bash
git add plugins/spec-gate/scripts/specgate_state.py plugins/spec-gate/tests/
git commit -m "feat(spec-gate): módulo de estado compartilhado + harness unittest"
```

---

### Task 2: Contador `seq` no log_event.py

**Files:**
- Modify: `plugins/spec-gate/scripts/log_event.py`
- Create: `plugins/spec-gate/tests/test_log_event.py`

**Interfaces:**
- Consumes: `specgate_state.bump_seq`, `specgate_state.read_seq` (Task 1)
- Produces: `.specgate/seq` incrementado só em `UserPromptSubmit`; campo `seq` nas entradas de `events.jsonl`

- [ ] **Step 1: Escrever o teste que falha**

```python
# plugins/spec-gate/tests/test_log_event.py
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python3 -m unittest discover -s plugins/spec-gate/tests -t . -v`
Expected: FAIL — `read_seq` devolve 0 e `entrada["seq"]` levanta `KeyError`

- [ ] **Step 3: Modificar o log_event.py**

Adicionar o import no topo (após `import time`):

```python
import specgate_state
```

Substituir o bloco que monta `entry` (hoje linhas 24-28) por:

```python
    ev = payload.get("hook_event_name", "event")
    entry = {
        "ts": time.strftime("%H:%M:%S"),
        "event": ev,
    }
    # O seq só avança em turno real do usuário: é a prova inforjável que o
    # gate de PO consome. Nenhum outro evento pode movê-lo.
    if ev == "UserPromptSubmit":
        entry["seq"] = specgate_state.bump_seq(cwd)
    else:
        entry["seq"] = specgate_state.read_seq(cwd)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python3 -m unittest discover -s plugins/spec-gate/tests -t . -v`
Expected: PASS, 13 testes

- [ ] **Step 5: Commit**

```bash
git add plugins/spec-gate/scripts/log_event.py plugins/spec-gate/tests/test_log_event.py
git commit -m "feat(spec-gate): contador seq monotônico em UserPromptSubmit"
```

---

### Task 3: Helper de alvos de escrita + `guard_po_gate`

O chokepoint. Com gate aberto, a escrita em `.specgate/phase` fica travada e o fluxo não avança.

**Files:**
- Modify: `plugins/spec-gate/scripts/gate_guard.py`
- Create: `plugins/spec-gate/tests/test_gate_guard.py`

**Interfaces:**
- Consumes: `specgate_state.open_gates` (Task 1)
- Produces: `write_targets(tool, tool_input) -> list[str]`, `guard_po_gate(tool, tool_input, cwd) -> None`

- [ ] **Step 1: Escrever os testes que falham**

```python
# plugins/spec-gate/tests/test_gate_guard.py
import json
import os
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python3 -m unittest discover -s plugins/spec-gate/tests -t . -v`
Expected: FAIL — os testes de bloqueio recebem returncode 0

- [ ] **Step 3: Implementar no gate_guard.py**

Adicionar `import specgate_state` no topo. Adicionar as constantes junto das demais:

```python
PHASE_REL = os.path.join(".specgate", "phase")
GATE_REL = os.path.join(".specgate", "gate.json")
```

Adicionar as funções novas (antes de `main()`):

```python
def write_targets(tool, tool_input):
    """Caminhos que esta chamada pretende escrever.

    Usado pelos guards que protegem arquivos de estado. Para Bash, devolve
    todos os tokens não-flag quando o comando tem cara de escrita — é
    grosseiro de propósito: preferimos um falso positivo (que o agente
    contorna explicando ao PO) a um falso negativo que fura o gate.
    """
    if tool in ("Write", "Edit"):
        c = tool_input.get("file_path") or tool_input.get("path")
        return [c] if isinstance(c, str) else []
    if tool != "Bash":
        return []
    cmd = tool_input.get("command", "")
    if not isinstance(cmd, str) or not cmd:
        return []
    if not any(rx.search(cmd) for rx in BASH_WRITE_RES):
        return []
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        tokens = cmd.split()
    return [t for t in tokens[1:] if not t.startswith("-")]


def _same_file(candidate, cwd, rel):
    if not candidate:
        return False
    return os.path.realpath(os.path.join(cwd, os.path.expanduser(candidate))) == \
        os.path.realpath(os.path.join(cwd, rel))


def guard_po_gate(tool, tool_input, cwd):
    """Chokepoint: com gate de PO aberto, a transição de fase fica travada.

    Toda transição de fase passa por escrita em .specgate/phase, então
    bloquear esse arquivo impede fisicamente o fluxo de avançar.
    """
    gates = specgate_state.open_gates(cwd)
    if not gates:
        return
    if not any(_same_file(t, cwd, PHASE_REL) for t in write_targets(tool, tool_input)):
        return
    nomes = ", ".join(str(g.get("checkpoint", "?")) for g in gates)
    block(
        f"[spec-gate] GATE DE PO ABERTO ({nomes}). O fluxo não avança de fase "
        "enquanto o PO não decidir. NÃO tente contornar o bloqueio nem editar "
        "o arquivo de fase por outro caminho. Apresente ao PO a decisão "
        "pendente, em uma linha e com opções concretas, e aguarde a resposta."
    )
```

Ligar em `main()`, dentro do `try`, antes dos guards de Bash:

```python
        guard_po_gate(tool, tool_input, cwd)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python3 -m unittest discover -s plugins/spec-gate/tests -t . -v`
Expected: PASS, 20 testes

- [ ] **Step 5: Commit**

```bash
git add plugins/spec-gate/scripts/gate_guard.py plugins/spec-gate/tests/test_gate_guard.py
git commit -m "feat(spec-gate): guard_po_gate trava a transição de fase com gate aberto"
```

---

### Task 4: `guard_gate_clear` — impedir auto-liberação

A camada que fecha o buraco: sem turno humano, o Claude não registra decisão.

**Files:**
- Modify: `plugins/spec-gate/scripts/gate_guard.py`
- Modify: `plugins/spec-gate/tests/test_gate_guard.py`

**Interfaces:**
- Consumes: `write_targets`, `_same_file` (Task 3); `specgate_state.open_gates`, `has_human_turn_since` (Task 1)
- Produces: `guard_gate_clear(tool, tool_input, cwd) -> None`

- [ ] **Step 1: Escrever os testes que falham**

Adicionar ao `test_gate_guard.py`:

```python
class GateClearTest(GuardBase):
    def _abre(self, opened_at_seq=10, checkpoint="testes"):
        self.state("gate.json", json.dumps([
            {"checkpoint": checkpoint, "status": "aguardando-po",
             "opened_at_seq": opened_at_seq}
        ]))

    def _escreve_gate(self, content="[]"):
        return run_guard({
            "tool_name": "Write",
            "tool_input": {"file_path": ".specgate/gate.json", "content": content},
            "cwd": self.tmp,
        }, self.tmp)

    def test_sem_turno_humano_bloqueia_escrita_no_gate(self):
        self._abre(opened_at_seq=10)
        self.state("seq", "10")
        r = self._escreve_gate()
        self.assertEqual(r.returncode, 2)
        self.assertIn("AUTO-LIBERAÇÃO BLOQUEADA", r.stderr)

    def test_com_turno_humano_permite_escrita_no_gate(self):
        self._abre(opened_at_seq=10)
        self.state("seq", "11")
        self.assertEqual(self._escreve_gate().returncode, 0)

    def test_bloqueia_tambem_via_bash(self):
        self._abre(opened_at_seq=10)
        self.state("seq", "10")
        r = self.bash("printf '[]' > .specgate/gate.json")
        self.assertEqual(r.returncode, 2)

    def test_resposta_parcial_um_gate_com_turno_outro_sem(self):
        # PBI-03 abriu em 10 (tem turno: seq=15), PBI-05 abriu em 20 (não tem).
        # Regra conservadora: existindo QUALQUER gate sem turno, a escrita para.
        self.state("gate.json", json.dumps([
            {"checkpoint": "aceite", "pbi": "03", "status": "aguardando-po", "opened_at_seq": 10},
            {"checkpoint": "aceite", "pbi": "05", "status": "aguardando-po", "opened_at_seq": 20},
        ]))
        self.state("seq", "15")
        self.assertEqual(self._escreve_gate().returncode, 2)

    def test_sem_gate_aberto_escrita_livre(self):
        self.assertEqual(self._escreve_gate().returncode, 0)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python3 -m unittest discover -s plugins/spec-gate/tests -t . -v`
Expected: FAIL — os quatro primeiros esperam 2 e recebem 0

- [ ] **Step 3: Implementar**

```python
def guard_gate_clear(tool, tool_input, cwd):
    """Impede o Claude de se auto-liberar escrevendo no gate.json.

    Camada 1 (inforjável): sem turno REAL do PO depois do gate abrir,
    nenhuma decisão pode ser registrada. Conservador de propósito — basta
    um gate aberto sem turno para travar a escrita, porque o hook não tem
    como saber com segurança qual gate a escrita pretende alterar.

    Camada 2 (decisão por gate) é responsabilidade do comando /spec-gate,
    que registra uma decisão individual por gate sustentada pela fala do PO.
    """
    if not any(_same_file(t, cwd, GATE_REL) for t in write_targets(tool, tool_input)):
        return
    sem_turno = [
        g for g in specgate_state.open_gates(cwd)
        if not specgate_state.has_human_turn_since(cwd, g.get("opened_at_seq", 0))
    ]
    if not sem_turno:
        return
    nomes = ", ".join(str(g.get("checkpoint", "?")) for g in sem_turno)
    block(
        f"[spec-gate] AUTO-LIBERAÇÃO BLOQUEADA ({nomes}). Nenhuma mensagem do "
        "PO chegou desde que este gate abriu, então a decisão dele não existe "
        "e não pode ser registrada. Este bloqueio é o sistema funcionando: "
        "apresente a decisão pendente ao PO e aguarde a resposta real."
    )
```

Ligar em `main()`, logo após `guard_po_gate`:

```python
        guard_gate_clear(tool, tool_input, cwd)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python3 -m unittest discover -s plugins/spec-gate/tests -t . -v`
Expected: PASS, 25 testes

- [ ] **Step 5: Commit**

```bash
git add plugins/spec-gate/scripts/gate_guard.py plugins/spec-gate/tests/test_gate_guard.py
git commit -m "feat(spec-gate): guard_gate_clear impede auto-liberação sem turno do PO"
```

---

### Task 5: Exceção `parked/*` no gate de regressão

Sem isto a mecânica de estacionamento trava no primeiro uso: um PBI estacionado tem suíte vermelha por definição, e o gate bloquearia justamente o commit WIP que preserva o trabalho.

**Files:**
- Modify: `plugins/spec-gate/scripts/gate_guard.py:197-227` (`guard_regression`)
- Modify: `plugins/spec-gate/tests/test_gate_guard.py`

**Interfaces:**
- Produces: `current_branch(cwd) -> str`

- [ ] **Step 1: Escrever os testes que falham**

```python
class ParkedBranchTest(GuardBase):
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python3 -m unittest discover -s plugins/spec-gate/tests -t . -v`
Expected: FAIL em `test_branch_parked_com_suite_vermelha_passa` (recebe 2, espera 0)

- [ ] **Step 3: Implementar**

Adicionar antes de `guard_regression`:

```python
def current_branch(cwd):
    """Branch atual, ou string vazia se não for repo git / git indisponível."""
    try:
        proc = subprocess.run(
            ["git", "branch", "--show-current"], cwd=cwd,
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""
```

Inserir no início de `guard_regression`, logo após o early-return de `--dry-run`:

```python
    # Commit WIP de PBI estacionado: a suíte está vermelha POR DEFINIÇÃO
    # (trabalho incompleto), e este commit existe justamente para preservar
    # esse trabalho. Seguro porque parked/* nunca é branch de entrega — o
    # merge de volta passa pelo gate normal na branch principal.
    if current_branch(cwd).startswith("parked/"):
        return
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python3 -m unittest discover -s plugins/spec-gate/tests -t . -v`
Expected: PASS, 28 testes

- [ ] **Step 5: Commit**

```bash
git add plugins/spec-gate/scripts/gate_guard.py plugins/spec-gate/tests/test_gate_guard.py
git commit -m "fix(spec-gate): gate de regressão libera commit WIP em branch parked/*"
```

---

### Task 6: Janela do freeze + `spec_paths` default

O freeze hoje liga sempre que há fase ativa, o que quebraria as Fases 0 e 1 — justamente quando o `spec-analyst` precisa escrever em `docs/backlog/`.

**Files:**
- Modify: `plugins/spec-gate/scripts/gate_guard.py:49-79` (`guard_spec_lock`) e `main()`
- Modify: `plugins/spec-gate/tests/test_gate_guard.py`

**Interfaces:**
- Consumes: `specgate_state.gate_po_1_passed` (Task 1)

- [ ] **Step 1: Escrever os testes que falham**

```python
class SpecFreezeTest(GuardBase):
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python3 -m unittest discover -s plugins/spec-gate/tests -t . -v`
Expected: FAIL em `test_antes_do_gate_po_1_spec_editavel` (recebe 2) e em `test_spec_md_nao_e_mais_protegido_por_default` (recebe 2)

- [ ] **Step 3: Implementar**

Em `guard_spec_lock`, trocar o default (linha 50):

```python
    spec_paths = cfg.get("spec_paths", ["docs/backlog"])
```

Em `main()`, trocar a condição do freeze. Substituir:

```python
        if phase:
            guard_spec_lock(tool, tool_input, cwd, cfg)
```

por:

```python
        # O freeze precisa de um FATO EM DISCO, não de narrativa: o hook só
        # enxerga arquivos. Antes do Gate PO 1 a spec ainda está sendo
        # escrita pelo spec-analyst e não é contrato; depois dele, é.
        if specgate_state.gate_po_1_passed(cwd):
            guard_spec_lock(tool, tool_input, cwd, cfg)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python3 -m unittest discover -s plugins/spec-gate/tests -t . -v`
Expected: PASS, 31 testes

- [ ] **Step 5: Commit**

```bash
git add plugins/spec-gate/scripts/gate_guard.py plugins/spec-gate/tests/test_gate_guard.py
git commit -m "fix(spec-gate): freeze da spec liga só após o Gate PO 1, via fato em disco"
```

---

### Task 7: Revisão dos 4 gates de 0.1.0 contra o fluxo novo

Tarefa explícita, não vigilância difusa. Das colisões achadas nas auditorias de design, três eram gate existente contra mecânica nova. Os 4 gates de 0.1.0 assumem um fluxo que está sendo reescrito por baixo deles.

**Files:**
- Modify: `plugins/spec-gate/scripts/gate_guard.py` (mensagens desatualizadas)
- Modify: `plugins/spec-gate/tests/test_gate_guard.py`

- [ ] **Step 1: Escrever os testes de regressão dos gates antigos**

```python
class GatesLegadoTest(GuardBase):
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
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `python3 -m unittest discover -s plugins/spec-gate/tests -t . -v`
Expected: FAIL — as duas asserções de mensagem (`SPEC.md` e `modo backlog` ainda presentes)

- [ ] **Step 3: Atualizar as mensagens**

Em `guard_testing_phase`, trocar o texto de `reason`:

```python
    reason = (
        "[spec-gate] Fase de testes black-box ativa: leitura de código-fonte "
        "bloqueada ({alvo}). Escreva os testes apenas a partir da spec do PBI "
        "em docs/backlog/. Se a spec não bastar, registre a lacuna em "
        "'Ambiguidades encontradas' em vez de inspecionar a implementação."
    )
```

Em `guard_destructive`, trocar as duas últimas frases do `block(...)`:

```python
        "Exceção: reverter APENAS os arquivos de um PBI estacionado é "
        "permitido via checkout/restore de caminhos específicos, nunca do "
        "repositório inteiro. Para limpar branch parked/* já mergeada use "
        "'git branch -d' minúsculo, que não é bloqueado."
```

Em `guard_spec_lock`, trocar `o pipeline está em execução` por `o Gate PO 1 já aprovou o backlog`.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `python3 -m unittest discover -s plugins/spec-gate/tests -t . -v`
Expected: PASS, 35 testes

- [ ] **Step 5: Commit**

```bash
git add plugins/spec-gate/scripts/gate_guard.py plugins/spec-gate/tests/test_gate_guard.py
git commit -m "refactor(spec-gate): alinha mensagens dos 4 gates legados ao fluxo 0.2.0"
```

---

### Task 8: Subagent `spec-analyst`

**Files:**
- Create: `plugins/spec-gate/agents/spec-analyst.md`

- [ ] **Step 1: Escrever o agente**

Frontmatter:

```yaml
---
name: spec-analyst
description: Entrevista o PO para gerar o backlog de PBIs e depois refina as specs existentes, caçando ambiguidade e propondo quebra de itens grandes demais. Use na Fase 0 (concepção) e Fase 1 (refinamento) do fluxo spec-gate, antes de qualquer teste ou código.
tools: Read, Write, Edit, Glob, Grep
---
```

Corpo, com estas seções obrigatórias:

1. **Modo concepção (Fase 0)** — entrevista o PO com **uma pergunta fechada por vez**, com opções concretas. Proibido supor. Ao final escreve `docs/backlog/NN-nome.md` numerados na ordem de execução, cada um no formato: Objetivo (uma frase), Comportamentos (numerados, dado X quando Y então Z com valores concretos), Casos de erro, Fora de escopo, Interfaces públicas.

2. **Modo refinamento (Fase 1)** — lê todas as specs de `docs/backlog/` e produz:
   - Lista de ambiguidades como perguntas fechadas de uma linha
   - Avaliação de granularidade por PBI

3. **Gatilho de quebra** — ler `max_behaviors_per_pbi` (default 7) e `max_public_interfaces_per_pbi` (default 1) do `.specgate.json`. Estourou qualquer um: **obrigatório** propor a quebra em PBIs menores, com a numeração sugerida e o que vai em cada um. Registrar honestamente que a contagem é julgamento do próprio agente.

4. **Proibições** — não escrever código, não escrever testes, não ler `source_paths`. O produto é spec e pergunta, nada mais.

5. **Formato do relatório final** — PBIs criados/alterados; `## Ambiguidades encontradas` com perguntas de uma linha; `## Quebras propostas` com o antes/depois; nada de prosa de acompanhamento.

- [ ] **Step 2: Validar o frontmatter**

Run: `python3 -c "import re,sys; t=open('plugins/spec-gate/agents/spec-analyst.md',encoding='utf-8').read(); m=re.match(r'^---\n(.*?)\n---\n', t, re.S); print('OK' if m and 'name: spec-analyst' in m.group(1) else 'FALHOU')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add plugins/spec-gate/agents/spec-analyst.md
git commit -m "feat(spec-gate): subagent spec-analyst para concepção e refinamento"
```

---

### Task 9: Comando único `/spec-gate`

**Files:**
- Create: `plugins/spec-gate/commands/spec-gate.md`
- Delete: `plugins/spec-gate/commands/pipeline.md`, `commands/backlog.md`, `commands/spec.md`
- Delete: `plugins/spec-gate/scripts/run-backlog.sh`

- [ ] **Step 1: Escrever o comando**

Frontmatter:

```yaml
---
description: Fluxo spec-gate. Auto-orientado - lê o estado, diz onde você está e qual decisão está pendente, e retoma dali.
---
```

Corpo, com estas seções obrigatórias:

1. **Orientação primeiro** — antes de qualquer ação, ler `.specgate/gate.json` e `.specgate/batch.json` e reportar ao PO: fase atual, PBI em curso, gates abertos e decisões pendentes. Se houver gate aberto, **apresentar a decisão e parar**; não tentar avançar.

2. **Fase 0 — concepção** (se `docs/backlog/` vazio ou o PO pediu item novo): delegar ao `spec-analyst` em modo concepção.

3. **Fase 1 — refinamento**: delegar ao `spec-analyst` em modo refinamento. Ao terminar, abrir o **Gate PO 1** escrevendo em `.specgate/gate.json`:
   ```json
   [{"checkpoint": "backlog", "status": "aguardando-po", "opened_at_seq": <seq atual>, "questions": ["..."]}]
   ```
   Apresentar perguntas e quebras ao PO. **PARAR.**

4. **Ao receber a resposta do PO** — registrar decisão **individual por gate** (camada 2), atualizar as specs, gravar `{"backlog_aprovado": true}` em `.specgate/batch.json` e montar a fila de PBIs.

5. **Por PBI, em fila** — Fase 2 (`blackbox-tester`, fase `testing`) → Gate PO 2 → Fase 3 (`implementer`, fase `implementing`) → Fase 4 (`spec-reviewer`) → Gate PO 3 → Fase 5 (commit).

6. **Estacionamento** — ambiguidade em qualquer fase: criar `parked/NN-nome`, commit WIP, voltar à branch principal com árvore limpa, abrir gate de ambiguidade, seguir ao próximo PBI. Retomar depois com checkout + merge; limpar com `git branch -d` minúsculo.

7. **Regras invioláveis** — nunca escrever `.specgate/phase` com gate aberto (o hook bloqueia e o bloqueio está certo); nunca registrar decisão sem fala do PO; nunca decidir por ele.

- [ ] **Step 2: Remover os comandos antigos**

```bash
git rm plugins/spec-gate/commands/pipeline.md plugins/spec-gate/commands/backlog.md plugins/spec-gate/commands/spec.md plugins/spec-gate/scripts/run-backlog.sh
```

- [ ] **Step 3: Verificar que nada mais referencia os removidos**

Run: `grep -rn "spec-gate:pipeline\|spec-gate:backlog\|spec-gate:spec\b\|run-backlog" plugins/ vscode-spec-gate-board/ || echo "(limpo)"`
Expected: `(limpo)`

- [ ] **Step 4: Commit**

```bash
git add -A plugins/spec-gate/commands plugins/spec-gate/scripts
git commit -m "feat(spec-gate)!: comando único /spec-gate substitui pipeline/backlog/spec"
```

---

### Task 10: SKILL.md, visualizações, README e bump 0.2.0

**Files:**
- Modify: `plugins/spec-gate/skills/spec-gate/SKILL.md`
- Modify: `plugins/spec-gate/scripts/board.sh`, `scripts/dashboard.html`, `scripts/statusline.sh`
- Modify: `vscode-spec-gate-board/media/board.html`
- Modify: `plugins/spec-gate/README.md`
- Modify: `plugins/spec-gate/.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`

- [ ] **Step 1: SKILL.md — reescrever as regras**

Manter as 3 regras atuais (escalação, término mecânico, testes são contrato) e adicionar a quarta:

> **4. Decisão de PO não se toma sozinho.** Gate aberto em `.specgate/gate.json` significa que existe uma decisão que só o PO pode tomar. Registrar decisão sem fala real dele é bloqueado por hook, e o bloqueio está correto. Apresente a decisão em uma linha com opções concretas e aguarde.

Trocar as referências a `SPEC.md` por `docs/backlog/NN-nome.md`. Trocar a seção de `.specgate/phase` para citar também `gate.json` e `seq`, com o aviso de nunca editá-los para contornar bloqueio.

- [ ] **Step 2: Visualizações — coluna de gate pendente**

Em `board.sh`, após a barra de progresso, imprimir os gates abertos lidos de `.specgate/gate.json`, no mesmo estilo da seção de perguntas já existente. Em `statusline.sh`, acrescentar `· ⛔ N gates` quando houver gates abertos. Em `dashboard.html` e `board.html`, adicionar um bloco de gates pendentes acima da lista de itens — **usar a função `esc()` já existente** nos dois arquivos para todo conteúdo vindo do JSON.

- [ ] **Step 3: README — reescrever**

Remover: divisão A/B, seção de cron, `run-backlog.sh`, `SPEC.md`, `/spec-gate:pipeline`, `/spec-gate:backlog`. Adicionar: o diagrama do caminho do PBI (copiar da spec), a tabela dos dois tipos de gate, a seção de estacionamento com a exceção `parked/*` e a nota do `git branch -d`, e a **ressalva honesta** de que o gatilho de granularidade é semi-mecânico (contagem depende do modelo), sem inflar.

- [ ] **Step 4: Bump de versão nos dois arquivos**

Em `plugin.json` e na entrada `spec-gate` do `marketplace.json`, `version` → `0.2.0`. Atualizar a `description` nos dois para refletir o fluxo gateado pelo PO.

- [ ] **Step 5: Verificar consistência e suíte completa**

Run:
```bash
python3 -m unittest discover -s plugins/spec-gate/tests -t . -v
python3 -c "
import json
mk={p['name']:p['version'] for p in json.load(open('.claude-plugin/marketplace.json'))['plugins']}
pj=json.load(open('plugins/spec-gate/.claude-plugin/plugin.json'))
print('OK' if mk['spec-gate']==pj['version']=='0.2.0' else 'DRIFT')"
grep -rn "SPEC\.md\|welt-plugins\|run-backlog" plugins/spec-gate/ --include=*.md || echo "(docs limpos)"
```
Expected: 35 testes PASS · `OK` · `(docs limpos)`

- [ ] **Step 6: Commit**

```bash
git add -A plugins/spec-gate vscode-spec-gate-board .claude-plugin/marketplace.json
git commit -m "feat(spec-gate)!: fluxo único gateado pelo PO (0.2.0)"
```

---

## Verificação end-to-end

Após a Task 10, num projeto de teste descartável com `.specgate.json`, `docs/backlog/` com 2 PBIs (um ambíguo de propósito) e git inicializado:

1. **Gate trava sem turno humano** — abrir gate e tentar `printf 'x' > .specgate/phase` na mesma volta: exit 2. Após mensagem real do PO: libera.
2. **Auto-liberação impedida** — gate aberto, sem turno do PO, escrever `status: aprovado` no `gate.json`: bloqueado.
3. **Fail-open** — corromper `gate.json` e confirmar exit 0.
4. **Gates legados intactos** — leitura de `source_paths` na fase de testes, `git commit` com suíte vermelha, `rm -rf`, edição de spec após o Gate PO 1.
5. **Ciclo do estacionamento** — o PBI ambíguo estaciona; **o commit WIP em `parked/*` passa com a suíte vermelha**; a working tree volta limpa; o PBI seguinte roda sem contaminação; retomar devolve o trabalho inteiro; `git branch -d` limpa e `-D` é bloqueado.
6. **Inércia** — remover `.specgate.json` e confirmar que nenhum gate dispara.
7. **Resposta parcial** — dois gates abertos, responder apenas o PBI-03: o 03 destrava, **o 05 continua travado**.
8. **`seq` no painel do VS Code** — repetir o cenário 1 pela UI gráfica, provando que `UserPromptSubmit` dispara igual fora do terminal.

## Fora de escopo

**Válvula `auto_approve`.** Deixar a arquitetura pronta para `"auto_approve": ["testes"]` no `.specgate.json`, **sem implementar**. Quando a fadiga do Gate PO 2 aparecer, deve ser uma chave, não uma reforma.
