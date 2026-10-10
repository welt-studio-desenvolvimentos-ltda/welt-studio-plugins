// O guia de estilo do projeto em .game-art/style.json: leitura estrita (um erro de digitação não some em silêncio) e a tool init_style.

import { findProject, joinPath } from './engine'
import type { Host } from './host'
import { KINDS } from './kinds'
import type { Kind } from './kinds'
import { isKind, optionalBoolean, optionalPaths, optionalString, side, toolId } from './request'
import type { Size, StyleGuide } from './request'
import { ensureWorkdir, STYLE_FILE, workdirOf } from './workdir'

export const INIT_STYLE_NAME = 'init_style'
export const INIT_STYLE_ID = toolId(INIT_STYLE_NAME)

export const INIT_STYLE_DESCRIPTION = [
  'Create the project style guide (.game-art/style.json) that generate_asset applies to every asset: art direction, pixel art or not, reference images and the default size per kind.',
  'Ask the user for the art direction before calling it; do not invent one. Refuses to replace an existing guide unless overwrite is true.',
].join(' ')

export const INIT_STYLE_SCHEMA = {
  type: 'object',
  properties: {
    style: { type: 'string', description: 'Art direction for every asset, e.g. "16-bit pixel art, 1px dark outline, muted earthy palette".' },
    pixelArt: { type: 'boolean', description: 'Whether the project is pixel art.' },
    references: { type: 'array', items: { type: 'string' }, description: 'Reference images, relative to the project root (or absolute).' },
    sizes: {
      type: 'object',
      description: 'Default final size per kind, e.g. { "sprite": [64, 64], "icon": [32, 32] }.',
      properties: Object.fromEntries(KINDS.map(kind => [kind, { type: 'array', items: { type: 'integer' }, minItems: 2, maxItems: 2 }])),
      additionalProperties: false,
    },
    overwrite: { type: 'boolean', description: 'Replace an existing style guide.' },
  },
  additionalProperties: false,
} as const

const FIELDS = ['style', 'pixelArt', 'references', 'sizes'] as const

function parseSizes(value: unknown): Partial<Record<Kind, Size>> | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error('sizes must be an object of kind: [width, height]')
  const sizes: Partial<Record<Kind, Size>> = {}
  for (const [kind, pair] of Object.entries(value)) {
    if (!isKind(kind)) throw new Error(`sizes.${kind} is not a kind (${KINDS.join(', ')})`)
    if (!Array.isArray(pair) || pair.length !== 2) throw new Error(`sizes.${kind} must be [width, height]`)
    const width = side(pair[0], `sizes.${kind} width`)
    const height = side(pair[1], `sizes.${kind} height`)
    if (width !== undefined && height !== undefined) sizes[kind] = { width, height }
  }
  return sizes
}

/** Valida um guia vindo de JSON ou da tool. Lança Error com o campo culpado. */
export function parseStyle(value: unknown): StyleGuide {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error('the style guide must be a JSON object')
  const fields = value as Record<string, unknown>
  const unknown = Object.keys(fields).filter(key => !(FIELDS as readonly string[]).includes(key))
  if (unknown.length > 0) throw new Error(`unknown field${unknown.length === 1 ? '' : 's'} ${unknown.join(', ')} (known: ${FIELDS.join(', ')})`)
  return {
    style: optionalString(fields.style, 'style'),
    pixelArt: optionalBoolean(fields.pixelArt, 'pixelArt'),
    references: optionalPaths(fields.references, 'references'),
    sizes: parseSizes(fields.sizes),
  }
}

export function stylePath(projectRoot: string): string {
  return joinPath(workdirOf(projectRoot), STYLE_FILE)
}

/** O guia do projeto, ou undefined quando não há. Um guia que existe e não se lê é erro, nunca ignorado. */
export async function loadStyle(host: Host, projectRoot: string): Promise<StyleGuide | undefined> {
  const path = stylePath(projectRoot)
  const text = await host.readText(path)
  if (text === undefined) return undefined
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch (error) {
    throw new Error(`${path} is not valid JSON: ${error instanceof Error ? error.message : String(error)}`)
  }
  try {
    return parseStyle(parsed)
  } catch (error) {
    throw new Error(`${path}: ${error instanceof Error ? error.message : String(error)}`)
  }
}

function serialize(guide: StyleGuide): string {
  const sizes = guide.sizes && Object.fromEntries(Object.entries(guide.sizes).map(([kind, size]) => [kind, [size.width, size.height]]))
  const plain = { style: guide.style, pixelArt: guide.pixelArt, references: guide.references, sizes }
  return `${JSON.stringify(plain, null, 2)}\n`
}

export type InitStyleOutcome = { ok: true; path: string; text: string } | { ok: false; error: string }

export async function initStyle(host: Host, cwd: string, input: Record<string, unknown>): Promise<InitStyleOutcome> {
  const { overwrite, ...fields } = input
  let guide: StyleGuide
  try {
    optionalBoolean(overwrite, 'overwrite')
    guide = parseStyle(fields)
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) }
  }
  const project = await findProject(host, cwd)
  const path = stylePath(project.root)
  if (overwrite !== true && (await host.exists(path))) {
    return { ok: false, error: `${path} already exists; pass overwrite: true to replace it` }
  }
  for (const reference of guide.references ?? []) {
    const absolute = reference.startsWith('/') ? reference : joinPath(project.root, reference)
    if (!(await host.exists(absolute))) return { ok: false, error: `reference image not found: ${absolute}` }
  }
  await ensureWorkdir(host, project.root)
  const text = serialize(guide)
  await host.writeText(path, text)
  return { ok: true, path, text }
}
