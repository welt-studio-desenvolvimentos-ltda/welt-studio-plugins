// A pasta de trabalho na raiz do projeto: guia de estilo (versionado) e os runs de geração (fora do git e da engine).

import { joinPath } from './engine'
import type { Host } from './host'

export const WORKDIR = '.game-art'
export const RUNS_DIR = 'runs'
export const STYLE_FILE = 'style.json'
/** O registro do run e o prompt enviado. */
export const RUN_FILE = 'run.json'
/** O que do run foi gravado no projeto (a galeria lê). */
export const SAVED_FILE = 'saved.json'
export const PROMPT_FILE = 'prompt.txt'

/** As variações de um run; uma geração simples é a variação `a`. */
export const VARIANTS = ['a', 'b', 'c'] as const
export type Variant = (typeof VARIANTS)[number]

/** Por variação: o bruto do Codex, os eventos dele, a miniatura que o modelo vê e, com várias, a versão processada. */
export const rawFile = (variant: Variant) => `raw-${variant}.png`
export const eventsFile = (variant: Variant) => `codex-${variant}.jsonl`
export const previewFile = (variant: Variant) => `preview-${variant}.png`
export const finalFile = (variant: Variant) => `final-${variant}.png`
/** Os pixels da miniatura do painel. */
export const thumbFile = (variant: Variant) => `thumb-${variant}.json`

/** Criados uma vez, nunca sobrescritos: o git ignora os runs; o Godot não importa nada da pasta. */
const MARKERS: Record<string, string> = {
  '.gitignore': `${RUNS_DIR}/\n`,
  '.gdignore': '',
}

export function workdirOf(projectRoot: string): string {
  return joinPath(projectRoot, WORKDIR)
}

export async function ensureWorkdir(host: Host, projectRoot: string): Promise<void> {
  const dir = workdirOf(projectRoot)
  for (const [file, text] of Object.entries(MARKERS)) {
    const path = joinPath(dir, file)
    if (!(await host.exists(path))) await host.writeText(path, text)
  }
}

function stamp(now: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
}

/** `.game-art/runs/<name>/<YYYYMMDD-HHMMSS>`, com sufixo quando dois runs caem no mesmo segundo. */
export async function newRunDir(host: Host, projectRoot: string, name: string, now: Date): Promise<string> {
  const parent = joinPath(workdirOf(projectRoot), RUNS_DIR, name)
  const taken = new Set(await host.list(parent))
  const base = stamp(now)
  for (let index = 1; ; index++) {
    const id = index === 1 ? base : `${base}-${index}`
    if (!taken.has(id)) return joinPath(parent, id)
  }
}
