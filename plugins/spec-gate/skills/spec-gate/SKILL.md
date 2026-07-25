---
name: spec-gate
description: Regras de trabalho do pipeline spec-gate. Use sempre que o projeto tiver um arquivo .specgate.json na raiz, ao implementar features, escrever testes, corrigir falhas de teste ou commitar em um projeto que usa spec-gate. Também quando o usuário mencionar spec-gate, testes black-box ou pipeline de spec.
---

# Regras spec-gate

Este projeto usa o pipeline spec-gate: fluxo único, gateado pelo PO, orquestrado pelo comando `/spec-gate`. Quatro regras valem em qualquer tarefa, mesmo fora desse comando:

## 1. Escalação em vez de palpite

Se um requisito estiver ambíguo, incompleto ou contraditório, pare e pergunte ao PO com uma pergunta objetiva de uma linha, oferecendo opções concretas quando possível. Nunca implemente em cima de uma suposição silenciosa. Código escrito com confiança sobre um palpite não sinalizado é o pior modo de falha possível, porque parece certo até quebrar em produção.

## 2. Término mecânico, nunca autodeclarado

Uma tarefa de código só está pronta quando a suíte de testes completa foi executada DE VERDADE nesta sessão e passou, com a saída mostrada. Dizer "pronto" sem rodar não conta. Se após `max_fix_attempts` rodadas de correção (padrão 5, configurável em `.specgate.json`) a suíte ainda falhar, pare e reporte o estado ao PO em vez de continuar tentando.

## 3. Testes são contrato, não obstáculo

Testes derivados da spec do PBI (fase Testes do fluxo) não podem ser editados, enfraquecidos ou deletados para "fazer passar". Divergência entre teste e implementação é uma decisão do PO: ou a spec muda, ou a implementação muda. Apresente o conflito, não o resolva sozinho.

## 4. Decisão de PO não se toma sozinho

Gate aberto em `.specgate/gate.json` (`status: "aguardando-po"`) significa que existe uma decisão que só o PO pode tomar — de backlog, de testes, de aceite ou de ambiguidade. Registrar essa decisão sem uma fala real dele é bloqueado por hook, e o bloqueio está correto: nenhum turno humano aconteceu para validar. Apresente a decisão pendente em uma linha, com opções concretas, e aguarde a resposta real. Não tente contornar por outro caminho (outra chamada de Bash, outro interpretador, heredoc) — isso é o sistema funcionando, não um obstáculo.

## Arquivos do sistema

- `docs/backlog/NN-nome.md`: spec de cada PBI (item de backlog); é o contrato de comportamento observável a partir do qual os testes black-box são derivados. `SPEC.md` não existe mais no fluxo atual. Depois do gate de backlog aprovado, `docs/backlog/` fica congelado para o agente — a promessa é "não altera o contrato por conta própria", não "nunca escreve": há uma janela estreita, amarrada ao turno do PO, que abre só a spec de UM PBI quando o gate de ambiguidade dele está `respondido` com turno humano real depois da abertura (retomada de estacionamento), e fecha quando a fase avança de novo ou o gate ganha rodada nova.
- `.specgate.json`: configuração (`test_command`, `source_paths`, `spec_paths`, `max_fix_attempts`, `max_behaviors_per_pbi`, `max_public_interfaces_per_pbi`, entre outras).
- `.specgate/phase`: quando contém `testing`, hooks bloqueiam leitura de `source_paths` para garantir testes black-box; quando contém `implementing`, o teto de `max_fix_attempts` está em jogo. Nunca edite este arquivo fora do fluxo do comando `/spec-gate`, e nunca o use para contornar um bloqueio — com qualquer gate `aguardando-po` aberto, a escrita fica travada de propósito.
- `.specgate/gate.json`: os gates de PO (backlog, testes, aceite, ambiguidade), cada um com `checkpoint`, `pbi`, `rodada`, `status` e `opened_at_seq`. Sempre reescrito por inteiro (Write do array completo), nunca por Edit ou Bash. Um gate decidido é registro imutável; nunca tente reabri-lo reescrevendo por cima.
- `.specgate/seq`: contador de turnos do PO, incrementado só pelo hook `log_event.py` no evento `UserPromptSubmit`. Nunca escreva este arquivo por nenhum caminho — é dele que depende a prova de que o PO falou de verdade.
- `.specgate/batch.json`: a fila de PBIs do lote corrente, com status, tentativas e commit de cada item, mais o campo `backlog_aprovado` que liga o congelamento de `docs/backlog/`.
- `.specgate/events.jsonl`: log de eventos da sessão (hooks, subagents), consumido pelo painel do VS Code.

Se `.specgate/phase` ficar preso por um erro anterior e bloquear trabalho legítimo, informe o PO e peça confirmação antes de limpar com `printf '' > .specgate/phase` — e só se não houver gate aberto (se houver, a escrita é bloqueada mesmo assim, e o bloqueio está certo).
