"""
comfyui-welt-live — espelha no canvas aberto as edições feitas por agente.

Por que isto existe como extensão, e não como mais uma chamada de API: o
ComfyUI não tem caminho para comandar a aba aberta de fora. O handler de
`/ws` aceita do cliente apenas mensagens `feature_flags` e descarta o
resto, e nenhuma rota faz o servidor transmitir um evento arbitrário aos
frontends conectados. `PromptServer.send_sync` é o único caminho, e ele só
existe dentro do processo do ComfyUI. Daí este pacote.

O que faz, nos dois sentidos:

- **empurra**: uma rota que o servidor MCP chama ao fim de cada edição, e
  que repassa o grafo para as abas abertas;
- **puxa**: uma rota que pede à aba o grafo que está no canvas agora e
  espera a resposta, para o agente editar a partir do que a pessoa está
  vendo em vez de exigir que ela salve antes.

O JavaScript em `web/live.js` é a outra ponta das duas.

Instalação: copie ou ligue este diretório dentro de `custom_nodes/` do seu
ComfyUI e reinicie o servidor.
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Optional, Tuple

from aiohttp import web
from server import PromptServer

# Nomes dos eventos no websocket. O prefixo evita colisão com os eventos do
# próprio ComfyUI e com os de qualquer outro pacote de custom node.
EVENTO = "welt.graph"
EVENTO_PULL = "welt.pull"

# Quanto esperar a aba responder a um pedido de leitura.
#
# A serialização em si é instantânea, mas o navegador reduz a prioridade de
# rede e de temporizadores numa aba fora de foco — e a aba do ComfyUI quase
# sempre está atrás da janela onde a pessoa conversa com o agente. Dez
# segundos falhavam com frequência por isso. O teto continua existindo para
# a rota não ficar pendurada quando nenhuma aba respondeu de fato.
PULL_TIMEOUT = 25.0

# Tamanho máximo do grafo aceito, em bytes. Um workflow grande tem alguns
# megabytes; o teto existe para um corpo malformado não virar consumo de
# memória sem limite.
MAX_BYTES = 32 * 1024 * 1024

log = logging.getLogger("welt-live")

# Último grafo publicado, para uma aba que abriu depois da edição conseguir
# se alinhar em vez de esperar a próxima.
_ultimo = {"version": 0, "graph": None, "name": None}


async def _ler_json(request) -> Tuple[Any, Optional[web.Response]]:
    """Lê o corpo inteiro como JSON, com teto de tamanho.

    Devolve (corpo, None) em sucesso e (None, resposta_de_erro) em falha.

    O laço é o ponto: `request.content.read(n)` devolve *até* n bytes — ele
    espera só o primeiro pedaço chegar ao buffer e entrega o que houver.
    Um workflow de verdade passa de um pedaço de transporte, então ler uma
    vez só entregaria um prefixo e o json.loads acusaria corpo inválido
    justamente nos grafos grandes. Aqui acumulamos até o fim, conferindo o
    teto a cada pedaço — o que também impede um corpo em chunked, sem
    content-length, de virar consumo de memória sem limite.
    """
    if request.content_length and request.content_length > MAX_BYTES:
        return None, web.json_response({"error": "grafo grande demais"}, status=413)

    pedacos = []
    total = 0
    try:
        async for pedaco in request.content.iter_any():
            total += len(pedaco)
            if total > MAX_BYTES:
                return None, web.json_response({"error": "grafo grande demais"}, status=413)
            pedacos.append(pedaco)
        corpo = json.loads(b"".join(pedacos).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return None, web.json_response({"error": f"corpo não é JSON: {exc}"}, status=400)

    return corpo, None


@PromptServer.instance.routes.post("/welt-live/publish")
async def publish(request):
    """Recebe um grafo do servidor MCP e o repassa às abas abertas.

    Responde com a contagem de abas alcançadas, para quem chamou conseguir
    dizer se alguém estava olhando — publicar sem nenhuma aba aberta não é
    erro, mas é uma informação que muda o que o agente fala para a pessoa.
    """
    corpo, erro = await _ler_json(request)
    if erro is not None:
        return erro

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


# ---------------------------------------------------------------------
# Leitura: o agente pede o canvas e a aba responde
#
# O sentido contrário do publish. O grafo aberto vive no navegador, não no
# processo do ComfyUI, então ninguém do lado servidor consegue lê-lo: o
# jeito é pedir pelo websocket e esperar a aba devolver por HTTP. Um pedido
# por vez fica registrado aqui pelo token, para duas leituras simultâneas
# não pegarem a resposta uma da outra.
# ---------------------------------------------------------------------

_pedidos: dict[str, dict] = {}


@PromptServer.instance.routes.post("/welt-live/pull")
async def pull(request):
    """Pede o grafo do canvas à aba aberta e devolve o que ela mandar.

    Se houver mais de uma aba, a primeira a responder ganha — é a mesma
    escolha que o publish faz ao mandar para todas.
    """
    if not PromptServer.instance.sockets:
        return web.json_response(
            {"error": "nenhuma aba do ComfyUI aberta", "code": "sem_aba"}, status=409
        )

    token = uuid.uuid4().hex
    evento = asyncio.Event()
    _pedidos[token] = {"event": evento, "graph": None, "name": None}
    try:
        PromptServer.instance.send_sync(EVENTO_PULL, {"token": token})
        try:
            await asyncio.wait_for(evento.wait(), timeout=PULL_TIMEOUT)
        except asyncio.TimeoutError:
            return web.json_response(
                {
                    "error": f"a aba não respondeu em {PULL_TIMEOUT}s",
                    "code": "sem_resposta",
                },
                status=504,
            )
        pedido = _pedidos[token]
        return web.json_response({"ok": True, "graph": pedido["graph"], "name": pedido["name"]})
    finally:
        _pedidos.pop(token, None)


@PromptServer.instance.routes.post("/welt-live/canvas")
async def canvas(request):
    """Recebe da aba o grafo pedido por /welt-live/pull.

    Chamada só pelo JavaScript da extensão, em resposta ao evento de
    pedido. Um token que não está registrado é pedido que já expirou.
    """
    corpo, erro = await _ler_json(request)
    if erro is not None:
        return erro

    if not isinstance(corpo, dict):
        return web.json_response({"error": "corpo não é objeto"}, status=400)

    # Só string é chave aceitável: um token de outro tipo — uma lista, por
    # exemplo — explodiria em TypeError dentro do dict e viraria 500.
    token = corpo.get("token")
    pedido = _pedidos.get(token) if isinstance(token, str) and token else None
    if pedido is None:
        # O pedido expirou ou já foi atendido por outra aba. Não é erro do
        # lado de lá, então respondemos em paz em vez de sujar o console.
        return web.json_response({"ok": True, "ignored": True})
    if pedido["event"].is_set():
        return web.json_response({"ok": True, "ignored": True})

    pedido["graph"] = corpo.get("graph")
    pedido["name"] = corpo.get("name")
    pedido["event"].set()
    return web.json_response({"ok": True})


# Nenhum node novo: esta extensão é só a ponte entre o agente e o canvas.
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
