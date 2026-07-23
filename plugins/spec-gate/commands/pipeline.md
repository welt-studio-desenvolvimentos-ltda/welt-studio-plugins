---
description: Roda o pipeline spec-gate - testes black-box a partir da spec, depois implementação até os testes passarem, com gates mecânicos
---

Execute o pipeline spec-gate para: $ARGUMENTS

Pré-condições. Verifique antes de qualquer outra coisa, e pare com uma mensagem clara se faltar algo:
- Existe um SPEC.md aprovado (ou o arquivo de spec que o usuário indicou)
- Existe `.specgate.json` na raiz com `test_command` e `source_paths`
- O repositório git está limpo o suficiente para trabalhar (sem conflito pendente)

## Fase 1 — Testes black-box

1. Ative a fase de testes: `mkdir -p .specgate && printf 'testing' > .specgate/phase`
2. Delegue ao subagent `blackbox-tester` a escrita dos testes a partir da spec. Enquanto a fase estiver ativa, um hook bloqueia mecanicamente qualquer leitura dos `source_paths`, inclusive por você. Isso é intencional.
3. Quando o subagent retornar, desative a fase: `printf '' > .specgate/phase`
4. Se o relatório do subagent contiver "Ambiguidades encontradas", PARE o pipeline aqui. Apresente as perguntas ao usuário, uma por linha, e aguarde as respostas. Depois atualize o SPEC.md com as decisões e reinicie a Fase 1, porque testes escritos sobre spec ambígua não valem nada.

## Fase 2 — Implementação até verde

5. Ative a fase de implementação: `printf 'implementing' > .specgate/phase`. Enquanto QUALQUER fase estiver ativa, o SPEC.md e `docs/backlog/` ficam congelados por hook: se você concluir que a spec está errada, pare e apresente o caso ao usuário em vez de editá-la, só o usuário altera o contrato. Agora implemente (ou corrija) o código para fazer os testes passarem. Leia a implementação livremente, a restrição black-box vale só para quem escreve os testes.
6. Regra de término mecânica: rode a suíte de verdade após cada rodada de mudanças e mostre a saída real. "Pronto" por autodeclaração não existe. A tarefa só termina quando:
   - Todos os testes novos passam
   - A suíte COMPLETA passa (regressão), não só os testes novos
7. Teto de tentativas: use `max_fix_attempts` do `.specgate.json` (padrão 5). Se após esse número de rodadas de correção a suíte ainda falhar, PARE. Reporte ao usuário: quais testes falham, o que você tentou, e qual é sua hipótese do bloqueio. Insistir além do teto só queima contexto e piora o código.
8. REGRA ABSOLUTA: você não pode editar, enfraquecer, pular ou deletar os testes escritos na Fase 1 para fazê-los passar. Se um teste parecer errado, a única saída é apresentar o caso ao usuário: ou a spec estava errada (e o usuário decide corrigi-la), ou o teste interpretou mal a spec (e o usuário autoriza o ajuste). Nunca decida isso sozinho.

## Fase 3 — Revisão de conformidade

9. Com a suíte verde, delegue ao subagent `spec-reviewer` a auditoria de conformidade entre o SPEC.md e a implementação. NÃO passe seu próprio resumo do que foi feito na delegação, passe apenas o caminho da spec e a instrução de auditar; o revisor deve formar a própria visão a partir do código.
10. Se o veredito for REPROVADO, volte à Fase 2 e corrija as divergências críticas apontadas; cada retorno conta uma tentativa no teto. Se houver "Ambiguidades encontradas", PARE e leve as perguntas ao usuário. Divergências menores não bloqueiam, mas devem constar no relatório final ao usuário.

## Fase 4 — Commit gateado

11. Ao commitar, o hook de regressão roda a suíte completa automaticamente e bloqueia o commit se algo falhar. Se o gate bloquear, volte à Fase 2, isso conta como uma tentativa no teto.
12. Desative a fase: `printf '' > .specgate/phase`. Ao final, reporte: testes criados, cobertura dos requisitos da spec, veredito da revisão de conformidade com as divergências menores restantes, tentativas usadas, e qualquer requisito que ficou de fora com o motivo.

Durante todo o pipeline vale a regra de escalação: requisito ambíguo vira pergunta de uma linha ao usuário, nunca código escrito em cima de palpite silencioso.
