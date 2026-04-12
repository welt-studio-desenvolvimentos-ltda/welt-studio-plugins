---
description: List all configured hookify rules
allowed-tools: ["Glob", "Read", "Skill"]
---

# List Hookify Rules

**Load hookify:writing-rules skill first** to understand rule format.

Show all configured hookify rules from both project and global scopes.

## Steps

1. Use Glob tool to find hookify rule files in **both** locations:
   - **Project rules**: `.claude/hookify.*.local.md` (current directory)
   - **Global rules**: `~/.claude/hookify.*.local.md` (home directory)

   Run two Glob searches:
   ```
   pattern: ".claude/hookify.*.local.md"
   ```
   ```
   pattern: "~/.claude/hookify.*.local.md"
   path: home directory (use $HOME or expand ~)
   ```

2. For each file found:
   - Use Read tool to read the file
   - Extract frontmatter fields: name, enabled, event, pattern
   - Extract message preview (first 100 chars)

3. Present results in a table:

```
## Configured Hookify Rules

| Name | Enabled | Event | Pattern | Scope | File |
|------|---------|-------|---------|-------|------|
| warn-dangerous-rm | ✅ Yes | bash | rm\s+-rf | Project | .claude/hookify.dangerous-rm.local.md |
| warn-console-log | ✅ Yes | file | console\.log\( | Global | ~/.claude/hookify.console-log.local.md |
| check-tests | ❌ No | stop | .* | Project | .claude/hookify.require-tests.local.md |

**Total**: 3 rules (2 enabled, 1 disabled)
**Searched**: `.claude/` (project) and `~/.claude/` (global)
```

4. For each rule, show a brief preview:
```
### warn-dangerous-rm
**Event**: bash
**Pattern**: `rm\s+-rf`
**Message**: "⚠️ **Dangerous rm command detected!** This command could delete..."

**Status**: ✅ Active
**File**: .claude/hookify.dangerous-rm.local.md
```

5. Add helpful footer:
```
---

To modify a rule: Edit the .local.md file directly
To disable a rule: Set `enabled: false` in frontmatter
To enable a rule: Set `enabled: true` in frontmatter
To delete a rule: Remove the .local.md file
To create a rule: Use `/hookify` command

**Searched locations**:
- Project: .claude/hookify.*.local.md
- Global: ~/.claude/hookify.*.local.md

**Remember**: Changes take effect immediately - no restart needed
```

## If No Rules Found

If no hookify rules exist in **either** location:

```
## No Hookify Rules Configured

You haven't created any hookify rules yet.

**Searched locations**:
- Project: .claude/hookify.*.local.md (not found)
- Global: ~/.claude/hookify.*.local.md (not found)

To get started:
1. Use `/hookify` to analyze conversation and create rules
2. Or manually create rule files:
   - Project-only: `.claude/hookify.<name>.local.md`
   - All projects: `~/.claude/hookify.<name>.local.md`
3. See `/hookify:help` for documentation

Example:
```
/hookify Warn me when I use console.log
```

Check `${CLAUDE_PLUGIN_ROOT}/examples/` for example rule files.
```
