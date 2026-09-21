# code-review-legacy

As receitas do `/code-review` com fan-out de subagentes em **todos** os níveis. São as células do Sonnet 5 no Claude Code 2.1.278, executadas pelo mesmo caminho de fork que o embutido usa, sobre um agente-base sem o freio de delegação (ver abaixo).

## Por que existe

No Opus 5, o `/code-review` embutido troca de receita conforme o nível. medium e high viram uma passada única (`o5-bmin`), xhigh roda 10 ângulos no próprio contexto sem verificação (`o48-xhigh-v1`), e só o max ainda dispara subagentes. Este plugin mantém a receita com finders e verifiers em subagentes, independente do modelo.

Uso: `/code-review-legacy [low|medium|high|xhigh|max] [--fix] [--comment] [<pr#>|<branch>|<path>]`

| nível | receita | teto |
|---|---|---|
| `low` | 1 passada no diff, sem verificação, sem subagentes | 4 |
| `medium` | 8 ângulos em subagentes, verify de 1 voto (precisão) | 8 |
| `high` | 8 ângulos em subagentes, verify de 1 voto (recall) | 10 |
| `xhigh` | 10 ângulos, verify, varredura de lacunas | 15 |
| `max` | igual ao xhigh, com mais intensidade | 15 |

Sem nível, reusa o último que você digitou; sem histórico, usa o esforço da sessão (vazio → `medium`, valor fora da lista → `high`), como o `Dn()` do embutido.

## O nível escolhe a receita; o esforço é o da sessão

No embutido, o nível tem dois papéis: escolhe a receita e define o esforço de raciocínio do fork (`getEffort()`). Aqui ele escolhe só a receita. Numa skill de plugin o esforço só viria de um `effort:` fixo no frontmatter, e o `getEffort()` que o embutido usa para variá-lo pelo nível não existe para skills do usuário. Sem `effort:`, o fork herda o esforço da sessão.

## Base do fork: `general-purpose` sem o freio de delegação

O fork do embutido roda sobre o agente `general-purpose`, cujo system prompt termina com "You are already the dedicated agent for this task. Do the work directly — do not re-delegate your entire assignment to another single subagent." Em fork, isso colide com a receita ("Run 8 independent finder angles via the `Agent` tool"): o modelo tende a revisar sozinho ou a agrupar os ângulos em poucos agentes. Antes da v2.1.218 o `/code-review` rodava inline, na sessão principal, sem esse texto.

Por isso a skill declara `agent: code-review-legacy:reviewer`, e `agents/reviewer.md` é o prompt do `general-purpose` copiado literalmente, **sem** essa última diretriz. Como o `general-purpose`, ele não declara `model:` nem `tools:`: segue `CLAUDE_CODE_SUBAGENT_MODEL` quando setado (senão, o modelo da sessão) e herda todas as ferramentas. A receita continua idêntica à do binário; muda só a base do fork.

## Como roda (como o embutido quando ele cai em fork, exceto pelo agente-base)

O embutido só roda na sessão principal se `CLAUDE_CODE_REPORT_FINDINGS` estiver setado (ou em coordinator mode). Fora disso ele roda em fork, e esta skill reproduz esse caminho:

1. `context: fork` + `agent: code-review-legacy:reviewer`: a skill roda em background num subagente `code-review-legacy:reviewer` (o `general-purpose` sem a diretriz de não re-delegar; o embutido usa o `general-purpose`). Esse subagente **não** recebe a conversa, só o prompt da skill.
2. O fork dispara os finders e verifiers como subagentes dele. A profundidade máxima padrão é 3.
3. A sessão principal recebe **só o relatório final** do fork (pela notificação da task ou pelo hand-back, ver o passo 4), nunca o que ele leu nem os subagentes dele. Por isso a saída é um JSON em texto e não usa `ReportFindings`.
4. `/code-review-legacy` é disparado direto pelo comando digitado, como o embutido. Como o relatório chega depende de o fork ter disparado subagentes:
   - **sem fan-out**, o texto vem dentro da própria notificação da task;
   - **com fan-out, em modo auto**, o fork lança os finders em background e encerra o turno para esperar. Cada filho que termina retoma o fork pelo caminho de mensagem, que liga o `SubagentHandback`. O relatório então chega como mensagem "[Subagent hand-back]" de `@code-review-legacy-code-review-legacy`, e a notificação só aponta para ela.

   O `/code-review` embutido segue o mesmo caminho quando o fork dele dispara subagentes; não é diferença do plugin.

O prompt é montado na hora por `scripts/build_prompt.py`, injetado pelo `` !`…` `` do `SKILL.md`, portando o `ps()` do embutido:

- `--fix` e `--comment` entram **só** quando passados.
- `--comment` usa GitLab (`glab`) quando o alvo é uma URL de MR, é `!N` ou o `origin` é GitLab; nos outros casos usa GitHub.
- `--post` só vale para o ultra: é ignorado, com um aviso de uma linha.

O texto de cada trecho fica em `recipe/`, copiado literalmente do binário, com uma exceção (abaixo). Quem decide o que entra é o script.

## Desvios deliberados do binário

- **Um agente por ângulo** (`recipe/one_agent_per_angle.md`): "Run 8 independent finder angles via the `Agent` tool" pede 8 *ângulos*, não 8 agentes, e o modelo agrupa. Medido no mesmo diff: 8 finders numa execução via `claude -p`, ~6 na CLI interativa, 3 no VS Code. A frase extra, logo após o parágrafo da Phase 1, manda exatamente um `Agent` `general-purpose` por ângulo (8 em medium/high, 10 em xhigh/max).
- **Sem a dica de finders**: o `gs()` do embutido acrescenta "Spawn about ⌈linhas/150⌉ finder subagents (min 2, max 8) — … rather than using a fixed large fleet", que contradiz a frase acima. Ela só existe nas células do Sonnet 5; foi removida.
- **Agente-base `reviewer`** (seção acima).

## Limitações conhecidas

- Não há variante "sem a ferramenta `Agent`". Se a skill for chamada perto do limite de profundidade, vale a frase de fallback da receita: o fork executa os ângulos sozinho, em sequência.
- Os argumentos entram **crus** no comando `!`, entre aspas simples. O Claude Code só neutraliza `!` (`uw()`): um `!` em início de palavra chega ao script como `\!`, e o script desfaz isso, então o atalho `!N` de MR do GitLab funciona. Dois caracteres continuam quebrando a skill:
  - Uma aspa simples (`'`) fecha as aspas e o que vier depois vira shell. Quem segura isso é a checagem de permissão que o Claude Code aplica a todo comando `!` (ver "Permission checks on injected commands" na doc de skills). O `allowed-tools` pré-aprova só `python3 …/build_prompt.py`; um trecho encadeado não casa com essa regra e segue o modo de permissão da sessão:
    - **fora do modo auto**, qualquer resultado que não seja `allow` aborta a skill (`Shell command permission check failed…`);
    - **no modo auto** também aborta: a doc de skills diz que a exceção do modo auto (carregar e deixar o modelo rodar o comando) não vale para "a forked skill that sets `agent`", que é o caso desta. O plugin só pré-aprova o próprio script.
  - Uma crase (`` ` ``) encerra o próprio comando `!`, que o Claude Code extrai até a primeira crase. O que sobra é uma aspa simples sem par, e o shell recusa. Por isso ``/code-review-legacy high `main` `` falha, embora o script saiba tirar as crases do alvo quando elas chegam até ele.

  Resultado: argumento com `'` ou `` ` `` faz a skill falhar em vez de revisar.
- O `code-review-legacy:reviewer` aparece na lista de agentes de toda sessão com o plugin instalado: o frontmatter de agente não tem campo para escondê-lo, e negar `Agent(code-review-legacy:reviewer)` nas permissões bloquearia também o fork desta skill. Só a `description` pede que ele não seja usado direto; se outro agente o escolher, ganha um `general-purpose` sem o freio de delegação, limitado pela profundidade máxima.
- Se ainda existirem skills com o mesmo nome em `~/.claude/skills/`, elas têm precedência sobre as do plugin.

## Testes

```bash
python3 -m unittest discover -s plugins/code-review-legacy/tests
```
