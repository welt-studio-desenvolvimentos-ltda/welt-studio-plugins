// A tool de geração que o Claude chama: identidade, schema, validação da entrada e a resolução contra o guia de estilo.

import { joinPath } from './engine'
import { KIND_SPECS, KINDS } from './kinds'
import type { Kind } from './kinds'
import { VARIANTS } from './workdir'

export const PLUGIN_NAME = 'game-art'

/** O nome completo com que o engine lista uma tool deste plugin. */
export function toolId<Name extends string>(name: Name): `mcp__${typeof PLUGIN_NAME}__${Name}` {
  return `mcp__${PLUGIN_NAME}__${name}`
}

export const TOOL_NAME = 'generate_asset'
export const TOOL_ID = toolId(TOOL_NAME)

/** Lado máximo pedido para o asset final. */
export const MAX_SIDE = 4096
export const MAX_VARIANTS = VARIANTS.length

export const TOOL_DESCRIPTION = [
  'Generate one game art asset with the Codex CLI image generator and save it as a PNG in the game project (Godot, Unity or Unreal, detected from the session directory).',
  'Kinds: sprite, icon, ui (transparent by default, trimmed); texture (seamless, power-of-two); background; concept.',
  'pixelArt keys out a flat magenta background and scales with nearest neighbour; otherwise transparency comes from the generator.',
  'The project style guide (.game-art/style.json, see init_style) fills style, pixelArt, references and the size per kind; fields given here win.',
  'Pass references (image paths) to keep a character or style consistent. One call takes 30 to 90 seconds.',
  'Returns the saved path, its size, any quality warnings, a preview image to check the result, and what to do in the engine.',
].join(' ')

export const INPUT_SCHEMA = {
  type: 'object',
  properties: {
    prompt: { type: 'string', description: 'What to draw: subject, pose, mood, colours.' },
    name: { type: 'string', description: 'File name without extension (letters, digits, - and _), e.g. "player_idle".' },
    kind: { type: 'string', enum: [...KINDS] },
    style: { type: 'string', description: 'Art direction, e.g. "16-bit pixel art, 1px dark outline". Default: the style guide\'s.' },
    pixelArt: { type: 'boolean', description: 'Crisp pixel art: magenta chroma key and nearest-neighbour scaling. Default: the style guide\'s, else false.' },
    transparent: { type: 'boolean', description: 'Override the kind\'s default background.' },
    references: { type: 'array', items: { type: 'string' }, description: 'Image paths the result must match (character, style). Replaces the style guide\'s.' },
    width: { type: 'integer', minimum: 1, maximum: MAX_SIDE, description: 'Final width in pixels; give height too. Default: the style guide\'s size for the kind.' },
    height: { type: 'integer', minimum: 1, maximum: MAX_SIDE, description: 'Final height in pixels; give width too.' },
    dest: { type: 'string', description: 'Folder to save into, relative to the project root; default is the engine\'s art folder for the kind.' },
    open: { type: 'boolean', description: 'Also open the saved PNG for the user (VS Code tab, or the Windows/Linux image viewer).' },
    background: {
      type: 'boolean',
      description: 'Return at once and keep generating in the background; the result arrives later as a message. Use for batches, not when the asset is needed right away.',
    },
    variants: {
      type: 'integer',
      minimum: 1,
      maximum: MAX_VARIANTS,
      description: `Generate up to ${MAX_VARIANTS} versions to choose from (two at a time). With more than one, nothing is saved into the project until keep_variant.`,
    },
  },
  required: ['prompt', 'name', 'kind'],
  additionalProperties: false,
} as const

export type Size = { width: number; height: number }

/** O que veio na chamada, só o que foi dito: o que faltar o guia de estilo ou o tipo completam. */
export type AssetInput = {
  prompt: string
  name: string
  kind: Kind
  style?: string
  pixelArt?: boolean
  transparent?: boolean
  references?: string[]
  size?: Size
  dest?: string
  open: boolean
  variants: number
  background: boolean
}

/** O pedido resolvido: referências absolutas, nada em aberto. */
export type AssetRequest = {
  prompt: string
  name: string
  kind: Kind
  style?: string
  pixelArt: boolean
  transparent: boolean
  references: string[]
  size?: Size
  dest?: string
  open: boolean
}

/** O guia de estilo do projeto (.game-art/style.json). */
export type StyleGuide = {
  style?: string
  pixelArt?: boolean
  /** Relativas à raiz do projeto. */
  references?: string[]
  sizes?: Partial<Record<Kind, Size>>
}

const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/

export function isKind(value: unknown): value is Kind {
  return typeof value === 'string' && (KINDS as readonly string[]).includes(value)
}

export function optionalString(value: unknown, field: string): string | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'string') throw new Error(`${field} must be a string`)
  return value.trim() === '' ? undefined : value.trim()
}

export function optionalBoolean(value: unknown, field: string): boolean | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'boolean') throw new Error(`${field} must be a boolean`)
  return value
}

export function optionalPaths(value: unknown, field: string): string[] | undefined {
  if (value === undefined) return undefined
  if (!Array.isArray(value) || value.some(path => typeof path !== 'string' || path.trim() === '')) {
    throw new Error(`${field} must be a list of image paths`)
  }
  return value.map(path => (path as string).trim())
}

export function side(value: unknown, field: string): number | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 1 || value > MAX_SIDE) {
    throw new Error(`${field} must be an integer from 1 to ${MAX_SIDE}`)
  }
  return value
}

/** Valida a entrada da tool. Lança Error com a mensagem para o modelo. */
export function parseInput(input: Record<string, unknown>): AssetInput {
  const prompt = optionalString(input.prompt, 'prompt')
  if (prompt === undefined) throw new Error('prompt is required')
  const name = optionalString(input.name, 'name')
  if (name === undefined || !NAME_PATTERN.test(name)) {
    throw new Error('name is required: letters, digits, - and _, starting with a letter or digit, up to 100 characters')
  }
  if (!isKind(input.kind)) throw new Error(`kind must be one of ${KINDS.join(', ')}`)
  const width = side(input.width, 'width')
  const height = side(input.height, 'height')
  if ((width === undefined) !== (height === undefined)) throw new Error('give both width and height, or neither')
  const variants = input.variants ?? 1
  if (typeof variants !== 'number' || !Number.isInteger(variants) || variants < 1 || variants > MAX_VARIANTS) {
    throw new Error(`variants must be an integer from 1 to ${MAX_VARIANTS}`)
  }

  return {
    prompt,
    name,
    kind: input.kind,
    style: optionalString(input.style, 'style'),
    pixelArt: optionalBoolean(input.pixelArt, 'pixelArt'),
    transparent: optionalBoolean(input.transparent, 'transparent'),
    references: optionalPaths(input.references, 'references'),
    size: width !== undefined && height !== undefined ? { width, height } : undefined,
    dest: optionalString(input.dest, 'dest'),
    open: optionalBoolean(input.open, 'open') === true,
    variants,
    background: optionalBoolean(input.background, 'background') === true,
  }
}

function absolute(base: string, path: string): string {
  return path.startsWith('/') ? path : joinPath(base, path)
}

/**
 * Campo a campo, a chamada vence o guia e o guia vence o padrão do tipo. Referências da chamada
 * são relativas à sessão; as do guia, à raiz do projeto.
 */
export function resolveRequest(input: AssetInput, guide: StyleGuide | undefined, cwd: string, projectRoot: string): AssetRequest {
  const references = input.references
    ? input.references.map(path => absolute(cwd, path))
    : (guide?.references ?? []).map(path => absolute(projectRoot, path))
  return {
    prompt: input.prompt,
    name: input.name,
    kind: input.kind,
    style: input.style ?? guide?.style,
    pixelArt: input.pixelArt ?? guide?.pixelArt ?? false,
    transparent: input.transparent ?? KIND_SPECS[input.kind].transparent,
    references,
    size: input.size ?? guide?.sizes?.[input.kind],
    dest: input.dest,
    open: input.open,
  }
}
