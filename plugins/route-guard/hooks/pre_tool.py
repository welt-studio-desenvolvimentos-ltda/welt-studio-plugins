#!/usr/bin/env python3
"""Hook PreToolUse do route-guard. A decisão mora em core.guard.pre_tool."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import guard, hook_io  # noqa: E402

if __name__ == "__main__":
    hook_io.run(guard.pre_tool)
