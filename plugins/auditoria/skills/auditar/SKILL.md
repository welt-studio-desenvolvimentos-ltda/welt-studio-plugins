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

**4. Onde está o trabalho.** O alvo é o **working tree**: o que ainda não foi commitado, staged
ou não. Liste os arquivos tocados e diga que há `git diff` e `git diff --staged` para ele ler.

A auditoria acontece antes do commit — é para isso que ela serve. Se parte do trabalho desta
sessão já foi commitada, ela está fora do alvo por padrão: diga que existe e que ficou de fora,
em uma linha, sem descrevê-la. Só inclua no alvo se o usuário pedir.

Com o working tree limpo e nada por commitar, não invente alvo: diga isso ao usuário e não
despache.

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

## O auditor roda em background — quase sempre

Despache uma vez. No caminho normal o despacho **volta na hora e volta vazio**: o veredito chega
depois, num turno seguinte, como notificação de tarefa concluída. O modo síncrono continua
existindo — com background desligado no ambiente, o veredito volta no próprio resultado do
despacho. Olhe o que voltou antes de decidir em qual dos dois casos você está.

**Se voltou vazio**, não anuncie resultado que você não tem. Nada de prever o veredito, resumir
o que acha que ele vai achar, ou adiantar defesa das suas escolhas. Se o usuário perguntar antes
da hora, diga que a auditoria ainda está rodando.

**O turno em que o veredito chegar é a entrega** — seja o do despacho, seja o da notificação.
Repasse íntegro, como ele veio, sem editar e sem resumir — e sem misturar defesa sua no repasse.
Receber o veredito e seguir sem dizer nada é o mesmo que não ter auditado: o usuário pediu a
auditoria para ler o que ela diz. O que vem depois do repasse, no mesmo turno, é a seção
seguinte.

## Depois de entregar, conserte — mas confira cada achado antes

Entregue primeiro. Só então trabalhe o veredito, no mesmo turno, sem esperar nova ordem.

**Cada achado é uma alegação até você conferir**, exatamente como as suas eram para o auditor.
Ele erra como qualquer um: infere assinatura de comando pelo comando irmão, classifica amostra
pelo rótulo em vez do dado, chega a um número que a verificação não reproduz. Aplicar sem checar
troca o seu erro pelo dele, e o resultado continua errado — só que agora com um veredito
carimbando.

Então, achado por achado:

- **confirme na fonte** — o arquivo apontado, o fonte da dependência, o comando executado;
- **corrija o que se sustentar**, começando pelos BLOQUEADORES;
- **diga o que não se sustentou, com o que você encontrou no lugar.** Um achado derrubado com
  evidência vale tanto quanto um corrigido, e é o que impede a rodada seguinte de recobrar a
  mesma coisa.

Não negocie severidade e não rebata — verificar é diferente de discordar.
Um BLOQUEADOR que você conferiu e confirmou se conserta, ponto; um que a fonte contradiz vira
uma linha dizendo o que a fonte diz.

O escopo é o que o veredito aponta. Melhoria adjacente que você notou pelo caminho continua
sendo outro pedido: liste para o usuário e siga.
