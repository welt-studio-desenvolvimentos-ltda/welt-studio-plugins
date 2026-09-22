#!/usr/bin/env python3
"""Hook Stop do route-guard. A decisão mora em core.guard.stop."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import guard, hook_io  # noqa: E402

if __name__ == "__main__":
    hook_io.run(guard.stop)
