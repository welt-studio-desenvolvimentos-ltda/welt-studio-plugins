const vscode = require("vscode");
const fs = require("fs");
const path = require("path");

let panel = null;
let watchers = [];

function root() {
  const ws = vscode.workspace.workspaceFolders;
  return ws && ws.length ? ws[0].uri.fsPath : null;
}

function readState(dir) {
  const out = { config: null, batch: null, events: [] };
  try { out.config = JSON.parse(fs.readFileSync(path.join(dir, ".specgate.json"), "utf8")); } catch (e) {}
  try { out.batch = JSON.parse(fs.readFileSync(path.join(dir, ".specgate", "batch.json"), "utf8")); } catch (e) {}
  try {
    const raw = fs.readFileSync(path.join(dir, ".specgate", "events.jsonl"), "utf8");
    out.events = raw.trim().split("\n").slice(-80).map(l => { try { return JSON.parse(l); } catch (e) { return null; } }).filter(Boolean).reverse();
  } catch (e) {}
  return out;
}

function push(dir) {
  if (panel) panel.webview.postMessage(readState(dir));
}

function watch(dir) {
  watchers.forEach(w => { try { w.close ? w.close() : fs.unwatchFile(w); } catch (e) {} });
  watchers = [];
  const targets = [path.join(dir, ".specgate"), path.join(dir, ".specgate.json")];
  for (const t of targets) {
    try {
      const w = fs.watch(t, { persistent: false }, () => push(dir));
      watchers.push(w);
    } catch (e) {
      // diretorio ainda nao existe: poll leve ate existir
      fs.watchFile(t, { interval: 2000 }, () => { push(dir); watch(dir); });
      watchers.push(t);
    }
  }
}

function showBoard(context) {
  const dir = root();
  if (!dir) { vscode.window.showWarningMessage("spec-gate: abra uma pasta de projeto primeiro."); return; }
  if (panel) { panel.reveal(); push(dir); return; }

  panel = vscode.window.createWebviewPanel(
    "specgateBoard", "spec-gate Board",
    { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
    { enableScripts: true, retainContextWhenHidden: true }
  );
  const htmlPath = path.join(context.extensionPath, "media", "board.html");
  panel.webview.html = fs.readFileSync(htmlPath, "utf8");
  panel.onDidDispose(() => { panel = null; });
  watch(dir);
  setTimeout(() => push(dir), 300);
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("specgate.showBoard", () => showBoard(context))
  );
  // auto-abre quando o workspace usa spec-gate
  const dir = root();
  if (dir && fs.existsSync(path.join(dir, ".specgate.json"))) showBoard(context);
}

function deactivate() {
  watchers.forEach(w => { try { w.close ? w.close() : fs.unwatchFile(w); } catch (e) {} });
}

module.exports = { activate, deactivate };
