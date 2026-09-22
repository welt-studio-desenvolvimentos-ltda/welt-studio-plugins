#!/usr/bin/env python3
"""Hook PostToolUse(ExitPlanMode) do route-guard. A decisão mora em core.guard.on_plan_approved."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import guard, hook_io  # noqa: E402

if __name__ == "__main__":
    hook_io.run(guard.on_plan_approved)
