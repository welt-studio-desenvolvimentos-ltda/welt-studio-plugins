---
name: auditar
description: Passa o trabalho da sessão pelo auditor — auditoria adversarial de evidência e escolhas. Use antes de declarar algo pronto, antes de commit, ou ao receber relatório de outro subagente.
argument-hint: [opcional: foco da auditoria, arquivo ou afirmação a priorizar]
disable-model-invocation: true
---

Despache o subagente `auditor` para auditar o trabalho desta sessão. $ARGUMENTS

**Você monta o envelope, não faz a auditoria.** O protocolo — o que provar, como classificar,
como fechar o veredito — é do agente e já está no system prompt dele. Não repita nada disso
aqui, e não adiante conclusão sua.

O agente começa sem ver esta conversa. Tudo que ele vai saber sobre o que foi pedido e o que
foi feito é o que você escrever no prompt de delegação.

## Monte o prompt com estas quatro seções

**1. O que o usuário pediu.** As mensagens dele nesta conversa, **copiadas literalmente**, na
ordem em que chegaram. Inclua as que chegaram no meio de um turno — são as correções de rota, e
é nelas que aparece "não é isso que eu pedi". Se alguma for muito longa, corte e marque o corte.

**2. O plano aprovado.** Se houver plan file nesta sessão, o caminho absoluto. O agente abre
sozinho.

**3. O que você afirmou.** Suas alegações sobre o trabalho, como você as escreveu: "implementei
X", "os testes passam", "corrigi Y", "não quebra nada". Cada uma é uma afirmação que ele vai
tentar derrubar.

**4. Onde está o trabalho.** Arquivos tocados, e se há diff, staged, ou commit recente.

## Três regras que decidem se a auditoria vale alguma coisa

**Copie, não resuma.** Nas seções 1 e 3 você é escrivão, não narrador. Um resumo seu já é uma
interpretação, e é a sua interpretação que está sendo auditada.

**Não filtre pelo que você acha relevante.** Requisito não atendido e escopo estourado só
aparecem comparando o pedido inteiro contra o diff. O item que você cortaria por parecer
secundário é exatamente o que você não percebeu que deixou passar — se tivesse percebido, teria
tratado.

**Não suavize a seção 3.** A tentação de escrever "cobertura parcial" onde você tinha dito
"testado" existe justamente porque agora alguém vai conferir. Suavizar aqui faz o agente auditar
uma versão que você acabou de inventar, e a auditoria passa a não medir nada.

Despache uma vez. Entregue o veredito ao usuário como ele veio, sem editar e sem responder às
críticas no mesmo turno.
