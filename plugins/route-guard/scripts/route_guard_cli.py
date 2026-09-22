#!/usr/bin/env python3
"""Entrada dos comandos /route-guard:*. A lógica mora em core.commands.

Uso: route_guard_cli.py <approve|status|off> '<CLAUDE_PLUGIN_DATA>' '<CLAUDE_SESSION_ID>'
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import commands  # noqa: E402

if __name__ == "__main__":
    sys.exit(commands.main(sys.argv))
