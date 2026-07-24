---
name: spec-gate
description: Regras de trabalho do pipeline spec-gate. Use sempre que o projeto tiver um arquivo .specgate.json na raiz, ao implementar features, escrever testes, corrigir falhas de teste ou commitar em um projeto que usa spec-gate. Também quando o usuário mencionar spec-gate, testes black-box ou pipeline de spec.
---

# Regras spec-gate

Este projeto usa o pipeline spec-gate. Três regras valem em qualquer tarefa, mesmo fora do comando `/spec-gate`:

## 1. Escalação em vez de palpite

Se um requisito estiver ambíguo, incompleto ou contraditório, pare e pergunte ao usuário com uma pergunta objetiva de uma linha, oferecendo opções concretas quando possível. Nunca implemente em cima de uma suposição silenciosa. Código escrito com confiança sobre um palpite não sinalizado é o pior modo de falha possível, porque parece certo até quebrar em produção.

## 2. Término mecânico, nunca autodeclarado

Uma tarefa de código só está pronta quando a suíte de testes completa foi executada DE VERDADE nesta sessão e passou, com a saída mostrada. Dizer "pronto" sem rodar não conta. Se após `max_fix_attempts` rodadas de correção (padrão 5, configurável em `.specgate.json`) a suíte ainda falhar, pare e reporte o estado ao usuário em vez de continuar tentando.

## 3. Testes são contrato, não obstáculo

Testes derivados da spec (Fase 1 do pipeline) não podem ser editados, enfraquecidos ou deletados para "fazer passar". Divergência entre teste e implementação é uma decisão do usuário: ou a spec muda, ou a implementação muda. Apresente o conflito, não o resolva sozinho.

## Arquivos do sistema

- `SPEC.md`: contrato de comportamento observável da feature atual
- `.specgate.json`: configuração (test_command, source_paths, max_fix_attempts)
- `.specgate/phase`: quando contém `testing`, hooks bloqueiam leitura de código-fonte para garantir testes black-box. Nunca edite este arquivo fora do fluxo do pipeline, e nunca o use para contornar um bloqueio.

Se `.specgate/phase` ficar preso em `testing` por um erro anterior e bloquear trabalho legítimo fora da Fase 1, informe o usuário e peça confirmação antes de limpar com `printf '' > .specgate/phase`.
