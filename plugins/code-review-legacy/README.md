# code-review-legacy

As receitas do `/code-review` com fan-out de subagentes em **todos** os níveis. São as células do Sonnet 5 no Claude Code 2.1.278, executadas pelo mesmo caminho que o embutido usa.

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

## Como roda (igual ao embutido quando ele cai em fork)

O embutido só roda na sessão principal se `CLAUDE_CODE_REPORT_FINDINGS` estiver setado (ou em coordinator mode). Fora disso ele roda em fork, e esta skill reproduz esse caminho:

1. `context: fork`: a skill roda num subagente `general-purpose` em background. Esse subagente **não** recebe a conversa, só o prompt da skill.
2. O fork dispara os finders e verifiers como subagentes dele. A profundidade máxima padrão é 3.
3. A sessão principal recebe **só a última mensagem** do fork, pela notificação da task. Por isso a saída é um JSON em texto e não usa `ReportFindings`.
4. `/code-review-legacy` é disparado direto pelo comando digitado, como o embutido: o resultado chega só pela notificação da task.

O prompt é montado na hora por `scripts/build_prompt.py`, injetado pelo `` !`…` `` do `SKILL.md`, portando o `ps()` do embutido:

- `--fix` e `--comment` entram **só** quando passados.
- `--comment` usa GitLab (`glab`) quando o alvo é uma URL de MR, é `!N` ou o `origin` é GitLab; nos outros casos usa GitHub.
- `--post` só vale para o ultra: é ignorado, com um aviso de uma linha.
- Em high, xhigh e max entra a dica de quantos finders disparar, `ceil(linhas do diff / 150)`, entre 2 e 8.

O texto de cada trecho fica em `recipe/`, copiado literalmente do binário. Quem decide o que entra é o script.

## Limitações conhecidas

- Não há variante "sem a ferramenta `Agent`". Se a skill for chamada perto do limite de profundidade, vale a frase de fallback da receita: o fork executa os ângulos sozinho, em sequência.
- Os argumentos entram **crus** no comando `!`, entre aspas simples. O Claude Code só neutraliza `!` (`uw()`): um `!` em início de palavra chega ao script como `\!`, e o script desfaz isso, então o atalho `!N` de MR do GitLab funciona. Dois caracteres continuam quebrando a skill:
  - Uma aspa simples (`'`) fecha as aspas e o que vier depois vira shell. Quem segura isso é a checagem de permissão que o Claude Code aplica a todo comando `!` (ver "Permission checks on injected commands" na doc de skills). O `allowed-tools` pré-aprova só `python3 …/build_prompt.py`; um trecho encadeado não casa com essa regra e segue o modo de permissão da sessão:
    - **fora do modo auto**, qualquer resultado que não seja `allow` aborta a skill (`Shell command permission check failed…`);
    - **no modo auto**, a skill não aborta: carrega com a instrução para o modelo rodar o comando, e essa chamada passa pelas checagens normais do modo auto. O plugin não contorna o modo auto; ele só pré-aprova o próprio script.
  - Uma crase (`` ` ``) encerra o próprio comando `!`, que o Claude Code extrai até a primeira crase. O que sobra é uma aspa simples sem par, e o shell recusa. Por isso ``/code-review-legacy high `main` `` falha, embora o script saiba tirar as crases do alvo quando elas chegam até ele.

  Resultado: argumento com `'` ou `` ` `` faz a skill falhar (ou, no modo auto, cair na checagem do classificador) em vez de revisar.
- Se ainda existirem skills com o mesmo nome em `~/.claude/skills/`, elas têm precedência sobre as do plugin.

## Testes

```bash
python3 -m unittest discover -s plugins/code-review-legacy/tests
```
