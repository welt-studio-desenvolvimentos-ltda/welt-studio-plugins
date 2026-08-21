# auditoria

Plugin de Claude Code que audita quem constrói, de forma adversarial.

O agente `auditor` confronta o trabalho em duas frentes: **evidência** — toda afirmação de
"implementei", "os testes passam", "corrigi", "não quebra nada" é verificada de forma
independente, executando a checagem em vez de repetir a do construtor — e **escolhas** — por
que foi feito assim e não de outro jeito, com a alternativa nomeada concretamente.

O alvo é sempre quem construiu, nunca o usuário. Escopo e prioridade são decisão de produto e
ficam fora do assunto.

## Pré requisitos

- **Claude Code recente** — o agente usa `memory:` e `background:` de subagente, e a skill usa
  `disable-model-invocation`. Tudo aqui foi medido na 2.1.236 e na 2.1.237; não há piso de
  versão testado.
- **`git`** é opcional. Com repositório o agente parte de `git status`, `git diff` e
  `git log`; sem ele, procura o que foi tocado há pouco com `find -newermt` e declara esse
  limite no veredito.

O plugin não tem hooks e não depende de binário externo.

## Instalação

```
/plugin marketplace add welt-studio-desenvolvimentos-ltda/welt-studio-plugins
/plugin install auditoria@welt-studio-plugins
```

Depois decida se quer confirmação antes de cada auditoria. A regra vai no seu
`~/.claude/settings.json`:

```json
{
  "permissions": {
    "ask": ["Agent(subagent_type:*auditor)"]
  }
}
```

Cada rodada custa de 1,9 a 6,1 milhões de tokens faturáveis e de 6 a 10 minutos, e sem a regra o
Claude pode despachar o auditor por conta própria — foi o que aconteceu em 19/08/2026, seis
vezes seguidas.

O grosso desse volume é cache read, cobrado a 0,1× do input, então o custo em dinheiro fica
bem abaixo do que o número de tokens sugere. Estas são as duas execuções do `auditoria:auditor`
registradas até agora, ambas em Opus 5 — o agente usa `model: inherit`, e as duas sessões
estavam em Opus:

| Data | Tempo | Turnos | Tokens faturáveis | Envelope recebido |
|---|---|---|---|---|
| 19/08 | 5m42 | 43 | 1,93 M | 4,8 mil caracteres |
| 21/08 | 10m04 | 80 | 6,13 M | 7,0 mil |

A amostra é pequena demais para prever: a segunda gastou três vezes mais que a primeira com um
envelope de tamanho parecido, porque o que manda na conta é o tamanho do trabalho a auditar, e
não o do prompt. Não há medição em Sonnet nem em Haiku. Trate os números como ordem de grandeza.

Sobre a regra de permissão, o dado que mais pesa veio de uma versão anterior deste agente: um
despacho de teste com uma frase de 39 caracteres — "Teste de invocação do agente" — custou
1,55 M de tokens. É o comportamento projetado, e continua valendo: sem afirmações no prompt, o
`auditor.md` manda reconstruir o alvo pelo diff e auditar assim mesmo. **Um despacho acidental
custa o que custa um pedido.**

**O `*` não é enfeite.** O valor enviado vem qualificado pelo plugin — `auditoria:auditor`, não
`auditor` — e a comparação é literal. Sem o curinga a regra não casa nada e fica inerte: você lê
uma proteção na configuração e não tem nenhuma. Medido nas duas formas.

**O preço:** a regra também alcança o `/auditar`, então você confirma mesmo quando foi você quem
pediu. É um clique a mais por auditoria, e não há como isentar só esse caminho — `allowed-tools`
na skill não vence uma regra `ask`, e foi medido. Quem achar o ruído pior que o risco simplesmente
não põe a regra.

Não troque `ask` por `deny` aqui. Medido: com `deny` no lugar, o próprio `/auditar` para de
funcionar — `Permission to use Agent with subagent_type:*auditor has been denied` —, porque os
dois caminhos passam pelo mesmo gate da tool `Agent`.

## Uso

```
/auditar
/auditar implementei o cache de sessão e os testes passam
```

Sem argumento, o agente reconstrói o alvo sozinho pelo diff e diz no relatório qual alvo
escolheu. Com argumento, audita as afirmações que você entregou.

## Onde isto entra no fluxo

```
trabalho pronto  ->  /auditar  ->  corrigir o veredito  ->  /code-review  ->  commit
```

A auditoria vem **antes** da revisão de código, e as duas não se sobrepõem. O auditor pergunta
"o que foi afirmado é verdade, e é isso que foi pedido?" — afirmação falsa, requisito que ficou
de fora, escopo que entrou sem pedido. A revisão de código pergunta "este código está certo?" —
bug, duplicação, caminho de erro, limpeza. Nenhum dos dois faz a pergunta do outro.

A ordem importa porque o alvo do auditor é o working tree: ele mede o que você construiu, não o
que uma revisão automática consertou depois. Rodar a revisão primeiro faz o auditor conferir
afirmações sobre um código que já mudou, e o que ele acha de errado volta para a revisão de
qualquer forma.

Também dá para despachar por `@auditor`, mas prefira a skill: ela entrega o contexto do que foi
pedido, que o `@auditor` solto não tem. O conjunto de ferramentas que o agente recebe é decidido
pelo harness em tempo de despacho, e o `agents/auditor.md` manda ele conferir o que tem antes de
concluir qualquer coisa — o plugin não promete `LSP` em nenhum dos dois caminhos.

## A auditoria roda em background

O agente declara `background: true`, então o `/auditar` devolve o turno na hora e a auditoria
corre em segundo plano. O veredito chega depois, por notificação, e a `SKILL.md` manda entregá-lo
íntegro no turno em que chegar. Acompanhe e cancele por `/tasks`.

Entregue o veredito, quem construiu passa a consertar sozinho, no mesmo turno — mas conferindo
cada achado na fonte antes de aplicar, e reportando o que não se sustentou. Auditor também erra,
e corrigir no escuro só troca um erro por outro.

Sem esse campo quem decide foreground ou background é o Claude, tarefa a tarefa — e com a skill
pedindo o veredito de volta, ele tende a segurar o turno. Para forçar o modo síncrono de novo,
suba `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` antes de abrir o Claude Code; essa variável vence
o frontmatter. No sentido contrário, com fork mode ligado o Claude Code já roda em background os
subagentes que o Claude despacha, com ou sem o campo.

**A exceção sem escape:** um teammate em processo não despacha agente com `background: true` — o
despacho falha na hora com `In-process teammates cannot spawn background agents`, e a variável
acima não contorna, porque essa checagem vem antes dela. Só aparece em sessão com teammates; do
`/auditar` na sessão principal, nunca.

**Pré-aprove os comandos de verificação.** O auditor prova afirmação executando: teste, lint,
type-check e `git` saem por `Bash` — foram 15 chamadas na auditoria de 19/08. Comando
fora do `allow` vira prompt de permissão, e em background a auditoria fica parada esperando você
responder — o prompt aparece na sessão principal, mas não interrompe o que você está fazendo, e
passa fácil despercebido. Vale somar ao `allow` do projeto os comandos de verificação que a sua
suíte usa.

## O que compõe o plugin

| Componente | Papel |
|---|---|
| `agents/auditor.md` | o protocolo do auditor — frentes, régua de aceite, formato do veredito |
| `skills/auditar/SKILL.md` | a invocação `/auditar` — instrui o modelo principal a montar o envelope e despachar o agente |

## Como o auditor sabe o que foi pedido

Um subagente começa sem ver a conversa: `"Each subagent starts with a fresh, isolated context
window. It doesn't see your conversation history."` Sem saber o que foi pedido, ele julga se o
código funciona, mas não se é o código certo.

Por isso a skill **não** roda em contexto forkado. Ela é lida pelo modelo principal — que viu a
conversa — e o instrui a montar o prompt de delegação com quatro seções: os pedidos do usuário
copiados literalmente, o caminho do plano aprovado, as afirmações de quem construiu, e onde o
trabalho está. O protocolo da auditoria não entra aí; ele já é o system prompt do agente.

A divisão de papéis é o ponto: **a skill diz o que colocar no envelope, o agente diz o que fazer
com o conteúdo.** Se a skill rodasse com `context: fork`, o corpo dela viraria o prompt do
próprio agente, que receberia instruções de invocador endereçadas a outra pessoa.

**O limite conhecido:** quem monta o envelope é a parte auditada. O `agents/auditor.md` trata
isso explicitamente — o agente abre o plano pelo caminho em vez de aceitar a descrição, e reporta
quando os pedidos vierem como resumo em vez de mensagens copiadas. Uma versão anterior lia o
transcript por script para contornar isso; saiu porque exigia manutenção própria, uma suíte de
testes e um casamento frágil com o formato interno do transcript — e porque nada disso impede o
verdadeiro risco, que é o auditado escolher o que conta.

## O que foi medido sobre as regras de permissão

Registrado para ninguém repetir os testes. Medido com `claude -p`, Claude Code 2.1.236:

| Regra | Onde | Efeito |
|---|---|---|
| `Agent(subagent_type:*auditor)` | `ask` | **pergunta** — é a que o README manda usar |
| `Agent(subagent_type:auditor)` | `ask` | inerte — o valor real vem qualificado pelo plugin |
| `Agent(<nome>)` | `ask` | inerte — a forma de *nome* só é documentada para `deny` |
| `Agent(<nome>)` | `deny` | bloqueia |
| `Agent(subagent_type:*auditor)` | `deny` | bloqueia — **inclusive o `/auditar`** |
| `Skill(auditar)` | `ask` | inerte — não existe matcher de skill |
| `allowed-tools` na skill | — | não isenta: `ask` vence `allow` |
| `plugin.json` → `settings` | — | valida e é ignorado em runtime |

Sobre a invocação: `/auditar` funciona como forma curta, medido. A forma qualificada
`/auditoria:auditar` também vale, e é a que aparece no transcript. O `subagent_type` que o
Claude envia é sempre o qualificado — `auditoria:auditor` —, que é a razão do curinga na regra.

Duas armadilhas, e as duas produzem config que se lê como proteção e não protege. A **forma da
regra**: `Agent(NomeDoAgente)` é a forma de nome; `Agent(subagent_type:valor)` é a forma de
parâmetro — `Tool(param:value)`, a mesma de `Agent(model:opus)` — e só esta vale em `ask`. E o
**valor**: a comparação é literal contra o que o Claude envia, que vem como `auditoria:auditor`.

Vale notar que `plugin.json` aceita um campo `settings` inteiro sem reclamar e depois o ignora.
É por isso que a regra é um passo manual: o plugin não consegue embutir a própria proteção.

## Régua de aceite

Só três coisas reprovam: afirmação falsa sobre a entrega, mecanismo que não cumpre o propósito
declarado, e fail-open no que foi vendido como garantia. O resto é classificado como
**CORRIGIR** (defeito real que não invalida a entrega) ou **DÉBITO** (limite assumido) e não
segura o trabalho.

Um veredito que reprova sempre não informa nada, e aprovar não é dizer que está perfeito — é
dizer que o que foi afirmado é verdade e o que foi construído funciona.

## Testes

Não há suíte: o plugin não tem código executável, só uma skill e um agente. O que dá para
verificar é a instalação — `claude plugin validate plugins/auditoria` e
`claude plugin details auditoria@welt-studio-plugins`, que deve listar 1 skill, 1 agente e 0 hooks.
