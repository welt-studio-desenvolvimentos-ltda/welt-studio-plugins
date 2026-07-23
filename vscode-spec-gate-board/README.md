# spec-gate Board (extensão companheira do VS Code)

Janela FIXA e dockável no VS Code mostrando o board do lote spec-gate ao vivo,
sem servidor, sem polling: a extensão vigia `.specgate/batch.json` com fs.watch
e empurra o estado pro webview a cada mudança.

Isto é uma extensão VS Code comum, não um plugin de Claude Code: a API de
plugins do Claude Code não tem superfície de UI, então janela fixa só por aqui.

## Rodar em desenvolvimento (sem empacotar)

1. Abra esta pasta (`vscode-spec-gate-board/`) no VS Code
2. F5 (Run Extension) abre um Extension Development Host
3. No host, abra a pasta do seu projeto que tem `.specgate.json`
4. O board abre sozinho ao lado (ou Ctrl+Shift+P, "spec-gate: Show Board")

## Empacotar e instalar de verdade

```bash
npm install -g @vscode/vsce
cd vscode-spec-gate-board
vsce package
code --install-extension spec-gate-board-0.1.0.vsix
```

## Comportamento

- Auto-abre quando o workspace contém `.specgate.json`
- Dockável em qualquer lugar do layout (arraste a aba), sobrevive a hide/show
  (`retainContextWhenHidden`)
- Mesmo visual do dashboard de navegador: projeto, barra de progresso, itens
  com status colorido, seção "Perguntas aguardando o PO"
- Zero dependências de runtime, só a API do VS Code e Node builtin
