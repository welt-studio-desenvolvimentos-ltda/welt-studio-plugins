"""Ponte entre o Claude Code e `core.guard`: lê o JSON do stdin, emite o resultado, nunca derruba o Claude."""

import json
import sys


def run(decide):
    """Executa `decide(inp, data_dir)`. Erro interno é fail-open: exit 0 com aviso, como no hookify."""
    try:
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
