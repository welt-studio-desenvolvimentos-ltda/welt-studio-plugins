# code-review-legacy

As receitas do `/code-review` com fan-out de subagentes em **todos** os níveis. São as células do Sonnet 5 no Claude Code 2.1.278, executadas pelo mesmo caminho de fork do embutido, sobre um agente-base sem o freio de delegação (ver abaixo). A sessão principal recebe só o relatório final, já com as correções do `--fix`.

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

No embutido, o nível tem dois papéis: escolhe a receita e define o esforço de raciocínio do fork (`getEffort()`). Aqui ele escolhe só a receita. O `getEffort()` que o embutido usa para variar o esforço pelo nível não existe para skills do usuário, então o fork roda com o esforço da sessão.

## O reviewer: `general-purpose` sem o freio de delegação

O fork do embutido roda sobre o agente `general-purpose`, cujo system prompt termina com "You are already the dedicated agent for this task. Do the work directly — do not re-delegate your entire assignment to another single subagent." Num agente que precisa disparar subagentes, isso colide com a receita ("Run 8 independent finder angles via the `Agent` tool"): o modelo tende a revisar sozinho ou a agrupar os ângulos em poucos agentes. Antes da v2.1.218 o `/code-review` rodava inline, na sessão principal, sem esse texto.

Por isso a skill declara `agent: code-review-legacy:reviewer`, e `agents/reviewer.md` é o prompt do `general-purpose` copiado literalmente, **sem** essa última diretriz. Como o `general-purpose`, ele não declara `model:` nem `tools:`: segue `CLAUDE_CODE_SUBAGENT_MODEL` quando setado (senão, o modelo da sessão) e herda todas as ferramentas.

## Como roda (o caminho de fork do embutido)

O embutido só roda na sessão principal se `CLAUDE_CODE_REPORT_FINDINGS` estiver setado (ou em coordinator mode). Fora disso ele roda em fork, e esta skill reproduz esse caminho:

1. `context: fork` + `agent: code-review-legacy:reviewer`: a skill roda num subagente que **não** recebe a conversa, só o prompt da skill. Na CLI interativa o fork roda em background. Numa sessão não interativa (o VS Code conta como uma) ele roda em primeiro plano e trava a sessão até terminar, como o `/code-review` embutido.
2. O fork dispara os finders numa mensagem só, com `run_in_background: false`. Eles rodam em paralelo e o fork espera todos no mesmo turno; depois verifica, aplica o `--fix` se foi pedido e termina.
3. A sessão principal recebe **só o relatório final** (o que foi corrigido e o motivo de cada skip), nunca o que o fork leu nem os subagentes dele. A saída é um JSON em texto, sem `ReportFindings`.

### Por que os finders rodam em primeiro plano

Até a 0.5.0 os finders rodavam em background, o padrão da ferramenta `Agent`, e o fork encerrava o turno para esperá-los. Na CLI o fork é retomado pelos hand-backs e fecha o review. No VS Code o fork em primeiro plano terminava ali: os relatórios dos finders caíam na sessão principal, que verificava e corrigia sozinha. Isso foi visto no transcript de uma sessão real. Com `run_in_background: false` o fork não depende da retomada, e o review fecha dentro dele nos dois ambientes.

A 0.6.0 tentou disparar um agente em background a partir da sessão principal. Funcionou, mas no VS Code o progresso do agente aparecia na sessão, e a leitura do arquivo com a receita pedia permissão fora do modo auto. A 0.7.0 volta ao fork.

O prompt é montado na hora por `scripts/build_prompt.py`, injetado pelo `` !`…` `` do `SKILL.md`, portando o `ps()` do embutido:

- `--fix` e `--comment` entram **só** quando passados.
- `--comment` usa GitLab (`glab`) quando o alvo é uma URL de MR, é `!N` ou o `origin` é GitLab; nos outros casos usa GitHub.
- `--post` só vale para o ultra: é ignorado, com um aviso de uma linha.

O texto de cada trecho fica em `recipe/`, copiado literalmente do binário, com uma exceção (abaixo). Quem decide o que entra é o script.

## Desvios deliberados do binário

- **Um agente por ângulo** (`recipe/one_agent_per_angle.md`): "Run 8 independent finder angles via the `Agent` tool" pede 8 *ângulos*, não 8 agentes, e o modelo agrupa. Medido no mesmo diff: 8 finders numa execução via `claude -p`, ~6 na CLI interativa, 3 no VS Code. A frase extra, logo após o parágrafo da Phase 1, manda exatamente um `Agent` `general-purpose` por ângulo (8 em medium/high, 10 em xhigh/max).
- **Sem a dica de finders**: o `gs()` do embutido acrescenta "Spawn about ⌈linhas/150⌉ finder subagents (min 2, max 8) — … rather than using a fixed large fleet", que contradiz a frase acima. Ela só existe nas células do Sonnet 5; foi removida.
- **Agente-base `reviewer`** (seção acima).
- **Subagentes em primeiro plano** (`recipe/one_agent_per_angle.md`): o fork dispara finders e verifiers com `run_in_background: false`, para receber todos os resultados no mesmo turno (seção "Como roda").

## Limitações conhecidas

- Não há variante "sem a ferramenta `Agent`". Se a skill for chamada perto do limite de profundidade, vale a frase de fallback da receita: o fork executa os ângulos sozinho, em sequência.
- O disparo depende de a sessão principal seguir a instrução de lançar o reviewer. É uma instrução direta e curta, mas é instrução, não trava.
- Os argumentos entram **crus** no comando `!`, entre aspas simples. O Claude Code só neutraliza `!` (`uw()`): um `!` em início de palavra chega ao script como `\!`, e o script desfaz isso, então o atalho `!N` de MR do GitLab funciona. Dois caracteres continuam quebrando a skill:
  - Uma aspa simples (`'`) fecha as aspas e o que vier depois vira shell. Quem segura isso é a checagem de permissão que o Claude Code aplica a todo comando `!` (ver "Permission checks on injected commands" na doc de skills). O `allowed-tools` pré-aprova só `python3 …/build_prompt.py`; um trecho encadeado não casa com essa regra e segue o modo de permissão da sessão:
    - **fora do modo auto**, qualquer resultado que não seja `allow` aborta a skill (`Shell command permission check failed…`);
    - **no modo auto** vale a exceção da doc de skills: a skill carrega e o modelo roda o comando ele mesmo, sujeito ao classificador do modo auto. O plugin só pré-aprova o próprio script.
  - Uma crase (`` ` ``) encerra o próprio comando `!`, que o Claude Code extrai até a primeira crase. O que sobra é uma aspa simples sem par, e o shell recusa. Por isso ``/code-review-legacy high `main` `` falha, embora o script saiba tirar as crases do alvo quando elas chegam até ele.

  Resultado: argumento com `'` ou `` ` `` faz a skill falhar em vez de revisar.
- O `code-review-legacy:reviewer` aparece na lista de agentes de toda sessão com o plugin instalado: o frontmatter de agente não tem campo para escondê-lo, e negar `Agent(code-review-legacy:reviewer)` nas permissões bloquearia também esta skill. Só a `description` pede que ele não seja usado direto; se outro agente o escolher, ganha um `general-purpose` sem o freio de delegação, limitado pela profundidade máxima.
- Se ainda existirem skills com o mesmo nome em `~/.claude/skills/`, elas têm precedência sobre as do plugin.

## Testes

```bash
python3 -m unittest discover -s plugins/code-review-legacy/tests
```
