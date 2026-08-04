---
name: comfy-local
description: Gerar imagem, vídeo, áudio e 3D num ComfyUI local via ferramentas MCP. Use ao criar, editar, validar ou executar workflows do ComfyUI, ao investigar quais nodes, custom nodes e modelos existem na instalação, e ao produzir lotes de variações. Cobre o ciclo completo, da descoberta até a coleta das saídas.
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

## Construir: três mecanismos

Escolha pela estrutura do problema, não por hábito.

**Template pronto.** `comfy_list_templates` e `comfy_fetch_template`. Se a
galeria já tem algo com a forma certa, comece dali. Para um teste rápido,
ajuste os slots e rode. Para algo que vai crescer, projete em fragmento
antes de editar.

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
