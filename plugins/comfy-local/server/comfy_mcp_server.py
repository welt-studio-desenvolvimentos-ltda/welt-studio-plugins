"""
comfy_mcp_server.py

Servidor MCP que expõe o ComfyUI local para clientes que não rodam shell,
como o Claude Desktop. Fala MCP por stdio.

Cada ferramenta é um wrapper fino sobre o comfy-cli, que já emite um
envelope JSON estável em todo comando. O envelope vem de volta cru para
o agente, incluindo o campo "hint" nos erros, que é onde o comfy-cli diz
exatamente o que fazer quando algo falha.

Requisitos:
    pip install "mcp[cli]" comfy-cli

Configuração (Claude Desktop, Windows):
    %APPDATA%\\Roaming\\Claude\\claude_desktop_config.json

    {
      "mcpServers": {
        "comfy": {
          "command": "C:\\\\comfy\\\\env\\\\Scripts\\\\python.exe",
          "args": ["C:\\\\comfy\\\\comfy_mcp_server.py"],
          "env": { "COMFY_BIN": "C:\\\\comfy\\\\env\\\\Scripts\\\\comfy.exe" }
        }
      }
    }

Variáveis de ambiente:
    COMFY_BIN            caminho absoluto do executável comfy (recomendado)
    COMFY_LOCAL_URL      endereço padrão do ComfyUI, se não for 127.0.0.1:8188.
                         Aceita "host:porta" ou "http://host:porta", e vale
                         para todas as chamadas. Os parâmetros host e port de
                         cada ferramenta sobrescrevem este padrão.
    COMFY_MCP_TIMEOUT    timeout padrão em segundos (default 300)
    COMFY_MCP_MAX_CHARS  teto de caracteres por resposta (default 24000)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from typing import Any, List, Optional, Tuple
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

# O SDK do MCP renomeou FastMCP para MCPServer na versão 2.0 e trocou o
# dicionário de anotações por um modelo tipado. Este bloco aceita as duas.
try:  # SDK 2.x
    from mcp.server import MCPServer as _ServerClass
    from mcp.types import ToolAnnotations as _ToolAnnotations

    _SDK_V2 = True
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as _ServerClass
    from mcp.types import ToolAnnotations as _ToolAnnotations

    _SDK_V2 = False

# ---------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------

COMFY_BIN: str = os.environ.get("COMFY_BIN") or shutil.which("comfy") or "comfy"
DEFAULT_TIMEOUT: int = int(os.environ.get("COMFY_MCP_TIMEOUT", "300"))
LONG_TIMEOUT: int = int(os.environ.get("COMFY_MCP_LONG_TIMEOUT", "1800"))
MAX_CHARS: int = int(os.environ.get("COMFY_MCP_MAX_CHARS", "24000"))


_HOSTNAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


def _parse_address(raw: Optional[str]) -> Tuple[Optional[str], Optional[int]]:
    """Extrai host e porta de COMFY_LOCAL_URL.

    Aceita "host", "host:porta" e "http://host:porta". Qualquer outra coisa
    é descartada e o padrão simplesmente não se aplica, deixando a CLI usar
    os defaults dela. Ser rigoroso aqui importa: um host inventado não falha
    alto, ele faz toda chamada bater numa máquina que não existe. E levantar
    exceção também não serve, porque mataria o servidor no import.
    """
    text = (raw or "").strip()
    if not text:
        return None, None
    if "${" in text:
        # Placeholder que o Claude Code não substituiu, tipicamente porque o
        # campo opcional da configuração ficou em branco. Sem isto, o literal
        # viraria um --host inválido em toda chamada.
        return None, None
    if "://" not in text:
        text = "http://" + text

    try:
        parsed = urlparse(text)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        # Porta fora da faixa, entre outros.
        return None, None

    if not host or not _HOSTNAME.match(host):
        return None, None
    if host.isdigit():
        # "8188" é porta digitada no campo do endereço, não nome de máquina.
        return None, None
    return host, port


DEFAULT_HOST, DEFAULT_PORT = _parse_address(os.environ.get("COMFY_LOCAL_URL"))

mcp = _ServerClass("comfy_mcp")


def _tool(
    name: str,
    title: str,
    read_only: bool = True,
    destructive: bool = False,
    idempotent: bool = True,
    open_world: bool = False,
):
    """Registra uma ferramenta com anotações, nos dois formatos de SDK."""
    annotations = _ToolAnnotations(
        title=title,
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )
    if _SDK_V2:
        return mcp.tool(name=name, title=title, annotations=annotations)
    return mcp.tool(name=name, annotations=annotations)


# ---------------------------------------------------------------------
# Infraestrutura compartilhada
# ---------------------------------------------------------------------


class Base(BaseModel):
    """Base comum: tira espaço em branco e recusa campos desconhecidos."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )


def _truncate(text: str, cap: Optional[int] = None) -> str:
    limit = cap or MAX_CHARS
    if len(text) <= limit:
        return text
    return (
        text[:limit]
        + f"\n\n[... resposta truncada em {limit} caracteres. "
        "Reduza o escopo com limit ou filtros mais específicos.]"
    )


def _parse_envelope(raw: str) -> Optional[dict]:
    """
    Extrai o envelope da saída.

    A CLI pode emitir NDJSON (várias linhas) dependendo do comando. O
    envelope final é o que interessa, então varremos de trás para frente.
    """
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    for line in reversed(lines):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ("ok" in obj or obj.get("type") == "envelope"):
            return obj
    # Último recurso: a saída inteira pode ser um único JSON multi linha.
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


async def _accumulate(stream: Any, sink: List[bytes]) -> None:
    """Lê um cano até o fim, guardando os pedaços fora da tarefa.

    É por isso que não usamos communicate(): ao ser cancelado por tempo
    esgotado ele descarta o que já tinha lido, junto com a resposta que
    talvez estivesse ali inteira. Acumulando aqui, o que chegou sobrevive.
    """
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return
        sink.append(chunk)


async def _spawn(args: List[str], timeout: int) -> Tuple[Optional[dict], str, str, int]:
    """
    Roda `comfy --json <args>` e devolve (falha, stdout, stderr, código).

    O --json vai antes do subcomando de propósito. Como flag global ele
    produz o envelope único; depois do subcomando, alguns comandos (run,
    por exemplo) passam a emitir NDJSON de eventos.

    `falha` só vem preenchido quando o processo nem chegou a produzir saída
    aproveitável: executável ausente ou tempo esgotado.
    """
    env = dict(os.environ)
    # Evita que o prompt de consentimento de telemetria trave o processo,
    # que não tem terminal interativo aqui.
    env.setdefault("DO_NOT_TRACK", "1")
    env.setdefault("COMFY_NO_TELEMETRY", "1")

    # Ao subir o servidor em segundo plano, o comfy-cli relança a si mesmo
    # pelo nome curto `comfy`. Se o venv dele não está no PATH desta sessão,
    # esse relançamento morre. Chamamos pelo caminho absoluto, então o
    # diretório precisa entrar no PATH explicitamente.
    bin_dir = os.path.dirname(COMFY_BIN)
    if bin_dir:
        env["PATH"] = os.path.abspath(bin_dir) + os.pathsep + env.get("PATH", "")

    cmd = [COMFY_BIN, "--json", *args]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except OSError as exc:
        # FileNotFoundError é o caso comum, mas o campo de configuração é um
        # seletor de arquivo: dá para apontar um diretório, um script sem
        # permissão de execução ou, no Windows, algo que não é executável
        # (WinError 193). Todos chegam aqui como OSError e merecem o mesmo
        # envelope com hint, em vez de um traceback cru.
        return (
            {
                "ok": False,
                "error": {
                    "code": "comfy_bin_not_found",
                    "message": f"Não foi possível executar {COMFY_BIN}: {exc}",
                    "hint": (
                        "Instale com `pip install comfy-cli` e aponte a variável "
                        "COMFY_BIN para o caminho absoluto do executável, por "
                        "exemplo C:\\comfy\\env\\Scripts\\comfy.exe"
                    ),
                },
            },
            "",
            "",
            -1,
        )

    out_chunks: List[bytes] = []
    err_chunks: List[bytes] = []
    leitores = [
        asyncio.ensure_future(_accumulate(proc.stdout, out_chunks)),
        asyncio.ensure_future(_accumulate(proc.stderr, err_chunks)),
    ]

    def decodificado() -> Tuple[str, str]:
        return (
            b"".join(out_chunks).decode("utf-8", errors="replace"),
            b"".join(err_chunks).decode("utf-8", errors="replace"),
        )

    try:
        await asyncio.wait_for(asyncio.gather(*leitores), timeout=timeout)
        await proc.wait()
    except asyncio.TimeoutError:
        for leitor in leitores:
            leitor.cancel()
        proc.kill()
        await proc.wait()
        # Os canos fecham no fim do último processo que os herdou, não no fim
        # do comfy. Um neto segurando o stdout estoura o tempo mesmo com a
        # resposta já entregue, então devolvemos o que chegou: quem chama
        # tenta ler o envelope daí antes de tratar como falha.
        raw_out, raw_err = decodificado()
        return (
            {
                "ok": False,
                "error": {
                    "code": "timeout",
                    "message": f"`{' '.join(cmd)}` passou de {timeout}s.",
                    "hint": (
                        "Para jobs longos, submeta sem wait e acompanhe com "
                        "comfy_job_status, ou aumente COMFY_MCP_TIMEOUT."
                    ),
                },
                "raw_stderr": raw_err[:4000],
            },
            raw_out,
            raw_err,
            -1,
        )

    raw_out, raw_err = decodificado()
    # returncode só é None enquanto o processo vive; depois do wait() ele já
    # terminou. O -1 é defensivo, para não mentir "código 0" caso a
    # implementação de subprocesso deixe o campo vazio.
    return (
        None,
        raw_out,
        raw_err,
        proc.returncode if proc.returncode is not None else -1,
    )


async def _cli_obj(args: List[str], timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Roda a CLI e devolve o envelope já desserializado, sem truncar."""
    failure, raw_out, raw_err, code = await _spawn(args, timeout)

    # O envelope vem primeiro, mesmo quando o processo foi morto por tempo
    # esgotado: se a resposta completa já tinha chegado, entregar ela é bem
    # melhor do que mandar o agente aumentar o timeout à toa.
    envelope = _parse_envelope(raw_out)
    if envelope is not None:
        return envelope

    if failure is not None:
        return failure

    # Sem envelope: devolve o que houver, com o código de saída, para o
    # agente ao menos conseguir diagnosticar.
    return {
        "ok": code == 0,
        "error": None
        if code == 0
        else {
            "code": "unparsed_output",
            "message": f"A CLI saiu com código {code}.",
            "hint": "Veja stdout e stderr abaixo.",
        },
        "raw_stdout": raw_out[:8000],
        "raw_stderr": raw_err[:4000],
    }


async def _cli(
    args: List[str],
    timeout: int = DEFAULT_TIMEOUT,
    cap: Optional[int] = None,
) -> str:
    """Roda a CLI e devolve o envelope como texto, pronto para o agente."""
    envelope = await _cli_obj(args, timeout)
    return _truncate(json.dumps(envelope, indent=2, ensure_ascii=False), cap)


async def _cli_text(
    args: List[str],
    timeout: int = DEFAULT_TIMEOUT,
    cap: Optional[int] = None,
) -> str:
    """
    Variante para comandos que imprimem texto puro em vez de envelope.

    `skills show`, por exemplo, devolve markdown. Nesses casos o stdout é a
    resposta e não faz sentido passar por json.dumps.
    """
    failure, raw_out, raw_err, _ = await _spawn(args, timeout)
    if raw_out.strip():
        return _truncate(raw_out, cap)
    if failure is not None:
        return json.dumps(failure, indent=2, ensure_ascii=False)
    return _truncate(raw_err or "(sem saída)", cap)


def _opt(args: List[str], flag: str, value: Any) -> None:
    """Acrescenta `--flag value` somente se value não for None."""
    if value is not None:
        args.extend([flag, str(value)])


def _routing(args: List[str], host: Optional[str], port: Optional[int]) -> None:
    """Endereço do servidor: o parâmetro da ferramenta vence COMFY_LOCAL_URL."""
    _opt(args, "--host", host if host is not None else DEFAULT_HOST)
    _opt(args, "--port", port if port is not None else DEFAULT_PORT)


# ---------------------------------------------------------------------
# Estado do servidor
# ---------------------------------------------------------------------


class EmptyInput(Base):
    """Sem parâmetros."""


@_tool(
    name="comfy_server_info",
    title="Estado do ComfyUI local",
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_server_info(params: EmptyInput) -> str:
    """Diz se existe um ComfyUI local rodando, onde, e qual workspace está ativo.

    Chame esta ferramenta PRIMEIRO em qualquer sessão. Ela combina o
    ambiente resolvido pela CLI com o workspace selecionado, o que responde
    de uma vez: a CLI existe, o workspace está configurado, e o servidor
    está no ar.

    Args:
        params (EmptyInput): sem parâmetros.

    Returns:
        str: JSON com duas chaves, "env" e "which", cada uma contendo o
            envelope do comando correspondente. Se o servidor não estiver
            rodando, os comandos seguintes retornarão o código de erro
            `server_not_running`, resolvido por comfy_launch_server.
    """
    env_obj, which_obj = await asyncio.gather(
        _cli_obj(["env"], timeout=60),
        _cli_obj(["which"], timeout=60),
    )
    return _truncate(
        json.dumps({"env": env_obj, "which": which_obj}, indent=2, ensure_ascii=False)
    )


class LaunchInput(Base):
    background: bool = Field(
        default=True,
        description="Subir em segundo plano e devolver o controle na hora. "
        "Deixe True: em primeiro plano a ferramenta bloqueia até o timeout.",
    )
    extra_args: Optional[List[str]] = Field(
        default=None,
        description="Argumentos extras passados direto ao ComfyUI, por exemplo "
        "['--lowvram'] ou ['--port', '8189'].",
        max_length=12,
    )


@_tool(
    name="comfy_launch_server",
    title="Subir o ComfyUI local",
    read_only=False,
    destructive=False,
    idempotent=False,
    open_world=False,
)
async def comfy_launch_server(params: LaunchInput) -> str:
    """Sobe o ComfyUI local, por padrão em segundo plano.

    Use quando comfy_server_info ou qualquer outro comando retornar o código
    `server_not_running`. Não derruba nem reinicia uma instância que já
    esteja no ar.

    Args:
        params (LaunchInput):
            - background (bool): rodar em segundo plano, padrão True
            - extra_args (Optional[List[str]]): argumentos para o ComfyUI

    Returns:
        str: envelope JSON da CLI. O carregamento dos modelos continua
            depois do retorno, então o servidor pode levar alguns segundos
            até aceitar jobs.
    """
    args = ["launch"]
    if params.background:
        args.append("--background")
    if params.extra_args:
        args.append("--")
        args.extend(params.extra_args)
    return await _cli(args, timeout=LONG_TIMEOUT)


@_tool(
    name="comfy_stop_server",
    title="Derrubar o ComfyUI em segundo plano",
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_stop_server(params: EmptyInput) -> str:
    """Derruba a instância de ComfyUI iniciada em segundo plano pela CLI.

    Encerra apenas o processo do servidor. Não apaga workspace, modelos,
    workflows nem saídas já geradas. Jobs em execução são interrompidos.

    Args:
        params (EmptyInput): sem parâmetros.

    Returns:
        str: envelope JSON da CLI.
    """
    return await _cli(["stop"], timeout=120)


# ---------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------


class ValidateInput(Base):
    workflow_path: str = Field(
        ...,
        description="Caminho do JSON do workflow. Ex: C:\\comfy\\projeto\\txt2img.json",
        min_length=1,
    )
    host: Optional[str] = Field(default=None, description="Host do ComfyUI, padrão 127.0.0.1.")
    port: Optional[int] = Field(default=None, description="Porta do ComfyUI, padrão 8188.", ge=1, le=65535)


@_tool(
    name="comfy_validate_workflow",
    title="Validar workflow antes de rodar",
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_validate_workflow(params: ValidateInput) -> str:
    """Confere um workflow contra a instalação viva sem submeter nada.

    Valida class_types, formato das entradas e valores de enum contra o
    object_info do servidor. Rode isto antes de comfy_run_workflow: pega
    no ato node ausente, checkpoint que não existe e ligação errada, em vez
    de descobrir depois de esperar a geração.

    Args:
        params (ValidateInput):
            - workflow_path (str): caminho do JSON
            - host, port (Optional): endereço do servidor

    Returns:
        str: envelope JSON. Em caso de falha, o campo error.details costuma
            trazer `close_matches` com o nome correto do node.
    """
    args = ["validate", "--workflow", params.workflow_path]
    _routing(args, params.host, params.port)
    return await _cli(args)


class RunInput(Base):
    workflow_path: Optional[str] = Field(
        default=None,
        description="Caminho do JSON do workflow. Aceita tanto formato API quanto "
        "o exportado pela interface, a conversão é feita pela CLI. Omita "
        "somente se for usar o parâmetro prompt.",
    )
    prompt: Optional[str] = Field(
        default=None,
        description="Prompt positivo para o workflow txt2img embutido. Use apenas "
        "para um teste rápido sem workflow próprio.",
    )
    wait: bool = Field(
        default=False,
        description="True bloqueia até terminar. False, o padrão, submete e "
        "devolve o prompt_id na hora, para acompanhar com comfy_job_status. "
        "Prefira False em jobs longos, senão a ferramenta estoura o timeout.",
    )
    host: Optional[str] = Field(default=None, description="Host do ComfyUI, padrão 127.0.0.1.")
    port: Optional[int] = Field(default=None, description="Porta do ComfyUI, padrão 8188.", ge=1, le=65535)


@_tool(
    name="comfy_run_workflow",
    title="Submeter um workflow",
    read_only=False,
    destructive=False,
    idempotent=False,
    open_world=False,
)
async def comfy_run_workflow(params: RunInput) -> str:
    """Submete um workflow ao ComfyUI local.

    Assíncrono por padrão: retorna em milissegundos com um prompt_id
    enquanto a geração roda. Acompanhe com comfy_job_status ou
    comfy_job_wait, depois recolha os arquivos com comfy_fetch_outputs.

    Passe wait=True apenas em workflows curtos. Uma geração de vídeo pode
    passar do timeout da ferramenta e o job continuaria rodando sem que
    você recebesse o resultado.

    Args:
        params (RunInput):
            - workflow_path (Optional[str]): caminho do JSON
            - prompt (Optional[str]): prompt para o workflow embutido
            - wait (bool): bloquear até terminar, padrão False
            - host, port (Optional): endereço do servidor

    Returns:
        str: envelope JSON com data.prompt_id. Guarde esse id, é o que as
            demais ferramentas de job consomem.
    """
    if not params.workflow_path and not params.prompt:
        return json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "missing_input",
                    "message": "Nem workflow_path nem prompt foram informados.",
                    "hint": "Passe workflow_path com o caminho do JSON, ou prompt "
                    "para usar o workflow txt2img embutido.",
                },
            },
            indent=2,
            ensure_ascii=False,
        )

    args = ["run"]
    _opt(args, "--workflow", params.workflow_path)
    _opt(args, "--prompt", params.prompt)
    if params.wait:
        args.append("--wait")
    _routing(args, params.host, params.port)
    return await _cli(args, timeout=LONG_TIMEOUT if params.wait else DEFAULT_TIMEOUT)


class JobStatusInput(Base):
    prompt_id: str = Field(..., description="O prompt_id devolvido por comfy_run_workflow.", min_length=1)
    host: Optional[str] = Field(default=None, description="Host do ComfyUI.")
    port: Optional[int] = Field(default=None, description="Porta do ComfyUI.", ge=1, le=65535)


@_tool(
    name="comfy_job_status",
    title="Status de um job",
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_job_status(params: JobStatusInput) -> str:
    """Consulta o estado de um job pelo prompt_id, sem bloquear.

    Args:
        params (JobStatusInput):
            - prompt_id (str): id devolvido pela submissão
            - host, port (Optional): endereço do servidor

    Returns:
        str: envelope JSON com o estado do job. Se já terminou, use
            comfy_fetch_outputs para trazer os arquivos.
    """
    args = ["jobs", "status", params.prompt_id]
    _routing(args, params.host, params.port)
    return await _cli(args, timeout=120)


class JobWaitInput(Base):
    prompt_ids: List[str] = Field(
        ...,
        description="Um ou mais prompt_ids. Bloqueia até todos terminarem.",
        min_length=1,
        max_length=32,
    )
    timeout_seconds: Optional[float] = Field(
        default=None,
        description="Desistir depois de N segundos. Deixe abaixo do timeout do "
        "servidor MCP para receber uma resposta limpa.",
        gt=0,
    )
    poll_interval: Optional[float] = Field(
        default=None, description="Segundos entre consultas. São jobs longos, não martele.", gt=0
    )
    host: Optional[str] = Field(default=None, description="Host do ComfyUI.")
    port: Optional[int] = Field(default=None, description="Porta do ComfyUI.", ge=1, le=65535)


@_tool(
    name="comfy_job_wait",
    title="Esperar jobs terminarem",
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_job_wait(params: JobWaitInput) -> str:
    """Bloqueia até todos os prompt_ids informados chegarem a um estado final.

    Útil depois de disparar um lote de variações. Para um job único e longo,
    prefere-se consultar comfy_job_status de tempos em tempos.

    Args:
        params (JobWaitInput):
            - prompt_ids (List[str]): ids a aguardar
            - timeout_seconds (Optional[float]): teto de espera
            - poll_interval (Optional[float]): intervalo entre consultas
            - host, port (Optional): endereço do servidor

    Returns:
        str: envelope JSON com o resumo de cada job.
    """
    args = ["jobs", "wait", *params.prompt_ids]
    _opt(args, "--timeout", params.timeout_seconds)
    _opt(args, "--poll-interval", params.poll_interval)
    _routing(args, params.host, params.port)
    return await _cli(args, timeout=LONG_TIMEOUT)


class FetchOutputsInput(Base):
    prompt_id: str = Field(..., description="Prompt_id de um job já concluído.", min_length=1)
    out_dir: Optional[str] = Field(
        default=None,
        description="Pasta onde salvar. Padrão: ./outputs relativo ao diretório "
        "de trabalho do servidor MCP.",
    )
    url_only: bool = Field(
        default=False,
        description="True devolve só as URLs, sem baixar arquivo. Útil para "
        "inspecionar o que saiu antes de gravar em disco.",
    )


@_tool(
    name="comfy_fetch_outputs",
    title="Recolher as saídas de um job",
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_fetch_outputs(params: FetchOutputsInput) -> str:
    """Baixa os arquivos gerados por um job concluído.

    Escreve arquivos novos em out_dir. Não apaga nada, mas um arquivo de
    mesmo nome de uma execução anterior pode ser sobrescrito, então use
    uma pasta por lote quando estiver gerando variações.

    Args:
        params (FetchOutputsInput):
            - prompt_id (str): id do job concluído
            - out_dir (Optional[str]): pasta de destino
            - url_only (bool): só listar URLs, sem baixar

    Returns:
        str: envelope JSON com os caminhos ou URLs das saídas.
    """
    # `download` não aceita --host nem --port: resolve o servidor pelo
    # workspace ativo. Por isso não passa por _routing.
    args = ["download", params.prompt_id]
    _opt(args, "--out-dir", params.out_dir)
    if params.url_only:
        args.append("--url-only")
    return await _cli(args, timeout=LONG_TIMEOUT)


# ---------------------------------------------------------------------
# Introspecção da instalação viva
# ---------------------------------------------------------------------


class SearchNodesInput(Base):
    query: str = Field(..., description="Texto a procurar, substring sem diferenciar maiúscula.", min_length=1)
    limit: Optional[int] = Field(default=25, description="Teto de resultados.", ge=1, le=200)
    host: Optional[str] = Field(default=None, description="Host do ComfyUI.")
    port: Optional[int] = Field(default=None, description="Porta do ComfyUI.", ge=1, le=65535)


@_tool(
    name="comfy_search_nodes",
    title="Procurar classes de node",
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_search_nodes(params: SearchNodesInput) -> str:
    """Busca difusa nas classes de node da instalação viva, custom nodes inclusos.

    Esta é a vantagem real de um servidor local sobre um na nuvem: os
    resultados refletem o que existe de fato nesta máquina, e não um
    catálogo genérico. Consulte aqui antes de montar qualquer workflow.

    Args:
        params (SearchNodesInput):
            - query (str): texto da busca
            - limit (Optional[int]): teto de resultados, padrão 25
            - host, port (Optional): endereço do servidor

    Returns:
        str: envelope JSON com as classes que casaram. Para o schema
            completo de uma delas, use comfy_show_node.
    """
    args = ["nodes", "search", params.query]
    _opt(args, "--limit", params.limit)
    _routing(args, params.host, params.port)
    return await _cli(args, timeout=120)


class ShowNodeInput(Base):
    name: str = Field(
        ...,
        description="Nome exato da classe, sensível a maiúscula. Ex: 'KSampler'.",
        min_length=1,
    )
    host: Optional[str] = Field(default=None, description="Host do ComfyUI.")
    port: Optional[int] = Field(default=None, description="Porta do ComfyUI.", ge=1, le=65535)


@_tool(
    name="comfy_show_node",
    title="Schema completo de um node",
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_show_node(params: ShowNodeInput) -> str:
    """Mostra entradas, saídas, defaults e restrições de uma classe de node.

    Args:
        params (ShowNodeInput):
            - name (str): nome exato da classe
            - host, port (Optional): endereço do servidor

    Returns:
        str: envelope JSON com o schema. Se o nome não existir, o erro
            `node_not_found` traz `details.close_matches` com sugestões.
    """
    args = ["nodes", "show", params.name]
    _routing(args, params.host, params.port)
    return await _cli(args, timeout=120)


class SearchModelsInput(Base):
    text: Optional[str] = Field(default=None, description="Substring no nome do arquivo do modelo.")
    model_type: Optional[str] = Field(
        default=None,
        description="Tipo do modelo: lora, checkpoint, vae, controlnet, e assim por diante.",
    )
    limit: Optional[int] = Field(default=50, description="Teto de resultados.", ge=1, le=500)
    folders_only: bool = Field(
        default=False,
        description="True lista apenas as pastas de modelo que o backend enxerga, "
        "ignorando os demais filtros. Use para descobrir nomes de tipo válidos.",
    )


@_tool(
    name="comfy_search_models",
    title="Procurar modelos em disco",
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_search_models(params: SearchModelsInput) -> str:
    """Lista os arquivos de modelo presentes nesta máquina.

    Chame antes de escrever um workflow que referencia checkpoint, LoRA ou
    VAE por nome. O erro mais comum em workflow gerado por agente é citar um
    arquivo que não existe no disco.

    Args:
        params (SearchModelsInput):
            - text (Optional[str]): substring do nome
            - model_type (Optional[str]): tipo do modelo
            - limit (Optional[int]): teto de resultados, padrão 50
            - folders_only (bool): listar só as pastas de modelo

    Returns:
        str: envelope JSON com os modelos encontrados.
    """
    # `models` não aceita --host nem --port: lê o workspace ativo em disco.
    # Por isso não passa por _routing.
    if params.folders_only:
        return await _cli(["models", "list-folders"], timeout=120)
    args = ["models", "search"]
    _opt(args, "--text", params.text)
    _opt(args, "--type", params.model_type)
    _opt(args, "--limit", params.limit)
    return await _cli(args, timeout=120)


# ---------------------------------------------------------------------
# Templates e edição por slot
# ---------------------------------------------------------------------


class ListTemplatesInput(Base):
    media_type: Optional[str] = Field(default=None, description="Tipo de saída: image, video, audio, 3d.")
    name: Optional[str] = Field(default=None, description="Substring no nome do template.")
    model: Optional[str] = Field(default=None, description="Substring no nome do modelo, ex 'Flux'.")
    limit: Optional[int] = Field(default=30, description="Teto de resultados.", ge=1, le=200)


@_tool(
    name="comfy_list_templates",
    title="Navegar a galeria de templates",
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_list_templates(params: ListTemplatesInput) -> str:
    """Lista os workflows da galeria curada do Comfy.

    Caminho mais rápido para um workflow que funciona: pegue um template
    próximo do que você quer e ajuste os slots, em vez de montar o grafo do
    zero.

    Args:
        params (ListTemplatesInput):
            - media_type, name, model (Optional[str]): filtros
            - limit (Optional[int]): teto de resultados, padrão 30

    Returns:
        str: envelope JSON com os templates. Baixe um com comfy_fetch_template.
    """
    args = ["templates", "ls"]
    _opt(args, "--type", params.media_type)
    _opt(args, "--name", params.name)
    _opt(args, "--model", params.model)
    _opt(args, "--limit", params.limit)
    return await _cli(args, timeout=180)


class FetchTemplateInput(Base):
    name: str = Field(..., description="Nome do template, igual ao listado por comfy_list_templates.", min_length=1)
    out_path: str = Field(
        ...,
        description="Onde gravar o JSON. Ex: C:\\comfy\\projeto\\meu.json",
        min_length=1,
    )


@_tool(
    name="comfy_fetch_template",
    title="Baixar o JSON de um template",
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=True,
)
async def comfy_fetch_template(params: FetchTemplateInput) -> str:
    """Baixa o workflow JSON de um template e grava em out_path.

    Escreve um arquivo. Se out_path já existir, o conteúdo é substituído,
    então aponte para um caminho novo se quiser preservar o anterior.

    Args:
        params (FetchTemplateInput):
            - name (str): nome do template
            - out_path (str): caminho de destino

    Returns:
        str: envelope JSON confirmando a gravação.
    """
    return await _cli(["templates", "fetch", params.name, "--out", params.out_path], timeout=180)


class SlotsInput(Base):
    workflow_path: str = Field(..., description="Caminho do workflow em formato de interface.", min_length=1)
    host: Optional[str] = Field(default=None, description="Host do ComfyUI.")
    port: Optional[int] = Field(default=None, description="Porta do ComfyUI.", ge=1, le=65535)


@_tool(
    name="comfy_workflow_slots",
    title="Listar slots editáveis de um workflow",
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_workflow_slots(params: SlotsInput) -> str:
    """Lista os campos de um workflow que dão para sobrescrever por endereço.

    Chame antes de comfy_set_workflow_slots para descobrir os endereços
    válidos. Evita editar JSON na mão.

    Args:
        params (SlotsInput):
            - workflow_path (str): caminho do workflow
            - host, port (Optional): endereço do servidor

    Returns:
        str: envelope JSON com os slots e seus endereços, no formato usado
            depois em overrides, por exemplo '6.text'.
    """
    args = ["workflow", "slots", params.workflow_path]
    _routing(args, params.host, params.port)
    return await _cli(args, timeout=120)


class SetSlotsInput(Base):
    workflow_path: str = Field(..., description="Caminho do workflow em formato de interface.", min_length=1)
    overrides: List[str] = Field(
        ...,
        description="Pares ENDEREÇO=VALOR. Ex: ['6.text=uma raposa na neve', '3.seed=42']. "
        "Os endereços vêm de comfy_workflow_slots.",
        min_length=1,
        max_length=40,
    )
    in_place: bool = Field(
        default=False,
        description="True regrava o arquivo original. False, o padrão, devolve o "
        "resultado sem tocar no arquivo. Use True apenas em cópia de trabalho.",
    )


@_tool(
    name="comfy_set_workflow_slots",
    title="Sobrescrever slots de um workflow",
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_set_workflow_slots(params: SetSlotsInput) -> str:
    """Aplica sobrescritas de slot num workflow.

    Atenção ao parâmetro in_place: com True, o arquivo original é regravado
    e o conteúdo anterior se perde. O padrão é False, que devolve o
    resultado pela saída sem alterar o arquivo. Prefira False e grave numa
    cópia, principalmente se o workflow original foi trabalhoso de montar.

    Args:
        params (SetSlotsInput):
            - workflow_path (str): caminho do workflow
            - overrides (List[str]): pares ENDEREÇO=VALOR
            - in_place (bool): regravar o arquivo, padrão False

    Returns:
        str: envelope JSON com o workflow resultante ou a confirmação da
            gravação.
    """
    args = ["workflow", "set-slot", params.workflow_path, *params.overrides]
    args.append("--in-place" if params.in_place else "--stdout")
    return await _cli(args, timeout=120)


# ---------------------------------------------------------------------
# Arquivos
# ---------------------------------------------------------------------


class UploadInput(Base):
    files: List[str] = Field(
        ...,
        description="Caminhos locais a enviar para a pasta de input do ComfyUI. "
        "Necessário para workflows de imagem para imagem.",
        min_length=1,
        max_length=20,
    )
    overwrite: bool = Field(default=False, description="Sobrescrever arquivo de mesmo nome no servidor.")


@_tool(
    name="comfy_upload_files",
    title="Enviar arquivos para o input do ComfyUI",
    read_only=False,
    destructive=False,
    idempotent=False,
    open_world=False,
)
async def comfy_upload_files(params: UploadInput) -> str:
    """Envia arquivos locais para a pasta de input do servidor ComfyUI.

    Com overwrite=True, um arquivo de mesmo nome já presente no servidor é
    substituído e o anterior se perde. O padrão é False.

    Args:
        params (UploadInput):
            - files (List[str]): caminhos locais
            - overwrite (bool): sobrescrever no servidor, padrão False

    Returns:
        str: envelope JSON com os nomes gravados, que são os que devem ser
            referenciados nos nodes de carregamento de imagem.
    """
    # `upload` não aceita --host nem --port: resolve o servidor pelo
    # workspace ativo. Por isso não passa por _routing.
    args = ["upload", *params.files]
    if params.overwrite:
        args.append("--overwrite")
    return await _cli(args, timeout=LONG_TIMEOUT)


class PreviewInput(Base):
    file: str = Field(..., description="Arquivo de imagem, vídeo ou áudio.", min_length=1)
    out_path: Optional[str] = Field(
        default=None, description="PNG de saída. Padrão: <arquivo>.preview.png"
    )
    width: Optional[int] = Field(default=None, description="Largura do preview em pixels.", ge=64, le=4096)


@_tool(
    name="comfy_preview_media",
    title="Gerar PNG de prévia",
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_preview_media(params: PreviewInput) -> str:
    """Gera um PNG de prévia a partir de um arquivo de mídia.

    Imagem vira miniatura, vídeo vira folha de contato com vários quadros,
    áudio vira forma de onda. Serve para o agente conseguir ver o resultado
    de um vídeo, já que não dá para reproduzir vídeo no chat.

    Args:
        params (PreviewInput):
            - file (str): arquivo de mídia
            - out_path (Optional[str]): PNG de saída
            - width (Optional[int]): largura em pixels

    Returns:
        str: envelope JSON com o caminho do PNG gerado.
    """
    args = ["preview", params.file]
    _opt(args, "--out", params.out_path)
    _opt(args, "--width", params.width)
    return await _cli(args, timeout=600)


# ---------------------------------------------------------------------
# Construção de grafo: descoberta de ligações
# ---------------------------------------------------------------------


class ListNodesInput(Base):
    produces: Optional[str] = Field(
        default=None, description="Só nodes cuja saída inclui este tipo. Ex: MODEL, IMAGE, LATENT."
    )
    accepts: Optional[str] = Field(
        default=None, description="Só nodes com ao menos uma entrada deste tipo."
    )
    category: Optional[str] = Field(
        default=None, description="Glob na categoria. Ex: 'loaders*', 'sampling/*'."
    )
    pack: Optional[str] = Field(
        default=None, description="Filtrar por pacote de custom node. Ex: 'core', 'comfyui-impact-pack'."
    )
    output_only: bool = Field(default=False, description="Só nodes terminais de saída.")
    exclude_deprecated: bool = Field(default=False, description="Excluir nodes obsoletos.")
    limit: Optional[int] = Field(default=40, description="Teto de resultados.", ge=1, le=300)
    host: Optional[str] = Field(default=None, description="Host do ComfyUI.")
    port: Optional[int] = Field(default=None, description="Porta do ComfyUI.", ge=1, le=65535)


@_tool(
    name="comfy_list_nodes",
    title="Listar nodes por tipo de entrada ou saída",
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_list_nodes(params: ListNodesInput) -> str:
    """Lista classes de node filtrando por tipo produzido, tipo aceito, categoria ou pacote.

    Esta é a ferramenta de montagem de grafo. Enquanto comfy_search_nodes
    procura por nome, esta procura por forma: "quem produz LATENT",
    "quem aceita CONDITIONING". É assim que se descobre o que ligar onde.

    Args:
        params (ListNodesInput):
            - produces, accepts (Optional[str]): tipos de saída e entrada
            - category, pack (Optional[str]): filtros de origem
            - output_only, exclude_deprecated (bool): filtros booleanos
            - limit (Optional[int]): teto, padrão 40
            - host, port (Optional): endereço do servidor

    Returns:
        str: envelope JSON com as classes que casaram com os filtros.
    """
    args = ["nodes", "ls"]
    _opt(args, "--produces", params.produces)
    _opt(args, "--accepts", params.accepts)
    _opt(args, "--category", params.category)
    _opt(args, "--pack", params.pack)
    if params.output_only:
        args.append("--output-only")
    if params.exclude_deprecated:
        args.append("--exclude-deprecated")
    _opt(args, "--limit", params.limit)
    _routing(args, params.host, params.port)
    return await _cli(args, timeout=120)


class NeighborsInput(Base):
    name: str = Field(..., description="Nome exato da classe de node. Ex: 'KSampler'.", min_length=1)
    direction: str = Field(
        ...,
        description="'upstream' lista quem pode alimentar as entradas deste node. "
        "'downstream' lista quem aceita as saídas dele.",
        pattern="^(upstream|downstream)$",
    )
    limit: Optional[int] = Field(default=30, description="Teto de resultados.", ge=1, le=200)
    host: Optional[str] = Field(default=None, description="Host do ComfyUI.")
    port: Optional[int] = Field(default=None, description="Porta do ComfyUI.", ge=1, le=65535)


@_tool(
    name="comfy_node_neighbors",
    title="Vizinhos ligáveis de um node",
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_node_neighbors(params: NeighborsInput) -> str:
    """Lista o que pode se conectar a um node, para cima ou para baixo.

    Use ao montar um grafo passo a passo: parta de um node conhecido e
    caminhe pelas conexões possíveis em vez de adivinhar nomes de classe.

    Args:
        params (NeighborsInput):
            - name (str): classe de node de referência
            - direction (str): 'upstream' ou 'downstream'
            - limit (Optional[int]): teto, padrão 30
            - host, port (Optional): endereço do servidor

    Returns:
        str: envelope JSON com as classes ligáveis naquela direção.
    """
    args = ["nodes", params.direction, params.name]
    _opt(args, "--limit", params.limit)
    _routing(args, params.host, params.port)
    return await _cli(args, timeout=120)


class NodePathInput(Base):
    from_type: str = Field(..., description="Tipo de partida. Ex: MODEL.", min_length=1)
    to_type: str = Field(..., description="Tipo de destino. Ex: IMAGE.", min_length=1)
    max_depth: Optional[int] = Field(default=None, description="Comprimento máximo do caminho.", ge=1, le=12)
    max_paths: Optional[int] = Field(default=8, description="Quantidade máxima de caminhos.", ge=1, le=50)
    host: Optional[str] = Field(default=None, description="Host do ComfyUI.")
    port: Optional[int] = Field(default=None, description="Porta do ComfyUI.", ge=1, le=65535)


@_tool(
    name="comfy_node_path",
    title="Caminhos entre dois tipos",
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_node_path(params: NodePathInput) -> str:
    """Encontra sequências de nodes que levam de um tipo até outro.

    Responde "como saio de MODEL até IMAGE nesta instalação". É o atalho
    para esboçar a espinha de um workflow sem conhecer o ecossistema de
    nodes de cor.

    Args:
        params (NodePathInput):
            - from_type, to_type (str): tipos de partida e destino
            - max_depth, max_paths (Optional[int]): limites da busca
            - host, port (Optional): endereço do servidor

    Returns:
        str: envelope JSON com os caminhos roteados.
    """
    args = ["nodes", "path", params.from_type, params.to_type]
    _opt(args, "--max-depth", params.max_depth)
    _opt(args, "--max-paths", params.max_paths)
    _routing(args, params.host, params.port)
    return await _cli(args, timeout=180)


# ---------------------------------------------------------------------
# Construção de grafo: fragmentos e blueprints
# ---------------------------------------------------------------------


class ComposeInput(Base):
    blueprint_path: str = Field(
        ...,
        description="Arquivo YAML de blueprint que combina fragmentos.",
        min_length=1,
    )
    out_path: Optional[str] = Field(
        default=None,
        description="JSON de saída. Padrão: <blueprint>.compiled.json",
    )
    lib_dir: Optional[str] = Field(
        default=None, description="Diretório da biblioteca de fragmentos. Padrão: ./fragments"
    )


@_tool(
    name="comfy_compose_workflow",
    title="Compilar blueprint em workflow",
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_compose_workflow(params: ComposeInput) -> str:
    """Compila um blueprint YAML de fragmentos num único workflow em formato API.

    Este é o caminho padrão para workflow que você vai estender, variar ou
    reutilizar. Em vez de editar JSON gigante na mão, você escreve um
    blueprint pequeno e compila.

    Grava um arquivo. Se out_path já existir, o conteúdo anterior é
    substituído, então use um caminho novo se quiser preservar a versão
    anterior.

    Args:
        params (ComposeInput):
            - blueprint_path (str): YAML de entrada
            - out_path (Optional[str]): JSON de saída
            - lib_dir (Optional[str]): biblioteca de fragmentos

    Returns:
        str: envelope JSON com o caminho do workflow compilado.
    """
    args = ["workflow", "compose", params.blueprint_path]
    _opt(args, "--out", params.out_path)
    _opt(args, "--lib", params.lib_dir)
    return await _cli(args, timeout=300)


class DecomposeInput(Base):
    workflow_path: str = Field(..., description="Workflow JSON, formato API ou de interface.", min_length=1)
    name: Optional[str] = Field(default=None, description="Nome do fragmento. Padrão: nome do arquivo.")
    out_path: Optional[str] = Field(default=None, description="Caminho de saída. Padrão: <lib>/<nome>.json")
    lib_dir: Optional[str] = Field(default=None, description="Biblioteca de fragmentos. Padrão: ./fragments")
    host: Optional[str] = Field(default=None, description="Host do ComfyUI.")
    port: Optional[int] = Field(default=None, description="Porta do ComfyUI.", ge=1, le=65535)


@_tool(
    name="comfy_decompose_workflow",
    title="Projetar workflow em fragmento",
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_decompose_workflow(params: DecomposeInput) -> str:
    """Converte um workflow existente num fragmento reutilizável. Inverso de compose.

    Uso típico: baixe um template com comfy_fetch_template, projete em
    fragmento com esta ferramenta, e a partir daí monte blueprints próprios.
    É como se pega conhecimento de um workflow que funciona sem herdar o
    JSON inteiro.

    Grava um arquivo na biblioteca de fragmentos, substituindo um de mesmo
    nome se já existir.

    Args:
        params (DecomposeInput):
            - workflow_path (str): workflow de origem
            - name, out_path, lib_dir (Optional[str]): destino
            - host, port (Optional): endereço do servidor

    Returns:
        str: envelope JSON com o fragmento gerado e suas portas.
    """
    args = ["workflow", "decompose", params.workflow_path]
    _opt(args, "--name", params.name)
    _opt(args, "--out", params.out_path)
    _opt(args, "--lib", params.lib_dir)
    _routing(args, params.host, params.port)
    return await _cli(args, timeout=300)


class VaryInput(Base):
    workflow_path: str = Field(..., description="Workflow em formato de interface.", min_length=1)
    slots: List[str] = Field(
        ...,
        description="Uma entrada por slot, no formato ENDEREÇO=[\"v1\",\"v2\"]. "
        "As listas são combinadas em paralelo, então todas precisam do mesmo "
        "comprimento. Endereços vêm de comfy_workflow_slots.",
        min_length=1,
        max_length=10,
    )
    out_dir: Optional[str] = Field(
        default=None,
        description="Pasta para gravar cada variante. Sem ela, as variantes saem "
        "apenas na resposta, sem tocar em disco.",
    )
    host: Optional[str] = Field(default=None, description="Host do ComfyUI.")
    port: Optional[int] = Field(default=None, description="Porta do ComfyUI.", ge=1, le=65535)


@_tool(
    name="comfy_vary_workflow",
    title="Gerar variantes de um workflow",
    read_only=False,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_vary_workflow(params: VaryInput) -> str:
    """Produz N variantes de um workflow a partir de listas de valores por slot.

    Feito para lote: dez prompts, cinco seeds, três escalas. Gera os
    arquivos, depois submeta cada um com comfy_run_workflow e aguarde todos
    com comfy_job_wait.

    Com out_dir preenchido, grava um arquivo por variante nessa pasta.
    Arquivos de mesmo nome de um lote anterior são substituídos, então use
    uma pasta por lote.

    Args:
        params (VaryInput):
            - workflow_path (str): workflow base
            - slots (List[str]): listas de valores por endereço
            - out_dir (Optional[str]): pasta de saída
            - host, port (Optional): endereço do servidor

    Returns:
        str: envelope JSON com as variantes geradas.
    """
    args = ["workflow", "vary", params.workflow_path]
    for slot in params.slots:
        args.extend(["--slot", slot])
    _opt(args, "--out-dir", params.out_dir)
    _routing(args, params.host, params.port)
    return await _cli(args, timeout=300)


class FragmentsInput(Base):
    lib_dir: Optional[str] = Field(
        default=None, description="Diretório da biblioteca. Padrão: ./fragments"
    )


@_tool(
    name="comfy_list_fragments",
    title="Listar fragmentos da biblioteca",
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_list_fragments(params: FragmentsInput) -> str:
    """Lista os fragmentos já existentes na biblioteca do projeto.

    Consulte antes de decompor um template novo: talvez o fragmento de que
    você precisa já tenha sido criado numa sessão anterior.

    Args:
        params (FragmentsInput):
            - lib_dir (Optional[str]): diretório da biblioteca

    Returns:
        str: envelope JSON com os fragmentos disponíveis.
    """
    args = ["workflow", "fragment", "ls"]
    _opt(args, "--lib", params.lib_dir)
    return await _cli(args, timeout=120)


# ---------------------------------------------------------------------
# Metodologia
# ---------------------------------------------------------------------


class ComfySkillInput(Base):
    name: str = Field(
        default="comfy",
        description="Qual skill imprimir: comfy (geral), comfy-fragments (montar "
        "grafos grandes), comfy-debug (job que falhou), comfy-relay (apresentar "
        "resultado), comfy-director (vídeo narrativo multi cena).",
        pattern="^(comfy|comfy-fragments|comfy-debug|comfy-relay|comfy-director)$",
    )


@_tool(
    name="comfy_read_methodology",
    title="Ler a metodologia oficial do Comfy",
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False,
)
async def comfy_read_methodology(params: ComfySkillInput) -> str:
    """Imprime uma das skills de metodologia que vêm embutidas no comfy-cli.

    Conteúdo extenso, milhares de palavras. Carregue sob demanda, não por
    precaução. Quando usar cada uma:

    - comfy: visão geral, contrato de saída, como escolher entre template,
      fragmento e JSON cru. Leia antes de um trabalho grande.
    - comfy-fragments: montar grafos grandes a partir de peças validadas.
    - comfy-debug: um job falhou e você precisa do mapa de erro para correção.
    - comfy-relay: como apresentar imagem e vídeo no chat.
    - comfy-director: vídeo narrativo de várias cenas, continuidade.

    Args:
        params (ComfySkillInput):
            - name (str): qual skill imprimir, padrão 'comfy'

    Returns:
        str: o texto da skill. Não é envelope JSON, é markdown cru.
    """
    out = await _cli_text(["skills", "show", params.name], timeout=120, cap=200000)
    # A CLI devolve o markdown dentro de data.content. Entregar o texto
    # limpo, e não o envelope com quebras de linha escapadas, torna o
    # conteúdo muito mais legível para quem vai seguir a metodologia.
    envelope = _parse_envelope(out)
    if envelope and isinstance(envelope.get("data"), dict):
        content = envelope["data"].get("content")
        if isinstance(content, str) and content.strip():
            return _truncate(content, 120000)
    return _truncate(out, 120000)


# ---------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
