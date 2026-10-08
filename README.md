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
| `preflight` | 0.1.0 | Catches at write time what `/code-review` would catch later: blocks file edits through Bash inside any git repository, runs the repo pre-commit on each edit and at end of turn, flags leftovers of removed identifiers, and feeds the project's recurring findings into the system prompt |

## Installation

One line per plugin, typed at the prompt of a terminal Claude Code session:

```
/plugin install typescript-lsp --marketplace welt-studio-desenvolvimentos-ltda/welt-studio-plugins
/plugin install python-lsp --marketplace welt-studio-desenvolvimentos-ltda/welt-studio-plugins
/plugin install hookify --marketplace welt-studio-desenvolvimentos-ltda/welt-studio-plugins
/plugin install spec-gate --marketplace welt-studio-desenvolvimentos-ltda/welt-studio-plugins
/plugin install comfy-local --marketplace welt-studio-desenvolvimentos-ltda/welt-studio-plugins
/plugin install preflight --marketplace welt-studio-desenvolvimentos-ltda/welt-studio-plugins
```

The first one asks to add the marketplace (`y`), then for a scope (Enter picks the user scope,
which applies to every session). From a local clone, pass its path instead:
`--marketplace /path/to/welt-studio-plugins`.

## Prerequisites

Only some plugins need anything installed. `hookify` and `spec-gate`
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
