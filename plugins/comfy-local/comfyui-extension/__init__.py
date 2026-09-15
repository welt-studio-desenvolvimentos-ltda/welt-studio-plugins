"""
comfyui-welt-live — espelha no canvas aberto as edições feitas por agente.

Por que isto existe como extensão, e não como mais uma chamada de API: o
ComfyUI não tem caminho para comandar a aba aberta de fora. O handler de
`/ws` aceita do cliente apenas mensagens `feature_flags` e descarta o
resto, e nenhuma rota faz o servidor transmitir um evento arbitrário aos
frontends conectados. `PromptServer.send_sync` é o único caminho, e ele só
existe dentro do processo do ComfyUI. Daí este pacote.

O que faz: registra uma rota que o servidor MCP do plugin comfy-local chama
ao fim de cada edição, e repassa o grafo para as abas abertas. O JavaScript
em `web/live.js` escuta e carrega no canvas.

Instalação: copie ou ligue este diretório dentro de `custom_nodes/` do seu
ComfyUI e reinicie o servidor.
"""

import json
import logging

from aiohttp import web
from server import PromptServer

# Nome do evento no websocket. O prefixo evita colisão com os eventos do
# próprio ComfyUI e com os de qualquer outro pacote de custom node.
EVENTO = "welt.graph"

# Tamanho máximo do grafo aceito, em bytes. Um workflow grande tem alguns
# megabytes; o teto existe para um corpo malformado não virar consumo de
# memória sem limite.
MAX_BYTES = 32 * 1024 * 1024

log = logging.getLogger("welt-live")

# Último grafo publicado, para uma aba que abriu depois da edição conseguir
# se alinhar em vez de esperar a próxima.
_ultimo = {"version": 0, "graph": None, "name": None}


@PromptServer.instance.routes.post("/welt-live/publish")
async def publish(request):
    """Recebe um grafo do servidor MCP e o repassa às abas abertas.

    Responde com a contagem de abas alcançadas, para quem chamou conseguir
    dizer se alguém estava olhando — publicar sem nenhuma aba aberta não é
    erro, mas é uma informação que muda o que o agente fala para a pessoa.
    """
    if request.content_length and request.content_length > MAX_BYTES:
        return web.json_response({"error": "grafo grande demais"}, status=413)

    try:
        # Lê com teto explícito em vez de request.json(): num corpo em
        # chunked o content_length é None e a guarda acima não vale, então
        # sem isto um corpo malformado viraria consumo de memória sem limite.
        bruto = await request.content.read(MAX_BYTES + 1)
        if len(bruto) > MAX_BYTES:
            return web.json_response({"error": "grafo grande demais"}, status=413)
        corpo = json.loads(bruto.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return web.json_response({"error": f"corpo não é JSON: {exc}"}, status=400)

    grafo = corpo.get("graph") if isinstance(corpo, dict) else None
    if not isinstance(grafo, dict):
        return web.json_response({"error": "campo 'graph' ausente ou não é objeto"}, status=400)

    _ultimo["version"] += 1
    _ultimo["graph"] = grafo
    _ultimo["name"] = corpo.get("name")

    PromptServer.instance.send_sync(
        EVENTO,
        {"version": _ultimo["version"], "name": _ultimo["name"], "graph": grafo},
    )
    abas = len(PromptServer.instance.sockets)
    log.info("welt-live: grafo v%s enviado para %s aba(s)", _ultimo["version"], abas)
    return web.json_response({"ok": True, "version": _ultimo["version"], "clients": abas})


@PromptServer.instance.routes.get("/welt-live/state")
async def state(request):
    """Último grafo publicado, para uma aba que abriu depois se alinhar."""
    return web.json_response(
        {"version": _ultimo["version"], "name": _ultimo["name"], "graph": _ultimo["graph"]}
    )


# Nenhum node novo: esta extensão é só a ponte entre o agente e o canvas.
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
