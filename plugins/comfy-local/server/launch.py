"""
launch.py

Ponto de entrada do servidor MCP do plugin comfy-local.

Este arquivo usa apenas a biblioteca padrão, de propósito. Ele roda com o
Python que o usuário apontou na configuração do plugin, que pode não ter
dependência nenhuma instalada. A tarefa dele é garantir o ambiente e só
então executar o servidor de verdade.

O que faz:
  1. Localiza o diretório persistente do plugin (CLAUDE_PLUGIN_DATA), que
     sobrevive a atualizações do plugin.
  2. Cria um venv lá dentro se não existir.
  3. Instala as dependências se o requirements.txt empacotado for diferente
     da cópia guardada, o que cobre tanto a primeira execução quanto uma
     atualização que mude dependências.
  4. Substitui o processo pelo servidor rodando com o Python do venv.

Não apaga nem sobrescreve nada fora do próprio diretório de dados do
plugin, que o Claude Code cria e gerencia.

Regra crítica: stdout pertence ao protocolo MCP. Todo diagnóstico daqui vai
para stderr. Uma única linha solta no stdout quebra o handshake.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# Teto de espera pela sessão que está provisionando primeiro. Instalar o SDK
# do MCP num venv novo leva segundos; três minutos cobre uma rede ruim.
LOCK_TIMEOUT_SECONDS = 180

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
REQUIREMENTS = PLUGIN_ROOT / "requirements.txt"
SERVER = HERE / "comfy_mcp_server.py"


def log(msg: str) -> None:
    """Diagnóstico vai para stderr. Nunca para stdout."""
    print(f"[comfy-local] {msg}", file=sys.stderr, flush=True)


def data_dir() -> Path:
    """Diretório persistente do plugin, com alternativa local se ausente."""
    env = os.environ.get("CLAUDE_PLUGIN_DATA")
    if env:
        d = Path(env)
    else:
        # Fora do Claude Code, por exemplo em teste manual.
        d = PLUGIN_ROOT / ".local-data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def needs_install(venv: Path, stamp: Path) -> bool:
    """True se o venv não existe ou se as dependências mudaram."""
    if not venv_python(venv).exists():
        return True
    if not stamp.exists():
        return True
    try:
        return stamp.read_text(encoding="utf-8") != REQUIREMENTS.read_text(encoding="utf-8")
    except OSError:
        return True


def acquire_lock(lock: Path, venv: Path, stamp: Path) -> bool:
    """Toma a trava de provisionamento, ou espera quem já a tem.

    Duas janelas do Claude Code abertas logo depois de habilitar o plugin
    disparam dois provisionamentos no mesmo diretório de dados, e o perdedor
    pode deixar uma instalação pela metade que o carimbo depois declara boa.

    Devolve True se esta sessão deve provisionar, e False se outra já
    terminou o serviço enquanto esperávamos.
    """
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    avisou = False
    while True:
        try:
            os.close(os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            if not needs_install(venv, stamp):
                return False  # a outra sessão terminou; nada a fazer

            if time.monotonic() >= deadline:
                # Trava abandonada por uma sessão que morreu no meio. Assume
                # o serviço em vez de deixar o plugin travado para sempre.
                log("trava de provisionamento velha demais, assumindo")
                return True

            if not avisou:
                log("outra sessão está preparando o ambiente, aguardando")
                avisou = True
            time.sleep(1)
            continue

        # Com a trava na mão, confere de novo: entre a última checagem e
        # agora, quem a soltou pode ter acabado de deixar tudo pronto.
        if needs_install(venv, stamp):
            return True
        release_lock(lock)
        return False


def release_lock(lock: Path) -> None:
    try:
        lock.unlink()
    except OSError:
        pass


def provision(venv: Path, stamp: Path) -> None:
    """Cria o venv e instala as dependências."""
    py = venv_python(venv)

    if not py.exists():
        log(f"criando venv em {venv}")
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    log("instalando dependências")
    subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", "-r", str(REQUIREMENTS)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # A marca só é gravada depois do sucesso. Se a instalação falhar, a
    # próxima sessão tenta de novo em vez de assumir ambiente pronto.
    stamp.write_text(REQUIREMENTS.read_text(encoding="utf-8"), encoding="utf-8")
    log("ambiente pronto")


def main() -> None:
    if not SERVER.exists():
        log(f"ERRO: servidor não encontrado em {SERVER}")
        sys.exit(1)
    if not REQUIREMENTS.exists():
        log(f"ERRO: requirements.txt não encontrado em {REQUIREMENTS}")
        sys.exit(1)

    data = data_dir()
    venv = data / "venv"
    stamp = data / "requirements.installed.txt"
    lock = data / "provision.lock"

    if needs_install(venv, stamp):
        if acquire_lock(lock, venv, stamp):
            try:
                provision(venv, stamp)
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or b"").decode("utf-8", errors="replace")[:1500]
                log("ERRO: falha ao preparar o ambiente Python.")
                log(f"comando: {' '.join(str(a) for a in exc.cmd)}")
                log(detail)
                log(
                    "Verifique se o Python configurado no plugin consegue criar venv "
                    "e alcançar o PyPI."
                )
                sys.exit(1)
            finally:
                release_lock(lock)

    py = venv_python(venv)
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PYTHONPATH", None)

    # No Windows não existe execv real; usa subprocesso e propaga o código.
    if os.name == "nt":
        proc = subprocess.run([str(py), str(SERVER)], env=env)
        sys.exit(proc.returncode)

    os.execve(str(py), [str(py), str(SERVER)], env)


if __name__ == "__main__":
    main()
