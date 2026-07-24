# spec-gate 0.2.0 — fluxo único gateado pelo PO

**Data:** 2026-07-24
**Status:** aprovado (revisado adversarialmente em duas rodadas)
**Origem:** skill `superpowers:brainstorming`

## Contexto

O spec-gate 0.1.0 (commit `79c596e`) nasceu como destilação do
[maul-team](https://github.com/sohei56/maul-team), um framework Scrum de agentes, "sem as
cerimônias". A destilação foi longe demais: ao cortar as cerimônias, cortou junto a **função**
delas — agrupar a atenção do PO em pontos discretos em vez de interrupção difusa.

Hoje o plugin tem **um único** contato com o PO (aprovar o `SPEC.md`) e depois corre sozinho
até o commit. E tem dois fluxos concorrentes: `/spec-gate:pipeline` (interativo, para na
ambiguidade) e `/spec-gate:backlog` (lote, decide sozinho e segue). A regra de escalação
existe só como **texto nos prompts** — não há gate mecânico que impeça o Claude de decidir
pelo PO.

Pedido do PO: fluxo único, guiado, com gate mecânico que impeça o Claude de tomar decisões
que só o PO pode tomar.

### Trade-off invertido conscientemente

Este design **contradiz** a direção anterior de "máximo de automação". O PO mudou o
trade-off com conhecimento de causa: troca automação por controle, ao custo de mais
interrupções. Isso é decisão de produto registrada, não inconsistência.

### O que a pesquisa no maul-team mostrou

- O PO é *"deliberately gated into discrete ceremonies rather than interrupted mid-sprint"* —
  a cerimônia É o mecanismo de agrupar atenção humana.
- *"Sprint boundaries are set by meaningful review checkpoints rather than velocity estimates,
  since AI agents have no stable velocity baseline."*
- Sprint 0 resolve ambiguidade **antes** do trabalho: *"no work without a PBI"*.
- **Lacuna do maul:** não define como épico vira PBI. Sem critério de granularidade, sem regra
  de split — é "human judgment". Este design cobre a lacuna com gatilho semi-mecânico.

## Design

### Dois tipos de gate

| | O que é | Comportamento |
|---|---|---|
| 🔒 **Mecânico** | Os 4 que já existem | Automático, não pergunta nada |
| ⛔ **De PO** | Os 3 novos + ambiguidade | Para o fluxo e espera o humano |

### O caminho

```
você descreve o que quer
         ▼
FASE 0 · CONCEPÇÃO                      subagent: spec-analyst
  entrevista interativa (uma pergunta fechada por vez)
  → escreve docs/backlog/NN-nome.md
         ▼
FASE 1 · REFINAMENTO                    subagent: spec-analyst
  · caça ambiguidade, contradição, lacuna
  · avalia granularidade; gatilho SEMI-mecânico estourado
    OBRIGA a propor quebra em PBIs menores (ver ressalva)
         ▼
    ⛔ GATE PO 1 · BACKLOG
       responde as perguntas + aprova a quebra. Uma sentada.
         ▼
   ─────── daqui pra baixo, UM PBI DE CADA VEZ (em fila) ───────
         ▼
FASE 2 · TESTES BLACK-BOX               subagent: blackbox-tester
  🔒 leitura de source_paths BLOQUEADA
         ▼
    ⛔ GATE PO 2 · TESTES
       os testes capturam o que você quis dizer?
         ▼
FASE 3 · IMPLEMENTAÇÃO                  subagent: implementer
  🔒 spec congelada · 🔒 teto de max_fix_attempts
         ▼
FASE 4 · CONFORMIDADE                   subagent: spec-reviewer
  auditoria adversarial em contexto limpo
  REPROVADO volta pra Fase 3
         ▼
    ⛔ GATE PO 3 · ACEITE
       aceita ou rejeita a entrega
         ▼
FASE 5 · COMMIT
  🔒 regressão: suíte completa roda e bloqueia se falhar
         ▼
   próximo PBI

TRANSVERSAIS
  🔒 destrutivo — reset --hard, rm -rf, push --force
  ⛔ GATE PO 4 · ambiguidade — estaciona o PBI (trabalho PRESERVADO),
     pergunta entra na fila, fluxo segue pro próximo PBI
```

**Custo assumido:** 1 sentada global + 2 paradas por PBI. Com 3 PBIs, 7 toques do PO.

**Sem teto de rodada.** A proteção contra "executar épico de uma vez" é a Fase 1 garantir que
épico não existe. A fatia é o controle.

### Ressalva honesta sobre a granularidade

O gatilho é ⛔ **semi-mecânico**, não 🔒. O disparo é mecânico (o limite está no config e a
obrigação de propor quebra é regra dura), mas a **contagem depende do modelo** —
`max_behaviors_per_pbi` exige que o próprio spec-analyst conte comportamentos. É superior ao
"human judgment" vago do maul-team, porém não deve ser vendido como parede. Documentar assim
no README, sem inflar.

### Isolamento do PBI estacionado

Trabalho parcial solto na working tree **contamina o PBI seguinte**: a suíte roda com código
meio-pronto alheio, o gate de regressão acusa falha que não é do PBI atual, e o commit arrisca
arrastar arquivos do estacionado junto.

**Mecânica:** ao estacionar, criar branch `parked/NN-nome` com um commit WIP e devolver a
working tree limpa. Escolhido sobre stash (frágil, some de vista, empilha em silêncio) e sobre
worktree (peso morto — com PBIs em fila nunca há dois mexendo em arquivo ao mesmo tempo).
Retomar é um checkout, e o estado sobrevive a sessão, `/compact` e reboot.

**Exceção obrigatória no gate de regressão.** Esta escolha cria um conflito com um gate
existente: `guard_regression()` intercepta *qualquer* `git commit` e roda a suíte completa. Um
PBI estacionado tem trabalho incompleto por definição — suíte provavelmente vermelha — então o
gate bloquearia justamente o commit WIP que preserva o trabalho. **Sem esta exceção, a mecânica
de estacionamento trava no primeiro uso real.**

Regra: o gate de regressão **não roda** quando o commit acontece numa branch `parked/*`. O
guard checa com `git branch --show-current` antes de invocar a suíte. A exceção é segura porque
branch `parked` nunca é a de entrega — o merge de volta continua passando pelo gate normal na
branch principal.

**Limpeza da branch usa `git branch -d` minúsculo.** Após o merge de retomada, deletar com `-d`
(só apaga branch já mergeada), nunca `-D`. O maiúsculo é bloqueado pelo gate destrutivo e
**nesse caso o bloqueio está correto**: `-D` numa `parked/*` não mergeada é exatamente o
descarte de trabalho que o gate existe para impedir.

### O gate mecânico de PO

Estado em `.specgate/gate.json`:

```json
{"checkpoint": "testes", "pbi": "docs/backlog/03-x.md",
 "opened_at_seq": 142, "status": "aguardando-po", "questions": []}
```

**Chokepoint:** toda transição de fase já passa por escrita em `.specgate/phase`. Com gate
aberto, o hook bloqueia essa escrita.

### Duas camadas, garantias diferentes

Confundir as duas seria prometer o que o código não faz.

**Camada de fricção — a trava do arquivo de fase.** Baseada em parsing do comando Bash.
Reconhece redirecionamento, `tee`, `sed -i`, `mv`/`cp`/`rm` e invocação de interpretador
inline (`python -c`, `node -e`, `sh -c`, `bash -c` e afins). Isso **encarece o desvio
acidental** — a ameaça real, um modelo sob pressão racionalizando um atalho — mas **não é
sandbox e não pretende ser**: quem quiser burlar, burla. Mesmo limite que o gate black-box
já assumia em 0.1.0.

**Camada forte — a validação do `seq` na escrita do `gate.json`.** Não depende de parsing
nenhum: depende de um turno real do usuário existir no log de eventos. **Nenhum interpretador
contorna essa.** É aqui que mora a garantia dura do gate de PO.

Por isso "fechar o barato" é o nível certo de investimento na camada de fricção — ela protege
o que precisa proteger, e a parede de verdade está em outro lugar.

**Caminho descartado, registrado para o futuro.** Validar a legitimidade da fase no ponto de
consumo (em vez de proteger o arquivo) esbarra no modelo de estacionamento: com um gate aberto
do PBI-03 estacionado, o fluxo precisa continuar livre para o PBI-04, então bloquear
ferramentas de trabalho globalmente enquanto houver gate aberto quebraria a fila. A versão
por-PBI disso exige rastrear qual arquivo pertence a qual PBI — o redesign caro. Se um dia
essa parede for necessária, o caminho é o rastreamento, não mais parsing.

**Duas camadas para liberar:**

1. **Turno humano (inforjável).** O `log_event.py` já registra `UserPromptSubmit`; ganha um
   contador monotônico `seq`. O gate só libera se existir turno do usuário com
   `seq > opened_at_seq`. **O Claude não fabrica uma fala do PO** — está fora do controle dele.
2. **Token.** O Claude registra a decisão no `gate.json`. Distingue "você falou" de "você
   aprovou".

Escrita no próprio `gate.json` é interceptada e validada — senão o Claude se auto-liberaria.

### O que é parede e o que não é

A validação da escrita no `gate.json` divide-se em duas metades com garantias **diferentes**,
e confundi-las seria prometer o que o código não faz.

🔒 **Parede (mecânica).** Ao interceptar a escrita, o hook lê o conteúdo pretendido e valida
gate a gate: **nenhum gate pode ser marcado como decidido sem existir turno humano com `seq`
posterior ao `opened_at_seq` daquele gate**. Isso fecha o buraco da auto-liberação por
completo — o Claude fica fisicamente impedido de aprovar qualquer gate sem o PO ter falado
depois de ele abrir. Quando o conteúdo não está disponível (Edit, Bash), a validação cai numa
regra conservadora que barra a escrita inteira.

⛔ **Semi-mecânica (instrução).** Se o conteúdo da mensagem do PO **sustenta** a decisão
daquele gate específico, o hook não tem como saber. Com dois PBIs estacionados, uma resposta
que trata só do PBI-03 tem `seq` posterior ao `opened_at_seq` do PBI-05 também — então se o
Claude registrar decisão no 05 por conta própria, o hook vê turno válido e deixa passar.
Distinguir "esta mensagem fala do 03 e não do 05" é **semântica, e hook não lê semântica**.

Essa metade depende de instrução mais honestidade do modelo, exatamente como o gatilho de
granularidade. Documentar assim, sem inflar.

**Consequência para a verificação:** o cenário de resposta parcial (07) é **teste de
comportamento do modelo**, não de parede. A parede é testada pelo cenário 02.

## Mudanças

### Novo

- `agents/spec-analyst.md` — entrevista (Fase 0) + refinamento com gatilho de quebra (Fase 1)
- `commands/spec-gate.md` — comando único auto-orientado; lê `gate.json` + `batch.json` e diz
  onde você está e qual a decisão pendente
- `scripts/gate_guard.py` → `guard_po_gate()` e `guard_gate_clear()`
- Chaves novas no `.specgate.json`: gatilho de granularidade (ex.: `max_behaviors_per_pbi`,
  `max_public_interfaces_per_pbi`)

### Alterado

- `scripts/gate_guard.py` — **congelamento de spec**, ajustes acoplados à remoção do `SPEC.md`:
  - `spec_paths` default passa de `["SPEC.md", "docs/backlog"]` para `["docs/backlog"]`
  - **Janela do freeze redefinida.** Hoje congela enquanto `.specgate/phase` não estiver vazio.
    Isso quebraria as Fases 0 e 1, que são justamente quando o `spec-analyst` *precisa* escrever
    em `docs/backlog/`. Nova regra: **congela do Gate PO 1 em diante**, por PBI em fila.
  - **O freeze exige um fato em estado, não em narrativa.** Quem aplica o congelamento é o hook,
    e hook só enxerga arquivos — "o Gate PO 1 já passou" precisa estar **escrito em disco** onde
    o `gate_guard.py` consiga ler (campo em `batch.json` ou histórico no `gate.json`).
  - **Exceção `parked/*` no `guard_regression()`** (ver seção de isolamento).
- `scripts/log_event.py` — contador `seq` monotônico persistido em `.specgate/seq`
- `skills/spec-gate/SKILL.md` — regras do fluxo novo
- `README.md` — reescrito, sem divisão A/B
- Visualizações ganham coluna de gate pendente: `board.sh`, `dashboard.html`, `statusline.sh`,
  e `vscode-spec-gate-board/media/board.html`
- `plugin.json` + `marketplace.json` → **0.2.0**

### Removido

- `commands/pipeline.md`, `commands/backlog.md`, `commands/spec.md` (fundem no `/spec-gate`)
- `scripts/run-backlog.sh` e a seção de cron do README — o plugin é de sessão
- O conceito de `SPEC.md`; tudo vive em `docs/backlog/`

### Preservado intacto

Os 4 gates mecânicos atuais e os 3 subagents existentes (`blackbox-tester`, `implementer`,
`spec-reviewer`).

## Categoria de risco identificada

Das quatro colisões encontradas nas auditorias, três eram **gate existente contra mecânica
nova** e uma era **mecânica nova precisando respeitar gate existente**. Os 4 gates de 0.1.0
assumem um fluxo que está sendo reescrito por baixo deles.

Revisar cada um dos 4 gates contra o fluxo novo deve ser **tarefa explícita** no plano de
implementação, não vigilância difusa.

## Verificação

1. **Gate trava sem turno humano** — abrir gate, tentar `printf 'x' > .specgate/phase` na mesma
   volta; deve bloquear com exit 2. Depois de uma mensagem real do PO, deve liberar.
2. **Auto-liberação impedida (parede)** — com gate aberto e sem turno do PO posterior ao
   `opened_at_seq` dele, tentar marcá-lo como decidido no `gate.json` deve ser bloqueado.
   Este é o teste da garantia mecânica.
3. **Fail-open preservado** — corromper o `gate.json` e confirmar que o hook sai 0.
4. **Gates mecânicos intactos** — reexecutar os cenários do 0.1.0: leitura de `source_paths` na
   fase de testes, `git commit` com suíte vermelha, `rm -rf`, edição de spec durante o pipeline.
5. **Ponta a ponta** — projeto de exemplo com 2 PBIs, um deles ambíguo de propósito: verificar
   que o ambíguo estaciona com trabalho preservado e o fluxo segue pro outro. Provar em seguida
   que **o commit WIP na branch `parked/*` passa** (exceção do gate de regressão funcionando com
   a suíte vermelha), que a working tree volta limpa, que o PBI seguinte roda sem contaminação,
   e que **retomar o estacionado devolve o trabalho inteiro**.
6. **Inércia** — sem `.specgate.json`, nenhum gate dispara.
7. **Resposta parcial com dois gates abertos (comportamento do modelo, não parede)** —
   estacionar PBI-03 e PBI-05, e responder apenas o PBI-03. O esperado é que só o 03 destrave.
   Ambos têm turno humano posterior, então o hook **permitiria** decidir os dois: o que segura
   o 05 aqui é instrução, não mecânica. Este cenário verifica a honestidade do modelo, e uma
   falha nele é defeito de prompt do comando `/spec-gate`, não de hook.
8. **`seq` no painel do VS Code** — provar que `UserPromptSubmit` dispara igual pela UI gráfica,
   não só pelo terminal. É o mesmo engine, mas é onde o PO vai viver.

## Fora de escopo (anotado para o futuro)

**Válvula `auto_approve`.** O Gate PO 2 (aprovar testes) é o candidato a ficar tedioso quando o
PO estiver calibrado — PBI pequeno e bem refinado gera testes aprovados no reflexo. Deixar a
arquitetura pronta para um `"auto_approve": ["testes"]` por projeto no `.specgate.json`, **sem
implementar agora**. Quando a fadiga aparecer, deve ser uma chave, não uma reforma.
