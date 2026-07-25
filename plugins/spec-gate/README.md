# spec-gate

Você descreve o que quer. O Claude escreve a spec te entrevistando, escreve os testes a partir dela, implementa até passar, audita o resultado e commita.

**Em quatro momentos ele para e te pergunta** — e não avança sem sua resposta. Você é o Product Owner: decide o que é o produto; o Claude decide como construir.

---

## Instalação

Dentro do Claude Code:

```
/plugin marketplace add welt-studio/welt-studio-plugins
/plugin install spec-gate@welt-studio-plugins
```

Precisa de Python 3 no PATH. Funciona em Linux, macOS e WSL2.

> O plugin fica **inerte** até você criar um `.specgate.json` no projeto. Sem ele, nada dispara — instalar não muda nada nos seus outros projetos.

---

## Começando

Abra o Claude Code na pasta do seu projeto e rode:

```
/spec-gate conversor de unidades de comprimento na CLI
```

A partir daí é conversa. O que vai acontecer, na ordem:

### 1. Ele te entrevista

Uma pergunta fechada por vez, com opções concretas:

> *Unidade inválida: erro em stderr com exit 1, ou mensagem em stdout com exit 0?*

Responda. Ele pergunta a próxima. **Ele nunca chuta** — se você não decidiu, ele pergunta de novo ou registra como pendência.

No fim, escreve os itens de backlog em `docs/backlog/01-nome.md`, `02-outro.md`… Cada um é um PBI: uma fatia pequena e entregável.

### 2. 🛑 Ele para e pede sua aprovação do backlog

Você recebe as ambiguidades que sobraram e, se algum PBI ficou grande demais, uma proposta de quebrá-lo em menores.

Responda tudo de uma sentada. **Este é o ponto mais barato de corrigir rumo** — daqui pra frente cada erro custa testes e código.

### 3. Ele escreve os testes

Um subagent lê **só a spec** e escreve os testes. Ele fica mecanicamente impedido de ler seu código-fonte durante essa fase, então os testes não conseguem espelhar a implementação — eles testam o que você pediu, não o que o código faz.

### 4. 🛑 Ele para e pede sua aprovação dos testes

*Os testes capturam mesmo o que você quis dizer?*

Vale ler com atenção: os testes viram o contrato. Se algo aqui está errado, é agora que sai barato.

### 5. Ele implementa e audita

Implementa até a suíte inteira passar. Depois um segundo subagent, em contexto limpo, audita spec contra código — sem receber o relatório de quem implementou, então ele forma a própria opinião.

### 6. 🛑 Ele para e pede seu aceite

Você aceita ou rejeita a entrega. Rejeitou? Volta para implementação e tenta de novo.

### 7. Ele commita

Antes do commit, a suíte inteira roda mais uma vez. Falhou, o commit não sai.

E então começa o próximo PBI.

---

## E se ele travar numa dúvida no meio?

Acontece: uma ambiguidade que ninguém viu aparece só na hora de implementar.

Nesse caso ele **estaciona o PBI** — guarda o trabalho parcial numa branch `parked/01-nome`, deixa a pasta limpa, e abre o **quarto tipo de parada**: a pergunta chega até você.

Quando você responder, ele retoma de onde parou, com o trabalho intacto.

> Estacionar **preserva o trabalho**, mas não faz o próximo PBI andar em paralelo. A fila continua parada até você responder — é o mesmo princípio: nada avança sem você.

---

## Retomando depois

Fechou o terminal? A sessão compactou? Rode `/spec-gate` de novo, sem argumento:

```
/spec-gate
```

Ele lê o estado do disco e te diz onde parou e o que está pendente. Você nunca precisa lembrar em que ponto estava.

---

## Acompanhando o progresso

### 📊 O board no navegador

Um painel estilo Kanban que se atualiza sozinho a cada 2 segundos, sem você precisar recarregar nada:

```bash
<plugin-dir>/scripts/dashboard.sh /caminho/do/projeto
```

Sobe um servidor local (porta 8437, só em `127.0.0.1`) e abre o navegador — no WSL2 abre no Windows automaticamente. O que você vê:

- **Barra de progresso** do lote inteiro
- **Cada PBI** com status colorido: `pending`, `running`, `delivered`, `skipped`, `failed` — mais tentativas e hash do commit
- **🛑 Gates aguardando você**, com o PBI e a rodada atual
- **❓ Perguntas acumuladas**, prontas para você responder de uma vez

Deixe aberto num segundo monitor e você acompanha o lote inteiro sem tocar no terminal.

> Adicione `.specgate-dashboard.html` ao `.gitignore` junto com `.specgate/`.

**No VS Code**, a mesma coisa num painel dockável ao lado do editor, sem servidor: a extensão em [`vscode-spec-gate-board/`](../../vscode-spec-gate-board/).

### No terminal

O mesmo quadro, em ANSI, direto na conversa:

```
/spec-gate:board
```

Funciona até fora do Claude Code — é só um script lendo o estado.

### Na barra do Claude Code

Uma linha de resumo permanente. No `.claude/settings.json` do projeto:

```json
{"statusLine": {"type": "command", "command": "bash <plugin-dir>/scripts/statusline.sh"}}
```

Mostra `spec-gate 3/7 ok · 1 pulados · 0 falhas`, e acrescenta `· 1 gate(s) aberto(s)` quando algo espera por você.

> `<plugin-dir>` é a pasta onde o plugin foi instalado.

---

## Configuração

Crie `.specgate.json` na raiz do projeto — ou deixe o `/spec-gate` criar na primeira vez:

```json
{
  "test_command": "pytest -q",
  "source_paths": ["src"]
}
```

Só esses dois importam para começar. Os demais têm padrão razoável:

| Chave | Padrão | Para quê |
|---|---|---|
| `test_command` | — | Como rodar sua suíte |
| `source_paths` | `["src"]` | Onde vive seu código (fica bloqueado na fase de testes) |
| `max_fix_attempts` | `5` | Tentativas de correção antes de parar e te reportar |
| `test_timeout_seconds` | `600` | Teto de tempo da suíte |
| `max_behaviors_per_pbi` | `7` | Acima disso, ele propõe quebrar o PBI |
| `max_public_interfaces_per_pbi` | `1` | Idem, contando interfaces públicas |
| `spec_paths` | `["docs/backlog"]` | O que o congelamento de spec protege |
| `block_destructive` | `true` | Bloqueio de `rm -rf`, `reset --hard` etc. |
| `project_name` | — | Nome exibido no dashboard |

Adicione `.specgate/` ao `.gitignore` — é estado de runtime.

**Para não ser interrompido por permissão a cada comando**, libere no `.claude/settings.json` do projeto só o que o fluxo precisa:

```json
{
  "permissions": {
    "allow": [
      "Bash(python3 -m pytest*)",
      "Bash(git add*)", "Bash(git commit*)", "Bash(git status*)",
      "Bash(git diff*)", "Bash(git log*)", "Bash(git checkout -- *)",
      "Bash(mkdir -p .specgate*)", "Bash(printf*)"
    ]
  }
}
```

Ative o `acceptEdits` com **shift+tab** e evite `--dangerously-skip-permissions`: com as regras acima, o fluxo roda sem abrir mão das proteções.

---

## Referência

<details>
<summary><b>O caminho completo de um PBI</b></summary>

```
você descreve o que quer
         ▼
CONCEPÇÃO                               subagent: spec-analyst
  entrevista, uma pergunta fechada por vez
  → escreve docs/backlog/NN-nome.md
         ▼
REFINAMENTO                             subagent: spec-analyst
  caça ambiguidade · avalia granularidade
         ▼
    🛑 GATE DE BACKLOG
         ▼
   ────── daqui pra baixo, um PBI de cada vez ──────
         ▼
TESTES                                  subagent: blackbox-tester
  🔒 leitura de source_paths bloqueada
         ▼
    🛑 GATE DE TESTES
         ▼
IMPLEMENTAÇÃO                           subagent: implementer
  🔒 spec congelada · teto de tentativas
         ▼
CONFORMIDADE                            subagent: spec-reviewer
  auditoria em contexto limpo
         ▼
    🛑 GATE DE ACEITE
         ▼
COMMIT
  🔒 suíte completa roda antes de deixar passar
         ▼
   próximo PBI

TRANSVERSAIS
  🔒 bloqueio de operações destrutivas
  🛑 GATE DE AMBIGUIDADE — estaciona o PBI e pergunta
```

Dois tipos de gate, com comportamentos diferentes:

| | Quais | O que faz |
|---|---|---|
| 🔒 **Mecânico** | Black-box, regressão, destrutivo, congelamento de spec | Age sozinho — bloqueia ou libera, sem perguntar |
| 🛑 **De PO** | Backlog, testes, aceite, ambiguidade | Para o fluxo e espera **você** |

</details>

<details>
<summary><b>Os gates mecânicos, em detalhe</b></summary>

**Black-box.** Durante a fase de Testes, leitura de `source_paths` é bloqueada (Read, Grep, Glob e comandos Bash de leitura). O agente é instruído a registrar a lacuna da spec em vez de espiar o código.

**Regressão.** Intercepta `git commit` e `git merge`, roda o `test_command` e bloqueia se a suíte quebrar. *Exceção:* commits numa branch `parked/*` não rodam a suíte — trabalho estacionado é incompleto por definição. O merge de volta passa pelo gate normal.

**Destrutivo.** Bloqueia `git reset --hard`, `git clean -f`, `git push --force`, `git branch -D`, `git checkout .`, `git restore .` e `rm -rf`. Para liberar, o agente precisa explicar o que seria perdido e obter sua confirmação. `git branch -d` minúsculo passa livre.

**Congelamento de spec.** Depois que você aprova o backlog, `docs/backlog/` fica congelado. A promessa exata é *"o agente não altera o contrato por conta própria"* — existe uma janela estreita, só para ele transcrever uma decisão sua na retomada de um PBI estacionado, exigindo que sua fala tenha acontecido antes.

**Gates de PO.** Toda transição de fase passa por `.specgate/phase`; com gate aberto, escrever nele é bloqueado. E registrar uma decisão em `gate.json` exige um turno seu posterior à abertura do gate — o contador `.specgate/seq` só avança quando você fala de verdade.

Todos os guards **falham abertos**: erro interno libera a ação, para nunca travar sua sessão. A exceção é o gate de regressão — se a suíte não *puder ser executada*, o commit é bloqueado, porque não conseguir verificar não é o mesmo que estar tudo bem.

</details>

<details>
<summary><b>Estado em <code>.specgate/</code></b></summary>

Tudo aqui é gitignored — estado de runtime, não histórico.

| Arquivo | Conteúdo |
|---|---|
| `phase` | Fase ativa (`testing`, `implementing`, ou vazio) |
| `gate.json` | Os gates, com `checkpoint`, `pbi`, `rodada`, `status`, `opened_at_seq` |
| `batch.json` | Fila de PBIs, com `backlog_aprovado` e status por item |
| `seq` | Contador de turnos seus — só o hook de eventos escreve nele |
| `events.jsonl` | Log da sessão, consumido pelo painel do VS Code |

**Rodada:** cada tentativa de decisão de um gate é uma rodada. Reprovou? A próxima tentativa abre a rodada seguinte, e a anterior fica no histórico. É o que permite rework sem apagar o registro do que foi decidido antes.

</details>

<details>
<summary><b>Honestidade sobre o que os guards são</b></summary>

Nenhum estado em arquivo é uma barreira real contra o Bash do próprio agente — formas contorcidas (`dd`, `eval`, heredoc, indireção por variável) contornam qualquer parsing de comando. Todo guard baseado em inspecionar a chamada é **fricção**, que encarece o desvio *casual* — a ameaça real, um modelo racionalizando um atalho. Não é uma sandbox e não se vende como tal.

A propriedade genuinamente dura é outra: o Claude **não fabrica um turno seu**. O contador `.specgate/seq` só avança dentro do hook de eventos, no `UserPromptSubmit` — nunca por uma ação do agente. Por isso o caminho honesto (esperar você falar) é sempre o de **menor resistência**.

</details>

---

## Limites conhecidos

- **Não existe modo automático.** Um gate que exige sua fala não funciona sem você. Se quiser trabalho não supervisionado, este plugin não é a ferramenta.
- **Bloqueio por parsing é fricção, não sandbox.** Um agente disposto a burlar de propósito consegue. O alvo é o desvio acidental.
- **O gate não lê semântica.** Com dois gates abertos, uma resposta que trata de um satisfaz mecanicamente os dois — distinguir é instrução, não mecânica.
- **A contagem de granularidade é julgamento**, não medição: o limite é comparado mecanicamente, mas quem conta os comportamentos é o modelo.
- **Suítes lentas doem**, porque o gate de regressão roda síncrono. Ajuste `test_timeout_seconds` ou aponte `test_command` para um subconjunto rápido.

---

## Para quem quer o porquê

O design completo, com as decisões e os trade-offs assumidos, está em
[`docs/superpowers/specs/2026-07-24-spec-gate-po-gated-flow-design.md`](../../docs/superpowers/specs/2026-07-24-spec-gate-po-gated-flow-design.md).

O plugin destila ideias do [maul-team](https://github.com/sohei56/maul-team) (framework Scrum de agentes), mantendo o PO gateado em pontos discretos em vez de interrompido difusamente — e cobrindo uma lacuna que o original deixa aberta: como um épico vira PBIs pequenos.
