---
description: Processa um backlog de specs em sequência sem parar o lote, pulando itens ambíguos e entregando um relatório único no final. Modo PO.
---

Processe o backlog de specs do projeto. $ARGUMENTS

Este é o modo lote do spec-gate: o usuário é o Product Owner, escreveu ou aprovou as specs antecipadamente, e NÃO está sentado esperando perguntas. Otimize para terminar o lote com o máximo de itens entregues e um relatório único no final, nunca para interatividade.

## Estrutura do backlog

- As specs vivem em `docs/backlog/` como arquivos `NN-nome.md` (a ordem numérica é a ordem de execução), cada um no mesmo formato do SPEC.md. Se o usuário indicou outro diretório ou lista nos argumentos, use o que ele indicou.
- Se o diretório não existir ou estiver vazio, informe e sugira `/spec-gate:spec` para criar as specs primeiro. Não invente specs.

## Você é o orquestrador, não o executor

No modo lote você NÃO escreve código, NÃO escreve testes e NÃO lê arquivos de implementação. Todo trabalho pesado é delegado a subagents, e seu contexto carrega apenas: o estado do lote, os relatórios resumidos e as decisões de fluxo. Isso é o que permite processar muitos itens sem degradar. Se você se pegar prestes a abrir um arquivo de código, pare: delegue.

## Estado persistente do lote

Antes de começar, crie (ou retome) `.specgate/batch.json`:

```json
{"items": [{"spec": "docs/backlog/01-x.md", "title": "resumo curto do item", "status": "pending", "attempts": 0, "questions": [], "commit": null, "updated": "HH:MM"}]}
```

Preencha `title` a partir da spec, `commit` com o hash ao entregar, `updated` a cada mudança de status, e mude status para `running` ao iniciar um item. Esses campos alimentam o dashboard ao vivo.

## Progresso visível na sessão

Mantenha DUAS visualizações durante o lote:
1. A todo list nativa do Claude Code: no início do lote, crie um todo por item do backlog; marque in_progress ao iniciar cada item e completed ao entregar (item pulado ou falho: marque completed com o prefixo [PULADO] ou [FALHOU] no texto). A lista renderizada é o progresso que o PO acompanha de relance.
2. O quadro: após concluir cada item (qualquer status), rode `bash "${CLAUDE_PLUGIN_ROOT}/scripts/board.sh" .` e mostre a saída intacta, sem resumir. O transcript vira a linha do tempo visual do lote.

Status possíveis: pending, delivered, skipped, failed. Atualize o arquivo IMEDIATAMENTE após concluir cada item, antes de iniciar o próximo. Se o arquivo já existir ao iniciar, o lote anterior foi interrompido: retome do primeiro item pending, informando ao usuário o que já estava feito. Isso torna o lote sobrevivente a interrupção de sessão e a /compact.

## Regras do lote

1. Para cada item pending, na ordem, execute o ciclo delegando cada fase:
   a. Fase de testes: ative `printf 'testing' > .specgate/phase` e delegue ao `blackbox-tester` (passe só o caminho da spec do item). Desative a fase ao retornar.
   b. Fase de implementação: ative `printf 'implementing' > .specgate/phase` e delegue ao `implementer` (passe só o caminho da spec). NÃO passe código nem contexto de itens anteriores.
   c. Fase de revisão: delegue ao `spec-reviewer` (só o caminho da spec, nunca o relatório do implementer).
   d. Verde e APROVADO: commite você mesmo (o gate de regressão roda no commit) e desative a fase com `printf '' > .specgate/phase`. Marque delivered no batch.json.
   Retenha de cada item apenas o resumo de uma linha e as perguntas; descarte os detalhes dos relatórios após registrar no batch.json.

2. AMBIGUIDADE NÃO PARA O LOTE. Se qualquer subagent devolver "Ambiguidades encontradas", ou o implementer retornar BLOQUEADO por conflito teste/spec:
   - NÃO pergunte ao usuário
   - NÃO decida por conta própria e siga em frente
   - Marque o item como PULADO, registre as perguntas, reverta qualquer mudança parcial daquele item (`git checkout` dos arquivos tocados ou descarte do worktree) e passe ao próximo item do backlog
   O aviso de comando destrutivo se aplica: reverter descarta as mudanças não commitadas daquele item; como o item foi pulado justamente por não estar decidido, isso é o comportamento desejado, mas nunca reverta além dos arquivos do item.

3. TETO ESGOTADO TAMBÉM NÃO PARA O LOTE. Item cujo implementer retornou TETO vira failed no batch.json, com a hipótese dele anexada, e o lote segue.

4. Cada item entregue termina com seu próprio commit (o gate de regressão do hook continua valendo). Um item nunca começa em cima de árvore suja do anterior.

5. Se três itens consecutivos forem pulados por ambiguidade, PARE o lote: é sinal de que as specs do backlog compartilham um defeito sistemático e continuar só queima tokens. Reporte o padrão que você observou nas perguntas acumuladas.

## Relatório final (a única interação com o PO)

Ao final do lote, ALÉM de apresentar no chat, escreva o relatório completo em `docs/backlog/REPORT.md` (sobrescrevendo o anterior, o histórico fica no git). Em execução headless esse arquivo é o único canal com o PO, então ele precisa ser autossuficiente. Conteúdo:

- **Entregues**: item, commit, resumo de uma linha, divergências menores da revisão
- **Pulados por ambiguidade**: item e as perguntas acumuladas, agrupadas, prontas para o PO responder de uma vez. Formule cada uma como pergunta fechada com opções, para a resposta caber em uma linha
- **Falharam no teto**: item, testes que falham, hipótese
- Sugestão de próximo passo: tipicamente, o PO responde as perguntas, as specs pulads são atualizadas, e um novo `/spec-gate:backlog` roda só com elas

O objetivo do relatório é que o PO tome todas as decisões pendentes numa sentada e dispare a próxima rodada. Se o relatório exigir investigação para ser entendido, ele falhou.
