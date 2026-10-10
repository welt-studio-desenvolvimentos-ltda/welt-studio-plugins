// A tool keep_variant: grava no projeto a variação escolhida de um run com várias.

import { optionalBoolean, optionalString, toolId } from './request'
import { VARIANTS } from './workdir'
import type { Variant } from './workdir'

export const KEEP_NAME = 'keep_variant'
export const KEEP_ID = toolId(KEEP_NAME)

export const KEEP_DESCRIPTION = [
  'Save the chosen variant of a generate_asset run (one made with variants > 1) into the game project, processed like any asset.',
  'Ask the user which variant to keep when they have not said. Returns the saved path, its size, warnings and a preview.',
].join(' ')

export const KEEP_SCHEMA = {
  type: 'object',
  properties: {
    run: { type: 'string', description: 'The run folder generate_asset reported.' },
    variant: { type: 'string', enum: [...VARIANTS] },
    dest: { type: 'string', description: 'Folder to save into, relative to the project root; default is the run\'s.' },
    open: { type: 'boolean', description: 'Also open the saved PNG for the user.' },
  },
  required: ['run', 'variant'],
  additionalProperties: false,
} as const

export type KeepInput = { run: string; variant: Variant; dest?: string; open: boolean }

function isVariant(value: unknown): value is Variant {
  return typeof value === 'string' && (VARIANTS as readonly string[]).includes(value)
}

export function parseKeep(input: Record<string, unknown>): KeepInput {
  const run = optionalString(input.run, 'run')
  if (run === undefined || !run.startsWith('/')) throw new Error('run must be the absolute run folder generate_asset reported')
  if (!isVariant(input.variant)) throw new Error(`variant must be one of ${VARIANTS.join(', ')}`)
  return { run, variant: input.variant, dest: optionalString(input.dest, 'dest'), open: optionalBoolean(input.open, 'open') === true }
}
