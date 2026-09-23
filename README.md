# Welt Studio Plugins Marketplace

A collection of Claude Code plugins for enhanced development experience.

## Available Plugins

| Plugin | Version | Description |
|--------|---------|-------------|
| `typescript-lsp` | 1.0.0 | TypeScript/JavaScript Language Server integration |
| `python-lsp` | 1.0.0 | Python Language Server integration (Pyright) |
| `hookify` | 0.1.4 | User-configurable hooks from Markdown rule files (patched fork) |
| `spec-gate` | 0.3.0 | Spec-driven flow gated by the product owner: six named phases and six human decision gates |
| `comfy-local` | 0.2.0 | Drives a local ComfyUI over MCP: introspection, workflow building, execution, job and VRAM diagnostics, and output collection |
| `auditoria` | 1.2.0 | Adversarial auditor: verifies claims independently and questions choices, with a severity-graded verdict |
| `code-review-legacy` | 0.8.0 | `/code-review-legacy [level]`: the `/code-review` recipes with subagent fan-out at every level (Sonnet 5 cells of Claude Code 2.1.278), run through the built-in's fork path on a base agent without the no-re-delegation directive; the fork waits for its finders and returns only the final report |
| `route-guard` | 0.1.1 | Implementation watcher: the approved plan becomes a route of steps with scope, done criteria and dependencies; hooks block out-of-scope edits before they happen, accept a step only when its criteria pass in a script, block stopping with open steps, and escalate after three failures |

## Installation

### Add the Marketplace

```bash
# From GitHub
/plugin marketplace add welt-studio-desenvolvimentos-ltda/welt-studio-plugins

# Or from a local clone
/plugin marketplace add /path/to/welt-studio-plugins
```

### Install Plugins

```bash
/plugin install typescript-lsp@welt-studio-plugins
/plugin install python-lsp@welt-studio-plugins
/plugin install hookify@welt-studio-plugins
/plugin install spec-gate@welt-studio-plugins
/plugin install comfy-local@welt-studio-plugins
/plugin install auditoria@welt-studio-plugins
/plugin install code-review-legacy@welt-studio-plugins
/plugin install route-guard@welt-studio-plugins
```

## Prerequisites

Only some plugins need anything installed. `hookify`, `spec-gate`, `auditoria` and `code-review-legacy`
run on what Claude Code already provides.

### TypeScript LSP

```bash
npm install -g typescript-language-server typescript
```

### Python LSP

```bash
pip install pyright
# or
npm install -g pyright
```

### comfy-local

Requires `comfy-cli` in its own virtualenv and a ComfyUI workspace. The plugin installs
disabled on purpose, since it connects to an external service. See
[`plugins/comfy-local/README.md`](plugins/comfy-local/README.md).

### auditoria

No dependency to install, but the README documents one manual step: a permission rule in your
`~/.claude/settings.json` so Claude asks before dispatching the auditor on its own. See
[`plugins/auditoria/README.md`](plugins/auditoria/README.md).

## Plugin Development

Every plugin lives in `plugins/<name>/` and is registered in
[`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json). Only `plugin.json` is
required; the rest is whatever the plugin actually ships:

```
plugins/<plugin-name>/
├── .claude-plugin/
│   └── plugin.json     # Required: identity (name, version, description, author)
├── .lsp.json           # LSP server configuration
├── hooks/hooks.json    # Hook event wiring
├── commands/*.md       # Slash commands
├── agents/*.md         # Subagent definitions
├── skills/*/SKILL.md   # Skills
└── README.md           # Documentation
```

Directory conventions are picked up automatically — declaring `agents`, `skills` or `hooks` in
`plugin.json` is optional, and the `agents` field takes file paths rather than a directory.

Validate before committing:

```bash
claude plugin validate plugins/<name>     # a single plugin
claude plugin validate .                  # the marketplace and every entry
```

## Author

Welt Studio Desenvolvimentos LTDA

## License

MIT
