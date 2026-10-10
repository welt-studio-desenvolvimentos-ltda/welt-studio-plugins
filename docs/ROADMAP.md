# Roadmap

Features e evoluções planejadas. Diferente de `TECH_DEBT.md` (problemas em código existente), aqui registramos o que queremos construir.

## game-art

### [game-art] Suporte a Windows nativo

- [ ] **Prioridade**: Baixa · `2026-10-10`
- **Trigger**: Decisão do PO na v0.2: focar em WSL/Linux e fazer o Windows numa slice própria quando for necessário.
- **Descrição**: O mod só roda em Linux/WSL. No Windows nativo ele quebra em quatro pontos:
  - caminhos são montados com `/` e só o que começa com `/` conta como absoluto;
  - o Codex instalado via npm é um `codex.cmd`, que `$.process.run` por argv, sem shell, pode não achar;
  - o `uv run` usa o mesmo argv;
  - abrir o preview depende de `explorer.exe`/`xdg-open`.
- **Mod (estimado)**:
  - Caminhos em `hooks/lib/engine.ts`: aceitar `C:\` e `\`, e `joinPath`/`parentOf` cientes da plataforma.
  - Resolver o executável do Codex por plataforma (`codex.cmd`, ou `cmd /c`).
  - `open` via `cmd /c start ""`.
- **Acceptance**:
  - Numa sessão do Claude Code no Windows nativo, `generate_asset` gera, processa e grava o PNG num projeto Godot/Unity/Unreal.
  - `open: true` abre o PNG no visualizador do Windows.

### [game-art] Spritesheet e animação (v2)

- [ ] **Prioridade**: Média · `2026-10-10`
- **Trigger**: Ficou fora do escopo da v1 por decisão do PO. Os modelos são o `sprite-pipeline` do plugin game-studio do Codex e o `tools/art/sprite_tool.py` do kingdom-redux.
- **Descrição**: Gerar animações consistentes a partir de um seed aprovado. Toda a tira de N frames sai numa única edição, nunca frame a frame, porque frame a frame o personagem deriva.
- **Mod (estimado)**:
  - Seed aprovado em `.game-art/` como referência obrigatória.
  - Prompt de tira com as invariantes de personagem, direção, paleta e contagem de frames.
  - `process_image.py`: separar as figuras, aplicar uma escala única para a tira, ancorar no centro-baixo e gerar um preview ampliado.
  - Saída por engine: frames numerados (Godot `SpriteFrames`), sheet em grade (Unity Sprite Editor) ou flipbook (Unreal Paper2D).
- **Acceptance**:
  - Uma chamada gera a tira de N frames de uma animação a partir do seed.
  - Os frames têm o mesmo tamanho, os pés alinhados e o personagem reconhecível em todos.
  - O resultado abre na engine sem ajuste manual de escala.
