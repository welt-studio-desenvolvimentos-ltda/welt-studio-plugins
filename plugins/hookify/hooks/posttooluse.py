#!/usr/bin/env python3
"""PostToolUse hook executor for hookify plugin.

This script is called by Claude Code after a tool executes.
It reads hookify.*.local.md from .claude/hookify/ and hookify.*.global.md from ~/.claude/hookify/ and evaluates rules.
"""

import os
import sys
import json

# CRITICAL: Add plugin root to Python path for imports
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
if PLUGIN_ROOT and PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)

try:
    from core.config_loader import load_rules
    from core.rule_engine import RuleEngine, is_blocking_result, TOOL_EVENT_MAP
except ImportError as e:
    error_msg = {"systemMessage": f"Hookify import error: {e}"}
    print(json.dumps(error_msg), file=sys.stdout)
    sys.exit(0)


def main():
    """Main entry point for PostToolUse hook."""
    exit_code = 0
    try:
        input_data = json.load(sys.stdin)
        hook_event = input_data.get('hook_event_name', '')
        tool_name = input_data.get('tool_name', '')
        event = TOOL_EVENT_MAP.get(tool_name)

        # Load rules
        rules = load_rules(event=event)

        # Evaluate rules
        engine = RuleEngine()
        result = engine.evaluate_rules(rules, input_data)

        # Route output: blocking → stderr + exit 2, otherwise → stdout + exit 0
        if result:
            if is_blocking_result(result, hook_event):
                print(json.dumps(result), file=sys.stderr)
                exit_code = 2
            else:
                print(json.dumps(result), file=sys.stdout)

    except Exception as e:
        error_output = {
            "systemMessage": f"Hookify error: {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    sys.exit(exit_code)


if __name__ == '__main__':
    main()
