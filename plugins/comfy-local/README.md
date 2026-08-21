# comfy-local

Plugin de Claude Code que dirige um ComfyUI local por MCP.

Empacota um servidor MCP com 38 ferramentas mais uma skill que ensina o
fluxo de trabalho. O servidor é um wrapper sobre o `comfy-cli`, que emite
um envelope JSON estável em todo comando, incluindo um campo `hint` nos
erros dizendo como corrigir.

## Pré requisitos

1. **Python 3.10 ou superior.** O plugin cria o próprio venv isolado no
   diretório de dados dele, então não suja o seu ambiente.

2. **comfy-cli instalado**, de preferência num venv separado:

   ```
   python -m venv C:\comfy\env
   C:\comfy\env\Scripts\pip.exe install comfy-cli
   C:\comfy\env\Scripts\comfy.exe --workspace=C:\comfy\ComfyUI install
   ```

   Mantenha o ComfyUI num venv próprio, não no venv do plugin. As
   dependências do ComfyUI são pesadas e não devem se misturar.

3. **ComfyUI rodando.** O plugin sobe com `comfy_launch_server` se
   precisar, mas o workspace precisa existir antes.

## Instalação

No marketplace:

```
/plugin marketplace add welt-studio-desenvolvimentos-ltda/welt-studio-plugins
/plugin install comfy-local@welt-studio-plugins
```

O plugin instala desabilitado de propósito, porque conecta a um serviço
externo. Habilite e responda as três perguntas de configuração:

| Campo | Valor |
|---|---|
| Interpretador Python | `python`, ou caminho absoluto |
| Executável comfy | `C:\comfy\env\Scripts\comfy.exe` |
| Endereço do ComfyUI | vazio para o padrão `127.0.0.1:8188` |

Na primeira execução o servidor monta o venv dele e instala o SDK do MCP.
Leva alguns segundos e acontece uma vez só. O progresso sai no stderr,
visível com `claude --debug`.

Essa instalação acontece dentro do tempo de conexão do servidor MCP. Numa
máquina lenta ou numa rede ruim ela pode estourar esse limite, e o servidor
aparece como falha na aba **Errors** do `/plugin`. Na sessão seguinte ele
sobe normal, porque o ambiente já está pronto. Para evitar de vez, suba a
variável `MCP_TIMEOUT` antes de abrir o Claude Code.

## Ferramentas

| Grupo | Ferramentas |
|---|---|
| Estado | `server_info`, `launch_server`, `stop_server` |
| Execução | `validate_workflow`, `run_workflow`, `job_status`, `job_wait`, `job_list`, `job_watch`, `job_cancel`, `fetch_outputs` |
| Diagnóstico | `system_stats`, `free_memory`, `server_logs` |
| Introspecção | `search_nodes`, `show_node`, `list_nodes`, `node_neighbors`, `node_path`, `search_models`, `show_model` |
| Construção | `list_templates`, `show_template`, `check_template`, `fetch_template`, `workflow_slots`, `set_workflow_slots`, `workflow_notes`, `compose_workflow`, `decompose_workflow`, `vary_workflow`, `list_fragments` |
| Dependências | `download_model`, `download_status`, `install_node` |
| Arquivos | `upload_files`, `preview_media` |
| Metodologia | `read_methodology` |

Todas com o prefixo `comfy_`.

Duas mudam a instalação e pedem confirmação do usuário antes:
`comfy_download_model` grava gigabytes em disco e `comfy_install_node` instala
código de terceiro no interpretador do ComfyUI. Uma terceira não mexe na
instalação mas também destrói trabalho: `comfy_job_cancel` descarta o que um
job em andamento já produziu.

## Variáveis de ambiente

Além das três da configuração do plugin, o servidor lê:

| Variável | Padrão | Efeito |
|---|---|---|
| `COMFY_LOCAL_URL` | `127.0.0.1:8188` | endereço padrão do ComfyUI |
| `COMFY_MCP_TIMEOUT` | 300 | timeout em segundos das chamadas normais |
| `COMFY_MCP_LONG_TIMEOUT` | 1800 | timeout de operações longas |
| `COMFY_MCP_MAX_CHARS` | 24000 | teto de caracteres por resposta |

`COMFY_LOCAL_URL` é o que a pergunta "Endereço do ComfyUI" preenche. Aceita
`host:porta` ou `http://host:porta`. As ferramentas que expõem `host` e
`port` sobrescrevem esse padrão chamada a chamada; um valor sem forma de
endereço é ignorado em silêncio e a CLI usa os defaults dela.

Quinze das trinta e oito ferramentas endereçam o servidor por `--host` e
`--port`. As outras vinte e três não: seis resolvem o servidor pelo workspace
ativo — `comfy_upload_files`, `comfy_fetch_outputs`, `comfy_search_models`,
`comfy_show_model`, `comfy_system_stats` e `comfy_free_memory` —, outras falam
com a galeria de templates, e as demais mexem em arquivo local ou no próprio
workspace: `comfy_server_info`, `comfy_launch_server` e `comfy_stop_server`
agem sobre o ComfyUI do workspace ativo, e `comfy_download_model`,
`comfy_download_status` e `comfy_install_node` gravam dentro dele.

Se o seu ComfyUI não está no endereço padrão, aponte o workspace com
`comfy set-default`. Sem isso, tudo que resolve pelo workspace mira no lugar
errado enquanto as quinze roteadas acertam, que é o tipo de falha que custa a
ser entendida.

`comfy_server_logs` é um caso à parte: o comando não aceita `--host`, mas o
`--port` seleciona de qual instância ler o log. Ele respeita a porta de
`COMFY_LOCAL_URL` quando você não passa uma — sem isso, um ComfyUI fora da
8188 teria o log lido da instância errada bem na hora de diagnosticar
uma falha.

## Notas

O `comfy-cli` já traz skills próprias, instaláveis com `comfy skills
install`, que ensinam o agente a usar a CLI direto pelo shell. Este plugin
resolve outro problema: expor a mesma capacidade por MCP, para superfícies
que não têm shell no seu Windows, como o Claude Desktop e o Cowork. Em
Claude Code as duas abordagens funcionam e você pode escolher.

A metodologia completa do Comfy continua acessível pela ferramenta
`comfy_read_methodology`, que imprime as skills oficiais sob demanda em vez
de carregar milhares de linhas em toda sessão.
