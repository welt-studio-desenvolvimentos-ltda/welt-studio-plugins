# spec-gate

Plugin de Claude Code que destila as ideias de governança que valem a pena do padrão "Scrum de agentes", mantendo o PO gateado em pontos discretos em vez de interrompido difusamente:

1. **Testes black-box**: um subagent escreve os testes lendo apenas a spec do PBI (`docs/backlog/NN-nome.md`). Um hook bloqueia mecanicamente a leitura do código-fonte durante essa fase, então os testes não conseguem espelhar a implementação.
2. **Término mecânico**: "pronto" só existe quando a suíte completa roda de verdade e passa, com teto de tentativas de correção. Um hook roda a suíte inteira em todo `git commit` e `git merge` e bloqueia se algo falhar.
3. **Escalação obrigatória**: requisito ambíguo vira pergunta ao PO, nunca código em cima de palpite. O testador devolve as ambiguidades da spec como perguntas, e o fluxo estaciona o PBI até serem respondidas.
4. **Decisão de PO não se toma sozinho**: quatro gates de PO (backlog, testes, aceite, ambiguidade) param o fluxo inteiro e bloqueiam por hook qualquer tentativa de registrar uma decisão sem uma fala real do PO depois que o gate abriu.

## Instalação

Faz parte do marketplace `welt-studio-plugins`. Dentro do Claude Code:

```bash
/plugin marketplace add welt-studio/welt-studio-plugins
/plugin install spec-gate@welt-studio-plugins
```

Requer Python 3 no PATH (o hook usa `python3`). Funciona em Linux, macOS e WSL2. No Windows nativo, hooks precisam ser reescritos em PowerShell com `shell: powershell` na entrada do hook, conforme a doc oficial; em WSL2 nada precisa mudar.

## Configuração por projeto

O plugin fica completamente inerte até existir um `.specgate.json` na raiz do projeto:

```json
{
  "test_command": "pytest -q",
  "source_paths": ["src"],
  "max_fix_attempts": 5,
  "test_timeout_seconds": 600
}
```

O comando `/spec-gate` cria esse arquivo pra você na primeira vez, se ele ainda não existir.

Chaves adicionais, todas opcionais (default entre parênteses):

| Chave | Default | Para que serve |
|---|---|---|
| `spec_paths` | `["docs/backlog"]` | Diretórios que o congelamento de spec protege (ver "Congelamento de spec" abaixo). `SPEC.md` não faz mais parte do default — o fluxo atual não usa esse arquivo. |
| `max_behaviors_per_pbi` | `7` | Limite de itens em **Comportamentos** de um PBI antes do gatilho de quebra obrigar uma proposta de split. Ver "Granularidade do backlog" abaixo — é **semi-mecânico**, não uma medição exata. |
| `max_public_interfaces_per_pbi` | `1` | Mesmo gatilho, mas contando entradas em **Interfaces públicas**. |
| `block_destructive` | `true` | Desliga o gate de operações destrutivas se definido como `false`. |
| `project_name` | — | Nome exibido no dashboard e (opcionalmente) no painel do VS Code. |

Adicione `.specgate/` ao `.gitignore` do projeto (é estado de runtime).

## Uso

```
/spec-gate conversor de unidades de comprimento na CLI
```

Um único comando, auto-orientado: ele lê `.specgate/gate.json` e `.specgate/batch.json`, reporta em que fase do fluxo você está, qual PBI está em curso e qual decisão está pendente, e retoma dali — nunca é preciso lembrar em que ponto o fluxo parou.

O fluxo tem seis fases, sempre pelo nome (nunca por número): **Concepção** (entrevista o PO e escreve os PBIs em `docs/backlog/`), **Refinamento** (caça ambiguidade e avalia granularidade), **Testes** (subagent `blackbox-tester` escreve os testes só a partir da spec, com leitura de `source_paths` bloqueada por hook), **Implementação** (até a suíte completa passar, com teto de tentativas), **Conformidade** (subagent `spec-reviewer` audita spec contra implementação em contexto isolado) e **Commit** (gate de regressão roda a suíte inteira antes de deixar passar).

Quatro gates de PO pontuam esse fluxo, cada um parando o fluxo inteiro até uma resposta real do PO — **gate de backlog** (aprova os PBIs antes de qualquer teste), **gate de testes** (aprova os testes como contrato antes da implementação começar), **gate de aceite** (aprova a entrega antes do commit) e **gate de ambiguidade**, que abre sempre que um PBI é estacionado (branch `parked/NN-nome` com commit WIP) por não ter decisão do PO ainda. Estacionar **preserva o trabalho e evita perder contexto**, mas **não paraleliza**: o fluxo continua serializado, e só retoma quando o PO responde e a rodada seguinte é aberta. Distintos destes, os gates mecânicos (black-box, regressão, destrutivo) agem sozinhos, sem parar para o PO — ver "O caminho do PBI" abaixo para a distinção completa.

## Orquestração em sessão

O modo é sempre em sessão aberta do Claude Code: ative acceptEdits (shift+tab alterna o modo de permissão) e rode `/spec-gate`. O agente principal atua como orquestrador: não toca em código, delega cada fase aos subagents (`spec-analyst`, `blackbox-tester`, `implementer`, `spec-reviewer`), commita, atualiza `.specgate/batch.json` e `.specgate/gate.json`, e para exatamente nos pontos em que uma decisão do PO está pendente. Como o trabalho pesado acontece nos contextos isolados dos subagents, o orquestrador se mantém leve por muitos itens. O `batch.json` torna a fila retomável: se a sessão cair ou compactar, rode `/spec-gate` de novo e ele continua de onde parou. Para não ser interrompido por prompts de permissão de Bash, libere no `.claude/settings.json` do projeto apenas o que o fluxo precisa, por exemplo:

```json
{
  "permissions": {
    "allow": [
      "Bash(python3 -m pytest*)",
      "Bash(git add*)",
      "Bash(git commit*)",
      "Bash(git status*)",
      "Bash(git diff*)",
      "Bash(git log*)",
      "Bash(git checkout -- *)",
      "Bash(mkdir -p .specgate*)",
      "Bash(printf*)"
    ]
  }
}
```

Evite `--dangerously-skip-permissions`: com as allow rules estreitas acima mais os gates do plugin, o fluxo roda sem abrir mão das proteções.

Não há mais modo headless: um gate que exige turno humano real (`.specgate/seq`, incrementado só no evento `UserPromptSubmit`) não tem como funcionar sem uma sessão com um humano do outro lado.

## Dashboard ao vivo e statusline

Painel no navegador, estilo board de PBIs, lendo o estado do lote em tempo real:

```bash
<plugin-dir>/scripts/dashboard.sh /caminho/do/projeto
```

Sobe um servidor local (porta 8437 por padrão, só em 127.0.0.1), abre o navegador (em WSL2 abre no Windows via wslview/explorer.exe) e mostra: projeto (campo opcional `project_name` no `.specgate.json`), barra de progresso do lote, cada item com status colorido (pending, running, delivered, skipped, failed), tentativas e commit, a seção "Perguntas aguardando o PO" com as ambiguidades acumuladas e a seção "Gates aguardando o PO" lendo `.specgate/gate.json` — só o gate VIGENTE de cada checkpoint+PBI (o de maior rodada; uma rodada anterior já decidida nunca aparece como pendente). Atualiza sozinho a cada 2 segundos lendo `.specgate/batch.json` e `.specgate/gate.json` que o orquestrador mantém. Adicione `.specgate-dashboard.html` ao `.gitignore` junto com `.specgate/`.

Para um resumo permanente dentro do próprio Claude Code, a statusline: no `.claude/settings.json` do projeto,

```json
{"statusLine": {"type": "command", "command": "bash <plugin-dir>/scripts/statusline.sh"}}
```

mostra `spec-gate 3/7 ok · 1 pulados · 0 falhas` na barra da sessão, e acrescenta `· 1 gate(s) aberto(s) (checkpoint/pbi rodada N)` quando há gate vigente aguardando o PO, atualizando conforme o lote avança.

Dentro da própria conversa, três elementos visuais: a todo list nativa do Claude Code (o orquestrador mantém um todo por item, marcando conforme a fila avança, renderizada pela UI com riscado e tudo), o quadro ANSI que o orquestrador imprime após cada item (barra de progresso, itens coloridos por status, seção de perguntas do PO, seção de gates do PO com a rodada corrente), e o comando `/spec-gate:board` para invocar o quadro a qualquer momento. O quadro é desenhado por `scripts/board.sh` lendo `batch.json` e `gate.json`, então funciona até fora do Claude Code, direto no seu terminal.

## O caminho do PBI

```
você descreve o que quer
         ▼
FASE CONCEPÇÃO                          subagent: spec-analyst
  entrevista interativa (uma pergunta fechada por vez)
  → escreve docs/backlog/NN-nome.md
         ▼
FASE REFINAMENTO                        subagent: spec-analyst
  · caça ambiguidade, contradição, lacuna
  · avalia granularidade; gatilho SEMI-mecânico estourado
    OBRIGA a propor quebra em PBIs menores
         ▼
    ⛔ GATE DE BACKLOG
       responde as perguntas + aprova a quebra. Uma sentada.
         ▼
   ─────── daqui pra baixo, UM PBI DE CADA VEZ (em fila) ───────
         ▼
FASE TESTES BLACK-BOX                   subagent: blackbox-tester
  🔒 leitura de source_paths BLOQUEADA
         ▼
    ⛔ GATE DE TESTES
       os testes capturam o que você quis dizer?
         ▼
FASE IMPLEMENTAÇÃO                      subagent: implementer
  🔒 spec congelada · 🔒 teto de max_fix_attempts
         ▼
FASE CONFORMIDADE                       subagent: spec-reviewer
  auditoria adversarial em contexto limpo
  REPROVADO volta pra Implementação
         ▼
    ⛔ GATE DE ACEITE
       aceita ou rejeita a entrega
         ▼
FASE COMMIT
  🔒 regressão: suíte completa roda e bloqueia se falhar
         ▼
   próximo PBI

TRANSVERSAIS
  🔒 destrutivo — reset --hard, rm -rf, push --force
  ⛔ GATE DE AMBIGUIDADE — estaciona o PBI (trabalho PRESERVADO, sem
     perder contexto); fluxo PARA até o PO responder — não paraleliza
```

Dois tipos de gate coexistem, e não têm o mesmo comportamento:

| | O que é | Comportamento |
|---|---|---|
| 🔒 **Mecânico** | Black-box, regressão, destrutivo | Automático, não pergunta nada — só bloqueia ou libera sozinho |
| ⛔ **De PO** | Backlog, testes, aceite, ambiguidade | Para o fluxo inteiro e espera uma resposta real do PO |

## Granularidade do backlog: o gatilho de quebra

Na fase Refinamento, o `spec-analyst` compara cada PBI contra `max_behaviors_per_pbi` (padrão 7, contando itens de **Comportamentos**) e `max_public_interfaces_per_pbi` (padrão 1, contando **Interfaces públicas**). Ultrapassar qualquer um dos dois torna a proposta de quebra em PBIs menores obrigatória, não uma sugestão.

**Ressalva honesta:** este gatilho é ⛔ **semi-mecânico**, não 🔒. A comparação contra o limite é mecânica — um número contra outro número. A **contagem em si é julgamento do modelo**: como um comportamento é redigido (um item que esconde três decisões, ou três itens que poderiam ser um só) muda o resultado. Não é uma medição exata, e o README não vende como tal. O relatório do `spec-analyst` inclui os números usados, para o PO poder discordar da contagem e não só da conclusão.

## Estado em `.specgate/`

Todo arquivo abaixo é gitignored — é estado de runtime, não histórico versionado.

| Arquivo | Conteúdo | Guard que protege |
|---|---|---|
| `phase` | Fase ativa (`testing`, `implementing`, ou vazio) | `guard_po_gate` bloqueia a escrita enquanto houver qualquer gate `aguardando-po` |
| `gate.json` | Array de gates (mecânicos de PO), cada um com `checkpoint`, `pbi`, `rodada`, `status`, `opened_at_seq`, `questions` | `guard_gate_clear` valida toda escrita chave a chave: gate decidido é imutável, deleção de gate aberto é bloqueada, abertura de rodada nova exige a rodada anterior reprovada/respondida |
| `batch.json` | Fila de PBIs do lote, com `backlog_aprovado` e status/tentativas/commit por item | `guard_batch_lock` bloqueia desligar `backlog_aprovado` depois que ele já está `true` |
| `seq` | Contador monotônico de turnos do PO, incrementado só por `log_event.py` no evento `UserPromptSubmit` | `guard_seq_lock` bloqueia qualquer escrita do agente nele, sempre |
| `events.jsonl` | Log de eventos da sessão (hooks, subagents), consumido pelo painel do VS Code | sem guard dedicado — é log, não estado que gate valida |

## Como os guards mecânicos funcionam

**Black-box.** Enquanto `.specgate/phase` contém `testing`, o hook PreToolUse intercepta Read, Grep, Glob e comandos Bash de leitura (cat, grep, sed etc.) e bloqueia qualquer alvo dentro de `source_paths`, devolvendo ao agente a instrução de registrar a lacuna da spec em vez de espiar o código. Fora da fase, nada é bloqueado.

**Regressão.** Intercepta `git commit` e `git merge`, roda o `test_command` no diretório do projeto e bloqueia com a saída da falha se a suíte quebrar. **Exceção:** commits numa branch `parked/*` não rodam a suíte — um PBI estacionado tem trabalho incompleto por definição (ver "Estacionamento" abaixo), e sem esta exceção o commit WIP que preserva esse trabalho seria bloqueado pelo próprio gate.

**Destrutivo.** Bloqueia `git reset --hard`, `git clean -f`, `git push --force` (o `--force-with-lease` passa), `git branch -D`, `git checkout .`, `git restore .` e `rm -rf`. A mensagem de bloqueio instrui o agente a explicar ao PO o que seria perdido e pedir confirmação explícita; confirmado, o agente cria `.specgate/allow-destructive` e reexecuta, e a liberação é consumida em uma única execução. Checkout ou restore de arquivos específicos não são bloqueados, só as formas que varrem o repositório inteiro. Desativável com `"block_destructive": false` no `.specgate.json`. **`git branch -d` minúsculo (limpeza de uma `parked/*` já mergeada) passa livre — só o `-D` maiúsculo é bloqueado, e nesse caso o bloqueio está correto: `-D` numa `parked/*` não mergeada descartaria exatamente o trabalho que o estacionamento existe para preservar.**

**Congelamento de spec.** A partir do momento em que o **gate de backlog** é aprovado (fato registrado em `.specgate/batch.json`, campo `backlog_aprovado: true` — antes disso o `spec-analyst` ainda está escrevendo as specs nas fases Concepção e Refinamento e precisa de acesso), `docs/backlog/` fica congelado para o agente: Edit, Write e escritas via Bash (sed -i, tee, redirecionamento, mv/cp/rm) são bloqueadas. A promessa exata é **"o agente não altera o contrato por conta própria"**, não "o agente nunca escreve na spec" — porque existe uma janela, estreita e controlada, para fechar o próprio ciclo de estacionamento: quando o **gate de ambiguidade** de um PBI está `respondido` e já houve um turno real do PO depois que esse gate abriu, o agente pode escrever, mas só na spec DAQUELE PBI (o arquivo do campo `pbi` do gate, nunca `docs/backlog/` inteiro), e só até a fase avançar de novo ou o gate ganhar uma rodada nova — ver "Janela de retomada da spec" logo abaixo. É transcrição mecânica de uma decisão que o PO já deu, com a fala dele como pré-condição, não uma exceção de conveniência. Fora dessa janela, se o agente concluir que a spec está errada por qualquer outro motivo, a única saída continua sendo parar e apresentar o caso ao PO. Caminhos configuráveis via `spec_paths` (padrão `["docs/backlog"]`).

**Janela de retomada da spec (`guard_spec_lock` + `_janela_retomada_spec_aberta`).** O gate de ambiguidade estaciona um PBI (ver "Estacionamento" abaixo); o PO responde; e a retomada exige atualizar a spec daquele PBI com a decisão antes do merge de volta — sem isso, o próprio congelamento que protege a spec impediria o PO de ver sua decisão registrada, e o ciclo nunca fecharia. A janela abre só quando **todas** estas condições valem para o gate VIGENTE (maior rodada) de uma série `(checkpoint, pbi)`: `checkpoint == "ambiguidade"`, `status == "respondido"`, um turno humano real depois de `opened_at_seq` (o mesmo `has_human_turn_since` que valida qualquer outra decisão de gate — nenhum mecanismo novo), a fase ainda vazia (`.specgate/phase` — a seção "Estacionamento" desativa a fase antes de abrir o gate, e só a reativa depois do merge), e o alvo da escrita é exatamente o arquivo do campo `pbi` daquele gate. Ela fecha sozinha em dois casos: a fase avança de novo (o retomada reativou `.specgate/phase`), ou o gate ganha rodada nova (o PBI achou outra ambiguidade — a rodada vigente vira `aguardando-po` outra vez, e a rodada `respondido` anterior deixa de ser a vigente). Fora dessas condições — outro checkpoint, gate ainda `aguardando-po`, spec de outro PBI, sem turno novo — nenhuma delas abre a janela, e o congelamento normal continua valendo.

**Gate de PO (`guard_po_gate`).** Chokepoint: toda transição de fase passa por uma escrita em `.specgate/phase`. Com qualquer gate `aguardando-po` em `gate.json`, essa escrita é bloqueada — mesmo que o gate pendente seja de um PBI diferente do que se quer tocar agora.

**Validação do gate (`guard_gate_clear`).** Intercepta escritas em `.specgate/gate.json` e valida gate a gate: nenhum é marcado como decidido sem um turno do PO (`seq` atual maior que o `opened_at_seq` registrado na abertura); um gate já decidido é registro imutável; apagar um gate aberto é bloqueado ("apagar não é decidir"); abrir a rodada seguinte de uma série `(checkpoint, pbi)` só é aceito se a rodada anterior estiver registrada com um status que legitima rodada nova (`reprovado` para backlog/testes/aceite, `respondido` para ambiguidade) e com o número exatamente `anterior + 1`. Conteúdo que a escrita não expõe (Edit, ou Bash como `rm`/`truncate`/redirecionamento) cai numa regra conservadora que bloqueia sempre que houver gate aberto.

**Trava do `seq` (`guard_seq_lock`) e trava do `batch` (`guard_batch_lock`).** A primeira bloqueia qualquer escrita do agente reconhecida em `.specgate/seq`, sempre — é o contador que prova que o PO falou. A segunda bloqueia só a escrita que desligaria `backlog_aprovado` depois que ele já está `true`, o que reabriria `docs/backlog/` para edição sem decisão nenhuma do PO.

Todos os guards falham abertos: qualquer erro interno do script resulta em permitir a ação, para nunca travar a sessão.

### A honestidade sobre o que esses guards são

Nenhum estado em arquivo é uma barreira real contra o Bash do próprio agente — formas contorcidas (`dd`, `eval`, heredoc, indireção por variável, `awk 'BEGIN{print > arq}'`) contornam qualquer parsing de comando. Todo guard acima é **fricção**, de altura variável, que encarece o desvio CASUAL — a ameaça real, um modelo racionalizando um atalho — e não pretende ser (nem se vende como) uma sandbox contra um adversário decidido.

A única propriedade genuinamente dura é diferente: o Claude **não fabrica um evento `UserPromptSubmit`**. O contador `.specgate/seq` só avança dentro de `log_event.py`, rodando como hook nesse evento — nunca como tool call do agente. Por isso o caminho honesto (esperar o PO falar) é sempre o de **menor resistência**; forjar exige Bash cada vez mais exótico, que um atalho casual não alcança. É nisso, e só nisso, que o gate de PO se apoia como garantia de verdade; o resto encarece o desvio acidental.

## Estacionamento

Quando uma fase encontra ambiguidade que o PO ainda não decidiu, o PBI é estacionado em vez de travar o fluxo inteiro nele: `git checkout -b parked/NN-nome`, commit WIP (`git commit`, suíte pode estar vermelha — exceção do gate de regressão acima), volta pra branch principal com a árvore limpa, e abre o **gate de ambiguidade**. O trabalho parcial fica preservado na branch, não em stash (frágil, some de vista) nem em worktree (peso morto para uma fila que processa um PBI por vez) — mas isso **preserva o trabalho e evita perder contexto, não paraleliza**: o gate de PO (`guard_po_gate`) bloqueia a escrita de `.specgate/phase` enquanto ele estiver `aguardando-po`, então mesmo uma tentativa de seguir para o próximo PBI esbarra nesse mecanismo. A fila só volta a andar quando o PO responde e a fase é retomada.

Retomar depois da resposta do PO: registre a decisão no gate (`status: "respondido"`, mesmo `opened_at_seq`) — o turno do PO já aconteceu, então a **janela de retomada da spec** (ver "Congelamento de spec" acima) já está aberta —, atualize a spec DAQUELE PBI com a decisão, e só então `git merge parked/NN-nome` de volta seguido de limpeza com **`git branch -d` minúsculo** (só apaga branch já mergeada). O `-D` maiúsculo é bloqueado pelo gate destrutivo, e **o bloqueio está correto nesse caso**: numa `parked/*` ainda não mergeada, `-D` descartaria de forma irreversível o trabalho que o estacionamento existe para preservar.

## Notas de design validadas contra a doc oficial

- Subagents distribuídos via plugin ignoram os campos `hooks`, `mcpServers` e `permissionMode` do frontmatter (restrição de segurança do Claude Code). Por isso o bloqueio black-box vive no `hooks/hooks.json` do plugin com o arquivo de fase, e não no frontmatter do agente. Se você copiar `agents/blackbox-tester.md` para `.claude/agents/` do projeto (fora do plugin), pode adicionar o hook no frontmatter e ter o bloqueio por agente em vez de por fase.
- O subagent roda em contexto isolado e recebe apenas seu próprio system prompt mais a mensagem de delegação, o que reforça o black-box: ele nem herda o que o agente principal já leu.
- Quando instalado como plugin, o agente registra com nome escopado `spec-gate:blackbox-tester` e aparece no typeahead de @-mention com esse nome.

## Limites conhecidos

- Todo bloqueio via parsing de comando Bash é fricção, não parser de shell de verdade: um agente disposto a burlar de propósito consegue (`python -c`, `eval`, `base64 | sh`, indireção por variável). O objetivo é encarecer o desvio acidental e barato, que é o caso real — não construir uma sandbox.
- O gate de regressão roda a suíte de forma síncrona dentro do hook; em suítes muito lentas, ajuste `test_timeout_seconds` ou aponte `test_command` para um subconjunto rápido e deixe a suíte completa para o CI.
- O gate de PO não lê a semântica da fala do PO: com dois gates abertos ao mesmo tempo, um turno que responde só um deles satisfaz a checagem mecânica dos dois igualmente. Distinguir qual gate a mensagem endereça é instrução do comando `/spec-gate` mais honestidade do modelo, não mecânica de hook.
- O gatilho de granularidade (`max_behaviors_per_pbi`, `max_public_interfaces_per_pbi`) dispara mecanicamente contra o limite, mas a contagem que alimenta essa comparação é julgamento do `spec-analyst`, não uma medição exata.
- Não há mais modo headless: um gate que exige turno humano real não tem como funcionar sem uma sessão com um humano do outro lado.
- Testado com a estrutura de plugins do Claude Code atual (hooks/hooks.json com ${CLAUDE_PLUGIN_ROOT}); confira a versão mínima do seu Claude Code se os hooks não dispararem.
