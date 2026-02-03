# Python LSP Plugin

Language Server integration for Python using Pyright in Claude Code.

## Supported Extensions

- `.py` - Python source files
- `.pyi` - Python stub files

## Prerequisites

Install Pyright:

```bash
# Via pip
pip install pyright

# Or via npm
npm install -g pyright
```

## Features

- Go to definition
- Find references
- Hover documentation
- Type checking diagnostics
- Code completion
- Symbol search

## Installation

Via marketplace:

```bash
/plugin marketplace add welt-studio/welt-studio-plugins
/plugin install python-lsp@welt-studio-plugins
```

Or directly:

```bash
claude --plugin-dir ~/.claude/plugins/repos/welt-studio-plugins/plugins/python-lsp
```

## Author

Welt Studio Desenvolvimentos LTDA
