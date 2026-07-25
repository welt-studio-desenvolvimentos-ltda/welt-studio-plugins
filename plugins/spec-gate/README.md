# spec-gate

Plugin de Claude Code que destila as três ideias de governança que valem a pena do padrão "Scrum de agentes", sem as cerimônias:

1. **Testes black-box**: um subagent escreve os testes lendo apenas o SPEC.md. Um hook bloqueia mecanicamente a leitura do código-fonte durante essa fase, então os testes não conseguem espelhar a implementação.
2. **Término mecânico**: "pronto" só existe quando a suíte completa roda de verdade e passa, com teto de tentativas de correção. Um hook roda a suíte inteira em todo `git commit` e `git merge` e bloqueia se algo falhar.
3. **Escalação obrigatória**: requisito ambíguo vira pergunta ao usuário, nunca código em cima de palpite. O testador devolve as ambiguidades da spec como perguntas, e o pipeline para até serem respondidas.

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

Adicione `.specgate/` ao `.gitignore` do projeto (é estado de runtime).

## Uso

```
/spec-gate conversor de unidades de comprimento na CLI
```

Um único comando, auto-orientado: ele lê `.specgate/gate.json` e `.specgate/batch.json`, reporta em que fase do fluxo você está, qual PBI está em curso e qual decisão está pendente, e retoma dali — nunca é preciso lembrar em que ponto o fluxo parou.

O fluxo tem seis fases, sempre pelo nome (nunca por número): **Concepção** (entrevista o PO e escreve os PBIs em `docs/backlog/`), **Refinamento** (caça ambiguidade e avalia granularidade), **Testes** (subagent `blackbox-tester` escreve os testes só a partir da spec, com leitura de `source_paths` bloqueada por hook), **Implementação** (até a suíte completa passar, com teto de tentativas), **Conformidade** (subagent `spec-reviewer` audita spec contra implementação em contexto isolado) e **Commit** (gate de regressão roda a suíte inteira antes de deixar passar).

Três gates mecânicos pontuam esse fluxo — **gate de backlog** (aprova os PBIs antes de qualquer teste), **gate de testes** (aprova os testes como contrato antes da implementação começar) e **gate de aceite** (aprova a entrega antes do commit) — mais um quarto, o **gate de ambiguidade**, que abre sempre que um PBI é estacionado (branch `parked/NN-nome` com commit WIP) por não ter decisão do PO ainda, liberando o fluxo para seguir aos próximos itens da fila sem perder o trabalho parcial.

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

## Como o bloqueio black-box funciona

Durante a Fase 1, o arquivo `.specgate/phase` contém `testing`. O hook PreToolUse intercepta Read, Grep, Glob e comandos Bash de leitura (cat, grep, sed etc.) e bloqueia qualquer alvo dentro de `source_paths`, devolvendo ao agente a instrução de registrar a lacuna da spec em vez de espiar o código. Fora da fase, nada é bloqueado.

O gate de regressão intercepta `git commit` e `git merge`, roda o `test_command` no diretório do projeto e bloqueia com a saída da falha se a suíte quebrar.

O terceiro gate bloqueia operações destrutivas: `git reset --hard`, `git clean -f`, `git push --force` (o `--force-with-lease` passa), `git branch -D`, `git checkout .`, `git restore .` e `rm -rf`. A mensagem de bloqueio instrui o agente a explicar ao usuário o que seria perdido e pedir confirmação explícita; confirmado, o agente cria `.specgate/allow-destructive` e reexecuta, e a liberação é consumida em uma única execução. Checkout ou restore de arquivos específicos não são bloqueados, só as formas que varrem o repositório inteiro. Desativável com `"block_destructive": false` no `.specgate.json`.

O quarto gate é o congelamento da spec: enquanto qualquer fase do pipeline estiver ativa (`.specgate/phase` não vazio), o `SPEC.md` e `docs/backlog/` ficam somente-leitura para o agente, bloqueando Edit, Write e escritas via Bash (sed -i, tee, redirecionamento, mv/cp/rm). A spec é o contrato que julga o trabalho; quem está sendo julgado não pode alterá-la. Se o agente concluir que a spec está errada, a única saída é parar e apresentar o caso ao usuário. Fora do pipeline, a spec edita normalmente. Caminhos configuráveis via `"spec_paths"` no `.specgate.json` (padrão: `["SPEC.md", "docs/backlog"]`).

Os dois gates falham abertos: qualquer erro interno do script resulta em permitir a ação, para nunca travar sua sessão.

## Notas de design validadas contra a doc oficial

- Subagents distribuídos via plugin ignoram os campos `hooks`, `mcpServers` e `permissionMode` do frontmatter (restrição de segurança do Claude Code). Por isso o bloqueio black-box vive no `hooks/hooks.json` do plugin com o arquivo de fase, e não no frontmatter do agente. Se você copiar `agents/blackbox-tester.md` para `.claude/agents/` do projeto (fora do plugin), pode adicionar o hook no frontmatter e ter o bloqueio por agente em vez de por fase.
- O subagent roda em contexto isolado e recebe apenas seu próprio system prompt mais a mensagem de delegação, o que reforça o black-box: ele nem herda o que o agente principal já leu.
- Quando instalado como plugin, o agente registra com nome escopado `spec-gate:blackbox-tester` e aparece no typeahead de @-mention com esse nome.

## Limites conhecidos

- O bloqueio de leitura via Bash é baseado em parsing do comando; um agente determinado a burlar conseguiria (ex.: python -c). O objetivo é impedir o desvio acidental e barato, que é o caso real, não construir uma sandbox.
- O gate de regressão roda a suíte de forma síncrona dentro do hook; em suítes muito lentas, ajuste `test_timeout_seconds` ou aponte `test_command` para um subconjunto rápido e deixe a suíte completa para o CI.
- Testado com a estrutura de plugins do Claude Code atual (hooks/hooks.json com ${CLAUDE_PLUGIN_ROOT}); confira a versão mínima do seu Claude Code se os hooks não dispararem.
