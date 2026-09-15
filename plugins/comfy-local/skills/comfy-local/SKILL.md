---
name: comfy-local
description: Gerar imagem, vídeo, áudio e 3D num ComfyUI local via ferramentas MCP. Use ao montar um grafo node a node, ao criar, editar, validar ou executar workflows do ComfyUI, ao investigar quais nodes, custom nodes e modelos existem na instalação, e ao produzir lotes de variações. Cobre o ciclo completo, da descoberta até a coleta das saídas.
---

# ComfyUI local

As ferramentas `comfy_*` dirigem um ComfyUI rodando nesta máquina. A
vantagem sobre qualquer catálogo genérico é que toda introspecção lê a
instalação viva, com os custom nodes e os modelos que existem de fato aqui.

## Regra zero: chame `comfy_server_info` primeiro

Responde de uma vez se a CLI existe, se o workspace está configurado e se o
servidor está no ar. Sem isso você gasta chamadas descobrindo por tentativa.

Se o servidor estiver fora, `comfy_launch_server` sobe em segundo plano. O
carregamento dos modelos continua depois do retorno, então espere alguns
segundos antes do primeiro job.

## Erros trazem a correção junto

Todo retorno é um envelope JSON. Quando `error` vem preenchido, **leia o
campo `hint` e siga**. Ele diz o que fazer. Não improvise nem tente
variações às cegas.

Dois casos que aparecem sempre:

- servidor fora do ar: suba com `comfy_launch_server`
- classe de node inexistente: `error.details.close_matches` traz o nome
  certo, pegue de lá

## O ciclo

```
descobrir  ->  construir  ->  validar  ->  executar  ->  coletar
```

**Validar nunca é opcional.** `comfy_validate_workflow` confere class_types,
formato de entrada e valores de enum contra a instalação viva sem submeter
nada. Pega node ausente e checkpoint inexistente em segundos, em vez de
você descobrir depois de esperar a geração inteira.

## Descoberta

Antes de escrever qualquer workflow:

- `comfy_search_models` lista o que existe em disco. **Sempre confira os
  nomes de arquivo antes de referenciar checkpoint, LoRA ou VAE.** Citar um
  modelo que não existe é o erro número um em workflow gerado por agente.
  Com `folders_only=true`, lista as pastas e os tipos válidos.
- `comfy_search_nodes` procura node por nome.
- `comfy_list_nodes` procura por forma: `produces="LATENT"`,
  `accepts="CONDITIONING"`. É esta que serve para montar grafo.
- `comfy_node_path` responde "como saio de MODEL até IMAGE aqui".
- `comfy_node_neighbors` caminha pelas conexões a partir de um node conhecido.
- `comfy_show_node` traz o schema completo de uma classe.

## Construir: quatro mecanismos

Escolha pela estrutura do problema, não por hábito.

**Montar o grafo node a node.** `comfy_edit_graph` é o único caminho que constrói
estrutura em vez de mexer em valores: `add_node` acrescenta pela classe, `connect`
liga `<id>.<saída>` em `<id>.<entrada>`, `set_widget` define um valor,
`delete_nodes` remove. Cada chamada devolve em `data.op` a operação aplicada.

Use quando não existe template próximo, ou quando o que precisa mudar é a forma
do grafo e não os parâmetros. O caminho é: `comfy_list_nodes` ou
`comfy_node_path` para descobrir o que liga em quê, `comfy_show_node` para ver os
slots de uma classe, e então as edições.

Duas ferramentas de leitura ajudam no meio: `action: "ls_nodes"` lista id, tipo e
título, e `action: "print"` devolve o grafo como uma linha de código por node,
com as ligações explícitas — é a forma mais rápida de conferir o que você montou
antes de validar.

**A pessoa vê acontecer.** Com a extensão `comfyui-welt-live` instalada, cada
edição aparece no canvas aberto na hora, e o envelope traz um bloco `live` com as
abas alcançadas. Se vier `published: false`, o canvas não acompanhou: leia o
`hint` e, no mínimo, publique o resultado com `comfy_workflow_library`.

**Sequência que se repete vira receita.** `comfy_graph_recipe` com
`action: "capture"` transforma um grafo que funciona no lote de operações que o
reconstrói, com widgets promovidos a parâmetro; `apply` aplica essa receita com
outros valores, e `foreach` gera N workflows de uma vez. É o caminho de lote
quando a variação muda a estrutura — para variar só valores, `comfy_vary_workflow`
é mais direto.

**Template pronto.** `comfy_list_templates` e `comfy_fetch_template`. Se a
galeria já tem algo com a forma certa, comece dali. Para um teste rápido,
ajuste os slots e rode. Para algo que vai crescer, projete em fragmento
antes de editar.

Antes de baixar, duas checagens que evitam retrabalho: `comfy_check_template`
diz se ele roda **nesta** instalação, e `comfy_show_template` mostra a ficha
sem gravar arquivo. Depois de baixar, `comfy_workflow_notes` lê o que o autor
deixou escrito no grafo — palavra de disparo de LoRA, faixa de CFG, resolução
esperada. Ignorar essas notas não dá erro: dá imagem ruim.

**Fragmento e blueprint.** Este é o caminho padrão para qualquer workflow
que você vai estender, variar ou reaproveitar. Use
`comfy_decompose_workflow` para transformar um template que funciona em
fragmento reutilizável, escreva um blueprint YAML pequeno, e compile com
`comfy_compose_workflow`. `comfy_list_fragments` mostra o que já existe na
biblioteca, consulte antes de decompor algo de novo.

**Edição por slot.** Para mudar valores sem mexer na estrutura,
`comfy_workflow_slots` lista os endereços editáveis e
`comfy_set_workflow_slots` aplica as sobrescritas. Não edite o JSON na mão.

## Executar

`comfy_run_workflow` é assíncrono por padrão e devolve um `prompt_id` na
hora. **Deixe assim.** Com `wait=True` uma geração de vídeo estoura o
timeout da ferramenta e o job continua rodando sem que você receba o
resultado.

O fluxo correto é submeter, acompanhar com `comfy_job_status`, e recolher
com `comfy_fetch_outputs`.

Para lote: `comfy_vary_workflow` gera as variantes, submeta cada uma, e
aguarde todas juntas com `comfy_job_wait`.

Workflow de imagem para imagem precisa dos arquivos de entrada no servidor
primeiro, via `comfy_upload_files`. Use os nomes que ele devolve nos nodes
de carregamento.

## Quando algo dá errado

Não fique adivinhando pelo sintoma. Cada pergunta tem uma ferramenta:

| Situação | Ferramenta |
|---|---|
| O que está rodando agora? Perdi o prompt_id | `comfy_job_list` |
| Falhou e o erro não explica | `comfy_server_logs` |
| Travou, ou os parâmetros estavam errados | `comfy_job_cancel` |
| Falhou e quero saber em que node parou | `comfy_job_watch` |
| Suspeita de falta de VRAM | `comfy_system_stats` |
| Confirmada a falta de VRAM | `comfy_free_memory` |

`comfy_free_memory` descarrega os modelos sem apagar nada do disco, e o
próximo job recarrega o que precisar. Tente isso antes de derrubar o servidor
com `comfy_stop_server`, que leva a fila inteira junto.

Duas coisas sobre ele: vale para o servidor inteiro, não para um workflow — o
ComfyUI já gerencia memória por execução sozinho, e este é o caso em que o
automático não bastou. E o efeito entra pela fila, então com job rodando ele
só se aplica depois; medir a VRAM no ato pode não mostrar diferença.

`comfy_job_watch` não é um `comfy_job_wait` melhorado: o wait responde
"terminou?", o watch devolve o caminho percorrido. Use para diagnóstico, não
para esperar.

Falta de dependência se resolve com `comfy_install_node` e
`comfy_download_model`, mas **os dois mudam a instalação da pessoa** — pergunte
antes. Custom node novo só aparece depois de reiniciar o ComfyUI.

## Entregar o workflow, não o caminho do arquivo

Um workflow num arquivo solto no disco não existe para quem está olhando a
interface. `comfy_workflow_library` com `action: "save"` grava na biblioteca do
ComfyUI e ele aparece na barra lateral de Workflows, a um clique de abrir. Feche
sempre o ciclo assim, em vez de dizer "está em /tmp/x.json".

Ela recusa sobrescrever por padrão. Se o nome já existir, escolha outro ou
confirme com o usuário antes de passar `overwrite=True` — pode ser trabalho dele.

## Mostrar o resultado

Isto é trabalho visual. O usuário decide olhando, não lendo. Assim que uma
geração terminar, leia a imagem para o chat com a ferramenta de leitura de
arquivo. Um caminho de arquivo não é resposta.

Vídeo não dá para exibir direto: rode `comfy_preview_media`, que gera uma
folha de contato PNG com vários quadros, e mostre isso. Áudio vira forma de
onda, mas você não consegue ouvir, então entregue ao ouvido do usuário em
vez de opinar.

Itere em ciclos curtos de mostrar e reagir, não em questionários longos
antes de gerar.

## Quando precisar de mais profundidade

`comfy_read_methodology` imprime as skills oficiais que vêm no comfy-cli.
São textos longos, carregue sob demanda e não por precaução:

| Skill | Quando |
|---|---|
| `comfy` | visão geral antes de um trabalho grande |
| `comfy-fragments` | montar grafos grandes com peças validadas |
| `comfy-debug` | um job falhou e você quer o mapa de erro para correção |
| `comfy-relay` | como apresentar mídia no chat |
| `comfy-director` | vídeo narrativo de várias cenas, continuidade |

## Cuidado com o que sobrescreve

Três ferramentas substituem conteúdo existente e vale conferir com o
usuário antes em material trabalhoso:

- `comfy_set_workflow_slots` com `in_place=True` regrava o workflow
  original. O padrão é `False`, que devolve o resultado sem tocar no
  arquivo. Prefira gravar numa cópia.
- `comfy_fetch_template` e `comfy_compose_workflow` substituem o arquivo de
  destino se ele já existir. Aponte para um caminho novo quando quiser
  preservar a versão anterior.
- `comfy_vary_workflow` e `comfy_fetch_outputs` sobrescrevem arquivos de
  mesmo nome de um lote anterior. Use uma pasta por lote.
- `comfy_edit_graph` regrava o workflow a cada edição. Com `stdout=True` ele
  devolve o resultado sem tocar no arquivo — e, por não gravar, também não
  publica no canvas.
- `comfy_edit_graph` com `action: "reset_doc"` apaga nodes, ids e o histórico de
  replay, e exige `confirm=True` justamente por isso. `clear` esvazia o grafo.
  Pergunte antes dos dois.
- `comfy_workflow_library` com `action: "delete"` remove da biblioteca do usuário
  e não tem desfazer.
