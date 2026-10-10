// O prompt e o argv do `codex exec`, que gera a imagem com a ferramenta embutida do Codex (login do ChatGPT, sem API key).

import { ENGINE_LABELS } from './engine'
import type { Engine } from './engine'
import { KIND_SPECS } from './kinds'
import type { AssetRequest } from './request'

/** Fundo chapado da pixel art, recortado depois pelo process_image.py (recebe a cor por argumento). */
export const CHROMA_HEX = 'FF00FF'

export type Background = 'chroma' | 'native' | 'opaque'

export function backgroundOf(request: AssetRequest): Background {
  if (!request.transparent) return 'opaque'
  return request.pixelArt ? 'chroma' : 'native'
}

const BACKGROUND_RULES: Record<Background, string> = {
  chroma: `Background: perfectly flat solid pure magenta #${CHROMA_HEX} everywhere (no gradient, no vignette, no glow, no shadow, no floor line). Never use magenta inside the asset itself.`,
  native: 'Call the image tool with transparent_background set to true. Nothing behind the asset: no backdrop, no shadow, no floor.',
  opaque: 'The image is fully opaque and fills the whole canvas.',
}

function ratio(width: number, height: number): string {
  const gcd = (a: number, b: number): number => (b === 0 ? a : gcd(b, a % b))
  const divisor = gcd(width, height)
  return `${width / divisor}:${height / divisor}`
}

/** `output`: o nome do arquivo que o Codex grava no diretório do run. */
export function buildPrompt(request: AssetRequest, engine: Engine, output: string): string {
  const style = [request.style, request.pixelArt ? 'crisp pixel art with hard pixel edges, no anti-aliasing, no blur, limited palette' : '']
    .filter(Boolean)
    .join('; ')
  const lines = [
    'Use your built-in image generation tool (do not write code and do not call any image API script).',
    `Use case: production game asset for a ${ENGINE_LABELS[engine]} game.`,
    `Asset type: ${request.kind}.`,
    `Subject: ${request.prompt}`,
    style ? `Style: ${style}.` : '',
    KIND_SPECS[request.kind].rules,
    request.references.length > 0
      ? `The attached images are references: pass them to the image tool as referenced images and match their character design, palette and rendering exactly.`
      : '',
    request.size ? `Compose for a ${ratio(request.size.width, request.size.height)} aspect ratio.` : '',
    BACKGROUND_RULES[backgroundOf(request)],
    'Constraints: no text, no labels, no logos or trademarks, no watermark.',
    `Save the result in the current directory as ${output} and print its path.`,
  ]
  return lines.filter(Boolean).join('\n')
}

/**
 * O prompt vai pelo stdin (`-`), porque `-i` aceita vários arquivos e engoliria um prompt posicional depois dele.
 * `--ephemeral` não grava o rollout da sessão (3 a 4 MB por imagem, com o PNG em base64); `--json` dá o thread id.
 */
export function codexArgv(runDir: string, references: readonly string[], model: string): string[] {
  return [
    'codex',
    'exec',
    '--skip-git-repo-check',
    '--ephemeral',
    '--json',
    '-s',
    'workspace-write',
    '-C',
    runDir,
    ...(model ? ['-m', model] : []),
    '-',
    ...references.flatMap(path => ['-i', path]),
  ]
}

const THREAD_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/

function events(stdout: string): Record<string, unknown>[] {
  return stdout.split('\n').flatMap(line => {
    try {
      const parsed: unknown = JSON.parse(line)
      return typeof parsed === 'object' && parsed !== null ? [parsed as Record<string, unknown>] : []
    } catch {
      return []
    }
  })
}

/** O id do thread (evento `thread.started`), só quando tem forma de UUID: ele vira nome de pasta a apagar. */
export function threadIdOf(stdout: string): string | undefined {
  const started = events(stdout).find(event => event.type === 'thread.started')
  const id = started?.thread_id
  return typeof id === 'string' && THREAD_ID.test(id) ? id : undefined
}

/** O que o Codex disse (mensagens do agente e erros), para explicar uma geração que não saiu. */
export function codexSaid(stdout: string): string[] {
  return events(stdout).flatMap(event => {
    const item = event.item as Record<string, unknown> | undefined
    if (item?.type === 'agent_message' && typeof item.text === 'string') return [item.text]
    if (typeof event.message === 'string') return [event.message]
    const error = event.error as Record<string, unknown> | undefined
    return typeof error?.message === 'string' ? [error.message] : []
  })
}

/** Onde o Codex deixa a cópia dele de cada imagem gerada. */
export function generatedImagesDir(codexHome: string, threadId: string): string {
  return `${codexHome.replace(/\/+$/, '')}/generated_images/${threadId}`
}
