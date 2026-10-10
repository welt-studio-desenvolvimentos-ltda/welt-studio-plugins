// A galeria do painel: o que o projeto tem de gerado, lido dos runs em .game-art/runs (sobrevive entre sessões).

import type { GameArtGalleryEntry, GameArtSaved } from '../../types'
import { joinPath } from './engine'
import type { Host } from './host'
import type { Kind } from './kinds'
import { isKind } from './request'
import { finalFile, RUN_FILE, RUNS_DIR, SAVED_FILE, VARIANTS, workdirOf } from './workdir'
import type { Variant } from './workdir'

export type GalleryEntry = GameArtGalleryEntry
export type Saved = GameArtSaved

// O contrato de estado não importa nada, então repete os tipos e as variações: estas checagens fazem o tsc falhar
// se um lado mudar sem o outro.
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false
type Expect<T extends true> = T
export type ContractKindsMatch = Expect<Same<Kind, GalleryEntry['kind']>>
export type ContractVariantsMatch = Expect<Same<Variant, Saved['variant']>>

function isVariant(value: unknown): value is Variant {
  return typeof value === 'string' && (VARIANTS as readonly string[]).includes(value)
}

function parseJson(text: string | undefined): unknown {
  if (text === undefined) return undefined
  try {
    return JSON.parse(text)
  } catch {
    return undefined
  }
}

function parseSaved(value: unknown): Saved[] {
  if (!Array.isArray(value)) return []
  return value.flatMap(item => {
    const { path, width, height, variant } = (item ?? {}) as Record<string, unknown>
    return typeof path === 'string' && typeof width === 'number' && typeof height === 'number' && isVariant(variant)
      ? [{ path, width, height, variant }]
      : []
  })
}

/** Lê um run; undefined quando não é um run do game-art (sem run.json legível). */
async function readEntry(host: Host, runDir: string, name: string, id: string): Promise<GalleryEntry | undefined> {
  const record = parseJson(await host.readText(joinPath(runDir, RUN_FILE))) as Record<string, unknown> | undefined
  const request = record?.request as Record<string, unknown> | undefined
  if (!isKind(request?.kind)) return undefined
  const variants = Array.isArray(record?.variants) ? record.variants.filter(isVariant) : []
  const saved = parseSaved(parseJson(await host.readText(joinPath(runDir, SAVED_FILE))))
  const finals = await Promise.all(variants.map(async variant => ((await host.exists(joinPath(runDir, finalFile(variant)))) ? variant : undefined)))
  return { name, kind: request.kind, runDir, id, saved, choices: finals.filter((variant): variant is Variant => variant !== undefined) }
}

/** Todos os runs do projeto, do mais novo para o mais antigo. */
export async function scanGallery(host: Host, projectRoot: string): Promise<GalleryEntry[]> {
  const runs = joinPath(workdirOf(projectRoot), RUNS_DIR)
  const entries: GalleryEntry[] = []
  for (const name of await host.list(runs)) {
    for (const id of await host.list(joinPath(runs, name))) {
      const entry = await readEntry(host, joinPath(runs, name, id), name, id)
      if (entry) entries.push(entry)
    }
  }
  return entries.sort((a, b) => (a.id === b.id ? a.name.localeCompare(b.name) : b.id.localeCompare(a.id)))
}

/** Registra no run o que foi gravado no projeto, para a galeria achar depois. */
export async function recordSaved(host: Host, runDir: string, saved: Saved): Promise<void> {
  const path = joinPath(runDir, SAVED_FILE)
  const current = parseSaved(parseJson(await host.readText(path)))
  await host.writeText(path, `${JSON.stringify([...current, saved], null, 2)}\n`)
}

/** `20261010-151126` → `10/10 15:11`. */
export function whenOf(id: string): string {
  const match = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(id)
  return match ? `${match[3]}/${match[2]} ${match[4]}:${match[5]}` : id
}

/** Variações ainda sem escolha: run com mais de uma e nada gravado. */
export function isPending(entry: GalleryEntry): boolean {
  return entry.saved.length === 0 && entry.choices.length > 1
}

/** Os arquivos que a engine cria ao lado de uma imagem importada (Godot: .import; Unity: .meta). */
export const ENGINE_SIDECARS = ['.import', '.meta'] as const

export type UnchooseOutcome = { ok: true; removed: string[] } | { ok: false; error: string }

/**
 * Desfaz um "save": apaga do projeto o PNG que este run gravou (e o que a engine criou ao lado dele) e tira o registro,
 * então a variação volta a ser uma opção. Só apaga o que o saved.json do run lista: nunca um caminho qualquer.
 */
export async function unchoose(host: Host, runDir: string, path: string): Promise<UnchooseOutcome> {
  const record = joinPath(runDir, SAVED_FILE)
  const current = parseSaved(parseJson(await host.readText(record)))
  if (!current.some(saved => saved.path === path)) return { ok: false, error: `${path} was not saved by this run; nothing was removed` }
  const candidates = [path, ...ENGINE_SIDECARS.map(suffix => `${path}${suffix}`)]
  const present = (await Promise.all(candidates.map(async file => ((await host.exists(file)) ? file : undefined)))).filter(
    (file): file is string => file !== undefined,
  )
  if (present.length > 0) {
    const removed = await host.run(['rm', '-f', '--', ...present])
    if (removed.exitCode !== 0) return { ok: false, error: `could not remove ${present.join(', ')}: ${removed.stderr.trim()}` }
  }
  await host.writeText(record, `${JSON.stringify(current.filter(saved => saved.path !== path), null, 2)}\n`)
  return { ok: true, removed: present }
}
