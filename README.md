# Welt Studio Plugins Marketplace

A collection of Claude Code plugins for enhanced development experience.

## Available Plugins

| Plugin | Description |
|--------|-------------|
| `typescript-lsp` | TypeScript/JavaScript Language Server integration |
| `python-lsp` | Python Language Server integration (Pyright) |

## Installation

### Add the Marketplace

```bash
# If hosted on GitHub
/plugin marketplace add welt-studio/welt-studio-plugins

# Or from local path
/plugin marketplace add ~/.claude/plugins/repos/welt-studio-plugins
```

### Install Plugins

```bash
# Install TypeScript LSP
/plugin install typescript-lsp@welt-studio-plugins

# Install Python LSP
/plugin install python-lsp@welt-studio-plugins
```

## Prerequisites

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

## Plugin Development

Each plugin follows the standard Claude Code plugin structure:

```
plugins/<plugin-name>/
├── .claude-plugin/
│   └── plugin.json    # Plugin metadata
├── .lsp.json          # LSP configuration (for LSP plugins)
└── README.md          # Documentation
```

## Author

Welt Studio Desenvolvimentos LTDA

## License

MIT
