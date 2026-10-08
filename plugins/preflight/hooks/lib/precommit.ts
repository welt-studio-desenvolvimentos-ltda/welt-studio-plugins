// O pre-commit do próprio repo como checagem: as mesmas travas do commit, sem config duplicada.

import type { Host } from './host'

export const PRECOMMIT_CONFIG = '.pre-commit-config.yaml'
const OUTPUT_LIMIT = 4000
const QUIET_LINE = /\.{3,}\s*(\(no files to check\))?(Passed|Skipped)\s*$/

/**
 * Ids dos hooks que olham o projeto inteiro (`pass_filenames: false` ou `always_run: true`),
 * como tsc e pyright: lentos e reprovando por estado transitório no meio de uma refatoração.
 * Rodam só no fim do turno. Leitura por linha, sem parser de YAML: o módulo não tem dependências.
 */
export function wholeProjectHookIds(config: string): string[] {
  const ids: string[] = []
  let current: { id: string; isWhole: boolean } | undefined
  const flush = (): void => {
    if (current?.isWhole && !ids.includes(current.id)) ids.push(current.id)
    current = undefined
  }
  for (const line of config.split('\n')) {
    const hook = /^\s*-\s+id:\s*['"]?([\w.-]+)/.exec(line)
    if (hook) {
      flush()
      current = { id: hook[1] as string, isWhole: false }
      continue
    }
    if (/^\s*-\s+repo:/.test(line)) flush()
    else if (current && /^\s*(pass_filenames:\s*false|always_run:\s*true)\b/.test(line)) current.isWhole = true
  }
  flush()
  return ids
}

function trimOutput(output: string): string {
  const kept = output
    .split('\n')
    .filter(line => !QUIET_LINE.test(line))
    .join('\n')
    .trim()
  return kept.length > OUTPUT_LIMIT ? `${kept.slice(0, OUTPUT_LIMIT)}\n… (cut)` : kept
}

export type PrecommitScope = 'file' | 'full'

/**
 * Resultado de uma rodada que não passou limpa, com o texto para o modelo. `failed`: sobrou erro
 * para corrigir; `autofixed`: um fixer reescreveu e a segunda rodada passou; `unfinished`: o
 * pre-commit não rodou até o fim (não instalado, timeout), nada que o modelo corrija no código.
 */
export type PrecommitOutcome = { kind: 'failed' | 'autofixed' | 'unfinished'; note: string }

/**
 * Roda o pre-commit nos arquivos (relativos à raiz). `file` pula os hooks de projeto inteiro.
 * undefined quando passou ou não se aplica.
 */
export async function runPrecommit(
  host: Host,
  root: string,
  files: readonly string[],
  scope: PrecommitScope,
  timeoutMs: number,
): Promise<PrecommitOutcome | undefined> {
  if (root === '' || files.length === 0) return undefined
  const config = await host.readFile(`${root}/${PRECOMMIT_CONFIG}`)
  if (config === undefined) return undefined

  const skip = scope === 'file' ? wholeProjectHookIds(config) : []
  const env = skip.length > 0 ? { SKIP: skip.join(',') } : undefined
  const run = () => host.run(['pre-commit', 'run', '--files', ...files], { cwd: root, env, timeoutMs })
  try {
    const first = await run()
    if (first.exitCode === 0) return undefined
    const firstRaw = `${first.stdout}\n${first.stderr}`
    // Na saída inteira: o aviso pode cair depois do corte que o texto para o modelo sofre.
    const fixed = /files were modified by this hook/.test(firstRaw)
    // Um fixer (formatter, ruff --fix) reescreve e sai 1; a segunda rodada diz se sobrou erro.
    const second = fixed ? await run() : first
    const listed = files.join(', ')
    if (second.exitCode === 0) {
      return { kind: 'autofixed', note: `preflight: the repo pre-commit autofixed ${listed}. Read the file again before the next Edit.` }
    }
    const note = [
      `preflight: the repo pre-commit${scope === 'file' ? ' (per-file hooks)' : ''} fails on ${listed}.`,
      fixed ? 'Some hooks also rewrote files: Read them again before the next Edit.' : '',
      'Fix this now, before moving on:',
      trimOutput(fixed ? `${second.stdout}\n${second.stderr}` : firstRaw),
    ]
      .filter(Boolean)
      .join('\n')
    return { kind: 'failed', note }
  } catch (error) {
    return { kind: 'unfinished', note: `preflight: pre-commit did not finish (${String(error)}). Run it before declaring the work done.` }
  }
}
