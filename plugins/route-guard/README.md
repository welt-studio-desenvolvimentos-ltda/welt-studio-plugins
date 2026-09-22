# route-guard

Acompanhador de implementação. Quando você aprova um plano no plan mode, o plano vira uma **rota**: passos em ordem, cada um com escopo (quais arquivos pode tocar), critério de pronto (comandos que provam que o passo está feito) e dependências. A partir daí, hooks conferem cada passo mecanicamente:

- **Edição fora do escopo é barrada antes de acontecer.** Vale para Edit/Write/MultiEdit/NotebookEdit e para escrita via Bash.
- **Um passo só conta como feito se o critério passar.** Ao marcar a tarefa `[R<id>]` como concluída, o hook roda os comandos `done_when`. Se algum falhar, a conclusão é recusada com a saída do comando.
- **Parar com passo aberto é barrado** (uma vez por turno; uma pergunta ao usuário libera).
- **O diff é auditado a cada fim de turno.** Arquivo alterado fora da rota, por qualquer caminho, é barrado; se persistir, a rota escala.
- **Três falhas no mesmo passo escalam para você.** Edições ficam barradas até você decidir.

## Por que existe

Instrução escrita ("siga o plano", "não invente") o modelo segue às vezes. Aqui a regra é trava: o hook roda sempre, e quem decide se o passo está pronto é um comando, não a palavra do agente. Pesquisei antes de construir (Lane, claude-code-harness, plan-verify, taskmaster, TDD Guard, Spec Kit, Kiro): nenhum pega o plano aprovado e roda o critério num script como condição de aceite; quase todos deixam um modelo julgar.

## Fluxo

1. **Você aprova o plano** (sai do plan mode). O hook `PostToolUse(ExitPlanMode)` instrui o Claude a escrever a rota e criar uma tarefa por passo, com o assunto `[R<id>] <título>`.
2. **O Claude mostra a rota.** Enquanto ela é rascunho, edições no projeto ficam barradas.
3. **Você roda `/route-guard:approve`.** O comando valida a rota e grava a baseline do repo: `HEAD` e o conteúdo dos arquivos já sujos. O Claude não consegue aprovar sozinho: a skill tem `disable-model-invocation`, e o PreToolUse barra o script quando chamado pelo Bash.
4. **Execução:** o escopo é conferido a cada escrita, o critério a cada conclusão de tarefa e o diff a cada fim de turno.
5. **Fim:** com todos os passos feitos e o diff limpo, a rota fecha e os hooks param de agir.

Comandos (só você dispara):

| Comando | O que faz |
|---|---|
| `/route-guard:approve` | Aprova a rota em rascunho ou retoma uma rota escalada (com a rota emendada, se for o caso) |
| `/route-guard:status` | Mostra passos, escopo, critérios, tentativas e o estado |
| `/route-guard:off` | Abandona a rota; edições deixam de ser conferidas nesta sessão |

Para mudar a rota no meio do caminho, volte ao plan mode e aprove o plano de novo. A rota volta a ser rascunho, e os passos já feitos que continuarem na rota não são refeitos.

## Formato da rota

```json
{
  "steps": [
    {"id": "1", "title": "Parser", "scope": ["src/parser/**", "tests/test_parser.py"],
     "done_when": ["python3 -m unittest tests.test_parser"], "depends_on": []},
    {"id": "2", "title": "Docs", "scope": ["docs/parser.md"],
     "done_when": ["grep -q 'parse(' docs/parser.md"], "depends_on": ["1"]}
  ],
  "generated": ["build/**"]
}
```

- **`scope`:** globs relativos à raiz do repo. `**` atravessa diretórios, e `dir/` casa tudo dentro.
  - Arquivos de passo já concluído continuam editáveis.
  - Arquivos de passo cujas dependências não terminaram ficam barrados até elas terminarem.
- **`done_when`:** comandos rodados na raiz do repo, com timeout de 300 s cada. Critério que sempre passa (`true`, `echo …`) é recusado na validação.
- **`generated`** (opcional): saídas de build e teste do projeto. Caches comuns (`__pycache__`, `node_modules`, `.pytest_cache`, etc.) já são ignorados.

A rota e o estado ficam em `${CLAUDE_PLUGIN_DATA}/routes/<session_id>.json` e `.state.json`.
- O estado é separado e protegido; o Claude não escreve nele.
- A rota fica travada depois de aprovada.

**Limpeza:** quando um plano novo é aprovado, o plugin apaga as rotas velhas.
- Rota encerrada (`closed` ou `abandoned`) há mais de 7 dias: apagada, de qualquer projeto. Ela já não protege nada.
- Rota em andamento (`draft`, `active` ou `escalated`) parada há mais de 30 dias: apagada **só se for do mesmo projeto**. A rota em curso de um projeto nunca é apagada pela sessão de outro.
- A rota da sessão atual nunca é apagada.
- Desinstalar o plugin apaga tudo, a menos que a desinstalação use `--keep-data`.

**Sessões em paralelo:** cada sessão só lê e escreve os próprios arquivos. Quando duas sessões com rota trabalham no mesmo repo, a auditoria de uma ignora o que cabe no escopo da rota da outra; quem confere aquele trabalho é a auditoria da outra sessão.

## Reverter é sempre permitido

Desfazer o que a rota mudou é voltar à baseline, então é permitido em qualquer estado:
- `git restore <arquivo>` e `git checkout -- <arquivo>`;
- `rm` de arquivo não rastreado.

A exceção é arquivo que já estava sujo antes da rota. Esse é trabalho seu, e continua protegido.

## Limitações

- **A detecção de escrita via Bash é heurística.** Ela pega redirecionamento, `tee`, `sed -i`, `mv`, `cp`, `rm`, `touch`, `mkdir`, `dd`, `git restore/checkout/rm/mv/clean`. Um script que escreve arquivo por conta própria passa; a auditoria do diff no fim do turno é a segunda camada que pega isso.
- **Uma sessão sem rota** mexendo no mesmo repo, ao mesmo tempo que uma com rota, aparece para esta como mudança fora da rota: o diff não diz qual sessão escreveu o quê. Para trabalho em paralelo no mesmo repo, use worktrees.
- **Fora de um repo git** não há auditoria de diff, só a checagem de escopo antes da escrita.
- **Arquivos fora do repo** não são conferidos.
- **A rota é por sessão.** `/clear` ou uma sessão nova começa sem rota; `--resume` mantém a mesma sessão.
- **O "uma pergunta libera a parada"** olha se a última mensagem termina com `?`.
- **O Claude Code força a parada depois de 8 bloqueios seguidos do Stop** (`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`). O limite de 3 tentativas por passo fica abaixo disso.
- **Erro interno do plugin é fail-open:** o hook sai com exit 0 e um aviso, sem travar o Claude.

## Testes

```bash
python3 -m unittest discover -s plugins/route-guard/tests
```
