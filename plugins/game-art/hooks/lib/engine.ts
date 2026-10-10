// Acha o projeto de jogo acima do diretório da sessão e diz onde a arte dele mora.

import type { Host } from './host'

export const ENGINES = ['godot', 'unity', 'unreal', 'none'] as const
export type Engine = (typeof ENGINES)[number]

export type Project = { engine: Engine; root: string }

/** Pasta da arte, relativa à raiz do projeto. Unreal fica fora de Content/: PNG só entra lá importado. */
export const ART_DIRS: Record<Engine, string> = {
  godot: 'assets',
  unity: 'Assets/Art',
  unreal: 'SourceArt',
  none: 'art',
}

export const ENGINE_LABELS: Record<Engine, string> = {
  godot: 'Godot',
  unity: 'Unity',
  unreal: 'Unreal Engine',
  none: 'video',
}

export function parentOf(path: string): string | undefined {
  const trimmed = path.replace(/\/+$/, '')
  const cut = trimmed.lastIndexOf('/')
  if (cut < 0 || trimmed === '') return undefined
  return cut === 0 ? '/' : trimmed.slice(0, cut)
}

export function joinPath(...parts: readonly string[]): string {
  return parts
    .filter(part => part !== '')
    .map((part, index) => (index === 0 ? part.replace(/\/+$/, '') : part.replace(/^\/+|\/+$/g, '')))
    .join('/')
}

async function engineAt(host: Host, dir: string): Promise<Engine | undefined> {
  if (await host.exists(joinPath(dir, 'project.godot'))) return 'godot'
  if ((await host.list(dir)).some(name => name.endsWith('.uproject'))) return 'unreal'
  if ((await host.exists(joinPath(dir, 'Assets'))) && (await host.exists(joinPath(dir, 'ProjectSettings/ProjectVersion.txt')))) return 'unity'
  return undefined
}

/** O projeto mais próximo subindo de `cwd`; sem nenhum, a própria `cwd` com engine `none`. */
export async function findProject(host: Host, cwd: string): Promise<Project> {
  for (let dir: string | undefined = cwd; dir !== undefined; dir = parentOf(dir)) {
    const engine = await engineAt(host, dir)
    if (engine) return { engine, root: dir }
  }
  return { engine: 'none', root: cwd }
}

/** O que fazer na engine depois que o PNG chega. */
export function engineNote(engine: Engine, pixelArt: boolean): string {
  switch (engine) {
    case 'godot':
      return [
        'Godot creates the .import file the next time the editor gains focus (or with `godot --headless --import`).',
        pixelArt ? 'For pixel art set the texture filter to Nearest (Project Settings > Rendering > Textures > Default Texture Filter, or the node\'s texture_filter).' : '',
      ].join(' ').trim()
    case 'unity':
      return [
        'Unity creates the .meta when the editor imports the file; for 2D set Texture Type to Sprite (2D and UI).',
        pixelArt ? 'For pixel art set Filter Mode to Point (no filter) and Compression to None.' : '',
      ].join(' ').trim()
    case 'unreal':
      return [
        'The PNG is outside Content/: import it from the Content Browser (Import, or drag it in) into the matching Content/ folder.',
        pixelArt ? 'For pixel art set the texture\'s Texture Group to 2D Pixels (unfiltered) and Compression to UserInterface2D.' : '',
      ].join(' ').trim()
    case 'none':
      return 'No Godot, Unity or Unreal project was found above the session directory; nothing engine-specific applies.'
  }
}
