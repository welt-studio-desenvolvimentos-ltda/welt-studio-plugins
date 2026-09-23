"""Ponte entre o Claude Code e `core.guard`: lê o JSON do stdin, emite o resultado, nunca derruba o Claude."""

import io
import json
import sys


def use_utf8_streams():
    """O Claude Code troca JSON e texto em UTF-8; no Windows o padrão dos streams é cp1252.

    Sem isto, um caminho ou título com acento vira mojibake na entrada e quebra a saída.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def run(decide):
    """Executa `decide(inp, data_dir)`. Erro interno é fail-open: exit 0 com aviso, como no hookify."""
    try:
        use_utf8_streams()
        data_dir = sys.argv[1] if len(sys.argv) > 1 else ""
        if not data_dir or data_dir.startswith("${"):
            raise RuntimeError("plugin data dir was not passed to the hook")
        inp = json.load(sys.stdin)
        result = decide(inp, data_dir)
    except Exception as exc:  # noqa: BLE001 — fail-open por contrato
        print(json.dumps({"systemMessage": "route-guard error (guard skipped): {}".format(exc)}))
        sys.exit(0)
    if result.stdout is not None:
        print(json.dumps(result.stdout))
    if result.stderr:
        sys.stderr.write(result.stderr)
    sys.exit(result.exit_code)
