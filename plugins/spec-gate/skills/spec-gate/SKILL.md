---
name: spec-gate
description: Regras de trabalho do pipeline spec-gate. Use sempre que o projeto tiver um arquivo .specgate.json na raiz, ao implementar features, escrever testes, corrigir falhas de teste ou commitar em um projeto que usa spec-gate. Também quando o usuário mencionar spec-gate, testes black-box ou pipeline de spec.
---

# Regras spec-gate

Este projeto usa o pipeline spec-gate: fluxo único, gateado pelo PO, orquestrado pelo comando `/spec-gate`. Quatro regras valem em qualquer tarefa, mesmo fora desse comando:

## 1. Escalação em vez de palpite

Se um requisito estiver ambíguo, incompleto ou contraditório, pare e pergunte ao PO com uma pergunta objetiva de uma linha, oferecendo opções concretas quando possível. Nunca implemente em cima de uma suposição silenciosa. Código escrito com confiança sobre um palpite não sinalizado é o pior modo de falha possível, porque parece certo até quebrar em produção.

## 2. Término mecânico, nunca autodeclarado

Uma tarefa de código só está pronta quando a suíte de testes completa foi executada DE VERDADE nesta sessão e passou, com a saída mostrada. Dizer "pronto" sem rodar não conta. As duas pontas desse ciclo são verificadas por hook, não por relato: antes de implementar, a suíte precisa estar VERMELHA (prova de RED — testes que já passam não capturam o comportamento da spec); antes do commit, precisa estar verde (gate de regressão). E o teto de `max_fix_attempts` rodadas de correção (padrão 5, configurável em `.specgate.json`) é contado pelo hook em `.specgate/attempts.json`, não por você: no estouro, editar código-fonte é bloqueado, e o que resta é parar e reportar o estado ao PO.

## 3. Testes são contrato, não obstáculo

Testes derivados da spec do PBI (fase Testes do fluxo) não podem ser editados, enfraquecidos ou deletados para "fazer passar". Divergência entre teste e implementação é uma decisão do PO: ou a spec muda, ou a implementação muda. Apresente o conflito, não o resolva sozinho.

## 4. Decisão de PO não se toma sozinho

Gate aberto em `.specgate/gate.json` (`status: "aguardando-po"`) significa que existe uma decisão que só o PO pode tomar — de backlog, de testes, de aceite, de ambiguidade, de RED (a suíte já passa antes de implementar: o comportamento já existe, ou os testes não capturam a spec?) ou de emenda (um requisito de PBI já entregue vai mudar). Registrar essa decisão sem uma fala real dele é bloqueado por hook, e o bloqueio está correto: nenhum turno humano aconteceu para validar. Apresente a decisão pendente em uma linha, com opções concretas, e aguarde a resposta real. Não tente contornar por outro caminho (outra chamada de Bash, outro interpretador, heredoc) — isso é o sistema funcionando, não um obstáculo.

## Arquivos do sistema

- `docs/traceability.json`: matriz requisito → teste → commit, versionada junto com o código. A chave é `<spec sem .md>#<id>` (ex.: `02-conversao#C1`). Escrita pelo `blackbox-tester` e verificada por hook na entrada da implementação: requisito sem entrada bloqueia, teste citado que não existe bloqueia.
- `docs/backlog/NN-nome.md`: spec de cada PBI (item de backlog); é o contrato de comportamento observável a partir do qual os testes black-box são derivados. Cada comportamento e caso de erro carrega um ID estável (`[C1]`, `[E1]`) que nunca é renumerado nem reusado — é ele que amarra requisito, teste e commit. `SPEC.md` não existe mais no fluxo atual. Depois do gate de backlog aprovado, `docs/backlog/` fica congelado para o agente — a promessa é "não altera o contrato por conta própria", não "nunca escreve": há uma janela estreita, amarrada ao turno do PO, que abre só a spec de UM PBI quando o gate de **ambiguidade** (retomada de estacionamento) ou de **emenda** (mudança de requisito num PBI entregue) dele está `respondido` com turno humano real depois da abertura, e fecha quando a fase avança de novo ou o gate ganha rodada nova.
- `.specgate.json`: configuração (`test_command`, `source_paths`, `spec_paths`, `max_fix_attempts`, `max_behaviors_per_pbi`, `max_public_interfaces_per_pbi`, entre outras).
- `.specgate/phase`: escrito sempre com Write (nunca `printf`), no formato `testing` ou `implementing:<caminho da spec>`. Com `testing`, hooks bloqueiam leitura de `source_paths` para garantir testes black-box; a transição para `implementing` dispara a prova de RED, e com ela ativa o teto de `max_fix_attempts` está em jogo. Nunca edite este arquivo fora do fluxo do comando `/spec-gate`, e nunca o use para contornar um bloqueio — com qualquer gate `aguardando-po` aberto, a escrita fica travada de propósito.
- `.specgate/red.json` e `.specgate/attempts.json`: mantidos só pelo hook. Um registra que a suíte foi observada vermelha antes da implementação daquele PBI; o outro conta as execuções da suíte gastas nele. Nunca escreva nenhum dos dois — valem exatamente porque quem escreve não é quem está sendo medido. Leitura é livre, e `cat .specgate/attempts.json` é a resposta certa para "em que tentativa eu estou?".
- `.specgate/gate.json`: os gates de PO (backlog, testes, aceite, ambiguidade), cada um com `checkpoint`, `pbi`, `rodada`, `status` e `opened_at_seq`. Sempre reescrito por inteiro (Write do array completo), nunca por Edit ou Bash. Um gate decidido é registro imutável; nunca tente reabri-lo reescrevendo por cima.
- `.specgate/seq`: contador de turnos do PO, incrementado só pelo hook `log_event.py` no evento `UserPromptSubmit`. Nunca escreva este arquivo por nenhum caminho — é dele que depende a prova de que o PO falou de verdade.
- `.specgate/batch.json`: a fila de PBIs do lote corrente, com status, tentativas e commit de cada item, mais o campo `backlog_aprovado` que liga o congelamento de `docs/backlog/`.
- `.specgate/events.jsonl`: log de eventos da sessão (hooks, subagents), consumido pelo painel do VS Code.

Se `.specgate/phase` ficar preso por um erro anterior e bloquear trabalho legítimo, informe o PO e peça confirmação antes de limpar o arquivo com Write (conteúdo vazio) — e só se não houver gate aberto daquele PBI (se houver, a escrita é bloqueada mesmo assim, e o bloqueio está certo).
