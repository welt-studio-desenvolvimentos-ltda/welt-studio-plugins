# TypeScript LSP Plugin

Language Server integration for TypeScript and JavaScript in Claude Code.

## Supported Extensions

- `.ts` - TypeScript
- `.tsx` - TypeScript React
- `.js` - JavaScript
- `.jsx` - JavaScript React
- `.mts`, `.cts` - TypeScript modules
- `.mjs`, `.cjs` - JavaScript modules

## Prerequisites

Install the TypeScript Language Server:

```bash
npm install -g typescript-language-server typescript
```

## Features

- Go to definition
- Find references
- Hover documentation
- Diagnostics
- Code completion
- Symbol search

## Installation

Via marketplace:

```bash
/plugin marketplace add welt-studio/welt-studio-plugins
/plugin install typescript-lsp@welt-studio-plugins
```

Or directly:

```bash
claude --plugin-dir ~/.claude/plugins/repos/welt-studio-plugins/plugins/typescript-lsp
```

## Author

Welt Studio Desenvolvimentos LTDA
