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

## O nível escolhe receita e esforço, e por isso há um repasse

No embutido, o nível tem dois papéis: escolhe a receita e define o esforço de raciocínio do fork (`getEffort()`). Numa skill de plugin o esforço só vem do `effort:` fixo do frontmatter; `getEffort()` é exclusivo do comando embutido. Então:

```
/code-review-legacy high --fix 42      skill visível, roda inline
   └─ scripts/route.py: nível=high, args="--fix 42", grava o último nível
   └─ a sessão principal chama a ferramenta Skill → code-review-legacy:code-review-legacy-high
        └─ skill oculta (user-invocable: false): context fork, effort high, modelo da sessão
             └─ scripts/build_prompt.py high '--fix 42' → receita, roda em background
```

Custo em relação ao embutido: a instrução curta do roteador e uma chamada da ferramenta Skill entram na sessão principal. O review em si continua isolado no fork.

## Como roda (igual ao embutido quando ele cai em fork)

O embutido só roda na sessão principal se `CLAUDE_CODE_REPORT_FINDINGS` estiver setado (ou em coordinator mode). Fora disso ele roda em fork, e as skills de nível reproduzem esse caminho:

1. `context: fork`: a skill roda num subagente `general-purpose` em background. Esse subagente **não** recebe a conversa, só o prompt da skill.
2. O fork dispara os finders e verifiers como subagentes dele. A profundidade máxima padrão é 3.
3. A sessão principal recebe **só a última mensagem** do fork, pela notificação da task. Por isso a saída é um JSON em texto e não usa `ReportFindings`.
4. `effort: <nível>` faz o fork rodar no esforço do nível, como o `getEffort()` do embutido.

O prompt é montado na hora por `scripts/build_prompt.py`, injetado pelo `` !`…` `` do `SKILL.md`, portando o `ps()` do embutido:

- `--fix` e `--comment` entram **só** quando passados.
- `--comment` usa GitLab (`glab`) quando o alvo é uma URL de MR, é `!N` ou o `origin` é GitLab; nos outros casos usa GitHub.
- `--post` só vale para o ultra: é ignorado, com um aviso de uma linha.
- Em high, xhigh e max entra a dica de quantos finders disparar, `ceil(linhas do diff / 150)`, entre 2 e 8.

O texto de cada trecho fica em `recipe/`, copiado literalmente do binário. Quem decide o que entra é o script.

## Limitações conhecidas

- Não há variante "sem a ferramenta `Agent`". Se a skill for chamada perto do limite de profundidade, vale a frase de fallback da receita: o fork executa os ângulos sozinho, em sequência.
- Os argumentos entram **crus** no comando `!`, entre aspas simples. O Claude Code só neutraliza `!` (`uw()`): um `!` em início de palavra chega ao script como `\!`, e o script desfaz isso, então o atalho `!N` de MR do GitLab funciona. Dois caracteres continuam quebrando a skill:
  - Uma aspa simples (`'`) fecha as aspas e o que vier depois vira shell. O que contém isso é a checagem de permissão: o comando `!` passa pela mesma checagem do Bash (`nf()`), e qualquer resultado que não seja `allow` aborta a skill. Como o `allowed-tools` de cada skill só libera o próprio script (`python3 …/route.py` na visível, `python3 …/build_prompt.py` nas de nível), um trecho encadeado não é aprovado automaticamente.
  - Uma crase (`` ` ``) encerra o próprio comando `!`, que o Claude Code extrai até a primeira crase. O que sobra é uma aspa simples sem par, e o shell recusa. Por isso ``/code-review-legacy high `main` `` falha, embora o script saiba tirar as crases do alvo quando elas chegam até ele.

  Resultado: argumento com `'` ou `` ` `` faz a skill falhar ou pedir permissão, em vez de revisar.
- Se ainda existirem skills com o mesmo nome em `~/.claude/skills/`, elas têm precedência sobre as do plugin.

## Testes

```bash
python3 -m unittest discover -s plugins/code-review-legacy/tests
```
